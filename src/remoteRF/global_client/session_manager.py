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

"""Orchestrates `remoterf use <slug>`: deployment resolution -> connection
descriptor -> CA fingerprint verification -> secure gRPC channel -> Global
access assertion -> GlobalAuthV1 exchange -> local session storage.

This is the one place that ties every other `global_client` module
together; individual modules (route_resolver, ca_store, channel_factory,
assertion_exchange) stay independently testable and this module's own
logic is the retry/caching *policy*, not any of the mechanics.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import grpc

from .api_client import GlobalApiHttpError
from .assertion_exchange import GlobalAuthExchangeClient, GlobalAuthExchangeRequest, UnavailableGlobalAuthV1Client
from .auth_client import AuthenticatedGlobalClient
from .ca_store import verify_and_store_ca
from .channel_factory import build_deployment_channel
from .errors import (
    AssertionRejectedError,
    DeploymentDisabledError,
    DeploymentNotFoundError,
    DeploymentOfflineError,
    GlobalAuthUnavailableError,
    GlobalClientError,
)
from .local_sessions import LocalDeploymentSession, LocalSessionStore
from .route_resolver import ResolvedRoute, resolve_route
from .schemas import DeploymentSummary
from .state import DeploymentProfile, ca_path, save_deployment_profile

# Bounded: the initial request plus at most one retry with a fresh
# assertion after an ambiguous failure. Never loops indefinitely.
MAX_ASSERTION_ATTEMPTS = 2


@dataclass(frozen=True)
class UseResult:
    deployment: DeploymentSummary
    route: ResolvedRoute
    session: LocalDeploymentSession


class GlobalSessionManager:
    def __init__(
        self,
        *,
        config_root: Path,
        api: AuthenticatedGlobalClient,
        local_sessions: LocalSessionStore,
        exchange_client: Optional[GlobalAuthExchangeClient] = None,
    ):
        self._config_root = config_root
        self._api = api
        self._local_sessions = local_sessions
        self._exchange_client = exchange_client or UnavailableGlobalAuthV1Client()

    # --- deployment + route resolution --------------------------------------

    def _translate_api_error(self, exc: GlobalApiHttpError, *, slug: str) -> GlobalClientError:
        if exc.status_code == 404 or exc.code == "deployment_not_found":
            return DeploymentNotFoundError(f"No such RemoteRF Global deployment: {slug!r}")
        if exc.code == "route_unavailable":
            return DeploymentDisabledError(f"Deployment {slug!r} has no configured route yet.")
        return exc

    def resolve_deployment_route(self, slug: str) -> tuple[DeploymentSummary, ResolvedRoute]:
        try:
            summary = self._api.get_deployment(slug)
        except GlobalApiHttpError as exc:
            raise self._translate_api_error(exc, slug=slug) from exc

        if not summary.online:
            raise DeploymentOfflineError(f"Deployment {slug!r} is currently offline.")

        try:
            descriptor = self._api.get_connection_descriptor(slug)
        except GlobalApiHttpError as exc:
            raise self._translate_api_error(exc, slug=slug) from exc

        route = resolve_route(descriptor)
        return summary, route

    def bootstrap_trust(self, route: ResolvedRoute) -> Path:
        """Fetch, verify, and persist the deployment's CA; persist its
        non-secret route profile. Returns the path to the verified CA."""
        cert_host = route.certificate_host or route.grpc_host
        cert_port = route.certificate_port if route.certificate_port is not None else route.grpc_port
        if route.certificate_host is None and route.certificate_port is None:
            # Descriptor omitted a certificate endpoint entirely -- do not
            # guess grpc_port +/- N; fail rather than bootstrap trust from
            # an unspecified location.
            from .errors import CertificateBootstrapError

            raise CertificateBootstrapError(
                f"Connection descriptor for {route.slug!r} has no certificate_endpoint; "
                "cannot bootstrap this deployment's CA."
            )

        dest = ca_path(self._config_root, route.deployment_id)
        verify_and_store_ca(host=cert_host, port=cert_port, expected_ca_sha256=route.ca_sha256, dest=dest)

        save_deployment_profile(
            self._config_root,
            DeploymentProfile(
                deployment_id=route.deployment_id,
                slug=route.slug,
                display_name=route.display_name,
                protocol_version=route.protocol_version,
                route_kind=route.kind,
                grpc_endpoint=f"{route.grpc_host}:{route.grpc_port}",
                certificate_endpoint=(f"{cert_host}:{cert_port}" if route.certificate_host else None),
                tls_server_name=route.tls_server_name,
                ca_sha256=route.ca_sha256,
                descriptor_issued_at=route.issued_at.isoformat(),
                descriptor_expires_at=route.expires_at.isoformat(),
            ),
        )
        return dest

    def open_channel(self, route: ResolvedRoute, ca_pem_path: Path) -> grpc.Channel:
        return build_deployment_channel(route, ca_pem_path.read_bytes())

    # --- assertion exchange with bounded, discard-on-ambiguous-failure retry ---

    def _exchange(self, route: ResolvedRoute, channel: grpc.Channel) -> LocalDeploymentSession:
        last_exc: Optional[GlobalClientError] = None
        for _attempt in range(MAX_ASSERTION_ATTEMPTS):
            assertion_resp = self._api.request_access_assertion(route.slug)
            request = GlobalAuthExchangeRequest(
                deployment_id=route.deployment_id,
                assertion=assertion_resp.assertion,
                client_request_id=str(uuid.uuid4()),
                protocol_version=route.protocol_version,
            )
            try:
                result = self._exchange_client.exchange_assertion(channel, request)
            except GlobalAuthUnavailableError:
                # Not ambiguous, not retryable: the deployment does not
                # speak the protocol at all.
                raise
            except AssertionRejectedError:
                # Owner denial is authoritative; do not retry.
                raise
            except GlobalClientError as exc:
                # Ambiguous transport-level failure (timeout, dropped
                # connection, UNAVAILABLE after the RPC may have been
                # processed): the assertion is discarded -- we never reuse
                # it -- and the next loop iteration requests a fresh one.
                last_exc = exc
                continue

            # The assertion itself is never persisted or logged, win or lose.
            return LocalDeploymentSession(
                deployment_id=route.deployment_id,
                tls_server_name=route.tls_server_name,
                session_material=result.session_material,
                obtained_at=datetime.now(timezone.utc),
                expires_at=result.expires_at,
            )

        assert last_exc is not None
        raise last_exc

    # --- top-level orchestration --------------------------------------------

    def use_deployment(self, slug: str, *, force_reauth: bool = False) -> UseResult:
        summary, route = self.resolve_deployment_route(slug)
        ca_pem_path = self.bootstrap_trust(route)

        cached = self._local_sessions.load(route.deployment_id)
        if (
            not force_reauth
            and cached is not None
            and not cached.is_expired()
            and cached.tls_server_name == route.tls_server_name
        ):
            return UseResult(deployment=summary, route=route, session=cached)

        channel = self.open_channel(route, ca_pem_path)
        try:
            session = self._exchange(route, channel)
        finally:
            channel.close()

        self._local_sessions.save(session)
        return UseResult(deployment=summary, route=route, session=session)
