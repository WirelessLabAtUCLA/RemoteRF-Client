import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from remoteRF.global_client.assertion_exchange import ExchangeResult, GlobalAuthExchangeRequest
from remoteRF.global_client.credentials import FileSecretStore
from remoteRF.global_client.errors import (
    AssertionRejectedError,
    DeploymentNotFoundError,
    DeploymentOfflineError,
    GlobalAuthUnavailableError,
    GlobalClientError,
)
from remoteRF.global_client.local_sessions import LocalDeploymentSession, LocalSessionStore
from remoteRF.global_client.schemas import AccessAssertionResponse, ConnectionDescriptor, DeploymentSummary, RouteDescriptor
from remoteRF.global_client.session_manager import GlobalSessionManager


def _summary(*, slug="ucla", online=True) -> DeploymentSummary:
    return DeploymentSummary(
        id="550e8400-e29b-41d4-a716-446655440000", slug=slug, display_name="UCLA WirelessLab",
        description=None, online=online, protocol_version="1", resource_count=1,
    )


def _descriptor(*, expires_in=300, certificate_endpoint="ucla.global.remoterf.net:61006") -> ConnectionDescriptor:
    now = datetime.now(timezone.utc)
    return ConnectionDescriptor(
        deployment_id="550e8400-e29b-41d4-a716-446655440000", slug="ucla", display_name="UCLA WirelessLab",
        protocol_version="1",
        route=RouteDescriptor(
            kind="tcp-relay", grpc_endpoint="ucla.global.remoterf.net:61005",
            certificate_endpoint=certificate_endpoint, tls_server_name="ucla.global.remoterf.net",
            ca_sha256="AA:" * 31 + "BB",
        ),
        issued_at=now.isoformat(), expires_at=(now + timedelta(seconds=expires_in)).isoformat(),
    )


def _assertion() -> AccessAssertionResponse:
    now = datetime.now(timezone.utc)
    return AccessAssertionResponse(
        assertion="header.payload.sig", deployment_id="550e8400-e29b-41d4-a716-446655440000",
        issued_at=now.isoformat(), expires_at=(now + timedelta(seconds=120)).isoformat(),
    )


class FakeAuthClient:
    def __init__(self, *, summary=None, descriptor=None, get_deployment_error=None, get_descriptor_error=None):
        self._summary = summary or _summary()
        self._descriptor = descriptor or _descriptor()
        self._get_deployment_error = get_deployment_error
        self._get_descriptor_error = get_descriptor_error
        self.assertion_requests = 0

    def get_deployment(self, slug):
        if self._get_deployment_error:
            raise self._get_deployment_error
        return self._summary

    def get_connection_descriptor(self, slug):
        if self._get_descriptor_error:
            raise self._get_descriptor_error
        return self._descriptor

    def request_access_assertion(self, slug):
        self.assertion_requests += 1
        return _assertion()


class FixedResultExchangeClient:
    def __init__(self, result: ExchangeResult):
        self._result = result
        self.calls = 0

    def exchange_assertion(self, channel, request: GlobalAuthExchangeRequest) -> ExchangeResult:
        self.calls += 1
        return self._result


