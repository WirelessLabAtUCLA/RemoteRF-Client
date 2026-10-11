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

import os
import re
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import grpc
from ..common.grpc import grpc_pb2
from ..common.grpc import grpc_pb2_grpc
from ..common.utils import *
from ..common.utils.process_arg import IQ16, fits_iq16, map_iq16
from .secure_channel import build_secure_channel
from . import direct_path
from ..deployment.direct import ConnectionProfile, DirectConnectionProfile, resolve_active_profile

_CONFIG_HELP = (
    "Run:\n"
    "  remoterf --config --addr <host:port>\n"
    "Example:\n"
    "  remoterf --config --addr 203.0.113.10:12321"
)


@dataclass
class _ActiveConnection:
    profile_key: tuple[str, ...]
    endpoint: str
    channel: grpc.Channel
    stub: grpc_pb2_grpc.GenericRPCStub


_active_connection: Optional[_ActiveConnection] = None

# Compatibility names. They are populated only when a caller asks for the
# active connection, rather than forcing direct-mode configuration at import.
addr = ""
channel: Optional[grpc.Channel] = None
stub: Optional[grpc_pb2_grpc.GenericRPCStub] = None


def _legacy_environment_profile() -> Optional[DirectConnectionProfile]:
    """Support callers that supplied the historic env variables explicitly.

    Normal installs use ``profile.load_direct_profile`` and its `.env` file;
    this fallback keeps scripted/direct callers working without making a
    module import read or mutate process configuration.
    """
    endpoint = (os.getenv("REMOTERF_ADDR") or "").strip().strip('"').strip("'")
    ca_value = (os.getenv("REMOTERF_CA_CERT") or "").strip().strip('"').strip("'")
    if not endpoint or not ca_value:
        return None
    return DirectConnectionProfile(
        grpc_endpoint=endpoint,
        tls_server_name=(os.getenv("REMOTERF_TLS_SERVER_NAME") or "").strip() or None,
        ca_path=Path(ca_value).expanduser(),
    )


def _current_profile() -> ConnectionProfile:
    # Native process configuration remains an explicit device transport override.
    from ..deployment.state import load_target
    target = load_target()
    if target and target['transport'] == 'https-json' and _legacy_environment_profile() is None:
        raise RuntimeError('Selected HTTPS account home has no local device transport')
    profile = resolve_active_profile()
    env_profile = _legacy_environment_profile()
    if profile is None or (profile.mode == "direct" and env_profile is not None):
        profile = env_profile or profile
    if profile is None:
        raise RuntimeError(
            "RemoteRF client is not configured.\n"
            "Expected REMOTERF_ADDR and REMOTERF_CA_CERT in:\n"
            f"  {Path.home() / '.config' / 'remoterf-client' / '.env'}\n{_CONFIG_HELP}"
        )
    if not profile.ca_path.exists():
        raise RuntimeError(
            "RemoteRF client config points to a missing CA certificate.\n"
            f"Re-run RemoteRF client config or select the deployment again.\n{_CONFIG_HELP}"
        )
    return profile


def _profile_key(profile: ConnectionProfile) -> tuple[str, ...]:
    ca_stat = profile.ca_path.stat()
    deployment_id = getattr(profile, "deployment_id", "")
    return (
        profile.mode,
        deployment_id,
        profile.grpc_endpoint,
        profile.tls_server_name or "",
        str(profile.ca_path.resolve()),
        str(ca_stat.st_mtime_ns),
        str(ca_stat.st_size),
    )


def close_active_connection() -> None:
    """Close the cached channel, including when the selected profile changes."""
    global _active_connection, addr, channel, stub
    if _active_connection is not None:
        _active_connection.channel.close()
    _active_connection = None
    addr = ""
    channel = None
    stub = None


def get_active_connection() -> _ActiveConnection:
    """Resolve the active profile lazily and replace stale channels safely."""
    global _active_connection, addr, channel, stub
    profile = _current_profile()
    key = _profile_key(profile)
    if _active_connection is not None and _active_connection.profile_key == key:
        return _active_connection

    close_active_connection()
    trusted_certs = profile.ca_path.read_bytes()
    selected_channel = build_secure_channel(
        profile.grpc_endpoint,
        trusted_certs,
        tls_server_name=profile.tls_server_name,
    )
    selected_stub = grpc_pb2_grpc.GenericRPCStub(selected_channel)
    _active_connection = _ActiveConnection(
        profile_key=key,
        endpoint=profile.grpc_endpoint,
        channel=selected_channel,
        stub=selected_stub,
    )
    addr = _active_connection.endpoint
    channel = _active_connection.channel
    stub = _active_connection.stub
    return _active_connection


