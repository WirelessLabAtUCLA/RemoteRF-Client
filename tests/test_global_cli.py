import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from remoteRF.global_client import cli as global_cli
from remoteRF.global_client.api_client import GlobalApiClient
from remoteRF.global_client.credentials import GlobalCredentialStore
from remoteRF.global_client.state import default_state, load_state, save_state


def _handler_for(routes):
    """routes: dict of (method, path) -> (status, json_body)"""

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key not in routes:
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "unhandled route in test"}})
        status, body = routes[key]
        return httpx.Response(status, json=body)

    return handler


def _patched_api_client(handler):
    def factory(base_url, *, allow_insecure_http=False, client_name="remoterf-cli", transport=None, timeout=None):
        return GlobalApiClient(
            base_url, allow_insecure_http=allow_insecure_http, client_name=client_name,
            transport=httpx.MockTransport(handler),
        )

    return factory


def _run_capturing(fn, *args, **kwargs):
    """Run a CLI entry point, capturing both plain `print()` output (stdout
    redirection works fine for that) and `printf()` output.

    `printf` goes through prompt_toolkit's `print_formatted_text`, which
    memoizes its output object against whatever `sys.stdout` was at its
    *first* call in the process and does not notice later
    `sys.stdout`/`redirect_stdout` reassignment -- so plain stdout
    redirection alone silently misses `printf` output on every test after
    the first. `remoteRF.remoterf_cli`'s own existing tests work around
    this the same way: by mocking `printf` directly instead of trying to
    capture its rendered terminal output.
    """
    printf_calls: list[tuple] = []

    def fake_printf(*args) -> None:
        printf_calls.append(args)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), mock.patch("remoteRF.global_client.cli.printf", side_effect=fake_printf):
        rc = fn(*args, **kwargs)

    rendered = []
    for call_args in printf_calls:
        for i in range(0, len(call_args), 2):
            rendered.append(str(call_args[i]))
    text = buf.getvalue() + "\n" + "\n".join(rendered)
    return rc, text


class GlobalCliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_root = Path(self._tmp.name)
        self._patchers = [
            mock.patch("remoteRF.global_client.cli.default_config_root", return_value=self.config_root),
            mock.patch("remoteRF.global_client.profile.default_config_root", return_value=self.config_root),
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)

    def _argv(self, *extra):
        # Force the weaker-but-deterministic file credential store so tests
        # never touch the developer's real OS keychain.
        return ["--credential-store", "file", *extra]


