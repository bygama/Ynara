"""Contratos mínimos sobre ``infra/docker/docker-compose.yml``."""

from __future__ import annotations

from pathlib import Path

_COMPOSE_FILE = Path(__file__).resolve().parents[4] / "infra" / "docker" / "docker-compose.yml"


def test_redis_uses_noeviction_maxmemory_policy() -> None:
    """Redis en producción debe usar ``noeviction`` para no descartar colas ni estado de auth."""
    content = _COMPOSE_FILE.read_text(encoding="utf-8")
    assert "--maxmemory-policy noeviction" in content
    assert "allkeys-lru" not in content
