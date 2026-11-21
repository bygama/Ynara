"""Tests unitarios del endurecimiento de sesión admin en ``get_current_admin``."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
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
def _clear_settings_cache() -> Iterator[None]:
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


@pytest.mark.parametrize("bad_iat", [None, "1700000000", True, False, float("nan"), float("inf")])
async def test_admin_rejects_missing_or_malformed_iat(bad_iat: Any) -> None:
    uid = uuid.uuid4()
    claims: dict[str, Any] = {"sub": str(uid)}
    if bad_iat is not None:
        claims["iat"] = bad_iat

    with pytest.raises(HTTPException) as exc_info:
        await get_current_admin(claims, uid, _fake_session(uid), _fake_request())
    assert exc_info.value.status_code == 401


async def test_admin_enforces_allowed_ips_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMIN_ALLOWED_IPS", "10.0.0.0/8, 2001:db8::/32")
    get_settings.cache_clear()
    uid = uuid.uuid4()
    claims = {"sub": str(uid), "iat": int(time.time())}

    got_v4 = await get_current_admin(
        claims, uid, _fake_session(uid), _fake_request(peer="10.1.2.3")
    )
    assert got_v4 == uid

    got_v6 = await get_current_admin(
        claims, uid, _fake_session(uid), _fake_request(peer="2001:db8::42")
    )
    assert got_v6 == uid

    for denied_peer in ("203.0.113.5", "2001:db9::1"):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_admin(
                claims, uid, _fake_session(uid), _fake_request(peer=denied_peer)
            )
        assert exc_info.value.status_code == 401


async def test_admin_allowed_ips_respects_trusted_proxy_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "172.18.0.0/16")
    monkeypatch.setenv("REAL_IP_HEADER", "CF-Connecting-IP")
    # Incluye a propósito el CIDR del proxy para verificar fail-closed cuando falta el header.
    monkeypatch.setenv("ADMIN_ALLOWED_IPS", "198.51.100.10/32, 172.18.0.0/16")
    get_settings.cache_clear()
    uid = uuid.uuid4()
    claims = {"sub": str(uid), "iat": int(time.time())}

    # Proxy confiable con header válido y permitido -> pasa.
    got = await get_current_admin(
        claims,
        uid,
        _fake_session(uid),
        _fake_request(peer="172.18.0.2", headers={"CF-Connecting-IP": "198.51.100.10"}),
    )
    assert got == uid

    # Proxy confiable con IP cliente fuera de allowlist -> 401.
    with pytest.raises(HTTPException) as exc_info:
        await get_current_admin(
            claims,
            uid,
            _fake_session(uid),
            _fake_request(peer="172.18.0.2", headers={"CF-Connecting-IP": "203.0.113.99"}),
        )
    assert exc_info.value.status_code == 401

    # Proxy confiable sin header o con lista/basura -> fail-closed (401 aunque el proxy
    # esté en la allowlist).
    bad_header_cases = (
        {},
        {"CF-Connecting-IP": "198.51.100.10, 203.0.113.99"},
        {"CF-Connecting-IP": "basura"},
    )
    for bad_headers in bad_header_cases:
        with pytest.raises(HTTPException) as exc_info:
            await get_current_admin(
                claims,
                uid,
                _fake_session(uid),
                _fake_request(peer="172.18.0.2", headers=bad_headers),
            )
        assert exc_info.value.status_code == 401

    # Peer no confiable que intenta inyectar header con IP permitida -> 401.
    with pytest.raises(HTTPException) as exc_info:
        await get_current_admin(
            claims,
            uid,
            _fake_session(uid),
            _fake_request(peer="203.0.113.7", headers={"CF-Connecting-IP": "198.51.100.10"}),
        )
    assert exc_info.value.status_code == 401
