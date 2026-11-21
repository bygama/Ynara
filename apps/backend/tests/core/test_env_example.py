"""Contrato de sincronización entre ``Settings`` y ``apps/backend/.env.example``."""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings

_ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
_VAR_PATTERN = re.compile(r"^(?:#\s*)?([A-Z][A-Z0-9_]+)=", flags=re.MULTILINE)


def test_env_example_lists_all_settings_aliases() -> None:
    """Cada variable con ``alias`` en ``Settings`` debe estar documentada en ``.env.example``."""
    content = _ENV_EXAMPLE.read_text(encoding="utf-8")
    documented = set(_VAR_PATTERN.findall(content))
    aliases = {field.alias for field in Settings.model_fields.values() if field.alias}

    missing = sorted(aliases - documented)
    assert missing == [], f"Variables faltantes en apps/backend/.env.example: {missing}"
