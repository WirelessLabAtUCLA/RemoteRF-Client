# Copyright (C) 2026 RemoteRF
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""The canonical deployment-side ``GlobalAuthV1.ExchangeAssertion`` call.

The bindings in :mod:`remoteRF.common.grpc.global_auth_pb2` are synchronized
from RemoteRF-Server's canonical ``global_auth.proto``. This module is the
only client code that redeems a Global assertion; it never persists or logs
the assertion, Global credentials, or the owner-local session token.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import grpc

from ..common.grpc import global_auth_pb2, global_auth_pb2_grpc
from .errors import AssertionRejectedError, GlobalAuthUnavailableError, GrpcConnectionError

DEFAULT_EXCHANGE_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class GlobalAuthExchangeRequest:
    deployment_id: str
    assertion: str
    client_request_id: str
    protocol_version: str


@dataclass(frozen=True)
class ExchangeResult:
    """Typed, deployment-local material returned by the canonical response."""

    local_username: str
    local_session_token: str
    local_session_expiration: datetime
    deployment_id: str

    def __repr__(self) -> str:
        return (
            "ExchangeResult("
            f"local_username={self.local_username!r}, "
            "local_session_token=<redacted>, "
            f"deployment_id={self.deployment_id!r})"
        )


class GlobalAuthExchangeClient(Protocol):
    def exchange_assertion(self, channel: grpc.Channel, request: GlobalAuthExchangeRequest) -> ExchangeResult: ...


def _parse_rfc3339(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AssertionRejectedError("Deployment returned an invalid local-session expiration.") from exc
    if parsed.tzinfo is None:
        raise AssertionRejectedError("Deployment returned a local-session expiration without a timezone.")
    return parsed.astimezone(timezone.utc)


class GrpcGlobalAssertionExchange:
    """Production implementation backed by the generated ``GlobalAuthV1Stub``."""

    def __init__(self, *, timeout_seconds: float = DEFAULT_EXCHANGE_TIMEOUT_SECONDS):
        self._timeout_seconds = timeout_seconds

    def exchange_assertion(self, channel: grpc.Channel, request: GlobalAuthExchangeRequest) -> ExchangeResult:
        stub = global_auth_pb2_grpc.GlobalAuthV1Stub(channel)
        wire_request = global_auth_pb2.ExchangeAssertionRequest(
            assertion=request.assertion,
            client_request_id=request.client_request_id,
            protocol_version=request.protocol_version,
        )
        try:
            response = stub.ExchangeAssertion(wire_request, timeout=self._timeout_seconds)
        except grpc.RpcError as exc:
            # The request may have reached the deployment. The session manager
            # obtains a *new* assertion for its one bounded retry.
            if exc.code() == grpc.StatusCode.UNIMPLEMENTED:
                raise GlobalAuthUnavailableError(
                    "This deployment does not provide RemoteRF Global authentication (GlobalAuthV1)."
                ) from exc
            raise GrpcConnectionError("RemoteRF Global authentication exchange did not complete.") from exc

        if response.error.code:
            message = response.error.message or "The deployment rejected the RemoteRF Global assertion."
            raise AssertionRejectedError(f"GlobalAuthV1 rejected the assertion ({response.error.code}): {message}")

        if not response.local_username or not response.local_session_token or not response.deployment_id:
            raise AssertionRejectedError("Deployment returned an incomplete GlobalAuthV1 response.")
        if response.deployment_id != request.deployment_id:
            raise AssertionRejectedError("Deployment identity in the GlobalAuthV1 response did not match the selected deployment.")

        return ExchangeResult(
            local_username=response.local_username,
            local_session_token=response.local_session_token,
            local_session_expiration=_parse_rfc3339(response.session_expires_at),
            deployment_id=response.deployment_id,
        )


class UnavailableGlobalAuthV1Client:
    """Injectable test adapter for callers that need to model an old server."""

    def exchange_assertion(self, channel: grpc.Channel, request: GlobalAuthExchangeRequest) -> ExchangeResult:
        raise GlobalAuthUnavailableError("This deployment does not provide RemoteRF Global authentication (GlobalAuthV1).")
