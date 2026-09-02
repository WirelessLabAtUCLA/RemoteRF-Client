import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from remoteRF.global_client.credentials import (
    CredentialStoreMode,
    FileSecretStore,
    GlobalCredentials,
    GlobalCredentialStore,
    KeyringSecretStore,
    resolve_secret_store,
)
from remoteRF.global_client.local_sessions import LocalDeploymentSession, LocalSessionStore


class FakeKeyringBackend:
    """In-memory stand-in for the `keyring` module's public API, so tests
    never touch the developer's real OS keychain."""

    def __init__(self):
        self._data: dict[tuple[str, str], str] = {}

    def get_password(self, service, key):
        return self._data.get((service, key))

    def set_password(self, service, key, value):
        self._data[(service, key)] = value

    def delete_password(self, service, key):
        if (service, key) not in self._data:
            import keyring.errors

            raise keyring.errors.PasswordDeleteError("not found")
        del self._data[(service, key)]


def _creds(access="access-tok", refresh="refresh-tok", *, expires_in=900) -> GlobalCredentials:
    return GlobalCredentials.from_token_pair(access, refresh, expires_in)


class GlobalCredentialsTests(unittest.TestCase):
    def test_round_trips_through_dict(self):
        creds = _creds()
        restored = GlobalCredentials.from_dict(creds.to_dict())
        self.assertEqual(restored.access_token, creds.access_token)
        self.assertEqual(restored.refresh_token, creds.refresh_token)
        self.assertEqual(restored.expires_at, creds.expires_at)

    def test_repr_never_contains_token_values(self):
        creds = _creds(access="super-secret-access", refresh="super-secret-refresh")
        text = repr(creds)
        self.assertNotIn("super-secret-access", text)
        self.assertNotIn("super-secret-refresh", text)

    def test_is_expired_respects_skew(self):
        creds = GlobalCredentials(
            access_token="a", refresh_token="r",
            obtained_at=datetime.now(timezone.utc) - timedelta(minutes=20),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=10),
        )
        self.assertTrue(creds.is_access_token_expired(skew_seconds=30))
        self.assertFalse(creds.is_access_token_expired(skew_seconds=0))


class KeyringSecretStoreTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeKeyringBackend()
        self.store = KeyringSecretStore(backend=self.backend)

    def test_set_then_get_round_trips(self):
        self.store.set("k1", {"a": 1})
        self.assertEqual(self.store.get("k1"), {"a": 1})

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.store.get("missing"))

    def test_delete_missing_does_not_raise(self):
        self.store.delete("missing")  # must not raise

    def test_corrupt_entry_treated_as_absent(self):
        self.backend.set_password("remoterf-global", "k1", "not json{{{")
        self.assertIsNone(self.store.get("k1"))

    def test_never_calls_real_keyring_module_directly(self):
        # Sanity check that our fake, not the real OS keychain, backs this test.
        self.assertIsInstance(self.store._keyring, FakeKeyringBackend)


class FileSecretStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "secrets"
        self.store = FileSecretStore(self.dir)

    def test_set_then_get_round_trips(self):
        self.store.set("k1", {"access_token": "abc"})
        self.assertEqual(self.store.get("k1"), {"access_token": "abc"})

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.store.get("missing"))

    def test_directory_and_file_permissions_are_restrictive(self):
        self.store.set("k1", {"a": 1})
        dir_mode = self.dir.stat().st_mode & 0o777
        file_mode = self.store._path("k1").stat().st_mode & 0o777
        self.assertEqual(dir_mode, 0o700)
        self.assertEqual(file_mode, 0o600)

    def test_write_is_atomic_no_partial_file_left_behind(self):
        self.store.set("k1", {"a": 1})
        leftover_tmp_files = [p for p in self.dir.iterdir() if p.name.startswith(".tmp-")]
        self.assertEqual(leftover_tmp_files, [])

    def test_corrupt_file_treated_as_absent(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "k1.json").write_text("{not valid json", encoding="utf-8")
        self.assertIsNone(self.store.get("k1"))

    def test_delete_missing_does_not_raise(self):
        self.store.delete("missing")


