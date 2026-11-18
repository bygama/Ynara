"""Helpers compartidos y nucleo episodico de ``app/workflows/consolidation.py``.

Extraidos para mantener ``consolidation.py`` por debajo de 500 lineas
(``AI-GUIDELINES.md`` #3): ciclo de vida de clientes HTTP (``_aclose_quietly`` /
``_owned``), parseo defensivo de payloads, politica de reintentos Celery
(``_CONSOLIDATION_TASK_OPTIONS`` / ``_retry_or_give_up``) y nucleo de la
consolidacion episodica (``_consolidate_session_in_db``).
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import MODEL_ALIAS_AGENT
from app.enums import AuditOperation, MemoryLayer, Mode
from app.llm.clients.base import LLMClient
from app.llm.clients.embedding import EmbeddingClient
from app.llm.clients.reranker import Reranker
from app.llm.errors import LlmTransientError
from app.llm.memory_engine import AgentMemoryEngine
from app.memory.audit import AuditStore
from app.memory.config import RetentionConfig
from app.memory.conversation_turns import ConversationTurnStore
from app.memory.episodic import EpisodicMemoryStore
from app.memory.hashing import compute_record_hash
from app.models.memory import EpisodicMemory
from app.schemas.memory import EpisodicMemoryCreate

logger = logging.getLogger("app.workflows.consolidation")

# Excepciones de control del worker que el motor de memoria NO debe tragar.
_WORKER_CONTROL_ERRORS: tuple[type[BaseException], ...] = (SoftTimeLimitExceeded,)

# Politica de reintentos de las dos tasks de consolidacion.
# ``Any`` es el boundary para desempaquetar ``**kwargs`` heterogeneos en ``@celery_app.task``.
_CONSOLIDATION_TASK_OPTIONS: dict[str, Any] = {
    "autoretry_for": (LlmTransientError,),
    "max_retries": 5,
    "retry_backoff": 30,
    "retry_backoff_max": 600,
    "retry_jitter": True,
    "acks_late": True,
    "soft_time_limit": 180,
    "time_limit": 210,
}


async def _aclose_quietly(client: object) -> None:
    """Cierra un cliente que armo la task (su ``httpx.AsyncClient``), sin propagar."""
    aclose = getattr(client, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception as exc:
        logger.warning("consolidation: no se pudo cerrar un cliente: %s", type(exc).__name__)


def _owned[ClientT](stack: AsyncExitStack, client: ClientT) -> ClientT:
    """Anota ``client`` (armado por la task) para cerrarlo al salir de ``stack``."""
    stack.push_async_callback(_aclose_quietly, client)
    return client


def _parse_source_session_id(session_id: str) -> UUID | None:
    """Parsea ``session_id`` (str) a ``UUID`` de forma defensiva."""
    try:
        return UUID(session_id)
    except (ValueError, AttributeError, TypeError):
        logger.warning("consolidation: session_id no es un UUID valido, source_session_id=None")
        return None


def _parse_origin_mode(mode: str) -> Mode | None:
    """Parsea ``mode`` (str) a ``Mode`` de forma defensiva."""
    try:
        return Mode(mode)
    except ValueError:
        logger.warning("consolidation: mode no es un valor valido de Mode, origin_mode=None")
        return None


def _retry_or_give_up(
    task: Task,
    exc: LlmTransientError | SoftTimeLimitExceeded,
    *,
    label: str,
    user_id: str,
    session_id: str,
) -> None:
    """Re-levanta la falla transitoria para que Celery reintente, o la registra al agotar.

    Agotados los reintentos NO levanta: una task fallida hace que Celery loguee sus
    kwargs (contenido del turno, regla #4). Con reintentos disponibles levanta un
    ``LlmTransientError`` nuevo ``from None`` para no arrastrar el error original.
    """
    attempt = task.request.retries + 1
    max_retries = task.max_retries
    if max_retries is not None and task.request.retries >= max_retries:
        logger.error(
            "%s: reintentos agotados user=%s session=%s intentos=%d: %s (sin datos de usuario)",
            label,
            user_id,
            session_id,
            attempt,
            type(exc).__name__,
        )
        return
    logger.warning(
        "%s: falla transitoria user=%s session=%s intento=%d: %s, se reintenta",
        label,
        user_id,
        session_id,
        attempt,
        type(exc).__name__,
    )
    raise LlmTransientError("se reintenta la consolidacion") from None


def _build_transcript(turns: list) -> str:
    """Reconstruye el transcript plaintext de una sesion a partir de los turnos."""
    lines: list[str] = []
    for turn in turns:
        speaker = "Usuario" if turn.role == "user" else "Asistente"
        lines.append(f"{speaker}: {turn.content}")
    return "\n".join(lines)


async def _episode_exists(session: AsyncSession, source_session_id: UUID) -> bool:
    """``True`` si ya existe un episodio para ``source_session_id`` (idempotencia)."""
    stmt = (
        select(func.count())
        .select_from(EpisodicMemory)
        .where(EpisodicMemory.session_id == source_session_id)
    )
    return ((await session.execute(stmt)).scalar_one() or 0) > 0


async def _consolidate_session_in_db(
    *,
    session: AsyncSession,
    user_id: UUID,
    source_session_id: UUID,
    mode: str,
    origin_mode: Mode | None,
    llm_client: LLMClient,
    embedder: EmbeddingClient,
    reranker: Reranker,
    retention_config: RetentionConfig,
) -> int:
    """Nucleo de la consolidacion episodica sobre una ``session`` ya abierta."""
    if await _episode_exists(session, source_session_id):
        logger.info("consolidate_session: episodio ya existe session=%s, no-op", source_session_id)
        return 0

    turns_store = ConversationTurnStore(session, user_id)
    turns = await turns_store.list_for_session(source_session_id)
    if not turns:
        logger.info("consolidate_session: 0 turnos session=%s, no-op", source_session_id)
        return 0

    transcript = _build_transcript(turns)
    mem_engine = AgentMemoryEngine(
        llm_client, model=MODEL_ALIAS_AGENT, reraise=_WORKER_CONTROL_ERRORS
    )
    summary = await mem_engine.summarize(transcript=transcript, mode=mode)
    if not summary.summary:
        logger.info("consolidate_session: resumen vacio session=%s, no-op", source_session_id)
        return 0

    is_sensitive = origin_mode == Mode.BIENESTAR
    retention_days = (
        retention_config.retention_sensitive_days
        if is_sensitive
        else retention_config.retention_default_days
    )
    payload = EpisodicMemoryCreate(
        session_id=source_session_id,
        summary=summary.summary,
        occurred_at=datetime.now(UTC),
        is_sensitive=is_sensitive,
        retention_days=retention_days,
        topics=summary.topics,
    )

    episodic_store = EpisodicMemoryStore(session, user_id, embedder, reranker)
    audit_store = AuditStore(session, user_id)

    try:
        out = await episodic_store.add(payload)
    except IntegrityError:
        await session.rollback()
        logger.info(
            "consolidate_session: IntegrityError (UNIQUE session_id) session=%s, no-op",
            source_session_id,
        )
        return 0

    await audit_store.record(
        operation=AuditOperation.WRITE,
        target_layer=MemoryLayer.EPISODIC,
        target_id=out.id,
        record_hash=compute_record_hash(summary.summary),
        origin_model_id=summary.model_id,
        origin_mode=origin_mode,
        sensitive=is_sensitive,
    )

    purged = await turns_store.purge_session(source_session_id)
    logger.info(
        "consolidate_session: episodio creado session=%s turnos_purgados=%d",
        source_session_id,
        purged,
    )
    return 1
