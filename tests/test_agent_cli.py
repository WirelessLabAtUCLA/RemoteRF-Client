"""`remoterf <command> [--json]` over a fake ops/session: JSON shape on
stdout only, exit codes, --confirm refusals, password on stdin, saved state."""

import io
import json
import sys
import types
from types import SimpleNamespace

import pytest

from remoterf import agent_cli, ops, remoterf_cli
from remoterf.deployment import homes, state
from remoterf.ops import RemoteRFOpsError

DEP = "11111111-1111-4111-8111-111111111111"
WHO = {"account": "jdoe@ucla", "home": "ucla", "home_kind": "lab", "labs": ["ucla"],
       "client_version": "2.8.0", "reserve_enabled": True}
SESSION = SimpleNamespace(home="ucla", home_kind="lab", account=None)


def run(capsys, *argv):
    code = agent_cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def session(monkeypatch):
    calls = []
    monkeypatch.setattr(ops, "saved_session", lambda: calls.append("session") or SESSION)
    return calls


def test_devices_json_is_the_only_stdout(capsys, monkeypatch, session):
    seen = {}

    def list_devices(s, **kw):
        print("stray library print")
        seen.update(kw, s=s)
        return {"devices": [{"handle": "Pluto_00@ucla"}], "notes": ["a note"], "incomplete": False}

    monkeypatch.setattr(ops, "list_devices", list_devices)
    code, out, err = run(capsys, "devices", "--all", "--lab", "ucla", "--json")
    assert code == 0
    assert json.loads(out) == {"devices": [{"handle": "Pluto_00@ucla"}], "notes": ["a note"], "incomplete": False}
    assert out.count("\n") == 1 and "stray library print" in err
    assert seen == {"s": SESSION, "lab": "ucla", "online_only": False}


def test_not_logged_in_is_exit_3_with_the_error_object(capsys, monkeypatch):
    def saved_session():
        raise RemoteRFOpsError("not_logged_in", ops.NOT_LOGGED_IN)

    monkeypatch.setattr(ops, "saved_session", saved_session)
    code, out, err = run(capsys, "reservations", "--json")
    assert code == 3
    assert json.loads(out) == {"error": {"code": "not_logged_in", "message": ops.NOT_LOGGED_IN,
                                         "retryable": False, "retry_after": None}}
    assert "Not logged in" in err


@pytest.mark.parametrize("argv", [
    ["reserve", "Pluto_00@ucla", "--start", "2026-10-15T14:00:00Z", "--end", "2026-10-15T15:00:00Z", "--json"],
    ["cancel", "7", "--json"],
])
def test_mutations_need_confirm(capsys, monkeypatch, session, argv):
    monkeypatch.setattr(ops, "reserve", lambda *a, **k: pytest.fail("reserved without --confirm"))
    monkeypatch.setattr(ops, "cancel", lambda *a, **k: pytest.fail("cancelled without --confirm"))
    code, out, _ = run(capsys, *argv)
    assert code == 2 and json.loads(out)["error"]["code"] == "usage"
    assert session == []


def test_reserve_and_cancel_with_confirm(capsys, monkeypatch, session):
    seen = []
    monkeypatch.setattr(ops, "reserve", lambda s, h, start, end, *, reveal_token: seen.append((h, start, end, reveal_token))
                        or {"reservation_id": "7", "handle": h})
    monkeypatch.setattr(ops, "cancel", lambda s, rid: seen.append(rid) or {"cancelled": True, "reservation_id": rid})
    code, out, _ = run(capsys, "reserve", "Pluto_00@ucla", "--start", "2026-10-15T14:00:00Z",
                       "--end", "2026-10-15T15:00:00Z", "--confirm", "--reveal-token", "--json")
    assert code == 0 and json.loads(out) == {"reservation_id": "7", "handle": "Pluto_00@ucla"}
    code, out, _ = run(capsys, "cancel", "7", "--confirm", "--json")
    assert code == 0 and json.loads(out) == {"cancelled": True, "reservation_id": "7"}
    assert seen == [("Pluto_00@ucla", "2026-10-15T14:00:00Z", "2026-10-15T15:00:00Z", True), "7"]


