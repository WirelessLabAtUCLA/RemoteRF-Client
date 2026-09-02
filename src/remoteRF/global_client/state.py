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

"""Non-secret RemoteRF Global client state.

Lives at ``~/.config/remoterf-client/global/state.json``. Contains only
information that is safe to read in plaintext: the configured Global base
URL, the signed-in user's UUID/email, which deployment (if any) is
currently active, and cache timestamps. Access/refresh tokens, deployment
assertions, and owner-local session tokens never appear here -- see
`credentials.py` and `local_sessions.py`.

Corrupt or partially written state is handled gracefully: `load_state`
returns a fresh default rather than raising, so a crashed write never
bricks the CLI.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

STATE_SCHEMA_VERSION = 1

DEFAULT_GLOBAL_BASE_URL = "https://global.remoterf.net"


@dataclass(frozen=True)
class GlobalState:
    schema_version: int
    global_base_url: str
    credential_store_mode: Optional[str] = None
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    active_deployment_id: Optional[str] = None
    active_deployment_slug: Optional[str] = None
    active_deployment_display_name: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "global_base_url": self.global_base_url,
            "credential_store_mode": self.credential_store_mode,
            "user_id": self.user_id,
            "user_email": self.user_email,
            "active_deployment_id": self.active_deployment_id,
            "active_deployment_slug": self.active_deployment_slug,
            "active_deployment_display_name": self.active_deployment_display_name,
        }

    def with_(self, **changes) -> "GlobalState":
        return replace(self, **changes)

    def cleared_active_deployment(self) -> "GlobalState":
        return self.with_(
            active_deployment_id=None,
            active_deployment_slug=None,
            active_deployment_display_name=None,
        )

    def cleared_user(self) -> "GlobalState":
        return self.with_(user_id=None, user_email=None).cleared_active_deployment()


def default_state(*, global_base_url: str = DEFAULT_GLOBAL_BASE_URL) -> GlobalState:
    return GlobalState(schema_version=STATE_SCHEMA_VERSION, global_base_url=global_base_url)


def state_path(config_root: Path) -> Path:
    return config_root / "global" / "state.json"


def load_state(config_root: Path, *, global_base_url: str = DEFAULT_GLOBAL_BASE_URL) -> GlobalState:
    path = state_path(config_root)
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return default_state(global_base_url=global_base_url)

    try:
        data = json.loads(raw)
    except ValueError:
        return default_state(global_base_url=global_base_url)

    if not isinstance(data, dict) or "schema_version" not in data:
        return default_state(global_base_url=global_base_url)

    try:
        return GlobalState(
            schema_version=int(data.get("schema_version", STATE_SCHEMA_VERSION)),
            global_base_url=data.get("global_base_url") or global_base_url,
            credential_store_mode=data.get("credential_store_mode"),
            user_id=data.get("user_id"),
            user_email=data.get("user_email"),
            active_deployment_id=data.get("active_deployment_id"),
            active_deployment_slug=data.get("active_deployment_slug"),
            active_deployment_display_name=data.get("active_deployment_display_name"),
        )
    except (TypeError, ValueError):
        return default_state(global_base_url=global_base_url)


def save_state(config_root: Path, state: GlobalState) -> None:
    path = state_path(config_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, stat.S_IRWXU)  # 0700

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-state-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2)
        os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# --- per-deployment non-secret profile (route/CA metadata) ------------------
#
# Stored at global/deployments/<uuid>/profile.json alongside that
# deployment's verified ca.crt (see ca_store.py). Non-secret: route
# endpoints, TLS server name, and CA fingerprint are all things the client
# already received over an authenticated Global HTTPS response.


@dataclass(frozen=True)
class DeploymentProfile:
    deployment_id: str
    slug: str
    display_name: str
    protocol_version: str
    route_kind: str
    grpc_endpoint: str
    certificate_endpoint: Optional[str]
    tls_server_name: str
    ca_sha256: str
    descriptor_issued_at: str
    descriptor_expires_at: str

    def to_dict(self) -> dict:
        return {
            "deployment_id": self.deployment_id,
            "slug": self.slug,
            "display_name": self.display_name,
            "protocol_version": self.protocol_version,
            "route_kind": self.route_kind,
            "grpc_endpoint": self.grpc_endpoint,
            "certificate_endpoint": self.certificate_endpoint,
            "tls_server_name": self.tls_server_name,
            "ca_sha256": self.ca_sha256,
            "descriptor_issued_at": self.descriptor_issued_at,
            "descriptor_expires_at": self.descriptor_expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DeploymentProfile":
        return cls(
            deployment_id=data["deployment_id"],
            slug=data["slug"],
            display_name=data["display_name"],
            protocol_version=data["protocol_version"],
            route_kind=data["route_kind"],
            grpc_endpoint=data["grpc_endpoint"],
            certificate_endpoint=data.get("certificate_endpoint"),
            tls_server_name=data["tls_server_name"],
            ca_sha256=data["ca_sha256"],
            descriptor_issued_at=data["descriptor_issued_at"],
            descriptor_expires_at=data["descriptor_expires_at"],
        )


def deployment_dir(config_root: Path, deployment_id: str) -> Path:
    return config_root / "global" / "deployments" / deployment_id


def ca_path(config_root: Path, deployment_id: str) -> Path:
    return deployment_dir(config_root, deployment_id) / "ca.crt"


def profile_path(config_root: Path, deployment_id: str) -> Path:
    return deployment_dir(config_root, deployment_id) / "profile.json"


def load_deployment_profile(config_root: Path, deployment_id: str) -> Optional[DeploymentProfile]:
    path = profile_path(config_root, deployment_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    try:
        data = json.loads(raw)
        return DeploymentProfile.from_dict(data)
    except (ValueError, KeyError):
        return None


def save_deployment_profile(config_root: Path, profile: DeploymentProfile) -> None:
    directory = deployment_dir(config_root, profile.deployment_id)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, stat.S_IRWXU)  # 0700

    path = profile_path(config_root, profile.deployment_id)
    fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".tmp-profile-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(profile.to_dict(), f, indent=2)
        os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
