"""Tests de la taxonomia de errores LLM (M1).

Verifican la jerarquia (transitorios / permanentes / semanticos) y la
garantia critica de la regla #4: ``str()`` de cualquier error NO filtra
contenido del usuario ni la respuesta del modelo.
"""

from __future__ import annotations

import pytest

from app.llm.errors import (
    EmbeddingDimensionError,
    LlmAuthError,
    LlmBadRequestError,
    LlmContextOverflowError,
    LlmError,
    LlmOverloadedError,
    LlmProviderRejectedError,
    LlmRateLimitError,
    LlmTimeoutError,
    LlmTransientError,
    LlmUnavailableError,
    ModelNotServedError,
    ToolParsingError,
    degraded_response,
)

_ALL_ERRORS = [
    LlmTransientError,
    LlmTimeoutError,
    LlmUnavailableError,
    LlmOverloadedError,
    LlmRateLimitError,
    LlmBadRequestError,
    LlmContextOverflowError,
    ModelNotServedError,
    LlmAuthError,
    LlmProviderRejectedError,
    EmbeddingDimensionError,
    ToolParsingError,
]


@pytest.mark.parametrize("error_cls", _ALL_ERRORS)
def test_all_inherit_from_llm_error(error_cls: type[LlmError]) -> None:
    assert issubclass(error_cls, LlmError)
    assert isinstance(error_cls(), LlmError)


def test_context_overflow_is_bad_request() -> None:
    """ContextOverflow es permanente: subclase de BadRequest."""
    assert issubclass(LlmContextOverflowError, LlmBadRequestError)
    assert isinstance(LlmContextOverflowError(), LlmBadRequestError)


@pytest.mark.parametrize("error_cls", _ALL_ERRORS)
def test_str_does_not_leak_user_content(error_cls: type[LlmError]) -> None:
    """Aunque se pase contenido sensible como detail, str() solo expone la
    etiqueta tecnica si no se pasa detail; y si se pasa, el caller es
    responsable. Por contrato, sin detail nunca hay contenido de usuario."""
    secret = "el usuario dijo: mi tarjeta es 4111-1111-1111-1111"
    err = error_cls()
    rendered = str(err)
    assert secret not in rendered
    assert rendered  # no vacio
    # La etiqueta es fija y tecnica.
    assert rendered == error_cls.message


def test_detail_is_optional_and_technical() -> None:
    """detail permite contexto tecnico no sensible (status HTTP, modelo)."""
    err = LlmUnavailableError("HTTP 503")
    assert "HTTP 503" in str(err)
    assert str(err).startswith(LlmUnavailableError.message)


def test_transient_vs_permanent_disjoint() -> None:
    """Un transitorio no es permanente y viceversa (sin herencia cruzada)."""
    transient = LlmTimeoutError()
    permanent = LlmBadRequestError()
    assert not isinstance(transient, LlmBadRequestError)
    assert not isinstance(permanent, (LlmTimeoutError, LlmUnavailableError))


_TRANSIENT_ERRORS = [LlmTimeoutError, LlmUnavailableError, LlmOverloadedError, LlmRateLimitError]
_NON_TRANSIENT_ERRORS = [
    LlmBadRequestError,
    LlmContextOverflowError,
    ModelNotServedError,
    LlmAuthError,
    LlmProviderRejectedError,
    EmbeddingDimensionError,
    ToolParsingError,
]


@pytest.mark.parametrize("error_cls", _TRANSIENT_ERRORS)
def test_transient_errors_share_a_root(error_cls: type[LlmError]) -> None:
    """Timeout, caida, sobrecarga y 429 son ``LlmTransientError``: una clase para reintentar."""
    assert issubclass(error_cls, LlmTransientError)


@pytest.mark.parametrize("error_cls", _NON_TRANSIENT_ERRORS)
def test_permanent_and_semantic_errors_are_not_transient(error_cls: type[LlmError]) -> None:
    """Un error permanente o semantico no se reintenta: no hereda de ``LlmTransientError``."""
    assert not issubclass(error_cls, LlmTransientError)


def test_transient_root_has_a_fixed_label() -> None:
    """La raiz se levanta sola (cadena agotada): etiqueta fija, sin contenido del usuario."""
    err = LlmTransientError("cadena agotada")
    assert str(err) == "falla transitoria del proveedor LLM: cadena agotada"
    assert isinstance(err, LlmError)


def test_rate_limit_is_overloaded_with_retry_after() -> None:
    """El 429 de un proveedor es sobrecarga (mismo trato) y lleva la espera pedida."""
    err = LlmRateLimitError("HTTP 429", retry_after_s=12.5)
    assert isinstance(err, LlmOverloadedError)
    assert err.retry_after_s == 12.5
    assert str(err) == "limite de tasa del proveedor LLM: HTTP 429"
    assert LlmRateLimitError().retry_after_s is None


def test_auth_error_is_not_a_request_error() -> None:
    """La credencial rechazada es del proveedor, no del request: no hereda de BadRequest.

    Si heredara, el ``ResilientClient`` la re-lanzaria sin probar el respaldo.
    """
    err = LlmAuthError("HTTP 401")
    assert not isinstance(err, LlmBadRequestError)
    assert not isinstance(err, (LlmTimeoutError, LlmUnavailableError, LlmOverloadedError))
    assert str(err) == "credencial rechazada por el proveedor LLM: HTTP 401"


def test_provider_rejected_is_not_a_request_error() -> None:
    """Un 4xx propio del proveedor no es un request invalido: otro proveedor puede aceptarlo.

    Si heredara de ``LlmBadRequestError``, el ``ResilientClient`` lo re-lanzaria sin
    probar el respaldo.
    """
    err = LlmProviderRejectedError("HTTP 404")
    assert not isinstance(err, (LlmBadRequestError, LlmAuthError))
    assert not isinstance(err, (LlmTimeoutError, LlmUnavailableError, LlmOverloadedError))
    assert str(err) == "request rechazado por el proveedor LLM: HTTP 404"


def test_embedding_dimension_carries_only_the_dimensions() -> None:
    """Un vector de otra dimension es permanente y el error lleva solo los dos numeros."""
    err = EmbeddingDimensionError("768 en vez de 1024", expected=1024, received=768)
    assert (err.expected, err.received) == (1024, 768)
    assert str(err) == "dimension de embedding inesperada: 768 en vez de 1024"
    assert not isinstance(err, (LlmBadRequestError, LlmAuthError, LlmProviderRejectedError))
    assert not isinstance(err, (LlmTimeoutError, LlmUnavailableError, LlmOverloadedError))
    bare = EmbeddingDimensionError()
    assert (bare.expected, bare.received) == (None, None)


class TestDegradedResponse:
    def test_builds_completion_result(self) -> None:
        result = degraded_response(text="no pude generar respuesta", model_name="agente")
        assert result.finish_reason == "degraded"
        assert result.text == "no pude generar respuesta"
        assert result.tool_calls == []
        assert result.prompt_tokens == 0
        assert result.model_name == "agente"

    def test_not_an_exception(self) -> None:
        result = degraded_response(text="x", model_name="m")
        assert not isinstance(result, Exception)
