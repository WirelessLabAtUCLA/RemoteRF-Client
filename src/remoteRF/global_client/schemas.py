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

"""Typed models mirroring the RemoteRF Global HTTP API response bodies.

Field names and shapes here are taken directly from the `remoterf-vps-global`
source (`src/remoterf_global/schemas/auth.py`,
`src/remoterf_global/schemas/deployments.py`,
`src/remoterf_global/schemas/resources.py`) -- that repository is the source
of truth for the wire contract, not this file. If the server's schema
changes, update these to match; do not invent divergent field names.

These models intentionally do *not* perform trust decisions (route-kind
support, CA fingerprint format, hostname syntax, expiration). That
belongs to `route_resolver.py`, which has access to the typed error
hierarchy in `errors.py`. A model here failing to parse only means the
server sent a shape we don't recognize.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


# --- /v1/auth/device/* -------------------------------------------------------


class DeviceCodeResponse(_StrictModel):
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


# --- /v1/auth/{login,refresh,device/token} -----------------------------------


class TokenPairResponse(_StrictModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


# --- /v1/me -------------------------------------------------------------------


class MeResponse(_StrictModel):
    id: str
    email: str
    status: str
    email_verified: bool
    created_at: str


# --- /v1/deployments ------------------------------------------------------


class DeploymentSummary(_StrictModel):
    id: str
    slug: str
    display_name: str
    description: str | None
    online: bool
    protocol_version: str
    resource_count: int


class RouteDescriptor(_StrictModel):
    kind: str
    grpc_endpoint: str
    certificate_endpoint: str | None
    tls_server_name: str
    ca_sha256: str


class ConnectionDescriptor(_StrictModel):
    deployment_id: str
    slug: str
    display_name: str
    protocol_version: str
    route: RouteDescriptor
    issued_at: str
    expires_at: str

    def expires_at_dt(self) -> datetime:
        return _parse_rfc3339(self.expires_at)

    def issued_at_dt(self) -> datetime:
        return _parse_rfc3339(self.issued_at)


class AccessAssertionResponse(_StrictModel):
    assertion: str
    deployment_id: str
    issued_at: str
    expires_at: str

    def expires_at_dt(self) -> datetime:
        return _parse_rfc3339(self.expires_at)


class ResourceSummary(_StrictModel):
    id: str
    resource_ref: str
    display_name: str
    device_type: str | None
    capabilities: dict
    policy_summary: dict


# --- shared helpers ------------------------------------------------------------


def _parse_rfc3339(value: str) -> datetime:
    """Parse the ISO-8601 timestamps the server emits via
    `datetime.isoformat()` (see remoterf_global services). Always returns a
    timezone-aware UTC datetime."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
