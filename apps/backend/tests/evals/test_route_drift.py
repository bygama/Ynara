"""El turno del runner es el que manda ``route``.

Si alguien cambia cómo el chat arma el system, las tools, el alias o la intención de
razonamiento, y el runner no acompaña, la evaluación pasa a medir otra cosa. Este test
corre ``route`` de verdad (con la base mockeada y el reloj fijo) y compara TODO lo que
le llega al cliente con lo que manda el runner para el mismo pedido.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.enums import Mode
from app.llm import router as router_mod
from app.llm.clients.embedding import FakeEmbeddingClient
from app.llm.clients.reranker import FakeReranker
from app.llm.config import load_llm_config
from app.llm.router import route
from app.llm.schemas import ChatRequest, CompletionResult
from evals.harness import build_turn, call_model

_TZ = "America/Argentina/Buenos_Aires"
_NOW = datetime(2026, 11, 25, 10, 30, tzinfo=ZoneInfo(_TZ))
_TEXT = "Agendame turno con el dentista mañana a las 15"


class _SpyClient:
    """Guarda los argumentos de cada ``complete``, sin filtrar ninguno."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> CompletionResult:
        self.calls.append(kwargs)
        return CompletionResult(
            text="ok",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
            model_name="org/model",
            latency_ms=1.0,
        )


@pytest.mark.parametrize("mode", list(Mode))
async def test_runner_sends_what_route_sends(mode: Mode, monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_llm_config()
    # Reloj fijo y memoria vacía: lo único que el runner no toma de producción.
    monkeypatch.setattr(router_mod, "current_now", lambda *_a, **_k: _NOW)
    monkeypatch.setattr(router_mod, "render_context_block", AsyncMock(return_value=""))

    from_route = _SpyClient()
    await route(
        ChatRequest(text=_TEXT, mode=mode, session_id="s-drift"),
        session=MagicMock(),
        user_id=uuid.uuid4(),
        llm_client=from_route,  # type: ignore[arg-type]
        embedder=FakeEmbeddingClient(),
        reranker=FakeReranker(),
        config=config,
        tz=_TZ,
    )

    from_runner = _SpyClient()
    turn = build_turn(mode, _TEXT, now=_NOW, tz=_TZ, config=config)
    await call_model(from_runner, turn)  # type: ignore[arg-type]

    # Mismos argumentos y mismos valores: ni un ``max_tokens`` ni una ``temperature``
    # que el chat no mande.
    assert from_runner.calls[0] == from_route.calls[0]
    assert set(from_route.calls[0]) == {"model", "messages", "tools", "thinking"}


def test_case_now_is_expressed_in_the_case_timezone() -> None:
    """Un «ahora» escrito en UTC llega al modelo como hora local, igual que en el chat."""
    same_instant_utc = datetime(2026, 11, 25, 13, 30, tzinfo=UTC)
    local = build_turn(Mode.VIDA, _TEXT, now=_NOW, tz=_TZ)
    from_utc = build_turn(Mode.VIDA, _TEXT, now=same_instant_utc, tz=_TZ)
    assert from_utc.messages[1].content == local.messages[1].content
    assert "10:30" in str(local.messages[1].content)