def get_active_channel() -> grpc.Channel:
    return get_active_connection().channel


def active_endpoint() -> str:
    return get_active_connection().endpoint

tcp_calls = 0

def get_tcp_calls():
    return tcp_calls


# ── federated device relay ───────────────────────────────────────────
#
# At an HTTPS account home there is no direct device transport.  A driver
# constructed with a HOME-minted ``<deployment_id>:<local_id>`` reference
# instead of a Server bearer token has its GenericRPCRequest serialized and
# relayed through the HOME; the response is the destination's ordinary
# GenericRPCResponse, so generated drivers do not know the difference.

_federated_backend = None
# A direct-path thread may ask at the same moment: two backends would each
# refresh the stored session, and Global revokes a replayed refresh token.
_federated_backend_lock = threading.Lock()


def bind_federated_backend(backend) -> None:
    """Reuse the interactive session's authenticated HTTPS backend."""
    global _federated_backend
    _federated_backend = backend


def federated_backend():
    global _federated_backend
    with _federated_backend_lock:
        if _federated_backend is None:
            from ..deployment.backend import selected_backend
            backend = selected_backend()
            if backend.capabilities["account_transport"] != "https-json":
                raise RuntimeError("Federated device references require an HTTPS account home")
            if not backend.resume():
                raise RuntimeError("Log in to the account home first (run `remoterf`, then `login`)")
            _federated_backend = backend
        return _federated_backend


def _federated_ref(function_name, args):
    """The global device reference this call targets, or None for native calls."""
    from remoterf_federation_core import is_global_ref
    keys = ("token", "device_id") if function_name == "IDL:get_drivers" else ("a",)
    for key in keys:
        if key in args:
            value = unmap_arg(args[key])
            return value if is_global_ref(value) else None
    return None


def _federated_call(route, function_name, args):
    """Relay through Global: an ``rfg_`` token needs no login, a ref the bearer."""
    from ..deployment.http import AccountBackendError
    ref = getattr(route, "wire", route)
    if direct_path.wants(function_name):
        path = direct_path.global_path(ref, getattr(route, "deployment_id", None))
        if path is not None:
            try:
                response = path.call(function_name, args)
            except direct_path.PathError:
                pass  # the path is down now; this call takes the relay, once
            else:
                # The lab refused it unrun: the path's reservation is over, but a
                # newer one on this device may be live, and the relay honours that.
                refused = "a" in response.results and unmap_arg(response.results["a"]) == "the reservation has ended"
                if not refused:
                    return response
    request = grpc_pb2.GenericRPCRequest(function_name=function_name, args=args)
    for retry in (function_name.endswith(":GET"), False):
        try:
            if ref.startswith("rfg_"):
                raw = _token_backend().device_token_rpc(
                    ref, getattr(route, "deployment_id", None), request.SerializeToString()
                )
            else:
                raw = federated_backend().device_rpc(ref, request.SerializeToString())
            break
        except AccountBackendError as exc:
            if retry and exc.code == "internal_error" and exc.provenance == "transport":
                # An older lab drops ~0.1% of relayed results when a clock second
                # ticks mid-call; a GET changes nothing, so it is sent once more.
                continue
            if ref.startswith("rfg_") and str(exc) == "not_found":
                raise RuntimeError(
                    "this RemoteRF Global does not accept device tokens yet; use the device reference"
                ) from None
            label = f" [{exc.provenance}]" if exc.provenance else ""
            raise RuntimeError(f"{exc}{label}") from None
    response = grpc_pb2.GenericRPCResponse()
    response.ParseFromString(raw)
    return response


def _device_token(function_name, args):
    """The token a device call carries: `a` on a device RPC, `token` on a schema fetch."""
    key = "token" if function_name == "IDL:get_drivers" else "a"
    if key not in args:
        return None
    value = unmap_arg(args[key])
    return value if isinstance(value, str) else None


_token_backend_cache = None


