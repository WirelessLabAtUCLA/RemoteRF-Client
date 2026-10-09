"""`<token or handle>@<deployment>`: resolved at the wire, and only ever sent to
the deployment it names. Bare tokens and Global refs behave exactly as before."""

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from remoterf_federation_core import local_capabilities

from remoterf.common.grpc import grpc_pb2
from remoterf.common.utils import map_arg, unmap_arg
from remoterf.core import direct_path, grpc_client
from remoterf.core.dynamic_v2_transport import DynamicV2Transport, _DevicePlaneStub
from remoterf.deployment import homes, state
from remoterf.deployment.backend import HttpsJsonAccountBackend
from remoterf.deployment.http import JsonTransport

GLOBAL = "550e8400-e29b-41d4-a716-446655440000"
DEST = "11111111-1111-4111-8111-111111111111"
REF = f"{DEST}:12"
RFG = "rfg_" + "A" * 43
GET = "pluto:tx_lo:GET"


def _unreachable(dep):
    return f"can't reach {dep}: log in with remoterf --login username@{dep} or use a token from {dep}"


class Stub:
    """A lab's GenericRPC service: records every call, answers from `replies`."""

    def __init__(self, replies=None):
        self.calls, self.replies = [], dict(replies or {})

    def Call(self, request):
        args = {k: unmap_arg(v) for k, v in request.args.items()}
        self.calls.append((request.function_name, args))
        reply = self.replies.get(request.function_name, {"tx_lo": 1})
        if callable(reply):
            reply = reply(args)
        return grpc_pb2.GenericRPCResponse(results={k: map_arg(v) for k, v in reply.items()})


def _never(*_a, **_k):
    raise AssertionError("must not be used")


@pytest.fixture(autouse=True)
def routes(monkeypatch, tmp_path):
    caches = (grpc_client._routes, grpc_client._deployment_ids, grpc_client._home_connections, grpc_client._told)
    for cache in caches:
        cache.clear()
    monkeypatch.setattr(grpc_client, "_token_backend_cache", None)
    monkeypatch.setattr(grpc_client, "_federated_backend", None)
    # The active lab is the saved home "ucla".
    ca = tmp_path / "ca.crt"
    ca.write_bytes(b"pem")
    config = Path.home() / ".config" / "remoterf-client"
    config.mkdir(parents=True, exist_ok=True)
    (config / ".env").write_text(
        f"REMOTERF_ADDR=203.0.113.10:12321\nREMOTERF_CA_CERT={ca}\nREMOTERF_PROFILE=ucla\n")
    monkeypatch.setattr(direct_path, "current", lambda token=None: None)
    monkeypatch.setattr(direct_path, "global_path", lambda ref, deployment_id=None: None)
    active = Stub()
    monkeypatch.setattr(grpc_client, "get_active_connection", lambda: SimpleNamespace(stub=active))
    monkeypatch.setattr(homes, "discover", _never)
    yield active
    for cache in caches:
        cache.clear()


def _login(monkeypatch, credentials=("jdoe", "s3cret")):
    monkeypatch.setattr(direct_path, "_credentials", lambda: credentials)


def _token_backend(monkeypatch, frame=None):
    """Global with no login at all: no credentials and no DEV:rpc operation."""
    transport = Mock()
    transport.origin = "https://global.example"
    caps = local_capabilities(deployment_id=GLOBAL)
    caps.update(account_transport="https-json", account_api_base="/v2/deployment")
    transport.request.return_value = caps
    backend = HttpsJsonAccountBackend(transport.origin, transport=transport, store=Mock())
    answer = frame or grpc_pb2.GenericRPCResponse(results={"tx_lo": map_arg(7)})
    transport.request.return_value = {
        "provenance": "destination",
        "response_b64": base64.b64encode(answer.SerializeToString()).decode(),
    }
    monkeypatch.setattr(grpc_client, "_token_backend_cache", backend)
    monkeypatch.setattr(grpc_client, "federated_backend", _never)
    return transport


# ── classification ───────────────────────────────────────────────────

def test_bare_strings_route_as_before(routes):
    assert grpc_client.device_route("Zm9vYmFy") == grpc_client.DeviceRoute("Zm9vYmFy", "active")
    assert grpc_client.device_route(REF).kind == "global"
    assert grpc_client.device_route(RFG) == grpc_client.DeviceRoute(RFG, "global")
    grpc_client.rpc_client(function_name=GET, args={"a": map_arg("Zm9vYmFy")})
    assert routes.calls == [(GET, {"a": "Zm9vYmFy"})]


