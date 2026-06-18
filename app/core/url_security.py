from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from app.core.errors import FetchError

PRIVATE_HOSTNAMES = {"localhost", "localhost.localdomain"}
METADATA_SERVICE_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


@dataclass(frozen=True, slots=True)
class URLSafetyResult:
    url: str
    hostname: str
    port: int | None


def redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<malformed-url>"
    netloc = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        port = None
    if port:
        netloc = f"{netloc}:{port}"
    redacted_query = ""
    if parts.query:
        redacted_query = "redacted=1"
    return urlunsplit((parts.scheme, netloc, parts.path or "/", redacted_query, ""))


def normalize_redirect_url(base_url: str, location: str) -> str:
    return urljoin(base_url, location)


def validate_public_http_url(
    url: str,
    *,
    allow_private_networks: bool = False,
    allow_localhost: bool = False,
    resolve_dns: bool = False,
) -> URLSafetyResult:
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise FetchError("malformed URL", error_code="malformed_url") from exc
    if parts.scheme not in {"http", "https"} or not parts.netloc or not parts.hostname:
        raise FetchError("URL must be absolute HTTP(S)", error_code="malformed_url")
    if parts.username or parts.password:
        raise FetchError("URL userinfo is not allowed", error_code="unsafe_url")
    hostname = unquote(parts.hostname).lower().rstrip(".")
    try:
        port = parts.port
    except ValueError as exc:
        raise FetchError("invalid URL port", error_code="malformed_url") from exc

    _reject_private_hostname(hostname, allow_localhost=allow_localhost)
    literal_ip = _parse_ip_literal(hostname)
    if literal_ip is not None:
        _reject_blocked_ip(
            literal_ip,
            allow_private_networks=allow_private_networks,
            allow_localhost=allow_localhost,
        )
    elif resolve_dns:
        _reject_private_resolved_addresses(
            hostname,
            allow_private_networks=allow_private_networks,
            allow_localhost=allow_localhost,
        )
    return URLSafetyResult(url=url, hostname=hostname, port=port)


def is_public_http_url(
    url: str,
    *,
    allow_private_networks: bool = False,
    allow_localhost: bool = False,
    resolve_dns: bool = False,
) -> bool:
    try:
        validate_public_http_url(
            url,
            allow_private_networks=allow_private_networks,
            allow_localhost=allow_localhost,
            resolve_dns=resolve_dns,
        )
    except FetchError:
        return False
    return True


def _reject_private_hostname(hostname: str, *, allow_localhost: bool) -> None:
    if not allow_localhost and (
        hostname in PRIVATE_HOSTNAMES or hostname.endswith(".localhost")
    ):
        raise FetchError("private or localhost URL is blocked", error_code="unsafe_url")


def _parse_ip_literal(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    cleaned = hostname.strip("[]")
    try:
        return ipaddress.ip_address(cleaned)
    except ValueError:
        return _parse_obscure_ipv4(cleaned)


def _parse_obscure_ipv4(hostname: str) -> ipaddress.IPv4Address | None:
    if ":" in hostname or not hostname:
        return None
    parts = hostname.split(".")
    if len(parts) > 4 or any(part == "" for part in parts):
        return None
    try:
        values = [_parse_ipv4_component(part) for part in parts]
    except ValueError:
        return None
    if len(values) == 1:
        if values[0] > 0xFFFFFFFF:
            return None
        return ipaddress.IPv4Address(values[0])
    if len(values) == 2 and values[0] <= 0xFF and values[1] <= 0xFFFFFF:
        return ipaddress.IPv4Address((values[0] << 24) | values[1])
    if (
        len(values) == 3
        and values[0] <= 0xFF
        and values[1] <= 0xFF
        and values[2] <= 0xFFFF
    ):
        return ipaddress.IPv4Address((values[0] << 24) | (values[1] << 16) | values[2])
    if len(values) == 4 and all(value <= 0xFF for value in values):
        return ipaddress.IPv4Address(
            (values[0] << 24) | (values[1] << 16) | (values[2] << 8) | values[3]
        )
    return None


def _parse_ipv4_component(value: str) -> int:
    if value.lower().startswith("0x"):
        return int(value[2:], 16)
    if len(value) > 1 and value.startswith("0"):
        return int(value[1:] or "0", 8)
    return int(value, 10)


def _reject_blocked_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_private_networks: bool,
    allow_localhost: bool,
) -> None:
    if ip in METADATA_SERVICE_IPS:
        raise FetchError("cloud metadata service URL is blocked", error_code="unsafe_url")
    if ip.is_loopback:
        if allow_localhost:
            return
        raise FetchError(
            "loopback URL is blocked",
            error_code="unsafe_url",
        )
    if ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise FetchError(
            "link-local, multicast, reserved, or unspecified IP URL is blocked",
            error_code="unsafe_url",
        )
    if ip.is_private and not allow_private_networks:
        raise FetchError("private-network URL is blocked", error_code="unsafe_url")


def _reject_private_resolved_addresses(
    hostname: str,
    *,
    allow_private_networks: bool,
    allow_localhost: bool,
) -> None:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError("DNS resolution failed", error_code="dns_resolution_failed") from exc
    addresses = {info[4][0] for info in infos}
    for address in addresses:
        ip = ipaddress.ip_address(address)
        _reject_blocked_ip(
            ip,
            allow_private_networks=allow_private_networks,
            allow_localhost=allow_localhost,
        )
