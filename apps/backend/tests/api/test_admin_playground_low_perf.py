"""Tests unitarios del preset "bajo rendimiento" del playground admin (F1 ADR-018).

El preset fija un timeout por request propio. El ``ResilientClient`` lo aplica a cada
intento, así que nunca puede pasar el timeout de la política
(``PROVIDER_REQUEST_TIMEOUT_S``): si lo pasara, los reintentos del principal
agotarían el presupuesto total antes de probar el respaldo.

Sin DB ni red: ``_resolve_playground_request`` solo lee ``Settings`` (patcheado acá,
sin ``.env``) y el ``ynara.config.json`` del repo. Los tests del endpoint con DB viven
en ``test_admin_playground.py`` (integración).
"""

from __future__ import annotations

import json
from unittest.mock import patch

from app.api.v1.admin.playground import _LOW_PERF_TIMEOUT_S, _resolve_playground_request
from app.core.config import Settings
from app.llm.clients.factory import PROVIDER_REQUEST_TIMEOUT_S, check_resilience_budget
from app.schemas.admin_api import PlaygroundIn, PlaygroundParams


def _api_settings() -> Settings:
    """Settings con ``LLM_BACKEND=api`` y un proveedor: el playground no corta con 409."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        DATABASE_URL="postgresql://test:test@localhost/test",
        REDIS_URL="redis://localhost:6379/0",
        JWT_SECRET="test-secret-no-usar-en-prod-min-32b",
        LLM_BACKEND="api",
        LLM_PROVIDERS=json.dumps(
            [
                {
                    "name": "principal",
                    "base_url": "https://api.proveedor.test/v1",
                    "models": {"conversacional": "org/chat", "agente": "org/agent"},
                }
            ]
        ),
        llm_api_keys={"principal": "sk-test-principal-0123"},
    )


def _resolve(*, low_perf: bool) -> float | None:
    """``timeout_s`` que el playground le pasa al cliente para un turno del alias agente."""
    body = PlaygroundIn(model="agente", message="hola", params=PlaygroundParams(low_perf=low_perf))
    with patch("app.api.v1.admin.playground.get_settings", return_value=_api_settings()):
        return _resolve_playground_request(body).timeout_s


def test_low_perf_timeout_never_exceeds_provider_timeout() -> None:
    """El preset acorta el timeout de la política, nunca lo alarga."""
    assert _LOW_PERF_TIMEOUT_S <= PROVIDER_REQUEST_TIMEOUT_S


def test_low_perf_timeout_leaves_room_for_fallback() -> None:
    """Con el timeout del preset, el respaldo entra en el presupuesto del ResilientClient."""
    check_resilience_budget(timeout_s=_LOW_PERF_TIMEOUT_S)


def test_low_perf_request_uses_preset_timeout() -> None:
    """Un turno con ``low_perf`` le pasa al cliente el timeout del preset."""
    assert _resolve(low_perf=True) == _LOW_PERF_TIMEOUT_S


def test_request_without_low_perf_keeps_policy_timeout() -> None:
    """Sin ``low_perf`` no se pisa el timeout: el cliente usa el de la política."""
    assert _resolve(low_perf=False) is None