def test_a_bare_rfg_token_needs_no_login(monkeypatch, routes):
    transport = _token_backend(monkeypatch)
    response = grpc_client.rpc_client(function_name=GET, args={"a": map_arg(RFG)})
    assert unmap_arg(response.results["tx_lo"]) == 7
    method, path = transport.request.call_args.args
    kwargs = transport.request.call_args.kwargs
    assert (method, path) == ("POST", "/v2/deployment/devices/token-rpc")
    assert "access" not in kwargs and set(kwargs["data"]) == {"token", "request_b64"}
    assert kwargs["data"]["token"] == RFG
    assert routes.calls == []


def test_the_token_backend_uses_the_default_global_origin(monkeypatch):
    seen = []
    monkeypatch.setenv("REMOTERF_GLOBAL_ORIGIN", "https://global.example")
    monkeypatch.setattr("remoterf.deployment.backend.HttpsJsonAccountBackend",
                        lambda origin, expected_id=None: seen.append((origin, expected_id)) or "backend")
    assert grpc_client._token_backend() == "backend"
    assert seen == [("https://global.example", None)]


def test_rfg_at_deployment_is_bound_to_that_deployment(monkeypatch, routes):
    transport = _token_backend(monkeypatch)
    monkeypatch.setattr(homes, "discover", lambda host: {"deployment_id": DEST} if host == "ucla.global.remoterf.net" else _never())
    grpc_client.rpc_client(function_name=GET, args={"a": map_arg(f"{RFG}@UCLA")})
    data = transport.request.call_args.kwargs["data"]
    assert data["token"] == RFG and data["deployment_id"] == DEST
    sent = grpc_pb2.GenericRPCRequest.FromString(base64.b64decode(data["request_b64"]))
    assert unmap_arg(sent.args["a"]) == ""  # the frame is relayed to the lab
    assert routes.calls == []


@pytest.mark.parametrize("fn, key", [(GET, "a"), ("IDL:get_drivers", "token")])
def test_the_rfg_token_never_leaves_global(monkeypatch, routes, fn, key):
    transport = _token_backend(monkeypatch)
    grpc_client.rpc_client(function_name=fn, args={key: map_arg(RFG)})
    data = transport.request.call_args.kwargs["data"]
    sent = grpc_pb2.GenericRPCRequest.FromString(base64.b64decode(data["request_b64"]))
    assert data["token"] == RFG and unmap_arg(sent.args[key]) == ""


def test_an_old_global_says_it_takes_no_device_tokens(monkeypatch, routes):
    from remoterf.deployment.http import AccountBackendError

    old = JsonTransport("https://global.example", client=httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(404, json={"detail": "Not Found"}))))
    with pytest.raises(AccountBackendError, match="not_found"):
        old.request("POST", "/v2/deployment/devices/token-rpc", data={"token": RFG})
    _token_backend(monkeypatch).request.side_effect = AccountBackendError("not_found")
    with pytest.raises(RuntimeError, match="does not accept device tokens yet; use the device reference"):
        grpc_client.rpc_client(function_name=GET, args={"a": map_arg(RFG)})


def test_a_handle_without_a_saved_login_is_a_plain_token(monkeypatch, routes):
    _login(monkeypatch, None)
    grpc_client.rpc_client(function_name=GET, args={"a": map_arg("PlutoA@ucla")})
    assert routes.calls == [(GET, {"a": "PlutoA"})]


def test_a_handle_is_exchanged_once_at_the_active_lab(monkeypatch, routes):
    _login(monkeypatch)
    seen = []
    monkeypatch.setattr(direct_path, "current", lambda token=None: seen.append(token))
    routes.replies["ACC:device_token"] = {"Token": "bridge-3-abc", "res_id": 7}
    for _ in range(2):
        grpc_client.rpc_client(function_name=GET, args={"a": map_arg("PlutoA@ucla")})
    assert routes.calls == [
        ("ACC:device_token", {"un": "jdoe", "pw": "s3cret", "h": "PlutoA"}),
        (GET, {"a": "bridge-3-abc"}),
        (GET, {"a": "bridge-3-abc"}),
    ]
    assert seen == ["bridge-3-abc"] * 2  # the direct path keys on the wire token
    assert grpc_client.device_route("PlutoA@ucla").res_id == 7