class ResolveSecretStoreTests(unittest.TestCase):
    def test_force_file_skips_keyring_probe_and_stays_quiet(self):
        warnings = []
        with tempfile.TemporaryDirectory() as tmp:
            store = resolve_secret_store(config_root=Path(tmp), force_file=True, warn=warnings.append)
        self.assertEqual(store.mode, CredentialStoreMode.FILE)
        self.assertEqual(warnings, [])

    def test_falls_back_to_file_with_warning_when_keyring_unusable(self):
        warnings = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "remoteRF.global_client.credentials._keyring_is_usable", return_value=False
        ):
            store = resolve_secret_store(config_root=Path(tmp), force_file=False, warn=warnings.append)
        self.assertEqual(store.mode, CredentialStoreMode.FILE)
        self.assertEqual(len(warnings), 1)
        self.assertIn("weaker", warnings[0].lower())

    def test_uses_keyring_when_usable(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "remoteRF.global_client.credentials._keyring_is_usable", return_value=True
        ), mock.patch("keyring.get_password", return_value=None), mock.patch("keyring.set_password"):
            store = resolve_secret_store(config_root=Path(tmp), force_file=False)
        self.assertEqual(store.mode, CredentialStoreMode.KEYRING)


class GlobalCredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.secret_store = FileSecretStore(Path(tempfile.mkdtemp()))
        self.store = GlobalCredentialStore(self.secret_store)

    def test_load_when_absent_is_none(self):
        self.assertIsNone(self.store.load())

    def test_save_then_load_round_trips(self):
        creds = _creds()
        self.store.save(creds)
        loaded = self.store.load()
        self.assertEqual(loaded.access_token, creds.access_token)
        self.assertEqual(loaded.refresh_token, creds.refresh_token)

    def test_clear_removes_credentials(self):
        self.store.save(_creds())
        self.store.clear()
        self.assertIsNone(self.store.load())

    def test_rotation_overwrites_atomically_not_appends(self):
        self.store.save(_creds(refresh="first-refresh"))
        self.store.save(_creds(refresh="second-refresh"))
        loaded = self.store.load()
        self.assertEqual(loaded.refresh_token, "second-refresh")


class LocalSessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.secret_store = FileSecretStore(Path(tempfile.mkdtemp()))
        self.store = LocalSessionStore(self.secret_store)

    def _session(self, deployment_id="dep-a", tls_name="ucla.global.remoterf.net") -> LocalDeploymentSession:
        return LocalDeploymentSession(
            deployment_id=deployment_id,
            tls_server_name=tls_name,
            session_material={"opaque": "blob"},
            obtained_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    def test_save_then_load_round_trips(self):
        session = self._session()
        self.store.save(session)
        loaded = self.store.load("dep-a")
        self.assertEqual(loaded.session_material, {"opaque": "blob"})

    def test_sessions_are_isolated_per_deployment(self):
        self.store.save(self._session(deployment_id="dep-a", tls_name="a.example"))
        self.store.save(self._session(deployment_id="dep-b", tls_name="b.example"))
        self.assertEqual(self.store.load("dep-a").tls_server_name, "a.example")
        self.assertEqual(self.store.load("dep-b").tls_server_name, "b.example")
        self.assertIsNone(self.store.load("dep-c"))

    def test_clearing_one_deployment_does_not_affect_another(self):
        self.store.save(self._session(deployment_id="dep-a"))
        self.store.save(self._session(deployment_id="dep-b"))
        self.store.clear("dep-a")
        self.assertIsNone(self.store.load("dep-a"))
        self.assertIsNotNone(self.store.load("dep-b"))

    def test_is_expired(self):
        expired = LocalDeploymentSession(
            deployment_id="dep-a", tls_server_name="a.example", session_material={},
            obtained_at=datetime.now(timezone.utc) - timedelta(hours=2),
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        self.assertTrue(expired.is_expired())

        no_expiry = LocalDeploymentSession(
            deployment_id="dep-a", tls_server_name="a.example", session_material={},
            obtained_at=datetime.now(timezone.utc), expires_at=None,
        )
        self.assertFalse(no_expiry.is_expired())

    def test_repr_never_contains_session_material(self):
        session = self._session()
        text = repr(session)
        self.assertNotIn("opaque", text)
        self.assertNotIn("blob", text)


if __name__ == "__main__":
    unittest.main()