class ScriptedExchangeClient:
    """Raises/returns a scripted sequence, one entry per call."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0
        self.request_ids = []

    def exchange_assertion(self, channel, request: GlobalAuthExchangeRequest):
        self.request_ids.append(request.client_request_id)
        self.calls += 1
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _manager(auth_client, exchange_client, config_root) -> GlobalSessionManager:
    store = FileSecretStore(config_root / "secrets")
    return GlobalSessionManager(
        config_root=config_root, api=auth_client, local_sessions=LocalSessionStore(store), exchange_client=exchange_client
    )


class DeploymentResolutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_root = Path(self._tmp.name)

    def test_offline_deployment_raises_before_any_network_bootstrap(self):
        auth = FakeAuthClient(summary=_summary(online=False))
        manager = _manager(auth, FixedResultExchangeClient(ExchangeResult(session_material={})), self.config_root)
        with self.assertRaises(DeploymentOfflineError):
            manager.use_deployment("ucla")

    def test_route_unavailable_error_code_translated(self):
        from remoteRF.global_client.api_client import GlobalApiHttpError

        err = GlobalApiHttpError(status_code=409, code="route_unavailable", message="no route yet")
        auth = FakeAuthClient(get_descriptor_error=err)
        manager = _manager(auth, FixedResultExchangeClient(ExchangeResult(session_material={})), self.config_root)
        with self.assertRaises(GlobalClientError) as ctx:
            manager.use_deployment("ucla")
        self.assertNotIsInstance(ctx.exception, DeploymentNotFoundError)

    def test_404_translated_to_deployment_not_found(self):
        from remoteRF.global_client.api_client import GlobalApiHttpError

        err = GlobalApiHttpError(status_code=404, code="deployment_not_found", message="nope")
        auth = FakeAuthClient(get_deployment_error=err)
        manager = _manager(auth, FixedResultExchangeClient(ExchangeResult(session_material={})), self.config_root)
        with self.assertRaises(DeploymentNotFoundError):
            manager.use_deployment("nope")


class TrustBootstrapTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_root = Path(self._tmp.name)

    def test_bootstrap_trust_verifies_ca_and_persists_profile(self):
        auth = FakeAuthClient()
        manager = _manager(auth, FixedResultExchangeClient(ExchangeResult(session_material={})), self.config_root)

        with mock.patch(
            "remoteRF.global_client.session_manager.verify_and_store_ca"
        ) as verify_mock:
            _, route = manager.resolve_deployment_route("ucla")
            manager.bootstrap_trust(route)

        verify_mock.assert_called_once()
        kwargs = verify_mock.call_args.kwargs
        self.assertEqual(kwargs["host"], "ucla.global.remoterf.net")
        self.assertEqual(kwargs["port"], 61006)
        self.assertEqual(kwargs["expected_ca_sha256"], "AA:" * 31 + "BB")

        from remoteRF.global_client.state import load_deployment_profile

        profile = load_deployment_profile(self.config_root, route.deployment_id)
        self.assertEqual(profile.slug, "ucla")
        self.assertEqual(profile.tls_server_name, "ucla.global.remoterf.net")

    def test_missing_certificate_endpoint_fails_without_guessing_a_port(self):
        from remoteRF.global_client.errors import CertificateBootstrapError

        auth = FakeAuthClient(descriptor=_descriptor(certificate_endpoint=None))
        manager = _manager(auth, FixedResultExchangeClient(ExchangeResult(session_material={})), self.config_root)
        _, route = manager.resolve_deployment_route("ucla")
        with self.assertRaises(CertificateBootstrapError):
            manager.bootstrap_trust(route)


class AssertionExchangeRetryPolicyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_root = Path(self._tmp.name)
        self._patchers = [
            mock.patch("remoteRF.global_client.session_manager.verify_and_store_ca"),
            mock.patch("remoteRF.global_client.session_manager.save_deployment_profile"),
            mock.patch.object(GlobalSessionManager, "open_channel", return_value=mock.MagicMock(close=mock.MagicMock())),
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_unavailable_error_is_not_retried(self):
        from remoteRF.global_client.assertion_exchange import UnavailableGlobalAuthV1Client

        auth = FakeAuthClient()
        exchange = UnavailableGlobalAuthV1Client()
        manager = _manager(auth, exchange, self.config_root)

        with self.assertRaises(GlobalAuthUnavailableError):
            manager.use_deployment("ucla")

        # Exactly one assertion was requested for the one attempt made.
        self.assertEqual(auth.assertion_requests, 1)

    def test_assertion_rejected_is_not_retried(self):
        script = [AssertionRejectedError("denied by owner policy")]
        auth = FakeAuthClient()
        exchange = ScriptedExchangeClient(script)
        manager = _manager(auth, exchange, self.config_root)

        with self.assertRaises(AssertionRejectedError):
            manager.use_deployment("ucla")

        self.assertEqual(exchange.calls, 1)
        self.assertEqual(auth.assertion_requests, 1)

    def test_ambiguous_failure_retries_once_with_a_fresh_assertion(self):
        script = [
            GlobalClientError("connection dropped after transmission -- ambiguous"),
            ExchangeResult(session_material={"local_token": "opaque"}),
        ]
        auth = FakeAuthClient()
        exchange = ScriptedExchangeClient(script)
        manager = _manager(auth, exchange, self.config_root)

        result = manager.use_deployment("ucla")

        self.assertEqual(exchange.calls, 2)
        self.assertEqual(auth.assertion_requests, 2)
        # A fresh client_request_id was used on the retry, not the same one.
        self.assertEqual(len(set(exchange.request_ids)), 2)
        self.assertEqual(result.session.session_material, {"local_token": "opaque"})

    def test_ambiguous_failure_is_bounded_to_one_retry_not_infinite(self):
        script = [
            GlobalClientError("ambiguous failure 1"),
            GlobalClientError("ambiguous failure 2"),
        ]
        auth = FakeAuthClient()
        exchange = ScriptedExchangeClient(script)
        manager = _manager(auth, exchange, self.config_root)

        with self.assertRaises(GlobalClientError):
            manager.use_deployment("ucla")

        self.assertEqual(exchange.calls, 2)  # never a third attempt

    def test_successful_exchange_persists_local_session(self):
        result = ExchangeResult(session_material={"local_token": "opaque-session"})
        auth = FakeAuthClient()
        exchange = FixedResultExchangeClient(result)
        manager = _manager(auth, exchange, self.config_root)

        use_result = manager.use_deployment("ucla")

        loaded = manager._local_sessions.load(use_result.route.deployment_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.session_material, {"local_token": "opaque-session"})
        self.assertEqual(loaded.tls_server_name, "ucla.global.remoterf.net")

    def test_valid_cached_session_is_reused_without_a_new_assertion(self):
        auth = FakeAuthClient()
        exchange = FixedResultExchangeClient(ExchangeResult(session_material={"a": 1}))
        manager = _manager(auth, exchange, self.config_root)

        first = manager.use_deployment("ucla")
        second = manager.use_deployment("ucla")

        self.assertEqual(auth.assertion_requests, 1)  # not called again on reuse
        self.assertEqual(exchange.calls, 1)
        self.assertEqual(first.session.session_material, second.session.session_material)

    def test_force_reauth_bypasses_the_cache(self):
        auth = FakeAuthClient()
        exchange = FixedResultExchangeClient(ExchangeResult(session_material={"a": 1}))
        manager = _manager(auth, exchange, self.config_root)

        manager.use_deployment("ucla")
        manager.use_deployment("ucla", force_reauth=True)

        self.assertEqual(auth.assertion_requests, 2)
        self.assertEqual(exchange.calls, 2)

    def test_expired_cached_session_is_not_reused(self):
        auth = FakeAuthClient()
        exchange = FixedResultExchangeClient(ExchangeResult(session_material={"a": 1}))
        manager = _manager(auth, exchange, self.config_root)

        expired = LocalDeploymentSession(
            deployment_id="550e8400-e29b-41d4-a716-446655440000",
            tls_server_name="ucla.global.remoterf.net",
            session_material={"stale": True},
            obtained_at=datetime.now(timezone.utc) - timedelta(hours=2),
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        manager._local_sessions.save(expired)

        manager.use_deployment("ucla")
        self.assertEqual(auth.assertion_requests, 1)  # a fresh exchange happened


if __name__ == "__main__":
    unittest.main()
