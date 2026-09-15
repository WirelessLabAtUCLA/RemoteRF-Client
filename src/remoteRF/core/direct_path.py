# Copyright (C) 2026 RemoteRF
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Gate I: the client end of the direct UDP device path.

On a home reached over its relay, ICE (aioice) punches one UDP pair to the
Server and QUIC (aioquic) runs over it; device RPCs then travel as one stream
per call (4-byte big-endian length + GenericRPCRequest, reply likewise)
instead of through the relay. Control RPCs (ACC:*) stay on gRPC. When the
punch fails or the path dies, calls fall back to gRPC and the path is retried
every 30 s. Signaling is ACC:direct_offer over the channel the client already
has. See RemoteRF-Server docs/gate-i-direct-path.md.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import secrets
import threading
from typing import Callable, Optional

from aioice import Candidate, Connection
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import QuicConnection

from ..common.grpc import grpc_pb2

ALPN = "remoterf-direct/1"
MAX_FRAME = 100 * 1024 * 1024  # the gRPC message ceiling
ICE_SECONDS = 5.0
QUIC_SECONDS = 3.0
SETTLE_SECONDS = 20.0  # gathering, the offer RPC and both budgets: one attempt, worst case
RETRY_SECONDS = 30.0
IDLE_SECONDS = 600
STUN = ("stun.l.google.com", 19302)


def wants(function_name: str) -> bool:
    """The device plane: <type>:<prop>:GET|SET and IDL:* (the Server's own rule)."""
    parts = function_name.split(":", 2)
    return function_name.startswith("IDL:") or (len(parts) == 3 and all(parts))


def frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + payload


async def read_frame(reader: asyncio.StreamReader) -> bytes:
    size = int.from_bytes(await reader.readexactly(4), "big")
    if size > MAX_FRAME:
        raise ValueError(f"frame of {size} bytes exceeds {MAX_FRAME}")
    return await reader.readexactly(size)


def peer_address(ice: Connection) -> tuple[str, int]:
    """The remote end of the nominated pair (aioice keeps it private)."""
    return ice._nominated[1].remote_addr


class PathError(Exception):
    """The direct path could not carry this call and is down now."""


class _IceTransport:
    """The sendto() surface aioquic's protocol needs, on a nominated ICE pair."""

    def __init__(self, ice: Connection):
        self._ice = ice
        self._loop = asyncio.get_running_loop()
        self._queue: collections.deque[bytes] = collections.deque()
        self._pump: Optional[asyncio.Task] = None
        self._closed = False

    def sendto(self, data: bytes, addr=None) -> None:
        if self._closed:
            return
        self._queue.append(data)
        if self._pump is None:
            self._pump = self._loop.create_task(self._drain())

    def close(self) -> None:
        """Drop the transport: QUIC's later timers must not touch a dead pair."""
        self._closed = True
        self._queue.clear()

    async def _drain(self) -> None:
        try:
            while self._queue:
                await self._ice.sendto(self._queue.popleft(), 1)
        except ConnectionError:
            self._queue.clear()
        finally:
            self._pump = None


