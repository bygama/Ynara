"""URL base de un proveedor por API (``validated_base_url`` en ``app/core/urls.py``).

Se valida al construir el ``OpenAICompatClient``: ``https`` con un host (``http`` solo
contra la propia máquina), sin usuario ni clave, sin query ni fragmento. Los errores
son mensajes fijos que nunca repiten la URL.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from app.core.urls import validated_base_url
from app.llm.clients.providers.openai_compat import OpenAICompatClient


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://api.proveedor.test/v1", "https://api.proveedor.test/v1"),
        ("  https://api.proveedor.test/v1/  ", "https://api.proveedor.test/v1"),
        ("https://api.proveedor.test:8443/openai/v1", "https://api.proveedor.test:8443/openai/v1"),
        ("HTTPS://API.Proveedor.test/v1", "HTTPS://API.Proveedor.test/v1"),
        ("http://localhost:8080/v1", "http://localhost:8080/v1"),
        ("http://inferencia.localhost/v1", "http://inferencia.localhost/v1"),
        ("http://127.0.0.1:8000/v1", "http://127.0.0.1:8000/v1"),
        ("http://[::1]:8000/v1", "http://[::1]:8000/v1"),
    ],
)
def test_accepts_https_and_local_http(raw: str, expected: str) -> None:
    assert validated_base_url(raw) == expected


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "necesita una URL base"),
        ("   ", "necesita una URL base"),
        ("api.proveedor.test/v1", "tiene que ser https"),
        ("ftp://api.proveedor.test/v1", "tiene que ser https"),
        ("http://api.proveedor.test/v1", "http solo en local"),
        ("http://10.0.0.5:8000/v1", "http solo en local"),
        ("http://localhost.proveedor.test/v1", "http solo en local"),
        ("https:///v1", "necesita un host"),
        ("https://usuario:clave-secreta@api.proveedor.test/v1", "no lleva usuario ni clave"),
        ("https://api.proveedor.test/v1?api-version=2026-01", "no lleva query ni fragmento"),
        ("https://api.proveedor.test/v1#anchor", "no lleva query ni fragmento"),
        ("https://api.proveedor.test:99999/v1", "no es válida"),
        ("https://api.proveedor.test:puerto/v1", "no es válida"),
        ("https://[::1/v1", "no es válida"),
        ("https://api proveedor.test/v1", "no es válida"),
    ],
)
def test_rejects_invalid_url_without_echoing_it(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message) as excinfo:
        validated_base_url(raw)
    rendered = str(excinfo.value)
    assert "proveedor" in rendered
    if raw.strip():
        assert raw.strip() not in rendered
    assert "api.proveedor.test" not in rendered
    assert "clave-secreta" not in rendered
    assert excinfo.value.__cause__ is None


async def test_client_rejects_plain_http_to_a_remote_provider() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError, match="tiene que ser https"):
            OpenAICompatClient(
                name="principal",
                base_url="http://api.proveedor.test/v1",
                api_key=SecretStr("sk-test-0123456789abcdef"),
                model_map={"agente": "org/modelo"},
                http_client=http,
            )