class GlobalLoginStatusLogoutTests(GlobalCliTestCase):
    def test_login_status_logout_round_trip(self):
        routes = {
            ("POST", "/v1/auth/device/code"): (200, {
                "device_code": "top-secret-device-code", "user_code": "WXYZ-9999",
                "verification_uri": "https://global.example/activate",
                "verification_uri_complete": "https://global.example/activate?user_code=WXYZ-9999",
                "expires_in": 600, "interval": 0,
            }),
            ("POST", "/v1/auth/device/token"): (200, {
                "access_token": "test-access-token-value", "refresh_token": "test-refresh-token-value",
                "token_type": "bearer", "expires_in": 900,
            }),
            ("GET", "/v1/me"): (200, {
                "id": "u1", "email": "student@ucla.edu", "status": "active",
                "email_verified": True, "created_at": "2026-01-01T00:00:00+00:00",
            }),
            ("POST", "/v1/auth/logout"): (200, {"message": "Logged out."}),
        }
        handler = _handler_for(routes)

        with mock.patch("remoteRF.global_client.cli.GlobalApiClient", side_effect=_patched_api_client(handler)), \
             mock.patch("remoteRF.global_client.device_flow.webbrowser.open", return_value=True), \
             mock.patch("time.sleep"):
            rc, out = _run_capturing(global_cli.cmd_global, [
                "login", "--no-browser", "--global-url", "https://global.example", *self._argv(),
            ])
            self.assertEqual(rc, 0)
            self.assertIn("student@ucla.edu", out)
            self.assertNotIn("test-access-token-value", out)
            self.assertNotIn("test-refresh-token-value", out)
            self.assertNotIn("top-secret-device-code", out)

            rc, out = _run_capturing(global_cli.cmd_global, ["status", "--global-url", "https://global.example", *self._argv()])
            self.assertEqual(rc, 0)
            self.assertIn("student@ucla.edu", out)
            self.assertNotIn("test-access-token-value", out)
            self.assertNotIn("test-refresh-token-value", out)

            rc, out = _run_capturing(global_cli.cmd_global, ["status", "--json", "--global-url", "https://global.example", *self._argv()])
            self.assertEqual(rc, 0)
            payload = json.loads(out)
            self.assertTrue(payload["signed_in"])
            for value in payload.values():
                self.assertNotIn("test-access-token-value", str(value))
                self.assertNotIn("test-refresh-token-value", str(value))

            rc, out = _run_capturing(global_cli.cmd_global, ["logout", "--global-url", "https://global.example", *self._argv()])
            self.assertEqual(rc, 0)

        # After logout, status reports signed out and the direct-mode env
        # (absent here, but exercised in profile tests) would be untouched.
        rc, out = _run_capturing(global_cli.cmd_global, ["status", "--json", *self._argv()])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertFalse(payload["signed_in"])

    def test_status_when_signed_out(self):
        rc, out = _run_capturing(global_cli.cmd_global, ["status", *self._argv()])
        self.assertEqual(rc, 0)
        self.assertIn("Signed out", out)

    def test_status_json_when_signed_out_has_no_extra_fields(self):
        rc, out = _run_capturing(global_cli.cmd_global, ["status", "--json", *self._argv()])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload, {"signed_in": False})

    def test_login_denied_returns_authorization_denied_exit_code(self):
        routes = {
            ("POST", "/v1/auth/device/code"): (200, {
                "device_code": "dc", "user_code": "AAAA-0000",
                "verification_uri": "https://global.example/activate",
                "verification_uri_complete": "https://global.example/activate?user_code=AAAA-0000",
                "expires_in": 600, "interval": 0,
            }),
            ("POST", "/v1/auth/device/token"): (400, {"error": "access_denied"}),
        }
        handler = _handler_for(routes)
        with mock.patch("remoteRF.global_client.cli.GlobalApiClient", side_effect=_patched_api_client(handler)), \
             mock.patch("remoteRF.global_client.device_flow.webbrowser.open", return_value=True), \
             mock.patch("time.sleep"):
            rc, out = _run_capturing(global_cli.cmd_global, [
                "login", "--no-browser", "--global-url", "https://global.example", *self._argv(),
            ])
        self.assertNotEqual(rc, 0)
        self.assertIn("denied", out.lower())

    def test_unknown_global_subcommand_is_invalid_usage(self):
        rc, out = _run_capturing(global_cli.cmd_global, ["bogus"])
        self.assertEqual(rc, 2)


class DeploymentsCliTests(GlobalCliTestCase):
    def test_list_deployments_human_output(self):
        routes = {
            ("GET", "/v1/deployments"): (200, [{
                "id": "d1", "slug": "ucla", "display_name": "UCLA WirelessLab", "description": None,
                "online": True, "protocol_version": "1", "resource_count": 3,
            }]),
        }
        handler = _handler_for(routes)
        with mock.patch("remoteRF.global_client.cli.GlobalApiClient", side_effect=_patched_api_client(handler)):
            rc, out = _run_capturing(global_cli.cmd_deployments, ["--global-url", "https://global.example"])
        self.assertEqual(rc, 0)
        self.assertIn("ucla", out)
        self.assertIn("online", out)

    def test_list_deployments_json_has_no_credentials(self):
        routes = {("GET", "/v1/deployments"): (200, [])}
        handler = _handler_for(routes)
        with mock.patch("remoteRF.global_client.cli.GlobalApiClient", side_effect=_patched_api_client(handler)):
            rc, out = _run_capturing(global_cli.cmd_deployments, ["--json", "--global-url", "https://global.example"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), [])

    def test_show_unknown_deployment_reports_deployment_unavailable(self):
        routes = {("GET", "/v1/deployments/nope"): (404, {"error": {"code": "deployment_not_found", "message": "No such deployment."}})}
        handler = _handler_for(routes)
        with mock.patch("remoteRF.global_client.cli.GlobalApiClient", side_effect=_patched_api_client(handler)):
            rc, out = _run_capturing(global_cli.cmd_deployments, ["show", "nope", "--global-url", "https://global.example"])
        self.assertNotEqual(rc, 0)

    def test_resources_lists_public_catalog(self):
        routes = {
            ("GET", "/v1/deployments/ucla/resources"): (200, [{
                "id": "r1", "resource_ref": "remoterf://d1/pluto-1", "display_name": "Pluto RX #1",
                "device_type": "adalm_pluto", "capabilities": {"rx": True}, "policy_summary": {"rx_only": True},
            }]),
        }
        handler = _handler_for(routes)
        with mock.patch("remoteRF.global_client.cli.GlobalApiClient", side_effect=_patched_api_client(handler)):
            rc, out = _run_capturing(global_cli.cmd_deployments, ["resources", "ucla", "--global-url", "https://global.example"])
        self.assertEqual(rc, 0)
        self.assertIn("Pluto RX #1", out)


