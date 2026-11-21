"""Tests del ``CircuitBreaker`` con clock inyectado (M3).

El clock es una lista de un elemento mutable: avanzamos el tiempo seteando
``clock_value[0]`` y le pasamos al breaker un callable que lo lee. Asi los
tests controlan el reloj sin dormir de verdad.
"""

from __future__ import annotations

from app.llm.clients.circuit import CircuitBreaker, CircuitState


class _Clock:
    """Reloj fake controlable: ``now`` avanza a mano."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _breaker(clock: _Clock, *, threshold: int = 3, recovery: float = 30.0) -> CircuitBreaker:
    return CircuitBreaker(
        failure_threshold=threshold,
        recovery_timeout_s=recovery,
        clock=clock,
    )


def test_starts_closed() -> None:
    breaker = _breaker(_Clock())
    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow() is True


def test_opens_on_threshold() -> None:
    breaker = _breaker(_Clock(), threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN


def test_open_blocks_before_recovery() -> None:
    clock = _Clock()
    breaker = _breaker(clock, threshold=1, recovery=30.0)
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    clock.now = 29.0  # todavia dentro del timeout
    assert breaker.allow() is False
    assert breaker.state is CircuitState.OPEN


def test_open_transitions_to_half_open_after_recovery() -> None:
    clock = _Clock()
    breaker = _breaker(clock, threshold=1, recovery=30.0)
    breaker.record_failure()
    clock.now = 30.0  # exactamente el timeout: permite la prueba
    assert breaker.allow() is True
    assert breaker.state is CircuitState.HALF_OPEN


def test_half_open_success_closes() -> None:
    clock = _Clock()
    breaker = _breaker(clock, threshold=1, recovery=30.0)
    breaker.record_failure()
    clock.now = 30.0
    assert breaker.allow() is True  # -> HALF_OPEN
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow() is True


def test_half_open_failure_reopens() -> None:
    clock = _Clock()
    breaker = _breaker(clock, threshold=1, recovery=30.0)
    breaker.record_failure()
    clock.now = 30.0
    assert breaker.allow() is True  # -> HALF_OPEN
    breaker.record_failure()  # la prueba fallo
    assert breaker.state is CircuitState.OPEN
    # el temporizador se reinicia: bloquea de nuevo hasta el proximo timeout.
    assert breaker.allow() is False
    clock.now = 60.0
    assert breaker.allow() is True


def test_half_open_allows_only_one_probe() -> None:
    """En HALF_OPEN solo pasa UNA prueba; las concurrentes se bloquean hasta
    que la primera cierre (record_success/failure)."""
    clock = _Clock()
    breaker = _breaker(clock, threshold=1, recovery=30.0)
    breaker.record_failure()
    clock.now = 30.0
    assert breaker.allow() is True  # primera prueba -> HALF_OPEN
    assert breaker.allow() is False  # segunda prueba concurrente: bloqueada
    assert breaker.allow() is False
    breaker.record_success()  # la prueba cerro con exito -> CLOSED
    assert breaker.allow() is True


def test_success_resets_failure_count() -> None:
    breaker = _breaker(_Clock(), threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()  # resetea el contador
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED  # solo 2 fallos tras el reset
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN


def test_would_allow_has_no_side_effects() -> None:
    """Responde lo mismo que ``allow`` sin pasar a HALF_OPEN ni consumir la prueba."""
    clock = _Clock()
    breaker = _breaker(clock, threshold=1, recovery=30.0)
    assert breaker.would_allow() is True  # CLOSED
    breaker.record_failure()
    clock.now = 29.0
    assert breaker.would_allow() is False  # OPEN, todavia dentro del timeout
    clock.now = 30.0
    assert breaker.would_allow() is True
    assert breaker.would_allow() is True  # preguntar de nuevo no consume la prueba
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow() is True  # la prueba sigue disponible -> HALF_OPEN
    assert breaker.would_allow() is False  # prueba en curso
    breaker.record_success()
    assert breaker.would_allow() is True


def test_cancel_probe_releases_half_open_without_recording_outcome() -> None:
    """Cancelar una prueba HALF_OPEN libera el cupo sin abrir ni cerrar el breaker."""
    clock = _Clock()
    breaker = _breaker(clock, threshold=1, recovery=30.0)
    breaker.record_failure()
    clock.now = 30.0
    assert breaker.allow() is True  # -> HALF_OPEN con prueba en vuelo
    assert breaker.allow() is False
    breaker.cancel_probe()
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.would_allow() is True
    assert breaker.allow() is True  # permite volver a probar y vuelve a reservar el cupo
    assert breaker.allow() is False  # concurrencia bloqueada mientras la 2da prueba esta en vuelo
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED
