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

"""remoterf.devices(): the devices the saved login can use, with handles and
what is free right now. Homes allow one call per account per interval; within
it this process reprints its last answer instead of asking again."""

import json
import time
from datetime import datetime

from ..common.utils import map_arg, unmap_arg
from .messages import explain

_cache = None  # (key, monotonic at fetch, rows or None after a refusal, interval)
NOT_LOGGED_IN = "Not logged in: run `remoterf` and log in (keep the login saved)."
STALE_LOGIN = "Saved login is no longer valid: run `remoterf` and log in again."


class _Refused(Exception):
    def __init__(self, wait):
        self.wait = wait


def _when(ts):
    try:
        return datetime.fromtimestamp(ts) if ts else None
    except (OverflowError, ValueError, OSError, TypeError):
        return None


def _hhmm(dt):
    return dt.strftime("%H:%M") if dt.date() == datetime.now().date() else dt.strftime("%b %d %H:%M")


def _row(handle, name, dtype, lab, online, state, until, nxt):
    return {"handle": handle, "name": name, "type": dtype, "lab": lab, "online": bool(online),
            "state": state if state in ("free", "busy", "mine") else "unknown",
            "until": _when(until), "next": _when(nxt)}


def _global(backend):
    from .app import _global_slug
    from ..deployment.http import AccountBackendError

    note = None
    try:
        data = backend.availability()
    except AccountBackendError as exc:
        if exc.retry_after:
            raise _Refused(exc.retry_after) from None
        if exc.code != "not_found":
            raise
        data, note = backend.devices(), "This Global home does not report availability yet; showing devices only."
    if data.get("incomplete") or any(d.get("status") == "unavailable" for d in data["destinations"]):
        note = "\n".join(filter(None, [note, "Some deployments did not answer; the list may be incomplete."]))
    slugs, rows = {}, []
    for d in data["devices"]:
        key = (d.get("deployment_id"), d.get("deployment_name"))
        if key not in slugs:
            slugs[key] = _global_slug(d.get("device_id", ""), d.get("deployment_name"))
        slug = slugs[key]
        handle = f"{d['handle']}@{slug}" if d.get("handle") and slug else None
        rows.append(_row(handle, d.get("display_name"), d.get("device_type"), d.get("deployment_name"),
                         d.get("online"), d.get("state"), d.get("until"), d.get("next")))
    return rows, data.get("interval"), note


def _lab(home, creds):
    from .grpc_client import rpc_client

    args = {"un": map_arg(creds[0]), "pw": map_arg(creds[1])}
    res = rpc_client(function_name="ACC:availability", args=args).results
    if "UC" in res:
        data = json.loads(unmap_arg(res["UC"]))
        rows = [_row(f"{d['handle']}@{home}" if d.get("handle") and home else None, d.get("display_name"),
                     d.get("device_type"), home, d.get("online"), d.get("state"), d.get("until"), d.get("next"))
                for d in data["devices"]]
        return rows, data.get("interval"), None
    if "retry_after" in res:
        raise _Refused(int(unmap_arg(res["retry_after"])))
    error = str(unmap_arg(res.get("ace", map_arg("Unexpected reply from the deployment."))))
    if error == "Invalid login credentials.":
        raise RuntimeError(STALE_LOGIN)
    if "No matching server function" not in error:
        raise RuntimeError(error)
    # A lab older than ACC:availability: its device list, no availability.
    from .app import _LOCAL_LABEL, _PARTNER_LABEL, _name_and_handle

    res = rpc_client(function_name="ACC:get_dev", args=args).results
    if "ace" in res:
        raise RuntimeError(str(unmap_arg(res["ace"])))
    rows = []
    for key in sorted(res, key=lambda k: int(k) if str(k).isdigit() else 0):
        label = str(unmap_arg(res[key]))
        local = _LOCAL_LABEL.match(label)
        if _PARTNER_LABEL.match(label) or not local:
            continue  # partner devices have no handles yet
        name, handle = _name_and_handle(local["name"])
        rows.append(_row(f"{handle}@{home}" if handle and home else None, name, None, home,
                         local["state"].startswith("online"), "unknown", None, None))
    return rows, None, "This deployment does not report availability yet; showing devices only."


def _print(rows, footer=None):
    def now(r):
        if r["state"] == "free":
            return "free"
        if r["state"] in ("busy", "mine") and r["until"]:
            return f"{'yours' if r['state'] == 'mine' else 'in use'} until {_hhmm(r['until'])}"
        return "?"

    table = [("DEVICE (token=)", "TYPE", "ONLINE", "NOW", "NEXT")] + [
        (r["handle"] or f"{r['name']}{' @' + r['lab'] if r['lab'] else ''} (no handle)", r["type"] or "-", "online" if r["online"] else "offline",
         now(r), _hhmm(r["next"]) if r["next"] else "-") for r in rows]
    widths = [max(len(str(line[i])) for line in table) for i in range(5)]
    for line in table:
        print("  ".join(str(c).ljust(w) for c, w in zip(line, widths)).rstrip())
    if not rows:
        print("(no devices)")
    if footer:
        print(footer)


def devices():
    global _cache
    from ..deployment.state import load_target

    target = load_target()
    try:
        if target and target["transport"] == "https-json":
            from .grpc_client import federated_backend

            try:
                backend = federated_backend()
            except RuntimeError as exc:
                print(explain(exc))
                return []
            key, fetch = ("global", target["origin"]), lambda: _global(backend)
        else:
            from . import direct_path
            from .grpc_client import _active_home_name

            home, creds = _active_home_name(), direct_path._credentials()
            if not creds:
                print(NOT_LOGGED_IN)
                return []
            key, fetch = ("lab", home, creds[0]), lambda: _lab(home, creds)

        if _cache and _cache[0] == key:
            left = _cache[3] - (time.monotonic() - _cache[1])
            if left > 0:
                if _cache[2] is None:
                    print(f"Asked too recently; try again in {max(1, round(left))} s.")
                    return []
                _print(_cache[2], f"(cached; refresh in {max(1, round(left))} s)")
                return _cache[2]
        try:
            rows, interval, note = fetch()
        except _Refused as exc:
            # Remember the refusal so calls inside the wait stay local.
            old = _cache[2] if _cache and _cache[0] == key else None
            _cache = (key, time.monotonic(), old, float(exc.wait))
            if old is not None:
                _print(old, f"(cached; refresh in {exc.wait} s)")
                return old
            print(f"Asked too recently; try again in {exc.wait} s.")
            return []
    except RuntimeError as exc:
        print(explain(exc))
        return []
    _cache = (key, time.monotonic(), rows, float(interval or 10))
    _print(rows, note)
    return rows
