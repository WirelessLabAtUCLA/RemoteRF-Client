import tempfile
import unittest
from pathlib import Path

from remoteRF.global_client import state as state_mod
from remoteRF.global_client.profile import (
    load_direct_profile,
    load_global_profile,
    resolve_active_profile,
)


class GlobalStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_root = Path(self._tmp.name)

    def test_missing_state_file_returns_default(self):
        loaded = state_mod.load_state(self.config_root)
        self.assertEqual(loaded.schema_version, state_mod.STATE_SCHEMA_VERSION)
        self.assertIsNone(loaded.active_deployment_id)

    def test_save_then_load_round_trips(self):
        s = state_mod.default_state().with_(
            user_id="u1", user_email="a@b.com",
            active_deployment_id="dep-1", active_deployment_slug="ucla",
            active_deployment_display_name="UCLA WirelessLab",
        )
        state_mod.save_state(self.config_root, s)
        loaded = state_mod.load_state(self.config_root)
        self.assertEqual(loaded.user_id, "u1")
        self.assertEqual(loaded.active_deployment_slug, "ucla")

    def test_corrupt_state_file_falls_back_to_default_without_raising(self):
        path = state_mod.state_path(self.config_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        loaded = state_mod.load_state(self.config_root)
        self.assertEqual(loaded.schema_version, state_mod.STATE_SCHEMA_VERSION)
        self.assertIsNone(loaded.active_deployment_id)

    def test_state_file_never_carries_secret_looking_fields(self):
        s = state_mod.default_state().with_(user_id="u1")
        state_mod.save_state(self.config_root, s)
        raw = state_mod.state_path(self.config_root).read_text(encoding="utf-8")
        for forbidden in ("access_token", "refresh_token", "assertion", "session_material", "password"):
            self.assertNotIn(forbidden, raw)

    def test_state_file_permissions_are_restrictive(self):
        state_mod.save_state(self.config_root, state_mod.default_state())
        path = state_mod.state_path(self.config_root)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_cleared_active_deployment_only_touches_deployment_fields(self):
        s = state_mod.default_state().with_(
            user_id="u1", active_deployment_id="dep-1", active_deployment_slug="ucla",
        )
        cleared = s.cleared_active_deployment()
        self.assertEqual(cleared.user_id, "u1")
        self.assertIsNone(cleared.active_deployment_id)

    def test_cleared_user_also_clears_active_deployment(self):
        s = state_mod.default_state().with_(user_id="u1", active_deployment_id="dep-1")
        cleared = s.cleared_user()
        self.assertIsNone(cleared.user_id)
        self.assertIsNone(cleared.active_deployment_id)


class DeploymentProfileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_root = Path(self._tmp.name)

    def _profile(self, deployment_id="dep-1") -> state_mod.DeploymentProfile:
        return state_mod.DeploymentProfile(
            deployment_id=deployment_id, slug="ucla", display_name="UCLA WirelessLab",
            protocol_version="1", route_kind="tcp-relay",
            grpc_endpoint="ucla.global.remoterf.net:61005",
            certificate_endpoint="ucla.global.remoterf.net:61006",
            tls_server_name="ucla.global.remoterf.net",
            ca_sha256="AA:" * 31 + "BB",
            descriptor_issued_at="2026-01-01T00:00:00+00:00",
            descriptor_expires_at="2026-01-01T00:05:00+00:00",
        )

    def test_save_then_load_round_trips(self):
        profile = self._profile()
        state_mod.save_deployment_profile(self.config_root, profile)
        loaded = state_mod.load_deployment_profile(self.config_root, "dep-1")
        self.assertEqual(loaded.slug, "ucla")
        self.assertEqual(loaded.grpc_endpoint, "ucla.global.remoterf.net:61005")

    def test_missing_profile_returns_none(self):
        self.assertIsNone(state_mod.load_deployment_profile(self.config_root, "nope"))

    def test_profile_directory_permissions_are_restrictive(self):
        state_mod.save_deployment_profile(self.config_root, self._profile())
        directory = state_mod.deployment_dir(self.config_root, "dep-1")
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)

    def test_two_deployments_do_not_collide(self):
        state_mod.save_deployment_profile(self.config_root, self._profile("dep-a"))
        state_mod.save_deployment_profile(self.config_root, self._profile("dep-b"))
        self.assertIsNotNone(state_mod.load_deployment_profile(self.config_root, "dep-a"))
        self.assertIsNotNone(state_mod.load_deployment_profile(self.config_root, "dep-b"))


class ProfileResolutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_root = Path(self._tmp.name)

    def _write_direct_env(self, addr="192.168.1.20:61005", ca="/tmp/ca.crt", tls_name=None):
        self.config_root.mkdir(parents=True, exist_ok=True)
        lines = [f"REMOTERF_ADDR={addr}", f"REMOTERF_CA_CERT={ca}"]
        if tls_name:
            lines.append(f"REMOTERF_TLS_SERVER_NAME={tls_name}")
        (self.config_root / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_no_config_at_all_resolves_to_none(self):
        self.assertIsNone(resolve_active_profile(self.config_root))

    def test_old_direct_env_with_no_mode_field_resolves_as_direct(self):
        self._write_direct_env()
        profile = resolve_active_profile(self.config_root)
        self.assertEqual(profile.mode, "direct")
        self.assertEqual(profile.grpc_endpoint, "192.168.1.20:61005")

    def test_dns_hostname_direct_env_preserved_verbatim(self):
        self._write_direct_env(addr="ucla.global.remoterf.net:61005")
        profile = load_direct_profile(self.config_root)
        self.assertEqual(profile.grpc_endpoint, "ucla.global.remoterf.net:61005")

    def test_incomplete_direct_env_resolves_to_none(self):
        self.config_root.mkdir(parents=True, exist_ok=True)
        (self.config_root / ".env").write_text("REMOTERF_ADDR=1.2.3.4:5\n", encoding="utf-8")
        self.assertIsNone(load_direct_profile(self.config_root))

    def test_active_global_deployment_takes_precedence_over_direct(self):
        self._write_direct_env()  # direct config exists too

        profile = state_mod.DeploymentProfile(
            deployment_id="dep-1", slug="ucla", display_name="UCLA WirelessLab",
            protocol_version="1", route_kind="tcp-relay",
            grpc_endpoint="ucla.global.remoterf.net:61005", certificate_endpoint=None,
            tls_server_name="ucla.global.remoterf.net", ca_sha256="AA:" * 31 + "BB",
            descriptor_issued_at="2026-01-01T00:00:00+00:00", descriptor_expires_at="2026-01-01T00:05:00+00:00",
        )
        state_mod.save_deployment_profile(self.config_root, profile)
        ca_file = state_mod.ca_path(self.config_root, "dep-1")
        ca_file.parent.mkdir(parents=True, exist_ok=True)
        ca_file.write_bytes(b"-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n")

        s = state_mod.load_state(self.config_root).with_(active_deployment_id="dep-1", active_deployment_slug="ucla")
        state_mod.save_state(self.config_root, s)

        resolved = resolve_active_profile(self.config_root)
        self.assertEqual(resolved.mode, "global")
        self.assertEqual(resolved.deployment_slug, "ucla")

    def test_global_profile_missing_ca_file_falls_back_to_none(self):
        # profile.json + state say "active" but the CA file was never
        # verified/written (e.g. a crashed `use` mid-flow) -- must not
        # claim a usable global profile without a verified CA on disk.
        profile = state_mod.DeploymentProfile(
            deployment_id="dep-1", slug="ucla", display_name="UCLA WirelessLab",
            protocol_version="1", route_kind="tcp-relay",
            grpc_endpoint="ucla.global.remoterf.net:61005", certificate_endpoint=None,
            tls_server_name="ucla.global.remoterf.net", ca_sha256="AA:" * 31 + "BB",
            descriptor_issued_at="2026-01-01T00:00:00+00:00", descriptor_expires_at="2026-01-01T00:05:00+00:00",
        )
        state_mod.save_deployment_profile(self.config_root, profile)
        s = state_mod.load_state(self.config_root).with_(active_deployment_id="dep-1")
        state_mod.save_state(self.config_root, s)

        self.assertIsNone(load_global_profile(self.config_root))

    def test_switching_back_to_direct_leaves_env_file_untouched(self):
        self._write_direct_env(addr="192.168.1.20:61005")
        original = (self.config_root / ".env").read_text(encoding="utf-8")

        s = state_mod.load_state(self.config_root).with_(active_deployment_id="dep-1", active_deployment_slug="ucla")
        state_mod.save_state(self.config_root, s)
        cleared = state_mod.load_state(self.config_root).cleared_active_deployment()
        state_mod.save_state(self.config_root, cleared)

        self.assertEqual((self.config_root / ".env").read_text(encoding="utf-8"), original)
        profile = resolve_active_profile(self.config_root)
        self.assertEqual(profile.mode, "direct")


if __name__ == "__main__":
    unittest.main()
