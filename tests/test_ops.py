"""remoterf.ops over fake homes: devices, slots, reservations, errors, and
nothing ever written to stdout."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import grpc
import pytest

from remoterf import ops
from remoterf.common.grpc import grpc_pb2
from remoterf.common.utils import map_arg
from remoterf.core import direct_path, grpc_acc, grpc_client
from remoterf.deployment import state
from remoterf.deployment.http import AccountBackendError

DAY = datetime(2026, 3, 2)  # naive local, like the lab's reservation rows
DEP = "11111111-1111-4111-8111-111111111111"
REF = f"{DEP}:1"


def _r(values):
    return SimpleNamespace(results={k: map_arg(v) for k, v in values.items()})


def _at(hour):
    return f"{DAY:%Y-%m-%d} {hour:02d}:00:00"


class Lab:
    """jdoe@ucla: Pluto_A (id 3: 60 min blocks, 2 days ahead), a USRP with no
    caps (id 4), and a partner's device (no handle)."""

    username, password = "jdoe", "s3cret"

    def __init__(self):
        self.res = {"7": f"jdoe,3,{_at(12)},{_at(13)}", "8": f"reserved,4,{_at(9)},{_at(10)}"}
        self.refusal = None

    def get_devices(self):
        return _r({"3": "Pluto SDR [Pluto_A] (online)", "4": "USRP B210 (offline)", "1001": "Pluto @mit (online)"})

    def get_perms(self):
        return _r({"UC": str([["Normal User"]]), "details": json.dumps(
            {"devices": [3, 4], "caps": {"3": {"max_reservation_time_sec": 3600, "max_horizon_sec": 2 * 86400}}})})

    def get_reservations(self):
        return _r(self.res)

    def reserve_device(self, device_id, start, end):
        if self.refusal:
            raise RuntimeError(self.refusal)
        self.res["9"] = f"jdoe,{device_id},{start:%Y-%m-%d %H:%M:%S},{end:%Y-%m-%d %H:%M:%S}"
        return "Zm9vYmFy"

    def cancel_reservation(self, rid):
        return _r({"UC": "ok"} if rid in self.res else {"ace": "Invalid reservation ID or user does not own reservation."})


class Home:
    """A Global HOME listing one Pluto at ucla (30 min blocks)."""

    def __init__(self, error=None):
        self.error = error

    def devices(self):
        return {"devices": [{"device_id": REF, "deployment_id": DEP, "deployment_name": "ucla", "local_device_id": "1",
                             "display_name": "Pluto 01", "device_type": "pluto", "online": True, "handle": "Pluto_01",
                             "max_reservation_time_sec": 1800}],
                "destinations": [{"deployment_id": DEP, "display_name": "ucla", "status": "ok"}]}

    def availability(self):
        if self.error:
            raise self.error
        data = self.devices()
        data["devices"][0].update(state="busy", until=int(DAY.timestamp()) + 600, next=None)
        data["destinations"].append({"deployment_id": "x", "display_name": "mit", "status": "unavailable"})
        return data

    def reservations(self):
        start = int(datetime(2026, 3, 2, 13, tzinfo=timezone.utc).timestamp())
        return {"reservations": [{"reservation_id": f"{DEP}:9", "device_id": REF, "deployment_id": DEP,
                                  "start_time": start, "end_time": start + 3600, "mine": True}],
                "destinations": [{"deployment_id": DEP, "display_name": "ucla", "status": "ok"}]}

    def reserve(self, device_id, start, end):
        return {"reservation_id": f"{DEP}:10", "device_id": device_id, "token": "rfg_abc"}

    def cancel(self, rid):
        return True

    def permissions(self):
        return {"federation": [{"display_name": "ucla", "status": "ok"}, {"display_name": "mit", "status": "blocked"}]}


@pytest.fixture
def lab(monkeypatch):
    calls = []

    def rpc(*, function_name, args):
        calls.append(function_name)
        return _r({"UC": json.dumps({"interval": 10, "devices": [
            {"local_device_id": "3", "display_name": "Pluto SDR", "handle": "Pluto_A", "device_type": "pluto",
             "online": True, "state": "free", "until": None, "next": int(DAY.timestamp()) + 3600},
            {"local_device_id": "4", "display_name": "USRP B210", "handle": "USRP_B210", "device_type": "usrp",
             "online": False, "state": "busy", "until": int(DAY.timestamp()), "next": None}]})})

    monkeypatch.setattr(grpc_client, "rpc_client", rpc)
    return ops.Session(Lab(), "ucla", "deployment")


