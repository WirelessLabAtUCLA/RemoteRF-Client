"""with sdr.batch(): a driver's SETs go to the lab as one <Prefix>:batch:CALLN."""

import pytest

from remoterf.common.grpc import grpc_pb2
from remoterf.common.utils import map_arg, unmap_arg
from remoterf.core import grpc_client

TOKEN = "Zm9vYmFy@lab"
OTHER = "YmFyYmF6@lab"


class Lab:
    """A device lab behind `_dispatch`: runs ops in order; old=True has no batch."""

    def __init__(self, old=None):
        self.sent, self.ran, self.state, self.fail, self.old = [], [], {}, set(), old

    def __call__(self, function_name, args, connection):
        self.sent.append(function_name)
        if function_name.endswith(":batch:CALLN") and self.old is not None:
            if isinstance(self.old, Exception):
                raise self.old
            return grpc_client._finish(grpc_pb2.GenericRPCResponse(results={k: map_arg(v) for k, v in self.old.items()}))
        return grpc_client._finish(self.run(function_name, dict(args)))

    def run(self, function_name, args):
        if function_name.count(":") != 2:
            return grpc_pb2.GenericRPCResponse()
        prefix, prop, verb = function_name.split(":")
        if prop == "batch":
            n = sum(1 for key in args if key.startswith("op"))
            for i in range(n):
                op = grpc_pb2.GenericRPCRequest()
                op.ParseFromString(args[f"op{i}"].bytes_value)
                assert op.function_name.startswith(prefix + ":") and "a" not in op.args
                response = self.run(op.function_name, dict(op.args))
                if "a" in response.results:
                    return grpc_pb2.GenericRPCResponse(results={
                        "batch_done": map_arg(i), "batch_failed": map_arg(i), "batch_error": response.results["a"]})
            response.results["batch_done"].CopyFrom(map_arg(n))
            return response
        self.ran.append(function_name)
        if verb == "SET":
            if prop in self.fail:
                return grpc_pb2.GenericRPCResponse(results={"a": map_arg(f"{prop} refused")})
            self.state[prop] = unmap_arg(args[prop])
            return grpc_pb2.GenericRPCResponse()
        if verb == "GET":
            return grpc_pb2.GenericRPCResponse(results={prop: map_arg(self.state.get(prop, 0))})
        return grpc_pb2.GenericRPCResponse(results={prop: map_arg("called")})


@pytest.fixture
def lab(monkeypatch):
    monkeypatch.delenv("REMOTERF_RX_READAHEAD", raising=False)
    monkeypatch.setattr(grpc_client, "_batch_local", grpc_client._BatchState())
    monkeypatch.setattr(grpc_client, "_batch_support", {})
    fake = Lab()
    monkeypatch.setattr(grpc_client, "_dispatch", fake)
    return fake


def _set(prop, value, token=TOKEN, prefix="Pluto"):
    return grpc_client.rpc_client(function_name=f"{prefix}:{prop}:SET", args={prop: map_arg(value), "a": map_arg(token)})


def _get(prop, token=TOKEN):
    return grpc_client.rpc_client(function_name=f"Pluto:{prop}:GET", args={"a": map_arg(token)})


def test_sets_coalesce_and_a_get_rides_along_as_the_last_op(lab):
    with grpc_client.batch(TOKEN):
        _set("rx_lo", 1)
        _set("sample_rate", 2)
        assert lab.sent == []
        response = _get("rx_lo")
        assert unmap_arg(response.results["rx_lo"]) == 1 and "batch_done" not in response.results
        assert unmap_arg(_get("sample_rate").results["sample_rate"]) == 2  # nothing queued: a plain GET
    assert lab.sent == ["Pluto:batch:CALLN", "Pluto:sample_rate:GET"]
    assert lab.ran == ["Pluto:rx_lo:SET", "Pluto:sample_rate:SET", "Pluto:rx_lo:GET", "Pluto:sample_rate:GET"]
    assert grpc_client._batch_support[TOKEN] is True


