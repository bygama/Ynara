"""Tests del Settings de core/config.py: fail-fast de production y parseo del env."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings


def _settings(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {
        "_env_file": None,
        "DATABASE_URL": "postgresql://test:test@localhost/test",
        "REDIS_URL": "redis://localhost:6379/0",
        "JWT_SECRET": "x" * 40,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


# Un proveedor de IA válido (ADR-028).
_PROVIDER: dict[str, object] = {
    "name": "principal",
    "base_url": "https://api.proveedor.test/v1",
    "models": {"conversacional": "org/chat", "agente": "org/agent"},
}


def test_dev_allows_weak_secret() -> None:
    # En development el placeholder se permite (sin fricción para dev).
    s = _settings(environment="development", JWT_SECRET="cambiar-en-produccion")
    assert s.jwt_secret == "cambiar-en-produccion"


def test_prod_rejects_placeholder_secret() -> None:
    with pytest.raises(ValueError, match="JWT_SECRET"):
        _settings(environment="production", JWT_SECRET="cambiar-en-produccion")


def test_prod_rejects_short_secret() -> None:
    with pytest.raises(ValueError, match="JWT_SECRET"):
        _settings(environment="production", JWT_SECRET="corto")


def test_prod_accepts_strong_secret() -> None:
    # Para pasar el validador de dev-config en prod hace falta master key + CORS reales.
    s = _settings(
        environment="production",
        JWT_SECRET="x" * 40,
        cors_origins=["https://app.ynara.com"],
        MEMORY_ENCRYPTION_MASTER_KEY="k" * 44,
    )
    assert s.environment == "production"


# ---------------------------------------------------------------------------
# S5 — fail-fast de config de dev en production (CORS localhost + master key)
# ---------------------------------------------------------------------------

# Config "limpia" de prod reutilizable: master key seteada + CORS de dominio real.
# Los tests sobreescriben SOLO el campo que quieren romper.
_PROD_CLEAN: dict[str, object] = {
    "environment": "production",
    "JWT_SECRET": "x" * 40,
    "MEMORY_ENCRYPTION_MASTER_KEY": "k" * 44,
    "cors_origins": ["https://app.ynara.com"],
}


def test_prod_rejects_cors_localhost() -> None:
    """En production un origin localhost en CORS rompe el boot (fail-fast)."""
    overrides = {**_PROD_CLEAN, "cors_origins": ["http://localhost:3000"]}
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        _settings(**overrides)


def test_prod_rejects_cors_127_0_0_1() -> None:
    """En production un origin 127.0.0.1 en CORS también rompe el boot."""
    overrides = {**_PROD_CLEAN, "cors_origins": ["http://127.0.0.1:8081"]}
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        _settings(**overrides)


def test_prod_rejects_cors_ipv6_loopback() -> None:
    """En production un origin IPv6 loopback (``[::1]``) en CORS rompe el boot.

    ``urlsplit('http://[::1]:3000').hostname`` == ``'::1'``, que está en
    ``dev_hosts`` — el origin IPv6 de dev no debe colarse en prod.
    """
    overrides = {**_PROD_CLEAN, "cors_origins": ["http://[::1]:3000"]}
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        _settings(**overrides)


def test_prod_rejects_empty_cors() -> None:
    """En production un CORS vacío (lista vacía) rompe el boot (fail-fast).

    ``any(...)`` sobre lista vacía es False, así que sin el chequeo previo el boot
    pasaría con CERO origins (mala config: API sin política CORS).
    """
    overrides = {**_PROD_CLEAN, "cors_origins": []}
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        _settings(**overrides)


def test_prod_rejects_empty_master_key() -> None:
    """En production una MEMORY_ENCRYPTION_MASTER_KEY vacía rompe el boot."""
    overrides = {**_PROD_CLEAN, "MEMORY_ENCRYPTION_MASTER_KEY": ""}
    with pytest.raises(ValueError, match="MEMORY_ENCRYPTION_MASTER_KEY"):
        _settings(**overrides)


def test_prod_accepts_clean_config() -> None:
    """Con CORS de dominio real + master key seteada, production bootea OK."""
    s = _settings(**_PROD_CLEAN)
    assert s.environment == "production"
    assert s.memory_encryption_master_key == "k" * 44


def test_dev_allows_localhost_cors_and_empty_master_key() -> None:
    """En development el default localhost + master key vacía NO rompen (sin fricción)."""
    # master key EXPLÍCITA "" (no se hereda del ambiente): _env_file=None ignora el
    # archivo .env pero NO las env vars reales del proceso (p.ej. la
    # MEMORY_ENCRYPTION_MASTER_KEY de test que la CI exporta para el cifrado). El
    # kwarg explícito tiene prioridad sobre la env var, así el test queda hermético.
    # cors_origins se omite a propósito: usa el default de dev (localhost).
    s = _settings(environment="development", MEMORY_ENCRYPTION_MASTER_KEY="")
    assert any("localhost" in origin for origin in s.cors_origins)
    assert s.memory_encryption_master_key == ""


# ---------------------------------------------------------------------------
# JWT_ALGORITHM allowlist (fail-fast: solo HMAC, anti algorithm-confusion)
# ---------------------------------------------------------------------------


def test_default_jwt_algorithm_is_hs256() -> None:
    """Sin override, el algoritmo por default es HS256."""
    assert _settings().jwt_algorithm == "HS256"


@pytest.mark.parametrize("alg", ["HS256", "HS384", "HS512"])
def test_jwt_algorithm_accepts_hmac(alg: str) -> None:
    """Los tres algoritmos HMAC permitidos bootean OK."""
    assert _settings(JWT_ALGORITHM=alg).jwt_algorithm == alg


def test_jwt_algorithm_rejects_none() -> None:
    """``JWT_ALGORITHM=none`` rompe el boot: evita el bypass de firma de PyJWT."""
    with pytest.raises(ValueError, match=r"(?i)algorithm"):
        _settings(JWT_ALGORITHM="none")


@pytest.mark.parametrize("alg", ["RS256", "ES256", "HS999", ""])
def test_jwt_algorithm_rejects_non_hmac(alg: str) -> None:
    """Asimétricos (RS*/ES*) y cualquier valor fuera del set rompen el boot
    (anti algorithm-confusion CVE-2022-39227)."""
    with pytest.raises(ValueError, match=r"(?i)algorithm"):
        _settings(JWT_ALGORITHM=alg)


# ---------------------------------------------------------------------------
# CORS_ORIGINS por env var (CSV human-friendly, mismo patrón que TRUSTED_PROXY_IPS)
# ---------------------------------------------------------------------------


def test_cors_origins_parsed_from_env_csv() -> None:
    """``CORS_ORIGINS`` CSV desde env se splittea a lista (no crashea por JSON-decode)."""
    s = _settings(CORS_ORIGINS="https://a.com,https://b.com")
    assert s.cors_origins == ["https://a.com", "https://b.com"]


def test_cors_origins_csv_strips_whitespace() -> None:
    """Espacios alrededor de cada origin del CSV se recortan."""
    s = _settings(CORS_ORIGINS=" https://a.com , https://b.com ")
    assert s.cors_origins == ["https://a.com", "https://b.com"]


def test_cors_origins_empty_env_falls_back_to_empty_list() -> None:
    """``CORS_ORIGINS=`` (vacío) no crashea el boot; queda lista vacía (mismo
    contrato que ``TRUSTED_PROXY_IPS`` vacío)."""
    s = _settings(CORS_ORIGINS="")
    assert s.cors_origins == []


def test_cors_origins_json_list_passes_intact() -> None:
    """Una lista ya parseada por kwarg pasa intacta (no rompe los tests que usan
    ``cors_origins=[...]``)."""
    s = _settings(cors_origins=["https://app.ynara.com"])
    assert s.cors_origins == ["https://app.ynara.com"]


def test_cors_origins_default_dev_localhost_when_unset() -> None:
    """Sin ``CORS_ORIGINS``, el default dev (localhost:3000/8081) queda intacto."""
    s = _settings(environment="development")
    assert s.cors_origins == ["http://localhost:3000", "http://localhost:8081"]


def test_prod_rejects_cors_localhost_from_env() -> None:
    """El fail-fast de prod rechaza localhost cuando ``CORS_ORIGINS`` llega por env
    (string CSV), no solo por kwarg list."""
    overrides = {**_PROD_CLEAN}
    overrides.pop("cors_origins")
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        _settings(CORS_ORIGINS="http://localhost:3000", **overrides)


def test_prod_rejects_empty_cors_from_env() -> None:
    """El fail-fast de prod rechaza ``CORS_ORIGINS=`` (vacío) por env: el split lo
    deja en ``[]`` y el boot no debe pasar con cero origins."""
    overrides = {**_PROD_CLEAN}
    overrides.pop("cors_origins")
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        _settings(CORS_ORIGINS="", **overrides)


def test_prod_accepts_cors_real_domains_from_env() -> None:
    """Con dominios reales por env (CSV), production bootea OK."""
    overrides = {**_PROD_CLEAN}
    overrides.pop("cors_origins")
    s = _settings(CORS_ORIGINS="https://app.ynara.com,https://api.ynara.com", **overrides)
    assert s.cors_origins == ["https://app.ynara.com", "https://api.ynara.com"]


# ---------------------------------------------------------------------------
# TRUSTED_PROXY_IPS por env var (CSV human-friendly, mismo patrón que CORS_ORIGINS)
# ---------------------------------------------------------------------------


def test_trusted_proxy_ips_parsed_from_env_csv() -> None:
    """``TRUSTED_PROXY_IPS`` CSV desde env se splittea a lista intacta (no JSON-decode)."""
    s = _settings(TRUSTED_PROXY_IPS="127.0.0.1,10.0.0.0/8")
    assert s.trusted_proxy_ips == ["127.0.0.1", "10.0.0.0/8"]


def test_trusted_proxy_ips_csv_strips_whitespace() -> None:
    """Espacios alrededor de cada IP/CIDR del CSV se recortan."""
    s = _settings(TRUSTED_PROXY_IPS=" 127.0.0.1 , 10.0.0.0/8 ")
    assert s.trusted_proxy_ips == ["127.0.0.1", "10.0.0.0/8"]


def test_trusted_proxy_ips_single_value() -> None:
    """Un único IP/CIDR sin comas también se parsea a una lista de un elemento."""
    s = _settings(TRUSTED_PROXY_IPS="192.168.0.0/16")
    assert s.trusted_proxy_ips == ["192.168.0.0/16"]


def test_trusted_proxy_ips_empty_env_falls_back_to_empty_list() -> None:
    """``TRUSTED_PROXY_IPS=`` (vacío) no crashea el boot; queda lista vacía (default
    seguro: no confiar ningún proxy)."""
    s = _settings(TRUSTED_PROXY_IPS="")
    assert s.trusted_proxy_ips == []


def test_trusted_proxy_ips_default_empty_when_unset() -> None:
    """Sin ``TRUSTED_PROXY_IPS``, el default es lista vacía (no se confía ningún proxy)."""
    s = _settings()
    assert s.trusted_proxy_ips == []


def test_trusted_proxy_ips_list_passes_intact() -> None:
    """Una lista ya parseada por kwarg pasa intacta (no rompe los kwargs list)."""
    s = _settings(trusted_proxy_ips=["10.0.0.0/8"])
    assert s.trusted_proxy_ips == ["10.0.0.0/8"]


def test_trusted_proxy_ips_invalid_entry_rejected() -> None:
    """Una entry no-IP/CIDR rompe el boot (fail-fast del validator anti-spoof, #151)."""
    with pytest.raises(ValueError, match="TRUSTED_PROXY_IPS"):
        _settings(TRUSTED_PROXY_IPS="no-soy-una-ip")


# ---------------------------------------------------------------------------
# Fallback de las URLs de Celery hacia redis_url (P2.7)
# ---------------------------------------------------------------------------


def test_celery_urls_default_to_redis_when_unset() -> None:
    """Sin CELERY_*_URL explícitas, broker y result-backend caen a redis_url."""
    s = _settings(REDIS_URL="redis://localhost:6379/3")
    assert s.celery_broker_url == "redis://localhost:6379/3"
    assert s.celery_result_backend == "redis://localhost:6379/3"


def test_celery_urls_respected_when_set() -> None:
    """Con CELERY_*_URL explícitas, se respetan y NO se pisan con redis_url."""
    s = _settings(
        REDIS_URL="redis://localhost:6379/0",
        CELERY_BROKER_URL="redis://broker:6379/1",
        CELERY_RESULT_BACKEND="redis://backend:6379/2",
    )
    assert s.celery_broker_url == "redis://broker:6379/1"
    assert s.celery_result_backend == "redis://backend:6379/2"


# ---------------------------------------------------------------------------
# Proveedores de IA (ADR-028): LLM_PROVIDERS y LLM_API_KEY_<NAME>
# ---------------------------------------------------------------------------

_SECOND_PROVIDER: dict[str, object] = {
    "name": "respaldo",
    "base_url": "https://api.respaldo.test/v1",
    "models": {"conversacional": "otro/chat", "agente": "otro/agent"},
    "supports_reasoning_effort": True,
}


def _provider_settings(providers: list[dict[str, object]], **overrides: object) -> Settings:
    return _settings(LLM_PROVIDERS=json.dumps(providers), **overrides)


def test_llm_defaults_to_fake_without_providers() -> None:
    s = _settings()
    assert s.llm_backend == "fake"
    assert s.llm_providers == []
    assert s.llm_api_keys == {}


def test_provider_keys_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY_PRINCIPAL", "sk-env-principal")
    monkeypatch.setenv("LLM_API_KEY_RESPALDO", "  sk-env-respaldo  ")
    s = _provider_settings([_PROVIDER, _SECOND_PROVIDER])
    keys = {name: key.get_secret_value() for name, key in s.llm_api_keys.items()}
    assert keys == {"principal": "sk-env-principal", "respaldo": "sk-env-respaldo"}


def test_provider_keys_from_the_env_file_yield_to_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Las credenciales del archivo de entorno también cuentan; el entorno lo pisa."""
    env_file = tmp_path / "proveedores.env"
    env_file.write_text(
        "LLM_API_KEY_PRINCIPAL=sk-file-principal\nLLM_API_KEY_RESPALDO=sk-file-respaldo\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_API_KEY_PRINCIPAL", "sk-env-principal")
    s = _provider_settings([_PROVIDER, _SECOND_PROVIDER], _env_file=env_file)
    keys = {name: key.get_secret_value() for name, key in s.llm_api_keys.items()}
    assert keys == {"principal": "sk-env-principal", "respaldo": "sk-file-respaldo"}


def test_provider_empty_key_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY_PRINCIPAL", "   ")
    assert _provider_settings([_PROVIDER]).llm_api_keys == {}


def test_provider_keys_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY_PRINCIPAL", "sk-env-principal-que-no-se-ve")
    s = _provider_settings([_PROVIDER])
    assert "sk-env-principal-que-no-se-ve" not in repr(s)
    assert "sk-env-principal-que-no-se-ve" not in str(s.model_dump())


def test_providers_keep_priority_order_and_reasoning_flag() -> None:
    s = _provider_settings([_PROVIDER, _SECOND_PROVIDER])
    assert [p.name for p in s.llm_providers] == ["principal", "respaldo"]
    assert [p.supports_reasoning_effort for p in s.llm_providers] == [False, True]
    assert s.llm_providers[1].api_key_env == "LLM_API_KEY_RESPALDO"


def test_providers_empty_env_is_an_empty_list() -> None:
    assert _settings(LLM_PROVIDERS="").llm_providers == []


def test_providers_invalid_json_is_rejected_without_echoing_it() -> None:
    with pytest.raises(ValueError, match="LLM_PROVIDERS no es JSON válido") as excinfo:
        _settings(LLM_PROVIDERS='[{"name": "principal", "base_url": "sk-roto')
    assert "sk-roto" not in str(excinfo.value)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"models": {"agente": "org/agent"}}, "no cubre el alias: conversacional"),
        ({"models": {"conversacional": "org/chat"}}, "no cubre el alias: agente"),
        ({"models": {"conversacional": "", "agente": "org/agent"}}, "id de modelo vacío"),
        ({"name": "Principal"}, "minúsculas"),
        ({"name": "principal-1"}, "minúsculas"),
        ({"base_url": "http://api.proveedor.test/v1"}, "tiene que ser https"),
        ({"api_key": "sk-no-va-en-el-json"}, "Extra inputs"),
    ],
)
def test_provider_entry_is_validated_at_startup(change: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _provider_settings([{**_PROVIDER, **change}])


def test_provider_extra_alias_is_accepted() -> None:
    provider = {**_PROVIDER, "models": {"conversacional": "a", "agente": "b", "otro": "c"}}
    s = _provider_settings([provider])
    assert s.llm_providers[0].models["otro"] == "c"


def test_provider_base_url_with_credentials_is_rejected_without_echoing_it() -> None:
    provider = {**_PROVIDER, "base_url": "https://usuario:clave-secreta@api.proveedor.test/v1"}
    with pytest.raises(ValueError, match="no lleva usuario ni clave") as excinfo:
        _provider_settings([provider])
    assert "clave-secreta" not in str(excinfo.value)


def test_providers_reject_a_repeated_name() -> None:
    with pytest.raises(ValueError, match="repite el proveedor: principal"):
        _provider_settings([_PROVIDER, _PROVIDER])
