"""Task Celery de consolidacion de memoria semantica + procedural (M8 Ola 2).

Reglas no negociables (ADR-010 + critica adversarial M8):

1. Solo encolada cuando el modelo del modo escribe memoria (alias agente). El caller
   (``ChatService.run_turn`` en el endpoint) ya filtra; esta task no re-chequea.
2. La task NUNCA esta en el path de respuesta: ``ChatService.run_turn`` encola con
   ``.delay()`` DESPUES del commit (M10 Ola 0); la escritura ocurre aqui,
   en el worker Celery, de forma async.
3. NUNCA episodica: ``layer`` = ``semantic`` | ``procedural`` solamente.
   El ``session_id`` ES el ``ChatSession.id`` real (M9 + M10 Ola 0): se parsea
   a ``UUID`` y se propaga como ``source_session_id`` (FK a ``sessions.id``,
   ``ondelete=SET NULL``) en el ADD semantic (M10 Ola 1). El enqueue es
   post-commit (Ola 0), asi que la fila de ``sessions`` ya existe -> sin race FK.
4. Serializacion 100% JSON: la firma de la task es solo strings/primitivos.
   El worker RECONSTRUYE todas sus deps in-process desde ``get_settings()`` y
   cierra los clientes que armo al terminar cada intento (``_aclose_quietly``).
   El engine de DB usa ``NullPool`` obligatorio (sin el, reusar conexiones
   entre event loops distintos da 'Future attached to a different loop').
5. Parseo defensivo heredado de ``QwenMemoryEngine._parse_ops``: JSON
   invalido / ops malformadas -> [] sin crashear el worker.
6. Ningun dato de usuario a logs (regla #4): el payload (turno del usuario)
   queda en Redis y el worker; al proveedor de IA va solo lo que pide la
   llamada (reglas #4a y #4b). Solo se loguea el conteo de ops aplicadas
   y errores tecnicos SIN contenido.
7. Idempotencia sin tocar ``app/memory/`` ni el schema sagrado: cada intento es
   todo-o-nada (la marca del turno, las ops y su auditoria van en un SAVEPOINT) y,
   con ``turn_seq``, el turno del usuario queda marcado como consolidado en la
   MISMA transaccion (``_turn_marker.claim_turn``). Una re-entrega del mismo turno
   encuentra la marca y no vuelve a escribir.
8. Reintentos ante fallas transitorias del proveedor (``LlmTransientError``: 429,
   5xx, timeout, breaker abierto, respuesta degradada) y ante un intento que vence
   el soft limit (``SoftTimeLimitExceeded``): Celery reprograma la task con backoff
   exponencial y jitter, y la ackea tarde (ver ``_CONSOLIDATION_TASK_OPTIONS``). Lo
   permanente (credencial rechazada, request invalido) no se reintenta: el motor lo
   descarta con un log sin contenido.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from uuid import UUID

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.constants import MODEL_ALIAS_AGENT
from app.enums import LlmModel, Mode
from app.llm.clients.base import LLMClient
from app.llm.clients.embedding import EmbeddingClient
from app.llm.clients.factory import build_embedder, build_llm_client, build_reranker
from app.llm.clients.reranker import Reranker
from app.llm.config import load_llm_config
from app.llm.errors import LlmTransientError
from app.llm.memory_engine import QwenMemoryEngine, apply_ops
from app.memory.audit import AuditStore
from app.memory.config import MemoryConfigError, RetentionConfig, load_retention_config
from app.memory.episodic import EpisodicMemoryStore  # noqa: F401 -- target de patch en tests
from app.memory.procedural import ProceduralMemoryStore
from app.memory.semantic import SemanticMemoryStore
from app.workers.celery_app import celery_app
from app.workflows._consolidation_helpers import (
    _CONSOLIDATION_TASK_OPTIONS,
    _aclose_quietly,  # noqa: F401 -- re-export para tests
    _build_transcript,  # noqa: F401 -- re-export para tests
    _consolidate_session_in_db,
    _episode_exists,  # noqa: F401 -- re-export para tests
    _owned,
    _parse_origin_mode,
    _parse_source_session_id,
    _retry_or_give_up,
)
from app.workflows._engine import worker_session
from app.workflows._turn_marker import TurnClaim, claim_turn

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers de construccion de deps (inyectables en tests)
# ---------------------------------------------------------------------------


def _build_embedder(settings: Settings) -> EmbeddingClient:
    """Construye el cliente de embeddings para la consolidacion (ADR-028 D4)."""
    return build_embedder(settings)


def _build_consolidation_llm(settings: Settings) -> LLMClient:
    """Construye el cliente LLM para consolidacion (alias ``agente``, ``degrade=False``)."""
    return build_llm_client(settings, load_llm_config(), degrade=False)


def _build_reranker() -> Reranker:
    """Construye el reranker passthrough para la consolidacion (ADR-028 D5)."""
    return build_reranker()


def _load_retention_config_safe() -> RetentionConfig:
    """Carga la retention de ``ynara.config.json[memory]``, default-safe ante fallo.

    Capa de defensa adicional sobre la regla del modulo: un fallo de config NO
    debe tumbar la consolidacion. Si ``load_retention_config()`` levanta
    ``MemoryConfigError`` (JSON ilegible, valor invalido, key extra), se degrada al
    ``RetentionConfig`` por defaults (=los valores historicos: default=365,
    sensible=180) y se loguea un mensaje tecnico SIN datos de usuario (regla #4: la
    config no es PII, pero igual no se vuelca el detalle del error a logs).
    """
    try:
        return load_retention_config()
    except MemoryConfigError:
        logger.warning(
            "consolidation: ynara.config.json[memory] de retention invalido, "
            "usando defaults ADR-007 D2 (default=365, sensible=180)"
        )
        return RetentionConfig()


# ---------------------------------------------------------------------------
# Cuerpo async de la consolidacion (separado para inyeccion en tests)
# ---------------------------------------------------------------------------


async def _consolidate_turn_in_db(
    *,
    session: AsyncSession,
    user_id: UUID,
    source_session_id: UUID | None,
    turn_seq: int | None,
    user_msg: str,
    model_response: str,
    mode: str,
    origin_mode: Mode | None,
    llm_client: LLMClient,
    embedder: EmbeddingClient,
    reranker: Reranker,
) -> int:
    """Nucleo de la consolidacion de UN turno sobre una ``session`` ya abierta.

    Retorna la cantidad de ops aplicadas (0 si el turno ya estaba consolidado). NO
    commitea: el commit lo da el caller (``worker_session`` en prod, o el fixture en
    tests).

    INTENTO ATOMICO: la marca del turno, las ops y su auditoria van en un
    SAVEPOINT. Si algo levanta en el medio (p.ej. una falla transitoria del embedder
    en la segunda op), se revierte entero, tambien con la sesion inyectada de los
    tests: el reintento arranca de cero, sin hechos ni auditoria a medias.

    IDEMPOTENCIA: con ``turn_seq`` el turno del usuario queda marcado como consolidado
    en esta misma transaccion (``claim_turn``); si otra entrega ya lo consolido, no se
    escribe nada. Sin ``turn_seq`` (payload viejo, turno que no se pudo persistir) o
    sin fila (los turnos ya se purgaron al cerrar la sesion) se consolida igual:
    saltearlo perderia el turno. La extraccion con el LLM corre ANTES de tomar la fila,
    asi el lock dura lo que tarda ``apply_ops`` y no la llamada al modelo.
    """
    mem_engine = QwenMemoryEngine(llm_client, model=MODEL_ALIAS_AGENT)
    # Una falla transitoria del LLM (LlmTransientError) sale de aca, antes de tocar la DB.
    ops = await mem_engine.consolidate(
        user_msg=user_msg,
        model_response=model_response,
        mode=mode,
    )
    async with session.begin_nested():
        if source_session_id is not None and turn_seq is not None:
            claim = await claim_turn(
                session, user_id=user_id, session_id=source_session_id, seq=turn_seq
            )
            if claim is TurnClaim.ALREADY_CONSOLIDATED:
                logger.info(
                    "consolidate_turn: turno ya consolidado session=%s seq=%d, no-op",
                    source_session_id,
                    turn_seq,
                )
                return 0
            if claim is TurnClaim.MISSING:
                logger.info(
                    "consolidate_turn: turno sin fila session=%s seq=%d, se consolida sin marca",
                    source_session_id,
                    turn_seq,
                )
        return await apply_ops(
            ops,
            session=session,
            semantic_store=SemanticMemoryStore(session, user_id, embedder, reranker),
            procedural_store=ProceduralMemoryStore(session, user_id),
            source_session_id=source_session_id,
            audit_store=AuditStore(session, user_id),
            origin_model=LlmModel.QWEN,
            origin_mode=origin_mode,
        )


async def _async_consolidate(
    *,
    user_id: str,
    session_id: str,  # str(ChatSession.id): se parsea a UUID y se usa como FK
    user_msg: str,
    model_response: str,
    mode: str,
    # seq del turno del usuario en conversation_turns (marca de idempotencia)
    turn_seq: int | None = None,
    # Inyectables para tests (None => construir desde get_settings())
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    embedder: EmbeddingClient | None = None,
    reranker: Reranker | None = None,
    # session inyectable para tests de integracion (evita crear engine nuevo)
    session: AsyncSession | None = None,
) -> int:
    """Nucleo async de la consolidacion; retorna la cantidad de ops aplicadas.

    Si ``session`` se inyecta (tests de integracion), se usa directamente y
    NO se crea engine ni se hace commit (el fixture del test controla el
    ciclo de vida de la sesion). Si es ``None`` (worker Celery en prod), se
    construye el engine con ``NullPool`` (decision #4 M8), se abre la sesion,
    se commitea y se dispone el engine.

    ``session_id`` es ``str(ChatSession.id)``: el id real de la sesion (FK a
    ``sessions.id``). Se parsea a ``UUID`` de forma DEFENSIVA y se propaga como
    ``source_session_id`` (provenance) a ``apply_ops`` en AMBOS branches (M10
    Ola 1). El enqueue post-commit (M10 Ola 0) garantiza que la ``ChatSession``
    ya este persistida cuando el worker corre, asi que la FK no tiene race. Si
    el parse falla (payload corrupto en la cola), ``source_session_id`` queda
    ``None`` y el hecho se persiste igual (el worker no se cae).

    ``turn_seq`` es el ``seq`` del turno del usuario en ``conversation_turns`` (lo
    manda ``ChatService`` al encolar): con el, el turno se marca como consolidado y
    una re-entrega no duplica (ver ``_consolidate_turn_in_db``).
    """
    cfg = settings or get_settings()

    uid = UUID(user_id)
    # Parse defensivo del session_id: UUID valido -> FK; basura -> None (sin crash).
    source_session_id = _parse_source_session_id(session_id)
    # Parse defensivo del mode -> origin_mode de audit_log (nullable, best-effort).
    origin_mode = _parse_origin_mode(mode)

    # --- Dependencias inyectadas o construidas ---
    # Las que arma la task se cierran al salir del bloque, ande o no el intento; las
    # inyectadas son del caller y no se tocan.
    async with AsyncExitStack() as owned:
        effective_llm = llm_client or _owned(owned, _build_consolidation_llm(cfg))
        effective_embedder = embedder or _owned(owned, _build_embedder(cfg))
        effective_reranker = reranker or _owned(owned, _build_reranker())

        async def _run(db_session: AsyncSession) -> int:
            return await _consolidate_turn_in_db(
                session=db_session,
                user_id=uid,
                source_session_id=source_session_id,
                turn_seq=turn_seq,
                user_msg=user_msg,
                model_response=model_response,
                mode=mode,
                origin_mode=origin_mode,
                llm_client=effective_llm,
                embedder=effective_embedder,
                reranker=effective_reranker,
            )

        if session is not None:
            # Modo test: usar la sesion inyectada, no crear engine ni commitear
            # (el fixture controla el rollback).
            return await _run(session)

        # Modo produccion: engine NullPool efimero; worker_session commitea al salir
        # del bloque y dispone el engine (decision #4 centralizada en _engine.py).
        async with worker_session(cfg) as db_session:
            return await _run(db_session)


# ---------------------------------------------------------------------------
# Task Celery — firma 100% JSON-serializable (politica en _consolidation_helpers)
# ---------------------------------------------------------------------------


@celery_app.task(bind=True, name="workflows.consolidate_turn", **_CONSOLIDATION_TASK_OPTIONS)
def consolidate_turn(
    self,  # bind=True: self.request.retries dice si quedan reintentos
    *,
    user_id: str,
    session_id: str,
    user_msg: str,
    model_response: str,
    mode: str,
    turn_seq: int | None = None,
) -> None:
    """Task Celery: consolida el turno del usuario en memoria semantica + procedural.

    Firma 100% strings/primitivos (``task_serializer='json'``). NUNCA cruza
    el wire un ``AsyncSession``, un ``LLMClient``, un ``UUID`` ni un store.

    El cuerpo async se corre con ``asyncio.run`` (worker prefork de Celery). Una
    falla transitoria del proveedor, o un intento que vence el soft limit, se
    re-levanta para que Celery reintente (ver ``_CONSOLIDATION_TASK_OPTIONS``);
    cualquier otro fallo, o la falla transitoria con los reintentos agotados, se
    loguea SIN el contenido del usuario (regla #4) y NO tumba el worker.

    Args:
        user_id: UUID del usuario como string (JSON-safe).
        session_id: ``str(ChatSession.id)`` (id real de la sesion, FK a
            ``sessions.id``). El cuerpo async lo parsea a ``UUID`` de forma
            defensiva y lo propaga como ``source_session_id`` (provenance) en el
            ADD semantic (M10 Ola 1). Si fuera basura no-UUID, degrada a ``None``.
        user_msg: Mensaje del usuario (NO se loguea).
        model_response: Respuesta del modelo (NO se loguea).
        mode: Modo activo de la sesion (p.ej. 'vida', 'estudio').
        turn_seq: ``seq`` del turno del usuario en ``conversation_turns``, para marcarlo
            como consolidado (idempotencia). ``None`` si el turno no se persistio o el
            mensaje se encolo antes de que existiera el campo: se consolida sin marca.
    """
    try:
        applied = asyncio.run(
            _async_consolidate(
                user_id=user_id,
                session_id=session_id,
                user_msg=user_msg,
                model_response=model_response,
                mode=mode,
                turn_seq=turn_seq,
            )
        )
        logger.info(
            "consolidate_turn: user=%s session=%s applied=%d",
            user_id,
            session_id,
            applied,
        )
    except (LlmTransientError, SoftTimeLimitExceeded) as exc:
        _retry_or_give_up(
            self, exc, label="consolidate_turn", user_id=user_id, session_id=session_id
        )
    except Exception as exc:
        # Regla: el worker NUNCA muere por un fallo de consolidacion.
        # regla #4: logger.error (NO logger.exception): el traceback / str(exc) podria
        # arrastrar contenido de usuario a los logs. Se loguea solo el TIPO de excepcion.
        logger.error(
            "consolidate_turn: fallo al consolidar user=%s session=%s: %s (sin datos de usuario)",
            user_id,
            session_id,
            type(exc).__name__,
        )


# ===========================================================================
# Consolidacion EPISODICA (issue #209) — nucleo en _consolidation_helpers
# ===========================================================================


async def _async_consolidate_session(
    *,
    user_id: str,
    session_id: str,  # str(ChatSession.id): se parsea a UUID (FK episodic.session_id)
    mode: str,
    # Inyectables para tests (None => construir desde get_settings())
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    embedder: EmbeddingClient | None = None,
    reranker: Reranker | None = None,
    # retention config inyectable para tests (None => cargar de ynara.config.json)
    retention_config: RetentionConfig | None = None,
    # session inyectable para tests de integracion (evita crear engine nuevo)
    session: AsyncSession | None = None,
) -> int:
    """Nucleo async de la consolidacion episodica; retorna 1 si creo un episodio, 0 si no.

    Si ``session`` se inyecta (tests), se usa directamente y NO se crea engine ni
    se commitea (el fixture controla el ciclo de vida). Si es ``None`` (worker
    Celery en prod), se construye el engine con ``NullPool`` (decision #4 M8), se
    abre la sesion, se commitea y se dispone el engine.

    ``session_id`` es ``str(ChatSession.id)`` (FK a ``sessions.id``). Si el parse a
    UUID falla (payload corrupto en la cola), se degrada a no-op (0) SIN crashear:
    el episodio requiere una FK real, no se puede crear con un session_id basura.
    """
    cfg = settings or get_settings()

    uid = UUID(user_id)
    source_session_id = _parse_source_session_id(session_id)
    if source_session_id is None:
        # session_id no-UUID: el episodio requiere una FK real -> no-op sin crash.
        return 0
    origin_mode = _parse_origin_mode(mode)
    # Retention config-driven (ADR-007 D2): inyectado en tests, o cargado de
    # ``ynara.config.json[memory]`` en prod. Fallback defensivo: si el config es
    # ilegible/invalido NO se tumba el worker (regla del modulo: un fallo de config
    # nunca mata la consolidacion) -> se usa el RetentionConfig por defaults (=los
    # valores historicos). El ``try/except`` del task sigue siendo la red final.
    effective_retention = retention_config or _load_retention_config_safe()

    # Los clientes que arma la task se cierran al salir del bloque, ande o no el
    # intento; los inyectados son del caller y no se tocan.
    async with AsyncExitStack() as owned:
        effective_llm = llm_client or _owned(owned, _build_consolidation_llm(cfg))
        effective_embedder = embedder or _owned(owned, _build_embedder(cfg))
        effective_reranker = reranker or _owned(owned, _build_reranker())

        async def _run(db_session: AsyncSession) -> int:
            return await _consolidate_session_in_db(
                session=db_session,
                user_id=uid,
                source_session_id=source_session_id,
                mode=mode,
                origin_mode=origin_mode,
                llm_client=effective_llm,
                embedder=effective_embedder,
                reranker=effective_reranker,
                retention_config=effective_retention,
            )

        if session is not None:
            # Modo test: usar la sesion inyectada, no crear engine ni commitear.
            return await _run(session)

        # Modo produccion: engine NullPool efimero; worker_session commitea al salir
        # del bloque y dispone el engine (decision #4 centralizada en _engine.py).
        async with worker_session(cfg) as db_session:
            return await _run(db_session)


@celery_app.task(bind=True, name="workflows.consolidate_session", **_CONSOLIDATION_TASK_OPTIONS)
def consolidate_session(
    self,  # bind=True: self.request.retries dice si quedan reintentos
    *,
    user_id: str,
    session_id: str,
    mode: str = Mode.VIDA.value,
) -> None:
    """Task Celery: al cerrar la sesion, resume sus turnos en ``episodic_memory``.

    Firma 100% strings/primitivos (``task_serializer='json'``). NUNCA cruza el
    wire un ``AsyncSession``, un ``LLMClient``, un ``UUID`` ni un store.

    El cuerpo async se corre con ``asyncio.run`` (worker prefork de Celery). Una falla
    transitoria del proveedor, o un intento que vence el soft limit, se re-levanta para
    que Celery reintente (mismos ``_CONSOLIDATION_TASK_OPTIONS`` que
    ``consolidate_turn``: el resumen y la purga van en un solo commit y un episodio
    existente corta, asi que re-ejecutar no duplica); cualquier otro fallo, o la falla
    transitoria con los reintentos agotados, se loguea SIN el contenido del usuario
    (regla #4) y NO tumba el worker.

    Args:
        user_id: UUID del usuario como string (JSON-safe).
        session_id: ``str(ChatSession.id)`` (FK a ``sessions.id`` / a
            ``episodic_memory.session_id``). El enqueue post-commit (desde
            ``close_session``) garantiza que la ``ChatSession`` ya este persistida.
        mode: Modo de la sesion (p.ej. 'bienestar' -> ``is_sensitive=True``).
            Default ``vida`` para tolerar un payload viejo sin ``mode``.
    """
    try:
        created = asyncio.run(
            _async_consolidate_session(
                user_id=user_id,
                session_id=session_id,
                mode=mode,
            )
        )
        logger.info(
            "consolidate_session: user=%s session=%s created=%d",
            user_id,
            session_id,
            created,
        )
    except (LlmTransientError, SoftTimeLimitExceeded) as exc:
        _retry_or_give_up(
            self, exc, label="consolidate_session", user_id=user_id, session_id=session_id
        )
    except Exception as exc:
        # Regla: el worker NUNCA muere por un fallo de consolidacion episodica.
        # regla #4: logger.error (NO logger.exception): el traceback / str(exc) podria
        # arrastrar contenido de usuario a los logs. Se loguea solo el TIPO de excepcion.
        logger.error(
            "consolidate_session: fallo user=%s session=%s: %s (sin datos de usuario)",
            user_id,
            session_id,
            type(exc).__name__,
        )
