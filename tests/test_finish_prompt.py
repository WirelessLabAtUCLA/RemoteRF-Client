"""A user-error reply never blocks a script on stdin."""

import io

from remoteRF.common.grpc import grpc_pb2
from remoteRF.common.utils import map_arg
from remoteRF.core import grpc_client


def test_a_user_error_only_prompts_on_a_terminal(monkeypatch, capsys):
    response = grpc_pb2.GenericRPCResponse(results={"UE": map_arg("Username already exists.")})
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # not a tty
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("prompted")))
    assert grpc_client._finish(response) is response
    assert "Username already exists" in capsys.readouterr().out