def _token_backend():
    """Global, unauthenticated: an ``rfg_`` token is its own credential."""
    global _token_backend_cache
    if _token_backend_cache is None:
        from ..deployment import homes
        from ..deployment.backend import HttpsJsonAccountBackend

        saved = homes.load_state()["global_account"]
        if saved:
            origin, ident = saved["origin"], saved["deployment_id"]
        else:
            from ..remoterf_cli import _global_origin

            origin, ident = _global_origin(), None
        _token_backend_cache = HttpsJsonAccountBackend(origin, expected_id=ident)
    return _token_backend_cache


# ── device tokens: <token or handle>@<deployment> ────────────────────
#
# A driver keeps the string it was given; it is resolved here, at the wire,
# and only ever sent to the deployment it names. A bare token (no "@") is
# used exactly as before.

@dataclass(frozen=True)
class DeviceRoute:
    wire: str                           # what goes on the wire as `a` / `token`
    kind: str                           # "active" | "home" | "global"
    connection: object = None           # kind "home": that saved home's connection
    deployment_id: Optional[str] = None  # kind "global", rfg_x@dep: dep's uuid
    res_id: Optional[int] = None        # set iff `wire` came from a handle exchange


_routes: Dict[str, DeviceRoute] = {}
_routes_lock = threading.Lock()
_deployment_ids: Dict[str, str] = {}
_home_connections: Dict[str, Any] = {}
_told: set = set()  # x@lab strings whose refusal ("why") was already printed


def _unreachable(dep):
    return RuntimeError(
        f"can't reach {dep}: log in with remoterf --login username@{dep} or use a token from {dep}"
    )


def device_route(token: str, *, refresh: bool = False) -> DeviceRoute:
    route = None if refresh else _routes.get(token)
    if route is not None:
        return route
    with _routes_lock:
        if refresh:
            _routes.pop(token, None)
        route = _routes.get(token)
        if route is None:
            route = _routes[token] = _resolve(token)
    return route


def wire_token(token):
    return device_route(token).wire if isinstance(token, str) else token


def renewed_route(token, route, refusal):
    """For ``x@lab`` whose ``route.wire`` the lab refused as an unknown or dead
    token (a lab restart, the next reservation, the slot starting, a login
    saved since): the lab's answer now, or None when it is unchanged."""
    # Every lab refusal of a token says "invalid token": v1, IDL and v2 alike.
    if "invalid token" not in str(refusal or "") or "@" not in token or route.kind == "global":
        return None
    fresh = device_route(token, refresh=True)
    if route.res_id is not None and fresh.res_id is None:
        # A refusal keeps the driver's reservation: the next re-resolve compares against it.
        with _routes_lock:
            _routes[token] = route
    return fresh if fresh.wire != route.wire else None


def _active_home_name():
    """The home of the profile the channel uses (None under a REMOTERF_ADDR override)."""
    try:
        return _current_profile().home
    except RuntimeError:
        return None


def _resolve(t):
    from remoterf_federation_core import is_global_ref

    if "@" not in t:
        return DeviceRoute(t, "global" if t.startswith("rfg_") or is_global_ref(t) else "active")
    x, _, dep = t.rpartition("@")
    dep = dep.strip().lower()
    if not x or not dep:
        raise ValueError(f"{t!r}: expected <token or handle>@<deployment>")
    if x.startswith("rfg_"):
        return DeviceRoute(x, "global", deployment_id=_deployment_id(dep))
    from ..deployment import homes
    from ..deployment.state import load_target

    target = load_target()
    if target and target["transport"] == "https-json":
        route = _global_handle(x, dep)
        if route is not None:
            return route
    elif _active_home_name() == dep:
        return _lab(t, x, dep, None)
    try:
        home = homes.homes().get(dep)
    except Exception:  # noqa: BLE001 - a corrupt homes file is no homes
        home = None
    if home and home["transport"] == "grpc":
        return _lab(t, x, dep, _home_connection(dep))
    raise _unreachable(dep)


def _deployment_id(dep):
    """The uuid behind <dep>.global.remoterf.net, from its own discovery."""
    ident = _deployment_ids.get(dep)
    if ident is None:
        from ..deployment import homes

        try:
            if homes.NAME_RE.match(dep):
                ident = homes.discover(f"{dep}.{homes.GLOBAL_DOMAIN}")["deployment_id"]
        except Exception:  # noqa: BLE001 - unreachable is unreachable
            ident = None
        if not ident:
            raise _unreachable(dep)
        _deployment_ids[dep] = ident
    return ident


