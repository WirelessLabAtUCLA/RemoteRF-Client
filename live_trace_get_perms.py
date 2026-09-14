#!/usr/bin/env python3
"""
READ-ONLY live trace of RemoteRF-Client's get_perms() deserialization path,
run against whatever RRF2 deployment is currently configured for this
checkout (`remoterf --config --addr <host>:<port>` must already have been
run once so connection settings are on disk at ~/.config/remoterf-client/).

This exists to answer one question with live evidence instead of a
synthetic repro: does the real ACC:get_perms RPC hand this client's
map_arg/unmap_arg deserializer a STRING for "UC"/"details" (what
src/remoteRF/core/app.py's interactive_reserve_next_days_auto() and
perms() expect, since they immediately re-parse with
ast.literal_eval()/json.loads()), or does it hand back an already-decoded
Python dict/list (which would make that second parse fail and get
silently swallowed)?

SAFETY / read-only guarantees:
  - Calls ONLY account.get_perms() and account.get_devices() — both are
    read-only RPCs (ACC:get_perms, ACC:get_dev).
  - NEVER calls reserve_device() / cancel_reservation() / create_user() /
    set_enroll() / any mutating RPC. Grep this file for "reserve" — it
    only appears in comments/prints.
  - Does not edit, patch, or monkeypatch any file in this checkout. It
    only imports the existing, unmodified remoteRF package and calls two
    of its existing read-only methods.
  - Safe to delete after use; nothing here is committed or pushed.

Run with the SAME Python environment `remoterf` normally uses (so grpc /
prompt_toolkit / remoterf_federation_core are importable), e.g. from the
repo root:

    source venv/bin/activate
    python3 live_trace_get_perms.py

or directly:

    ./venv/bin/python3 live_trace_get_perms.py

Then paste back everything it prints.
"""

import ast
import json
import sys
import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from remoteRF.core import app as app_mod
from remoteRF.common.utils import unmap_arg, map_arg, str_to_list


def banner(msg):
    print("\n" + "=" * 78)
    print(msg)
    print("=" * 78)


def show(label, value):
    print(f"{label}\n    type = {type(value).__name__}\n    repr = {value!r}")


