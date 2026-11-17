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
# Inferencia por API lista para production: el proveedor con su credencial.
_LLM_API: dict[str, object] = {
    "LLM_BACKEND": "api",
    "LLM_PROVIDERS": json.dumps([_PROVIDER]),
    "llm_api_keys": {"principal": "sk-test-principal-0123"},
}
# Embeddings por API listos para production (ADR-028 D4): URL base y credencial; el
# modelo queda en su default.
_EMBEDDING_KEY = "sk-test-embeddings-4321"
_EMBEDDING_API: dict[str, object] = {
    "EMBEDDING_BACKEND": "api",
    "EMBEDDING_BASE_URL": "https://api.embeddings.test/v1",
    "EMBEDDING_API_KEY": _EMBEDDING_KEY,
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
        **_LLM_API,
        **_EMBEDDING_API,
    )
    assert s.environment == "production"


# ---------------------------------------------------------------------------
# S5 — fail-fast de config de dev en production (CORS localhost + master key)
# ---------------------------------------------------------------------------

# Config "limpia" de prod reutilizable: master key seteada + CORS de dominio real +
# inferencia y embeddings por API. Los tests sobreescriben SOLO el campo que quieren
# romper.
_PROD_CLEAN: dict[str, object] = {
    "environment": "production",
    "JWT_SECRET": "x" * 40,
    "MEMORY_ENCRYPTION_MASTER_KEY": "k" * 44,
    "cors_origins": ["https://app.ynara.com"],
    **_LLM_API,
    **_EMBEDDING_API,
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


# ---------------------------------------------------------------------------
# Inferencia por API (ADR-028): LLM_BACKEND y el fail-fast del arranque
# ---------------------------------------------------------------------------


def _api_settings(providers: list[dict[str, object]], **overrides: object) -> Settings:
    return _provider_settings(providers, LLM_BACKEND="api", **overrides)


def test_prod_with_fake_llm_does_not_start() -> None:
    """Production con el LLM fake no arranca (ADR-028 D7)."""
    overrides = {**_PROD_CLEAN, "LLM_BACKEND": "fake"}
    with pytest.raises(ValueError, match="LLM_BACKEND tiene que ser 'api' en production"):
        _settings(**overrides)


def test_prod_without_llm_backend_does_not_start() -> None:
    """Sin LLM_BACKEND el default es fake: production tampoco arranca."""
    overrides = {k: v for k, v in _PROD_CLEAN.items() if k != "LLM_BACKEND"}
    with pytest.raises(ValueError, match="LLM_BACKEND"):
        _settings(**overrides)


@pytest.mark.parametrize("environment", ["production", "development"])
def test_api_without_providers_does_not_start(environment: str) -> None:
    """``LLM_BACKEND=api`` con la lista vacía no arranca, tampoco en dev."""
    overrides = {**_PROD_CLEAN, "environment": environment, "LLM_PROVIDERS": "[]"}
    with pytest.raises(ValueError, match="LLM_PROVIDERS está vacío"):
        _settings(**overrides)


def test_api_with_a_missing_key_names_the_variable_without_leaking_secrets() -> None:
    """Falta la credencial del respaldo: el error nombra la variable y no muestra claves."""
    present_key = "sk-test-principal-secret-9876"
    with pytest.raises(ValueError) as excinfo:
        _api_settings(
            [_PROVIDER, _SECOND_PROVIDER],
            llm_api_keys={"principal": present_key},
            JWT_SECRET="jwt-secret-que-no-se-muestra-0123456789",
        )
    rendered = str(excinfo.value)
    assert "LLM_API_KEY_RESPALDO" in rendered
    assert "LLM_API_KEY_PRINCIPAL" not in rendered
    assert present_key not in rendered
    assert present_key[-6:] not in rendered
    assert "jwt-secret-que-no-se-muestra" not in rendered


def test_api_empty_key_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY_PRINCIPAL", "   ")
    with pytest.raises(ValueError, match="LLM_API_KEY_PRINCIPAL"):
        _api_settings([_PROVIDER])


def test_api_empty_env_key_overrides_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Una LLM_API_KEY_<NAME> vacía en el entorno anula la del .env."""
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_API_KEY_PRINCIPAL=sk-from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY_PRINCIPAL", "")
    with pytest.raises(ValueError, match="LLM_API_KEY_PRINCIPAL"):
        Settings(
            _env_file=env_file,
            DATABASE_URL="postgresql://test:test@localhost/test",
            REDIS_URL="redis://localhost:6379/0",
            JWT_SECRET="test-secret",
            LLM_BACKEND="api",
            LLM_PROVIDERS=json.dumps([_PROVIDER]),
        )  # type: ignore[call-arg]


def test_api_blank_secret_in_llm_api_keys_kwarg_is_rejected() -> None:
    """Un SecretStr vacío o solo espacios en llm_api_keys también cuenta como faltante."""
    with pytest.raises(ValueError, match="LLM_API_KEY_PRINCIPAL"):
        _api_settings([_PROVIDER], llm_api_keys={"principal": "   "})


def test_prod_api_config_from_the_environment_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Como en el arranque real: todo por variables de entorno, sin kwargs de la IA."""
    monkeypatch.setenv("LLM_BACKEND", "api")
    monkeypatch.setenv("LLM_PROVIDERS", json.dumps([_PROVIDER, _SECOND_PROVIDER]))
    monkeypatch.setenv("LLM_API_KEY_PRINCIPAL", "sk-env-principal")
    monkeypatch.setenv("LLM_API_KEY_RESPALDO", "sk-env-respaldo")
    monkeypatch.setenv("EMBEDDING_BACKEND", "api")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://api.embeddings.test/v1")
    monkeypatch.setenv("EMBEDDING_API_KEY", "sk-env-embeddings")
    base = {k: v for k, v in _PROD_CLEAN.items() if k not in {**_LLM_API, **_EMBEDDING_API}}
    s = _settings(**base)
    assert s.llm_backend == "api"
    assert [p.name for p in s.llm_providers] == ["principal", "respaldo"]
    assert set(s.llm_api_keys) == {"principal", "respaldo"}
    assert s.embedding_backend == "api"
    assert s.embedding_api_key.get_secret_value() == "sk-env-embeddings"


# ---------------------------------------------------------------------------
# Embeddings por API (ADR-028 D4): EMBEDDING_* y el fail-fast del arranque
# ---------------------------------------------------------------------------


def test_embeddings_default_to_fake_without_a_provider() -> None:
    s = _settings()
    assert s.embedding_backend == "fake"
    assert s.embedding_base_url == ""
    assert s.embedding_api_key.get_secret_value() == ""
    assert s.embedding_model == "BAAI/bge-m3"
    assert s.embedding_batch_size == 32
    assert s.embedding_timeout_s == 30.0


def test_embeddings_config_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_BACKEND", "api")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://api.embeddings.test/v1/")
    monkeypatch.setenv("EMBEDDING_API_KEY", "  sk-env-embeddings  ")
    monkeypatch.setenv("EMBEDDING_MODEL", " org/bge-m3 ")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "8")
    monkeypatch.setenv("EMBEDDING_TIMEOUT_S", "12.5")
    s = _settings()
    assert s.embedding_backend == "api"
    assert s.embedding_base_url == "https://api.embeddings.test/v1"
    assert s.embedding_api_key.get_secret_value() == "sk-env-embeddings"
    assert s.embedding_model == "org/bge-m3"
    assert s.embedding_batch_size == 8
    assert s.embedding_timeout_s == 12.5


