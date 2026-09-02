"""Active direct/Global profile channel lifecycle regression tests."""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from remoteRF.core import grpc_client
from remoteRF.global_client.profile import DirectConnectionProfile, GlobalConnectionProfile


class GrpcClientProfileTests(unittest.TestCase):
    def setUp(self):
        # A few legacy driver tests install a lightweight fake under this
        # module name at collection time. This suite intentionally exercises
        # the production resolver, so reload the real module once needed.
        global grpc_client
        if not hasattr(grpc_client, "close_active_connection"):
            sys.modules.pop("remoteRF.core.grpc_client", None)
            grpc_client = importlib.import_module("remoteRF.core.grpc_client")
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ca = Path(self.tmp.name) / "ca.crt"
        self.ca.write_text("test-ca", encoding="utf-8")
        grpc_client.close_active_connection()
        self.addCleanup(grpc_client.close_active_connection)

    def _direct(self) -> DirectConnectionProfile:
        return DirectConnectionProfile("192.0.2.10:61005", None, self.ca)

    def _global(self, deployment_id: str = "dep-ucla") -> GlobalConnectionProfile:
        return GlobalConnectionProfile(
            deployment_id=deployment_id,
            deployment_slug="ucla" if deployment_id == "dep-ucla" else "other",
            display_name="Test deployment",
            grpc_endpoint="ucla.global.example:61005" if deployment_id == "dep-ucla" else "other.global.example:61005",
            tls_server_name="ucla.global.example" if deployment_id == "dep-ucla" else "other.global.example",
            ca_path=self.ca,
        )

    def test_direct_startup_builds_direct_channel_only_when_requested(self):
        channel = mock.MagicMock()
        with mock.patch.object(grpc_client, "resolve_active_profile", return_value=self._direct()), mock.patch.object(
            grpc_client, "build_secure_channel", return_value=channel
        ) as build:
            self.assertEqual(grpc_client.active_endpoint(), "192.0.2.10:61005")
        build.assert_called_once_with("192.0.2.10:61005", b"test-ca", tls_server_name=None)

    def test_global_startup_uses_descriptor_endpoint_and_tls_name(self):
        channel = mock.MagicMock()
        with mock.patch.object(grpc_client, "resolve_active_profile", return_value=self._global()), mock.patch.object(
            grpc_client, "build_secure_channel", return_value=channel
        ) as build:
            self.assertEqual(grpc_client.active_endpoint(), "ucla.global.example:61005")
        build.assert_called_once_with(
            "ucla.global.example:61005", b"test-ca", tls_server_name="ucla.global.example"
        )

    def test_direct_to_global_replaces_and_closes_stale_channel(self):
        direct_channel, global_channel = mock.MagicMock(), mock.MagicMock()
        with mock.patch.object(grpc_client, "resolve_active_profile", side_effect=[self._direct(), self._global()]), mock.patch.object(
            grpc_client, "build_secure_channel", side_effect=[direct_channel, global_channel]
        ):
            grpc_client.get_active_connection()
            active = grpc_client.get_active_connection()
        direct_channel.close.assert_called_once_with()
        self.assertEqual(active.endpoint, "ucla.global.example:61005")

    def test_global_to_direct_replaces_and_closes_stale_channel(self):
        global_channel, direct_channel = mock.MagicMock(), mock.MagicMock()
        with mock.patch.object(grpc_client, "resolve_active_profile", side_effect=[self._global(), self._direct()]), mock.patch.object(
            grpc_client, "build_secure_channel", side_effect=[global_channel, direct_channel]
        ):
            grpc_client.get_active_connection()
            active = grpc_client.get_active_connection()
        global_channel.close.assert_called_once_with()
        self.assertEqual(active.endpoint, "192.0.2.10:61005")

    def test_switching_deployments_cannot_reuse_another_deployment_channel(self):
        ucla_channel, other_channel = mock.MagicMock(), mock.MagicMock()
        with mock.patch.object(grpc_client, "resolve_active_profile", side_effect=[self._global(), self._global("dep-other")]), mock.patch.object(
            grpc_client, "build_secure_channel", side_effect=[ucla_channel, other_channel]
        ) as build:
            grpc_client.get_active_connection()
            grpc_client.get_active_connection()
        ucla_channel.close.assert_called_once_with()
        self.assertEqual(build.call_count, 2)