@pytest.mark.parametrize("reply, wire, printed", [
    ({"Token": "Zm9vYmFy"}, "Zm9vYmFy", False),  # not a handle: the lab hands it back
    ({"Token": "SuperTok_3_force"}, "SuperTok_3_force", False),  # <super token>@<lab>: same path
    ({"Token": "Zm9v__YmFy"}, "Zm9v__YmFy", False),  # a token is never keyed
    ({"Token": "PlutoA", "why": "no active reservation of yours on PlutoA right now"}, "PlutoA", True),
    ({"ace": "No matching server function to call: device_token"}, "PlutoA", False),  # old server
])
def test_the_lab_decides_handle_or_token(monkeypatch, routes, capsys, reply, wire, printed):
    _login(monkeypatch)
    routes.replies["ACC:device_token"] = reply
    t = f"{reply.get('Token', 'PlutoA')}@ucla" if "why" not in reply else "PlutoA@ucla"
    for _ in range(2):
        grpc_client.rpc_client(function_name=GET, args={"a": map_arg(t)})
    assert routes.calls[0] == ("ACC:device_token", {"un": "jdoe", "pw": "s3cret", "h": t[:-len("@ucla")]})
    assert [c for c in routes.calls if c[0] == GET] == [(GET, {"a": wire})] * 2
    err = capsys.readouterr().err
    if printed:
        assert err.count("no active reservation of yours on PlutoA right now; using 'PlutoA' as a token") == 1
    else:
        assert err == ""


def test_a_saved_home_that_is_not_active_gets_its_own_channel(monkeypatch, routes):
    homes.save_state({"global_account": None, "last_target": None, "homes": {
        "otheruni": {"deployment_id": None, "display_name": "x", "transport": "grpc",
                     "routes": [], "cert_sha256": None}}})
    route = {"kind": "direct", "host": "198.51.100.7", "port": 12321, "cert_port": 12322}
    monkeypatch.setattr(homes, "select_route", lambda home: (route, b"cert"))
    monkeypatch.setattr(homes, "recall_login", lambda name: {"username": "jdoe", "secret": "s"} if name == "otheruni" else None)
    channels = []
    monkeypatch.setattr(grpc_client, "build_secure_channel", lambda target, cert: channels.append((target, cert)) or "chan")
    other = Stub({"ACC:device_token": {"Token": "bridge-9-xyz", "res_id": 3}})
    monkeypatch.setattr(grpc_client.grpc_pb2_grpc, "GenericRPCStub", lambda channel: other)
    monkeypatch.setattr(direct_path, "current", _never)
    monkeypatch.setattr(direct_path, "_credentials", _never)

    grpc_client.rpc_client(function_name=GET, args={"a": map_arg("PlutoA@otheruni")})
    assert channels == [("198.51.100.7:12321", b"cert")]
    assert other.calls == [
        ("ACC:device_token", {"un": "jdoe", "pw": "s", "h": "PlutoA"}),
        (GET, {"a": "bridge-9-xyz"}),
    ]
    assert routes.calls == []


def test_an_unknown_deployment_is_one_standard_error(monkeypatch, routes):
    _login(monkeypatch)
    with pytest.raises(RuntimeError) as exc:
        grpc_client.rpc_client(function_name=GET, args={"a": map_arg("PlutoA@nowhere")})
    assert str(exc.value) == _unreachable("nowhere")
    assert routes.calls == []
    with pytest.raises(RuntimeError, match="can't reach nowhere"):
        grpc_client.device_route(f"{RFG}@nowhere")  # discovery refuses (_never)
    with pytest.raises(ValueError):
        grpc_client.device_route("PlutoA@")


# ── re-resolve after a refusal ───────────────────────────────────────

def _flaky_lab(routes, res_ids):
    tokens = iter(res_ids)

    def exchange(args):
        n, res_id = next(tokens)
        return {"Token": f"bridge-{n}", "res_id": res_id}

    routes.replies["ACC:device_token"] = exchange
    routes.replies[GET] = lambda args: {"a": "no device available / invalid token"} if args["a"] == "bridge-1" else {"tx_lo": 5}


def test_a_lost_bridge_token_is_renewed_once(monkeypatch, routes):
    _login(monkeypatch)
    _flaky_lab(routes, [(1, 7), (2, 7)])
    response = grpc_client.rpc_client(function_name=GET, args={"a": map_arg("PlutoA@ucla")})
    assert unmap_arg(response.results["tx_lo"]) == 5
    assert [c[1]["a"] for c in routes.calls if c[0] == GET] == ["bridge-1", "bridge-2"]


