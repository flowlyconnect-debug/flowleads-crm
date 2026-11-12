"""Outgoing webhook URLs must point at the public internet.

Without this an organization admin could make the server call internal
addresses (cloud metadata 169.254.169.254, localhost services, the private
network) and read the answer on the deliveries page (SSRF).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeWebhookUrl(ValueError):
    pass


def _blocked(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def check_webhook_url(url: str, *, resolve: bool = True) -> str:
    """Returns the URL or raises UnsafeWebhookUrl (Finnish message). A host
    that does not resolve is accepted here; it simply fails on delivery."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise UnsafeWebhookUrl("Webhookin osoitteen pitää alkaa https://.")
    host = parsed.hostname
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _blocked(literal):
            raise UnsafeWebhookUrl("Webhook ei voi osoittaa sisäverkon tai paikalliseen osoitteeseen.")
        return url
    if host.lower() in ("localhost",) or host.lower().endswith((".localhost", ".local", ".internal")):
        raise UnsafeWebhookUrl("Webhook ei voi osoittaa sisäverkon tai paikalliseen osoitteeseen.")
    if resolve:
        try:
            addresses = _resolve(host)
        except (socket.gaierror, UnicodeError, OSError):
            return url
        for address in addresses:
            try:
                if _blocked(ipaddress.ip_address(address.split("%")[0])):
                    raise UnsafeWebhookUrl("Webhook ei voi osoittaa sisäverkon tai paikalliseen osoitteeseen.")
            except ValueError:
                continue
    return url
