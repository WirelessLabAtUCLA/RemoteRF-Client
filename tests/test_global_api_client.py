import json
import unittest

import httpx

from remoteRF.global_client.api_client import GlobalApiClient, GlobalApiHttpError
from remoteRF.global_client.errors import GlobalUnavailableError, InvalidUsageError


def _client(handler) -> GlobalApiClient:
    transport = httpx.MockTransport(handler)
    return GlobalApiClient("https://global.example", transport=transport)


class BaseUrlValidationTests(unittest.TestCase):
    def test_https_accepted(self):
        GlobalApiClient("https://global.example").close()

    def test_bare_http_rejected_by_default(self):
        with self.assertRaises(InvalidUsageError):
            GlobalApiClient("http://global.example")

    def test_http_allowed_with_explicit_opt_in(self):
        GlobalApiClient("http://localhost:8000", allow_insecure_http=True).close()

    def test_non_http_scheme_rejected(self):
        with self.assertRaises(InvalidUsageError):
            GlobalApiClient("ftp://global.example")


class DeviceCodeTests(unittest.TestCase):
    def test_request_device_code_parses_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/auth/device/code")
            return httpx.Response(200, json={
                "device_code": "very-secret-device-code",
                "user_code": "ABCD-1234",
                "verification_uri": "https://global.example/activate",
                "verification_uri_complete": "https://global.example/activate?user_code=ABCD-1234",
                "expires_in": 600,
                "interval": 5,
            })

        api = _client(handler)
        resp = api.request_device_code()
        self.assertEqual(resp.user_code, "ABCD-1234")
        self.assertEqual(resp.interval, 5)
        api.close()

    def test_poll_device_token_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "access_token": "acc", "refresh_token": "ref", "token_type": "bearer", "expires_in": 900,
            })

        api = _client(handler)
        outcome = api.poll_device_token("dc")
        self.assertIsNotNone(outcome.token_pair)
        self.assertEqual(outcome.token_pair.access_token, "acc")
        api.close()

    def test_poll_device_token_bare_string_error_shape(self):
        # device_auth.py returns {"error": "authorization_pending"} -- a
        # bare string, unlike every other endpoint's {"error": {...}}.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "authorization_pending"})

        api = _client(handler)
        outcome = api.poll_device_token("dc")
        self.assertIsNone(outcome.token_pair)
        self.assertEqual(outcome.error, "authorization_pending")
        api.close()

    def test_poll_device_token_slow_down_carries_retry_after(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "slow_down"}, headers={"Retry-After": "3"})

        api = _client(handler)
        outcome = api.poll_device_token("dc")
        self.assertEqual(outcome.error, "slow_down")
        self.assertEqual(outcome.retry_after, 3.0)
        api.close()


class GenericErrorParsingTests(unittest.TestCase):
    def test_generic_error_envelope_parsed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": {"code": "deployment_not_found", "message": "No such deployment."}})

        api = _client(handler)
        with self.assertRaises(GlobalApiHttpError) as ctx:
            api.get_deployment("nope")
        self.assertEqual(ctx.exception.code, "deployment_not_found")
        self.assertEqual(ctx.exception.status_code, 404)
        api.close()

    def test_rate_limited_carries_retry_after(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429, json={"error": {"code": "rate_limited", "message": "slow down"}}, headers={"Retry-After": "7"}
            )

        api = _client(handler)
        with self.assertRaises(GlobalApiHttpError) as ctx:
            api.list_deployments()
        self.assertEqual(ctx.exception.retry_after, 7.0)
        api.close()

    def test_non_json_error_body_does_not_crash(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal server error")

        api = _client(handler)
        with self.assertRaises(GlobalApiHttpError):
            api.list_deployments()
        api.close()

    def test_network_failure_raises_global_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        api = _client(handler)
        with self.assertRaises(GlobalUnavailableError):
            api.list_deployments()
        api.close()


class RedirectRefusalTests(unittest.TestCase):
    def test_redirect_response_is_refused_not_followed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

        api = _client(handler)
        with self.assertRaises(GlobalUnavailableError):
            api.list_deployments(access_token="secret-token")
        api.close()

    def test_follow_redirects_is_disabled_on_the_underlying_client(self):
        api = GlobalApiClient("https://global.example")
        self.assertFalse(api._client.follow_redirects)
        api.close()


class AuthorizationHeaderTests(unittest.TestCase):
    def test_authorization_header_present_only_when_token_given(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={
                "id": "u1", "email": "a@b.com", "status": "active", "email_verified": True, "created_at": "x",
            })

        api = _client(handler)
        api.me(access_token="my-secret-token")
        api.close()
        self.assertEqual(seen["auth"], "Bearer my-secret-token")

    def test_no_authorization_header_for_anonymous_deployment_listing(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=[])

        api = _client(handler)
        api.list_deployments()
        api.close()
        self.assertIsNone(seen["auth"])


class DeploymentEndpointTests(unittest.TestCase):
    def test_list_deployments_parses_array(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[{
                "id": "d1", "slug": "ucla", "display_name": "UCLA WirelessLab", "description": None,
                "online": True, "protocol_version": "1", "resource_count": 3,
            }])

        api = _client(handler)
        deployments = api.list_deployments()
        self.assertEqual(len(deployments), 1)
        self.assertEqual(deployments[0].slug, "ucla")
        api.close()

    def test_get_connection_descriptor_parses_nested_route(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "deployment_id": "d1", "slug": "ucla", "display_name": "UCLA WirelessLab", "protocol_version": "1",
                "route": {
                    "kind": "tcp-relay", "grpc_endpoint": "ucla.global.remoterf.net:61005",
                    "certificate_endpoint": "ucla.global.remoterf.net:61006",
                    "tls_server_name": "ucla.global.remoterf.net", "ca_sha256": "AA:" * 31 + "BB",
                },
                "issued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2026-01-01T00:05:00+00:00",
            })

        api = _client(handler)
        descriptor = api.get_connection_descriptor("ucla", access_token="tok")
        self.assertEqual(descriptor.route.kind, "tcp-relay")
        self.assertEqual(descriptor.route.grpc_endpoint, "ucla.global.remoterf.net:61005")
        api.close()

    def test_access_assertion_parses_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertTrue(request.url.path.endswith("/access-assertion"))
            return httpx.Response(200, json={
                "assertion": "header.payload.sig", "deployment_id": "d1",
                "issued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2026-01-01T00:02:00+00:00",
            })

        api = _client(handler)
        resp = api.request_access_assertion("ucla", access_token="tok")
        self.assertEqual(resp.deployment_id, "d1")
        api.close()

    def test_slug_is_url_encoded(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["raw_path"] = request.url.raw_path.decode()
            return httpx.Response(200, json={
                "id": "d1", "slug": "weird/slug", "display_name": "x", "description": None,
                "online": True, "protocol_version": "1", "resource_count": 0,
            })

        api = _client(handler)
        api.get_deployment("weird/slug")
        api.close()
        # the raw slash in the slug must be percent-encoded on the wire,
        # not sent as an extra path segment
        self.assertIn("weird%2Fslug", seen["raw_path"])


if __name__ == "__main__":
    unittest.main()
