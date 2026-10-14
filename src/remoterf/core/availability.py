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

import time
from datetime import datetime

from ..ops import NOT_LOGGED_IN, RemoteRFOpsError

_cache = None  # (key, monotonic at fetch, rows or None after a refusal, interval)


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


def _rows(devices, interval, notes, incomplete):
    return ([_row(d["handle"], d["name"], d["type"], d["lab"], d["online"], d["state"], d["until_ts"], d["next_ts"])
             for d in devices], interval, "\n".join(notes) or None)


def _global(backend):
    from .app import _global_slug
    from ..ops import _global_devices

    return _rows(*_global_devices(backend, _global_slug))


def _lab(home, creds):
    from ..ops import _lab_devices

    return _rows(*_lab_devices(home, creds))


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
                print(exc)
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
        except RemoteRFOpsError as exc:
            if not exc.retry_after:
                raise RuntimeError(exc.message) from None
            # Remember the refusal so calls inside the wait stay local.
            old = _cache[2] if _cache and _cache[0] == key else None
            _cache = (key, time.monotonic(), old, float(exc.retry_after))
            if old is not None:
                _print(old, f"(cached; refresh in {exc.retry_after} s)")
                return old
            print(f"Asked too recently; try again in {exc.retry_after} s.")
            return []
    except RuntimeError as exc:
        print(exc)
        return []
    _cache = (key, time.monotonic(), rows, float(interval or 10))
    _print(rows, note)
    return rows