class DirectPath:
    """One process's path to the Server, kept up from its own thread.

    ``offer`` sends the ICE offer dict and returns the answer dict (it runs
    the ACC:direct_offer RPC, blocking). ``server_name`` is the hostname the
    Server's certificate must carry; ``ca_pem`` the CA pinned for this home.
    """

    def __init__(self, *, offer: Callable[[dict], dict], server_name: str, ca_pem: bytes,
                 stun: Optional[tuple[str, int]] = STUN):
        self._offer = offer
        self._server_name = server_name
        self._ca_pem = ca_pem
        self._stun = stun
        self._id = secrets.token_hex(8)  # tells this process's path from the session's others
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="remoterf-direct", daemon=True
        )
        self._task: Optional[asyncio.Task] = None
        self._tasks: list[asyncio.Task] = []
        self._ice: Optional[Connection] = None
        self._transport: Optional[_IceTransport] = None
        self._protocol: Optional[QuicConnectionProtocol] = None
        self._dead: Optional[asyncio.Event] = None
        self._settled = threading.Event()  # the first attempt finished, either way
        self._stopping = False
        self.state = "connecting"  # connecting | ready | down | unavailable | off
        self.reason = ""
        self.remote: Optional[tuple[str, int]] = None
        self.calls = 0

    # ---- the synchronous side (any thread) ----

    def start(self) -> None:
        self._thread.start()
        self._loop.call_soon_threadsafe(self._spawn)

    def _spawn(self) -> None:
        self._task = self._loop.create_task(self._run())

    def wait(self, timeout: float = SETTLE_SECONDS) -> bool:
        """Block until the first attempt has settled; True when the path is ready."""
        self._settled.wait(timeout)
        return self.ready

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def describe(self) -> str:
        if self.state == "ready":
            calls = f" ({self.calls} calls)" if self.calls else ""
            return f"direct {self.remote[0]}:{self.remote[1]}{calls}"
        if self.state == "connecting":
            return "relay (direct path: connecting)"
        if self.state == "off":
            return "relay (direct path off)"
        if self.state == "unavailable":
            return f"relay ({self.reason})"
        return f"relay (direct path down: {self.reason}; retrying)"

    def call(self, function_name: str, args) -> grpc_pb2.GenericRPCResponse:
        """Carry one device RPC; raises PathError when the path cannot, marking it down."""
        request = grpc_pb2.GenericRPCRequest(function_name=function_name, args=args)
        future = asyncio.run_coroutine_threadsafe(self._call(request.SerializeToString()), self._loop)
        try:
            raw = future.result()
        except (Exception, asyncio.CancelledError) as exc:
            self._loop.call_soon_threadsafe(self._die, f"{type(exc).__name__}: {exc}")
            raise PathError(exc) from exc
        response = grpc_pb2.GenericRPCResponse()
        response.ParseFromString(raw)
        self.calls += 1
        return response

    def stop(self) -> None:
        """Close the path and the thread; the instance is finished."""
        self._stopping = True
        self.state = "off"

        async def cancel() -> None:
            if self._task is not None:
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._task
            for task in asyncio.all_tasks():
                if task is not asyncio.current_task():
                    task.cancel()

        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(cancel(), self._loop).result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)

    # ---- the asyncio side (the path thread) ----

    async def _run(self) -> None:
        try:
            while not self._stopping:
                self._dead = asyncio.Event()
                try:
                    await asyncio.wait_for(self._establish(), SETTLE_SECONDS)
                except Exception as exc:  # noqa: BLE001 - a failed punch is expected on some networks
                    self.reason = (
                        "timed out" if isinstance(exc, asyncio.TimeoutError)
                        else f"{type(exc).__name__}: {exc}"
                    )
                    self.state = "down"
                    if "No matching server function" in self.reason:
                        self.state = "unavailable"
                        self.reason = "this Server has no direct path"
                        return
                else:
                    self.state = "ready"
                    self._settled.set()
                    await self._dead.wait()
                    self.state = "down"
                self._settled.set()
                await self._teardown()
                await asyncio.sleep(RETRY_SECONDS)
        finally:
            self._settled.set()
            await self._teardown()

    async def _establish(self) -> None:
        loop = asyncio.get_running_loop()
        ice = self._ice = Connection(ice_controlling=True, stun_server=self._stun)
        await ice.gather_candidates()
        offer = {
            "id": self._id,
            "ufrag": ice.local_username,
            "pwd": ice.local_password,
            "candidates": [candidate.to_sdp() for candidate in ice.local_candidates],
        }
        answer = await loop.run_in_executor(None, self._offer, offer)
        ice.remote_username = str(answer["ufrag"])
        ice.remote_password = str(answer["pwd"])
        for sdp in answer["candidates"]:
            await ice.add_remote_candidate(Candidate.from_sdp(str(sdp)))
        await ice.add_remote_candidate(None)
        await asyncio.wait_for(ice.connect(), ICE_SECONDS)
        peer = peer_address(ice)

        configuration = QuicConfiguration(
            is_client=True, alpn_protocols=[ALPN], server_name=self._server_name,
            idle_timeout=IDLE_SECONDS,
        )
        configuration.load_verify_locations(cadata=self._ca_pem)
        protocol = QuicConnectionProtocol(QuicConnection(configuration=configuration))
        self._transport = _IceTransport(ice)
        protocol.connection_made(self._transport)
        self._tasks = [
            asyncio.ensure_future(self._pump(ice, protocol, peer)),
            asyncio.ensure_future(self._watch(protocol)),
        ]
        protocol.connect(peer)
        await asyncio.wait_for(protocol.wait_connected(), QUIC_SECONDS)
        self._protocol, self.remote = protocol, peer

    async def _pump(self, ice: Connection, protocol: QuicConnectionProtocol, peer) -> None:
        try:
            while True:
                data, _ = await ice.recvfrom()
                protocol.datagram_received(data, peer)
        except ConnectionError:
            self._die("UDP path lost")

    async def _watch(self, protocol: QuicConnectionProtocol) -> None:
        await protocol.wait_closed()
        self._die("closed by the Server")

    def _die(self, reason: str) -> None:
        if self.state == "ready":
            self.reason = reason
        if self._dead is not None:
            self._dead.set()

    async def _call(self, payload: bytes) -> bytes:
        protocol = self._protocol
        if protocol is None or self.state != "ready":
            raise ConnectionError("direct path is down")
        reader, writer = await protocol.create_stream()
        writer.write(frame(payload))
        writer.write_eof()
        reply = asyncio.ensure_future(read_frame(reader))
        dead = asyncio.ensure_future(self._dead.wait())
        done, _ = await asyncio.wait({reply, dead}, return_when=asyncio.FIRST_COMPLETED)
        dead.cancel()
        if reply not in done:
            reply.cancel()
            raise ConnectionError("direct path died mid-call")
        return reply.result()

    async def _teardown(self) -> None:
        if self._dead is not None:
            self._dead.set()  # an in-flight call fails now rather than hanging
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        if self._protocol is not None:
            self._protocol.close()
            self._protocol = None
            await asyncio.sleep(0)  # the CONNECTION_CLOSE leaves before the pair goes
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        if self._ice is not None:
            await self._ice.close()
            self._ice = None
        self.remote = None
