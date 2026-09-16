"""Guarding outbound requests to user-supplied URLs (webhooks, feeds, guidelines) against reaching private networks."""

import ipaddress
import os
import socket
from urllib.parse import urlparse


class UnsafeURL(ValueError):
    """The URL isn't a public http or https address. The message is safe to show users."""


def private_urls_allowed() -> bool:
    """Development only: ALLOW_PRIVATE_WEBHOOK_URLS=true lets webhooks reach a receiver on your own machine."""
    return (
        os.getenv("ALLOW_PRIVATE_WEBHOOK_URLS", "").strip().lower() == "true"
        and os.getenv("APP_ENV", "development") != "production"
    )


def ensure_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeURL("Give an http or https address")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, None)}
    except socket.gaierror as exc:
        raise UnsafeURL("That address couldn't be found") from exc
    if private_urls_allowed():
        return
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeURL("The address must be on the public internet")
