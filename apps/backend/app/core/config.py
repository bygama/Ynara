"""Settings de la aplicación cargados desde variables de entorno.

Pydantic Settings v2 con .env como fuente. Cualquier variable nueva
se agrega acá + en ``apps/backend/.env.example``.
"""

from __future__ import annotations

import ipaddress
import json
import re
import uuid
from collections.abc import Mapping
from functools import lru_cache
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from app.core.constants import MODEL_ALIASES
from app.core.urls import validated_base_url


class ServingEndpoint(BaseModel):
    """Un endpoint de serving (ADR-013): su ``base_url`` y los ``models``
    (served_names) que anuncia. En 16GB el motor es Ollama/GGUF; en 24GB+ es
    vLLM (ADR-014).

    Cada entrada de ``LLM_SERVING`` = un endpoint/proceso. ``served_name``,
    parsers y quantization NO van acá: siguen en ``ynara.config.json`` (ADR-009 D4).

    ``engine`` marca el MOTOR del endpoint y gobierna CÓMO se controla el thinking
    (ADR-014 D4):

    - ``"vllm"`` (default, 24GB+): el cliente habla el endpoint OpenAI-compat
      (``/v1/chat/completions`` + ``reasoning_effort`` / ``chat_template_kwargs``).
      Default ``"vllm"`` para NO cambiar el path existente (byte-equivalente).
    - ``"ollama"`` (16GB): el OpenAI-compat de Ollama IGNORA ``chat_template_kwargs``
      y, para gemma4, también ``reasoning_effort`` (upstream Ollama #15288/#15293/
      #15635) -> el cliente rutea el thinking por la API NATIVA ``/api/chat`` con el
      top-level ``"think"``, el único mecanismo que apaga el thinking de gemma4.

    Si ``engine`` no se setea explícito en la entrada de ``LLM_SERVING``, se DERIVA
    del ``base_url`` (puerto 11434 o un host con 'ollama' -> ``"ollama"``), así el dev
    contra Ollama lo agarra sin tener que tocar el flag. Un ``engine`` explícito
    SIEMPRE manda.
    """

    base_url: str
    models: list[str]
    engine: Literal["ollama", "vllm"] = "vllm"

    @model_validator(mode="before")
    @classmethod
    def _derive_engine_from_base_url(cls, data: object) -> object:
        """Deriva ``engine`` del ``base_url`` cuando la entrada no lo setea explícito.

        El endpoint OpenAI-compat de Ollama ignora ``chat_template_kwargs`` y, para
        gemma4, también ``reasoning_effort`` (Ollama #15288/#15293/#15635): el thinking
        solo se apaga por la API nativa ``/api/chat`` con ``think:false``. Para que un
        endpoint de Ollama (puerto 11434, o un host con 'ollama') tome ese camino sin que
        el operador tenga que agregar el flag, si la entrada no trae ``engine`` se infiere
        del ``base_url`` -> ``"ollama"``; el resto queda en el default ``"vllm"`` (path
        OpenAI-compat intacto). Un ``engine`` explícito SIEMPRE gana (no se pisa).
        """
        if isinstance(data, dict) and "engine" not in data and "base_url" in data:
            try:
                parts = urlsplit(str(data["base_url"]))
                host = (parts.hostname or "").lower()
                is_ollama = parts.port == 11434 or "ollama" in host
            except ValueError:
                return data
            if is_ollama:
                return {**data, "engine": "ollama"}
        return data


# Prefijo de la credencial de cada proveedor de IA: ``LLM_API_KEY_<NAME>`` (ADR-028 D2).
LLM_API_KEY_PREFIX = "LLM_API_KEY_"

# ``name`` de un proveedor: nombra su variable de credencial, así que va en minúsculas,
# empieza con una letra y sigue con letras, dígitos o ``_``.
_PROVIDER_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")


