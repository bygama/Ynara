"""Resolución compartida de IP de cliente y parseo cacheado de redes IP/CIDR.

Unifica el contrato de ``TRUSTED_PROXY_IPS`` + ``REAL_IP_HEADER`` entre rate-limit
(``app.api.v1.auth``) y allowlist de acceso admin (``app.core.deps``).
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache
def parse_ip_networks(raw: tuple[str, ...]) -> tuple[IpNetwork, ...]:
    """Parsea una tupla de IPs/CIDRs a redes ``ipaddress``, cacheado.

    Recibe una tupla (hashable) para poder cachear por ``lru_cache``: ``Settings`` ya
    validó al arranque que cada entrada es parseable.
    """
    return tuple(ipaddress.ip_network(entry, strict=False) for entry in raw)


def resolve_client_ip(
    peer: str,
    header_value: str | None,
    *,
    trusted_networks: tuple[IpNetwork, ...],
) -> str:
    """Resuelve la IP del cliente para rate-limit (pura, testeable; issue #151).

    El header de IP real SOLO se confía cuando el peer inmediato pertenece a
    ``trusted_networks``. Si el proxy confiable no entrega una única IP válida en el
    header, cae al ``peer`` (nunca a una lista XFF ni a basura).
    """
    if peer == "unknown":
        return peer
    try:
        peer_addr = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if not any(peer_addr in net for net in trusted_networks):
        return peer
    if header_value is not None:
        candidate = header_value.strip()
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            return peer
        return candidate
    return peer


def resolve_verified_client_ip(
    peer: str,
    header_value: str | None,
    *,
    trusted_networks: tuple[IpNetwork, ...],
) -> IpAddress | None:
    """Resuelve la IP del cliente con política fail-closed para allowlists de acceso.

    - Si ``peer`` no es una IP válida -> ``None``.
    - Si ``peer`` pertenece a ``trusted_networks`` -> exige una única IP válida en
      ``header_value``; si el header falta o es inválido devuelve ``None`` en lugar de
      caer a la IP del proxy.
    - Si ``peer`` NO pertenece a ``trusted_networks`` -> ignora ``header_value`` y
      devuelve ``peer``.
    """
    try:
        peer_addr = ipaddress.ip_address(peer)
    except ValueError:
        return None
    if not any(peer_addr in net for net in trusted_networks):
        return peer_addr
    if header_value is None:
        return None
    try:
        return ipaddress.ip_address(header_value.strip())
    except ValueError:
        return None
