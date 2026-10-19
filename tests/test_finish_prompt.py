"""A user-error reply never blocks on stdin, terminal or not."""

import io

import pytest

from remoterf.common.grpc import grpc_pb2
from remoterf.common.utils import map_arg
from remoterf.core import grpc_client


@pytest.mark.parametrize("isatty", [False, True])
def test_a_user_error_never_prompts(monkeypatch, capsys, isatty):
    response = grpc_pb2.GenericRPCResponse(results={"UE": map_arg("Username already exists.")})
    stdin = io.StringIO("")
    monkeypatch.setattr(stdin, "isatty", lambda: isatty)
    monkeypatch.setattr("sys.stdin", stdin)
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("prompted")))
    assert grpc_client._finish(response) is response
    assert "Username already exists" in capsys.readouterr().out


def _reply(**results):
    return grpc_pb2.GenericRPCResponse(results={k: map_arg(v) for k, v in results.items()})


def test_a_host_device_failure_is_an_error_not_a_silent_set_or_a_bad_value():
    timed_out = _reply(Ok=False, Error="")
    for name in ("adalm_pluto_mimo_ykush:sample_rate:SET", "adalm_pluto_mimo_ykush:sample_rate:GET"):
        with pytest.raises(RuntimeError, match="did not answer in time"):
            grpc_client._finish(timed_out, name)
    with pytest.raises(RuntimeError, match="usb gone"):
        grpc_client._finish(_reply(Ok=False, Error="usb gone"), "Pluto:rx:CALL0")
    assert grpc_client._finish(_reply(), "Pluto:rx_lo:SET")  # a SET answers {}
    assert grpc_client._finish(_reply(rx_lo=915e6), "Pluto:rx_lo:GET")
    assert grpc_client._finish(_reply(Error="old lab"), "Pluto:batch:CALL1")  # probes keep their own handling