def _home_connection(dep):
    """A channel to a saved home that is not the active one."""
    connection = _home_connections.get(dep)
    if connection is None:
        from types import SimpleNamespace
        from remoterf_federation_core import ValidationError
        from ..deployment import homes

        try:
            route, certificate = homes.select_route(homes.load_home(dep))
        except ValidationError:
            raise _unreachable(dep) from None
        selected = build_secure_channel(f"{route['host']}:{route['port']}", certificate)
        connection = _home_connections[dep] = SimpleNamespace(
            channel=selected, stub=grpc_pb2_grpc.GenericRPCStub(selected)
        )
    return connection


def _lab(t, x, dep, connection):
    """Ask the lab whether ``x`` is a handle of a device the caller holds now.

    Without a saved login, or from a lab that does not know handles, ``x`` is
    simply a token."""
    kind = "active" if connection is None else "home"
    if connection is None:
        credentials = direct_path._credentials()
    else:
        from ..deployment import homes

        login = homes.recall_login(dep)
        credentials = (login["username"], login["secret"]) if login else None
    if credentials is None:
        return DeviceRoute(x, kind, connection)
    results = rpc_client(
        function_name="ACC:device_token",
        args={"un": map_arg(credentials[0]), "pw": map_arg(credentials[1]), "h": map_arg(x)},
        connection=connection,
    ).results
    if "Token" not in results:
        return DeviceRoute(x, kind, connection)
    if "why" not in results:
        _told.discard(t)
    elif t not in _told:
        _told.add(t)
        print(f"{t}: {unmap_arg(results['why'])}; using {x!r} as a token", file=sys.stderr)
    res_id = unmap_arg(results["res_id"]) if "res_id" in results else None
    return DeviceRoute(unmap_arg(results["Token"]), kind, connection, res_id=res_id)


def _key(s):
    """A device name as a handle: 'pluto A', 'pluto_A' and 'pluto__A' are one.
    Only for comparing names; never rewrite a string that may be a token."""
    return re.sub(r"[\s_]+", "_", s).strip("_")


def _global_handle(x, dep):
    """A handle on a device Global lists for this user, as its ref (None: no match)."""
    key = _key(x)
    try:
        backend = federated_backend()
        dep_id = _deployment_id(dep)
    except Exception:  # noqa: BLE001 - not logged in to Global, or no such slug
        return None
    matches = [
        d for d in backend.devices()["devices"]
        if key and d.get("deployment_id") == dep_id and _key(d.get("handle") or "") == key
    ]
    if len(matches) > 1:
        raise RuntimeError(f"{x}@{dep}: more than one device has that handle")
    return DeviceRoute(matches[0]["device_id"], "global") if matches else None


def rpc_client(*, function_name, args, connection=None):
    global tcp_calls
    tcp_calls += 1
    wire = os.getenv("REMOTERF_RX_WIRE", "i16").strip().lower()
    if wire and function_name.endswith(":rx:CALL0"):
        # Gate I slice 3: c64 halves the samples on the wire, i16 (the default)
        # quarters them when they are integer IQ; the driver still sees a complex
        # array. REMOTERF_RX_WIRE="" or "c128" opts out (servers ignore unknown).
        # Also through a HOME, whose relay caps each response at ~768 KiB.
        args = {**args, "wire": map_arg(wire)}
    token = _device_token(function_name, args)
    if function_name.endswith(":tx:CALL1") and "arg1" in args and token in _ci16_tokens:
        args = {**args, "arg1": _tx_wire(args["arg1"])}
    response = _driver_hooks(function_name, args, token) if connection is None else None
    if response is None:
        response = _dispatch(function_name, args, connection)
    if token is not None and function_name.endswith(":rx:CALL0") and _is_ci16(response.results.get(function_name.split(":")[1])):
        _ci16_tokens.add(token)
    if token is not None and function_name.endswith(":rx:CALL0") and _readahead_k():
        _rx_sizes[token] = response.ByteSize()
    return response


