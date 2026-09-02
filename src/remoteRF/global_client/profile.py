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

"""Direct vs. Global connection profiles, and which one is "active".

This is intentionally a thin, additive layer on top of two things that
already exist independently:

* direct mode's `~/.config/remoterf-client/.env` (`REMOTERF_ADDR`,
  `REMOTERF_CA_CERT`, optional `REMOTERF_TLS_SERVER_NAME`) -- untouched by
  anything in this module;
* Global mode's `~/.config/remoterf-client/global/state.json`
  (`active_deployment_id`) plus that deployment's verified
  `global/deployments/<uuid>/{profile.json,ca.crt}`.

A config directory with no Global state at all -- i.e. every existing
direct-mode install -- resolves to `DirectConnectionProfile` exactly as
before; there is no migration step and no `mode` field is required in
`.env`. `remoterf use <slug>` sets `active_deployment_id`; `remoterf use
direct` clears it. Neither ever deletes or rewrites the other mode's
files, so switching back and forth is non-destructive in both directions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from dotenv import dotenv_values

from .state import ca_path, load_deployment_profile, load_state


def default_config_root() -> Path:
    return Path.home() / ".config" / "remoterf-client"


@dataclass(frozen=True)
class DirectConnectionProfile:
    grpc_endpoint: str
    tls_server_name: Optional[str]
    ca_path: Path
    mode: str = "direct"


@dataclass(frozen=True)
class GlobalConnectionProfile:
    deployment_id: str
    deployment_slug: str
    display_name: str
    grpc_endpoint: str
    tls_server_name: str
    ca_path: Path
    mode: str = "global"


ConnectionProfile = Union[DirectConnectionProfile, GlobalConnectionProfile]


def load_direct_profile(config_root: Optional[Path] = None) -> Optional[DirectConnectionProfile]:
    config_root = config_root or default_config_root()
    env_file = config_root / ".env"
    if not env_file.exists():
        return None

    values = dotenv_values(env_file)
    addr = (values.get("REMOTERF_ADDR") or "").strip()
    ca = (values.get("REMOTERF_CA_CERT") or "").strip()
    if not addr or not ca:
        return None

    tls_server_name = (values.get("REMOTERF_TLS_SERVER_NAME") or "").strip() or None
    return DirectConnectionProfile(grpc_endpoint=addr, tls_server_name=tls_server_name, ca_path=Path(ca).expanduser())


def load_global_profile(config_root: Optional[Path] = None) -> Optional[GlobalConnectionProfile]:
    config_root = config_root or default_config_root()
    state = load_state(config_root)
    if not state.active_deployment_id:
        return None

    profile = load_deployment_profile(config_root, state.active_deployment_id)
    if profile is None:
        return None

    ca = ca_path(config_root, state.active_deployment_id)
    if not ca.exists():
        return None

    return GlobalConnectionProfile(
        deployment_id=profile.deployment_id,
        deployment_slug=profile.slug,
        display_name=profile.display_name,
        grpc_endpoint=profile.grpc_endpoint,
        tls_server_name=profile.tls_server_name,
        ca_path=ca,
    )


def resolve_active_profile(config_root: Optional[Path] = None) -> Optional[ConnectionProfile]:
    """An active Global deployment selection (set by `remoterf use <slug>`)
    takes precedence; otherwise fall back to direct `.env`. A config
    directory with neither returns None."""
    config_root = config_root or default_config_root()
    global_profile = load_global_profile(config_root)
    if global_profile is not None:
        return global_profile
    return load_direct_profile(config_root)