class LlmProvider(BaseModel):
    """Un proveedor de IA de ``LLM_PROVIDERS`` (ADR-028 D2).

    - ``name``: identifica al proveedor en los logs y nombra su credencial,
      ``LLM_API_KEY_<NAME>`` (el ``name`` en mayúsculas).
    - ``base_url``: URL base de su API compatible con OpenAI, con el ``/v1``
      (``https``; ver ``validated_base_url``).
    - ``models``: ``{alias: id del modelo en el proveedor}``. Tiene que cubrir los dos
      alias de Ynara (``conversacional`` y ``agente``).
    - ``supports_reasoning_effort``: si el proveedor acepta ``reasoning_effort``.

    La credencial no va acá ni en el JSON: se lee aparte, como secreto
    (``Settings.llm_api_keys``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    name: str
    base_url: str
    models: dict[str, str]
    supports_reasoning_effort: bool = False

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _PROVIDER_NAME.fullmatch(value):
            raise ValueError(
                "el name del proveedor va en minúsculas, con letras, dígitos o '_' "
                "(nombra su variable LLM_API_KEY_<NAME>)"
            )
        return value

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        return validated_base_url(value)

    @field_validator("models")
    @classmethod
    def _require_both_aliases(cls, value: dict[str, str]) -> dict[str, str]:
        if not all(alias.strip() and model_id.strip() for alias, model_id in value.items()):
            raise ValueError("models del proveedor tiene un alias o un id de modelo vacío")
        missing = [alias for alias in MODEL_ALIASES if alias not in value]
        if missing:
            raise ValueError(f"models del proveedor no cubre el alias: {', '.join(missing)}")
        return value

    @property
    def api_key_env(self) -> str:
        """Variable de entorno con la credencial del proveedor."""
        return f"{LLM_API_KEY_PREFIX}{self.name.upper()}"


class _LlmApiKeysSource(PydanticBaseSettingsSource):
    """Junta las credenciales ``LLM_API_KEY_<NAME>`` en ``Settings.llm_api_keys``.

    El nombre de cada variable depende de ``LLM_PROVIDERS``, así que ningún campo fijo
    la declara: esta fuente las busca por prefijo entre las variables que ya leyeron las
    fuentes del ``.env`` y del entorno, en ese orden (el entorno pisa al ``.env``, como
    en el resto de los campos). Cada valor se envuelve en ``SecretStr`` acá mismo, y uno
    vacío cuenta como faltante.
    """

    def __init__(
        self, settings_cls: type[BaseSettings], *sources: PydanticBaseSettingsSource
    ) -> None:
        super().__init__(settings_cls)
        self._sources = sources

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        # Sin uso: ``__call__`` arma el valor del campo entero.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        prefix = LLM_API_KEY_PREFIX.lower()
        keys: dict[str, SecretStr] = {}
        for source in self._sources:
            env_vars: Mapping[str, str | None] = getattr(source, "env_vars", {})
            for env_name, value in env_vars.items():
                name = env_name.lower()
                if name.startswith(prefix) and value and value.strip():
                    keys[name.removeprefix(prefix)] = SecretStr(value.strip())
        return {"llm_api_keys": keys} if keys else {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # alias y nombre de campo conviven (misma convención que app/schemas/base.py):
        # un campo con alias env (p.ej. cors_origins/CORS_ORIGINS) se puede setear por
        # cualquiera de los dos. Los tests setean varios campos por nombre de campo.
        populate_by_name=True,
        # Un error de validación no repite los valores recibidos: entre ellos hay
        # secretos (JWT_SECRET, credenciales de los proveedores de IA).
        hide_input_in_errors=True,
    )

    environment: Literal["development", "staging", "production"] = "development"

    # Database (Supabase en MVP, self-hosted en V2 — solo cambia el valor)
    database_url: str = Field(..., alias="DATABASE_URL")
    database_pool_size: int = Field(10, alias="DATABASE_POOL_SIZE")

    # Redis / Celery
    redis_url: str = Field(..., alias="REDIS_URL")
    celery_broker_url: str = Field("", alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field("", alias="CELERY_RESULT_BACKEND")

    # LLM serving (ADR-013): lista explícita de endpoints de serving (Ollama en
    # 16GB / vLLM en 24GB+, ADR-014). Cada entrada describe un endpoint — su
    # base_url y los served_names que sirve — y vive en .env (JSON). served_name,
    # parsers y quantization siguen en ynara.config.json (ADR-009 D4).
    # pydantic-settings parsea el JSON del env var para tipos complejos
    # automáticamente (sin NoDecode).
    llm_serving: list[ServingEndpoint] = Field(
        default_factory=lambda: [
            # Default (motor local 16GB, ADR-014): UN endpoint Ollama que sirve
            # AMBOS modelos (gemma4 + qwen) en la misma GPU. Espeja el default de
            # ``.env.example``. ``engine="ollama"`` rutea el thinking por la API
            # nativa ``/api/chat`` (ADR-014 D4). El vLLM dual (2 endpoints, un modelo
            # c/u) es para 24GB+ y se setea explicito via ``LLM_SERVING`` en el .env.
            ServingEndpoint(
                base_url="http://localhost:11434/v1",
                models=["gemma4", "qwen"],
                engine="ollama",
            ),
        ],
        alias="LLM_SERVING",
    )
    # Inferencia por API (ADR-028). ``llm_backend`` elige el cliente LLM: ``fake`` (el
    # default, determinista y sin red: dev y CI) o ``api`` (los proveedores de
    # ``LLM_PROVIDERS``). En production tiene que ser ``api`` (``_require_llm_api``).
    llm_backend: Literal["fake", "api"] = Field("fake", alias="LLM_BACKEND")
    # Proveedores en orden de prioridad: el primero es el principal y el resto, respaldo
    # (ADR-028 D2). Lista JSON en el env; vacía (``LLM_PROVIDERS=``) es sin proveedores.
    # ``NoDecode``: el string crudo llega a ``_parse_llm_providers``, que lo decodifica.
    llm_providers: Annotated[list[LlmProvider], NoDecode] = Field(
        default_factory=list, alias="LLM_PROVIDERS"
    )
    # Credencial de cada proveedor, por ``name``, de las variables ``LLM_API_KEY_<NAME>``
    # (las junta ``_LlmApiKeysSource``). Secretas: nunca van en ``LLM_PROVIDERS``, en
    # ``ynara.config.json`` ni en logs (regla #4b).
    llm_api_keys: dict[str, SecretStr] = Field(default_factory=dict)

    # Embeddings (ADR-008: bge-m3 1024-dim on-prem). `embedding_backend` elige
    # entre el Fake determinista (default, sin GPU) y el cliente vLLM real
    # (cuando el servidor de embeddings esté levantado, ADR-009).
    embedding_model: str = Field("bge-m3", alias="EMBEDDING_MODEL")
    embedding_base_url: str = Field("http://localhost:8003/v1", alias="EMBEDDING_BASE_URL")
    embedding_backend: Literal["fake", "vllm"] = Field("fake", alias="EMBEDDING_BACKEND")
    # Timeout por request del embedder (segundos), solo aplica con backend 'vllm'.
    # El chat LLM toma el suyo de ynara.config.json[llm.serving].request_timeout_s.
    embedding_timeout_s: float = Field(30.0, gt=0, alias="EMBEDDING_TIMEOUT_S")

    # Reranker (cross-encoder). `reranker_backend` elige el Fake passthrough
    # (default) vs el `VllmReranker` real contra la API `/rerank` de vLLM. Ollama
    # NO sirve cross-encoders: en dev se queda en 'fake'. El modelo companion de
    # bge-m3 es bge-reranker-v2-m3 (ADR-008 lo deja como palanca de re-ranking).
    reranker_backend: Literal["fake", "vllm"] = Field("fake", alias="RERANKER_BACKEND")
    reranker_base_url: str = Field("http://localhost:8004/v1", alias="RERANKER_BASE_URL")
    reranker_model: str = Field("bge-reranker-v2-m3", alias="RERANKER_MODEL")
    reranker_timeout_s: float = Field(30.0, gt=0, alias="RERANKER_TIMEOUT_S")

    # Conexión / Compartir (control plane del admin, /v1/admin/connectivity): puertos
    # de los servicios que se exponen a otra máquina por el tailnet de Tailscale. El
    # panel arma las URLs para compartir con el IP del tailnet + estos puertos. Defaults:
    # Ollama (OpenAI-compatible) 11434, Open WebUI 3001, panel admin 3002, app web 3000.
    # Se setean por env si corrés esos servicios en otro puerto.
    ollama_api_port: int = Field(11434, gt=0, le=65535, alias="OLLAMA_API_PORT")
    openwebui_port: int = Field(3001, gt=0, le=65535, alias="OPENWEBUI_PORT")
    admin_port: int = Field(3002, gt=0, le=65535, alias="ADMIN_PORT")
    web_port: int = Field(3000, gt=0, le=65535, alias="WEB_PORT")

    # Cifrado de memoria a nivel campo (ADR-007 D3). Base64 de 32 bytes random
    # (`openssl rand -base64 32`). Vacío => el helper de crypto falla al primer
    # uso (no se importa la key al boot). NUNCA commitear (regla #2).
    memory_encryption_master_key: str = Field("", alias="MEMORY_ENCRYPTION_MASTER_KEY")

    # Auth
    jwt_secret: str = Field(..., alias="JWT_SECRET")
    # Allowlist de algoritmos HMAC (fail-fast en boot, no `str` libre): Ynara firma
    # con secret simétrico, así que solo HS* es válido. Un `str` libre permitía
    # `JWT_ALGORITHM=none` (bypass total de firma en PyJWT) o un asimétrico (RS*/ES*)
    # con secret corto, que dispara el patrón de algorithm-confusion CVE-2022-39227.
    # El `Literal` hace que un valor fuera del set rompa el arranque (mismo estilo
    # que `llm_backend`), cerrando el vector sin depender de validación en `verify_token`.
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = Field("HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(10080, alias="JWT_EXPIRE_MINUTES")
    # TTL del refresh token. Mayor que el access (30 dias por default). Se usa
    # el MISMO secret/alg que el access; el claim `type` separa los dominios.
    jwt_refresh_expire_minutes: int = Field(43200, alias="JWT_REFRESH_EXPIRE_MINUTES")
    # Reuse-detection de refresh a nivel familia (sid), retry-safe (item 1 de #142).
    # Ventana de gracia tras rotar un refresh: dentro de ella, un reenvio del MISMO
    # refresh (retry de red benigno, comun en mobile que perdio la respuesta) NO
    # revoca la familia — se trata como reintento idempotente. Fuera de la ventana,
    # un refresh ya rotado que resurge es replay/robo -> revoca la familia entera.
    auth_refresh_reuse_grace_seconds: int = Field(30, alias="AUTH_REFRESH_REUSE_GRACE_SECONDS")

    # Rate-limit / lockout del login (issue #63). Bucket por (ip, email_hash).
    # El estado vive solo en Redis (sin tablas). fail-OPEN: si Redis cae, el
    # login procede sin freno (baseline pre-#63), nunca se auto-DoSea.
    auth_login_max_attempts: int = Field(5, alias="AUTH_LOGIN_MAX_ATTEMPTS")
    auth_login_window_seconds: int = Field(900, alias="AUTH_LOGIN_WINDOW_SECONDS")
    auth_login_lockout_seconds: int = Field(900, alias="AUTH_LOGIN_LOCKOUT_SECONDS")
    # Rate-limit del register, por IP (el email aun no existe). Mas laxo.
    auth_register_max_attempts: int = Field(10, alias="AUTH_REGISTER_MAX_ATTEMPTS")
    auth_register_window_seconds: int = Field(3600, alias="AUTH_REGISTER_WINDOW_SECONDS")
    # Rate-limit de /auth/refresh, por (ip, sub). El refresh es rotacion legitima
    # frecuente (mobile que reconecta), asi que es MAS permisivo que el login: la
    # rama benign-retry del grace ya absorbe los reenvios sanos, este freno es solo
    # un techo contra abuso. fail-OPEN si Redis cae.
    auth_refresh_max_attempts: int = Field(30, alias="AUTH_REFRESH_MAX_ATTEMPTS")
    auth_refresh_window_seconds: int = Field(900, alias="AUTH_REFRESH_WINDOW_SECONDS")
    # Rate-limit de /chat y /chat/stream, por user_id. Decenas por minuto: cubre
    # un uso intensivo legitimo y frena scripting abusivo. fail-OPEN si Redis cae.
    chat_max_requests: int = Field(60, alias="CHAT_MAX_REQUESTS")
    chat_window_seconds: int = Field(60, alias="CHAT_WINDOW_SECONDS")
    # Rate-limit de GET /v1/memory/export, por user_id. El endpoint mas caro
    # (descifra 3 capas sin paginar): pocas por hora alcanzan para un export
    # legitimo y cortan el abuso. fail-OPEN si Redis cae.
    memory_export_max_requests: int = Field(5, alias="MEMORY_EXPORT_MAX_REQUESTS")
    memory_export_window_seconds: int = Field(3600, alias="MEMORY_EXPORT_WINDOW_SECONDS")
    # Rate-limit de POST /v1/memory/wipe (execute), por user_id. Operacion DESTRUCTIVA e
    # irreversible: un techo bajo por hora corta el abuso/scripting sin molestar un wipe
    # legitimo (raro). El preview (?dry_run=true) NO se frena. fail-OPEN si Redis cae.
    memory_wipe_max_requests: int = Field(5, alias="MEMORY_WIPE_MAX_REQUESTS")
    memory_wipe_window_seconds: int = Field(3600, alias="MEMORY_WIPE_WINDOW_SECONDS")
    # Rate-limit de GET /v1/memory/search, por user_id. El search dispara el pipeline
    # caro embed -> ANN -> rerank (GPU/CPU) en CADA query; sin freno un usuario
    # autenticado podía saturarlo. Techo amplio (decenas por minuto) para no molestar
    # la búsqueda interactiva legítima y cortar solo el scripting abusivo. fail-OPEN si
    # Redis cae.
    memory_search_max_requests: int = Field(30, alias="MEMORY_SEARCH_MAX_REQUESTS")
    memory_search_window_seconds: int = Field(60, alias="MEMORY_SEARCH_WINDOW_SECONDS")
    # Rate-limit de /v1/sessions (issue #208), por user_id. UN bucket compartido por
    # list/get/close (las 3 son lecturas/ops baratas): default amplio para cubrir un
    # polling legitimo y solo cortar scripting abusivo. fail-OPEN si Redis cae.
    sessions_max_requests: int = Field(120, alias="SESSIONS_MAX_REQUESTS")
    sessions_window_seconds: int = Field(60, alias="SESSIONS_WINDOW_SECONDS")
    # Rate-limit de /v1/events (dominio Agenda, ADR-023), por user_id. UN bucket
    # compartido por las 4 rutas (list/create/patch/delete): default amplio para
    # cubrir el uso interactivo de la agenda y solo cortar scripting abusivo.
    # fail-OPEN si Redis cae.
    events_max_requests: int = Field(120, alias="EVENTS_MAX_REQUESTS")
    events_window_seconds: int = Field(60, alias="EVENTS_WINDOW_SECONDS")
    # Rate-limit de /v1/tasks (dominio TAREAS, Fase D1), por user_id. UN bucket
    # compartido por las 2 rutas (list/patch): default amplio para cubrir el uso
    # interactivo del dashboard "Hoy" y solo cortar scripting abusivo. Mismo criterio
    # que el bucket de events. fail-open si Redis cae.
    tasks_max_requests: int = Field(120, alias="TASKS_MAX_REQUESTS")
    tasks_window_seconds: int = Field(60, alias="TASKS_WINDOW_SECONDS")
    # Rate-limit de /v1/reminders (dominio Recordatorios), por user_id. UN bucket
    # compartido por las 4 rutas (list/create/patch/delete): default amplio para cubrir el
    # uso interactivo y solo cortar scripting abusivo. Mismo criterio que events/tasks.
    # fail-open si Redis cae.
    reminders_max_requests: int = Field(120, alias="REMINDERS_MAX_REQUESTS")
    reminders_window_seconds: int = Field(60, alias="REMINDERS_WINDOW_SECONDS")

    # Reverse-proxy / IP real del cliente (issue #151). El rate-limit anti-fuerza-bruta
    # cuenta por IP; detrás de Cloudflare Tunnel el peer que ve uvicorn es el conector
    # cloudflared (todos los clientes colapsan a una IP) -> el freno por IP se neutraliza.
    # FIX SEGURO: leer la IP real del header SOLO cuando el peer inmediato está en esta
    # allowlist de proxies confiables. Default VACÍO = no confiar ningún proxy = se usa el
    # peer host (comportamiento actual, cero riesgo). NUNCA confiar el header si el peer
    # NO está en la allowlist: un atacante spoofearía el header y se daría una IP nueva por
    # intento, evadiendo el rate-limit por completo (PEOR que el status quo).
    # ``NoDecode`` desactiva el JSON-decode que ``EnvSettingsSource`` aplicaría a un
    # ``list[str]`` (que crashea ante ``TRUSTED_PROXY_IPS=`` o ``=127.0.0.1,10.0.0.0/8``):
    # el string crudo del env llega tal cual al ``field_validator`` de abajo, que lo
    # splittea por comas. Así el formato del .env es human-friendly (una IP/CIDR o CSV).
    trusted_proxy_ips: Annotated[list[str], NoDecode] = Field(
        default_factory=list, alias="TRUSTED_PROXY_IPS"
    )
    real_ip_header: str = Field("CF-Connecting-IP", alias="REAL_IP_HEADER")

    # Observabilidad
    sentry_dsn: str = Field("", alias="SENTRY_DSN")
    sentry_traces_sample_rate: float = Field(0.0, ge=0.0, le=1.0, alias="SENTRY_TRACES_SAMPLE_RATE")

    # CORS — origins permitidos por el navegador. Se setea por entorno via
    # ``CORS_ORIGINS`` (CSV human-friendly, p.ej.
    # ``https://app.ynara.com,https://api.ynara.com``); el default es dev =
    # localhost. En production ``_reject_dev_config_in_prod`` falla el boot si
    # quedan origins de dev (localhost/127.0.0.1/::1), así que los dominios
    # reales del front son la fuente de verdad por entorno, no este default.
    # ``NoDecode`` desactiva el JSON-decode que ``EnvSettingsSource`` aplicaría a un
    # ``list[str]`` (que crashea ante ``CORS_ORIGINS=`` o ``=https://a.com,https://b.com``):
    # el string crudo del env llega tal cual al ``field_validator`` de abajo, que lo
    # splittea por comas (mismo patrón que ``trusted_proxy_ips``).
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:8081",
        ],
        alias="CORS_ORIGINS",
    )

    # Bootstrap de admins del panel interno (/v1/admin/*). CSV human-friendly de
    # UUIDs de usuarios que arrancan como admin ANTES de que exista la columna
    # ``users.is_admin`` poblada (huevo-y-la-gallina): ``get_current_admin`` gatea
    # contra ``user.is_admin OR str(user_id) in admin_bootstrap_ids``. Default VACÍO
    # = nadie es admin por bootstrap (solo los que tengan la flag en DB). ``NoDecode``
    # desactiva el JSON-decode que ``EnvSettingsSource`` aplicaría a un ``list[str]``
    # (que crashea ante ``ADMIN_BOOTSTRAP_IDS=`` o ``=uuid1,uuid2``): el string crudo
    # del env llega tal cual al ``field_validator`` de abajo, que lo splittea por comas
    # (mismo patrón que ``trusted_proxy_ips`` / ``cors_origins``).
    admin_bootstrap_ids: Annotated[list[str], NoDecode] = Field(
        default_factory=list, alias="ADMIN_BOOTSTRAP_IDS"
    )

    @field_validator("trusted_proxy_ips", "cors_origins", "admin_bootstrap_ids", mode="before")
    @classmethod
    def _split_csv_lists(cls, v: object) -> object:
        """Acepta CSV/vacío desde env para los 3 ``list[str]`` marcados con ``NoDecode``.

        ``trusted_proxy_ips`` / ``cors_origins`` / ``admin_bootstrap_ids`` usan
        ``NoDecode`` (desactiva el JSON-decode que ``EnvSettingsSource`` aplicaría a un
        ``list[str]``), así que el string crudo del env llega tal cual acá: un CSV
        human-friendly (``a,b,c``) o vacío (``=``) que, sin esta normalización,
        crashearía el boot (el parser esperaría un JSON-array). Convierte el string a
        lista (vacío -> ``[]``); una lista ya parseada (kwargs / JSON) pasa intacta —
        así los tests que pasan ``cors_origins=[...]`` por kwarg siguen funcionando.

        La validación de CONTENIDO la hacen los ``model_validator`` de después, por
        campo: IP/CIDR (``_validate_trusted_proxy_ips``), UUID
        (``_validate_admin_bootstrap_ids``) y rechazo de host de dev en prod
        (``_reject_dev_config_in_prod`` para CORS). Antes eran 3 validators idénticos.
        """
        if isinstance(v, str):
            return [part.strip() for part in v.split(",") if part.strip()]
        return v

    @field_validator("llm_providers", mode="before")
    @classmethod
    def _parse_llm_providers(cls, v: object) -> object:
        """Decodifica el JSON de ``LLM_PROVIDERS``; vacío es sin proveedores.

        Una lista ya parseada (kwargs, en los tests) pasa intacta. El error de un JSON
        roto no repite el valor.
        """
        if not isinstance(v, str):
            return v
        if not v.strip():
            return []
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            raise ValueError(
                "LLM_PROVIDERS no es JSON válido: va una lista de proveedores"
            ) from None

    @field_validator("llm_providers")
    @classmethod
    def _reject_repeated_providers(cls, providers: list[LlmProvider]) -> list[LlmProvider]:
        """Fail-fast: dos proveedores con el mismo ``name`` compartirían credencial."""
        names = [provider.name for provider in providers]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"LLM_PROVIDERS repite el proveedor: {', '.join(repeated)}")
        return providers

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Suma ``_LlmApiKeysSource`` (las credenciales ``LLM_API_KEY_<NAME>``).

        El resto queda en el orden por default de pydantic-settings: kwargs, entorno,
        ``.env`` y secretos de archivo.
        """
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _LlmApiKeysSource(settings_cls, dotenv_settings, env_settings),
            file_secret_settings,
        )

    @model_validator(mode="after")
    def _default_celery_urls_to_redis(self) -> Settings:
        """Fallback: si las URLs de Celery no se setean, usar la de Redis.

        Mantiene una sola fuente de verdad para el broker/result-backend cuando el
        deploy no las define por separado. Un valor explícito SIEMPRE se respeta.

        Mutar ``self`` acá es el patrón idiomático de los ``model_validator(mode=
        'after')`` de Pydantic v2 (el validator corre sobre la instancia ya
        construida y devuelve ``self``): NO es una violación de inmutabilidad —
        ocurre durante la construcción del modelo, antes de que el objeto se
        exponga al resto de la app.
        """
        if not self.celery_broker_url:
            self.celery_broker_url = self.redis_url
        if not self.celery_result_backend:
            self.celery_result_backend = self.redis_url
        return self

    @model_validator(mode="after")
    def _reject_weak_jwt_secret_in_prod(self) -> Settings:
        """Fail-fast: en production el JWT_SECRET no puede ser débil ni placeholder.

        Un secret débil o conocido permite forjar tokens y suplantar a cualquier
        usuario. En development/staging se permite para no meter fricción.
        """
        if self.environment == "production":
            weak = {"", "cambiar-en-produccion", "secret", "changeme"}
            if self.jwt_secret in weak or len(self.jwt_secret) < 32:
                raise ValueError(
                    "JWT_SECRET débil o placeholder en production: mínimo 32 chars; "
                    "generar con `openssl rand -base64 48`"
                )
        return self

    @model_validator(mode="after")
    def _reject_dev_config_in_prod(self) -> Settings:
        """Fail-fast: en production rechaza CORS dev y exige la master key de cifrado.

        (a) ``cors_origins`` con ``localhost``/``127.0.0.1``: exponer un origin de dev
            en production abriría la API a un front local del atacante (CORS es la
            barrera del navegador contra requests cross-origin con credenciales). El
            ``# TODO: ajustar a dominios reales`` del default deja de ser opcional en
            prod: si quedó sin ajustar, el boot falla en vez de servir inseguro.
            ``cors_origins`` VACÍO (p.ej. ``CORS_ORIGINS=``) también falla: una lista
            vacía pasa el chequeo de ``any(...)`` (no hay origins de dev que detectar)
            pero deja la API sin política CORS configurada, lo que es mala config en
            prod — se exige explicitar los dominios reales del front.

        (b) ``MEMORY_ENCRYPTION_MASTER_KEY`` vacía: sin la key el cifrado de memoria
            (ADR-007) no funciona y el helper de crypto recién falla al PRIMER uso (un
            chat que escribe memoria), no al boot. En prod adelantamos ese fallo al
            arranque (fail-fast) para no descubrir la mala config en caliente.

        En development/staging NO rompe: el default de ``cors_origins`` ES localhost y
        la master key suele estar vacía en dev (sin fricción, igual que el JWT secret).
        """
        if self.environment == "production":
            # CORS vacío en prod: ``any(...)`` sobre lista vacía es False (no detecta
            # origins de dev), así que sin este chequeo previo el boot pasaría con CERO
            # origins. Eso es mala config (la API queda sin política CORS): se exige
            # explicitar los dominios reales del front en vez de bootear inseguro.
            if not self.cors_origins:
                raise ValueError(
                    "CORS_ORIGINS vacío en production: configurar los dominios reales "
                    "del front (p.ej. https://app.ynara.com)"
                )
            # Hostname EXACTO (urlsplit), no substring: así un dominio prod legítimo
            # que contenga 'localhost' como substring (p.ej. localhost-staging.x.com)
            # no da falso positivo. Igual falla cerrado ante el origin de dev real.
            dev_hosts = {"localhost", "127.0.0.1", "::1"}
            if any(urlsplit(origin).hostname in dev_hosts for origin in self.cors_origins):
                raise ValueError(
                    "CORS_ORIGINS contiene un origin de desarrollo (localhost/127.0.0.1) "
                    "en production: ajustar a los dominios reales del front"
                )
            if not self.memory_encryption_master_key:
                raise ValueError(
                    "MEMORY_ENCRYPTION_MASTER_KEY vacía en production: el cifrado de "
                    "memoria (ADR-007) la requiere; generar con `openssl rand -base64 32`"
                )
        return self

    @model_validator(mode="after")
    def _validate_trusted_proxy_ips(self) -> Settings:
        """Fail-fast: cada entry de ``trusted_proxy_ips`` debe ser un IP/CIDR válido.

        La allowlist gobierna cuándo se confía el header de IP real (anti-spoof, issue
        #151): una entry malformada que silenciosamente no matchee dejaría el freno por
        IP roto sin aviso. Validar al boot (consistente con los demás validators fail-fast
        del módulo) convierte una mala config en un error de arranque, no en un agujero
        de seguridad latente. ``strict=False`` acepta tanto IPs sueltas (``127.0.0.1``)
        como redes CIDR (``10.0.0.0/8``).
        """
        for entry in self.trusted_proxy_ips:
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"TRUSTED_PROXY_IPS contiene una entry inválida ({entry!r}): "
                    "cada valor debe ser un IP o CIDR parseable (p.ej. 127.0.0.1 o 10.0.0.0/8)"
                ) from exc
        return self

    @model_validator(mode="after")
    def _validate_admin_bootstrap_ids(self) -> Settings:
        """Fail-fast: cada entry de ``admin_bootstrap_ids`` debe ser un UUID válido.

        El bootstrap de admin gobierna el acceso al panel interno (/v1/admin/*) antes de
        que ``users.is_admin`` esté poblado: una entry malformada que silenciosamente no
        matchee ningún ``user_id`` dejaría sin admin de arranque sin aviso. Validar al boot
        (consistente con los demás validators fail-fast del módulo) convierte una mala
        config en un error de arranque. Mismo estilo que ``_validate_trusted_proxy_ips``.
        """
        for entry in self.admin_bootstrap_ids:
            try:
                uuid.UUID(entry)
            except ValueError as exc:
                raise ValueError(
                    f"ADMIN_BOOTSTRAP_IDS contiene una entry inválida ({entry!r}): "
                    "cada valor debe ser un UUID parseable"
                ) from exc
        return self

    @model_validator(mode="after")
    def _require_llm_api(self) -> Settings:
        """Fail-fast de la inferencia por API (ADR-028 D7).

        En production el LLM tiene que ser ``api``: con ``fake`` el chat le contestaría
        a un usuario real con el cliente de prueba. Con ``api``, en cualquier entorno,
        tiene que haber al menos un proveedor y cada uno con su credencial
        ``LLM_API_KEY_<NAME>``. El error nombra la variable que falta, nunca un valor.
        """
        if self.environment == "production" and self.llm_backend != "api":
            raise ValueError(
                "LLM_BACKEND tiene que ser 'api' en production: el cliente fake no le "
                "contesta a usuarios reales (ADR-028)"
            )
        if self.llm_backend != "api":
            return self
        if not self.llm_providers:
            raise ValueError(
                "LLM_PROVIDERS está vacío: con LLM_BACKEND=api hace falta al menos un proveedor"
            )
        missing = [p.api_key_env for p in self.llm_providers if p.name not in self.llm_api_keys]
        if missing:
            raise ValueError(f"falta la credencial del proveedor: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