def test_a_new_reservation_is_never_continued_silently(monkeypatch, routes):
    _login(monkeypatch)
    _flaky_lab(routes, [(1, 7), (2, 8)])
    with pytest.raises(RuntimeError, match="reservation changed; re-create the driver"):
        grpc_client.rpc_client(function_name=GET, args={"a": map_arg("PlutoA@ucla")})


def _call(t="PlutoA@ucla"):
    return grpc_client.rpc_client(function_name=GET, args={"a": map_arg(t)})


def test_a_fallback_is_asked_again_once_the_slot_starts(monkeypatch, routes):
    _login(monkeypatch)
    why = {"Token": "PlutoA", "why": "no active reservation of yours on PlutoA right now"}
    answers = iter([why, why, {"Token": "bridge-1", "res_id": 7}])
    routes.replies["ACC:device_token"] = lambda args: next(answers)
    routes.replies[GET] = lambda args: {"tx_lo": 5} if args["a"] == "bridge-1" else {"a": "no device available / invalid token"}
    with pytest.raises(RuntimeError, match="invalid token"):
        _call()  # before the slot
    assert unmap_arg(_call().results["tx_lo"]) == 5
    assert [c[1]["a"] for c in routes.calls if c[0] == GET] == ["PlutoA", "PlutoA", "bridge-1"]


def test_a_login_saved_later_is_used(monkeypatch, routes):
    _login(monkeypatch, None)
    routes.replies["ACC:device_token"] = {"Token": "bridge-1", "res_id": 7}
    routes.replies[GET] = lambda args: {"tx_lo": 5} if args["a"] == "bridge-1" else {"a": "no device available / invalid token"}
    with pytest.raises(RuntimeError, match="invalid token"):
        _call()
    _login(monkeypatch)
    assert unmap_arg(_call().results["tx_lo"]) == 5


def test_an_ordinary_device_error_does_not_ask_again(monkeypatch, routes):
    _login(monkeypatch)
    routes.replies["ACC:device_token"] = {"Token": "bridge-1", "res_id": 7}
    routes.replies[GET] = {"a": "frequency out of range"}
    for _ in range(3):
        with pytest.raises(RuntimeError, match="out of range"):
            _call()
    assert [c[0] for c in routes.calls].count("ACC:device_token") == 1


def test_the_idl_adopts_the_next_reservation(monkeypatch, routes):
    _login(monkeypatch)
    _flaky_lab(routes, [(1, 7), (2, 8)])
    routes.replies["IDL:get_drivers"] = lambda args: (
        {"error": "No schema available for this token (invalid token or device offline)"}
        if args["token"] == "bridge-1" else {"schema": "{}"})
    response = grpc_client.rpc_client(function_name="IDL:get_drivers", args={"token": map_arg("PlutoA@ucla")})
    assert unmap_arg(response.results["schema"]) == "{}"
    assert grpc_client.device_route("PlutoA@ucla").wire == "bridge-2"


def test_v2_renews_a_dead_bridge_token_once(monkeypatch, routes):
    from remoterf.core.v2_errors import RemoteRFReservationError

    _login(monkeypatch)
    _flaky_lab(routes, [(1, 7), (2, 8)])
    sent = []

    class Control:
        def OpenSession(self, request, timeout=None):
            sent.append(request.token)
            return grpc_pb2.OpenSessionResponse(error=grpc_pb2.ErrorEnvelope(
                code="RemoteRFReservationError", message="invalid token or inactive reservation"))

    with pytest.raises(RemoteRFReservationError):
        DynamicV2Transport(control_stub=Control(), sample_stub=object()).open_session("PlutoA@ucla", "h")
    assert sent == ["bridge-1", "bridge-2"]


# ── Global users ─────────────────────────────────────────────────────

def test_a_global_user_handle_maps_to_the_listed_device(monkeypatch, routes):
    monkeypatch.setattr(state, "load_target", lambda: {"transport": "https-json", "origin": "https://g", "deployment_id": GLOBAL})
    listing = {"devices": [
        {"deployment_id": "22222222-2222-4222-8222-222222222222", "handle": "PlutoA", "device_id": "x:1"},
        {"deployment_id": DEST, "handle": "PlutoA", "device_id": REF},
    ]}
    monkeypatch.setattr(grpc_client, "federated_backend", lambda: SimpleNamespace(devices=lambda: listing))
    monkeypatch.setattr(homes, "discover", lambda host: {"deployment_id": DEST})
    assert grpc_client.device_route("PlutoA@ucla") == grpc_client.DeviceRoute(REF, "global")
    # The handle is the name: spaces and underscores are one.
    assert grpc_client._key("pluto A") == grpc_client._key("pluto_A") == grpc_client._key(" pluto\tA ") \
        == grpc_client._key("pluto__A") == "pluto_A"
    listing["devices"][1]["handle"] = "pluto_A"
    for typed in ("pluto A@ucla", "pluto__A@ucla"):
        assert grpc_client.device_route(typed) == grpc_client.DeviceRoute(REF, "global")
    with pytest.raises(RuntimeError) as exc:
        grpc_client.device_route("PlutoB@ucla")
    assert str(exc.value) == _unreachable("ucla")
    assert routes.calls == []


