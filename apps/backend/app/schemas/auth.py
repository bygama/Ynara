"""Schemas HTTP del modulo ``/v1/auth`` (register + login).

Contrato wire de auth: separado de los schemas de dominio de usuario
(``app/schemas/user.py``). Hereda ``YnaraBaseModel`` (strict=True,
extra=forbid). La respuesta de ``register`` es ``UserOut`` (reusa el schema de
usuario, que NUNCA expone ``password_hash``); ``token`` devuelve ``TokenOut``.

NO se reusa ``UserCreate`` como request de register a proposito: su ``password``
es opcional y ademas trae ``is_ephemeral`` / ``retention_sensitive_days`` /
``onboarding_completed``, lo que abriria un mass-assignment desde el wire. El
request de register es un schema acotado a (email, password, display_name).

Nota de strict mode + el body de FastAPI (mismo caso que ``ChatHttpRequest``):
    ``YnaraBaseModel`` hereda ``strict=True``. FastAPI parsea el body JSON a un
    ``dict`` y valida con ``model_validate(dict)``. Los campos de los requests
    de auth son ``str`` / ``EmailStr`` (sin coercion enum/UUID), asi que strict
    los acepta tal cual del wire. Aun asi se override-a ``strict=False`` en
    ``RegisterRequest`` / ``LoginRequest`` por consistencia con el resto de los
    requests wire y para no depender de ese detalle; las constraints
    (``min_length`` / ``max_length``) y ``extra='forbid'`` se mantienen.
    ``TokenOut`` (response) sigue strict.
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, EmailStr, Field, ValidationInfo, field_validator

from app.schemas.base import YnaraBaseModel

PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 128

# Lista acotada de contraseñas comunes que cumplen longitud + letra + dígito.
_COMMON_PASSWORDS: frozenset[str] = frozenset(
    {
        "123456789a",
        "1234567890a",
        "abc1234567",
        "admin12345",
        "changeme12",
        "contrasena1",
        "contraseña1",
        "iloveyou12",
        "letmein123",
        "password12",
        "password123",
        "password1234",
        "qwerty1234",
        "qwerty12345",
        "welcome123",
        "ynara12345",
    }
)


class RegisterRequest(YnaraBaseModel):
    """Payload de ``POST /v1/auth/register``.

    Acotado a propósito (no reusa ``UserCreate``): el wire no puede setear
    ``is_ephemeral`` / ``retention_sensitive_days`` / ``onboarding_completed``.
    """

    # Override del strict heredado SOLO para el request wire (consistencia con
    # ChatHttpRequest). Los campos son str/EmailStr, pero relajar strict evita
    # depender de ese detalle. Constraints + extra=forbid siguen.
    model_config = ConfigDict(
        strict=False,
        from_attributes=True,
        populate_by_name=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    display_name: str | None = Field(default=None, max_length=40)

    @field_validator("password")
    @classmethod
    def _validate_password_strength(cls, value: str, info: ValidationInfo) -> str:
        """Exige letra + dígito, rechaza contraseñas comunes y el propio email (sin ecoar)."""
        if not any(ch.isalpha() for ch in value) or not any(ch.isdigit() for ch in value):
            raise ValueError("la contraseña debe incluir al menos una letra y un número")
        if value.strip().lower() in _COMMON_PASSWORDS:
            raise ValueError("la contraseña elegida es demasiado común")
        email_val = info.data.get("email")
        if email_val:
            lowered = value.lower()
            email_lower = str(email_val).lower()
            local_part = email_lower.split("@", 1)[0]
            if email_lower in lowered or (len(local_part) >= 4 and local_part in lowered):
                raise ValueError("la contraseña no puede incluir tu dirección de correo")
        return value


class LoginRequest(YnaraBaseModel):
    """Payload de ``POST /v1/auth/token``.

    ``password`` SIN ``min_length`` / ``max_length`` a propósito: un 422 por
    longitud seria un oráculo de formato (revelaria la politica de password al
    atacante). Login solo distingue 200 / 401; la validez del password se decide
    en el service, no en el schema.
    """

    model_config = ConfigDict(
        strict=False,
        from_attributes=True,
        populate_by_name=True,
        extra="forbid",
    )

    email: EmailStr
    password: str


class TokenOut(YnaraBaseModel):
    """Response de ``POST /v1/auth/token`` y ``/auth/refresh``: el access JWT (Bearer).

    ``refresh_token`` (issue #63) es additive y no-breaking: un campo opcional con
    default ``None`` en un RESPONSE no rompe a ningún cliente (el que solo lee
    ``access_token``/``token_type`` lo ignora; ``extra='forbid'`` aplica al INPUT,
    no a la serialización). Lo emiten ``/auth/token`` y ``/auth/refresh``.
    """

    access_token: str
    # S105: "bearer" no es una credencial; es el tipo de token del RFC 6750.
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    refresh_token: str | None = None


class RefreshRequest(YnaraBaseModel):
    """Payload de ``POST /v1/auth/refresh``: el refresh token a rotar.

    Mismo override de strict que los demás requests wire (consistencia). El token
    es un ``str`` opaco; ``verify_token(expected_type="refresh")`` lo valida en el
    handler.
    """

    model_config = ConfigDict(
        strict=False,
        from_attributes=True,
        populate_by_name=True,
        extra="forbid",
    )

    refresh_token: str


class LogoutRequest(YnaraBaseModel):
    """Payload de ``POST /v1/auth/logout``: el refresh token a revocar (opcional).

    El access se revoca siempre (sale del header ``Authorization``). El
    ``refresh_token`` es opcional: si viene y es válido, también se blocklistea;
    si es inválido, se ignora en silencio (logout idempotente y best-effort).
    """

    model_config = ConfigDict(
        strict=False,
        from_attributes=True,
        populate_by_name=True,
        extra="forbid",
    )

    refresh_token: str | None = None