def test_embedding_key_is_masked_in_repr() -> None:
    s = _settings(**_EMBEDDING_API)
    assert _EMBEDDING_KEY not in repr(s)
    assert _EMBEDDING_KEY not in str(s.model_dump())


def test_prod_with_fake_embeddings_does_not_start() -> None:
    """Production con el embedder fake no arranca (ADR-028 D7)."""
    overrides = {**_PROD_CLEAN, "EMBEDDING_BACKEND": "fake"}
    with pytest.raises(ValueError, match="EMBEDDING_BACKEND tiene que ser 'api' en production"):
        _settings(**overrides)


def test_prod_without_embedding_backend_does_not_start() -> None:
    """Sin EMBEDDING_BACKEND el default es fake: production tampoco arranca."""
    overrides = {k: v for k, v in _PROD_CLEAN.items() if k != "EMBEDDING_BACKEND"}
    with pytest.raises(ValueError, match="EMBEDDING_BACKEND tiene que ser 'api'"):
        _settings(**overrides)


@pytest.mark.parametrize("environment", ["production", "development"])
@pytest.mark.parametrize(
    ("blank", "missing"),
    [
        (("EMBEDDING_BASE_URL",), "EMBEDDING_BASE_URL"),
        (("EMBEDDING_API_KEY",), "EMBEDDING_API_KEY"),
        (("EMBEDDING_MODEL",), "EMBEDDING_MODEL"),
        (("EMBEDDING_BASE_URL", "EMBEDDING_API_KEY"), "EMBEDDING_BASE_URL, EMBEDDING_API_KEY"),
    ],
)
def test_embeddings_api_without_its_config_does_not_start(
    environment: str, blank: tuple[str, ...], missing: str
) -> None:
    """``EMBEDDING_BACKEND=api`` sin URL, credencial o modelo no arranca, tampoco en dev."""
    overrides = {**_PROD_CLEAN, "environment": environment, **dict.fromkeys(blank, "  ")}
    with pytest.raises(ValueError) as excinfo:
        _settings(**overrides)
    assert f"falta la configuración del proveedor de embeddings: {missing} [" in str(excinfo.value)


