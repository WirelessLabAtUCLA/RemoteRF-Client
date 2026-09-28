import socket
import unittest
from unittest import mock

from remoteRF.config import config as config_mod


class ParseHostPortTests(unittest.TestCase):
    def test_valid_ipv4(self):
        host, port = config_mod._parse_hostport("192.168.1.20:12321")
        self.assertEqual(host, "192.168.1.20")
        self.assertEqual(port, 12321)

    def test_valid_hostname(self):
        host, port = config_mod._parse_hostport("ucla.global.remoterf.net:12321")
        self.assertEqual(host, "ucla.global.remoterf.net")
        self.assertEqual(port, 12321)

    def test_hostname_not_rewritten_or_rejected(self):
        # The parser must not attempt to resolve or normalize the hostname
        # into an IP -- it is stored/passed through as-is.
        host, _ = config_mod._parse_hostport("ucla.global.remoterf.net:443")
        self.assertEqual(host, "ucla.global.remoterf.net")

    def test_valid_bracketed_ipv6(self):
        host, port = config_mod._parse_hostport("[2001:db8::1]:12321")
        self.assertEqual(host, "[2001:db8::1]")
        self.assertEqual(port, 12321)

    def test_scheme_prefix_is_stripped(self):
        host, port = config_mod._parse_hostport("grpc://ucla.global.remoterf.net:443")
        self.assertEqual(host, "ucla.global.remoterf.net")
        self.assertEqual(port, 443)

    def test_missing_port_is_invalid(self):
        with self.assertRaises(ValueError):
            config_mod._parse_hostport("ucla.global.remoterf.net")

    def test_empty_host_is_invalid(self):
        with self.assertRaises(ValueError):
            config_mod._parse_hostport(":12321")

    def test_port_out_of_range_is_invalid(self):
        with self.assertRaises(ValueError):
            config_mod._parse_hostport("192.168.1.20:99999")

    def test_non_integer_port_is_invalid(self):
        with self.assertRaises(ValueError):
            config_mod._parse_hostport("192.168.1.20:not-a-port")


class BareHostTests(unittest.TestCase):
    def test_ipv4_unchanged(self):
        self.assertEqual(config_mod._bare_host("192.168.1.20"), "192.168.1.20")

    def test_hostname_unchanged(self):
        self.assertEqual(
            config_mod._bare_host("ucla.global.remoterf.net"),
            "ucla.global.remoterf.net",
        )

    def test_ipv6_brackets_stripped(self):
        self.assertEqual(config_mod._bare_host("[2001:db8::1]"), "2001:db8::1")


class HostResolvesTests(unittest.TestCase):
    def test_resolvable_hostname_true(self):
        with mock.patch.object(config_mod.socket, "getaddrinfo", return_value=[]):
            self.assertTrue(config_mod._host_resolves("ucla.global.remoterf.net"))

    def test_unresolvable_hostname_false(self):
        with mock.patch.object(
            config_mod.socket,
            "getaddrinfo",
            side_effect=socket.gaierror("not known"),
        ):
            self.assertFalse(config_mod._host_resolves("this.does.not.exist.invalid"))

    def test_ipv6_literal_resolved_without_brackets(self):
        with mock.patch.object(config_mod.socket, "getaddrinfo", return_value=[]) as gai:
            self.assertTrue(config_mod._host_resolves("[2001:db8::1]"))
            gai.assert_called_once_with("2001:db8::1", None)


class ConfigurePreservesHostnameTests(unittest.TestCase):
    """`configure()` must store the hostname as configured, never a resolved IP."""

    def _run_configure(self, host, port=443, cert_port=444):
        written: dict[str, dict[str, str]] = {}

        def fake_write_env_kv(path, kv):
            written["kv"] = kv

        with mock.patch.object(config_mod, "_confirm_tos", return_value=True), \
             mock.patch.object(config_mod, "_host_resolves", return_value=True), \
             mock.patch.object(config_mod, "fetch_and_save_ca_cert", return_value=True) as fetch, \
             mock.patch.object(config_mod, "_write_env_kv", side_effect=fake_write_env_kv), \
             mock.patch.object(config_mod, "_print_config_summary"):
            config_mod.configure(host, port, cert_port)

        return written["kv"], fetch

    def test_hostname_is_preserved_in_saved_config(self):
        kv, fetch = self._run_configure("ucla.global.remoterf.net")

        self.assertEqual(kv["REMOTERF_ADDR"], "ucla.global.remoterf.net:443")
        # Certificate bootstrap must hit the same hostname + configured cert port.
        fetch.assert_called_once()
        called_args, called_kwargs = fetch.call_args
        self.assertEqual(called_args[0], "ucla.global.remoterf.net")
        self.assertEqual(called_args[1], 444)

    def test_ipv4_still_works(self):
        kv, _ = self._run_configure("192.168.1.20")
        self.assertEqual(kv["REMOTERF_ADDR"], "192.168.1.20:443")

    def test_unresolvable_hostname_blocks_configure(self):
        with mock.patch.object(config_mod, "_confirm_tos", return_value=True), \
             mock.patch.object(config_mod, "_host_resolves", return_value=False), \
             mock.patch.object(config_mod, "fetch_and_save_ca_cert") as fetch:
            rc = config_mod.configure("this.does.not.exist.invalid", 443, 444)

        self.assertEqual(rc, 1)
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
