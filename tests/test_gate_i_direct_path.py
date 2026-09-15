"""Gate I: the direct device path -- ICE + QUIC on this machine with both ends
in-process, and framing."""

from __future__ import annotations

import asyncio
import datetime
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from aioice import Candidate, Connection
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.buffer import Buffer
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import QuicConnection
from aioquic.quic.packet import pull_quic_header
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from remoteRF.common.grpc import grpc_pb2
from remoteRF.common.utils import map_arg, unmap_arg
from remoteRF.core import direct_path

SERVER_NAME = "direct.test"


def write_certs(cert_dir: Path) -> bytes:
    """A throwaway CA and a server certificate for SERVER_NAME; returns the CA PEM."""
    now = datetime.datetime.now(datetime.timezone.utc)

    def cert(subject, issuer, key, signer, *, ca):
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        )
        if not ca:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.DNSName(SERVER_NAME)]), critical=False
            )
        return builder.sign(signer, hashes.SHA256())

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "RemoteRF test CA")])
    ca_cert = cert(ca_name, ca_name, ca_key, ca_key, ca=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = cert(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SERVER_NAME)]),
        ca_name, key, ca_key, ca=False,
    )
    (cert_dir / "server.crt").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (cert_dir / "server.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca_cert.public_bytes(serialization.Encoding.PEM)


class FakeServer:
    """The Server end of one path, in-process: answers the offer, serves streams."""

    def __init__(self, cert_dir: Path, handler):
        self.cert_dir = cert_dir
        self.handler = handler  # GenericRPCRequest -> results dict, on a worker thread
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.paths = []  # (ice, transport) per answered offer
        self.offers = 0

    def offer(self, offer: dict) -> dict:
        self.offers += 1
        return asyncio.run_coroutine_threadsafe(self._answer(offer), self.loop).result(15)

    async def _answer(self, offer: dict) -> dict:
        ice = Connection(ice_controlling=False, use_ipv6=False)
        await ice.gather_candidates()
        ice.remote_username = offer["ufrag"]
        ice.remote_password = offer["pwd"]
        for sdp in offer["candidates"]:
            await ice.add_remote_candidate(Candidate.from_sdp(sdp))
        await ice.add_remote_candidate(None)
        asyncio.ensure_future(self._serve(ice))
        return {
            "ufrag": ice.local_username,
            "pwd": ice.local_password,
            "candidates": [c.to_sdp() for c in ice.local_candidates],
        }

    async def _serve(self, ice: Connection) -> None:
        await ice.connect()
        peer = direct_path.peer_address(ice)
        transport = direct_path._IceTransport(ice)
        self.paths.append((ice, transport))
        protocol = None
        while True:
            try:
                data, _ = await ice.recvfrom()
            except ConnectionError:
                transport.close()
                return
            if protocol is None:
                header = pull_quic_header(Buffer(data=data), host_cid_length=8)
                configuration = QuicConfiguration(is_client=False, alpn_protocols=[direct_path.ALPN])
                configuration.load_cert_chain(self.cert_dir / "server.crt", self.cert_dir / "server.key")
                protocol = QuicConnectionProtocol(
                    QuicConnection(
                        configuration=configuration,
                        original_destination_connection_id=header.destination_cid,
                    ),
                    stream_handler=lambda r, w: asyncio.ensure_future(self._reply(r, w)),
                )
                protocol.connection_made(transport)
            protocol.datagram_received(data, peer)

    async def _reply(self, reader, writer) -> None:
        request = grpc_pb2.GenericRPCRequest()
        request.ParseFromString(await direct_path.read_frame(reader))
        results = await asyncio.get_running_loop().run_in_executor(None, self.handler, request)
        writer.write(direct_path.frame(grpc_pb2.GenericRPCResponse(results=results).SerializeToString()))
        writer.write_eof()

    def close(self) -> None:
        async def finish():
            for ice, transport in self.paths:
                transport.close()
                await ice.close()
            for task in asyncio.all_tasks():
                if task is not asyncio.current_task():
                    task.cancel()

        asyncio.run_coroutine_threadsafe(finish(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)


class RulesTests(unittest.TestCase):
    def test_wants_only_the_device_plane(self):
        for name in ("adalm_pluto:rx:GET", "adalm_pluto:rx_lo:SET", "IDL:get_drivers", "IDL:x"):
            self.assertTrue(direct_path.wants(name), name)
        for name in ("ACC:login", "ACC:direct_offer", "echo", "RemoteAdmin:x", "a::GET", "x:y"):
            self.assertFalse(direct_path.wants(name), name)

    def test_framing_round_trip(self):
        async def run():
            reader = asyncio.StreamReader()
            reader.feed_data(direct_path.frame(b"abc") + direct_path.frame(b""))
            return await direct_path.read_frame(reader), await direct_path.read_frame(reader)

        self.assertEqual(asyncio.run(run()), (b"abc", b""))


class LoopbackPathTests(unittest.TestCase):
    """Both ends in this process, no STUN: punch, handshake against the pinned
    CA, a 1 M-sample reply, death, fallback and re-establishment."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cert_dir = Path(self._tmp.name)
        self.ca_pem = write_certs(self.cert_dir)
        self.samples = (np.random.default_rng(1).standard_normal(1_000_000)
                        + 1j * np.random.default_rng(2).standard_normal(1_000_000))
        self.seen = []

    def handler(self, request):
        self.seen.append(request.function_name)
        if request.function_name.endswith(":rx:GET"):
            return {"rx": map_arg(self.samples)}
        if request.function_name.endswith(":slow:GET"):
            time.sleep(5)
        return {"echo": map_arg(unmap_arg(request.args["a"]))}

    def _start(self, server_name=SERVER_NAME, ca_pem=None):
        server = FakeServer(self.cert_dir, self.handler)
        self.addCleanup(server.close)
        path = direct_path.DirectPath(
            offer=server.offer, server_name=server_name, ca_pem=ca_pem or self.ca_pem, stun=None
        )
        self.addCleanup(path.stop)
        path.start()
        return server, path

    def test_punch_call_die_fallback_reestablish(self):
        server, path = self._start()
        self.assertTrue(path.wait(15), path.describe())
        self.assertTrue(path.describe().startswith("direct "), path.describe())

        response = path.call("adalm_pluto:rx_lo:GET", {"a": map_arg("tok")})
        self.assertEqual(unmap_arg(response.results["echo"]), "tok")
        started = time.monotonic()
        response = path.call("adalm_pluto:rx:GET", {"a": map_arg("tok")})
        elapsed = time.monotonic() - started
        got = unmap_arg(response.results["rx"])
        self.assertTrue(np.array_equal(got, self.samples))
        self.assertLess(elapsed, 20, "1 M complex128 samples over the loopback path")
        self.assertEqual(path.calls, 2)

        # The path dies under a call in flight: the call fails at once, not at
        # the QUIC idle timeout, and the path is down for the next caller.
        with mock.patch.object(direct_path, "RETRY_SECONDS", 0.5):
            outcome = {}

            def slow_call():
                try:
                    path.call("adalm_pluto:slow:GET", {"a": map_arg("tok")})
                except direct_path.PathError as exc:
                    outcome["error"] = exc

            worker = threading.Thread(target=slow_call)
            worker.start()
            time.sleep(0.5)
            asyncio.run_coroutine_threadsafe(path._ice.close(), path._loop).result(5)
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertIn("error", outcome)
            self.assertFalse(path.ready)
            self.assertIn("relay", path.describe())

            # ... and it comes back on its own, with a fresh offer.
            deadline = time.monotonic() + 15
            while not path.ready and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertTrue(path.ready, path.describe())
            self.assertEqual(server.offers, 2)
            response = path.call("adalm_pluto:rx_lo:GET", {"a": map_arg("again")})
            self.assertEqual(unmap_arg(response.results["echo"]), "again")

    def test_wrong_name_or_ca_never_becomes_ready(self):
        import tempfile

        _, path = self._start(server_name="other.test")
        self.assertFalse(path.wait(15))
        self.assertIn("relay", path.describe())
        with tempfile.TemporaryDirectory() as other:
            other_ca = write_certs(Path(other))  # the server keeps its own cert
        _, path = self._start(ca_pem=other_ca)
        self.assertFalse(path.wait(15))

    def test_a_refused_offer_stays_on_the_relay(self):
        def refuse(offer):
            raise direct_path.PathError("No matching server function to call: direct_offer")

        path = direct_path.DirectPath(offer=refuse, server_name=SERVER_NAME, ca_pem=self.ca_pem, stun=None)
        self.addCleanup(path.stop)
        path.start()
        self.assertFalse(path.wait(15))
        self.assertEqual(path.state, "unavailable")
        self.assertEqual(path.describe(), "relay (this Server has no direct path)")


if __name__ == "__main__":
    unittest.main()
