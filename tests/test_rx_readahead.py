"""REMOTERF_RX_READAHEAD=<k>: one <Prefix>:rx_n:CALL1 serves the next rx() calls."""

import numpy as np
import pytest

from remoterf.common.grpc import grpc_pb2
from remoterf.common.utils import map_arg, unmap_arg
from remoterf.common.utils.process_arg import map_iq16
from remoterf.core import grpc_client

TOKEN = "Zm9vYmFy@lab"
OTHER = "YmFyYmF6@lab"
RX = "Pluto:rx:CALL0"


class Lab:
    """Numbered rx buffers; `refuse` makes rx_n fail like an older lab or Host."""

    def __init__(self):
        self.sent, self.count, self.limit, self.refuse = [], 0, 64, None

    def buffer(self):
        self.count += 1
        return np.full(4, self.count + 1j * self.count)

    def __call__(self, function_name, args, connection):
        self.sent.append(function_name)
        prop = function_name.split(":")[1] if function_name.count(":") == 2 else None
        if prop == "rx_n":
            if isinstance(self.refuse, Exception):
                raise self.refuse
            if self.refuse is not None:
                return grpc_pb2.GenericRPCResponse(results={k: map_arg(v) for k, v in self.refuse.items()})
            assert unmap_arg(args["wire"]) == "i16"
            m = min(unmap_arg(args["arg1"]), self.limit)
            return grpc_pb2.GenericRPCResponse(results={"rx_n": map_iq16(np.stack([self.buffer() for _ in range(m)]))})
        if prop == "rx":
            return grpc_pb2.GenericRPCResponse(results={"rx": map_iq16(self.buffer())})
        return grpc_pb2.GenericRPCResponse(results={prop: map_arg(0)} if prop else {})


@pytest.fixture
def lab(monkeypatch):
    for name, value in (("_rx_queues", {}), ("_rx_sizes", {}), ("_rx_n_unsupported", set()),
                        ("_ci16_tokens", set()), ("_batch_support", {}), ("_batch_local", grpc_client._BatchState())):
        monkeypatch.setattr(grpc_client, name, value)
    monkeypatch.delenv("REMOTERF_RX_WIRE", raising=False)
    monkeypatch.setenv("REMOTERF_RX_READAHEAD", "4")
    fake = Lab()
    monkeypatch.setattr(grpc_client, "_dispatch", fake)
    return fake


def _rx(token=TOKEN):
    arg = grpc_client.rpc_client(function_name=RX, args={"a": map_arg(token)}).results["rx"]
    return int(unmap_arg(arg)[0].real)


def _call(verb, prop="rx_lo", token=TOKEN):
    args = {"a": map_arg(token), **({prop: map_arg(1)} if verb == "SET" else {})}
    grpc_client.rpc_client(function_name=f"Pluto:{prop}:{verb}", args=args)


@pytest.mark.parametrize("value", [None, "0", "1", "x"])
def test_off_unless_asked(lab, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("REMOTERF_RX_READAHEAD")
    else:
        monkeypatch.setenv("REMOTERF_RX_READAHEAD", value)
    assert [_rx() for _ in range(3)] == [1, 2, 3]
    assert lab.sent == [RX] * 3


def test_one_rx_n_serves_the_next_rx_calls_in_order(lab):
    assert [_rx() for _ in range(6)] == [1, 2, 3, 4, 5, 6]
    # The first rx is plain (its size bounds k); then k buffers per RPC.
    assert lab.sent == [RX, "Pluto:rx_n:CALL1", "Pluto:rx_n:CALL1"]
    served = grpc_client.rpc_client(function_name=RX, args={"a": map_arg(TOKEN)}).results["rx"]
    assert served.ndarray_value.dtype == "ci16" and list(served.ndarray_value.shape) == [4]
    assert TOKEN in grpc_client._ci16_tokens


def test_k_is_bounded_by_the_relay_response_cap(lab, monkeypatch):
    sent = []
    _rx()
    size = grpc_client._rx_sizes[TOKEN]
    monkeypatch.setattr(grpc_client, "_RX_N_BYTES", size * 3 + 1)

    def spy(function_name, args, connection):
        if "arg1" in args:
            sent.append(unmap_arg(args["arg1"]))
        return lab(function_name, args, connection)

    monkeypatch.setattr(grpc_client, "_dispatch", spy)
    _rx()
    assert sent == [3]
    monkeypatch.setattr(grpc_client, "_RX_N_BYTES", size * 2 - 1)
    grpc_client._rx_queues.clear()
    _rx()
    assert sent == [3] and lab.sent[-1] == RX  # k < 2: plain rx


def test_fewer_buffers_than_asked(lab):
    lab.limit = 2
    assert [_rx() for _ in range(4)] == [1, 2, 3, 4]
    assert lab.sent == [RX, "Pluto:rx_n:CALL1", "Pluto:rx_n:CALL1"]


@pytest.mark.parametrize("verb,token,clears", [
    ("SET", TOKEN, True), ("CALL0", TOKEN, True), ("CALL1", TOKEN, True), ("CALLN", TOKEN, True),
    ("SET", OTHER, True), ("GET", TOKEN, False), ("GET", OTHER, False),
])
def test_device_changes_drop_the_buffers(lab, verb, token, clears):
    assert [_rx(), _rx()] == [1, 2]
    _call(verb, token=token)
    before = len(lab.sent)
    assert _rx() == (6 if clears else 3)
    assert (len(lab.sent) > before) is clears


def test_old_buffers_are_dropped(lab, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(grpc_client.time, "monotonic", lambda: now[0])
    assert [_rx(), _rx()] == [1, 2]
    now[0] += 1.0
    assert _rx() == 3
    now[0] += 1.01
    assert _rx() == 6 and lab.sent.count("Pluto:rx_n:CALL1") == 2


@pytest.mark.parametrize("refuse", [
    RuntimeError("\"'call_rx_n' is not exposed on adalm_pluto\""),  # an old lab, native or direct
    RuntimeError("device_unavailable [destination]"),  # an old lab over the relay
    RuntimeError("response_too_large [destination]"),
    {"rx_n": "None", "Error": "unknown call"},  # an old lab's Host device
])
def test_an_old_lab_gets_plain_rx_from_then_on(lab, refuse):
    lab.refuse = refuse
    assert [_rx(), _rx(), _rx()] == [1, 2, 3]
    assert lab.sent == [RX, "Pluto:rx_n:CALL1", RX, RX]
    assert TOKEN in grpc_client._rx_n_unsupported


def test_a_transport_failure_is_raised(lab):
    _rx()
    lab.refuse = RuntimeError("route_unavailable [transport]")
    with pytest.raises(RuntimeError, match="route_unavailable"):
        _rx()
    assert TOKEN not in grpc_client._rx_n_unsupported


def test_an_rx_after_queued_sets_rides_the_batch_as_a_plain_rx(lab):
    _rx()
    with grpc_client.batch(TOKEN):
        _call("SET")
        _call("SET", prop="gain")
        _rx()
    assert "Pluto:rx_n:CALL1" not in lab.sent