# ── Dynamic v2 and the IDL ───────────────────────────────────────────

def test_v2_over_global_rides_the_device_plane_without_an_inner_token(monkeypatch):
    sent = []

    def rpc_client(*, function_name, args, connection=None):
        sent.append((function_name, unmap_arg(args["a"]), grpc_pb2.OpenSessionRequest.FromString(unmap_arg(args["wire"]))))
        raise RuntimeError("stop here")

    monkeypatch.setattr(grpc_client, "rpc_client", rpc_client)
    transport = DynamicV2Transport(token=RFG)
    assert isinstance(transport.control, _DevicePlaneStub)
    with pytest.raises(Exception) as exc:
        transport.open_session(RFG, "sha256:abc")
    assert str(exc.value) == "stop here"  # Global's refusal as is, not a "partner device"
    assert sent[0][:2] == ("DynamicV2:OpenSession:WIRE", RFG) and sent[0][2].token == ""


def test_v2_at_a_saved_home_uses_that_homes_channel_and_wire_token(monkeypatch):
    channel = Mock()
    grpc_client._routes["PlutoA@otheruni"] = grpc_client.DeviceRoute(
        "bridge-9", "home", SimpleNamespace(channel=channel))
    monkeypatch.setattr(grpc_client, "get_active_channel", _never)
    DynamicV2Transport(token="PlutoA@otheruni")
    assert channel.unary_unary.called

    requests = []

    class Control:
        def OpenSession(self, request, timeout=None):
            requests.append(request)
            raise RuntimeError("stop here")

    with pytest.raises(RuntimeError, match="stop here"):
        DynamicV2Transport(control_stub=Control(), sample_stub=object()).open_session("PlutoA@otheruni", "h")
    assert requests[0].token == "bridge-9"


def test_fetch_idl_tries_v2_for_an_rfg_token_then_v1(monkeypatch):
    from remoterf.core import v2_errors
    from remoterf.drivers import dynamic_device, dynamic_v2

    tried, calls = [], []

    def refuse(token):
        tried.append(token)
        raise v2_errors.RemoteRFReservationError("v1 only")

    def v1(*, function_name, args, connection=None):
        calls.append((function_name, unmap_arg(args["token"])))
        return grpc_pb2.GenericRPCResponse(results={"schema": map_arg('{"device_type": "pluto"}')})

    monkeypatch.setattr(dynamic_v2, "fetch_schema_v2", refuse)
    monkeypatch.setattr(grpc_client, "rpc_client", v1)
    assert dynamic_device.fetch_idl(token=RFG)["device_type"] == "pluto"
    assert tried == [RFG] and calls == [("IDL:get_drivers", RFG)]
    dynamic_device.fetch_idl(token=REF)  # a bare ref: v1 only, as before
    assert tried == [RFG] and calls[-1] == ("IDL:get_drivers", REF)


# ── what the shell shows ─────────────────────────────────────────────

def _shown(monkeypatch, client_app):
    lines = []
    monkeypatch.setattr(client_app, "printf", lambda *parts: lines.append("".join(parts[::2])))
    return lines


def _row(lines, *words):
    return any(line.split() == list(words) for line in lines)


def test_a_lab_reservation_shows_the_token_and_the_handle(monkeypatch):
    import datetime
    import remoterf.core.app as client_app

    lines = _shown(monkeypatch, client_app)
    account = Mock()
    account.reserve_device.return_value = "Zm9vYmFy"
    account.install_reserved_driver.return_value = "pluto"
    monkeypatch.setattr(client_app, "account", account)
    monkeypatch.setattr(client_app, "home_name", "ucla")
    client_app._native_reserve_slot(3, datetime.date(2026, 1, 1), (0, 1), "10:00", "11:00",
                                    device_label="Pluto SDR (OTA) [PlutoA] (online)")
    assert "Reservation successful on PlutoA @ucla for 2026-01-01 10:00-11:00." in lines
    assert _row(lines, "Token", "Zm9vYmFy@ucla") and _row(lines, "Device", "handle", "PlutoA@ucla")
    assert any("logged in to ucla" in line for line in lines)
    assert '  sdr = adi.Pluto(token="Zm9vYmFy@ucla")' in lines
    assert '  sdr = adi.Pluto(token="PlutoA@ucla")' in lines
    account.install_reserved_driver.assert_called_once_with("Zm9vYmFy", 3)


