"""Repeatable three-component RemoteRF Global v1 local integration.

This deliberately uses temporary state, real FastAPI/HTTP, real TLS gRPC,
real Ed25519 signing/JWKS verification, the actual Server connector, and the
canonical Client exchange adapter. It never reads normal user configuration,
keyrings, or production endpoints.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import threading
import time
from concurrent import futures
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import grpc
import pytest
import requests
import uvicorn
from alembic import command
from alembic.config import Config

from remoteRF.common.grpc import global_auth_pb2_grpc, grpc_pb2_grpc
from remoteRF.common.utils import map_arg, unmap_arg
from remoteRF.global_client.api_client import GlobalApiClient
from remoteRF.global_client.assertion_exchange import GlobalAuthExchangeRequest, GrpcGlobalAssertionExchange
from remoteRF.global_client.auth_client import AuthenticatedGlobalClient
from remoteRF.global_client.channel_factory import build_deployment_channel
from remoteRF.global_client.credentials import FileSecretStore, GlobalCredentialStore, GlobalCredentials
from remoteRF.global_client.errors import AssertionRejectedError, GlobalUnavailableError
from remoteRF.global_client.local_sessions import GlobalDeploymentSession, LocalSessionStore
from remoteRF.global_client.session_manager import GlobalSessionManager
from remoteRF.global_client.state import default_state, save_state
from remoteRF_server.server import grpc_server
from remoteRF_server.server import device_manager
from remoteRF_server.server.acc_perms import perms_db
from remoteRF_server.server.global_access import identity
from remoteRF_server.server.global_access.api_client import GlobalApiClient as ServerGlobalApiClient
from remoteRF_server.server.global_access.config import parse_global_config
from remoteRF_server.server.global_access.connector import GlobalConnector
from remoteRF_server.server.global_access.db import GlobalDB
from remoteRF_server.server.reservation import ReservationHandler
from remoteRF_server.server.user_group_handler import UserGroupHandler
from remoterf_global.config import get_settings
from remoterf_global.db.session import get_sessionmaker
from remoterf_global.main import create_app
from remoterf_global.security import signing
from remoterf_global.services import users as users_service
from remoterf_global.services.deployments import create_enrollment_token

VPS_ROOT = Path(__file__).resolve().parents[3] / "remoterf-vps-global"
PUBLIC_GROUP = "global-public"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _CertHandler(BaseHTTPRequestHandler):
    pem = b""

    def do_GET(self):  # noqa: N802
        if self.path == "/ca.crt":
            self.send_response(200)
            self.send_header("Content-Type", "application/x-pem-file")
            self.end_headers()
            self.wfile.write(self.pem)
            return
        self.send_error(404)

    def log_message(self, _format, *args):
        pass


class _FakeDevice:
    """Minimal local Server device used only inside the temporary harness."""

    device_type = "fake"

    def get_idl_json(self) -> str:
        return "{}"

    def dispatch(self, method: str, _args: dict):
        if method == "get_metadata":
            return "safe fake-device metadata"
        raise AssertionError(f"unexpected fake-device operation: {method}")


class _UvicornThread:
    def __init__(self, app, port: int):
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if requests.get(f"http://127.0.0.1:{self.server.config.port}/health", timeout=0.2).ok:
                    return
            except requests.RequestException:
                time.sleep(0.05)
        raise RuntimeError("local Global service did not start")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)


def test_three_component_global_v1_core_flow():
    """Proves Global -> connector -> TLS GlobalAuth -> owner-local session."""
    with TemporaryDirectory() as raw_tmp, contextlib.ExitStack() as stack:
        # Dynamic-driver tests deliberately install a fake grpc_client during
        # collection. Use the production modules for this real-wire test, then
        # restore that isolated fixture exactly as it was for those unit tests.
        import importlib
        import sys

        module_names = ("remoteRF.core.grpc_client", "remoteRF.core.grpc_acc")
        previous_modules = {name: sys.modules.get(name) for name in module_names}
        if not hasattr(previous_modules["remoteRF.core.grpc_client"], "close_active_connection"):
            for name in module_names:
                sys.modules.pop(name, None)
            grpc_client_module = importlib.import_module("remoteRF.core.grpc_client")
            grpc_acc_module = importlib.import_module("remoteRF.core.grpc_acc")

            def restore_test_modules() -> None:
                for name, module in previous_modules.items():
                    if module is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = module

            stack.callback(restore_test_modules)
        else:
            grpc_client_module = previous_modules["remoteRF.core.grpc_client"]
            grpc_acc_module = previous_modules["remoteRF.core.grpc_acc"]

        RemoteRFAccount = grpc_acc_module.RemoteRFAccount
        close_active_connection = grpc_client_module.close_active_connection
        rpc_client = grpc_client_module.rpc_client

        # Restore a cache-free settings module after the temporary environment
        # is removed so later tests never retain this test's data directory.
        stack.callback(get_settings.cache_clear)
        tmp = Path(raw_tmp)
        global_data = tmp / "global-data"
        server_data = tmp / "server-data"
        cert_dir = tmp / "certs"
        client_root = tmp / "home" / ".config" / "remoterf-client"
        global_port, grpc_port, cert_port = _free_port(), _free_port(), _free_port()
        base_url = f"http://127.0.0.1:{global_port}"

        stack.enter_context(mock.patch.dict(os.environ, {
            "REMOTERF_GLOBAL_ENV": "development",
            "REMOTERF_GLOBAL_DATA_DIR": str(global_data),
            "REMOTERF_GLOBAL_PUBLIC_BASE_URL": base_url,
            "REMOTERF_GLOBAL_TRUSTED_HOSTS": "127.0.0.1,localhost,testserver",
            "REMOTERF_GLOBAL_EMAIL_MODE": "console",
            "REMOTERF_GLOBAL_COOKIE_SECURE": "false",
            "REMOTERF_GLOBAL_ENABLED": "true",
            # The Client's normal RPC path resolves the selected profile via
            # Path.home(); keep that entire path inside the test sandbox too.
            "HOME": str(tmp / "home"),
        }, clear=False))
        global_data.mkdir()
        get_settings.cache_clear()
        cfg = Config(str(VPS_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(VPS_ROOT / "src" / "remoterf_global" / "db" / "migrations"))
        command.upgrade(cfg, "head")
        settings = get_settings()
        signing.generate_keypair(settings.resolved_signing_private_key_path, settings.resolved_signing_public_key_path)

        global_service = _UvicornThread(create_app(), global_port)
        global_service.start()
        stack.callback(global_service.stop)

        # Console mail deliberately redacts raw verification links. The test
        # obtains the one-time token through the service layer, then exercises
        # the public verification route with it.
        db = get_sessionmaker()()
        try:
            verification_token = users_service.register_user(
                db, email="integration@example.invalid", password="correct horse battery staple", settings=settings
            )
            _, enrollment_token = create_enrollment_token(db, intended_slug="local-test", intended_display_name="Local Test")
            db.commit()
        finally:
            db.close()
        assert requests.post(f"{base_url}/v1/auth/verify-email", json={"token": verification_token}, timeout=3).status_code == 200

        # Real device-code issuance/activation/polling against FastAPI.
        global_api = GlobalApiClient(base_url, allow_insecure_http=True)
        device_code = global_api.request_device_code()
        browser = requests.Session()
        browser.get(f"{base_url}/login", timeout=3)
        csrf = browser.cookies["remoterf_csrf"]
        assert browser.post(
            f"{base_url}/login", data={"email": "integration@example.invalid", "password": "correct horse battery staple", "csrf_token": csrf, "next": ""},
            allow_redirects=False, timeout=3,
        ).status_code == 303
        browser.get(f"{base_url}/activate", timeout=3)
        assert browser.post(
            f"{base_url}/activate", data={"user_code": device_code.user_code, "action": "approve", "csrf_token": browser.cookies["remoterf_csrf"]}, timeout=3,
        ).status_code == 200
        token_pair = global_api.poll_device_token(device_code.device_code).token_pair
        assert token_pair is not None

        # A real temporary CA/server certificate supports both connector route
        # publication and the Client's TLS verification.
        cert_dir.mkdir()
        server_data.mkdir()
        key_path, cert_path = cert_dir / "server.key", cert_dir / "server.crt"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key_path), "-out", str(cert_path),
            "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
        ], check=True, capture_output=True)
        (cert_dir / "ca.crt").write_bytes(cert_path.read_bytes())
        cert_handler = type("CertHandler", (_CertHandler,), {"pem": cert_path.read_bytes()})
        cert_http = ThreadingHTTPServer(("127.0.0.1", cert_port), cert_handler)
        cert_thread = threading.Thread(target=cert_http.serve_forever, daemon=True)
        cert_thread.start()
        stack.callback(cert_http.server_close)
        stack.callback(cert_http.shutdown)

        server_cfg = parse_global_config({"global": {
            "enabled": True, "base_url": base_url, "issuer": base_url, "jwks_url": f"{base_url}/.well-known/jwks.json",
            "deployment_state_path": str(server_data / "deployment.json"), "deployment_private_key_path": str(server_data / "deployment-private.pem"),
            "heartbeat_seconds": 30, "offline_seconds": 90, "relay_ingress": {"trusted_peer_cidrs": ["10.77.0.1/32"]},
            "public_group": PUBLIC_GROUP,
            "route": {"kind": "tcp-relay", "grpc_endpoint": f"localhost:{grpc_port}", "certificate_endpoint": f"localhost:{cert_port}", "tls_server_name": "localhost"},
            "exports": [{"local_device_id": "1", "display_name": "Fake RX", "device_type": "fake", "visibility": "public",
                         "reservation_policy": {"max_duration_minutes": 10, "max_active_reservations": 1, "max_daily_minutes": 10},
                         "operation_policy": {"default": "deny", "allow_operations": ["metadata:GET"], "allow_client_sample_upload": False}}],
        }}, allow_insecure_transport=True)

        # Bind the legacy process-wide Server DB singletons to temporary files.
        stack.enter_context(mock.patch("remoteRF_server.server.reservation.get_db_dir", return_value=server_data))
        reservation_handler = ReservationHandler()
        stack.enter_context(mock.patch("remoteRF_server.server.reservation.reservation_handler", reservation_handler))
        stack.enter_context(mock.patch.dict(
            device_manager._devices,
            {
                1: device_manager._DeviceState(
                    dev=_FakeDevice(), origin="local", online=True, salt="", hsh="",
                    name="Fake RX", ident="integration-fake", dtype="fake",
                )
            },
            clear=True,
        ))
        stack.enter_context(mock.patch("remoteRF_server.server.acc_perms.get_db_dir", return_value=server_data))
        server_perms = perms_db()
        stack.enter_context(mock.patch("remoteRF_server.server.acc_perms.perms", server_perms))
        stack.enter_context(mock.patch("remoteRF_server.server.reservation.perms", server_perms))
        stack.enter_context(mock.patch("remoteRF_server.server.user_group_handler.get_db_dir", return_value=server_data))
        groups = UserGroupHandler()
        public_group_id = groups.create_user_group(
            group_name=PUBLIC_GROUP, devices_whitelist=[1], max_reservation_time_sec=600,
            max_reservations=1, lifetime_sec=0,
        )
        assert public_group_id is not None
        stack.enter_context(mock.patch("remoteRF_server.server.user_group_handler.user_group_handler", groups))
        stack.enter_context(mock.patch("remoteRF_server.server.reservation.user_group_handler", groups))
        stack.enter_context(mock.patch("remoteRF_server.server.acc_perms.user_group_handler", groups))
        stack.enter_context(mock.patch("remoteRF_server.server.rpc_manager.reservation_handler", reservation_handler))
        server_global_db = GlobalDB(db_dir=server_data)
        stack.enter_context(mock.patch("remoteRF_server.server.global_access.db.get_global_db", return_value=server_global_db))
        stack.enter_context(mock.patch("remoteRF_server.server.global_access.config.load_global_config", return_value=server_cfg))
        # RemoteRFAccount.reserve_device normally attempts optional dynamic
        # driver generation after the authoritative reservation succeeds.
        # That is outside this authentication/authority harness and mutates
        # Python's driver-import cache, so isolate it from later codegen tests.
        stack.enter_context(mock.patch("remoteRF.drivers.dynamic_device.install_driver", return_value=None))
        grpc_server._JWKS_CACHES.clear()

        # A conventional owner-local account remains valid on a non-relay
        # (LAN) peer even while Global is enabled. This account is unrelated
        # to the JIT Global principal created later.
        assert groups.create_enrollment_codes(
            code_names=["local-integration-enrollment"], group_id=public_group_id,
            uses=1, duration_sec=3600,
        ) == "Created 1/1 enrollment codes."
        direct_username, direct_password = "direct-integration", "direct-only-password"
        assert "UC" in reservation_handler.create_user(
            un=map_arg(direct_username), pw=map_arg(direct_password),
            em=map_arg("direct@example.invalid"), ec=map_arg("local-integration-enrollment"),
        )

        identity.init_identity(server_data)
        enrollment = ServerGlobalApiClient(base_url).enroll(
            enrollment_token=enrollment_token, slug="local-test", display_name="Local Test",
            public_key_b64=identity.public_key_b64(server_data), protocol_version=identity.PROTOCOL_VERSION,
        )
        identity.save_state(server_data, identity.DeploymentState(
            deployment_id=enrollment.deployment_id, slug=enrollment.slug, display_name="Local Test",
            protocol_version=enrollment.supported_protocol_version, issuer=enrollment.issuer, api_base_url=enrollment.api_base_url,
        ))
        connector = GlobalConnector(cfg=server_cfg, state_dir=server_data, cert_dir=cert_dir, get_known_device_ids=lambda: {"1"})
        connector.publish_now()

        # Actual Server GlobalAuth service on a real TLS gRPC listener.
        grpc_service = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        global_auth_pb2_grpc.add_GlobalAuthV1Servicer_to_server(grpc_server.GlobalAuthV1Servicer(), grpc_service)
        grpc_pb2_grpc.add_GenericRPCServicer_to_server(grpc_server.GenericRPCServicer(), grpc_service)
        grpc_service.add_secure_port(
            f"127.0.0.1:{grpc_port}", grpc.ssl_server_credentials([(key_path.read_bytes(), cert_path.read_bytes())])
        )
        grpc_service.start()
        stack.callback(lambda: grpc_service.stop(None))

        # Client sees the connector's real catalog, gets a descriptor and
        # verified CA, then exchanges a signed assertion over TLS.
        assert [r.display_name for r in global_api.list_resources("local-test")] == ["Fake RX"]
        store = FileSecretStore(client_root / "secrets")
        credentials = GlobalCredentialStore(store)
        credentials.save(GlobalCredentials.from_token_pair(token_pair.access_token, token_pair.refresh_token, token_pair.expires_in))
        manager = GlobalSessionManager(config_root=client_root, api=AuthenticatedGlobalClient(global_api, credentials), local_sessions=LocalSessionStore(store))
        used = manager.use_deployment("local-test")
        assert used.session.deployment_id == enrollment.deployment_id
        assert reservation_handler.login_user(used.session.local_username, used.session.local_session_token).get("UC") is not None
        assert len(server_global_db.list_mappings()) == 1

        # Select the saved Global deployment, then use the unmodified
        # ordinary Client account path over the same real TLS listener.
        save_state(
            client_root,
            default_state(global_base_url=base_url).with_(
                credential_store_mode="file",
                active_deployment_id=enrollment.deployment_id,
                active_deployment_slug="local-test",
                active_deployment_display_name="Local Test",
            ),
        )
        close_active_connection()
        stack.callback(close_active_connection)
        account = RemoteRFAccount()
        account.use_global_session(used.session, refresher=lambda: (_ for _ in ()).throw(AssertionError("unexpected session refresh")))
        device_response = account.get_devices()
        assert list(device_response.results) == ["1"]
        assert "Fake RX" in device_response.results["1"].string_value
        reservation_start = datetime.now()
        reservation_token = account.reserve_device(
            1,
            reservation_start,
            reservation_start + timedelta(minutes=10),
        )
        assert reservation_token
        reservations = reservation_handler.grab_all_reservations_server()
        assert len(reservations) == 1
        assert next(iter(reservations.values()))[0] == used.session.local_username
        allowed = rpc_client(function_name="fake:metadata:GET", args={"a": map_arg(reservation_token)})
        assert unmap_arg(allowed.results["metadata"]) == "safe fake-device metadata"
        with pytest.raises(RuntimeError, match="not allowed"):
            rpc_client(function_name="fake:tx:SET", args={"a": map_arg(reservation_token), "tx": map_arg(True)})
        with pytest.raises(Exception, match="Invalid device ID"):
            account.reserve_device(2, reservation_start, reservation_start + timedelta(minutes=10))

        # The owner-local session is intentionally independent of Global
        # availability.  Once it expires, a new assertion cannot be obtained
        # during the outage and fails with the typed, non-secret error.
        global_service.stop()
        assert list(account.get_devices().results) == ["1"]
        expired_session = GlobalDeploymentSession(
            **{**used.session.__dict__, "local_session_expiration": datetime.now(timezone.utc) - timedelta(seconds=1)}
        )
        expired_account = RemoteRFAccount()
        expired_account.use_global_session(
            expired_session,
            refresher=lambda: manager.use_deployment("local-test", force_reauth=True).session,
        )
        with pytest.raises(GlobalUnavailableError):
            expired_account.get_devices()

        # Restarting Global uses the same temporary SQLite database and
        # signing identity. A fresh connector publish recovers its heartbeat
        # and catalog without changing the exported resource.
        restarted_global_service = _UvicornThread(create_app(), global_port)
        restarted_global_service.start()
        stack.callback(restarted_global_service.stop)
        connector.publish_now()
        assert [r.display_name for r in global_api.list_resources("local-test")] == ["Fake RX"]

        # A consumed assertion cannot be replayed through the real server.
        assertion = AuthenticatedGlobalClient(global_api, credentials).request_access_assertion("local-test")
        channel = build_deployment_channel(used.route, (client_root / "global" / "deployments" / enrollment.deployment_id / "ca.crt").read_bytes())
        try:
            request = GlobalAuthExchangeRequest(enrollment.deployment_id, assertion.assertion, "replay-test", used.route.protocol_version)
            GrpcGlobalAssertionExchange().exchange_assertion(channel, request)
            with pytest.raises(AssertionRejectedError):
                GrpcGlobalAssertionExchange().exchange_assertion(channel, request)

            # Clearing only the active Global selection returns the Client to
            # its untouched direct-profile mechanism. It authenticates the
            # independent LAN/local account through the ordinary RPC path.
            (client_root / ".env").write_text(
                "\n".join((
                    f"REMOTERF_ADDR=localhost:{grpc_port}",
                    f"REMOTERF_CA_CERT={client_root / 'global' / 'deployments' / enrollment.deployment_id / 'ca.crt'}",
                    "REMOTERF_TLS_SERVER_NAME=localhost",
                    "",
                )),
                encoding="utf-8",
            )
            save_state(client_root, default_state(global_base_url=base_url))
            close_active_connection()
            direct_account = RemoteRFAccount(username=direct_username, password=direct_password)
            assert direct_account.login_user()
            assert list(direct_account.get_devices().results) == ["1"]
        finally:
            channel.close()
            global_api.close()
