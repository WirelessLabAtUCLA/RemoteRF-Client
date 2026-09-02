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

"""Build a secure gRPC channel to a resolved RemoteRF Global deployment
route, reusing the exact same TLS channel construction as direct/LAN mode
(`core/secure_channel.py`) instead of a parallel implementation.

`tls_server_name` here is always the identity Global's connection
descriptor says the deployment's certificate carries
(`route.tls_server_name`, e.g. `ucla.global.remoterf.net`) -- it selects
which name gRPC checks the certificate against, it never disables the
check. The channel is never opened directly against a VPS/relay IP; it
dials `route.grpc_endpoint` (a hostname:port, per the v0 client work's
hostname/IPv6 handling) exactly as the client received it from Global.
"""

from __future__ import annotations

import grpc

from ..core.secure_channel import build_secure_channel
from .route_resolver import ResolvedRoute


def build_deployment_channel(route: ResolvedRoute, trusted_ca_pem: bytes) -> grpc.Channel:
    target = f"{route.grpc_host}:{route.grpc_port}"
    return build_secure_channel(target, trusted_ca_pem, tls_server_name=route.tls_server_name)
