"""Cliente de chat contra la Messages API de Anthropic (ADR-031).

``AnthropicClient`` habla ``POST {base_url}/messages`` con la credencial en
``x-api-key`` y la versión de la API fija en ``anthropic-version``. Implementa
``LLMClient`` igual que ``OpenAICompatClient``: el router, el tool loop, el playground
y el ``ResilientClient`` lo usan sin saber qué proveedor atiende. Recibe el
``httpx.AsyncClient`` por constructor, así que se testea con ``httpx.MockTransport``
sin red.

Qué resuelve el cliente:

- Modelos: el código pide un alias (``conversacional`` o ``agente``) y el cliente manda
  el id del modelo (``model_map``). Un alias que no está levanta
  ``ModelNotServedError`` sin hacer el request.
- Razonamiento (D3, D4): cada alias tiene un modo para ``thinking=True`` (y ``None``) y
  otro para ``False``; con el adaptativo, ``max_tokens`` sube al piso del alias. No se
  manda ``temperature``, ``top_p`` ni ``top_k``: el argumento ``temperature`` se ignora.
- Historial de la vuelta (D5): reenvía tal cual los bloques que produjo esta misma
  entrada y rearma los ajenos sin razonamiento; agrupa los resultados de tools.
- Caché (D6): con dos mensajes ``system``, marca el primero.
- Errores (D7): el mapeo de ``_http``, también para el status de un stream y para el
  evento ``error`` a mitad del stream.

Regla #4a: la clave vive en un ``SecretStr`` que solo se lee al armar el header
(``AnthropicAuth``); ni el ``repr`` del cliente ni el detail de una excepción la
muestran, ni tampoco la URL o el body del proveedor. Solo se loguea un ``stop_reason``
desconocido, que es un valor de un enum de la API.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Generator, Mapping
from typing import Any

import httpx
from pydantic import SecretStr

from app.core.llm_providers import AliasThinking
from app.core.urls import validated_base_url
from app.llm.clients.providers._anthropic_stream import AnthropicStreamDecoder
from app.llm.clients.providers._anthropic_wire import (
    THINKING_BINDING_BETA,
    encode_messages,
    encode_system,
    encode_tools,
    parse_message,
    thinking_fields,
)
from app.llm.clients.providers._http import raise_for_provider_status, transport_errors
from app.llm.clients.providers._stream import sse_data
from app.llm.clients.providers._wire import json_body
from app.llm.errors import ModelNotServedError
from app.llm.schemas import (
    ChatMessage,
    CompletionChunk,
    CompletionResult,
    ModelHealth,
    ToolChoice,
    ToolSpec,
)

# Versión fija de la API (ADR-031 D1): cambiarla es su propio PR.
ANTHROPIC_API_VERSION = "2026-10-21"

_MESSAGES_PATH = "/messages"
_MODELS_PATH = "/models"


class AnthropicAuth(httpx.Auth):
    """Credencial como ``x-api-key`` y versión de la API en ``anthropic-version``.

    Va en cada request del cliente, también en ``health()``. La clave se lee del
    ``SecretStr`` recién al armar el header, como en ``BearerAuth``.
    """

    def __init__(self, api_key: SecretStr) -> None:
        self._api_key = api_key

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["x-api-key"] = self._api_key.get_secret_value()
        request.headers["anthropic-version"] = ANTHROPIC_API_VERSION
        yield request

    def __repr__(self) -> str:
        return "AnthropicAuth(***)"


class AnthropicClient:
    """Implementa ``LLMClient`` contra la Messages API de Anthropic."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: SecretStr,
        model_map: Mapping[str, str],
        thinking: Mapping[str, AliasThinking],
        http_client: httpx.AsyncClient,
        default_timeout_s: float = 30.0,
        health_timeout_s: float = 5.0,
    ) -> None:
        """Un ``AnthropicClient`` = una entrada de ``LLM_PROVIDERS`` (una credencial).

        - ``name``: nombre de la entrada; sale en ``repr``, en los logs del
          ``ResilientClient`` y en ``health()``, y marca los bloques que produce para
          reenviarlos solo a esta misma entrada.
        - ``base_url``: ``https://api.anthropic.com/v1``, o la de un doble de pruebas.
        - ``model_map``: ``{alias: id del modelo}``.
        - ``thinking``: ``{alias: AliasThinking}``, uno por alias de ``model_map``.
        - ``default_timeout_s``: timeout por request si el caller no pasa uno.
        - ``health_timeout_s``: timeout corto de ``health()``.

        El ``http_client`` queda a cargo de este cliente: ``aclose()`` lo cierra.
        """
        if not name.strip():
            raise ValueError("el proveedor necesita un nombre")
        base_url = validated_base_url(base_url)
        if not api_key.get_secret_value().strip():
            raise ValueError("el proveedor necesita una credencial")
        if not model_map or not all(
            alias.strip() and model_id.strip() for alias, model_id in model_map.items()
        ):
            raise ValueError("el mapa de modelos del proveedor está vacío o incompleto")
        missing = sorted(set(model_map) - set(thinking))
        if missing:
            raise ValueError(f"falta el razonamiento del alias: {', '.join(missing)}")
        self._name = name
        self._base_url = base_url
        self._auth = AnthropicAuth(api_key)
        self._model_map = dict(model_map)
        self._thinking = dict(thinking)
        self._http = http_client
        self._default_timeout_s = default_timeout_s
        self._health_timeout_s = health_timeout_s

    @property
    def name(self) -> str:
        """Nombre de la entrada en la config."""
        return self._name

    def __repr__(self) -> str:
        return f"AnthropicClient(name={self._name!r})"

    def serves_model(self, model: str) -> bool:
        return model in self._model_map

    async def aclose(self) -> None:
        """Cierra el ``httpx.AsyncClient`` subyacente (lo llama el shutdown del pool)."""
        await self._http.aclose()

    async def complete(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,  # el protocolo no la manda (D3).
        thinking: bool | None = None,
        tool_choice: ToolChoice = "auto",
        timeout_s: float | None = None,
    ) -> CompletionResult:
        model_id, payload, headers = self._build_request(
            model=model,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            thinking=thinking,
            tool_choice=tool_choice,
            stream=False,
        )
        started = time.perf_counter()
        with transport_errors():
            response = await self._http.post(
                f"{self._base_url}{_MESSAGES_PATH}",
                json=payload,
                headers=headers,
                timeout=self._timeout(timeout_s),
                auth=self._auth,
            )
        latency_ms = (time.perf_counter() - started) * 1000.0
        raise_for_provider_status(response)
        return parse_message(
            json_body(response), provider=self._name, model_id=model_id, latency_ms=latency_ms
        )

    async def stream(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,  # el protocolo no la manda (D3).
        thinking: bool | None = None,
        tool_choice: ToolChoice = "auto",
        timeout_s: float | None = None,
    ) -> AsyncIterator[CompletionChunk]:
        _model_id, payload, headers = self._build_request(
            model=model,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            thinking=thinking,
            tool_choice=tool_choice,
            stream=True,
        )
        decoder = AnthropicStreamDecoder()
        with transport_errors():
            async with self._http.stream(
                "POST",
                f"{self._base_url}{_MESSAGES_PATH}",
                json=payload,
                headers=headers,
                timeout=self._timeout(timeout_s),
                auth=self._auth,
            ) as response:
                if response.status_code >= httpx.codes.BAD_REQUEST:
                    # Sin leer el body no se ven el contexto excedido ni el tope de gasto.
                    await response.aread()
                    raise_for_provider_status(response)
                async for line in response.aiter_lines():
                    data = sse_data(line)
                    if data is None:
                        continue
                    chunk = decoder.feed(data)
                    if chunk is not None:
                        yield chunk
                    if decoder.done:
                        break

    async def health(self) -> ModelHealth:
        """``GET {base_url}/models`` con la credencial: sano si responde 200.

        Valida la credencial sin gastar tokens. Un 401 también da no-sano.
        """
        try:
            response = await self._http.get(
                f"{self._base_url}{_MODELS_PATH}",
                timeout=self._health_timeout_s,
                auth=self._auth,
            )
        except httpx.RequestError:
            return ModelHealth(model_name=self._name, healthy=False)
        return ModelHealth(model_name=self._name, healthy=response.status_code == httpx.codes.OK)

    # ---------- helpers internos ----------

    def _timeout(self, timeout_s: float | None) -> float:
        return timeout_s if timeout_s is not None else self._default_timeout_s

    def _build_request(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None,
        max_tokens: int,
        thinking: bool | None,
        tool_choice: ToolChoice,
        stream: bool,
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        """Id del modelo, body y headers extra del request."""
        model_id = self._model_map.get(model)
        if model_id is None:
            raise ModelNotServedError(model)
        alias = self._thinking[model]
        fields, needs_beta = thinking_fields(
            alias.mode_for(thinking),
            max_tokens=max_tokens,
            max_tokens_floor=alias.max_tokens_floor,
        )
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": encode_messages(messages, provider=self._name),
            **fields,
            **encode_tools(tools, tool_choice),
            "stream": stream,
        }
        system = encode_system(messages)
        if system:
            payload["system"] = system
        headers = {"anthropic-beta": THINKING_BINDING_BETA} if needs_beta else {}
        return model_id, payload, headers
