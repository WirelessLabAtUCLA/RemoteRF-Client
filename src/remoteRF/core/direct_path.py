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
import atexit
import collections
import contextlib
import json
import os
import secrets
import socket
import threading
from typing import Callable, Optional

from aioice import Candidate, Connection
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic import events
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import QuicConnection

from ..common.grpc import grpc_pb2
from ..common.utils import map_arg, unmap_arg

ALPN = "remoterf-direct/1"
MAX_FRAME = 100 * 1024 * 1024  # the gRPC message ceiling
ICE_SECONDS = 5.0
QUIC_SECONDS = 3.0
SETTLE_SECONDS = 20.0  # gathering, the offer RPC and both budgets: one attempt, worst case
RETRY_SECONDS = 30.0
IDLE_SECONDS = 600
STUN = ("stun.l.google.com", 19302)
STUN_SECONDS = 1.5  # a STUN server answers in milliseconds or not at all


def enabled() -> bool:
    return os.getenv("REMOTERF_DIRECT", "1").strip().lower() not in {"0", "off", "no", "false"}


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


def default_route_address() -> Optional[str]:
    """The IPv4 address the default route leaves from, or None without one."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
    except OSError:
        return None


class Ice(Connection):
    """aioice's Connection, gathering on the default-route interface only.

    Every other interface (a VPN, an overlay, a bridge) cannot reach the STUN
    server, and aioice would hold gathering for its whole 5 s timeout on each
    one, once per attempt. One interface and a short STUN wait instead; a
    machine without a default route gathers the way aioice does.
    """

    async def gather_candidates(self) -> None:
        address = default_route_address()
        if address is None or self._local_candidates_start:
            return await super().gather_candidates()
        self._local_candidates_start = True
        self._local_candidates += await self.get_component_candidates(
            component=1, addresses=[address], timeout=STUN_SECONDS
        )
        self._local_candidates_end = True


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


class _Protocol(QuicConnectionProtocol):
    """aioquic's protocol, remembering why the connection ended."""

    terminated = ""

    def quic_event_received(self, event) -> None:
        if isinstance(event, events.ConnectionTerminated):
            self.terminated = event.reason_phrase or f"QUIC error {event.error_code:#x}"
        super().quic_event_received(event)


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
        self._wanted: Optional[asyncio.Event] = None  # set when a call needs an idle path back
        self._settled = threading.Event()  # the first attempt finished, either way
        self._stopping = False
        self.state = "connecting"  # connecting | ready | idle | down | unavailable | off
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
        if self.state == "idle":
            return "relay (direct path idle; reopens on the next device call)"
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

    def wake(self) -> None:
        """A device call wants the idle path back (it takes the relay meanwhile)."""
        self._loop.call_soon_threadsafe(lambda: self._wanted and self._wanted.set())

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
                if "Idle timeout" in self.reason:
                    # Both ends let an unused path go after IDLE_SECONDS; the
                    # next device call brings it back, nothing else does.
                    self.state = "idle"
                    self._wanted = asyncio.Event()
                    await self._wanted.wait()
                    continue
                await asyncio.sleep(RETRY_SECONDS)
        finally:
            self._settled.set()
            await self._teardown()

    async def _establish(self) -> None:
        loop = asyncio.get_running_loop()
        ice = self._ice = Ice(ice_controlling=True, stun_server=self._stun)
        await ice.gather_candidates()
        if self._stun and not any(c.type == "srflx" for c in ice.local_candidates):
            # Without a reflexive address there is nothing to punch with, and a
            # network that drops STUN drops the checks too: skip the 5 s of them.
            raise PathError("no STUN answer (UDP blocked?)")
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
        protocol = _Protocol(QuicConnection(configuration=configuration))
        self._transport = _IceTransport(ice)
        protocol.connection_made(self._transport)
        self._tasks = [
            asyncio.ensure_future(self._pump(ice, protocol, peer)),
            asyncio.ensure_future(self._watch(protocol)),
        ]
        protocol.connect(peer)
        try:
            await asyncio.wait_for(protocol.wait_connected(), QUIC_SECONDS)
        except ConnectionError:
            raise ConnectionError(f"QUIC handshake refused: {protocol.terminated}") from None
        self._protocol, self.remote = protocol, peer

    async def _pump(self, ice: Connection, protocol: QuicConnectionProtocol, peer) -> None:
        try:
            while True:
                data, _ = await ice.recvfrom()
                protocol.datagram_received(data, peer)
        except ConnectionError:
            self._die("UDP path lost")

    async def _watch(self, protocol: _Protocol) -> None:
        await protocol.wait_closed()
        self._die(f"connection closed: {protocol.terminated}")

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


# ---- this process's path ----

_path: Optional[DirectPath] = None
_resolved = False  # whether this process has decided about its path
_lock = threading.Lock()


def _offer_rpc(username: str, secret: str) -> Callable[[dict], dict]:
    def send(offer: dict) -> dict:
        from .grpc_client import rpc_client

        response = rpc_client(function_name="ACC:direct_offer", args={
            "un": map_arg(username), "pw": map_arg(secret), "ice": map_arg(json.dumps(offer)),
        })
        if "ace" in response.results:
            raise PathError(unmap_arg(response.results["ace"]))
        return json.loads(unmap_arg(response.results["ice"]))

    return send


def start(*, username: str, secret: str, route: dict) -> Optional[DirectPath]:
    """After a login: punch in the background when this home is reached over its relay.

    A LAN or manually configured route is already direct, so nothing is done."""
    global _path, _resolved
    stop()
    _resolved = True
    if not enabled() or route.get("kind") != "relay":
        return None
    from .grpc_client import _current_profile

    _path = DirectPath(
        offer=_offer_rpc(username, secret), server_name=route["host"],
        ca_pem=_current_profile().ca_path.read_bytes(),
    )
    _path.start()
    return _path


def _from_stored_login() -> Optional[DirectPath]:
    """A path for a process that never logged in (a script using a driver)."""
    from ..deployment import homes
    from .grpc_client import _current_profile

    profile = _current_profile()
    if not profile.home:
        return None
    route = next(
        (r for r in homes.load_home(profile.home)["routes"]
         if f"{r['host']}:{r['port']}" == profile.grpc_endpoint),
        None,
    )
    login = homes.recall_login(profile.home)
    if route is None or route["kind"] != "relay" or login is None:
        return None
    return DirectPath(
        offer=_offer_rpc(login["username"], login["secret"]), server_name=route["host"],
        ca_pem=profile.ca_path.read_bytes(),
    )


def current() -> Optional[DirectPath]:
    """This process's path when it is ready, else None.

    The first call decides: a process that did not log in (a script) uses the
    stored login of the active home and waits once for the first attempt, so
    its very first capture already takes the direct path when there is one.
    """
    global _path, _resolved
    if not enabled():
        return None
    if not _resolved:
        with _lock:
            if not _resolved:
                _resolved = True
                try:
                    _path = _from_stored_login()
                except Exception:  # noqa: BLE001 - no path is always an option
                    _path = None
                if _path is not None:
                    _path.start()
                    _path.wait()
    path = _path
    if path is not None and path.state == "idle":
        path.wake()
    return path if path is not None and path.ready else None


def describe() -> str:
    """What device calls use right now, for the shell."""
    if not enabled():
        return "relay (direct path disabled: REMOTERF_DIRECT=0)"
    path = _path
    return path.describe() if path is not None else "relay (direct path off)"


def stop() -> None:
    global _path
    path, _path = _path, None
    if path is not None:
        path.stop()


atexit.register(stop)