def test_the_block_end_sends_the_queue_also_when_it_raised(lab):
    with grpc_client.batch(TOKEN):
        _set("rx_lo", 1)
        _set("tx_lo", 2)
    assert lab.sent == ["Pluto:batch:CALLN"] and lab.state == {"rx_lo": 1, "tx_lo": 2}
    with pytest.raises(ValueError):
        with grpc_client.batch(TOKEN):
            _set("gain", 3)
            _set("rx_lo", 4)
            raise ValueError
    assert lab.sent == ["Pluto:batch:CALLN"] * 2 and lab.state["rx_lo"] == 4
    with grpc_client.batch(TOKEN):
        _set("gain", 5)  # one op: a plain SET
    assert lab.sent[-1] == "Pluto:gain:SET"


def test_any_other_call_sends_the_queue_first(lab):
    with grpc_client.batch(TOKEN):
        _set("rx_lo", 1)
        _set("tx_lo", 2)
        _set("rx_lo", 9, token=OTHER)  # another driver, not batching
        _set("gain", 3)
        _set("gain", 4)
        grpc_client.rpc_client(function_name="ACC:get_dev", args={})
        _set("gain", 5)
        _set("gain", 6, prefix="DynamicV2")  # never batched
    assert lab.sent == ["Pluto:batch:CALLN", "Pluto:rx_lo:SET", "Pluto:batch:CALLN", "ACC:get_dev",
                        "Pluto:gain:SET", "DynamicV2:gain:SET"]


def test_nested_blocks_send_at_the_outermost_end(lab):
    with grpc_client.batch(TOKEN):
        _set("rx_lo", 1)
        with grpc_client.batch(TOKEN):
            _set("tx_lo", 2)
        assert lab.sent == []
        _set("gain", 3)
    assert lab.sent == ["Pluto:batch:CALLN"] and len(lab.ran) == 3


def test_a_full_queue_is_sent_at_64_ops(lab, monkeypatch):
    sizes = []
    real = lab.run

    def run(function_name, args):
        if function_name.endswith(":batch:CALLN"):
            sizes.append(sum(1 for key in args if key.startswith("op")))
        return real(function_name, args)

    monkeypatch.setattr(lab, "run", run)
    with grpc_client.batch(TOKEN):
        for i in range(66):
            _set("rx_lo", i)
        assert sizes == [64]
    assert sizes == [64, 2] and lab.state["rx_lo"] == 65


def test_a_failed_set_raises_naming_it_and_later_ops_do_not_run(lab):
    lab.fail.add("sample_rate")
    with pytest.raises(RuntimeError, match=r"^Pluto:sample_rate:SET: sample_rate refused$"):
        with grpc_client.batch(TOKEN):
            _set("rx_lo", 1)
            _set("sample_rate", 2)
            _set("gain", 3)
    assert lab.ran == ["Pluto:rx_lo:SET", "Pluto:sample_rate:SET"]


@pytest.mark.parametrize("old", [
    {"a": "\"'call_batch' is not exposed on adalm_pluto\""},  # an old lab, native or direct
    RuntimeError("device_unavailable [destination]"),  # an old lab over the relay
    RuntimeError("Partner refused: device_unavailable"),  # an old partner lab
    {"batch": "None", "Error": "unknown call"},  # an old lab's Host device
])
def test_an_old_lab_gets_the_ops_one_by_one_and_is_not_probed_again(lab, old):
    lab.old = old
    with grpc_client.batch(TOKEN):
        _set("rx_lo", 1)
        response = _get("rx_lo")
    assert unmap_arg(response.results["rx_lo"]) == 1
    assert lab.sent == ["Pluto:batch:CALLN", "Pluto:rx_lo:SET", "Pluto:rx_lo:GET"]
    assert grpc_client._batch_support[TOKEN] is False
    with grpc_client.batch(TOKEN):
        _set("rx_lo", 2)
        _set("tx_lo", 3)
    assert lab.sent[3:] == ["Pluto:rx_lo:SET", "Pluto:tx_lo:SET"]


def test_a_transport_failure_is_raised_not_replayed(lab):
    lab.old = RuntimeError("route_unavailable [transport]")
    with pytest.raises(RuntimeError, match="route_unavailable"):
        with grpc_client.batch(TOKEN):
            _set("rx_lo", 1)
            _set("tx_lo", 2)
    assert lab.sent == ["Pluto:batch:CALLN"] and lab.ran == [] and TOKEN not in grpc_client._batch_support


def test_a_virtual_driver_batch_is_a_no_op(lab):
    from remoterf.drivers.adalm_pluto import adi

    sdr = adi.Pluto(virtual=True)
    with sdr.batch():
        assert grpc_client._batch_local.depth == {}
    assert lab.sent == []
