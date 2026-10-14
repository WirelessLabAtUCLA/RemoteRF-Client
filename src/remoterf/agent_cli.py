# Copyright (C) 2026 RemoteRF
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""The scriptable CLI over remoterf.ops: no prompts, no colour, no spinner.
With --json, stdout carries exactly one JSON object; messages go to stderr."""

import argparse
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime

from . import ops
from .ops import RemoteRFOpsError

COMMANDS = ("auth", "devices", "slots", "reservations", "reserve", "cancel", "doctor")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise RemoteRFOpsError("usage", f"{self.prog}: {message}")


def _clean(value):
    """Server and lab text, safe for a terminal."""
    return "".join(c for c in str(value) if c.isprintable())


# ── auth ─────────────────────────────────────────────────────────────

def _default_home():
    """The home `remoterf -l` would pick on its own."""
    from .deployment import homes

    last = homes.last_target()
    if last:
        return homes.GLOBAL_ACCOUNTS if last["kind"] == "global" else last["name"]
    saved = sorted(homes.homes())
    if len(saved) > 1 or saved and homes.has_global_account():
        raise RemoteRFOpsError("usage", "Several logins are saved here; choose one with --home.")
    return saved[0] if saved else homes.GLOBAL_ACCOUNTS


def _login_global(user, password):
    """`remoterf -l global` then login: Global selected, its session stored."""
    from .deployment import homes
    from .deployment.backend import HttpsJsonAccountBackend
    from .deployment.state import origin, select_target
    from .remoterf_cli import _global_origin

    selected = origin(_global_origin())
    backend = HttpsJsonAccountBackend(selected)
    try:
        deployment_id = backend.capabilities["deployment_id"]
        select_target({"origin": selected, "transport": "https-json", "deployment_id": deployment_id})
        homes.remember_global_account(selected, deployment_id)
        homes.set_last_target("global")
        backend.login(user, password)
    finally:
        backend.close()


def _login_lab(home, user, password):
    """`remoterf -l LAB` then login: the lab selected, its session token stored."""
    from .core.grpc_acc import RemoteRFAccount
    from .deployment import homes
    from .deployment.backend import LegacyGrpcAccountBackend

    try:
        if home in homes.homes():
            homes.connect(home)
        else:
            home = homes.register_home(f"{home}.{homes.GLOBAL_DOMAIN}")[0]
        backend = LegacyGrpcAccountBackend()
    except (RuntimeError, ValueError) as exc:
        raise RemoteRFOpsError("lab_unreachable", f"Could not connect to {home}: {exc}") from exc
    account = RemoteRFAccount(user, password, backend=backend, quiet=True)
    if not account.login_user(remember=True):
        raise RemoteRFOpsError("invalid_credentials", "Login failed: check the username and password.")
    if not account.session_token:
        raise RemoteRFOpsError("lab_error", f"{home} issues no session token, so this login can't be saved; use `remoterf -l {home}`.")
    homes.remember_login(home, account.username, account.session_token)


@ops._op
def _login(a):
    from .config.config import _read_tos_notice, remember_tos_agreement, tos_agreed
    from .deployment import homes

    if a.accept_tos:
        print(_read_tos_notice(), file=sys.stderr)
        remember_tos_agreement()
    elif not tos_agreed():
        raise RemoteRFOpsError("usage", "Logging in needs your agreement to the RemoteRF Terms of Service: add --accept-tos.")
    password = sys.stdin.readline().rstrip("\r\n")
    if not password:
        raise RemoteRFOpsError("usage", "No password on stdin.")
    homes.migrate_legacy_target()
    name, lab = homes.split_account(a.user)
    home = (a.home or lab or _default_home()).lower()
    user = name if lab == home else a.user  # `jdoe@ucla` at ucla is `jdoe` there
    if home == homes.GLOBAL_ACCOUNTS:
        _login_global(user, password)
    else:
        _login_lab(home, user, password)
    return ops.whoami(ops.saved_session())


@ops._op
def _logout(a):
    """Revoke the saved session (best effort), then forget it locally."""
    from .deployment.state import CredentialStore, load_target

    target = load_target()
    if target and target["transport"] == "https-json":
        from .deployment.backend import selected_backend

        try:
            backend = selected_backend()
            try:
                if backend.resume():
                    backend.logout()
            finally:
                backend.close()
        except Exception:  # noqa: BLE001 - revocation is best effort
            pass
        CredentialStore(target["origin"], target["deployment_id"]).clear()
        return {"logged_out": True}
    from .core.grpc_acc import RemoteRFAccount
    from .core.grpc_client import _active_home_name
    from .deployment import homes

    home = _active_home_name()
    saved = homes.recall_login(home) if home else None
    if saved:
        account = RemoteRFAccount(saved["username"], saved["secret"], quiet=True)
        account.session_token = saved["secret"]
        account.logout()
        homes.forget_login(home)
    return {"logged_out": True}


@ops._op
def _apps(a):
    s = ops.saved_session()
    if s.home_kind != "global":
        raise RemoteRFOpsError("invalid_argument", "Connected apps belong to a Global account (remoterf auth login --home global).")
    backend = s.account.backend
    return backend.revoke_app(a.revoke) if a.revoke else backend.connected_apps()


# ── devices and reservations ─────────────────────────────────────────

def _reserve(a):
    if not a.confirm:
        raise RemoteRFOpsError("usage", "Reserving changes the lab's schedule: re-run with --confirm.")
    return ops.reserve(ops.saved_session(), a.handle, a.start, a.end, reveal_token=a.reveal_token)


def _cancel(a):
    if not a.confirm:
        raise RemoteRFOpsError("usage", "Cancelling gives the slot up: re-run with --confirm.")
    return ops.cancel(ops.saved_session(), a.reservation_id)


# ── doctor ───────────────────────────────────────────────────────────

def _doctor(a):
    """What this install sees; never a secret, never a failure."""
    import importlib.util
    import platform

    from .deployment import homes
    from .deployment.state import CredentialStore, load_target, root

    out = {"version": ops._version(), "python": platform.python_version(), "executable": sys.executable,
           "config_dir": str(root().parent), "home": None, "home_kind": None, "endpoint": None,
           "logged_in": False, "reachable": False, "mcp_installed": importlib.util.find_spec("mcp") is not None,
           "problems": []}
    try:
        target = load_target()
        if target and target["transport"] == "https-json":
            store = CredentialStore(target["origin"], target["deployment_id"])
            out.update(home=homes.GLOBAL_ACCOUNTS, home_kind="global", endpoint=target["origin"],
                       logged_in=(store.directory / "selected.json").exists())
        else:
            from .deployment.direct import resolve_active_profile

            profile = resolve_active_profile()
            if profile:
                out.update(home=profile.home, home_kind="lab", endpoint=profile.grpc_endpoint,
                           logged_in=bool(profile.home and homes.recall_login(profile.home)))
        if out["endpoint"]:
            from .remoterf_cli import _connected_server

            out["reachable"] = _connected_server(3.0) is not None
    except Exception as exc:  # noqa: BLE001 - doctor reports, it never fails
        out["problems"].append(str(exc))
    if not out["endpoint"]:
        out["problems"].append("No home selected: run `remoterf auth login`.")
    else:
        if not out["logged_in"]:
            out["problems"].append("No saved login: run `remoterf auth login`.")
        if not out["reachable"]:
            out["problems"].append(f"{out['endpoint']} did not answer.")
    if not out["mcp_installed"]:
        out["problems"].append('For `remoterf mcp`: pip install "remoterf[mcp]"')
    return out


# ── human output ─────────────────────────────────────────────────────

def _local(ts):
    return datetime.fromtimestamp(ts).strftime("%a %Y-%m-%d %H:%M") if ts else "-"


def _table(out, rows):
    rows = [[_clean(c) for c in row] for row in rows]
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))] if rows else []
    for row in rows:
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip(), file=out)


def _show_whoami(r, out):
    print(_clean(f"{r['account']} ({r['home_kind']}); labs: {', '.join(r['labs']) or '-'}"), file=out)


def _show_devices(r, out):
    _table(out, [[d["handle"] or d["name"], d["state"] if d["online"] else "offline", d["type"] or "-", d["lab"] or "-",
                  f"until {_local(d['until_ts'])}" if d["until_ts"] else ""] for d in r["devices"]])


def _show_slots(r, out):
    print(_clean(f"{r['handle']}: {r['slot_minutes']}-minute slots ({r['timezone']})"), file=sys.stderr)
    _table(out, [[_local(s["start_ts"]), "-", _local(s["end_ts"])] for s in r["slots"]])


def _show_reservations(r, out):
    _table(out, [[x["reservation_id"], x["handle"] or x["device"], _local(x["start_ts"]), "-", _local(x["end_ts"])]
                 for x in r["reservations"]])


def _show_reserved(r, out):
    print(_clean(f"Reserved {r['handle']} {_local(r['start_ts'])} - {_local(r['end_ts'])} "
                 f"(reservation {r['reservation_id'] or 'unknown'})."), file=out)
    print(_clean(f'Use token="{r["use_as_token"]}"' + (f', or the raw token {r["token"]}' if r.get("token") else "")), file=out)


def _show_apps(r, out):
    if "apps" not in r:
        print("Revoked.", file=out)
        return
    _table(out, [[x.get("client_id"), x.get("client_name"), " ".join(x.get("scopes") or ()), f"last used {x.get('last_used_at') or 'never'}"]
                 for x in r["apps"]] or [["No connected apps."]])


def _show_doctor(r, out):
    for key, value in r.items():
        if key != "problems":
            print(_clean(f"{key}: {value}"), file=out)
    for problem in r["problems"]:
        print(_clean(f"! {problem}"), file=out)


# ── main ─────────────────────────────────────────────────────────────

def _parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print one JSON object on stdout")
    parser = _Parser(prog="remoterf", description="Scriptable RemoteRF commands (no prompts).")
    sub = parser.add_subparsers(dest="command", required=True)

    def command(where, name, run, show, help):
        p = where.add_parser(name, parents=[common], help=help, description=help)
        p.set_defaults(run=run, show=show)
        return p

    auth = sub.add_parser("auth", help="the saved login").add_subparsers(dest="action", required=True)
    p = command(auth, "login", _login, _show_whoami, "log in without prompts and keep the login saved")
    p.add_argument("--home", help="global, or a lab name (default: the account's lab, else the last home used)")
    p.add_argument("--user", required=True, help="NAME, or NAME@lab")
    p.add_argument("--password-stdin", action="store_true", required=True, help="read the password from stdin")
    p.add_argument("--accept-tos", action="store_true", help="agree to the RemoteRF Terms of Service")
    command(auth, "status", lambda a: ops.whoami(ops.saved_session()), _show_whoami, "who the saved login is")
    command(auth, "logout", _logout, lambda r, out: print("Logged out.", file=out), "end and forget the saved login")
    p = command(auth, "apps", _apps, _show_apps, "AI apps connected to this Global account")
    p.add_argument("--revoke", metavar="CLIENT_ID", help="disconnect one app")
    p = command(sub, "devices", lambda a: ops.list_devices(ops.saved_session(), lab=a.lab, online_only=not a.all),
                _show_devices, "devices and their state now")
    p.add_argument("--lab", help="only this lab's devices")
    p.add_argument("--all", action="store_true", help="include offline devices")
    p = command(sub, "slots", lambda a: ops.find_slots(ops.saved_session(), a.handle, days=a.days),
                _show_slots, "free slots on a device, aligned like resdev")
    p.add_argument("handle", help="Name@lab, for example Pluto_00@ucla")
    p.add_argument("--days", type=int, default=1)
    command(sub, "reservations", lambda a: ops.list_reservations(ops.saved_session()), _show_reservations, "your reservations")
    p = command(sub, "reserve", _reserve, _show_reserved, "reserve a device (needs --confirm)")
    p.add_argument("handle")
    p.add_argument("--start", required=True, help="RFC 3339, for example 2026-10-15T14:00:00Z")
    p.add_argument("--end", required=True)
    p.add_argument("--confirm", action="store_true", help="really reserve")
    p.add_argument("--reveal-token", action="store_true", help="include the raw reservation token")
    p = command(sub, "cancel", _cancel, lambda r, out: print(_clean(f"Cancelled reservation {r['reservation_id']}."), file=out),
                "cancel a reservation (needs --confirm)")
    p.add_argument("reservation_id")
    p.add_argument("--confirm", action="store_true", help="really cancel")
    command(sub, "doctor", _doctor, _show_doctor, "check this install, its home and its login")
    return parser


def main(argv) -> int:
    out = sys.stdout
    as_json = "--json" in argv
    try:
        args = _parser().parse_args(argv)
        as_json = args.json
        with redirect_stdout(sys.stderr):  # a stray library print must not reach stdout
            result = args.run(args)
    except RemoteRFOpsError as exc:
        if as_json:
            print(json.dumps(exc.to_dict()), file=out)
        print(_clean(f"remoterf: {exc.message}"), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
    except SystemExit as exc:  # --help
        return exc.code if isinstance(exc.code, int) else 0
    if as_json:
        print(json.dumps(result), file=out)
    else:
        for note in result.get("notes", ()):
            print(_clean(note), file=sys.stderr)
        args.show(result, out)
    return 0
