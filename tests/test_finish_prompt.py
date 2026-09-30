"""A user-error reply never blocks on stdin, terminal or not."""

import io

import pytest

from remoteRF.common.grpc import grpc_pb2
from remoteRF.common.utils import map_arg
from remoteRF.core import grpc_client


@pytest.mark.parametrize("isatty", [False, True])
def test_a_user_error_never_prompts(monkeypatch, capsys, isatty):
    response = grpc_pb2.GenericRPCResponse(results={"UE": map_arg("Username already exists.")})
    stdin = io.StringIO("")
    monkeypatch.setattr(stdin, "isatty", lambda: isatty)
    monkeypatch.setattr("sys.stdin", stdin)
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("prompted")))
    assert grpc_client._finish(response) is response
    assert "Username already exists" in capsys.readouterr().out