def test_embeddings_missing_config_does_not_leak_secrets() -> None:
    """El error nombra la variable que falta y no muestra la credencial ni otros secretos."""
    overrides = {
        **_EMBEDDING_API,
        "EMBEDDING_BASE_URL": "",
        "JWT_SECRET": "jwt-secret-que-no-se-muestra-0123456789",
    }
    with pytest.raises(ValueError) as excinfo:
        _settings(**overrides)
    rendered = str(excinfo.value)
    assert "falta la configuración del proveedor de embeddings: EMBEDDING_BASE_URL [" in rendered
    assert _EMBEDDING_KEY not in rendered
    assert _EMBEDDING_KEY[-6:] not in rendered
    assert "jwt-secret-que-no-se-muestra" not in rendered


@pytest.mark.parametrize(
    ("base_url", "message"),
    [
        ("http://api.embeddings.test/v1", "tiene que ser https"),
        ("ftp://api.embeddings.test/v1", "tiene que ser https"),
        ("https://api.embeddings.test/v1?modelo=x", "no lleva query"),
    ],
)
def test_embedding_base_url_is_validated_at_startup(base_url: str, message: str) -> None:
    """Se valida aunque el backend sea fake: una URL mal escrita frena el arranque."""
    with pytest.raises(ValueError, match=message):
        _settings(EMBEDDING_BASE_URL=base_url)


def test_embedding_base_url_with_credentials_is_rejected_without_echoing_it() -> None:
    url = "https://usuario:clave-secreta@api.embeddings.test/v1"
    with pytest.raises(ValueError, match="no lleva usuario ni clave") as excinfo:
        _settings(EMBEDDING_BASE_URL=url)
    assert "clave-secreta" not in str(excinfo.value)


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [("", ""), ("   ", ""), ("http://localhost:8003/v1/", "http://localhost:8003/v1")],
)
def test_embedding_base_url_blank_or_on_loopback_boots_with_fake(
    base_url: str, expected: str
) -> None:
    s = _settings(EMBEDDING_BASE_URL=base_url)
    assert s.embedding_backend == "fake"
    assert s.embedding_base_url == expected


def test_embedding_backend_vllm_is_rejected() -> None:
    """El embedder local de vLLM ya no existe (ADR-028): un .env viejo no arranca."""
    with pytest.raises(ValueError, match="EMBEDDING_BACKEND"):
        _settings(EMBEDDING_BACKEND="vllm")


@pytest.mark.parametrize("batch_size", [0, -1, 2049])
def test_embedding_batch_size_is_bounded(batch_size: int) -> None:
    with pytest.raises(ValueError, match="EMBEDDING_BATCH_SIZE"):
        _settings(EMBEDDING_BATCH_SIZE=batch_size)
