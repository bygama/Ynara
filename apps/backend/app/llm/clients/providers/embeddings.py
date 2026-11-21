"""Cliente de embeddings contra la API de Embeddings de un proveedor de IA (ADR-028 D4).

``ApiEmbeddingClient`` habla ``POST {base_url}/embeddings`` de cualquier proveedor
compatible con OpenAI, con la credencial del proveedor como ``Authorization: Bearer``.
Implementa ``EmbeddingClient``: los stores de memoria lo usan sin saber qué proveedor
atiende. Recibe el ``httpx.AsyncClient`` por constructor, así que se testea con
``httpx.MockTransport`` sin red.

Qué resuelve el cliente:

- Modelo: ``model`` (``EMBEDDING_MODEL``, default ``BAAI/bge-m3``) viaja tal cual. El
  embedder tiene un solo modelo y no usa los alias del chat.
- Dimensión: cada vector tiene que traer exactamente ``EMBEDDING_DIM`` (1024) valores,
  la dimensión de las columnas ``vector(1024)`` de la memoria. Si no, levanta
  ``EmbeddingDimensionError``: no recorta ni rellena, porque el vector ya no
  representaría al texto.
- Lotes: parte la lista en requests de hasta ``batch_size`` textos, uno detrás del otro,
  y devuelve los vectores en el orden de entrada. Si falla un lote, falla todo el
  ``embed()``: nunca devuelve una lista a medias.
- Errores: el mismo mapeo que el cliente de chat (``_http``): credencial rechazada
  (401/403), límite de tasa con la espera de ``Retry-After`` (429), request inválido,
  caídas y red. No reintenta: decide quien llama.
- Salud: ``GET {base_url}/models``, que no gasta tokens.

Regla #4a: el cliente no loguea nada, y ninguna excepción lleva los textos, los
vectores, la URL ni la credencial. La clave vive en un ``SecretStr`` que solo se lee al
armar el header (``BearerAuth``).
"""

from __future__ import annotations

import math
from typing import Any

import httpx
from pydantic import SecretStr

from app.core.constants import (
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_DIM,
)
from app.core.urls import validated_base_url
from app.llm.clients.providers._http import (
    BearerAuth,
    raise_for_provider_status,
    transport_errors,
)
from app.llm.clients.providers._wire import json_body
from app.llm.errors import EmbeddingDimensionError, LlmError
from app.llm.schemas import ModelHealth

_EMBEDDINGS_PATH = "/embeddings"
_MODELS_PATH = "/models"
_MALFORMED = "respuesta de embeddings malformada"