def test_lab_errors_keep_their_exit_codes(capsys, monkeypatch, session):
    def find_slots(s, handle, *, days):
        raise RemoteRFOpsError("rate_limited", "Asked too recently; try again in 9 s.", retry_after=9)

    monkeypatch.setattr(ops, "find_slots", find_slots)
    code, out, _ = run(capsys, "slots", "Pluto_00@ucla", "--days", "2", "--json")
    assert code == 5 and json.loads(out)["error"] == {"code": "rate_limited", "message": "Asked too recently; try again in 9 s.",
                                                      "retryable": True, "retry_after": 9}


def test_usage_errors_are_json_too(capsys):
    code, out, _ = run(capsys, "slots", "--days", "x", "--json")
    assert code == 2 and json.loads(out)["error"]["code"] == "usage"
    code, out, err = run(capsys, "nonsense")
    assert code == 2 and out == "" and "nonsense" in err


def test_interrupt_is_130(capsys, monkeypatch):
    def saved_session():
        raise KeyboardInterrupt

    monkeypatch.setattr(ops, "saved_session", saved_session)
    assert run(capsys, "devices")[0] == 130


class FakeGlobal:
    logins = []

    def __init__(self, origin):
        self.capabilities = {"deployment_id": DEP}

    def login(self, user, password):
        self.logins.append((user, password))

    def close(self):
        pass


def test_login_global_password_on_stdin_leaves_the_saved_state(capsys, monkeypatch):
    monkeypatch.setattr("remoterf.deployment.backend.HttpsJsonAccountBackend", FakeGlobal)
    monkeypatch.setattr(sys, "stdin", io.StringIO("hunter2-secret\n"))
    monkeypatch.setattr(ops, "saved_session", lambda: SESSION)
    monkeypatch.setattr(ops, "whoami", lambda s: {**WHO, "account": "jdoe@global"})
    FakeGlobal.logins = []
    code, out, err = run(capsys, "auth", "login", "--user", "jdoe@global", "--password-stdin", "--json")
    assert code == 0 and json.loads(out)["account"] == "jdoe@global"
    assert FakeGlobal.logins == [("jdoe", "hunter2-secret")]
    assert state.load_target() == {"origin": "https://global.remoterf.net", "transport": "https-json", "deployment_id": DEP}
    assert homes.last_target() == {"kind": "global", "name": None} and homes.has_global_account()
    assert "hunter2" not in out + err


def test_login_lab_stores_the_session_token(capsys, monkeypatch):
    class Account:
        def __init__(self, user, password, *, backend, quiet):
            assert quiet and (user, password) == ("jdoe", "pw")
            self.username, self.session_token = user, None

        def login_user(self, *, remember):
            self.session_token = "tok-123" if remember else None
            return True

    connected = []
    monkeypatch.setattr(homes, "homes", lambda: {"ucla": {}})
    monkeypatch.setattr(homes, "connect", connected.append)
    monkeypatch.setattr("remoterf.deployment.backend.LegacyGrpcAccountBackend", lambda: object())
    monkeypatch.setattr("remoterf.core.grpc_acc.RemoteRFAccount", Account)
    monkeypatch.setattr(sys, "stdin", io.StringIO("pw\n"))
    monkeypatch.setattr(ops, "saved_session", lambda: SESSION)
    monkeypatch.setattr(ops, "whoami", lambda s: WHO)
    code, out, err = run(capsys, "auth", "login", "--user", "jdoe@ucla", "--password-stdin", "--json")
    assert code == 0 and json.loads(out) == WHO and connected == ["ucla"]
    assert homes.recall_login("ucla") == {"username": "jdoe", "secret": "tok-123"}
    assert "tok-123" not in out + err


def test_login_needs_recorded_tos_or_accept_tos(capsys, monkeypatch):
    from remoterf.config.config import forget_tos_agreement, tos_agreed

    forget_tos_agreement()
    monkeypatch.setattr("remoterf.deployment.backend.HttpsJsonAccountBackend", FakeGlobal)
    monkeypatch.setattr(sys, "stdin", io.StringIO("pw\n"))
    FakeGlobal.logins = []
    code, out, _ = run(capsys, "auth", "login", "--home", "global", "--user", "jdoe", "--password-stdin", "--json")
    assert code == 2 and json.loads(out)["error"]["code"] == "usage"
    assert FakeGlobal.logins == [] and not tos_agreed()

    monkeypatch.setattr(ops, "saved_session", lambda: SESSION)
    monkeypatch.setattr(ops, "whoami", lambda s: WHO)
    code, out, err = run(capsys, "auth", "login", "--home", "global", "--user", "jdoe",
                         "--password-stdin", "--accept-tos", "--json")
    assert code == 0 and tos_agreed() and "remoterf.net/tos" in err
    assert FakeGlobal.logins == [("jdoe", "pw")]


