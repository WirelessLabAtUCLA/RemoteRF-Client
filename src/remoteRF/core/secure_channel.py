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

"""Shared secure gRPC channel construction.

Extracted from `grpc_client.py`'s module-level setup so that RemoteRF
Global (`global_client/channel_factory.py`) can build a channel to a
selected deployment using the exact same TLS behavior as direct/LAN mode,
without duplicating it. Direct mode's behavior is unchanged by this
refactor: `grpc_client.py` now calls this function instead of inlining the
same four lines.

TLS verification is never disabled here. `tls_server_name`, when given,
only selects *which* identity gRPC must find in the presented certificate
(`grpc.ssl_target_name_override` / `grpc.default_authority`) -- e.g. for a
Tailscale address that differs from the certificate's subject name. It does
not skip or weaken the check itself.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

import grpc

DEFAULT_OPTIONS: Tuple[Tuple[str, int], ...] = (
    ("grpc.max_send_message_length", 100 * 1024 * 1024),
    ("grpc.max_receive_message_length", 100 * 1024 * 1024),
)


def build_secure_channel(
    target: str,
    trusted_certs_pem: bytes,
    *,
    tls_server_name: Optional[str] = None,
    extra_options: Optional[Sequence[Tuple[str, object]]] = None,
) -> grpc.Channel:
    options = list(DEFAULT_OPTIONS)
    if extra_options:
        options.extend(extra_options)

    tls_server_name = (tls_server_name or "").strip()
    if tls_server_name:
        options.extend(
            [
                ("grpc.ssl_target_name_override", tls_server_name),
                ("grpc.default_authority", tls_server_name),
            ]
        )

    credentials = grpc.ssl_channel_credentials(root_certificates=trusted_certs_pem)
    return grpc.secure_channel(target, credentials, options=options)
