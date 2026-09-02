"""End-to-end integration test for the RemoteRF Global v1.0 client pipeline,
using only local fakes/test servers -- never production infrastructure.

This proves the full chain that does NOT depend on a canonical GlobalAuthV1
contract:

    Global login (stored credentials)
        -> deployment discovery (fake Global HTTP API)
        -> connection descriptor (fake Global HTTP API)
        -> CA fetch over real HTTP (local test server) + real DER-SHA256
           fingerprint verification against the descriptor's expected value
        -> real secure TLS gRPC channel construction and readiness against
           a real local TLS gRPC server presenting that exact certificate
        -> Global access-assertion request (fake Global HTTP API)
        -> GlobalAuthV1.ExchangeAssertion

The last step is deliberately NOT faked with an invented wire protocol
(see assertion_exchange.py's module docstring for why: no canonical
GlobalAuthV1 contract exists anywhere in the workspace at the time this
client was built). This test uses the real `UnavailableGlobalAuthV1Client`
adapter and asserts it fails clearly there, proving the client does not
silently fabricate a session or fall back to a password login once every
step it *can* legitimately complete has succeeded.
"""

import hashlib
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

import grpc
import httpx

from remoteRF.common.grpc import grpc_pb2_grpc
from remoteRF.global_client.api_client import GlobalApiClient
from remoteRF.global_client.auth_client import AuthenticatedGlobalClient
from remoteRF.global_client.credentials import FileSecretStore, GlobalCredentials, GlobalCredentialStore
from remoteRF.global_client.errors import GlobalAuthUnavailableError
from remoteRF.global_client.local_sessions import LocalSessionStore
from remoteRF.global_client.session_manager import GlobalSessionManager
from remoteRF.global_client.state import load_deployment_profile

TLS_SERVER_NAME = "test.deployment.invalid"


def _generate_self_signed_cert(tmp_dir: Path) -> tuple[Path, Path, str]:
    key = tmp_dir / "key.pem"
    cert = tmp_dir / "cert.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1",
            "-subj", f"/CN={TLS_SERVER_NAME}",
            "-addext", f"subjectAltName=DNS:{TLS_SERVER_NAME}",
        ],
        check=True, capture_output=True,
    )
    pem_bytes = cert.read_bytes()
    der = ssl.PEM_cert_to_DER_cert(pem_bytes.decode("ascii"))
    digest = hashlib.sha256(der).hexdigest().upper()
    fingerprint = ":".join(digest[i : i + 2] for i in range(0, 64, 2))
    return key, cert, fingerprint


class _CertHttpHandler(BaseHTTPRequestHandler):
    cert_pem: bytes = b""

    def do_GET(self):
        if self.path == "/ca.crt":
            self.send_response(200)
            self.send_header("Content-Type", "application/x-pem-file")
            self.end_headers()
            self.wfile.write(self.cert_pem)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # keep test output quiet


class _EchoGenericRPCServicer(grpc_pb2_grpc.GenericRPCServicer):
    pass  # channel readiness is all this test needs; no RPC is invoked


