"""The host TCP relay fallback after direct QUIC paths fail."""

from __future__ import annotations

import asyncio
import json
import ssl
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from remoterf.common.grpc import grpc_pb2
from remoterf.common.utils import map_arg, unmap_arg
from remoterf.core import direct_path

from test_gate_i_direct_path import write_certs


SERVER_NAME = "lab-host--lab.global.remoterf.net"


class RelayServer:
    def __init__(self, cert_dir: Path, *, accept=True):
        self.accept = accept
        self.hellos, self.calls = [], []
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_dir / "server.crt", cert_dir / "server.key")
        try:
            self.server = asyncio.run_coroutine_threadsafe(
                asyncio.start_server(self._serve, "127.0.0.1", 0, ssl=context), self.loop
            ).result(5)
        except PermissionError:
            self.loop.call_soon_threadsafe(self.loop.stop)
            raise unittest.SkipTest("sandbox does not permit loopback TCP listeners") from None
        self.port = self.server.sockets[0].getsockname()[1]

    async def _serve(self, reader, writer):
        try:
            self.hellos.append(json.loads(await direct_path.read_frame(reader)))
            reply = {"ok": True} if self.accept else {"a": "unknown session"}
            writer.write(direct_path.frame(json.dumps(reply).encode()))
            await writer.drain()
            if not self.accept:
                return
            while True:
                request = grpc_pb2.GenericRPCRequest()
                request.ParseFromString(await direct_path.read_frame(reader))
                self.calls.append(request.function_name)
                response = grpc_pb2.GenericRPCResponse(results={"echo": map_arg(request.function_name)})
                writer.write(direct_path.frame(response.SerializeToString()))
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()

    def close(self):
        self.loop.call_soon_threadsafe(self.server.close)
        self.loop.call_soon_threadsafe(self.loop.stop)


class RelayPathTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ca_pem = write_certs(Path(self._tmp.name), SERVER_NAME)

    def _path(self, answer):
        path = direct_path.DirectPath(offer=lambda offer: answer, server_name=SERVER_NAME, ca_pem=self.ca_pem, stun=None)
        self.addCleanup(path.stop)
        path.start()
        return path

    def _answer(self, server):
        return {
            "ufrag": "a", "pwd": "b", "candidates": [],
            "relay": {"endpoint": f"127.0.0.1:{server.port}", "session": "once"},
        }

    async def _failed_punch(self, ice, answer, configuration):
        raise ConnectionError("ICE negotiation failed")

    def test_failed_punch_uses_host_relay_for_serial_calls(self):
        server = RelayServer(Path(self._tmp.name))
        self.addCleanup(server.close)
        with mock.patch.object(direct_path.DirectPath, "_punch", self._failed_punch):
            path = self._path(self._answer(server))
            self.assertTrue(path.wait(5), path.describe())
        self.assertEqual(path.describe(), f"host relay {SERVER_NAME}")
        response = path.call("pluto:rx_lo:GET", {"a": map_arg("token")})
        self.assertEqual(unmap_arg(response.results["echo"]), "pluto:rx_lo:GET")
        self.assertEqual(server.hellos, [{"session": "once"}])
        self.assertEqual(server.calls, ["pluto:rx_lo:GET"])

    def test_refused_relay_leaves_calls_on_the_usual_relay(self):
        server = RelayServer(Path(self._tmp.name), accept=False)
        self.addCleanup(server.close)
        with mock.patch.object(direct_path.DirectPath, "_punch", self._failed_punch):
            path = self._path(self._answer(server))
            self.assertFalse(path.wait(5))
        self.assertEqual(path.state, "down")
        self.assertTrue(path.describe().startswith("relay (direct path down"))
        self.assertEqual(server.hellos, [{"session": "once"}])

    def test_answer_without_relay_never_dials_one(self):
        answer = {"ufrag": "a", "pwd": "b", "candidates": []}
        with mock.patch.object(direct_path.DirectPath, "_punch", self._failed_punch), \
             mock.patch.object(direct_path.DirectPath, "_dial_relay") as dial:
            path = self._path(answer)
            self.assertFalse(path.wait(5))
        dial.assert_not_called()

