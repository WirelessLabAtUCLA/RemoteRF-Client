"""A partner lab's device gets its own punched path: the token learned from a
schema fetch routes calls to it, and the home's answer names the lab's
certificate for the handshake."""

from __future__ import annotations

import json

from remoterf.common.grpc import grpc_pb2
from remoterf.common.utils import map_arg, unmap_arg
from remoterf.core import direct_path, grpc_client


class FakePath:
    instances: list = []

    def __init__(self, *, offer, server_name, ca_pem, stun=None, memo=None):
        self.offer, self.server_name, self.ca_pem, self.memo = offer, server_name, ca_pem, memo
        self.state, self.calls = "ready", []
        FakePath.instances.append(self)

    @property
    def ready(self):
        return self.state == "ready"

    def start(self):
        pass

    def wait(self, timeout=None):
        return True

    repunch = True  # whether a woken path comes back within the wait

    def wake(self, wait=0.0):
        # As the real one: only an idle path is waited for; a down one retries in the background.
        self.woken = getattr(self, "woken", 0) + 1
        if wait and self.repunch and self.state == "idle":
            self.state = "ready"

    def stop(self):
        self.state = "off"

    def call(self, function_name, args):
        self.calls.append(function_name)
        return grpc_pb2.GenericRPCResponse(results={"echo": map_arg(function_name)})


def setup_function(function):
    direct_path.stop()
    direct_path._partner_owners.clear()
    FakePath.instances.clear()


teardown_function = setup_function  # no fake path outlives its test


def test_a_marked_token_gets_its_own_path_and_other_tokens_do_not(monkeypatch):
    monkeypatch.setenv("REMOTERF_DIRECT", "1")
    monkeypatch.setattr(direct_path, "DirectPath", FakePath)
    monkeypatch.setattr(direct_path, "_credentials", lambda: ("alice", "secret"))
    monkeypatch.setattr(direct_path, "_from_stored_login", lambda: None)
    direct_path.mark_partner("tok", "owner-uuid")

    path = direct_path.current(token="tok")
    assert path is FakePath.instances[0] and path.memo == "owner-uuid"
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


def test_no_login_offers_with_the_token_alone_and_disabled_means_none(monkeypatch):
    monkeypatch.setattr(direct_path, "DirectPath", FakePath)
    monkeypatch.setattr(direct_path, "_from_stored_login", lambda: None)
    monkeypatch.setattr(direct_path, "_stored_login", lambda: None)
    direct_path._login = None
    monkeypatch.setenv("REMOTERF_DIRECT", "1")
    direct_path.mark_partner("tok", "owner-uuid")
    path = direct_path.current(token="tok")
    assert path is FakePath.instances[0] and path.memo == "owner-uuid|token"  # its own memo scope
    seen = {}

    def fake_rpc(*, function_name, args, connection=None):
        seen.update(function_name=function_name, args={k: unmap_arg(v) for k, v in args.items()})
        return grpc_pb2.GenericRPCResponse(results={"ice": map_arg("{}")})

    monkeypatch.setattr(grpc_client, "rpc_client", fake_rpc)
    path.offer({"id": "x"})
    assert seen["function_name"] == "ACC:direct_offer" and sorted(seen["args"]) == ["a", "ice"]
    assert seen["args"]["a"] == "tok"
    direct_path.stop()
    direct_path._login = ("alice", "secret")
    assert direct_path.current(token="tok").memo == "owner-uuid"  # a login signs as before
    monkeypatch.setenv("REMOTERF_DIRECT", "0")
    assert direct_path.current(token="tok") is None  # disabled: nothing, however marked
    direct_path._login = None


def test_an_old_home_refusing_a_token_offer_means_no_direct_path(monkeypatch):
    replies = []
    monkeypatch.setattr(grpc_client, "rpc_client", lambda **k: grpc_pb2.GenericRPCResponse(
        results={"ace": map_arg(replies.pop())}))
    send = direct_path._offer_rpc(None, None, token="tok")
    # A Server older than token offers fails reading the missing login.
    replies.append("Internal server error; please try again or contact your instructor.")
    try:
        send({})
    except direct_path.PathUnavailable as exc:
        assert "needs a login" in str(exc)
    else:
        raise AssertionError("an old home must stop the path")
    replies.append("That reservation is not active now.")  # a new home's refusal: retried later
    try:
        send({})
    except direct_path.PathError as exc:
        assert not isinstance(exc, direct_path.PathUnavailable)
    else:
        raise AssertionError("a refusal is no answer")


def test_describe_reports_partner_paths(monkeypatch):
    monkeypatch.setattr(direct_path, "DirectPath", FakePath)
    monkeypatch.setattr(direct_path, "_from_stored_login", lambda: None)
    monkeypatch.setattr(grpc_client, "_current_profile", lambda: None)
    monkeypatch.setattr(direct_path, "_active_route", lambda profile: None)
    monkeypatch.setattr(direct_path, "_credentials", lambda: None)
    monkeypatch.setenv("REMOTERF_DIRECT", "1")
    assert direct_path.describe() == "relay (direct path off)"  # nothing partnered yet
    direct_path.mark_partner("tok", "lab-b")
    path = direct_path.current(token="tok")
    path.describe = lambda: "direct 192.0.2.7:4433 (3 calls)"
    assert direct_path.describe() == "partner lab-b: direct 192.0.2.7:4433 (3 calls)"
    path.state = "down"
    path.describe = lambda: "relay (direct path down: timed out; retrying)"
    assert direct_path.describe() == "partner lab-b: via HOME (direct path down: timed out; retrying)"
    direct_path._path = home = FakePath(offer=None, server_name="", ca_pem=b"")
    home.describe = lambda: "direct 192.0.2.1:4433"
    assert direct_path.describe() == (
        "direct 192.0.2.1:4433; partner lab-b: via HOME (direct path down: timed out; retrying)"
    )
    direct_path._path = None
    monkeypatch.setattr(direct_path, "_active_route", lambda profile: {"kind": "lan"})
    assert direct_path.describe().startswith("LAN (direct); partner lab-b: via HOME")


def test_a_partner_path_that_is_down_sends_the_call_to_the_home(monkeypatch):
    monkeypatch.setattr(direct_path, "DirectPath", FakePath)
    direct_path._login = ("alice", "secret")
    monkeypatch.setenv("REMOTERF_DIRECT", "1")
    direct_path.mark_partner("tok", "owner-uuid")
    path = direct_path.current(token="tok")
    path.state = "down"
    assert direct_path.current(token="tok") is None and path.woken == 1  # a failed path retries on demand
    path.state = "idle"
    path.repunch = False
    assert direct_path.current(token="tok") is None  # woken, but not back in time: the relay it is
    path.repunch = True
    assert direct_path.current(token="tok") is path
    direct_path._login = None


def test_a_call_after_idle_waits_for_the_repunch(monkeypatch):
    monkeypatch.setattr(direct_path, "DirectPath", FakePath)
    direct_path._login = ("alice", "secret")
    monkeypatch.setenv("REMOTERF_DIRECT", "1")
    direct_path.mark_partner("tok", "owner-uuid")
    path = direct_path.current(token="tok")
    path.state = "idle"
    assert direct_path.current(token="tok") is path and path.woken == 1  # woken and back within the wait
    direct_path._login = None