@unittest.skipUnless(shutil.which("openssl"), "openssl CLI not available")
class GlobalV1EndToEndTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)
        self.config_root = self.tmp_dir / "config-root"

        self.key_path, self.cert_path, self.fingerprint = _generate_self_signed_cert(self.tmp_dir)
        self.cert_pem = self.cert_path.read_bytes()
        self.key_pem = self.key_path.read_bytes()

        # --- local cert-bootstrap HTTP server (mirrors direct mode's
        # unauthenticated ca.crt endpoint) ---
        handler_cls = type("Handler", (_CertHttpHandler,), {"cert_pem": self.cert_pem})
        self.http_server = HTTPServer(("127.0.0.1", 0), handler_cls)
        self.http_port = self.http_server.server_address[1]
        self.http_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        self.http_thread.start()
        self.addCleanup(self.http_server.shutdown)
        self.addCleanup(self.http_server.server_close)

        # --- local TLS gRPC server presenting exactly that certificate ---
        self.grpc_server = grpc.server(__import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor(max_workers=2))
        grpc_pb2_grpc.add_GenericRPCServicer_to_server(_EchoGenericRPCServicer(), self.grpc_server)
        creds = grpc.ssl_server_credentials([(self.key_pem, self.cert_pem)])
        self.grpc_port = self.grpc_server.add_secure_port("127.0.0.1:0", creds)
        self.grpc_server.start()
        self.addCleanup(lambda: self.grpc_server.stop(None))

        # --- Global credentials already present (login is exercised
        # separately in test_global_device_flow.py / test_global_cli.py) ---
        self.secret_store = FileSecretStore(self.config_root / "secrets")
        self.cred_store = GlobalCredentialStore(self.secret_store)
        self.cred_store.save(GlobalCredentials.from_token_pair("global-access", "global-refresh", expires_in=900))

    def _fake_global_handler(self):
        now = datetime.now(timezone.utc)
        deployment_id = "550e8400-e29b-41d4-a716-446655440000"

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/v1/deployments/ucla":
                return httpx.Response(200, json={
                    "id": deployment_id, "slug": "ucla", "display_name": "UCLA WirelessLab",
                    "description": None, "online": True, "protocol_version": "1", "resource_count": 1,
                })
            if path == "/v1/deployments/ucla/connection":
                return httpx.Response(200, json={
                    "deployment_id": deployment_id, "slug": "ucla", "display_name": "UCLA WirelessLab",
                    "protocol_version": "1",
                    "route": {
                        "kind": "tcp-relay",
                        "grpc_endpoint": f"127.0.0.1:{self.grpc_port}",
                        "certificate_endpoint": f"127.0.0.1:{self.http_port}",
                        "tls_server_name": TLS_SERVER_NAME,
                        "ca_sha256": self.fingerprint,
                    },
                    "issued_at": now.isoformat(),
                    "expires_at": (now + timedelta(minutes=5)).isoformat(),
                })
            if path == "/v1/deployments/ucla/access-assertion":
                return httpx.Response(200, json={
                    "assertion": "header.payload.signature",
                    "deployment_id": deployment_id,
                    "issued_at": now.isoformat(),
                    "expires_at": (now + timedelta(seconds=120)).isoformat(),
                })
            return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})

        return handler

    def test_full_pipeline_up_to_the_globalauth_boundary(self):
        api = GlobalApiClient("https://global.example", transport=httpx.MockTransport(self._fake_global_handler()))
        auth = AuthenticatedGlobalClient(api, self.cred_store)
        manager = GlobalSessionManager(
            config_root=self.config_root, api=auth, local_sessions=LocalSessionStore(self.secret_store),
        )

        with self.assertRaises(GlobalAuthUnavailableError):
            manager.use_deployment("ucla")

        api.close()

        # Everything up to the boundary must have actually happened:
        # the CA was fetched over real HTTP, verified against the
        # descriptor's fingerprint, and persisted.
        profile = load_deployment_profile(self.config_root, "550e8400-e29b-41d4-a716-446655440000")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.tls_server_name, TLS_SERVER_NAME)

        from remoteRF.global_client.state import ca_path

        stored_ca = ca_path(self.config_root, "550e8400-e29b-41d4-a716-446655440000")
        self.assertEqual(stored_ca.read_bytes(), self.cert_pem)

        # No local session was persisted (the exchange never succeeded).
        self.assertIsNone(LocalSessionStore(self.secret_store).load("550e8400-e29b-41d4-a716-446655440000"))

    def test_tampered_ca_fingerprint_fails_closed_before_any_grpc_connection(self):
        handler = self._fake_global_handler()

        def tampered_handler(request: httpx.Request) -> httpx.Response:
            resp = handler(request)
            if request.url.path == "/v1/deployments/ucla/connection":
                import json as _json

                body = _json.loads(resp.content)
                body["route"]["ca_sha256"] = "AA:" * 31 + "BB"  # wrong fingerprint
                return httpx.Response(200, json=body)
            return resp

        api = GlobalApiClient("https://global.example", transport=httpx.MockTransport(tampered_handler))
        auth = AuthenticatedGlobalClient(api, self.cred_store)
        manager = GlobalSessionManager(
            config_root=self.config_root, api=auth, local_sessions=LocalSessionStore(self.secret_store),
        )

        from remoteRF.global_client.errors import CaFingerprintMismatchError

        with self.assertRaises(CaFingerprintMismatchError):
            manager.use_deployment("ucla")
        api.close()

        # Nothing should have been persisted for this deployment.
        from remoteRF.global_client.state import ca_path

        stored_ca = ca_path(self.config_root, "550e8400-e29b-41d4-a716-446655440000")
        self.assertFalse(stored_ca.exists())


if __name__ == "__main__":
    unittest.main()
