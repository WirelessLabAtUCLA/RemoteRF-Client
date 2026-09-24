"""A partner lab's device gets its own punched path: the token learned from a
schema fetch routes calls to it, and the home's answer names the lab's
certificate for the handshake."""

from __future__ import annotations

import json

from remoteRF.common.grpc import grpc_pb2
from remoteRF.common.utils import map_arg, unmap_arg
from remoteRF.core import direct_path, grpc_client


class FakePath:
    instances: list = []

    def __init__(self, *, offer, server_name, ca_pem, stun=None):
        self.offer, self.server_name, self.ca_pem = offer, server_name, ca_pem
        self.state, self.calls = "ready", []
        FakePath.instances.append(self)

    @property
    def ready(self):
        return self.state == "ready"

    def start(self):
        pass

    def wait(self, timeout=None):
        return True

    def wake(self):
        pass

    def stop(self):
        self.state = "off"

    def call(self, function_name, args):
        self.calls.append(function_name)
        return grpc_pb2.GenericRPCResponse(results={"echo": map_arg(function_name)})


def setup_function(function):
    direct_path.stop()
    direct_path._partner_owners.clear()
    FakePath.instances.clear()


def test_a_marked_token_gets_its_own_path_and_other_tokens_do_not(monkeypatch):
    monkeypatch.setenv("REMOTERF_DIRECT", "1")
    monkeypatch.setattr(direct_path, "DirectPath", FakePath)
    monkeypatch.setattr(direct_path, "_credentials", lambda: ("alice", "secret"))
    monkeypatch.setattr(direct_path, "_from_stored_login", lambda: None)
    direct_path.mark_partner("tok", "owner-uuid")

    path = direct_path.current(token="tok")
    assert path is FakePath.instances[0]
    assert direct_path.current(token="tok") is path  # one path per token, kept
    assert direct_path.current(token="native") is None  # the home's path, not opened here
    assert direct_path.current() is None
    assert len(FakePath.instances) == 1
    direct_path.stop()
    assert path.state == "off"


def test_the_offer_carries_the_token_and_takes_the_owners_certificate(monkeypatch):
    seen = {}

    def fake_rpc(*, function_name, args, connection=None):
        seen.update(function_name=function_name, args={k: unmap_arg(v) for k, v in args.items()})
        return grpc_pb2.GenericRPCResponse(results={
            "ice": map_arg(json.dumps({"ufrag": "u", "pwd": "p", "candidates": []})),
            "server_name": map_arg("owner.test"), "ca_pem": map_arg("PEM"),
        })

    monkeypatch.setattr(grpc_client, "rpc_client", fake_rpc)
    answer = direct_path._offer_rpc("alice", "secret", token="tok")({"id": "x"})
    assert seen["function_name"] == "ACC:direct_offer" and seen["args"]["a"] == "tok"
    assert answer == {"ufrag": "u", "pwd": "p", "candidates": [], "server_name": "owner.test", "ca_pem": "PEM"}

    plain = direct_path._offer_rpc("alice", "secret")
    plain({"id": "y"})
    assert "a" not in seen["args"]


def test_rpc_client_routes_by_the_token_the_call_carries(monkeypatch):
    routed = []
    fake = FakePath(offer=None, server_name="", ca_pem=b"")

    def current(token=None):
        routed.append(token)
        return fake

    monkeypatch.setattr(direct_path, "current", current)
    response = grpc_client.rpc_client(function_name="pluto:rx:GET", args={"a": map_arg("tok")})
    assert routed == ["tok"] and unmap_arg(response.results["echo"]) == "pluto:rx:GET"
    grpc_client.rpc_client(function_name="IDL:get_drivers", args={"token": map_arg("tok2")})
    assert routed[-1] == "tok2"
    grpc_client.rpc_client(function_name="pluto:rx:GET", args={"a": map_arg(7)})
    assert routed[-1] is None  # only a string token can name a partner path
