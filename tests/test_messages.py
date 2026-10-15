"""Plain-English failures (core/messages.py) and the shell's labels: offline
deployments as rows, handle-form labels, reservation times, refusal reasons."""

import ast
import datetime
import logging
import re
import string
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import grpc
import pytest

from remoterf.common.grpc import grpc_pb2
from remoterf.common.utils import map_arg
from remoterf.core import grpc_client, messages
from remoterf.core.messages import explain
from remoterf.deployment.http import AccountBackendError

DEST = "11111111-1111-4111-8111-111111111111"
UID = "0b5c1f7e-1111-4222-8333-444455556666"
LIMIT = ("ucla turned the reservation down: you already have the maximum of 3 reservations there. "
         "Cancel one (cancelres) or wait for one to end.")


@pytest.fixture(autouse=True)
def no_debug(monkeypatch):
    monkeypatch.delenv("REMOTERF_DEBUG", raising=False)


class Unavailable(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.UNAVAILABLE

    def details(self):
        return "failed to connect to all addresses; last error: UNKNOWN: ipv4:192.0.2.1:61005"


# ── the code table ───────────────────────────────────────────────────

@pytest.mark.parametrize("facts", [{}, dict(where="ucla", device="PlutoA@ucla", held=3, minutes=30, days=7)])
def test_every_code_reads_as_a_sentence(facts):
    for code in messages._TEXT:
        for provenance in (None, "destination"):
            sentence = explain(AccountBackendError(code, provenance=provenance), **facts)
            assert not set("_[{") & set(sentence), (code, sentence)
            assert sentence[0].isupper() or sentence.startswith("ucla"), sentence


def test_templates_name_no_ids():
    fields = {f for t in messages._TEXT.values() for t in (t if isinstance(t, tuple) else (t,))
              for _, f, _, _ in string.Formatter().parse(t) if f}
    assert fields <= {"Where", "where", "Device", "device", "held", "minutes", "days", "retry_after"}


def test_the_owner_sentence_and_its_fallbacks():
    assert explain(AccountBackendError("reservation_limit", provenance="destination"), where="ucla", held=3) == LIMIT
    # No count known: the sentence without the number.
    assert explain("reservation_limit", "destination", where="ucla").startswith(
        "ucla turned the reservation down: you already have as many reservations there as you're allowed.")
    assert explain("Partner refused: reservation_limit", where="mit").startswith("mit turned the reservation down:")
    # An older lab, or a reason this client doesn't know yet.
    assert explain("reservation_refused", "destination", where="ucla") == "ucla turned the reservation down."
    assert explain(AccountBackendError("brand_new_reason", provenance="destination"), where="ucla",
                   request="reservation") == "ucla turned the reservation down."
    assert explain("route_unavailable [transport]") == "That deployment isn't responding right now; try again in a minute."
    assert explain(AccountBackendError("rate_limited", retry_after=12)) == "Too many requests; try again in 12 s."


def test_text_a_lab_wrote_passes_through():
    for text in ("End time must not be in the past", "You already have the maximum of 3 reservations.",
                 "Registration failed", "no device available / invalid token", ""):
        assert explain(text) == text
        assert explain(RuntimeError(text), "destination") == text


def test_a_grpc_status_is_a_sentence_not_a_dump():
    assert explain(Unavailable(), where="ucla") == "Can't reach ucla right now; check your connection and try again."


def test_the_raw_code_is_logged_and_shown_only_with_remoterf_debug(monkeypatch, caplog):
    exc = AccountBackendError("reservation_limit", provenance="destination")
    with caplog.at_level(logging.DEBUG, logger="remoterf"):
        assert explain(exc, where="ucla", held=3) == LIMIT
    assert "reservation_limit [destination]" in caplog.text
    monkeypatch.setenv("REMOTERF_DEBUG", "1")
    assert explain(exc, where="ucla", held=3) == LIMIT + " (reservation_limit [destination])"
    assert explain("not_found").endswith(" (not_found)")


# ── what the shell shows ─────────────────────────────────────────────

def _app(monkeypatch, account=None):
    import remoterf.core.app as client_app

    lines = []
    monkeypatch.setattr(client_app, "printf", lambda *parts: lines.append("".join(parts[::2])))
    monkeypatch.setattr(client_app, "home_name", "ucla")
    monkeypatch.setattr(client_app, "current_route", None)
    if account is not None:
        monkeypatch.setattr(client_app, "account", account)
    return client_app, lines


def _row(lines, *words):
    return any(line.replace("│", " ").split() == list(words) for line in lines)


def test_reservation_times_read_from_to():
    from remoterf.core.app import _format_reservation_range

    day = datetime.datetime(2026, 10, 14, 9)
    assert _format_reservation_range(day, day.replace(minute=30)) == "2026-10-14 from 09:00 AM to 09:30 AM"
    assert (_format_reservation_range(day.replace(hour=23, minute=30), day + datetime.timedelta(hours=15))
            == "2026-10-14 from 11:30 PM to 2026-10-15 12:00 AM")


def test_device_labels_are_in_handle_form(monkeypatch):
    client_app, _ = _app(monkeypatch)
    assert client_app._device_label("Bluetooth Dongle", None, "ucla") == "Bluetooth_Dongle@ucla"
    assert client_app._device_label("Pluto SDR", "PlutoA", "ucla") == "PlutoA@ucla"
    assert client_app._device_label(None, None, None) == "unnamed device"
    assert client_app._native_label("Pluto SDR [PlutoA] (online)") == "PlutoA@ucla"
    assert client_app._native_label("Pluto SDR @mit (online)") == "Pluto_SDR@mit"


def _dest(name, status="ok", dep=None, code=None, provenance="destination_signed"):
    return {"deployment_id": dep or f"{name}-id", "display_name": name, "status": status, "provenance": provenance,
            "error_code": code, "contract_id": "c-1", "contract_version": 2, "state": "active"}


DESTS = [_dest("ucla", dep=DEST), _dest("mit", "unavailable", provenance="transport"),
         _dest(UID, "unavailable", dep=UID, provenance="transport"), _dest("stanford", "blocked", code="contract_not_active")]


def _dev(name, online=True, handle=None):
    return {"deployment_name": "ucla", "deployment_id": DEST, "display_name": name, "handle": handle,
            "local_device_id": 1, "online": online, "max_reservation_time_sec": 1800, "max_horizon_sec": 0,
            "device_id": f"{DEST}:{name}"}


def test_getdev_shows_deployments_that_didnt_answer_as_rows(monkeypatch):
    account = Mock(is_https_home=True)
    account.get_devices.return_value = {"destinations": DESTS, "devices": [_dev("Bluetooth Dongle")]}
    client_app, lines = _app(monkeypatch, account)
    client_app._federated_devices()
    assert lines[0] == "◆ stanford isn't sharing with Global right now."
    box = lines[1:]
    assert [line.strip("│ ") for line in box[1:-1]] == [
        "@ucla", "Bluetooth Dongle  online", "@mit (offline)", "unnamed deployment (offline)"]
    assert not any(UID[:8] in line or "isn't responding" in line for line in lines)
    lines.clear()
    account.get_devices.return_value["devices"] = []
    client_app._federated_devices()
    assert lines[-1] == "No devices visible through this home; @mit, an unnamed deployment are offline."


def test_the_resdev_picker_lists_offline_deployments_before_the_footer(monkeypatch):
    account = Mock(is_https_home=True)
    account.get_devices.return_value = {"destinations": DESTS[:2], "devices": [_dev("Bluetooth Dongle"), _dev("Pluto", False)]}
    client_app, lines = _app(monkeypatch, account)
    monkeypatch.setattr(client_app, "session", SimpleNamespace(prompt=lambda text: "x"))
    client_app._federated_reserve()
    body = [line.strip("│ ") for line in lines[1:-1]]
    assert body == ["@ucla", "0. Bluetooth Dongle  30 min slots", "@mit (offline)", "", "1 offline device not shown"]
    lines.clear()
    account.get_devices.return_value["devices"] = [_dev("Pluto", False)]
    client_app._federated_reserve()
    assert lines == ["No devices are online right now; @mit is offline."]


def test_reservation_rows_name_the_device_and_offline_deployments(monkeypatch):
    start = datetime.datetime(2026, 10, 14, 9).timestamp()
    res = lambda device: {"device_id": f"{DEST}:{device}", "deployment_id": DEST, "mine": True,
                          "start_time": start, "end_time": start + 1800}
    account = Mock(is_https_home=True)
    account.get_reservations.return_value = {"destinations": DESTS[:2], "reservations": [res("Bluetooth Dongle"), res("gone")]}
    account.get_devices.return_value = {"destinations": [], "devices": [_dev("Bluetooth Dongle")]}
    client_app, lines = _app(monkeypatch, account)
    client_app._federated_reservations(mine_only=True)
    assert _row(lines, "Bluetooth_Dongle@ucla", "2026-10-14", "from", "09:00", "AM", "to", "09:30", "AM")
    assert _row(lines, "unlisted", "device@ucla", "2026-10-14", "from", "09:00", "AM", "to", "09:30", "AM")
    assert lines[-3].strip("│ ") == "" and lines[-2].strip("│ ") == "@mit (offline)"
    lines.clear()
    account.get_reservations.return_value["reservations"] = []
    client_app._federated_reservations(mine_only=True)
    assert lines == ["No reservations found; @mit is offline."]


def test_native_reservation_rows_name_the_device(monkeypatch):
    rows = {"r1": "jdoe,3,2026-10-14 09:00:00,2026-10-14 09:30:00", "r2": "jdoe,1001,2026-10-15 13:00:00,2026-10-15 14:00:00",
            "r3": "jdoe,77,2026-10-15 13:00:00,2026-10-15 14:00:00", "r4": "admin,3,2026-10-16 13:00:00,2026-10-16 14:00:00"}
    account = Mock(is_https_home=False, username="jdoe")
    account.get_reservations.return_value = SimpleNamespace(results={k: map_arg(v) for k, v in rows.items()})
    account.get_devices.return_value = SimpleNamespace(results={k: map_arg(v) for k, v in {
        "3": "Pluto SDR [PlutoA] (online)", "1001": "Pluto SDR @mit (online)"}.items()})
    client_app, lines = _app(monkeypatch, account)
    client_app.my_reservations()
    body = [" ".join(line.strip("│ ").split()) for line in lines[1:-1]]
    assert body == ["PlutoA@ucla 2026-10-14 from 09:00 AM to 09:30 AM",
                    "Pluto_SDR@mit 2026-10-15 from 01:00 PM to 02:00 PM",
                    "unlisted device 2026-10-15 from 01:00 PM to 02:00 PM"]


def test_a_refused_reservation_says_why(monkeypatch):
    now = datetime.datetime.now().timestamp()
    held = {"mine": True, "deployment_id": DEST, "end_time": now + 3600}
    account = Mock(is_https_home=True)
    account.get_reservations.return_value = {"reservations": [held] * 3 + [{**held, "mine": False}]}
    client_app, lines = _app(monkeypatch, account)
    slot = (datetime.datetime(2026, 10, 14, 9), datetime.datetime(2026, 10, 14, 9, 30))
    reserve = lambda: client_app._federated_reserve_slot(f"{DEST}:1", slot[0].date(), slot, "", "", deployment_name="ucla",
                                                         device_name="Bluetooth Dongle", max_sec=1800)
    for code, shown in [
        ("reservation_limit", "◆ " + LIMIT),
        ("reservation_refused", "◆ ucla turned the reservation down."),  # an older lab
        ("reservation_too_long", "◆ ucla turned the reservation down: Bluetooth_Dongle@ucla can be reserved for at most 30 minutes at a time."),
        ("reservation_conflict", "◆ ucla turned the reservation down: someone else has Bluetooth_Dongle@ucla at that time. Pick another slot."),
    ]:
        lines.clear()
        account.reserve_device.side_effect = AccountBackendError(code, provenance="destination")
        reserve()
        assert lines == [shown]
    # At a lab HOME: the partner's code, or the lab's own sentence.
    native = Mock(is_https_home=False)
    client_app, lines = _app(monkeypatch, native)
    for refusal, shown in [
        ("Partner refused: reservation_limit", "◆ mit turned the reservation down: you already have as many reservations "
                                                "there as you're allowed. Cancel one (cancelres) or wait for one to end."),
        ("You already have the maximum of 3 reservations.", "◆ You already have the maximum of 3 reservations."),
    ]:
        lines.clear()
        native.reserve_device.side_effect = RuntimeError(refusal)
        client_app._native_reserve_slot(1001, slot[0].date(), slot, "", "", device_label="Pluto SDR @mit (online)")
        assert lines == [shown]


def test_federated_perms_give_a_reason_not_a_code(monkeypatch):
    client_app, lines = _app(monkeypatch)
    client_app._render_federation_permissions([_dest("stanford", "blocked", code="principal_disabled"),
                                               _dest(UID, "unavailable", dep=UID, provenance="transport")])
    text = " ".join(lines)
    assert "Reason: Your account at stanford is disabled; ask its administrator." in text
    assert "Status: refused" in text and "Status: offline" in text
    assert "Source:" not in text and "principal" not in text and UID[:8] not in text
    lines.clear()
    # Global's own rejection of a lab's answer is not the lab turning the request down.
    client_app._render_federation_permissions([_dest("stanford", "blocked", code="invalid_result_fields", provenance="local")])
    assert "Reason: RemoteRF Global couldn't read that request" in " ".join(lines)


def test_python_errors_in_resdev_are_not_shown_raw(monkeypatch):
    client_app, lines = _app(monkeypatch, Mock(is_https_home=False))
    monkeypatch.setattr(client_app, "session", SimpleNamespace(prompt=lambda *_a, **_k: "2026-10-14 9am"))
    with pytest.raises(RuntimeError, match=r"^Enter the time as YYYY-MM-DD HH:MM\.$"):
        client_app.get_datetime("Reserve Start Time")
    client_app.account.reserve_device.side_effect = KeyError("max_horizon_sec")
    slot = (datetime.datetime(2026, 10, 14, 9), datetime.datetime(2026, 10, 14, 9, 30))
    client_app._native_reserve_slot(1001, slot[0].date(), slot, "", "", device_label="Pluto SDR @mit (online)")
    assert lines == ["◆ " + client_app._UNEXPECTED]


def test_a_failed_command_never_ends_the_shell_with_a_traceback(monkeypatch, capsys):
    client_app, lines = _app(monkeypatch, Mock(is_https_home=False))
    monkeypatch.setattr(client_app, "home_name", None)
    for debug in ("", "1"):
        monkeypatch.setenv("REMOTERF_DEBUG", debug)
        lines.clear()
        monkeypatch.setattr(client_app, "_account_command", Mock(side_effect=[ValueError("boom"), KeyboardInterrupt]))
        assert client_app.welcome(show_banner=False, initial=["login"]) is False
        assert lines[0].startswith("◆ That command failed unexpectedly; run with REMOTERF_DEBUG=1 for details.")
        assert ("Traceback" in capsys.readouterr().err) == bool(debug)


def test_a_grpc_failure_keeps_its_type_with_a_readable_message(monkeypatch):
    raw = Unavailable()

    def down(*_a, **_k):
        raise raw

    monkeypatch.setattr(grpc_client, "_dispatch", down)
    with pytest.raises(grpc.RpcError) as raised:
        grpc_client.rpc_client(function_name="pluto:tx_lo:GET", args={"a": map_arg("PlutoA@ucla")})
    assert isinstance(raised.value, RuntimeError) and raised.value.__cause__ is raw
    assert raised.value.code() == grpc.StatusCode.UNAVAILABLE
    assert str(raised.value) == "Can't reach ucla right now; check your connection and try again."


def test_an_account_error_keeps_its_type_and_code(monkeypatch):
    def expired(*_a, **_k):
        raise AccountBackendError("invalid_access_token", provenance="home")

    monkeypatch.setattr(grpc_client, "_dispatch", expired)
    with pytest.raises(AccountBackendError) as raised:
        grpc_client.rpc_client(function_name="ACC:get_dev", args={})
    assert (str(raised.value), raised.value.code, raised.value.provenance) == (
        "Your login has expired; log in again.", "invalid_access_token", "home")


def test_lab_texts_that_name_an_id_or_a_python_label_read_as_sentences(capsys):
    assert explain("No schema available for device 5 (offline)") == "That device isn't connected right now; try again in a minute."
    assert explain("No schema available for this token (invalid token or device offline)") == messages._TEXT["invalid_device_token"]
    grpc_client._finish(grpc_pb2.GenericRPCResponse(results={"UE": map_arg("API Token Invalid")}))
    assert capsys.readouterr().out == messages._TEXT["invalid_device_token"] + "\n"


def test_no_user_facing_template_interpolates_an_id():
    """An f-string never shows a device, deployment or reservation id."""
    src = Path(messages.__file__).parent
    is_id = re.compile(r"^(?:str\()?(?:\w+_id\b|\w+\[['\"]\w+_id['\"]\])")
    found = []
    for path in [*(src / n for n in ("app.py", "grpc_acc.py", "availability.py", "grpc_client.py", "direct_path.py",
                                     "dynamic_v2_transport.py", "messages.py")), src.parent / "remoterf_cli.py"]:
        tree = ast.parse(path.read_text())
        legacy = {n for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                  and f.name == "interactive_reserve_next_days" for n in ast.walk(f)}  # unreachable picker
        found += [f"{path.name}:{n.lineno} {ast.unparse(n.value)}" for n in ast.walk(tree)
                  if isinstance(n, ast.FormattedValue) and n not in legacy and is_id.match(ast.unparse(n.value))]
    assert found == []