def test_an_untagged_device_is_reached_by_its_name(monkeypatch):
    import datetime
    import remoterf.core.app as client_app

    lines = _shown(monkeypatch, client_app)
    account = Mock()
    account.reserve_device.return_value = "Zm9vYmFy"
    account.install_reserved_driver.return_value = "usrp"
    monkeypatch.setattr(client_app, "account", account)
    monkeypatch.setattr(client_app, "home_name", "ucla")
    client_app._native_reserve_slot(4, datetime.date(2026, 1, 1), (0, 1), "10:00", "11:00",
                                    device_label="USRP B210 (online)")
    assert "Reservation successful on USRP_B210 @ucla for 2026-01-01 10:00-11:00." in lines
    assert _row(lines, "Token", "Zm9vYmFy@ucla") and _row(lines, "Device", "handle", "USRP_B210@ucla")
    assert '  sdr = uhd.usrp.MultiUSRP(token="USRP_B210@ucla")' in lines
    # A failed driver install: the token is still shown, just no usage lines.
    lines.clear()
    account.install_reserved_driver.return_value = None
    client_app._native_reserve_slot(4, datetime.date(2026, 1, 1), (0, 1), "10:00", "11:00",
                                    device_label="USRP B210 (online)")
    assert _row(lines, "Token", "Zm9vYmFy@ucla") and not any("sdr =" in line for line in lines)


def test_a_faulted_device_keeps_its_handle_and_a_partner_has_none(monkeypatch):
    import datetime
    import remoterf.core.app as client_app

    lines = _shown(monkeypatch, client_app)
    account = Mock()
    account.reserve_device.return_value = "Zm9vYmFy"
    account.install_reserved_driver.return_value = "pluto"
    monkeypatch.setattr(client_app, "account", account)
    monkeypatch.setattr(client_app, "home_name", "ucla")
    client_app._native_reserve_slot(3, datetime.date(2026, 1, 1), (0, 1), "10:00", "11:00",
                                    device_label="Pluto SDR [PlutoA] (offline: x (cable/port/board fault))")
    assert "Reservation successful on PlutoA @ucla for 2026-01-01 10:00-11:00." in lines
    assert _row(lines, "Device", "handle", "PlutoA@ucla")
    # A partner's bracketed name is not a handle of ours.
    lines.clear()
    client_app._native_reserve_slot(1001, datetime.date(2026, 1, 1), (0, 1), "10:00", "11:00",
                                    device_label="Pluto [B] @mit (online)")
    assert "Reservation successful on Pluto [B] @mit for 2026-01-01 10:00-11:00." in lines
    assert not any("Device handle" in line for line in lines)


def test_a_global_reservation_shows_the_token_and_the_handle(monkeypatch):
    import datetime
    import remoterf.core.app as client_app

    lines = _shown(monkeypatch, client_app)
    account = Mock()
    account.reserve_device.return_value = RFG
    account.install_reserved_driver.return_value = "pluto"
    monkeypatch.setattr(client_app, "account", account)
    monkeypatch.setattr(homes, "discover", lambda host: {"deployment_id": DEST})
    day = datetime.date(2026, 1, 1)
    client_app._federated_reserve_slot(REF, day, (0, 1), "10:00", "11:00", deployment_name="UCLA",
                                       device_name="Pluto SDR", device_handle="PlutoA")
    assert "Reservation successful on PlutoA @ucla for 2026-01-01 10:00-11:00." in lines
    assert _row(lines, "Token", f"{RFG}@ucla") and _row(lines, "Device", "handle", "PlutoA@ucla")
    assert any("logged in to RemoteRF Global" in line for line in lines)
    # A display name that is not the lab's slug: the bare token, which works
    # as is, and no handle (it needs @<slug> to route).
    lines.clear()
    client_app._federated_reserve_slot(REF, day, (0, 1), "10:00", "11:00", deployment_name="UCLA Lab",
                                       device_name="Pluto SDR", device_handle="PlutoA")
    assert _row(lines, "Token", RFG) and not any("Device handle" in line for line in lines)
    assert not any("has no handle" in line for line in lines)
    assert any("Its handle PlutoA can't be used from here: this lab's Global name is unknown" in line
               for line in lines)