@pytest.fixture
def home(monkeypatch):
    def deployment_id(name):
        if name != "ucla":
            raise RuntimeError(f"can't reach {name}")
        return DEP

    monkeypatch.setattr(grpc_client, "_deployment_id", deployment_id)
    return ops.Session(SimpleNamespace(username="jdoe", backend=Home()), "global", "global")


def test_lab_devices_carry_handles_state_and_limits(lab):
    out = ops.list_devices(lab)
    assert [d["handle"] for d in out["devices"]] == ["Pluto_A@ucla"]
    pluto = out["devices"][0]
    assert (pluto["state"], pluto["slot_minutes"], pluto["max_days_ahead"], pluto["deployment"]) == ("free", 60, 2, "ucla")
    assert pluto["next_ts"] == int(DAY.timestamp()) + 3600 and pluto["next"].endswith("Z") and pluto["until"] is None
    every = ops.list_devices(lab, online_only=False)["devices"]
    assert [(d["handle"], d["slot_minutes"], d["max_days_ahead"]) for d in every] == [
        ("Pluto_A@ucla", 60, 2), ("USRP_B210@ucla", 30, None)]
    assert all("_id" not in d for d in every)
    assert [d["handle"] for d in ops.list_devices(lab, online_only=False, type="USRP")["devices"]] == ["USRP_B210@ucla"]
    assert ops.list_devices(lab, deployment="mit")["devices"] == []


def test_global_devices_and_whoami(home):
    out = ops.list_devices(home)
    assert [(d["handle"], d["state"], d["slot_minutes"]) for d in out["devices"]] == [("Pluto_01@ucla", "busy", 30)]
    assert out["incomplete"] and out["notes"] == ["@mit is offline; the list may be incomplete."]
    me = ops.whoami(home)
    assert (me["account"], me["home_kind"], me["deployments"]) == ("jdoe@global", "global", ["ucla"])


def test_lab_slots_align_and_skip_the_past_and_taken(lab):
    out = ops.find_slots(lab, "Pluto A@UCLA", now=DAY.replace(hour=10, minute=5))  # spaces == underscores
    starts = [datetime.fromtimestamp(s["start_ts"]).hour for s in out["slots"]]
    # The block running now is still offered; 12:00 is taken.
    assert starts == [10, 11, *range(13, 24)]
    assert (out["handle"], out["slot_minutes"], out["days"]) == ("Pluto_A@ucla", 60, 1)
    assert out["slots"][0]["end_ts"] - out["slots"][0]["start_ts"] == 3600
    with pytest.raises(ops.RemoteRFOpsError) as raised:
        ops.find_slots(lab, "Pluto_A@ucla", days=3, now=DAY)
    assert (raised.value.code, raised.value.exit_code) == ("invalid_argument", 2)


def test_global_slots_in_a_named_zone(home):
    out = ops.find_slots(home, "Pluto_01@ucla", tz="UTC", now=datetime(2026, 3, 2, 10, 5, tzinfo=timezone.utc))
    starts = [s["start"] for s in out["slots"]]
    assert out["timezone"] == "UTC" and out["slot_minutes"] == 30
    assert starts[0] == "2026-03-02T10:00:00Z" and starts[-1] == "2026-03-02T23:30:00Z"
    assert "2026-03-02T13:00:00Z" not in starts and "2026-03-02T13:30:00Z" not in starts and "2026-03-02T14:00:00Z" in starts


def test_build_slots_and_slot_minutes():
    assert [ops.slot_minutes(s) for s in (0, None, 5 * 60, 25 * 60, 3600, 10 ** 6)] == [30, 30, 5, 20, 60, 720]
    midnight = datetime(2026, 3, 1)
    assert len(ops.build_slots(DAY.date(), 45, [], now=midnight)) == 32  # the last block ends at midnight
    assert len(ops.build_slots(DAY.date(), 420, [], now=midnight)) == 3  # 21:00-04:00 would cross it
    assert len(ops.build_slots(DAY.date(), 60, [], now=midnight, days=2)) == 48
    assert ops.build_slots(DAY.date(), 0, [], now=midnight) == []
    late = (datetime(2026, 3, 2, 23, 30), datetime(2026, 3, 3, 0, 30))  # it blocks the next day's first slot too
    assert (datetime(2026, 3, 3), datetime(2026, 3, 3, 0, 30)) not in ops.build_slots(DAY.date(), 30, [late], now=midnight, days=2)