class ApiEmbeddingClient:
    """Implementa ``EmbeddingClient`` contra la API de Embeddings de un proveedor."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        http_client: httpx.AsyncClient,
        model: str = DEFAULT_EMBEDDING_MODEL,
        batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
        default_timeout_s: float = 30.0,
        health_timeout_s: float = 5.0,
    ) -> None:
        """Un ``ApiEmbeddingClient`` = un proveedor de embeddings (URL base y credencial).

        - ``base_url``: URL base del proveedor (``https://.../v1``); ``http`` solo
          contra la propia máquina (ver ``validated_base_url``).
        - ``model``: id del modelo en el proveedor; tiene que dar vectores de
          ``EMBEDDING_DIM``.
        - ``batch_size``: máximo de textos por request.
        - ``default_timeout_s``: timeout de cada request de ``embed()``.
        - ``health_timeout_s``: timeout corto de ``health()``.

        El ``http_client`` queda a cargo de este cliente: ``aclose()`` lo cierra.
        """
        base_url = validated_base_url(base_url)
        cleaned_key = api_key.get_secret_value().strip()
        if not cleaned_key:
            raise ValueError("el proveedor de embeddings necesita una credencial")
        cleaned_model = model.strip()
        if not cleaned_model:
            raise ValueError("el proveedor de embeddings necesita un modelo")
        if batch_size < 1:
            raise ValueError("el lote de embeddings necesita al menos un texto")
        self._base_url = base_url
        self._auth = BearerAuth(SecretStr(cleaned_key))
        self._http = http_client
        self._model = cleaned_model
        self._batch_size = batch_size
        self._default_timeout_s = default_timeout_s
        self._health_timeout_s = health_timeout_s

    @property
    def model(self) -> str:
        """Id del modelo de embeddings en el proveedor."""
        return self._model

    def __repr__(self) -> str:
        return f"ApiEmbeddingClient(model={self._model!r})"

    async def aclose(self) -> None:
        """Cierra el ``httpx.AsyncClient`` subyacente (lo llama el shutdown del lifespan)."""
        await self._http.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Un vector de ``EMBEDDING_DIM`` por texto, en el mismo orden.

        Una lista vacía no hace ningún request (la API rechaza un ``input`` vacío), igual
        que ``FakeEmbeddingClient``.
        """
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            vectors.extend(await self._embed_batch(batch))
        return vectors

    async def health(self) -> ModelHealth:
        """``GET {base_url}/models`` con la credencial: sano si responde 200.

        No gasta tokens. Un 401 también da no-sano: con esa credencial el proveedor no
        sirve. El ``model_name`` es el modelo de embeddings configurado.
        """
        try:
            response = await self._http.get(
                f"{self._base_url}{_MODELS_PATH}",
                timeout=self._health_timeout_s,
                auth=self._auth,
            )
        except httpx.RequestError:
            return ModelHealth(model_name=self._model, healthy=False)
        return ModelHealth(model_name=self._model, healthy=response.status_code == httpx.codes.OK)

    # ---------- helpers internos ----------

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        """Un request a ``/embeddings`` con hasta ``batch_size`` textos."""
        payload: dict[str, Any] = {
            "model": self._model,
            "input": batch,
            # Explícito: algunos proveedores devuelven base64 si no se pide.
            "encoding_format": "float",
        }
        with transport_errors():
            response = await self._http.post(
                f"{self._base_url}{_EMBEDDINGS_PATH}",
                json=payload,
                timeout=self._default_timeout_s,
                auth=self._auth,
            )
        raise_for_provider_status(response)
        return _parse_vectors(json_body(response), expected=len(batch))


def _parse_vectors(body: Any, *, expected: int) -> list[list[float]]:
    """Los vectores del body, en el orden de los textos pedidos.

    El body tiene la forma de OpenAI: ``data`` con un ``{"index", "embedding"}`` por
    texto, en cualquier orden. Exige un vector por texto, los ``index`` de ``0`` a
    ``expected - 1`` sin repetir y, en cada vector, ``EMBEDDING_DIM`` números finitos.
    Ningún error muestra el body (regla #4a).
    """
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or len(data) != expected:
        raise LlmError(_MALFORMED)
    by_index: dict[int, list[float]] = {}
    for item in data:
        if not isinstance(item, dict):
            raise LlmError(_MALFORMED)
        index = item.get("index")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < expected
            or index in by_index
        ):
            raise LlmError(_MALFORMED)
        by_index[index] = _vector(item.get("embedding"))
    return [by_index[index] for index in range(expected)]


def _vector(raw: Any) -> list[float]:
    """Un ``embedding`` del body como ``EMBEDDING_DIM`` floats, sin recortar ni rellenar."""
    if not isinstance(raw, list):
        raise LlmError(_MALFORMED)
    if len(raw) != EMBEDDING_DIM:
        raise EmbeddingDimensionError(
            f"{len(raw)} en vez de {EMBEDDING_DIM}",
            expected=EMBEDDING_DIM,
            received=len(raw),
        )
    if not all(_is_finite_number(value) for value in raw):
        raise LlmError(_MALFORMED)
    return [float(value) for value in raw]


def _is_finite_number(value: Any) -> bool:
    """``True`` si ``value`` es un número JSON finito (``NaN`` e infinito no entran a pgvector)."""
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
