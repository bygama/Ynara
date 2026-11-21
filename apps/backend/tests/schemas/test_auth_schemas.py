"""Tests unitarios de validación de ``RegisterRequest`` y ``LoginRequest``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.auth import LoginRequest, RegisterRequest


@pytest.mark.parametrize(
    "password",
    [
        "corta1234",  # 9 chars (< 10)
        "sololetraslargas",  # sin dígitos
        "123456789012",  # sin letras
        "password123",  # común
        "Password1234",  # común (case-insensitive)
        "qwerty12345",  # común
        "ynara12345",  # común
        "          ",  # solo espacios
        "a" * 129 + "1",  # > 128
    ],
)
def test_register_rejects_weak_passwords(password: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        RegisterRequest(email="usuario@example.com", password=password)
    # Los mensajes de error no deben repetir la contraseña enviada (regla #4).
    for err in exc_info.value.errors():
        assert password not in str(err.get("msg", ""))


def test_register_rejects_password_containing_email_local_part() -> None:
    pwd = "MiMateo.Gomez2026"
    with pytest.raises(ValidationError) as exc_info:
        RegisterRequest(email="mateo.gomez@example.com", password=pwd)
    errors = exc_info.value.errors()
    assert errors[0]["loc"] == ("password",)
    assert pwd not in str(errors[0].get("msg", ""))
    assert pwd not in str(exc_info.value)


def test_register_accepts_strong_password_and_short_email_local_part() -> None:
    req = RegisterRequest(email="ana@example.com", password="anacondaverde2026")
    assert req.password == "anacondaverde2026"

    req2 = RegisterRequest(email="usuario@example.com", password="supersecreta1")
    assert req2.password == "supersecreta1"


def test_login_request_keeps_no_password_policy_constraints() -> None:
    """Login no valida política de longitud ni complejidad (sin oráculo ni breakage)."""
    req = LoginRequest(email="usuario@example.com", password="123")
    assert req.password == "123"
