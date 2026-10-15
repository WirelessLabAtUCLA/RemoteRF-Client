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

"""Print-free core for the CLI, the MCP server and the shell: JSON-ready
dicts in, typed errors out. Nothing here prints, prompts or spins."""

import ast
import functools
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import grpc

from .common.utils import map_arg, str_to_list, unmap_arg
from .core.grpc_client import _key
from .core.messages import explain
from .deployment.http import AccountBackendError

EXIT_CODES = {
    **dict.fromkeys(("usage", "invalid_argument", "reservation_too_long", "reservation_too_short",
                     "reservation_too_far_ahead", "reservation_in_past"), 2),
    **dict.fromkeys(("not_logged_in", "invalid_credentials", "session_expired"), 3),
    **dict.fromkeys(("not_found", "unknown_handle"), 4),
    **dict.fromkeys(("conflict", "slot_unavailable", "reservation_conflict", "rate_limited", "offline"), 5),
    **dict.fromkeys(("permission_denied", "principal_unknown", "contract_not_active", "reserve_disabled",
                     "reservation_refused", "reservation_limit", "lab_reservation_limit", "device_not_permitted"), 6),
    **dict.fromkeys(("route_unavailable", "transport", "deployment_unreachable"), 7),
}
_RETRYABLE = {"rate_limited", "route_unavailable", "transport", "deployment_unreachable"}
# A wire code, also as a lab HOME relays it ('Partner refused: <code>') or the Global relay sends it
# ('route_unavailable [destination]: ...').
_WIRE = re.compile(r"(?:(?:Partner refused|Partner unreachable|Federation error): )?([a-z_]{1,64})(?: \[\w+\])?(?::.*)?", re.S)
# Global's answers to a login it no longer honours.
_AUTH_FAILED = {"invalid_access_token", "invalid_refresh_token", "refresh_token_reused", "invalid_token", "unauthorized"}
# explain()'s hints name shell commands; these surfaces use the CLI's (the MCP tools match them).
_SHELL_HINTS = {"(cancelres)": "(remoterf cancel)", "run getdev": "run remoterf devices", "run myres": "run remoterf reservations"}
NOT_LOGGED_IN = "Not logged in: run `remoterf` and log in (keep the login saved)."
STALE_LOGIN = "Saved login is no longer valid: run `remoterf` and log in again."


class RemoteRFOpsError(Exception):
    def __init__(self, code, message=None, *, retryable=None, retry_after=None):
        self.code = code
        message = message or explain(code)
        for shell, cli in _SHELL_HINTS.items():
            message = message.replace(shell, cli)
        self.message = message
        self.retry_after = retry_after
        self.retryable = (code in _RETRYABLE or bool(retry_after)) if retryable is None else retryable
        super().__init__(self.message)

    @property
    def exit_code(self):
        return EXIT_CODES.get(self.code, 1)

    def to_dict(self):
        return {"error": {"code": self.code, "message": self.message,
                          "retryable": self.retryable, "retry_after": self.retry_after}}


def _rate_limited(wait):
    return RemoteRFOpsError("rate_limited", f"Asked too recently; try again in {wait} s.", retry_after=int(wait))


def _wire_code(text):
    """The code in a failure's text when the exit codes know it, else None.
    'Partner unreachable' with any other code, or none, is a deployment that didn't answer."""
    m = _WIRE.fullmatch(str(text))
    if m and m[1] in EXIT_CODES:
        return m[1]
    return "deployment_unreachable" if str(text).startswith("Partner unreachable") else None


def _deployment_error(text, **facts):
    """A deployment's refusal text as a typed error; `message` says it in plain English."""
    if text == "Invalid login credentials.":
        return RemoteRFOpsError("session_expired", STALE_LOGIN)
    if text.startswith("Device already reserved"):
        return RemoteRFOpsError("slot_unavailable", explain(text))
    if text.startswith("Invalid reservation ID"):
        return RemoteRFOpsError("not_found", explain(text))
    return RemoteRFOpsError(_wire_code(text) or "deployment_error", explain(text, **facts))