def test_a_global_listing_shows_the_handle():
    import remoterf.core.app as client_app

    assert client_app._handle_tag({"handle": "PlutoA"}) == " [PlutoA]"
    assert client_app._handle_tag({}) == ""
    assert client_app._handle_tag({"display_name": "pluto A", "handle": "pluto_A"}) == ""


def test_install_after_a_global_reservation_uses_the_ref():
    from unittest.mock import patch
    from remoterf.core.grpc_acc import RemoteRFAccount

    account = RemoteRFAccount(backend=Mock(capabilities={"account_transport": "https-json"}))
    account.backend.reserve.return_value = {"device_id": REF, "token": RFG}
    assert account.reserve_device(REF, Mock(timestamp=lambda: 1), Mock(timestamp=lambda: 2)) == RFG
    with patch("remoterf.drivers.dynamic_device.install_driver") as install:
        account.install_reserved_driver(RFG, REF)
        account.install_reserved_driver(REF, REF)  # an older Global: the ref
    # The ref needs no live slot; the rfg_ token would until the slot starts.
    assert [c.kwargs for c in install.call_args_list] == [{"device_id": REF}] * 2


def _native_shell(monkeypatch, client_app, answers):
    """A lab account (jdoe@ucla) with a handled device, a plain one and a partner's."""
    from remoterf.common.utils import map_arg

    results = lambda d: SimpleNamespace(results={k: map_arg(v) for k, v in d.items()})
    account = Mock(is_https_home=False, username="jdoe")
    account.get_perms.return_value = results({"UC": str([["Normal User"]]), "details": json.dumps(
        {"devices": [3, 4], "caps": {"3": {"max_reservation_time_sec": 3600, "max_horizon_sec": 7 * 86400}}})})
    account.get_devices.return_value = results({"3": "Pluto SDR (OTA) [PlutoA] (online)", "4": "USRP B210 (online)",
                                                "1001": "Pluto SDR @mit (online)"})
    account.get_reservations.return_value = results({})
    account.reserve_device.return_value = "Zm9vYmFy"
    account.install_reserved_driver.return_value = "pluto"
    monkeypatch.setattr(client_app, "account", account)
    monkeypatch.setattr(client_app, "home_name", "ucla")
    monkeypatch.setattr(client_app, "current_route", None)
    prompts, answers = [], iter(answers)
    monkeypatch.setattr(client_app, "session", SimpleNamespace(
        prompt=lambda text: prompts.append("".join(part[1] for part in text)) or next(answers)))
    return account, prompts


@pytest.mark.parametrize("pick, device_id, shown, where, handle", [
    ("0", 3, "Pluto SDR (OTA) [PlutoA]", "PlutoA @ucla", "PlutoA@ucla"),
    # A partner's device: the token is the HOME's, so @ucla; no handle.
    ("2", 1001, "Pluto SDR @mit", "Pluto SDR @mit", None),
])
def test_resdev_names_the_device_and_shows_the_token_block(monkeypatch, pick, device_id, shown, where, handle):
    import remoterf.core.app as client_app

    lines = _shown(monkeypatch, client_app)
    account, prompts = _native_shell(monkeypatch, client_app, [pick, "1", "0", "y"])
    client_app.interactive_reserve_next_days_auto()
    assert prompts[-1].startswith("Reserve ") and prompts[-1].endswith(f" for {shown}?\nConfirm reservation? (y/n): ")
    assert any(line.startswith(f"Available time slots for device {shown} (") for line in lines)
    assert any(line.startswith(f"Reservation successful on {where} for ") for line in lines)
    assert _row(lines, "Token", "Zm9vYmFy@ucla")
    assert _row(lines, "Device", "handle", handle) if handle else not any("Device handle" in line for line in lines)
    assert account.reserve_device.call_args.args[0] == device_id
    account.install_reserved_driver.assert_called_once_with("Zm9vYmFy", device_id)


def test_resdev_n_lists_numbered_devices(monkeypatch):
    import remoterf.core.app as client_app

    lines = _shown(monkeypatch, client_app)
    account, _ = _native_shell(monkeypatch, client_app, ["1", "2026-01-01 10:00", "2026-01-01 11:00"])
    client_app.reserve()
    assert "1.   Device Name: Pluto SDR (OTA) [PlutoA] (online)" in lines
    assert _row(lines, "Device", "handle", "PlutoA@ucla")
    assert account.reserve_device.call_args.args[0] == 3


