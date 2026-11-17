"""URL base de un proveedor de IA por API (ADR-028).

``validated_base_url`` decide qué URL base se acepta para un proveedor. La usan
``Settings``, al cargar ``LLM_PROVIDERS`` (así una URL inválida frena el arranque), y
los clientes de ``app/llm/clients/providers/``, al construirse. Vive en ``app/core``
para que la config la use sin importar el paquete de los proveedores. No hace red:
solo valida el texto.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

import httpx

_INVALID_BASE_URL = "la URL base del proveedor no es válida"


def validated_base_url(base_url: str) -> str:
    """La URL base de un proveedor sin espacios ni la ``/`` final; ``ValueError`` si no sirve.

    Exige ``https`` con un host. ``http`` se acepta solo contra la propia máquina
    (``localhost``, ``*.localhost`` o una IP de loopback), para un servidor compatible
    corriendo en local. La URL no lleva usuario ni clave (la credencial va aparte, en
    ``BearerAuth``) ni query o fragmento, porque el cliente le agrega el path atrás.
    Los mensajes de error son fijos y nunca repiten la URL.
    """
    url = base_url.strip().rstrip("/")
    if not url:
        raise ValueError("el proveedor necesita una URL base")
    if any(char.isspace() for char in url):
        raise ValueError(_INVALID_BASE_URL)
    try:
        parts = urlsplit(url)
        _ = parts.port  # un puerto que no es número o está fuera de rango levanta acá
        httpx.URL(url)
    except (httpx.InvalidURL, ValueError):
        # ``from None``: el mensaje de la excepción original puede repetir la URL.
        raise ValueError(_INVALID_BASE_URL) from None
    if parts.scheme not in ("http", "https"):
        raise ValueError("la URL base del proveedor tiene que ser https")
    host = parts.hostname
    if not host:
        raise ValueError("la URL base del proveedor necesita un host")
    if "@" in parts.netloc:
        raise ValueError("la URL base del proveedor no lleva usuario ni clave")
    if "?" in url or "#" in url:
        raise ValueError("la URL base del proveedor no lleva query ni fragmento")
    if parts.scheme == "http" and not _is_loopback_host(host):
        raise ValueError("la URL base del proveedor tiene que ser https (http solo en local)")
    return url


def _is_loopback_host(host: str) -> bool:
    """``True`` si ``host`` es la propia máquina (``localhost`` o una IP de loopback)."""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
