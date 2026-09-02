import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from remoteRF.global_client.api_client import GlobalApiClient
from remoteRF.global_client.auth_client import AuthenticatedGlobalClient
from remoteRF.global_client.credentials import FileSecretStore, GlobalCredentials, GlobalCredentialStore
from remoteRF.global_client.errors import GlobalAuthenticationExpiredError, NotLoggedInError


def _api(handler) -> GlobalApiClient:
    return GlobalApiClient("https://global.example", transport=httpx.MockTransport(handler))


def _cred_store() -> GlobalCredentialStore:
    return GlobalCredentialStore(FileSecretStore(Path(tempfile.mkdtemp())))


class NotLoggedInTests(unittest.TestCase):
    def test_call_without_stored_credentials_raises_not_logged_in(self):
        def handler(request):
            self.fail("should never reach the network when not logged in")

        api = _api(handler)
        auth = AuthenticatedGlobalClient(api, _cred_store())
        with self.assertRaises(NotLoggedInError):
            auth.me()
        api.close()


class AutoRefreshTests(unittest.TestCase):
    def test_expired_access_token_is_refreshed_before_the_call(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == "/v1/auth/refresh":
                return httpx.Response(200, json={
                    "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 900,
                })
            self.assertEqual(request.headers.get("authorization"), "Bearer new-access")
            return httpx.Response(200, json={
                "id": "u1", "email": "a@b.com", "status": "active", "email_verified": True, "created_at": "x",
            })

        api = _api(handler)
        store = _cred_store()
        expired = GlobalCredentials.from_token_pair("old-access", "old-refresh", expires_in=-100)
        store.save(expired)

        auth = AuthenticatedGlobalClient(api, store)
        me = auth.me()
        api.close()

        self.assertEqual(me.id, "u1")
        self.assertEqual(calls, ["/v1/auth/refresh", "/v1/me"])
        # rotation persisted
        self.assertEqual(store.load().access_token, "new-access")
        self.assertEqual(store.load().refresh_token, "new-refresh")

    def test_401_triggers_exactly_one_refresh_and_retry(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == "/v1/auth/refresh":
                return httpx.Response(200, json={
                    "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 900,
                })
            if request.headers.get("authorization") == "Bearer old-access":
                return httpx.Response(401, json={"error": {"code": "invalid_token", "message": "expired"}})
            return httpx.Response(200, json=[])

        api = _api(handler)
        store = _cred_store()
        store.save(GlobalCredentials.from_token_pair("old-access", "old-refresh", expires_in=900))

        auth = AuthenticatedGlobalClient(api, store)
        result = auth.list_deployments()
        api.close()

        self.assertEqual(result, [])
        self.assertEqual(calls, ["/v1/deployments", "/v1/auth/refresh", "/v1/deployments"])

    def test_never_retries_more_than_once(self):
        call_count = {"deployments": 0, "refresh": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/auth/refresh":
                call_count["refresh"] += 1
                return httpx.Response(200, json={
                    "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 900,
                })
            call_count["deployments"] += 1
            return httpx.Response(401, json={"error": {"code": "invalid_token", "message": "still bad"}})

        api = _api(handler)
        store = _cred_store()
        store.save(GlobalCredentials.from_token_pair("old-access", "old-refresh", expires_in=900))

        auth = AuthenticatedGlobalClient(api, store)
        with self.assertRaises(Exception):
            auth.list_deployments()
        api.close()

        # Exactly one retry: two attempts at the protected call, one refresh.
        self.assertEqual(call_count["deployments"], 2)
        self.assertEqual(call_count["refresh"], 1)

    def test_failed_refresh_clears_credentials_and_raises_authentication_expired(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/auth/refresh":
                return httpx.Response(401, json={"error": {"code": "invalid_refresh_token", "message": "bad"}})
            self.fail("must not call the protected endpoint before refresh succeeds")

        api = _api(handler)
        store = _cred_store()
        store.save(GlobalCredentials.from_token_pair("old-access", "old-refresh", expires_in=-100))

        auth = AuthenticatedGlobalClient(api, store)
        with self.assertRaises(GlobalAuthenticationExpiredError):
            auth.me()
        api.close()

        self.assertIsNone(store.load())


class LogoutTests(unittest.TestCase):
    def test_logout_clears_credentials_even_if_server_call_fails(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("global is down")

        api = _api(handler)
        store = _cred_store()
        store.save(GlobalCredentials.from_token_pair("a", "r", expires_in=900))

        auth = AuthenticatedGlobalClient(api, store)
        auth.logout()  # must not raise
        api.close()

        self.assertIsNone(store.load())

    def test_logout_when_already_signed_out_is_a_no_op(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.fail("must not call Global when there is nothing to log out")

        api = _api(handler)
        store = _cred_store()
        auth = AuthenticatedGlobalClient(api, store)
        auth.logout()
        api.close()


if __name__ == "__main__":
    unittest.main()