def test_resdev_n_names_the_lab_as_the_token_does(monkeypatch):
    import remoterf.core.app as client_app

    lines = _shown(monkeypatch, client_app)
    _native_shell(monkeypatch, client_app, ["1", "2026-01-01 10:00", "2026-01-01 11:00"])
    monkeypatch.setattr(client_app, "current_route", {"home": {"display_name": "127.0.0.1:29496"}})
    client_app.reserve()
    assert any(line.startswith("Reservation successful on PlutoA @ucla for ") for line in lines)


def test_a_handle_is_the_last_bracketed_word():
    import remoterf.core.app as client_app

    assert client_app._name_and_handle("SimSDR-0 [Lab]A]") == ("SimSDR-0", "Lab]A")
    assert client_app._name_and_handle("Bench [x] [B1]") == ("Bench [x]", "B1")
    assert client_app._name_and_handle("Bench [a b]") == ("Bench [a b]", "Bench_[a_b]")
    assert client_app._name_and_handle("pluto A") == ("pluto A", "pluto_A")
    assert [client_app._name_and_handle(n)[1] for n in ("a@b", "rfg_x", "bridge-x", " _ ")] == [None] * 4


def test_a_shell_without_a_home_shows_the_profiles_lab(monkeypatch):
    """The first shell after --register has no home yet; the profile names the lab."""
    import remoterf.core.app as client_app

    lines = _shown(monkeypatch, client_app)
    account = Mock()
    account.install_reserved_driver.return_value = "pluto"
    monkeypatch.setattr(client_app, "account", account)
    monkeypatch.setattr(client_app, "home_name", None)
    client_app._print_native_reserved(2, "SimSDR-2 [SimB] (online)", "today", "Zm9vYmFy")
    assert "Reservation successful on SimB @ucla for today." in lines
    assert _row(lines, "Token", "Zm9vYmFy@ucla") and _row(lines, "Device", "handle", "SimB@ucla")
    # A profile with no lab name: the bare token, and the handle is not denied.
    env = Path.home() / ".config" / "remoterf-client" / ".env"
    env.write_text(env.read_text().replace("REMOTERF_PROFILE=ucla\n", ""))
    lines.clear()
    client_app._print_native_reserved(2, "SimSDR-2 [SimB] (online)", "today", "Zm9vYmFy")
    assert _row(lines, "Token", "Zm9vYmFy") and not any("has no handle" in line for line in lines)
    assert any("Its handle SimB can't be used from here" in line for line in lines)


def test_a_refusal_is_printed_once_though_asked_again(monkeypatch, routes, capsys):
    _login(monkeypatch)
    routes.replies["ACC:device_token"] = {"Token": "PlutoA", "why": "no active reservation of yours on PlutoA right now"}
    routes.replies[GET] = {"a": "no device available / invalid token"}
    for _ in range(2):
        with pytest.raises(RuntimeError, match="invalid token"):
            _call()
    assert [c[0] for c in routes.calls].count("ACC:device_token") == 3  # asked again on each refusal
    assert capsys.readouterr().err.count("using 'PlutoA' as a token") == 1


def test_a_holder_change_after_a_refused_call_still_raises(monkeypatch, routes):
    _login(monkeypatch)
    why = {"Token": "PlutoA", "why": "no active reservation of yours on PlutoA right now"}
    answers = iter([{"Token": "bridge-1", "res_id": 7}, why, {"Token": "bridge-2", "res_id": 8}])
    routes.replies["ACC:device_token"] = lambda args: next(answers)
    live = {"bridge-1"}
    routes.replies[GET] = lambda args: {"tx_lo": 5} if args["a"] in live else {"a": "no device available / invalid token"}
    assert unmap_arg(_call().results["tx_lo"]) == 5
    live.clear()  # cancelled; another user holds the device
    with pytest.raises(RuntimeError, match="invalid token"):
        _call()
    live.add("bridge-2")  # reserved again: a new reservation
    with pytest.raises(RuntimeError, match="reservation changed; re-create the driver"):
        _call()


def test_an_addr_override_is_not_the_saved_profiles_lab(monkeypatch, routes, tmp_path):
    """REMOTERF_ADDR/CA point the channel at another lab: x@ucla must not go there."""
    ca = tmp_path / "other.crt"
    ca.write_bytes(b"pem")
    monkeypatch.setenv("REMOTERF_ADDR", "198.51.100.7:12321")
    monkeypatch.setenv("REMOTERF_CA_CERT", str(ca))
    _login(monkeypatch)
    with pytest.raises(RuntimeError, match="can't reach ucla"):
        _call()
    assert routes.calls == []