def main():
    banner("STEP 0 — login (use a native RRF2 account, NOT the Global federated one)")
    username = input("Username: ")
    password = getpass.getpass("Password (hidden): ")
    app_mod.account.username = username
    app_mod.account.password = password
    ok = app_mod.account.login_user()
    if not ok:
        print("Login failed (bad credentials, or server unreachable) — aborting before any further RPCs.")
        return

    banner("STEP 1 — raw get_perms() RPC response: account.get_perms()")
    perms_resp = app_mod.account.get_perms()
    print(f"perms_resp type       = {type(perms_resp).__name__}")
    print(f"perms_resp.results type = {type(perms_resp.results).__name__}")
    print(f"perms_resp.results keys = {list(perms_resp.results.keys())}")

    if "ace" in perms_resp.results:
        show("perms_resp.results['ace']  (raw, BEFORE unmap_arg)", perms_resp.results["ace"])
        show("unmap_arg(perms_resp.results['ace'])", unmap_arg(perms_resp.results["ace"]))
        print("\nServer returned an access-control-error ('ace') instead of UC/details — stopping here.")
        return

    banner("STEP 2 — 'UC' field: raw wire value vs. after unmap_arg()  <-- THE KEY QUESTION")
    uc_present = "UC" in perms_resp.results
    print(f"'UC' in perms_resp.results: {uc_present}")
    if uc_present:
        show("perms_resp.results['UC']  (raw grpc_pb2.Argument, BEFORE unmap_arg)", perms_resp.results["UC"])
    uc_raw = unmap_arg(perms_resp.results.get("UC", map_arg("[]")))
    show(
        "uc_raw = unmap_arg(perms_resp.results['UC'])\n"
        "    <-- this is EXACTLY the value app.py's interactive_reserve_next_days_auto()\n"
        "        and perms() receive before they call ast.literal_eval(uc_raw)",
        uc_raw,
    )

    banner("STEP 3 — app.py's actual next line: ast.literal_eval(uc_raw)[0]")
    try:
        perm_row = ast.literal_eval(uc_raw)[0]
        print("ast.literal_eval(uc_raw) SUCCEEDED  -> uc_raw was a string, as app.py assumes.")
    except Exception as e:
        perm_row = []
        print(f"ast.literal_eval(uc_raw) RAISED: {type(e).__name__}: {e}")
        print("app.py's own `except Exception: perm_row = []` swallows this silently — no traceback,")
        print("no error message to the user, perm_level below just becomes \"\".")
    show("perm_row", perm_row)
    perm_level = perm_row[0] if perm_row else ""
    show("perm_level", perm_level)

    banner("STEP 4 — 'details' field (app.py only parses this when perm_level == 'Normal User')")
    details_present = "details" in perms_resp.results
    print(f"'details' in perms_resp.results: {details_present}")
    if details_present:
        show("perms_resp.results['details']  (raw grpc_pb2.Argument, BEFORE unmap_arg)", perms_resp.results["details"])
    details_raw = unmap_arg(perms_resp.results.get("details", map_arg("{}")))
    show("details_raw = unmap_arg(perms_resp.results['details'])", details_raw)

    details = {}
    if perm_level == "Normal User":
        try:
            details = json.loads(details_raw) if details_raw else {}
            print("json.loads(details_raw) SUCCEEDED -> details_raw was a JSON string, as app.py assumes.")
        except Exception as e:
            details = {}
            print(f"json.loads(details_raw) RAISED: {type(e).__name__}: {e}")
            print("app.py's own `except Exception: details = {}` swallows this silently -> caps/devices lost.")
    else:
        print(f"perm_level is {perm_level!r}, not 'Normal User' -> app.py's real code never even attempts")
        print("json.loads(details_raw) in this run. Probing it anyway, for completeness only:")
        try:
            probe = json.loads(details_raw) if details_raw else {}
            print(f"    (probe) json.loads WOULD succeed -> {probe!r}")
        except Exception as e:
            print(f"    (probe) json.loads WOULD raise {type(e).__name__}: {e}")

    show("details (final value app.py's code would be left holding)", details)
    caps = details.get("caps", {}) or {}
    show("caps = details.get('caps', {})", caps)

    banner("STEP 5 — allowed_dev_ids derivation (this is what gates 'No devices available for your permission level.')")
    allowed_dev_ids = None
    if perm_level == "Normal User":
        devs = details.get("devices", []) or []
        try:
            allowed_dev_ids = {int(d) for d in devs}
        except Exception as e:
            allowed_dev_ids = set()
            print(f"{{int(d) for d in details['devices']}} RAISED: {type(e).__name__}: {e}")
    elif perm_level == "Power User":
        try:
            allowed_dev_ids = {int(x) for x in str_to_list(perm_row[5])}
        except Exception as e:
            allowed_dev_ids = None
            print(f"str_to_list(perm_row[5]) RAISED: {type(e).__name__}: {e}")
    elif perm_level == "Admin":
        allowed_dev_ids = None
    show("allowed_dev_ids", allowed_dev_ids)
    if allowed_dev_ids == set():
        print("\n*** allowed_dev_ids is an EMPTY SET -> resdev will print")
        print('    "No devices available for your permission level." and return, BEFORE reaching')
        print("    get_devices()'s filtering step or the reserve_device() RPC. ***")

    banner("STEP 6 — get_devices() (read-only RPC; the other half of 'device fields' the question asked about)")
    dev_resp = app_mod.account.get_devices()
    print(f"dev_resp.results type = {type(dev_resp.results).__name__}")
    if "ace" in dev_resp.results:
        show("dev_resp.results['ace']", unmap_arg(dev_resp.results["ace"]))
    else:
        for dev_id, raw_name in dev_resp.results.items():
            show(f"dev_resp.results[{dev_id!r}]  (raw, BEFORE unmap_arg)", raw_name)
            show(f"unmap_arg(dev_resp.results[{dev_id!r}])", unmap_arg(raw_name))

    banner("DONE — reserve_device() was never called. No reservation was made, nothing was mutated.")
    print("Copy everything printed above (from STEP 0 down) and send it back for analysis.")


if __name__ == "__main__":
    main()
