"""
Limits on where the server will connect on a caller's behalf.

Connectors fetch URLs, open database connections and spawn MCP servers using
values supplied through the API, so without these checks an API key is enough
to reach cloud metadata endpoints or the internal network, read local files, or
run a command on the host.

Self-hosters who deliberately ingest from their own network can set
SPAIDER_ALLOW_PRIVATE_NET=true. stdio commands are off unless listed by name in
MCP_STDIO_ALLOWED_COMMANDS.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Optional
from urllib.parse import urljoin, urlsplit

import httpx

from app.config import settings

_MAX_REDIRECTS = 5
_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_SQL_DRIVERS = {"postgresql+asyncpg", "mysql+aiomysql"}
# Not all of these are covered by ipaddress's is_private: CGNAT, benchmarking,
# NAT64, and the deprecated IPv6 site-local range.
_EXTRA_BLOCKED = tuple(
    ipaddress.ip_network(net)
    for net in ("0.0.0.0/8", "100.64.0.0/10", "192.0.0.0/24", "198.18.0.0/15", "64:ff9b::/96", "fec0::/10")
)


class BlockedTarget(ValueError):
    """The target is not an acceptable destination. Maps to HTTP 422."""


class PolicyRefused(PermissionError):
    """This deployment does not allow the operation. Maps to HTTP 403."""


class UnresolvableHost(OSError):
    """DNS gave no answer; an ordinary fetch failure rather than a policy issue."""


def is_blocked_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        # An IPv4 address hidden inside IPv6 reaches the same host.
        embedded = ip.ipv4_mapped or ip.sixtofour or ip.teredo and ip.teredo[1]
        if embedded:
            return is_blocked_ip(str(embedded))
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or any(ip in net for net in _EXTRA_BLOCKED if net.version == ip.version)
    )


async def _resolve(host: str) -> list[str]:
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        pass
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnresolvableHost(f"Cannot resolve {host}") from exc
    return sorted({info[4][0].split("%")[0] for info in infos})


async def check_host(host: Optional[str]) -> list[str]:
    """Addresses for ``host``; raises if any of them is off-limits."""
    if not host:
        raise BlockedTarget("Target has no host.")
    addresses = await _resolve(host.strip("[]"))
    if settings.spaider_allow_private_net:
        return addresses
    if not addresses or any(is_blocked_ip(a) for a in addresses):
        raise BlockedTarget("Target resolves to a private or reserved address.")
    return addresses


async def check_url(url: str) -> list[str]:
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise BlockedTarget("Only http and https URLs are allowed.")
    if parts.username or parts.password:
        raise BlockedTarget("URLs must not contain credentials.")
    return await check_host(parts.hostname)


async def check_urls(urls: list[str]) -> None:
    """Reject a batch before any request is sent. DNS failures are left to the fetch."""
    for url in urls:
        try:
            await check_url(url)
        except UnresolvableHost:
            continue


async def guarded_get(
    client: httpx.AsyncClient, url: str, *, headers: Optional[dict[str, str]] = None
) -> httpx.Response:
    """GET with every hop validated.

    The connection goes to the address that was checked, so a DNS answer that
    changes between check and connect cannot redirect it. TLS still verifies
    against the original hostname.
    """
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        addresses = await check_url(current)
        if settings.spaider_allow_private_net:
            response = await client.get(current, headers=headers, follow_redirects=False)
        else:
            target = httpx.URL(current)
            pinned = target.copy_with(host=addresses[0])
            response = await client.get(
                pinned,
                headers={**(headers or {}), "Host": target.netloc.decode("ascii")},
                extensions={"sni_hostname": target.host},
                follow_redirects=False,
            )
        location = response.headers.get("location")
        if not (response.is_redirect and location):
            return response
        current = urljoin(current, location)
    raise BlockedTarget("Too many redirects.")


async def check_image_url(image_url: str) -> None:
    if image_url.startswith("data:"):
        if not image_url.lower().startswith("data:image/"):
            raise BlockedTarget("Only image data URIs are allowed.")
        return
    try:
        await check_url(image_url)
    except UnresolvableHost as exc:
        raise BlockedTarget("Image host cannot be resolved.") from exc


def check_stdio_command(command: str) -> None:
    allowed = {c.strip() for c in settings.mcp_stdio_allowed_commands.split(",") if c.strip()}
    if command not in allowed:
        raise PolicyRefused(
            "Spawning local MCP servers is disabled. An operator can allow a "
            "specific command with MCP_STDIO_ALLOWED_COMMANDS."
        )


async def check_dsn(dsn: str) -> None:
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    try:
        url = make_url(dsn)
    except ArgumentError as exc:
        raise BlockedTarget("Invalid database URL.") from exc
    if settings.spaider_allow_private_net:
        return
    if url.drivername not in _ALLOWED_SQL_DRIVERS:
        raise BlockedTarget("Only postgresql+asyncpg and mysql+aiomysql databases are allowed.")
    # Drivers honour ?host= over the URL's host, which would bypass the check.
    if {"host", "hostaddr", "unix_socket", "service"} & {k.lower() for k in url.query}:
        raise BlockedTarget("The database host must be in the URL itself.")
    try:
        await check_host(url.host)
    except UnresolvableHost as exc:
        raise BlockedTarget("Database host cannot be resolved.") from exc
