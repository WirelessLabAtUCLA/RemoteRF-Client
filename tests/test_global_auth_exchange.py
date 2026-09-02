"""Wire-level tests for the canonical GlobalAuthV1 Client adapter."""

from __future__ import annotations

from concurrent import futures
from datetime import datetime, timedelta, timezone
import unittest

import grpc

from remoteRF.common.grpc import global_auth_pb2, global_auth_pb2_grpc, grpc_pb2
from remoteRF.global_client.assertion_exchange import (
    GlobalAuthExchangeRequest,
    GrpcGlobalAssertionExchange,
)
from remoteRF.global_client.errors import AssertionRejectedError

DEPLOYMENT_ID = "550e8400-e29b-41d4-a716-446655440000"


class _ExchangeServicer(global_auth_pb2_grpc.GlobalAuthV1Servicer):
    def __init__(self, *, error_code: str = "", expiration: str | None = None):
        self.error_code = error_code
        self.expiration = expiration or (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.received = None

    def ExchangeAssertion(self, request, context):
        self.received = request
        if self.error_code:
            return global_auth_pb2.ExchangeAssertionResponse(
                error=grpc_pb2.ErrorEnvelope(code=self.error_code, message="owner policy rejected this request")
            )
        return global_auth_pb2.ExchangeAssertionResponse(
            local_username="global-test-user",
            local_session_token="owner-local-test-session",
            local_account_id=17,
            session_expires_at=self.expiration,
            deployment_id=DEPLOYMENT_ID,
            effective_policy_summary_json='{"public_group":"global-public"}',
            protocol_version="1.0",
        )


class GlobalAuthExchangeTests(unittest.TestCase):
    def setUp(self):
        self.server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        self.servicer = _ExchangeServicer()
        global_auth_pb2_grpc.add_GlobalAuthV1Servicer_to_server(self.servicer, self.server)
        self.port = self.server.add_insecure_port("127.0.0.1:0")
        self.server.start()
        self.addCleanup(lambda: self.server.stop(None))
        self.channel = grpc.insecure_channel(f"127.0.0.1:{self.port}")
        self.addCleanup(self.channel.close)

    def _request(self) -> GlobalAuthExchangeRequest:
        return GlobalAuthExchangeRequest(
            deployment_id=DEPLOYMENT_ID,
            assertion="test-assertion-not-persisted",
            client_request_id="request-123",
            protocol_version="1",
        )

    def test_exchange_uses_canonical_service_and_parses_typed_response(self):
        result = GrpcGlobalAssertionExchange().exchange_assertion(self.channel, self._request())
        self.assertEqual(result.local_username, "global-test-user")
        self.assertEqual(result.deployment_id, DEPLOYMENT_ID)
        self.assertGreater(result.local_session_expiration, datetime.now(timezone.utc))
        self.assertEqual(self.servicer.received.client_request_id, "request-123")
        self.assertEqual(self.servicer.received.protocol_version, "1")

    def test_structured_server_error_is_not_retried_as_a_transport_failure(self):
        self.servicer.error_code = "mapping_disabled"
        with self.assertRaises(AssertionRejectedError) as caught:
            GrpcGlobalAssertionExchange().exchange_assertion(self.channel, self._request())
        self.assertIn("mapping_disabled", str(caught.exception))

    def test_malformed_expiration_is_rejected_without_exposing_session(self):
        self.servicer.expiration = "not-a-timestamp"
        with self.assertRaises(AssertionRejectedError):
            GrpcGlobalAssertionExchange().exchange_assertion(self.channel, self._request())


if __name__ == "__main__":
    unittest.main()