def _backend_error(exc, **facts):
    code = str(exc.code)
    if exc.retry_after:
        return _rate_limited(exc.retry_after)
    if re.fullmatch("[a-z_]{1,64}", code):
        # The relay's other transport failures (invalid_signed_result, ...): the deployment didn't answer.
        down = exc.provenance == "transport" and code not in EXIT_CODES and code != "response_too_large"
        return RemoteRFOpsError("deployment_unreachable" if down else code, explain(exc, **facts))
    if code == "Log in first":
        return RemoteRFOpsError("not_logged_in", NOT_LOGGED_IN)
    return RemoteRFOpsError("transport" if "connection failed" in code or "deadline exceeded" in code else "error",
                            explain(exc, **facts))


def _op(fn):
    """A public call: nothing reaches stdout, and every failure is a RemoteRFOpsError."""
    @functools.wraps(fn)
    def call(*args, **kwargs):
        from .core.grpc_client import quiet

        with quiet():
            try:
                return fn(*args, **kwargs)
            except RemoteRFOpsError:
                raise
            except AccountBackendError as exc:
                raise _backend_error(exc) from exc
            except grpc.RpcError as exc:
                raise RemoteRFOpsError("deployment_unreachable", explain(exc)) from exc
            except (RuntimeError, ValueError, OSError) as exc:
                # rpc_client explains a failure ('route_unavailable [destination]' from the
                # Global relay) and chains the original: its code picks the exit code.
                raise RemoteRFOpsError(_wire_code(exc.__cause__ or exc) or "error", explain(exc)) from exc
    return call


def _results(response):
    """A native reply's results; its `ace` is the lab's refusal."""
    if "ace" in response.results:
        raise _deployment_error(str(unmap_arg(response.results["ace"])))
    return response.results


# ── times ────────────────────────────────────────────────────────────

SERVER_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _parse_reservation_time(value: str) -> datetime:
    return datetime.strptime(value, SERVER_TIME_FORMAT)


def _instant(name, ts):
    """`name`: RFC 3339 UTC, `name_ts`: epoch seconds; both None when unknown."""
    try:
        text = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None
    except (OverflowError, ValueError, OSError, TypeError):
        text = None
    return {name: text, f"{name}_ts": int(ts) if text else None}


def _epoch(value):
    """An aware datetime or RFC 3339 string -> epoch seconds (naive: local time)."""
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00").replace("z", "+00:00"))
        return int(value.timestamp())
    except (AttributeError, TypeError, ValueError):
        raise RemoteRFOpsError("invalid_argument", f"Not an RFC 3339 time: {value!r}") from None


