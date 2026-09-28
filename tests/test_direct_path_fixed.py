"""The fixed port: when the answer names one, the path dials it and claims
its session; ICE is only for Servers that cannot be reached that way."""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path

from aioquic.asyncio import serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration

from remoteRF.common.grpc import grpc_pb2
from remoteRF.common.utils import map_arg, unmap_arg
from remoteRF.core import direct_path

from test_gate_i_direct_path import SERVER_NAME, write_certs


class FixedServer:
    """A Server's fixed port, in-process: answers the hello, echoes calls."""

    def __init__(self, cert_dir: Path, accept="s3cret"):
        self.accept = accept
        self.hellos, self.calls = [], []
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        configuration = QuicConfiguration(is_client=False, alpn_protocols=[direct_path.ALPN], idle_timeout=60)
        configuration.load_cert_chain(cert_dir / "server.crt", cert_dir / "server.key")
        self.server = asyncio.run_coroutine_threadsafe(
            serve("127.0.0.1", 0, configuration=configuration, create_protocol=QuicConnectionProtocol,
                  stream_handler=self._stream), self.loop).result(5)
        self.port = self.server._transport.get_extra_info("sockname")[1]

    def _stream(self, reader, writer):
        asyncio.ensure_future(self._serve(reader, writer))

    async def _serve(self, reader, writer):
        payload = await direct_path.read_frame(reader)
        try:
            hello = json.loads(payload)
        except ValueError:
            request = grpc_pb2.GenericRPCRequest(); request.ParseFromString(payload)
            self.calls.append(request.function_name)
            writer.write(direct_path.frame(grpc_pb2.GenericRPCResponse(results={"echo": map_arg(request.function_name)}).SerializeToString()))
        else:
            self.hellos.append(hello)
            ok = hello.get("session") == self.accept
            writer.write(direct_path.frame(json.dumps({"ok": True} if ok else {"a": "unknown session"}).encode()))
        writer.write_eof()

    def close(self):
        self.loop.call_soon_threadsafe(self.server.close)
        self.loop.call_soon_threadsafe(self.loop.stop)


class FixedPathTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ca_pem = write_certs(Path(self._tmp.name))

    def _path(self, server, session="s3cret"):
        answer = {"ufrag": "abcd", "pwd": "0123456789012345678901", "candidates": [],
                  "udp": f"127.0.0.1:{server.port}", "session": session}
        path = direct_path.DirectPath(offer=lambda offer: answer, server_name=SERVER_NAME, ca_pem=self.ca_pem, stun=None)
        path.start()
        self.addCleanup(path.stop)
        return path

    def test_the_fixed_port_is_dialed_claimed_and_carries_calls(self):
        server = FixedServer(Path(self._tmp.name))
        self.addCleanup(server.close)
        path = self._path(server)
        self.assertTrue(path.wait(15), path.describe())
        self.assertTrue(path.fixed)
        self.assertEqual(path.remote, ("127.0.0.1", server.port))
        self.assertIn("fixed port", path.describe())
        self.assertEqual(server.hellos, [{"session": "s3cret"}])
        response = path.call("adalm_pluto:rx_lo:GET", {"a": map_arg("tok")})
        self.assertEqual(unmap_arg(response.results["echo"]), "adalm_pluto:rx_lo:GET")
        self.assertEqual(server.calls, ["adalm_pluto:rx_lo:GET"])

    def test_a_refused_session_falls_back_to_the_punch(self):
        server = FixedServer(Path(self._tmp.name), accept="other")
        self.addCleanup(server.close)
        path = self._path(server)
        self.assertFalse(path.wait(15))  # then the punch, with nothing to punch: down, not ready
        self.assertFalse(path.fixed)
        self.assertEqual(path.state, "down")
        self.assertEqual(server.hellos, [{"session": "s3cret"}])
