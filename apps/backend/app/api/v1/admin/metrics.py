"""Métricas del panel admin: 5 GET de negocio (read-only) + ``/admin/system``.

Capa FINA: los 5 endpoints de negocio (overview / users / modes / moat / audit) delegan
en ``AdminMetricsService`` (``app/services/admin_metrics.py``); este módulo solo declara
las rutas, valida los query params (rango, facets, paginación) y arma el service.

``GET /admin/system`` queda acá (no en el service): es salud OPERACIONAL —probes de
Postgres/Redis + guard anti-prod + inventario de runtime + proveedores de IA—, no una
métrica de negocio, y necesita ``app.state`` (Redis y los clientes de IA singleton) + el
head de Alembic. Lee ``app.state``, no la DB de negocio.

Gate: todos con ``CurrentAdmin`` (``get_current_admin``): sin admin -> 401 estático.
Privacidad (regla #4): el service NUNCA descifra contenido ni expone ``record_hash`` /
``target_id``; ver su docstring.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol, runtime_checkable

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.core.config import Settings, get_settings
from app.core.constants import EMBEDDING_DIM
from app.core.db_guard import _host_of, is_prod_db_host
from app.core.deps import CurrentAdmin, DbSession, get_embedder, get_llm_client
from app.core.llm_protocol import EmbeddingClientProtocol, LLMClientProtocol
from app.enums import AuditOperation, MemoryLayer, Mode, enum_values
from app.llm.config import load_llm_config
from app.llm.schemas import ModelHealth
from app.models.audit import ORIGIN_MODEL_ID_MAX_LENGTH
from app.schemas.admin import (
    AdminMoatOut,
    AdminModesOut,
    AdminOverviewOut,
    AdminSystemOut,
    AdminUsersOut,
    AiBackend,
    AiStatus,
    ServiceStatus,
    SystemAi,
    SystemEmbeddings,
    SystemGuard,
    SystemLlm,
    SystemLlmProvider,
    SystemModelRoute,
    SystemRuntime,
    SystemServices,
)
from app.schemas.admin_api import AdminAuditPage
from app.services.admin_metrics import AdminMetricsService

router = APIRouter()

# Rango temporal soportado por el dashboard (default 7d). /system NO toma rango.
RangeId = Literal["24h", "7d", "30d", "90d"]
RangeParam = Annotated[RangeId, Query()]

# Default + cap de la paginación del audit (igual que ``/v1/sessions`` / ``/v1/memory``).
_AUDIT_LIMIT_DEFAULT = 50
_AUDIT_LIMIT_MAX = 100


@router.get("/admin/overview", response_model=AdminOverviewOut, status_code=200)
async def admin_overview(
    session: DbSession,
    admin_id: CurrentAdmin,
    range: RangeParam = "7d",
) -> AdminOverviewOut:
    """KPIs + serie de sesiones + mix de modos + preview de audit, para el rango pedido."""
    return await AdminMetricsService(session).overview(range)


@router.get("/admin/users", response_model=AdminUsersOut, status_code=200)
async def admin_users(
    session: DbSession,
    admin_id: CurrentAdmin,
    range: RangeParam = "7d",
) -> AdminUsersOut:
    """Actividad aprox. (DAU/WAU/MAU por sesiones), heatmap, conversión estimada, signups."""
    return await AdminMetricsService(session).users(range)


@router.get("/admin/modes", response_model=AdminModesOut, status_code=200)
async def admin_modes(
    session: DbSession,
    admin_id: CurrentAdmin,
    range: RangeParam = "7d",
) -> AdminModesOut:
    """Mix de sesiones por modo + duración media (solo cerradas) por modo, en la ventana."""
    return await AdminMetricsService(session).modes(range)


@router.get("/admin/moat", response_model=AdminMoatOut, status_code=200)
async def admin_moat(
    session: DbSession,
    admin_id: CurrentAdmin,
    range: RangeParam = "7d",
) -> AdminMoatOut:
    """Conteos por capa + crecimiento + salud procedural + consolidación. CERO descifrado."""
    return await AdminMetricsService(session).moat(range)


@router.get("/admin/audit", response_model=AdminAuditPage, status_code=200)
async def admin_audit(
    session: DbSession,
    admin_id: CurrentAdmin,
    range: RangeParam = "7d",
    operation: Annotated[AuditOperation | None, Query()] = None,
    target_layer: Annotated[MemoryLayer | None, Query()] = None,
    origin_mode: Annotated[Mode | None, Query()] = None,
    origin_model_id: Annotated[
        str | None, Query(min_length=1, max_length=ORIGIN_MODEL_ID_MAX_LENGTH)
    ] = None,
    sensitive: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=_AUDIT_LIMIT_MAX)] = _AUDIT_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminAuditPage:
    """Página de audit filtrable. NUNCA expone ``record_hash`` ni ``target_id`` (ver service).

    ``origin_model_id`` filtra por el id exacto del modelo que originó la op (ADR-028).
    """
    return await AdminMetricsService(session).audit(
        range_id=range,
        operation=operation,
        target_layer=target_layer,
        origin_mode=origin_mode,
        origin_model_id=origin_model_id,
        sensitive=sensitive,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# GET /v1/admin/system — infra + guard + runtime + IA (operacional, no métrica)
# ---------------------------------------------------------------------------


async def _timed_probe(action: Callable[[], Awaitable[object]], *, label: str) -> ServiceStatus:
    """Ejecuta un probe (``action``) midiendo latencia + capturando el tipo de error.

    Centraliza el patrón timing + try/except + construcción de ``ServiceStatus`` que
    comparten todos los probes de infra: en éxito reporta ``label`` como detalle; en
    fallo, solo el nombre de la excepción (NUNCA el mensaje, que podría filtrar credenciales
    o estructura interna). ``action`` es un callable (no un awaitable ya creado) para que el
    armado del coroutine —y cualquier acceso a ``app.state`` que pueda fallar— quede DENTRO
    del try. Cada probe concreto solo aporta su ``await`` específico.
    """
    started = time.perf_counter()
    try:
        await action()
        detail, up = label, True
    except Exception as exc:
        detail, up = type(exc).__name__, False
    latency = round((time.perf_counter() - started) * 1000, 2)
    return ServiceStatus(up=up, latency_ms=latency, detail=detail, checked_at=datetime.now(UTC))


async def _probe_postgres(session: AsyncSession) -> ServiceStatus:
    """``SELECT 1`` contra la DB de la sesión actual. Reporta solo el tipo de error."""
    return await _timed_probe(lambda: session.execute(text("SELECT 1")), label="SELECT 1")


async def _probe_redis(request: Request) -> ServiceStatus:
    """PING al cliente Redis singleton (``app.state.redis``). Solo el tipo de error."""
    return await _timed_probe(lambda: request.app.state.redis.ping(), label="PING")


def _schema_head() -> str:
    """Head de Alembic leído del directorio de migraciones (sin tocar la DB).

    Defensivo: si no se puede resolver (config ausente), devuelve "unknown" en vez de
    romper el endpoint de salud.
    """
    try:
        from pathlib import Path

        from alembic.config import Config
        from alembic.script import ScriptDirectory

        # ``apps/backend/alembic.ini`` — desde app/api/v1/admin/metrics.py son 4 niveles
        # (admin -> v1 -> api -> app -> backend = parents[4]).
        ini = Path(__file__).resolve().parents[4] / "alembic.ini"
        cfg = Config(str(ini))
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
        return heads[0] if heads else "unknown"
    except Exception:
        return "unknown"


@runtime_checkable
class _ProviderHealthReporter(Protocol):
    """Cliente LLM que sabe la salud de cada proveedor (el ``ResilientClient``)."""

    async def provider_health(self) -> list[ModelHealth]: ...


def _ai_status(backend: AiBackend, healthy: bool | None) -> AiStatus:
    """Estado de un proveedor de IA para el panel.

    Con el backend ``fake`` no se sondea a nadie: ``unconfigured``. Con ``api`` es ``ok``
    solo si el proveedor respondió el health; uno que no se pudo sondear (``None``)
    cuenta como ``down``, nunca como sano.
    """
    if backend != "api":
        return "unconfigured"
    return "ok" if healthy else "down"


async def _probe_llm(settings: Settings, llm_client: LLMClientProtocol) -> SystemLlm:
    """Los proveedores de ``LLM_PROVIDERS`` con su host, sus modelos y su estado.

    Con ``api`` sondea a todos a la vez (``provider_health``); si el sondeo levanta,
    todos quedan caídos. El health lo hace cada cliente del proveedor (regla #4b): acá no
    hay HTTP. La credencial no sale: solo el host de la URL base, como el ``db_target``
    del guard.
    """
    healthy: dict[str, bool] = {}
    if settings.llm_backend == "api" and isinstance(llm_client, _ProviderHealthReporter):
        try:
            healthy = {h.model_name: h.healthy for h in await llm_client.provider_health()}
        except Exception:
            healthy = {}
    providers = [
        SystemLlmProvider(
            name=provider.name,
            host=_host_of(provider.base_url) or "unknown",
            models=[
                SystemModelRoute(alias=alias, model_id=model_id)
                for alias, model_id in sorted(provider.models.items())
            ],
            status=_ai_status(settings.llm_backend, healthy.get(provider.name)),
        )
        for provider in settings.llm_providers
    ]
    return SystemLlm(backend=settings.llm_backend, providers=providers)


async def _probe_embeddings(
    settings: Settings, embedder: EmbeddingClientProtocol
) -> SystemEmbeddings:
    """El cliente de embeddings: modo, host, modelo, dimensión y estado.

    Con ``api`` usa el ``health()`` del cliente; uno que levanta cuenta como caído.
    """
    healthy: bool | None = None
    if settings.embedding_backend == "api":
        try:
            healthy = (await embedder.health()).healthy
        except Exception:
            healthy = False
    return SystemEmbeddings(
        backend=settings.embedding_backend,
        host=_host_of(settings.embedding_base_url) or None,
        model=settings.embedding_model,
        dim=EMBEDDING_DIM,
        status=_ai_status(settings.embedding_backend, healthy),
    )


async def _probe_ai(
    settings: Settings, llm_client: LLMClientProtocol, embedder: EmbeddingClientProtocol
) -> SystemAi:
    """Proveedores de IA (ADR-028): el LLM y los embeddings, sondeados a la vez."""
    llm, embeddings = await asyncio.gather(
        _probe_llm(settings, llm_client), _probe_embeddings(settings, embedder)
    )
    return SystemAi(llm=llm, embeddings=embeddings)


@router.get("/admin/system", response_model=AdminSystemOut, status_code=200)
async def admin_system(
    request: Request,
    session: DbSession,
    admin_id: CurrentAdmin,
    llm_client: Annotated[LLMClientProtocol, Depends(get_llm_client)],
    embedder: Annotated[EmbeddingClientProtocol, Depends(get_embedder)],
) -> AdminSystemOut:
    """Salud de infra + guard anti-prod + inventario de runtime + proveedores de IA.

    Sin queries de negocio.
    """
    settings = get_settings()

    is_prod_in_dev = settings.environment != "production" and is_prod_db_host(settings.database_url)
    # ``db_target``: solo el host (NUNCA el connection string con credenciales, regla #2).
    guard = SystemGuard(
        active=settings.environment != "production",
        db_target=_host_of(settings.database_url) or "unknown",
        is_prod_in_dev=is_prod_in_dev,
    )

    postgres = await _probe_postgres(session)
    redis = await _probe_redis(request)

    models = sorted(load_llm_config().models)
    runtime = SystemRuntime(
        models=models,
        modes=enum_values(Mode),
        schema_head=_schema_head(),
        build_version=__version__,
    )

    return AdminSystemOut(
        guard=guard,
        services=SystemServices(postgres=postgres, redis=redis),
        runtime=runtime,
        ai=await _probe_ai(settings, llm_client, embedder),
    )
