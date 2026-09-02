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

"""Adapter interface for the deployment-side `GlobalAuthV1.ExchangeAssertion`
gRPC call.

At the time this client was built, no canonical `GlobalAuthV1` protobuf
contract exists anywhere in the workspace:

* `RemoteRF-Server` has no `GlobalAuthV1` service, message, or `.proto`
  file (confirmed by grepping the whole repository).
* `FEDERATION_RESEARCH_BRIEF.md` in that repo is explicitly a *research*
  document ("intended as background for technical design/research, not as
  a claim that federation is already implemented") describing a
  differently-scoped `FederationPeer` protocol, not `GlobalAuthV1`.
* `remoterf-vps-global` mints the *assertion* the deployment would
  eventually verify (`services/assertions.py`), but nothing in that
  repository specifies how a deployment redeems it, because that's server
  (RemoteRF-Server) territory.

Per the instruction not to guess a security-sensitive wire contract, this
module defines a narrow, injectable interface instead of inventing
`GlobalAuthV1` messages. `UnavailableGlobalAuthV1Client` is the default
implementation and always raises `GlobalAuthUnavailableError` -- callers
must not catch that and fall back to a password login. Once RemoteRF-Server
publishes generated `GlobalAuthV1` bindings, a real implementation of
`GlobalAuthExchangeClient` should replace the default without any change to
`session_manager.py`'s orchestration logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Protocol

import grpc

from .errors import GlobalAuthUnavailableError


@dataclass(frozen=True)
class GlobalAuthExchangeRequest:
    deployment_id: str
    assertion: str
    client_request_id: str
    protocol_version: str


@dataclass(frozen=True)
class ExchangeResult:
    """What a deployment hands back after redeeming a Global assertion,
    wrapped opaquely -- this client does not interpret `session_material`;
    it is whatever the (future) GlobalAuthV1 response contains, handed
    straight to `LocalDeploymentSession` for storage."""

    session_material: dict[str, Any]
    expires_at: Optional[datetime] = None


class GlobalAuthExchangeClient(Protocol):
    def exchange_assertion(self, channel: grpc.Channel, request: GlobalAuthExchangeRequest) -> ExchangeResult: ...


class UnavailableGlobalAuthV1Client:
    """Default adapter: no canonical GlobalAuthV1 contract is available yet."""

    def exchange_assertion(self, channel: grpc.Channel, request: GlobalAuthExchangeRequest) -> ExchangeResult:
        raise GlobalAuthUnavailableError(
            "This deployment's RemoteRF Global authentication service "
            "(GlobalAuthV1.ExchangeAssertion) is not available in this client build. "
            "No canonical GlobalAuthV1 protobuf/service was found in RemoteRF-Server "
            "when this client was built. This client will not fall back to a "
            "UCLA/owner password login for a Global-selected deployment; wait for "
            "GlobalAuthV1 support and update remoterf, or connect to this deployment "
            "directly with `remoterf --config --addr <host>:<port>` if you have LAN/"
            "direct access and existing account credentials."
        )
