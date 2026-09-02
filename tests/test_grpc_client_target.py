import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

FAKE_PEM = (
    b"-----BEGIN CERTIFICATE-----\n"
    b"ZmFrZS1jZXJ0LWZvci10ZXN0aW5nLW9ubHk=\n"
    b"-----END CERTIFICATE-----\n"
)


class GrpcClientTargetTests(unittest.TestCase):
    """A DNS hostname such as ucla.global.remoterf.net must be used verbatim
    as the gRPC channel target -- the client never resolves it to an IP and
    substitutes that instead.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._ca_path = Path(self._tmpdir.name) / "ca.crt"
        self._ca_path.write_bytes(FAKE_PEM)

        self._saved_module = sys.modules.pop("remoteRF.core.grpc_client", None)
        self.addCleanup(self._restore_module)

    def _restore_module(self):
        sys.modules.pop("remoteRF.core.grpc_client", None)
        if self._saved_module is not None:
            sys.modules["remoteRF.core.grpc_client"] = self._saved_module

    def _import_with_env(self, addr, tls_server_name=None):
        env = {
            "REMOTERF_ADDR": addr,
            "REMOTERF_CA_CERT": str(self._ca_path),
        }
        if tls_server_name is not None:
            env["REMOTERF_TLS_SERVER_NAME"] = tls_server_name

        with mock.patch.dict("os.environ", env, clear=False), \
             mock.patch("grpc.secure_channel") as secure_channel, \
             mock.patch("grpc.ssl_channel_credentials", return_value="fake-creds"), \
             mock.patch("dotenv.load_dotenv"):
            module = importlib.import_module("remoteRF.core.grpc_client")
        return module, secure_channel

    def test_dns_hostname_used_verbatim_as_target(self):
        module, secure_channel = self._import_with_env(
            "ucla.global.remoterf.net:12321"
        )

        self.assertEqual(module.addr, "ucla.global.remoterf.net:12321")
        secure_channel.assert_called_once()
        target = secure_channel.call_args[0][0]
        self.assertEqual(target, "ucla.global.remoterf.net:12321")

    def test_ipv4_target_unchanged(self):
        module, secure_channel = self._import_with_env("192.168.1.20:12321")

        self.assertEqual(module.addr, "192.168.1.20:12321")
        target = secure_channel.call_args[0][0]
        self.assertEqual(target, "192.168.1.20:12321")


if __name__ == "__main__":
    unittest.main()
