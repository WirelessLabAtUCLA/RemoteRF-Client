import unittest
from unittest import mock

from remoteRF import remoterf_cli


class RemoteRFCliTests(unittest.TestCase):
    # `-l` on an unconfigured client registers instead: tests/test_cli_login_fresh.py

    @mock.patch.object(remoterf_cli, "_print_server_unavailable")
    @mock.patch.object(remoterf_cli, "_connected_server", return_value=None)
    @mock.patch.object(
        remoterf_cli,
        "_ensure_config_present",
        return_value=(True, ""),
    )
    @mock.patch.object(remoterf_cli.sys, "argv", ["remoterf", "-l"])
    def test_disconnected_server_stops_before_login(
        self,
        _ensure_config,
        connected_server,
        print_unavailable,
    ):
        self.assertEqual(remoterf_cli.main(), 2)
        connected_server.assert_called_once_with()
        print_unavailable.assert_called_once_with()

    @mock.patch("remoteRF.config.config.configure")
    @mock.patch.object(
        remoterf_cli.sys, "argv", ["remoterf", "--config", "--addr", "10.0.0.1:12321"]
    )
    @mock.patch("remoteRF.deployment.state.select_direct")
    def test_config_without_cert_port_defaults_to_grpc_port_plus_one(self, _select_direct, configure):
        configure.return_value = 0
        self.assertEqual(remoterf_cli.main(), 0)
        configure.assert_called_once_with("10.0.0.1", 12321, 12322)

    @mock.patch("remoteRF.config.config.configure")
    @mock.patch.object(
        remoterf_cli.sys,
        "argv",
        ["remoterf", "--config", "--addr", "10.0.0.1:12321", "--cert-port", "9999"],
    )
    @mock.patch("remoteRF.deployment.state.select_direct")
    def test_config_with_explicit_cert_port_overrides_default(self, _select_direct, configure):
        configure.return_value = 0
        self.assertEqual(remoterf_cli.main(), 0)
        configure.assert_called_once_with("10.0.0.1", 12321, 9999)

    @mock.patch("remoteRF.config.config.configure")
    @mock.patch.object(
        remoterf_cli.sys, "argv", ["remoterf", "-c", "-a", "10.0.0.1:12321"]
    )
    @mock.patch("remoteRF.deployment.state.select_direct")
    def test_existing_short_flag_usage_remains_compatible(self, _select_direct, configure):
        configure.return_value = 0
        self.assertEqual(remoterf_cli.main(), 0)
        configure.assert_called_once_with("10.0.0.1", 12321, 12322)


if __name__ == "__main__":
    unittest.main()