def _driver_hooks(function_name, args, token):
    """Batching and RX read-ahead for driver calls; None: send it as usual."""
    parts = function_name.split(":")
    driver_call = token is not None and len(parts) == 3
    if driver_call and parts[2] != "GET" and parts[1:] != ["rx", "CALL0"]:
        _rx_queues.clear()
    pending = _batch_local.pending
    same = driver_call and pending is not None and pending[:2] == (token, parts[0])
    if same and parts[2] != "SET":
        return _flush((function_name, args))
    if pending is not None and not same:
        _flush()
    if driver_call and parts[2] == "SET" and parts[0] != "DynamicV2" and token in _batch_local.depth:
        if _batch_local.pending is None:
            _batch_local.pending = (token, parts[0], [])
        _batch_local.pending[2].append((function_name, args))
        if len(_batch_local.pending[2]) >= _BATCH_MAX_OPS:
            _flush()
        return grpc_pb2.GenericRPCResponse()  # setters' results are never read
    if driver_call and parts[1:] == ["rx", "CALL0"] and _readahead_k():
        return _rx_readahead(parts[0], args, token)
    return None


# ── config batching: with sdr.batch(): ───────────────────────────────
#
# A driver's property SETs queue up and go out as one <Prefix>:batch:CALLN
# RPC, which the lab runs in order under the device lock. The queue is sent
# when the block ends or at any other call; a call on the batching driver
# rides along as the last op and gets its own results back. A failed SET
# raises there, naming it; the SETs before it ran, the ones after did not.

_BATCH_MAX_OPS = 64
_batch_support: Dict[str, bool] = {}  # token -> the lab runs batches (unknown until the first)


class _BatchState(threading.local):
    pending = None  # (token, prefix, [(function_name, args), ...])

    def __init__(self):
        self.depth = {}  # token -> nesting of its open `with batch(token)`


_batch_local = _BatchState()


@contextmanager
def batch(token):
    """Queue this token's property SETs until the block ends (re-entrant)."""
    from ..drivers._virtual import is_virtual_token

    if is_virtual_token(token):
        yield
        return
    depth = _batch_local.depth
    depth[token] = depth.get(token, 0) + 1
    try:
        yield
    finally:
        depth[token] -= 1
        if not depth[token]:
            del depth[token]
            pending = _batch_local.pending
            if pending is not None and pending[0] == token:
                _flush()


def _flush(last=None):
    token, prefix, ops = _batch_local.pending
    _batch_local.pending = None
    if last is not None:
        ops.append(last)
    if len(ops) == 1 or _batch_support.get(token) is False:
        return _replay(ops)
    args = {"a": ops[0][1]["a"]}
    for i, (name, op_args) in enumerate(ops):
        op = grpc_pb2.GenericRPCRequest(function_name=name, args={k: v for k, v in op_args.items() if k != "a"})
        args[f"op{i}"] = grpc_pb2.Argument(bytes_value=op.SerializeToString())
    try:
        response = _dispatch(f"{prefix}:batch:CALLN", args, None)
    except RuntimeError as exc:
        # An older lab or Host refuses it before running anything: as before, one by one.
        if not any(s in str(exc) for s in ("call_batch", "device_unavailable")):
            raise
        response = None
    if response is None or "batch_done" not in response.results:
        _batch_support[token] = False
        return _replay(ops)
    _batch_support[token] = True
    if "batch_failed" in response.results:
        failed = unmap_arg(response.results["batch_failed"])
        name = ops[failed][0] if isinstance(failed, int) and 0 <= failed < len(ops) else f"{prefix}:batch:CALLN"
        raise RuntimeError(f"{name}: {unmap_arg(response.results['batch_error'])}")
    del response.results["batch_done"]
    return response


def _replay(ops):
    for name, op_args in ops:
        response = _dispatch(name, op_args, None)
    return response


# ── RX read-ahead (opt-in): REMOTERF_RX_READAHEAD=<k> ────────────────
#
# One <Prefix>:rx_n:CALL1 RPC captures up to k buffers back to back; the
# next rx() calls are served from them. They were captured when the first
# rx() of the group ran, not when each later rx() is called, so use this only
# for continuous-capture loops. They are dropped after 1 s and on any SET or
# CALL (not GET) on any device of this process; changes made elsewhere
# (another program, another user's TX, the RF environment) are not seen.

# ponytail: fixed 1 s bound; make it configurable if a real loop needs another.
_RX_READAHEAD_MAX_AGE = 1.0
# The relay's response frame cap (core MAX_DEVICE_RPC_RESPONSE_FRAME_BYTES), less headroom.
_RX_N_BYTES = 786432 - 4096
_rx_queues: Dict[str, tuple] = {}  # token -> (captured at, deque of rx Arguments)
_rx_sizes: Dict[str, int] = {}  # token -> size of its last rx response
_rx_n_unsupported: set = set()


