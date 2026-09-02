# Copyright (C) 2026 RemoteRF
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Validation for RemoteRF Global connection descriptors.

v1.0 supports exactly one route kind: `tcp-relay` (the existing v0 transparent
TCP-forwarding route -- see docs/remoterf-global-v0.md and README.md). Any
other `route.kind` fails clearly rather than being silently reinterpreted.

Endpoint host:port syntax reuses `config._parse_hostport`, the same helper
that already accepts DNS hostnames, IPv4, and bracketed IPv6 literals for
direct/LAN mode (see RemoteRF Global v0 client work) -- there is no
Global-specific address parser.

Deployment identity always comes from the *typed descriptor fields*
(`deployment_id`, `slug`, route endpoints), never inferred from a resolved
IP address, and the descriptor's certificate port is whatever
`route.certificate_endpoint` says -- v1.0 does not assume `grpc_port + 1`
the way v0's CLI convention does, because Global routes are not required to
follow that convention.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..config.config import _parse_hostport
from .ca_store import validate_ca_sha256_syntax
from .errors import (
    DescriptorExpiredError,
    MalformedDescriptorError,
    ProtocolVersionError,
    UnsupportedRouteKindError,
)
from .schemas import ConnectionDescriptor

SUPPORTED_ROUTE_KINDS = frozenset({"tcp-relay"})
SUPPORTED_PROTOCOL_VERSIONS = frozenset({"1"})

# Same hostname grammar the server itself validates tls_server_name against
# (remoterf_global/services/deployments.py: _HOSTNAME_RE) -- kept here
# independently since the client must not trust the server blindly, only
# consistently.
_HOSTNAME_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)

# A validity-freshness margin: reject a descriptor already within this many
# seconds of expiring, rather than racing its expiration mid-use.
EXPIRY_SKEW_SECONDS = 5


@dataclass(frozen=True)
class ResolvedRoute:
    deployment_id: str
    slug: str
    display_name: str
    protocol_version: str
    kind: str
    grpc_host: str
    grpc_port: int
    certificate_host: Optional[str]
    certificate_port: Optional[int]
    tls_server_name: str
    ca_sha256: str
    issued_at: datetime
    expires_at: datetime


def _require_nonempty(value: Optional[str], field: str) -> str:
    if not value or not value.strip():
        raise MalformedDescriptorError(f"Connection descriptor field {field!r} is empty.")
    return value


def _validate_hostname(value: str, *, field: str) -> None:
    if not _HOSTNAME_RE.match(value):
        raise MalformedDescriptorError(f"{field} is not a syntactically valid hostname: {value!r}")


def resolve_route(descriptor: ConnectionDescriptor, *, now: Optional[datetime] = None) -> ResolvedRoute:
    """Validate a `ConnectionDescriptor` end-to-end and return a
    `ResolvedRoute` the rest of the client can act on. Raises a specific
    `GlobalClientError` subclass (see errors.py) on the first problem
    found; never silently reinterprets or downgrades an unsupported field.
    """
    now = now or datetime.now(timezone.utc)

    _require_nonempty(descriptor.deployment_id, "deployment_id")
    _require_nonempty(descriptor.slug, "slug")
    _require_nonempty(descriptor.protocol_version, "protocol_version")

    if descriptor.protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
        raise ProtocolVersionError(
            f"Deployment {descriptor.slug!r} requires RemoteRF Global protocol "
            f"{descriptor.protocol_version}; this client supports "
            f"{', '.join(sorted(SUPPORTED_PROTOCOL_VERSIONS))}."
        )

    route = descriptor.route
    if route.kind not in SUPPORTED_ROUTE_KINDS:
        raise UnsupportedRouteKindError(f"This RemoteRF client does not support route kind: {route.kind}")

    grpc_endpoint = _require_nonempty(route.grpc_endpoint, "route.grpc_endpoint")
    try:
        grpc_host, grpc_port = _parse_hostport(grpc_endpoint)
    except ValueError as exc:
        raise MalformedDescriptorError(f"route.grpc_endpoint is malformed: {grpc_endpoint!r} ({exc})") from exc

    certificate_host: Optional[str] = None
    certificate_port: Optional[int] = None
    if route.certificate_endpoint:
        try:
            certificate_host, certificate_port = _parse_hostport(route.certificate_endpoint)
        except ValueError as exc:
            raise MalformedDescriptorError(
                f"route.certificate_endpoint is malformed: {route.certificate_endpoint!r} ({exc})"
            ) from exc

    tls_server_name = _require_nonempty(route.tls_server_name, "route.tls_server_name")
    _validate_hostname(tls_server_name, field="route.tls_server_name")

    validate_ca_sha256_syntax(_require_nonempty(route.ca_sha256, "route.ca_sha256"))

    try:
        issued_at = descriptor.issued_at_dt()
        expires_at = descriptor.expires_at_dt()
    except ValueError as exc:
        raise MalformedDescriptorError(f"Connection descriptor timestamps are malformed: {exc}") from exc

    if expires_at <= now + timedelta(seconds=EXPIRY_SKEW_SECONDS):
        raise DescriptorExpiredError(
            f"The connection descriptor for {descriptor.slug!r} expired at {expires_at.isoformat()}; "
            "request a fresh one."
        )

    return ResolvedRoute(
        deployment_id=descriptor.deployment_id,
        slug=descriptor.slug,
        display_name=descriptor.display_name,
        protocol_version=descriptor.protocol_version,
        kind=route.kind,
        grpc_host=grpc_host,
        grpc_port=grpc_port,
        certificate_host=certificate_host,
        certificate_port=certificate_port,
        tls_server_name=tls_server_name,
        ca_sha256=route.ca_sha256,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def is_descriptor_fresh(expires_at: datetime, *, now: Optional[datetime] = None) -> bool:
    """True if a cached descriptor is still safely usable (not expired,
    with the same skew margin `resolve_route` itself enforces)."""
    now = now or datetime.now(timezone.utc)
    return expires_at > now + timedelta(seconds=EXPIRY_SKEW_SECONDS)
