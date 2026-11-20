"""Cliente de chat contra la API de Chat Completions de un proveedor de IA (ADR-028).

``OpenAICompatClient`` habla ``POST {base_url}/chat/completions`` de cualquier
proveedor compatible con OpenAI, con la credencial del proveedor como
``Authorization: Bearer``. Implementa ``LLMClient``: el router, el tool loop, el
playground y el ``ResilientClient`` lo usan sin saber qué proveedor atiende. Recibe el
``httpx.AsyncClient`` por constructor, así que se testea con ``httpx.MockTransport``
sin red.

Qué resuelve el cliente:

- Modelos: el código pide un alias de ``ynara.config.json`` (``conversacional`` o
  ``agente``) y el cliente manda el id del modelo en el proveedor (``model_map``). Un
  alias que no está en el mapa levanta ``ModelNotServedError`` sin hacer el request.
- Tools: ``calendar.create_event`` viaja como ``calendar__create_event`` y vuelve
  revertido, también en streaming (``_tool_names``).
- Razonamiento: ``reasoning_effort`` solo si el proveedor tiene un valor configurado
  para lo que pide el caller (``thinking=True`` / ``False``); con ``None`` no se manda.
  Se lee del canal (``reasoning_content`` o ``reasoning``) o del ``<think>`` inline del
  ``content`` (``_think``). La forma de ``CompletionResult`` y ``CompletionChunk`` no
  cambia.
- Errores: el mapeo de ``_http`` (credencial, límite de tasa con ``Retry-After``,
  contexto excedido, caídas y red), también para el status de un stream.

Sin extensiones propias de un server (``chat_template_kwargs``) ni APIs nativas.

Regla #4a: el cliente no loguea nada. La clave vive en un ``SecretStr`` que solo se lee
al armar el header (``BearerAuth``); ni el ``repr`` del cliente ni el detail de una
excepción la muestran, ni tampoco la URL o el body del proveedor.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx
from pydantic import SecretStr

from app.core.urls import validated_base_url
from app.llm.clients.base import ToolCallParser
from app.llm.clients.parsers import OpenAIToolCallParser
from app.llm.clients.providers._http import (
    BearerAuth,
    raise_for_provider_status,
    transport_errors,
)
from app.llm.clients.providers._stream import SSE_DONE, StreamDecoder, sse_data
from app.llm.clients.providers._think import split_inline_thinking
from app.llm.clients.providers._wire import (
    content_text,
    decode_tool_names,
    encode_message,
    encode_tool,
    finish_reason,
    json_body,
    reasoning_channel,
    token_count,
)
from app.llm.errors import LlmError, ModelNotServedError
from app.llm.schemas import (
    ChatMessage,
    CompletionChunk,
    CompletionResult,
    ModelHealth,
    ToolSpec,
)

_CHAT_PATH = "/chat/completions"
_MODELS_PATH = "/models"


class OpenAICompatClient:
    """Implementa ``LLMClient`` contra la API de Chat Completions de un proveedor."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: SecretStr,
        model_map: Mapping[str, str],
        http_client: httpx.AsyncClient,
        reasoning_effort_on: str | None = None,
        reasoning_effort_off: str | None = None,
        parser: ToolCallParser | None = None,
        default_timeout_s: float = 30.0,
        health_timeout_s: float = 5.0,
    ) -> None:
        """Un ``OpenAICompatClient`` = un proveedor (una URL base y una credencial).

        - ``name``: nombre del proveedor en la config (``"principal"``, ``"respaldo"``);
          sale en ``repr``, en los logs del ``ResilientClient`` y en ``health()``.
        - ``base_url``: URL base del proveedor (``https://.../v1``); ``http`` solo
          contra la propia máquina (ver ``validated_base_url``).
        - ``model_map``: ``{alias: id del modelo en el proveedor}``.
        - ``reasoning_effort_on`` / ``reasoning_effort_off``: valor de
          ``reasoning_effort`` para ``thinking=True`` / ``False``. ``None`` = el
          proveedor o el modelo no lo soportan y no se manda (cada proveedor acepta
          valores distintos, por ejemplo ``"none"`` o ``"minimal"`` para apagarlo).
        - ``default_timeout_s``: timeout por request si el caller no pasa uno.
        - ``health_timeout_s``: timeout corto de ``health()``.

        El ``http_client`` queda a cargo de este cliente: ``aclose()`` lo cierra.
        """
        if not name.strip():
            raise ValueError("el proveedor necesita un nombre")
        base_url = validated_base_url(base_url)
        if not api_key.get_secret_value().strip():
            raise ValueError("el proveedor necesita una credencial")
        if not model_map or not all(alias and model_id for alias, model_id in model_map.items()):
            raise ValueError("el mapa de modelos del proveedor está vacío o incompleto")
        for effort in (reasoning_effort_on, reasoning_effort_off):
            if effort is not None and not effort.strip():
                raise ValueError("reasoning_effort vacío: usá None para no mandarlo")
        self._name = name
        self._base_url = base_url
        self._auth = BearerAuth(api_key)
        self._model_map = dict(model_map)
        self._http = http_client
        self._reasoning_effort = {True: reasoning_effort_on, False: reasoning_effort_off}
        self._parser = parser if parser is not None else OpenAIToolCallParser()
        self._default_timeout_s = default_timeout_s
        self._health_timeout_s = health_timeout_s

    @property
    def name(self) -> str:
        """Nombre del proveedor en la config."""
        return self._name

    def __repr__(self) -> str:
        return f"OpenAICompatClient(name={self._name!r})"

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
        temperature: float = 0.7,
        thinking: bool | None = None,
        timeout_s: float | None = None,
    ) -> CompletionResult:
        model_id = self._model_id(model)
        payload = self._build_payload(
            model_id=model_id,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=thinking,
            stream=False,
        )
        started = time.perf_counter()
        with transport_errors():
            response = await self._http.post(
                f"{self._base_url}{_CHAT_PATH}",
                json=payload,
                timeout=self._timeout(timeout_s),
                auth=self._auth,
            )
        latency_ms = (time.perf_counter() - started) * 1000.0
        raise_for_provider_status(response)
        return self._parse_completion(json_body(response), model_id, latency_ms)

    async def stream(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        thinking: bool | None = None,
        timeout_s: float | None = None,
    ) -> AsyncIterator[CompletionChunk]:
        model_id = self._model_id(model)
        payload = self._build_payload(
            model_id=model_id,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=thinking,
            stream=True,
        )
        decoder = StreamDecoder()
        with transport_errors():
            async with self._http.stream(
                "POST",
                f"{self._base_url}{_CHAT_PATH}",
                json=payload,
                timeout=self._timeout(timeout_s),
                auth=self._auth,
            ) as response:
                if response.status_code >= httpx.codes.BAD_REQUEST:
                    # Sin leer el body no se distingue el contexto excedido de otro 400.
                    await response.aread()
                    raise_for_provider_status(response)
                async for line in response.aiter_lines():
                    data = sse_data(line)
                    if data is None:
                        continue
                    if data == SSE_DONE:
                        break
                    chunk = decoder.feed(data)
                    if chunk is not None:
                        yield chunk
        tail = decoder.finish()
        if tail is not None:
            yield tail

    async def health(self) -> ModelHealth:
        """``GET {base_url}/models`` con la credencial: sano si responde 200.

        Un 401 también da no-sano: con esa credencial el proveedor no sirve. El
        ``model_name`` es el nombre del proveedor (un proveedor sirve varios modelos).
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

    def _model_id(self, model: str) -> str:
        model_id = self._model_map.get(model)
        if model_id is None:
            raise ModelNotServedError(model)
        return model_id

    def _timeout(self, timeout_s: float | None) -> float:
        return timeout_s if timeout_s is not None else self._default_timeout_s

    def _build_payload(
        self,
        *,
        model_id: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None,
        max_tokens: int,
        temperature: float,
        thinking: bool | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": [encode_message(m) for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = [encode_tool(t) for t in tools]
            payload["tool_choice"] = "auto"
        effort = self._reasoning_effort[thinking] if thinking is not None else None
        if effort is not None:
            payload["reasoning_effort"] = effort
        return payload

    def _parse_completion(self, body: Any, model_id: str, latency_ms: float) -> CompletionResult:
        if not isinstance(body, dict):
            raise LlmError("respuesta malformada")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LlmError("respuesta sin choices")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            message = {}
        text, inline_reasoning = split_inline_thinking(content_text(message.get("content")))
        tool_calls = self._parser.parse(decode_tool_names(message))
        usage = body.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        # El proveedor informa el modelo concreto (p.ej. con fecha de versión).
        model_name = body.get("model")
        if not isinstance(model_name, str) or not model_name:
            model_name = model_id
        return CompletionResult(
            text=text,
            tool_calls=tool_calls,
            finish_reason=finish_reason(choice.get("finish_reason"), bool(tool_calls))
            or ("tool_calls" if tool_calls else "stop"),
            prompt_tokens=token_count(usage.get("prompt_tokens")),
            completion_tokens=token_count(usage.get("completion_tokens")),
            model_name=model_name,
            latency_ms=latency_ms,
            reasoning=reasoning_channel(message) or inline_reasoning,
        )