@pytest.mark.parametrize("day, count", [("2026-03-08", 46), ("2026-11-01", 47)])
def test_slots_on_dst_change_days_are_real_lengths(home, day, count):
    out = ops.find_slots(home, "Pluto_01@ucla", start_day=day, tz="America/Los_Angeles",
                         now=datetime(2026, 3, 8, 8, tzinfo=timezone.utc))
    starts = [s["start_ts"] for s in out["slots"]]
    assert len(starts) == count == len(set(starts)) and all(s["end_ts"] - s["start_ts"] == 1800 for s in out["slots"])


def test_slots_report_an_iana_zone(home, monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Paris")
    assert ops.find_slots(home, "Pluto_01@ucla", now=DAY.astimezone())["timezone"] == "Europe/Paris"
    monkeypatch.setenv("TZ", "EST5EDT,M3.2.0,M11.1.0")
    assert ops._local_zone() is None


def test_lab_reserve_finds_its_id_and_keeps_the_token_back(lab):
    start = DAY.replace(hour=14).astimezone()
    out = ops.reserve(lab, "Pluto_A@ucla", start, start.isoformat().replace("T14", "T15"))
    assert (out["reservation_id"], out["token_ref"], out["use_as_token"]) == ("9", "9", "Pluto_A@ucla")
    assert "token" not in out and out["start_ts"] == int(start.timestamp())
    assert ops.reserve(lab, "Pluto_A@ucla", start, start.replace(hour=15), reveal_token=True)["token"] == "Zm9vYmFy@ucla"
    assert ops.list_reservations(lab)["reservations"][-1]["reservation_id"] == "9"
    assert ops.cancel(lab, "9") == {"cancelled": True, "reservation_id": "9"}


def test_lab_reserve_finds_its_id_when_the_lab_is_in_another_zone(lab, monkeypatch):
    def reserve_device(device_id, start, end):  # the lab writes its rows three hours ahead of this machine
        shift = timedelta(hours=3)
        lab.account.res["9"] = f"jdoe,{device_id},{start + shift:%Y-%m-%d %H:%M:%S},{end + shift:%Y-%m-%d %H:%M:%S}"
        return "Zm9vYmFy"

    monkeypatch.setattr(lab.account, "reserve_device", reserve_device)
    start = DAY.replace(hour=14).astimezone()
    assert ops.reserve(lab, "Pluto_A@ucla", start, start + timedelta(hours=1))["reservation_id"] == "9"


def test_global_reserve_and_reservations(home):
    out = ops.reserve(home, "Pluto_01@ucla", "2026-03-02T15:00:00Z", "2026-03-02T15:30:00Z", reveal_token=True)
    assert (out["reservation_id"], out["token"], out["use_as_token"]) == (f"{DEP}:10", "rfg_abc@ucla", "Pluto_01@ucla")
    assert out["start"] == "2026-03-02T15:00:00Z"
    rows = ops.list_reservations(home)["reservations"]
    assert [(r["handle"], r["device"], r["deployment"], r["start"], r["mine"]) for r in rows] == [
        ("Pluto_01@ucla", "Pluto 01", "ucla", "2026-03-02T13:00:00Z", True)]


def test_lab_reservations_name_devices_by_handle(lab):
    rows = ops.list_reservations(lab, mine_only=False)["reservations"]
    assert [(r["reservation_id"], r["handle"], r["mine"]) for r in rows] == [("8", "USRP_B210@ucla", False), ("7", "Pluto_A@ucla", True)]
    assert rows[0]["device"] == "USRP B210" and rows[1]["start_ts"] == int(datetime.fromisoformat(_at(12)).timestamp())


def _code(call, *args, **kwargs):
    with pytest.raises(ops.RemoteRFOpsError) as raised:
        call(*args, **kwargs)
    return raised.value


def test_errors_are_typed_and_mapped(lab, home, monkeypatch):
    lab.account.refusal = "Device already reserved at this time. Please select another time or device."
    err = _code(ops.reserve, lab, "Pluto_A@ucla", "2026-03-02T15:00:00Z", "2026-03-02T16:00:00Z")
    assert (err.code, err.exit_code, err.message) == ("slot_unavailable", 5, lab.account.refusal)
    lab.account.refusal = "Partner refused: reservation_conflict"
    err = _code(ops.reserve, lab, "Pluto_A@ucla", "2026-03-02T15:00:00Z", "2026-03-02T16:00:00Z")
    assert (err.code, err.exit_code, err.message) == (
        "reservation_conflict", 5, "ucla turned the reservation down: someone else has Pluto_A@ucla at that time. Pick another slot.")

    def unreachable(*args):  # as rpc_client raises a gRPC failure
        down = SimpleNamespace(code=lambda: grpc.StatusCode.UNAVAILABLE, details=lambda: "connect failed")
        raise grpc_client.RemoteRFRpcError("down", down)

    with monkeypatch.context() as m:
        m.setattr(lab.account, "reserve_device", unreachable)
        err = _code(ops.reserve, lab, "Pluto_A@ucla", "2026-03-02T15:00:00Z", "2026-03-02T16:00:00Z")
    assert (err.code, err.exit_code, err.retryable) == ("deployment_unreachable", 7, True)
    assert err.message == "Can't reach that deployment right now; check your connection and try again."
    assert _code(ops.find_slots, lab, "Nope@ucla").exit_code == 4
    assert _code(ops.device_api, lab, "Pluto_A@mit").code == "unknown_handle"
    assert _code(ops.cancel, lab, "12").code == "not_found"
    assert _code(ops.reserve, lab, "Pluto_A@ucla", "soon", "later").code == "invalid_argument"

    home.account.backend.error = AccountBackendError("rate_limited", retry_after=7)
    err = _code(ops.list_devices, home)
    assert err.to_dict() == {"error": {"code": "rate_limited", "message": "Asked too recently; try again in 7 s.",
                                       "retryable": True, "retry_after": 7}} and err.exit_code == 5
    home.account.backend.error = AccountBackendError("route_unavailable", provenance="transport")
    err = _code(ops.list_devices, home)
    assert (err.message, err.retryable, err.exit_code) == (
        "That deployment isn't responding right now; try again in a minute.", True, 7)

    def refuse(code):
        def reserve(*args):
            raise AccountBackendError(code, provenance="destination")
        return reserve

    monkeypatch.setattr(home.account.backend, "reserve", refuse("reservation_too_long"))
    err = _code(ops.reserve, home, "Pluto_01@ucla", "2026-03-02T15:00:00Z", "2026-03-02T17:00:00Z")
    assert (err.code, err.exit_code, err.message) == (
        "reservation_too_long", 2, "ucla turned the reservation down: Pluto_01@ucla can be reserved for at most 30 minutes at a time.")
    monkeypatch.setattr(home.account.backend, "reserve", refuse("reservation_limit"))
    err = _code(ops.reserve, home, "Pluto_01@ucla", "2026-03-02T15:00:00Z", "2026-03-02T15:30:00Z")
    assert (err.code, err.exit_code) == ("reservation_limit", 6) and "as many reservations there as you're allowed" in err.message

    monkeypatch.setattr(grpc_client, "rpc_client", lambda **kw: _r({"ace": "Invalid login credentials."}))
    assert _code(ops.list_devices, lab).code == "session_expired"


def test_any_transport_failure_is_unreachable(lab, home):
    for text in ("Partner unreachable", "Partner unreachable: invalid_transport_response"):  # as a lab HOME relays them
        lab.account.refusal = text
        err = _code(ops.reserve, lab, "Pluto_A@ucla", "2026-03-02T15:00:00Z", "2026-03-02T16:00:00Z")
        assert (err.code, err.exit_code, err.retryable, err.message) == (
            "deployment_unreachable", 7, True, "ucla isn't responding right now; try again in a minute.")
    home.account.backend.error = AccountBackendError("invalid_signed_result", provenance="transport")
    err = _code(ops.list_devices, home)
    assert (err.code, err.exit_code, err.retryable) == ("deployment_unreachable", 7, True)


def test_hints_name_cli_commands_not_shell_ones():
    assert ops.RemoteRFOpsError("reservation_limit").message.endswith("Cancel one (remoterf cancel) or wait for one to end.")
    assert "run remoterf reservations again" in ops.RemoteRFOpsError("invalid_reservation_id").message


def test_global_cancel_takes_the_clis_reference(home, monkeypatch):
    seen = []
    monkeypatch.setattr(home.account.backend, "cancel", seen.append)
    ops.cancel(home, "10@UCLA"), ops.cancel(home, f"{DEP}:11")
    assert seen == [f"{DEP}:10", f"{DEP}:11"]


def test_saved_session_without_a_login(monkeypatch):
    monkeypatch.setattr(state, "load_target", lambda: {"origin": None, "transport": "grpc", "deployment_id": None})
    monkeypatch.setattr(direct_path, "_credentials", lambda: None)
    assert _code(ops.saved_session).exit_code == 3
    monkeypatch.setattr(direct_path, "_credentials", lambda: ("jdoe", "tok"))
    monkeypatch.setattr(grpc_client, "_active_home_name", lambda: "ucla")
    s = ops.saved_session()
    assert (s.home, s.home_kind, s.account.username, s.account.quiet) == ("ucla", "deployment", "jdoe", True)

    monkeypatch.setattr(state, "load_target", lambda: {"origin": "https://global.example.org", "transport": "https-json",
                                                       "deployment_id": DEP})

    def no_login():
        raise RuntimeError("Log in to the account home first (run `remoterf`, then `login`)")

    monkeypatch.setattr(grpc_client, "federated_backend", no_login)
    assert _code(ops.saved_session).code == "not_logged_in"

    def fails(error):
        def backend():
            raise error
        return backend

    monkeypatch.setattr(grpc_client, "federated_backend", fails(AccountBackendError("rate_limited", retry_after=9)))
    assert (_code(ops.saved_session).code, _code(ops.saved_session).retry_after) == ("rate_limited", 9)
    monkeypatch.setattr(grpc_client, "federated_backend", fails(AccountBackendError("refresh_token_reused")))
    assert _code(ops.saved_session).code == "session_expired"


def test_relay_and_lookup_failures_keep_their_codes(lab, home, monkeypatch):
    def relay_down(**kwargs):
        raise RuntimeError("route_unavailable [destination]")

    def explained_relay_down(**kwargs):  # as rpc_client raises it
        with grpc_client._explained({}):
            relay_down()

    for fetch in (relay_down, explained_relay_down):
        monkeypatch.setattr("remoterf.drivers.dynamic_device.fetch_idl", fetch)
        err = _code(ops.device_api, lab, "Pluto_A@ucla")
        assert (err.code, err.exit_code, err.retryable) == ("route_unavailable", 7, True)
        assert err.message == "That deployment isn't responding right now; try again in a minute."

    assert _code(ops.find_slots, home, "Pluto_01@nope").code == "unknown_handle"

    def discovery_down(name):
        raise RuntimeError(f"can't reach {name}")

    monkeypatch.setattr(grpc_client, "_deployment_id", discovery_down)
    err = _code(ops.find_slots, home, "Pluto_01@ucla")
    assert (err.code, err.message) == ("deployment_unreachable", "ucla isn't responding right now; try again in a minute.")


def test_a_duplicate_name_resolves_to_the_labs_owner(lab, monkeypatch):
    monkeypatch.setattr(lab.account, "get_devices", lambda: _r({"12": "Pluto [Pluto_A] (online)", "3": "Pluto [Pluto_A] (online)"}))
    assert ops._resolve(lab, "Pluto_A@ucla")[1] == 3


def test_reply_text_is_printed_or_logged_never_both(caplog, capsys):
    reply = grpc_pb2.GenericRPCResponse(results={"Message": map_arg("Lab maintenance at 5 PM")})
    caplog.set_level("INFO", logger="remoterf")
    grpc_client._finish(reply)
    assert capsys.readouterr().out == "Lab maintenance at 5 PM\n" and not caplog.records
    with grpc_client.quiet():
        grpc_client._finish(reply)
    assert capsys.readouterr().out == "" and [r.getMessage() for r in caplog.records] == ["Lab maintenance at 5 PM"]


def test_ops_lab_calls_have_a_deadline():
    seen = []
    connection = SimpleNamespace(stub=SimpleNamespace(Call=lambda request, **kwargs: seen.append(kwargs)))
    with grpc_client.quiet():
        grpc_client._send("ACC:get_dev", {}, connection)
    grpc_client._send("ACC:get_dev", {}, connection)
    assert seen == [{"timeout": grpc_client.OPS_LAB_TIMEOUT}, {}]


def test_device_api_reads_the_idl_without_a_reservation(lab, monkeypatch):
    seen = []

    def fetch_idl(**kwargs):
        seen.append(kwargs)
        return {"schema_version": "1.0", "device_type": "pluto", "client_class": "Pluto",
                "getters": {"get_rx_lo": {"doc": "RX LO in Hz"}}, "setters": {"set_rx_lo": {}}, "calls": {"call_rx": {}}}

    monkeypatch.setattr("remoterf.drivers.dynamic_device.fetch_idl", fetch_idl)
    out = ops.device_api(lab, "Pluto_A@ucla")
    assert seen == [{"device_id": 3}]
    assert (out["class_name"], out["import_line"], out["constructor"]) == (
        "adi.Pluto", "from remoterf.drivers.adalm_pluto import adi", "sdr = adi.Pluto(token='Pluto_A@ucla')")
    assert [(m["name"], m["kind"]) for m in out["methods"]] == [("rx_lo", "getter"), ("rx_lo", "setter"), ("rx", "call")]
    assert ops.driver_usage("gps")["import_line"] == "from remoterf.drivers.gps import Gps"
    assert ops.driver_usage("GPS")["constructor_template"] == "dev = GPS(token={token!r})"  # any name the installer takes
    assert ops.driver_usage("class")["import_line"] is None


def test_device_api_keeps_lab_text_out_of_code(lab, monkeypatch):
    evil = 'x");open("/tmp/p","w");("'
    monkeypatch.setattr(lab.account, "get_devices", lambda: _r({"3": f"{evil} (online)"}))
    monkeypatch.setattr("remoterf.drivers.dynamic_device.fetch_idl",
                        lambda **kw: {"schema_version": "1.0", "device_type": "gps", "client_class": "Gps"})
    out = ops.device_api(lab, f"{evil}@ucla")
    assert out["constructor"] == f"dev = Gps(token={out['handle']!r})" and f"ensure_driver(token={out['handle']!r})" in out["notes"][1]
    compile(out["constructor"], "c", "exec")
    monkeypatch.setattr("remoterf.drivers.dynamic_device.fetch_idl",
                        lambda **kw: {"schema_version": "1.0", "device_type": 'evil;import os;os.system("id")#'})
    out = ops.device_api(lab, f"{evil}@ucla")
    assert (out["import_line"], out["constructor"]) == (None, None) and "not Python names" in out["notes"][1]


def test_nothing_reaches_stdout(lab, home, monkeypatch, capsys):
    ops.whoami(lab), ops.list_devices(lab), ops.find_slots(lab, "Pluto_A@ucla", now=DAY)
    ops.reserve(lab, "Pluto_A@ucla", DAY.replace(hour=20), DAY.replace(hour=21), reveal_token=True)
    ops.list_reservations(home), ops.reserve(home, "Pluto_01@ucla", DAY, DAY.replace(hour=1)), ops.cancel(home, f"{DEP}:10")
    with pytest.raises(ops.RemoteRFOpsError):
        ops.find_slots(lab, "Nope@ucla")
    reply = grpc_pb2.GenericRPCResponse(results={"UE": map_arg("Invalid login credentials.")})
    with grpc_client.quiet():
        grpc_client._finish(reply)
    # A quiet account: no note, no spinner, no reply text.
    monkeypatch.setattr(grpc_acc, "rpc_client", lambda **kw: grpc_client._finish(reply))
    assert grpc_acc.RemoteRFAccount("jdoe", "pw", quiet=True).login_user() is False
    assert capsys.readouterr().out == ""
    grpc_client._finish(reply)  # the shell still shows it
    assert "Invalid login credentials." in capsys.readouterr().out
