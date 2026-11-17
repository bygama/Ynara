"""Tests del ``ClientPool`` + ``build_provider_pool`` (M3, ADR-028).

Usan ``FakeLlmClient`` como doble: no hay red. Verifican el filtrado por
``serves_model``, la estrategia de ruteo, el error cuando nadie sirve el
modelo y el armado de la lista de proveedores.
"""

from __future__ import annotations

import pytest

from app.llm.clients.fakes import FakeLlmClient
from app.llm.clients.pool import (
    ClientPool,
    FirstHealthy,
    RoutingStrategy,
    build_provider_pool,
)
from app.llm.errors import ModelNotServedError

_QWEN = "qwen-3.5-9b"
_GEMMA = "gemma-4-12b"


def _fake(model: str) -> FakeLlmClient:
    return FakeLlmClient(models=frozenset({model}))


def _pool(clients: list[FakeLlmClient], strategy: RoutingStrategy | None = None) -> ClientPool:
    return ClientPool(list(clients), strategy or FirstHealthy())


# ---------- candidates ----------


def test_candidates_filters_by_serves_model() -> None:
    qwen = _fake(_QWEN)
    gemma = _fake(_GEMMA)
    pool = _pool([qwen, gemma])
    assert pool.candidates(_QWEN) == [qwen]
    assert pool.candidates(_GEMMA) == [gemma]
    assert pool.candidates("no-servido") == []


def test_candidates_preserves_pool_order() -> None:
    primary = _fake(_QWEN)
    secondary = _fake(_QWEN)
    pool = _pool([primary, secondary])
    assert pool.candidates(_QWEN) == [primary, secondary]


# ---------- pick ----------


def test_pick_returns_first_healthy() -> None:
    primary = _fake(_QWEN)
    secondary = _fake(_QWEN)
    pool = _pool([primary, secondary])
    assert pool.pick(_QWEN) is primary


def test_pick_uses_strategy() -> None:
    primary = _fake(_QWEN)
    secondary = _fake(_QWEN)

    class _PickLast:
        def pick(self, candidates: list[object]) -> object:
            return candidates[-1]

    pool = _pool([primary, secondary], strategy=_PickLast())  # type: ignore[arg-type]
    assert pool.pick(_QWEN) is secondary


def test_pick_without_candidates_raises() -> None:
    pool = _pool([_fake(_QWEN)])
    with pytest.raises(ModelNotServedError):
        pool.pick("no-servido")


def test_first_healthy_satisfies_protocol() -> None:
    assert isinstance(FirstHealthy(), RoutingStrategy)


def test_pool_routes_each_model_to_its_own_client() -> None:
    """Regression #206: cada client anuncia solo SUS modelos.

    Con dos clients (uno por modelo), ``pick`` devuelve el que sirve el modelo
    pedido aunque no sea el primero del pool.
    """
    gemma = _fake("gemma4")
    qwen = _fake("qwen")
    pool = _pool([gemma, qwen])
    assert pool.pick("qwen") is qwen
    assert pool.pick("gemma4") is gemma


# ---------- build_provider_pool (ADR-028) ----------


class _Provider(FakeLlmClient):
    """``FakeLlmClient`` con ``name``, como ``OpenAICompatClient``."""

    def __init__(self, name: str, *models: str) -> None:
        super().__init__(models=frozenset(models))
        self.name = name


def test_build_provider_pool_keeps_priority_order() -> None:
    principal = _Provider("principal", _QWEN, _GEMMA)
    respaldo = _Provider("respaldo", _QWEN)
    pool = build_provider_pool([principal, respaldo])
    assert pool.clients == [principal, respaldo]
    assert pool.candidates(_QWEN) == [principal, respaldo]
    assert pool.candidates(_GEMMA) == [principal]  # solo lo sirve el principal
    assert pool.pick(_QWEN) is principal


def test_build_provider_pool_accepts_unnamed_clients() -> None:
    pool = build_provider_pool([_fake(_QWEN), _fake(_QWEN)])
    assert len(pool.clients) == 2


@pytest.mark.parametrize(
    ("clients", "match"),
    [
        ([], "al menos un proveedor"),
        ([_Provider("principal", _QWEN), _Provider("principal", _GEMMA)], "mismo nombre"),
    ],
)
def test_build_provider_pool_rejects_incoherent_config(
    clients: list[FakeLlmClient], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        build_provider_pool(clients)


def test_build_provider_pool_rejects_same_client_twice() -> None:
    principal = _Provider("principal", _QWEN)
    with pytest.raises(ValueError, match="dos veces"):
        build_provider_pool([principal, principal])