class UseCommandCliTests(GlobalCliTestCase):
    def test_use_direct_without_prior_config_reports_no_direct_config(self):
        rc, out = _run_capturing(global_cli.cmd_use, ["direct"])
        self.assertEqual(rc, 0)
        self.assertIn("direct", out.lower())

    def test_use_direct_does_not_touch_env_file(self):
        self.config_root.mkdir(parents=True, exist_ok=True)
        env_file = self.config_root / ".env"
        env_file.write_text("REMOTERF_ADDR=192.168.1.20:61005\nREMOTERF_CA_CERT=/tmp/ca.crt\n", encoding="utf-8")
        before = env_file.read_text(encoding="utf-8")

        s = load_state(self.config_root).with_(active_deployment_id="dep-1", active_deployment_slug="ucla")
        save_state(self.config_root, s)

        rc, _ = _run_capturing(global_cli.cmd_use, ["direct"])
        self.assertEqual(rc, 0)
        self.assertEqual(env_file.read_text(encoding="utf-8"), before)

        state_after = load_state(self.config_root)
        self.assertIsNone(state_after.active_deployment_id)

    def test_use_slug_without_login_fails_with_authentication_required(self):
        rc, out = _run_capturing(global_cli.cmd_use, ["ucla", *self._argv()])
        self.assertNotEqual(rc, 0)
        self.assertIn("not logged in", out.lower())

    def test_use_slug_blocked_at_globalauth_boundary_reports_clearly_and_does_not_set_active(self):
        routes = {
            ("GET", "/v1/deployments/ucla"): (200, {
                "id": "550e8400-e29b-41d4-a716-446655440000", "slug": "ucla", "display_name": "UCLA WirelessLab",
                "description": None, "online": True, "protocol_version": "1", "resource_count": 1,
            }),
            ("GET", "/v1/deployments/ucla/connection"): (200, {
                "deployment_id": "550e8400-e29b-41d4-a716-446655440000", "slug": "ucla",
                "display_name": "UCLA WirelessLab", "protocol_version": "1",
                "route": {
                    "kind": "tcp-relay", "grpc_endpoint": "ucla.global.remoterf.net:61005",
                    "certificate_endpoint": "ucla.global.remoterf.net:61006",
                    "tls_server_name": "ucla.global.remoterf.net", "ca_sha256": "AA:" * 31 + "BB",
                },
                "issued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00",
            }),
            ("POST", "/v1/deployments/ucla/access-assertion"): (200, {
                "assertion": "header.payload.sig", "deployment_id": "550e8400-e29b-41d4-a716-446655440000",
                "issued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2026-01-01T00:02:00+00:00",
            }),
        }
        handler = _handler_for(routes)

        # Force login first.
        store = global_cli.resolve_secret_store(config_root=self.config_root, force_file=True)
        cred_store = GlobalCredentialStore(store)
        from remoteRF.global_client.credentials import GlobalCredentials

        cred_store.save(GlobalCredentials.from_token_pair("access", "refresh", expires_in=900))
        save_state(self.config_root, default_state().with_(global_base_url="https://global.example"))

        with mock.patch("remoteRF.global_client.cli.GlobalApiClient", side_effect=_patched_api_client(handler)), \
             mock.patch("remoteRF.global_client.session_manager.verify_and_store_ca"), \
             mock.patch("remoteRF.global_client.session_manager.GlobalSessionManager.open_channel", return_value=mock.MagicMock(close=mock.MagicMock())):
            rc, out = _run_capturing(global_cli.cmd_use, ["ucla", *self._argv()])

        self.assertNotEqual(rc, 0)
        self.assertIn("globalauth", out.lower())

        state_after = load_state(self.config_root)
        self.assertIsNone(state_after.active_deployment_id, "must not mark a deployment active without a working session")


if __name__ == "__main__":
    unittest.main()
