"""Tests unitarios del endurecimiento de sesión admin en ``get_current_admin``."""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.config import get_settings
from app.core.deps import get_current_admin


def _fake_request(peer: str = "127.0.0.1", headers: dict[str, str] | None = None) -> MagicMock:
    req = MagicMock()
    req.client = SimpleNamespace(host=peer)
    req.headers = headers or {}
    return req


def _fake_session(user_id: uuid.UUID, *, is_admin: bool = True) -> MagicMock:
    session = MagicMock()
    session.get = AsyncMock(return_value=SimpleNamespace(id=user_id, is_admin=is_admin))
    return session


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_admin_accepts_fresh_token() -> None:
    uid = uuid.uuid4()
    claims = {"sub": str(uid), "iat": int(time.time()) - 60}
    got = await get_current_admin(claims, uid, _fake_session(uid), _fake_request())
    assert got == uid


async def test_admin_rejects_token_older_than_max_session_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMIN_SESSION_MAX_MINUTES", "60")
    get_settings.cache_clear()
    uid = uuid.uuid4()
    stale_claims = {"sub": str(uid), "iat": int(time.time()) - 3700}

    with pytest.raises(HTTPException) as exc_info:
        await get_current_admin(stale_claims, uid, _fake_session(uid), _fake_request())
    assert exc_info.value.status_code == 401


async def test_admin_enforces_allowed_ips_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMIN_ALLOWED_IPS", "10.0.0.0/8")
    get_settings.cache_clear()
    uid = uuid.uuid4()
    claims = {"sub": str(uid), "iat": int(time.time())}

    got = await get_current_admin(claims, uid, _fake_session(uid), _fake_request(peer="10.1.2.3"))
    assert got == uid

    with pytest.raises(HTTPException) as exc_info:
        await get_current_admin(claims, uid, _fake_session(uid), _fake_request(peer="203.0.113.5"))
    assert exc_info.value.status_code == 401
