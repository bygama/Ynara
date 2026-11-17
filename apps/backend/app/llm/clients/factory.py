"""Factory de clientes LLM / embedder / reranker.

Centraliza la decision entre los clientes fake y los reales, por settings:

- ``LLM_BACKEND=fake`` (el default: dev y CI) -> ``FakeLlmClient`` determinista, sin
  red.
- ``LLM_BACKEND=api`` -> un ``OpenAICompatClient`` por entrada de ``LLM_PROVIDERS``, en
  orden de prioridad, reunidos en un pool y envueltos en un ``ResilientClient`` (retry,
  breaker por proveedor y respaldo, ADR-028 D2). En production es el unico valor que
  arranca (``Settings._require_llm_api``).
- ``EMBEDDING_BACKEND=api`` -> ``ApiEmbeddingClient`` contra el proveedor de embeddings
  (ADR-028 D4); ``fake`` (el default) -> ``FakeEmbeddingClient``. En production es
  ``api`` (``Settings._require_embedding_api``).
- El reranker es siempre el ``FakeReranker`` passthrough, en todos los entornos
  (ADR-028 D5): no hay variable que lo cambie.

Los fakes quedan DETRAS de la condicion, no hardcodeados en el lifespan: pasar a los
clientes reales es cambiar settings. La factory no abre conexiones al construir
(``httpx.AsyncClient`` no disca hasta el primer request), asi que es seguro llamarla
en el arranque.

No importa FastAPI (regla de capas): solo ``Settings`` + ``LlmRuntimeConfig`` +
``httpx`` + los clientes de ``app.llm.clients``.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import LlmProvider, Settings
from app.llm.clients.base import LLMClient
from app.llm.clients.embedding import EmbeddingClient, FakeEmbeddingClient
from app.llm.clients.fakes import FakeLlmClient
from app.llm.clients.parsers import OpenAIToolCallParser
from app.llm.clients.pool import build_provider_pool
from app.llm.clients.providers.embeddings import ApiEmbeddingClient
from app.llm.clients.providers.openai_compat import OpenAICompatClient
from app.llm.clients.reranker import RERANKER_PASSTHROUGH, FakeReranker, Reranker
from app.llm.clients.resilient import ResilientClient
from app.llm.config import LlmRuntimeConfig

# Politica de resiliencia del LLM por API. Para que un proveedor colgado deje lugar al
# respaldo, el peor caso del principal (todos sus intentos con timeout, mas el backoff
# entre ellos) mas un request al respaldo tiene que entrar en el presupuesto total del
# ``ResilientClient``: 3 x 20 s + 2,5 s + 20 s = 82,5 s < 90 s
# (``check_resilience_budget``). El timeout cubre la generacion entera: el chat usa
# ``complete``, que espera la respuesta completa.
PROVIDER_REQUEST_TIMEOUT_S = 20.0
PROVIDER_MAX_ATTEMPTS = 3
PROVIDER_BASE_BACKOFF_S = 0.5
PROVIDER_MAX_BACKOFF_S = 8.0
PROVIDER_TOTAL_BUDGET_S = 90.0

# ``reasoning_effort`` que se pide cuando el rol piensa (``thinking=True``) a un
# proveedor con ``supports_reasoning_effort``. Para ``thinking=False`` no se manda nada:
# el valor que lo apaga cambia de proveedor a proveedor.
_REASONING_EFFORT_ON = "medium"


def check_resilience_budget(
    *,
    timeout_s: float = PROVIDER_REQUEST_TIMEOUT_S,
    max_attempts: int = PROVIDER_MAX_ATTEMPTS,
    base_backoff_s: float = PROVIDER_BASE_BACKOFF_S,
    max_backoff_s: float = PROVIDER_MAX_BACKOFF_S,
    total_budget_s: float = PROVIDER_TOTAL_BUDGET_S,
) -> None:
    """``ValueError`` si el respaldo no entra en el presupuesto despues del peor caso.

    El peor caso del principal son ``max_attempts`` requests cortados por timeout mas
    el backoff maximo entre ellos (exponencial con jitter, con techo, como
    ``RetryPolicy.backoff``). Despues tiene que quedar lugar para un request al
    respaldo; si no, el presupuesto corta antes y la respuesta degrada aunque el
    respaldo este sano.
    """
    backoff_s = sum(
        min(min(base_backoff_s * 2**attempt, max_backoff_s) + base_backoff_s, max_backoff_s)
        for attempt in range(max_attempts - 1)
    )
    worst_case_s = max_attempts * timeout_s + backoff_s + timeout_s
    if worst_case_s >= total_budget_s:
        raise ValueError(
            f"el timeout por request ({timeout_s} s) no deja lugar al respaldo: "
            f"{max_attempts} intentos + backoff + respaldo suman {worst_case_s} s, "
            f"y el presupuesto es {total_budget_s} s"
        )


def _quiet_httpx_request_logs() -> None:
    """Deja el logger de httpx en WARNING: en INFO loguea cada request con la URL.

    La factory lo llama al armar un cliente que habla con un proveedor (chat o
    embeddings), asi la URL del proveedor no queda en los logs del proceso.
    """
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _wants_real_llm(settings: Settings) -> bool:
    """``True`` si el LLM es el de los proveedores por API (``LLM_BACKEND=api``).

    En production es siempre asi: ``Settings`` no arranca con el fake.
    """
    return settings.llm_backend == "api"


def _build_provider_client(
    provider: LlmProvider, settings: Settings, parser: OpenAIToolCallParser
) -> OpenAICompatClient:
    """Un ``OpenAICompatClient`` para una entrada de ``LLM_PROVIDERS``."""
    return OpenAICompatClient(
        name=provider.name,
        base_url=provider.base_url,
        api_key=settings.llm_api_keys[provider.name],
        model_map=provider.models,
        http_client=httpx.AsyncClient(),
        reasoning_effort_on=_REASONING_EFFORT_ON if provider.supports_reasoning_effort else None,
        reasoning_effort_off=None,
        parser=parser,
        default_timeout_s=PROVIDER_REQUEST_TIMEOUT_S,
    )


def build_llm_client(settings: Settings, config: LlmRuntimeConfig) -> LLMClient:
    """Construye el cliente LLM segun ``LLM_BACKEND``: el de los proveedores o el fake.

    ``api``: un ``OpenAICompatClient`` por proveedor, en el orden de ``LLM_PROVIDERS``
    (el primero es el principal), en un pool con respaldo y envuelto en un
    ``ResilientClient``. ``Settings`` ya valido que haya proveedores y que cada uno
    tenga su credencial.

    ``fake`` (default dev/test): ``FakeLlmClient`` que sirve los alias de la config.
    No abre red.
    """
    if not _wants_real_llm(settings):
        return FakeLlmClient(models=frozenset(config.models))

    check_resilience_budget()
    _quiet_httpx_request_logs()
    parser = OpenAIToolCallParser()
    clients: list[LLMClient] = [
        _build_provider_client(provider, settings, parser) for provider in settings.llm_providers
    ]
    return ResilientClient(
        build_provider_pool(clients),
        max_attempts=PROVIDER_MAX_ATTEMPTS,
        base_backoff_s=PROVIDER_BASE_BACKOFF_S,
        max_backoff_s=PROVIDER_MAX_BACKOFF_S,
        total_budget_s=PROVIDER_TOTAL_BUDGET_S,
    )


def build_embedder(settings: Settings) -> EmbeddingClient:
    """Construye el cliente de embeddings segun ``EMBEDDING_BACKEND``.

    ``api`` -> ``ApiEmbeddingClient`` contra ``EMBEDDING_BASE_URL``, con la credencial
    ``EMBEDDING_API_KEY``, el modelo ``EMBEDDING_MODEL``, lotes de hasta
    ``EMBEDDING_BATCH_SIZE`` textos y el timeout ``EMBEDDING_TIMEOUT_S`` (ADR-028 D4).
    ``Settings`` ya valido que esten la URL, la credencial y el modelo. No abre red al
    construirse (``httpx.AsyncClient`` perezoso), asi que es seguro instanciarlo en el
    arranque.

    ``fake`` (default dev/test) -> ``FakeEmbeddingClient`` determinista, sin red.
    """
    if settings.embedding_backend != "api":
        return FakeEmbeddingClient(model=settings.embedding_model)
    _quiet_httpx_request_logs()
    return ApiEmbeddingClient(
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        http_client=httpx.AsyncClient(),
        model=settings.embedding_model,
        batch_size=settings.embedding_batch_size,
        default_timeout_s=settings.embedding_timeout_s,
    )


def build_reranker() -> Reranker:
    """El reranker de todos los entornos: el ``FakeReranker`` passthrough (ADR-028 D5).

    Deja el orden del ANN tal cual y se reporta como ``RERANKER_PASSTHROUGH``. Va con
    ``record_calls=False``: es un singleton de la app y no guarda las llamadas. Un
    rerank real por API queda como palanca futura, a decidir con una medicion de
    recall; no depende de settings.
    """
    return FakeReranker(model=RERANKER_PASSTHROUGH, record_calls=False)


def build_llm_clients(
    settings: Settings, config: LlmRuntimeConfig
) -> tuple[LLMClient, EmbeddingClient, Reranker]:
    """Construye los tres clientes de una (``llm_client``, ``embedder``, ``reranker``).

    Helper de conveniencia para el lifespan: una sola llamada arma el trio de
    singletons. Cada uno se decide por separado (ver ``build_llm_client`` /
    ``build_embedder`` / ``build_reranker``).
    """
    return (
        build_llm_client(settings, config),
        build_embedder(settings),
        build_reranker(),
    )