def _days(horizon_sec):
    return -(-int(horizon_sec or 0) // 86400) or None


# ── native labels ────────────────────────────────────────────────────

_PARTNER_LABEL = re.compile(r"^(?P<name>.*) @(?P<lab>.+?) \((?P<state>[^)]*)\)$")
# The state always starts with online/offline; a PoE fault after it can hold brackets.
_LOCAL_LABEL = re.compile(r"^(?P<name>.*?) \((?P<state>(?:online|offline)\b.*)\)$")
_HANDLE_TAG = re.compile(r"^(?P<name>.*) \[(?P<handle>\S+)\]$")  # handles have no whitespace


def _label_name(label):
    """'Pluto SDR [PlutoA] (online)' -> 'Pluto SDR [PlutoA]'; a partner's
    'Pluto SDR @mit (online)' -> 'Pluto SDR @mit'."""
    match = _LOCAL_LABEL.match(label)
    return match["name"] if match else label


def _label_online(label):
    match = _LOCAL_LABEL.match(label)
    return bool(match) and match["state"].startswith("online")


def _name_and_handle(name):
    """A listed device name -> (name, handle). The handle is the name keyed,
    'Pluto SDR' -> 'Pluto_SDR' (None when the lab can't address it), unless a
    1.2.0 lab tags it: 'Pluto SDR [PlutoA]'."""
    m = _HANDLE_TAG.match(name)
    if m:
        return m["name"], m["handle"]
    h = _key(name)
    # The lab's is_device_handle (core 0.19.0; this client pins 0.16.0).
    ok = h and not h.startswith(("rfg_", "bridge-")) and not any(c in "@:" or c.isspace() or not c.isprintable() for c in h)
    return name, h if ok else None


def native_rows(results, home):
    """ACC:get_dev labels -> {id, name, lab, state, away} rows: the home's
    devices, then any a partner lab shares with us, grouped by lab."""
    rows = []
    for key in sorted(results, key=int):
        label = str(unmap_arg(results[key]))
        partner = _PARTNER_LABEL.match(label)
        local = _LOCAL_LABEL.match(label)
        if partner:
            rows.append({"id": int(key), "name": partner["name"], "lab": partner["lab"], "state": partner["state"], "away": True})
        elif local:
            rows.append({"id": int(key), "name": local["name"], "lab": home, "state": local["state"], "away": False})
        else:
            rows.append({"id": int(key), "name": label, "lab": home, "state": "", "away": False})
    rows.sort(key=lambda r: (r["away"], r["lab"], r["name"], r["id"]))
    return rows


def native_reservations(results):
    """ACC:get_res rows ('username,device_id,start,end') keyed by the lab's reservation key."""
    rows = []
    for key, value in results.items():
        parts = unmap_arg(value).split(',')
        rows.append({'key': key, 'username': parts[0], 'device_id': int(parts[1]),
                     'start_time': _parse_reservation_time(parts[2]), 'end_time': _parse_reservation_time(parts[3])})
    return rows


def native_limits(results):
    """ACC:get_perms -> (allowed device ids or None for all, limits), where
    limits(device_id) is (max_reservation_time_sec, max days ahead or None)."""
    raw = unmap_arg(results.get("UC", map_arg("[]")))
    try:
        row = ast.literal_eval(raw)[0]  # the server returns a list of rows
    except Exception:
        row = []
    level = row[0] if row else ""
    details = {}
    if level == "Normal User":
        raw = unmap_arg(results.get("details", map_arg("{}")))
        try:
            details = json.loads(raw) if raw else {}
        except Exception:
            details = {}
    caps = details.get("caps", {}) or {}
    allowed = power = None
    if level == "Normal User":
        try:
            allowed = {int(d) for d in details.get("devices", []) or []}
        except Exception:
            allowed = set()
    elif level == "Power User":
        try:
            allowed = {int(x) for x in str_to_list(row[5])}
        except Exception:
            allowed = None
        try:
            power = int(row[4])
        except Exception:
            power = None

    def limits(device_id):
        if level == "Normal User":
            # tolerate a server sending str keys, or int keys
            c = caps.get(str(device_id)) or caps.get(device_id) or {}
            try:
                return int(c.get("max_reservation_time_sec", 0) or 0), _days(c.get("max_horizon_sec", 0))
            except Exception:
                return 0, None
        if level == "Power User" and power is not None:
            return power, None
        if level == "Admin":
            return 60 * 60, None  # no cap; 60 min blocks keep the picker sane
        return 0, None

    return allowed, limits


# ── slots ────────────────────────────────────────────────────────────

def slot_minutes(max_reservation_time_sec) -> int:
    try:
        max_t_sec = int(max_reservation_time_sec)
    except Exception:
        return 30
    if max_t_sec <= 0:
        return 30
    max_min = max_t_sec // 60
    if max_min < 10:
        # Can't make a valid reservation at all (server min = 10 minutes).
        return max_min
    # The largest "nice" block <= max_min
    best = 10
    for c in [10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360, 480, 720]:
        if c > max_min:
            break
        best = c
    return best


def is_slot_conflicting(slot: tuple, reservations: list):
    slot_start, slot_end = slot
    for res_start, res_end in reservations:
        if slot_start < res_end and slot_end > res_start:
            return True
    return False


def build_slots(day, block_minutes, taken, *, now, days=1):
    """Free [start, end) blocks of `block_minutes` from midnight on each of
    `days` days from `day`, as resdev lists them: blocks that ended by `now`
    are skipped, and so are blocks overlapping a `taken` (start, end). All
    naive local datetimes."""
    if block_minutes <= 0:
        return []
    slots = []
    for i in range(days):
        d = day + timedelta(days=i)
        start_of_day = datetime.combine(d, time(0, 0))
        offset = 0
        while offset < 24 * 60:
            slot = (start_of_day + timedelta(minutes=offset), start_of_day + timedelta(minutes=offset + block_minutes))
            # Stop if the block bleeds into the next calendar day
            if slot[1].date() != d and slot[1].time() != time.min:
                break
            if slot[1] > now and not is_slot_conflicting(slot, taken):
                slots.append(slot)
            offset += block_minutes
    return slots


# ── drivers ──────────────────────────────────────────────────────────

# What each bundled driver package exposes.
DRIVER_CALLS = {"pluto": "adi.Pluto", "adalm_pluto": "adi.Pluto", "usrp": "uhd.usrp.MultiUSRP"}
DRIVER_IMPORTS = {"pluto": "adalm_pluto import adi", "adalm_pluto": "adalm_pluto import adi", "usrp": "usrp import uhd"}


def driver_usage(device_type, class_name=None) -> dict:
    """The import line and constructor for a device type; `{token!r}` in the
    template is where the token goes, as a string literal. A type without a
    fixed form is a dynamic driver (ensure_driver installs it); both are None
    when the lab's names for it are not Python names."""
    if call := DRIVER_CALLS.get(device_type):
        return {"import_line": f"from remoterf.drivers.{DRIVER_IMPORTS[device_type]}",
                "constructor_template": f"sdr = {call}(token={{token!r}})"}
    from .drivers.dynamic_device import _default_class_name, _valid_identifier

    if not _valid_identifier(str(device_type or "")):  # what the driver installer accepts
        return {"import_line": None, "constructor_template": None}
    cls = class_name or _default_class_name(device_type)
    if not str(cls).isidentifier():
        return {"import_line": None, "constructor_template": None}
    return {"import_line": f"from remoterf.drivers.{device_type} import {cls}",
            "constructor_template": f"dev = {cls}(token={{token!r}})"}


# ── device rows ──────────────────────────────────────────────────────

def _device(handle, name, dtype, deployment, online, state, until, nxt, max_sec=None, horizon_sec=None):
    return {"handle": handle, "name": name, "type": dtype, "deployment": deployment, "online": bool(online),
            "state": state if state in ("free", "busy", "mine") else "unknown",
            **_instant("until", until), **_instant("next", nxt),
            "slot_minutes": None if max_sec is None else slot_minutes(max_sec), "max_days_ahead": _days(horizon_sec)}


def _global_slug(device_id, deployment_name):
    """The lab's name when it is its own Global slug (checked by discovery),
    else None: then the bare token, which works as is."""
    from .core.grpc_client import _deployment_id
    from .deployment.homes import NAME_RE

    name = str(deployment_name or "").lower()
    try:
        if NAME_RE.match(name) and _deployment_id(name) == str(device_id).split(":")[0]:
            return name
    except RuntimeError:
        pass
    return None


def _destination_notes(data):
    """Global's word on deployments that refused or didn't answer, as the shell says it."""
    notes, offline = [], []
    for d in data.get("destinations", ()):
        name = d.get("display_name") if d.get("display_name") != d.get("deployment_id") else None
        if d.get("status") == "unavailable":
            offline.append(f"@{name}" if name else "an unnamed deployment")
        elif d.get("status") == "blocked" and not (d.get("provenance") == "destination_signed"
                                                  and d.get("error_code") == "principal_unknown"):
            notes.append(explain(d.get("error_code") or "blocked",
                                 "destination" if d.get("provenance") == "destination_signed" else "home", where=name))
    if offline:
        notes.append(f"{', '.join(offline)} {'is' if len(offline) == 1 else 'are'} offline; the list may be incomplete.")
    elif data.get("incomplete"):
        notes.append("Some deployments did not answer; the list may be incomplete.")
    return notes


def _held_at(reservations, device_id):
    """Your live reservations at the device's deployment; None if unknown.
    `reservations`: fetches Global's reservations reply."""
    dep, now = str(device_id).split(":")[0], datetime.now().timestamp()
    try:
        return sum(1 for r in reservations()["reservations"]
                   if r["mine"] and r["deployment_id"] == dep and r["end_time"] > now) or None
    except Exception:  # noqa: BLE001 - the sentence then leaves the number out
        return None


def _global_devices(backend, slug=None, *, destination_notes=True):
    """A Global home's devices -> (devices, interval, notes, incomplete);
    an older Global without availability: its device list, state unknown.
    destination_notes=False: remoterf.devices()' one line for any that didn't answer."""
    slug = slug or _global_slug
    notes = []
    try:
        data = backend.availability()
    except AccountBackendError as exc:
        if exc.retry_after:
            raise _rate_limited(exc.retry_after) from None
        if exc.code != "not_found":
            raise
        data, notes = backend.devices(), ["This Global home does not report availability yet; showing devices only."]
    incomplete = bool(data.get("incomplete") or any(d.get("status") == "unavailable" for d in data["destinations"]))
    if destination_notes:
        notes += _destination_notes(data)
    elif incomplete:
        notes.append("Some deployments did not answer; the list may be incomplete.")
    slugs, devices = {}, []
    for d in data["devices"]:
        key = (d.get("deployment_id"), d.get("deployment_name"))
        if key not in slugs:
            slugs[key] = slug(d.get("device_id", ""), d.get("deployment_name"))
        devices.append(_device(f"{d['handle']}@{slugs[key]}" if d.get("handle") and slugs[key] else None,
                               d.get("display_name"), d.get("device_type"), d.get("deployment_name"), d.get("online"),
                               d.get("state"), d.get("until"), d.get("next"),
                               d.get("max_reservation_time_sec"), d.get("max_horizon_sec")))
    return devices, data.get("interval"), notes, incomplete


def _lab_devices(home, creds, *, shell=False):
    """A deployment's ACC:availability -> (devices, interval, notes, incomplete);
    one older than it: its device list, state unknown. Rows carry the
    deployment's device id as `_id`. shell=True: refusals read as remoterf.devices()
    has always shown them ('that deployment'; an old lab's own text)."""
    from .core.grpc_client import rpc_client

    args = {"un": map_arg(creds[0]), "pw": map_arg(creds[1])}
    res = rpc_client(function_name="ACC:availability", args=args).results
    if "UC" in res:
        data = json.loads(unmap_arg(res["UC"]))
        return [{**_device(f"{d['handle']}@{home}" if d.get("handle") and home else None, d.get("display_name"),
                           d.get("device_type"), home, d.get("online"), d.get("state"), d.get("until"), d.get("next")),
                 "_id": d.get("local_device_id")} for d in data["devices"]], data.get("interval"), [], False
    if "retry_after" in res:
        raise _rate_limited(unmap_arg(res["retry_after"]))
    error = str(unmap_arg(res.get("ace", map_arg("Unexpected reply from the deployment."))))
    if "No matching server function" not in error:
        raise _deployment_error(error, where=None if shell else home)
    res = rpc_client(function_name="ACC:get_dev", args=args).results
    if "ace" in res:
        text = str(unmap_arg(res["ace"]))
        raise RuntimeError(text) if shell else _deployment_error(text, where=home)
    devices = []
    for key in sorted(res, key=lambda k: int(k) if str(k).isdigit() else 0):
        label = str(unmap_arg(res[key]))
        local = _LOCAL_LABEL.match(label)
        if _PARTNER_LABEL.match(label) or not local:
            continue  # partner devices have no handles yet
        name, handle = _name_and_handle(local["name"])
        devices.append({**_device(f"{handle}@{home}" if handle and home else None, name, None, home,
                                  local["state"].startswith("online"), "unknown", None, None), "_id": key})
    return devices, None, ["This deployment does not report availability yet; showing devices only."], False


# ── sessions ─────────────────────────────────────────────────────────

@dataclass
class Session:
    account: object  # RemoteRFAccount
    home: str | None
    home_kind: str  # "deployment" | "global"


@_op
def saved_session() -> Session:
    """The saved login (Global: the stored HTTPS session; a deployment: the
    active home's stored login). Never prompts."""
    from .core.direct_path import _credentials
    from .core.grpc_acc import RemoteRFAccount
    from .core.grpc_client import _active_home_name, federated_backend
    from .deployment import homes
    from .deployment.state import load_target

    target = load_target()
    if target and target["transport"] == "https-json":
        try:
            backend = federated_backend()
        except AccountBackendError as exc:
            code = str(exc.code)
            if code in _AUTH_FAILED or code.startswith(("Home ", "Invalid home", "Authenticated home")):
                raise RemoteRFOpsError("session_expired", STALE_LOGIN) from exc
            raise  # a busy or unreachable Global: _backend_error keeps retry_after
        except RuntimeError as exc:
            raise RemoteRFOpsError("not_logged_in", NOT_LOGGED_IN) from exc
        account = RemoteRFAccount(backend.credentials["username"], backend=backend, quiet=True)
        return Session(account, homes.GLOBAL_ACCOUNTS, "global")
    creds = _credentials()
    if not creds:
        raise RemoteRFOpsError("not_logged_in", NOT_LOGGED_IN)
    account = RemoteRFAccount(*creds, quiet=True)
    account.session_token = creds[1]
    return Session(account, _active_home_name(), "deployment")


def _resolve(s, handle):
    """`Name@deployment` -> (canonical handle, device id at the home, its row)."""
    name, _, lab = str(handle or "").strip().rpartition("@")
    lab, key = lab.lower(), _key(name)
    if not key or not lab:
        raise RemoteRFOpsError("invalid_argument", "A handle is Name@deployment, for example Pluto_00@ucla.")
    if s.home_kind == "global":
        from .core.grpc_client import _deployment_id

        devices = s.account.backend.devices()["devices"]
        try:
            dep = _deployment_id(lab)
        except RuntimeError:
            if any(str(d.get("deployment_name") or "").lower() == lab for d in devices):
                # a deployment we know, not reachable now
                raise RemoteRFOpsError("deployment_unreachable", explain("route_unavailable", where=lab)) from None
            dep = None
        matches = [(f"{d['handle']}@{lab}", d["device_id"], d) for d in devices
                   if dep and d.get("deployment_id") == dep and _key(d.get("handle") or "") == key]
    elif lab != s.home:
        raise RemoteRFOpsError("unknown_handle", f"This login is at {s.home}; it can't reach devices at {lab}.")
    else:
        matches = []
        for row in native_rows(_results(s.account.get_devices()), s.home):
            h = None if row["away"] else _name_and_handle(row["name"])[1]
            if h and _key(h) == key:
                matches.append((f"{h}@{lab}", row["id"], row))
    if not matches:
        raise RemoteRFOpsError("unknown_handle", f"No device {name}@{lab} is visible to this account.")
    # Two devices with one name: the lab's handle drives the lowest id.
    # ponytail: the lab puts its own devices before host ones first, which the listing doesn't show
    return min(matches, key=lambda m: int(str(m[1]).rpartition(":")[2]))


def _version():
    from importlib import metadata

    try:
        return metadata.version("remoterf")
    except metadata.PackageNotFoundError:
        return "unknown"


# ── operations ───────────────────────────────────────────────────────

@_op
def whoami(s) -> dict:
    if s.home_kind == "global":
        perms = s.account.backend.permissions()
        deployments = sorted({e["display_name"] for e in perms.get("federation", [])
                              if e.get("status") == "ok" and e["display_name"] != e.get("deployment_id")})
    else:
        deployments = [s.home] if s.home else []
    return {"account": f"{s.account.username}@{s.home}" if s.home else s.account.username, "home": s.home,
            "home_kind": s.home_kind, "deployments": deployments, "client_version": _version(), "reserve_enabled": True}


@_op
def list_devices(s, *, deployment=None, online_only=True, type=None) -> dict:
    if s.home_kind == "global":
        devices, _, notes, incomplete = _global_devices(s.account.backend)
    else:
        devices, _, notes, incomplete = _lab_devices(s.home, (s.account.username, s.account.password))
        _, limits = native_limits(_results(s.account.get_perms()))
        for d in devices:
            max_sec, d["max_days_ahead"] = limits(int(d["_id"]))
            d["slot_minutes"] = slot_minutes(max_sec)
    for d in devices:
        d.pop("_id", None)
    deployment = (deployment or "").lower()
    return {"devices": [d for d in devices
                        if (d["online"] or not online_only)
                        and (not deployment or deployment in (str(d["deployment"]).lower(), (d["handle"] or "").rpartition("@")[2]))
                        and (not type or str(d["type"] or "").lower() == type.lower())],
            "notes": notes, "incomplete": incomplete}


def _local_zone():
    """This machine's IANA zone name, or None when it has none."""
    from zoneinfo import ZoneInfo

    name = os.environ.get("TZ", "").lstrip(":") or os.path.realpath("/etc/localtime").partition("zoneinfo/")[2]
    try:
        return ZoneInfo(name).key
    except (ValueError, LookupError):
        return None


def _day(value, local):
    if value is None or isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return date.fromisoformat(value.strip())
    return local(_epoch(value)).date()


@_op
def find_slots(s, handle, *, start_day=None, days=1, tz=None, now=None) -> dict:
    """Free slots aligned like resdev: blocks from midnight in `tz` (default
    this machine's zone). `now`: an aware datetime, for tests."""
    zone = None
    if tz:
        from zoneinfo import ZoneInfo

        try:
            zone = ZoneInfo(tz)
        except (ValueError, LookupError):
            raise RemoteRFOpsError("invalid_argument", f"Unknown time zone {tz!r}.") from None
    local = lambda ts: datetime.fromtimestamp(ts, zone).replace(tzinfo=None)
    now = local((now or datetime.now(timezone.utc)).timestamp())
    day = _day(start_day, local) or now.date()
    if not isinstance(days, int) or days < 1:
        raise RemoteRFOpsError("invalid_argument", "days must be at least 1.")
    h, device_id, row = _resolve(s, handle)
    if s.home_kind == "global":
        max_sec, max_days = row.get("max_reservation_time_sec") or 0, _days(row.get("max_horizon_sec"))
        taken = [(local(r["start_time"]), local(r["end_time"]))
                 for r in s.account.backend.reservations()["reservations"] if r["device_id"] == device_id]
    else:
        max_sec, max_days = native_limits(_results(s.account.get_perms()))[1](device_id)
        taken = [(local(r["start_time"].timestamp()), local(r["end_time"].timestamp()))
                 for r in native_reservations(_results(s.account.get_reservations())) if r["device_id"] == device_id]
    block = slot_minutes(max_sec)
    if block < 10:
        raise RemoteRFOpsError("permission_denied",
                               f"{h} can be reserved for at most {int(max_sec or 0) // 60} minutes, shorter than the 10-minute minimum.")
    if max_days and (day - now.date()).days + days > max_days:
        raise RemoteRFOpsError("invalid_argument", f"{h} can be reserved at most {max_days} days ahead.")
    epoch = lambda dt: int(dt.replace(tzinfo=zone).timestamp())
    # On a DST change day a wall-clock block can be skipped (02:30) or doubled (01:00-02:00): keep real-length ones.
    slots = [(epoch(a), epoch(b)) for a, b in build_slots(day, block, taken, now=now, days=days)
             if epoch(b) - epoch(a) == block * 60 and local(epoch(a)) == a]
    return {"handle": h, "slot_minutes": block, "timezone": tz or _local_zone(), "days": days,
            "slots": [{**_instant("start", a), **_instant("end", b)} for a, b in slots]}


@_op
def list_reservations(s, *, mine_only=True) -> dict:
    out, notes = [], []
    if s.home_kind == "global":
        backend = s.account.backend
        data = backend.reservations()
        devices = {d["device_id"]: d for d in backend.devices()["devices"]}
        names = {d["deployment_id"]: d["display_name"] for d in data["destinations"]
                 if d.get("display_name") != d["deployment_id"]}
        notes = _destination_notes(data)
        slugs = {}
        for r in data["reservations"]:
            if mine_only and not r.get("mine"):
                continue
            dev = devices.get(r["device_id"], {})
            lab = dev.get("deployment_name") or names.get(r["deployment_id"])
            if r["deployment_id"] not in slugs:
                slugs[r["deployment_id"]] = _global_slug(r["device_id"], lab)
            slug = slugs[r["deployment_id"]]
            out.append({"reservation_id": r["reservation_id"],
                        "handle": f"{dev['handle']}@{slug}" if dev.get("handle") and slug else None,
                        "device": dev.get("display_name") or r.get("display_name") or "unlisted device",
                        "deployment": lab, **_instant("start", r["start_time"]), **_instant("end", r["end_time"]),
                        "mine": bool(r.get("mine"))})
    else:
        results = _results(s.account.get_reservations())
        rows = {row["id"]: row for row in native_rows(_results(s.account.get_devices()), s.home)}
        for r in native_reservations(results):
            mine = r["username"] == s.account.username
            if mine_only and not mine:
                continue
            row = rows.get(r["device_id"]) or {"name": "unlisted device", "lab": s.home, "away": True}
            name, handle = (row["name"], None) if row["away"] else _name_and_handle(row["name"])
            out.append({"reservation_id": str(r["key"]), "handle": f"{handle}@{s.home}" if handle and s.home else None,
                        "device": name, "deployment": row["lab"], **_instant("start", r["start_time"].timestamp()),
                        **_instant("end", r["end_time"].timestamp()), "mine": mine})
    out.sort(key=lambda r: (r["start_ts"] or 0, str(r["device"])))
    return {"reservations": out, "notes": notes}


def _native_reservations(s):
    """A deployment's reservation rows, or None when it won't list them."""
    try:
        results = s.account.get_reservations().results
        return None if "ace" in results else native_reservations(results)
    except Exception:  # noqa: BLE001 - the reservation stands; only its id is unknown
        return None


@_op
def reserve(s, handle, start, end, *, reveal_token=False) -> dict:
    """Forward one reservation to the deployment, which decides; never retried."""
    start_ts, end_ts = _epoch(start), _epoch(end)
    if end_ts <= start_ts:
        raise RemoteRFOpsError("invalid_argument", "end must be after start.")
    h, device_id, row = _resolve(s, handle)
    where = h.rpartition("@")[2]
    if s.home_kind == "global":
        backend = s.account.backend
        try:
            reservation = backend.reserve(device_id, start_ts, end_ts)
        except AccountBackendError as exc:
            raise _backend_error(exc, where=where, device=h, request="reservation",
                                 held=_held_at(backend.reservations, device_id) if exc.code == "reservation_limit" else None,
                                 minutes=int(row.get("max_reservation_time_sec") or 0) // 60 or None,
                                 days=_days(row.get("max_horizon_sec"))) from exc
        rid, token = reservation["reservation_id"], reservation.get("token")
        # An older Global issues no rfg_ token; the handle still works.
        token = f"{token}@{h.rpartition('@')[2]}" if token and token.startswith("rfg_") else None
    else:
        before = _native_reservations(s)
        try:
            token = s.account.reserve_device(device_id, datetime.fromtimestamp(start_ts), datetime.fromtimestamp(end_ts))
        except RuntimeError as exc:  # the deployment's refusal, or rpc_client's explained failure
            if isinstance(exc, grpc.RpcError):
                raise  # no answer at all: _op says so
            raise _deployment_error(str(exc.__cause__ or exc), where=where, device=h, request="reservation") from None
        if not token:
            raise RemoteRFOpsError("deployment_error", "The deployment answered without a token.")
        token = f"{token}@{s.home}"
        # The deployment answers with the token only: its key is the new row in the listing.
        # Rows are in the deployment's own zone, so only their length is compared.
        after = _native_reservations(s) if before is not None else None
        old = {r["key"] for r in before or ()}
        new = [r for r in after or ()
               if r["key"] not in old and r["device_id"] == device_id and r["username"] == s.account.username
               and (r["end_time"] - r["start_time"]).total_seconds() == end_ts - start_ts]
        rid = str(new[0]["key"]) if len(new) == 1 else None
    out = {"reservation_id": rid, "handle": h, **_instant("start", start_ts), **_instant("end", end_ts), "token_ref": rid}
    if token and reveal_token:
        out["token"] = token
    out["use_as_token"] = h
    return out


@_op
def cancel(s, reservation_id) -> dict:
    rid = str(reservation_id)
    if s.home_kind == "global":
        num, at, lab = rid.rpartition("@")
        if at:  # '10@ucla', as the CLI shows a Global reservation
            from .core.grpc_client import _deployment_id

            rid = f"{_deployment_id(lab.lower())}:{num}"
        s.account.backend.cancel(rid)
    else:
        _results(s.account.cancel_reservation(rid))
    return {"cancelled": True, "reservation_id": rid}


@_op
def device_api(s, handle) -> dict:
    """The driver's API from the deployment's IDL; needs no reservation."""
    from .drivers.dynamic_device import _client_class_name, fetch_idl

    h, device_id, _ = _resolve(s, handle)
    schema = fetch_idl(device_id=device_id)
    device_type = str(schema.get("device_type") or "")
    cls = _client_class_name(schema)
    usage = driver_usage(device_type, cls)
    version = str(schema.get("schema_version") or "")
    if version == "2.0":
        methods = [{"name": m["name"] if str(m.get("owner", "")).rpartition(".")[2] == cls else f"{m.get('owner')}.{m['name']}",
                    "kind": "call", "doc": str(m.get("doc") or "")} for m in schema.get("methods", ())]
    else:
        methods = [{"name": str(key).removeprefix(prefix), "kind": kind, "doc": str((info or {}).get("doc") or "")}
                   for kind, field, prefix in (("getter", "getters", "get_"), ("setter", "setters", "set_"), ("call", "calls", "call_"))
                   for key, info in (schema.get(field) or {}).items() if kind == "call" or str(key).startswith(prefix)]
    notes = ["Reading this API needs no reservation; calling the device needs a live one."]
    if not usage["import_line"]:
        notes.append("This deployment's names for the driver are not Python names, so there is no import line; "
                     "ask its administrator.")
    elif device_type not in DRIVER_CALLS:
        notes.append(f"Install this driver once per machine: from remoterf.drivers import ensure_driver; ensure_driver(token={h!r})")
    template = usage["constructor_template"]
    return {"handle": h, "device_type": device_type, "class_name": DRIVER_CALLS.get(device_type) or cls,
            "import_line": usage["import_line"], "constructor": template and template.format(token=h),
            "schema_version": version, "methods": methods, "notes": notes}
