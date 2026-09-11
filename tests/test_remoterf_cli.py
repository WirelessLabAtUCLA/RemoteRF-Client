import unittest
from unittest import mock

from remoteRF import remoterf_cli


class RemoteRFCliTests(unittest.TestCase):
    @mock.patch.object(remoterf_cli, "printf")
    @mock.patch.object(remoterf_cli, "print_client_banner")
    @mock.patch.object(
        remoterf_cli,
        "_ensure_config_present",
        return_value=(False, "not configured"),
    )
    @mock.patch.object(remoterf_cli, "_installed_version", return_value="2.0.9")
    @mock.patch.object(remoterf_cli.sys, "argv", ["remoterf", "-l"])
    def test_unconfigured_login_shows_na_banner_and_configure_message(
        self,
        _version,
        _ensure_config,
        print_banner,
        printf,
    ):
        self.assertEqual(remoterf_cli.main(), 2)
        print_banner.assert_called_once_with("2.0.9", server="")
        self.assertTrue(
            any(
                call.args and call.args[0] == "Please configure remoterf properly first."
                for call in printf.call_args_list
            )
        )

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
    def test_config_without_cert_port_defaults_to_grpc_port_plus_one(self, configure):
        self.assertEqual(remoterf_cli.main(), 0)
        configure.assert_called_once_with("10.0.0.1", 12321, 12322)

    @mock.patch("remoteRF.config.config.configure")
    @mock.patch.object(
        remoterf_cli.sys,
        "argv",
        ["remoterf", "--config", "--addr", "10.0.0.1:12321", "--cert-port", "9999"],
    )
    def test_config_with_explicit_cert_port_overrides_default(self, configure):
        self.assertEqual(remoterf_cli.main(), 0)
        configure.assert_called_once_with("10.0.0.1", 12321, 9999)

    @mock.patch("remoteRF.config.config.configure")
    @mock.patch.object(
        remoterf_cli.sys, "argv", ["remoterf", "-c", "-a", "10.0.0.1:12321"]
    )
    def test_existing_short_flag_usage_remains_compatible(self, configure):
        self.assertEqual(remoterf_cli.main(), 0)
        configure.assert_called_once_with("10.0.0.1", 12321, 12322)


if __name__ == "__main__":
    unittest.main()