def test_login_requires_password_stdin(capsys):
    code, out, _ = run(capsys, "auth", "login", "--user", "jdoe", "--json")
    assert code == 2 and "--password-stdin" in json.loads(out)["error"]["message"]


def test_apps_list_and_revoke_are_global_only(capsys, monkeypatch):
    calls = []
    backend = SimpleNamespace(connected_apps=lambda: {"apps": []},
                              revoke_app=lambda cid: calls.append(cid) or {"revoked": True})
    monkeypatch.setattr(ops, "saved_session", lambda: SimpleNamespace(home="global", home_kind="global",
                                                                       account=SimpleNamespace(backend=backend)))
    assert run(capsys, "auth", "apps", "--json")[1] == '{"apps": []}\n'
    code, out, _ = run(capsys, "auth", "apps", "--revoke", "client-1", "--json")
    assert code == 0 and json.loads(out) == {"revoked": True} and calls == ["client-1"]
    monkeypatch.setattr(ops, "saved_session", lambda: SESSION)
    assert run(capsys, "auth", "apps", "--json")[0] == 2


def test_doctor_reports_without_secrets(capsys, monkeypatch, isolated_home):
    config = isolated_home / ".config" / "remoterf-client"
    config.mkdir(parents=True, exist_ok=True)
    (config / "ca.crt").write_text("x")
    (config / ".env").write_text(f"REMOTERF_ADDR=lab.example.org:12321\nREMOTERF_CA_CERT={config / 'ca.crt'}\nREMOTERF_PROFILE=ucla\n")
    homes.remember_login("ucla", "jdoe", "SECRET-TOKEN")
    monkeypatch.setattr(remoterf_cli, "_connected_server", lambda timeout: "lab.example.org:12321")
    code, out, err = run(capsys, "doctor", "--json")
    report = json.loads(out)
    assert code == 0
    assert report["home"] == "ucla" and report["home_kind"] == "lab" and report["logged_in"] and report["reachable"]
    assert {"version", "python", "mcp_installed", "problems"} <= set(report)
    assert "SECRET-TOKEN" not in out + err


def test_doctor_on_a_fresh_install(capsys):
    code, out, _ = run(capsys, "doctor", "--json")
    report = json.loads(out)
    assert code == 0 and report["home"] is None and not report["logged_in"] and report["problems"]


def test_remoterf_dispatches_agent_commands(monkeypatch):
    seen = []
    monkeypatch.setattr(agent_cli, "main", lambda argv: seen.append(argv) or 0)
    for name in ("agent_docs", "mcp_server"):
        monkeypatch.setitem(sys.modules, f"remoterf.{name}",
                            types.SimpleNamespace(main=lambda argv, n=name: seen.append((n, argv)) or 0))
    for argv in (["devices", "--json"], ["guide", "tokens"], ["mcp", "--allow-reserve"]):
        monkeypatch.setattr(sys, "argv", ["remoterf", *argv])
        assert remoterf_cli.main() == 0
    assert seen == [["devices", "--json"], ("agent_docs", ["guide", "tokens"]), ("mcp_server", ["mcp", "--allow-reserve"])]


def test_backend_connected_apps_routes():
    from remoterf.deployment.backend import HttpsJsonAccountBackend

    sent = []
    backend = object.__new__(HttpsJsonAccountBackend)
    backend.base, backend._access = "/v1/account", lambda: "access"
    backend.transport = SimpleNamespace(request=lambda method, path, **kw: sent.append((method, path, kw))
                                        or ({"apps": []} if method == "GET" else {"revoked": True}))
    assert backend.connected_apps() == {"apps": []}
    assert backend.revoke_app("client-1") == {"revoked": True}
    assert sent == [("GET", "/v1/account/connected-apps", {"access": "access"}),
                    ("POST", "/v1/account/connected-apps/revoke", {"data": {"client_id": "client-1"}, "access": "access"})]