def _readahead_k():
    try:
        k = int(os.getenv("REMOTERF_RX_READAHEAD") or 0)
    except ValueError:
        return 0
    return min(k, 64) if k >= 2 else 0


def _rx_readahead(prefix, args, token):
    stamp, buffers = _rx_queues.get(token, (0.0, None))
    if buffers and time.monotonic() - stamp <= _RX_READAHEAD_MAX_AGE:
        try:
            return grpc_pb2.GenericRPCResponse(results={"rx": buffers.popleft()})
        except IndexError:
            pass  # another thread took the last one
    size = _rx_sizes.get(token)
    k = min(_readahead_k(), _RX_N_BYTES // size) if size and token not in _rx_n_unsupported else 0
    if k < 2:
        return None
    stamp = time.monotonic()
    try:
        response = _dispatch(f"{prefix}:rx_n:CALL1", {**args, "arg1": map_arg(k)}, None)
    except RuntimeError as exc:
        # An older lab or Host refused it unrun, or it outgrew the relay: plain rx from now on.
        if not any(s in str(exc) for s in ("call_rx_n", "device_unavailable", "response_too_large")):
            raise
        response = None
    arg = response.results.get("rx_n") if response is not None else None
    nd = arg.ndarray_value if arg is not None and arg.WhichOneof("value") == "ndarray_value" else None
    m = nd.shape[0] if nd is not None and nd.shape else 0
    if m < 1 or len(nd.data) % m:
        _rx_n_unsupported.add(token)
        return None
    step = len(nd.data) // m
    buffers = deque(
        grpc_pb2.Argument(ndarray_value=grpc_pb2.NDArrayValue(
            dtype=nd.dtype, shape=nd.shape[1:], data=nd.data[i * step:(i + 1) * step]))
        for i in range(m)
    )
    first = buffers.popleft()
    _rx_queues[token] = (stamp, buffers)
    return grpc_pb2.GenericRPCResponse(results={"rx": first})


# Device tokens whose Server answered an rx in ci16: it decodes ci16 too (the
# same Gate I slice 3 change), so their tx may go out that way.
_ci16_tokens: set = set()


def _is_ci16(arg) -> bool:
    return arg is not None and arg.WhichOneof("value") == "ndarray_value" and arg.ndarray_value.dtype == IQ16


def _tx_wire(arg):
    """TX samples as int16 IQ, a quarter of complex128, only when that is
    lossless (integer-valued, in range); REMOTERF_TX_WIRE="" opts out."""
    if os.getenv("REMOTERF_TX_WIRE", "i16").strip().lower() != "i16" or arg.WhichOneof("value") != "ndarray_value":
        return arg
    samples = unmap_arg(arg)
    return map_iq16(samples) if fits_iq16(samples) else arg


def _dispatch(function_name, args, connection):
    ref = _federated_ref(function_name, args) if connection is None else None
    if ref is not None:
        return _finish(_federated_call(ref, function_name, args))
    token = (
        _device_token(function_name, args)
        if connection is None and direct_path.wants(function_name) else None
    )
    if token is not None:
        route = device_route(token)
        response = _route_call(route, function_name, args)
        key = "error" if function_name.startswith("IDL:") else "a"
        fresh = renewed_route(token, route, unmap_arg(response.results[key]) if key in response.results else None)
        if fresh is not None:
            if key == "a" and route.res_id is not None and fresh.res_id not in (None, route.res_id):
                # The device was reset between holders; never carry on silently.
                raise RuntimeError(f"{token}: reservation changed; re-create the driver")
            response = _route_call(fresh, function_name, args)
        return _finish(response)
    return _finish(_send(function_name, args, connection))


def _route_call(route, function_name, args):
    key = "token" if function_name == "IDL:get_drivers" else "a"
    if route.wire.startswith("rfg_"):
        # The rfg_ token is Global's credential and goes only in the request body;
        # the frame is relayed to the lab, which pins the device itself.
        args = {**args, key: map_arg("")}
    elif unmap_arg(args[key]) != route.wire:
        args = {**args, key: map_arg(route.wire)}
    if route.kind == "global":
        return _federated_call(route, function_name, args)
    return _send(function_name, args, route.connection, token=route.wire)


def _send(function_name, args, connection, token=None):
    if connection is None and direct_path.wants(function_name):
        path = direct_path.current(token=token)
        if path is not None:
            try:
                return path.call(function_name, args)
            except direct_path.PathError:
                pass  # the path is down now; this call goes over gRPC, once
    # print(tcp_calls)
    # if not is_connected:
    #     response = rpc_client(function_name="UserLogin", args={"username": grpc_pb2.Argument(string_value=input("Username: ")), "password": grpc_pb2.Argument(string_value=getpass.getpass("Password: ")), "client_ip": grpc_pb2.Argument(string_value=local_ip)})
    #     if (response.results['status'].string_value == 'Success'):
    #         print("Login successful.")
    #         is_connected = True
    
    # TODO: Handle user login
    
    # print(f"Opening connection to {server_ip}:{server_port}")
    
    # TODO: Handle Errors
    
    # print(f"Calling function: {function_name}")
    return (connection or get_active_connection()).stub.Call(grpc_pb2.GenericRPCRequest(function_name=function_name, args=args))


def _finish(response):
    if 'a' in response.results:
        raise RuntimeError(unmap_arg(response.results['a']))
        
    if 'UE' in response.results:
        print(f"UserError: {unmap_arg(response.results['UE'])}")

    if 'Message' in response.results:
        print(f"{unmap_arg(response.results['Message'])}")
            
    
    return response

#region Example Usage

# if __name__ == '__main__':
#     args={
#         "key1": grpc_pb2.Argument(string_value="Hello"),
#         "key2": grpc_pb2.Argument(int32_value=123),
#         "key3": grpc_pb2.Argument(float_value=4.56),
#         "key4": grpc_pb2.Argument(bool_value=True),
#         "client_ip": grpc_pb2.Argument(string_value=local_ip)
#     }
    
#     response = rpc_client(function_name="echo", args=args)
    
#     if 'client_ip' in response.results:
#         del response.results['client_ip']
    
#     # Print results
#     print("Received response:")
#     for key, arg in response.results.items():
#         # Decode the oneof fields
#         if arg.HasField('string_value'):
#             value = arg.string_value
#         elif arg.HasField('int32_value'):
#             value = arg.int32_value
#         elif arg.HasField('float_value'):
#             value = arg.float_value
#         elif arg.HasField('bool_value'):
#             value = arg.bool_value
#         else:
#             value = "Undefined"
            
#         print(f"{key}: {value}")

#endregion
    
def handle_admin_command(inpu: str):
    """
    Parses commands like:
      admin help
      admin printa | printr | printp | printd
      admin rm aa | rm ar | rm a <username>
      admin setacc <username> <U|P|A> [devices=1,2,3] [max_res=5] [max_time=1800]

    Requires account.is_admin == True (client-side gate). Server still enforces.
    """
    if not getattr(account, "is_admin", False):
        print("Access denied: you are not an Admin.")
        return

    tokens = (inpu or "").strip().split()
    if len(tokens) < 2 or tokens[0].lower() != "admin":
        print("Usage: admin <command>. Try: admin help")
        return

    cmd = tokens[1].lower()

    def call_admin(fn: str, extra: dict | None = None):
        return remote_admin_rpc_client(
            function_name=fn,
            auth_un=account.username,
            auth_pw=account.password,
            args=extra or {},
            print_result=True,
        )

    if cmd in ("help", "h"):
        print("Admin commands:")
        print("  admin printa            - Print all accounts")
        print("  admin printr            - Print all reservations")
        print("  admin printp            - Print all perms")
        print("  admin printd            - Print all devices")
        print("  admin rm aa             - Remove all accounts")
        print("  admin rm ar             - Remove all reservations")
        print("  admin rm a <username>   - Remove one account")
        print("  admin setacc <u> <U|P|A> [devices=1,2] [max_res=3] [max_time=1800]")
        return

    # ----- print* -----
    if cmd in ("printa", "print_accounts", "print_all_accounts"):
        call_admin("print_all_accounts")
        return

    if cmd in ("printr", "print_res", "print_all_reservations"):
        call_admin("print_all_reservations")
        return

    if cmd in ("printp", "print_perms", "print_all_perms"):
        call_admin("print_all_perms")
        return

    if cmd in ("printd", "print_devices", "print_all_devices"):
        call_admin("print_all_devices")
        return

    # ----- rm -----
    if cmd == "rm":
        if len(tokens) < 3:
            print("Usage: admin rm <aa|ar|a> [username]")
            return

        sub = tokens[2].lower()

        if sub == "aa":
            call_admin("remove_all_users")
            return

        if sub == "ar":
            call_admin("remove_all_reservations")
            return

        if sub == "a":
            if len(tokens) < 4:
                print("Usage: admin rm a <username>")
                return
            call_admin("remove_user", {"username": tokens[3]})
            return

        print("Unknown rm subcommand. Use: aa, ar, a <username>")
        return

    # ----- setacc -----
    if cmd in ("setacc", "set_account", "setperm", "set"):
        if len(tokens) < 4:
            print("Usage: admin setacc <username> <U|P|A> [devices=1,2] [max_res=3] [max_time=1800]")
            return

        target_user = tokens[2]
        perm = tokens[3].upper()

        extra: dict = {"username": target_user, "permission": perm}

        # Parse optional k=v args (very lightweight parser)
        # Examples:
        #   devices=1,2,3
        #   max_res=5
        #   max_time=1800
        for t in tokens[4:]:
            if "=" not in t:
                continue
            k, v = t.split("=", 1)
            k = k.strip().lower()
            v = v.strip()

            if k in ("devices", "device_list", "devices_allowed", "device_ids"):
                # server-side remote_admin_call already supports "1,2,3" as string
                extra["device_list"] = v
            elif k in ("max_res", "max_reservations"):
                extra["max_reservations"] = int(v)
            elif k in ("max_time", "max_res_time", "max_reservation_time_sec"):
                extra["max_reservation_time_sec"] = int(v)

        call_admin("set_account", extra)
        return

    print(f"Unknown admin command: {cmd}. Try: admin help")


def remote_admin_rpc_client(
    *,
    function_name: str,
    auth_un: str,
    auth_pw: str,
    args: Optional[Dict[str, Any]] = None,
    raise_on_error: bool = False,
    print_result: bool = False,
) -> Dict[str, Any]:
    """
    Client helper for RemoteAdmin:<function> calls.

    Builds and sends:
      function_name = "RemoteAdmin:<function_name>"
      args include:
        auth_un, auth_pw (mapped via map_arg)
        + any additional args (mapped via map_arg if not already grpc_pb2.Argument)

    Returns:
      {
        "ok": bool,
        "result": str | None,
        "error": str | None,
        "traceback": str | None,
        "raw": grpc_pb2.GenericRPCResponse,
      }
    """
    fn = (function_name or "").strip()
    if fn.startswith("RemoteAdmin:"):
        rpc_fn = fn
    else:
        rpc_fn = f"RemoteAdmin:{fn}"

    payload: Dict[str, Any] = {
        "auth_un": map_arg(auth_un),
        "auth_pw": map_arg(auth_pw),
    }

    if args:
        for k, v in args.items():
            if v is None:
                continue
            # If caller already passed a grpc Argument, keep it.
            if isinstance(v, grpc_pb2.Argument):
                payload[str(k)] = v
            else:
                payload[str(k)] = map_arg(v)

    resp = rpc_client(function_name=rpc_fn, args=payload)

    # Parse the standardized RemoteAdmin response keys:
    #   Ok, Result, Error, Traceback (each typically map_arg-encoded)
    results = getattr(resp, "results", {}) or {}

    def _get_str(key: str) -> Optional[str]:
        if key not in results:
            return None
        try:
            return str(unmap_arg(results[key]))
        except Exception:
            try:
                return str(results[key])
            except Exception:
                return None

    ok_val = results.get("Ok", None)
    ok = False
    if ok_val is not None:
        try:
            ok = bool(unmap_arg(ok_val))
        except Exception:
            ok = False

    out = {
        "ok": ok,
        "result": _get_str("Result"),
        "error": _get_str("Error"),
        "traceback": _get_str("Traceback"),
        "raw": resp,
    }

    if print_result:
        if out["ok"]:
            print(out["result"] or "OK")
        else:
            print(out["error"] or "RemoteAdmin call failed.")
            if out["traceback"]:
                print(out["traceback"])

    if raise_on_error and not out["ok"]:
        msg = out["error"] or "RemoteAdmin call failed."
        raise RuntimeError(msg)

    return out
