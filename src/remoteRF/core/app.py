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

from remoteRF.core.grpc_client import addr as server_addr, handle_admin_command
from . import *
from ..common.utils import *

import getpass
import os
import datetime
import time
import ast
from pathlib import Path

from prompt_toolkit import PromptSession

from ..deployment.http import AccountBackendError
from remoterf_federation_core import local_capabilities, ValidationError

account = RemoteRFAccount()
session = None

DEFAULT_TOS_URL = "https://remoterf.net/tos"
SERVER_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _clear_terminal() -> None:
    """Clear without forking after gRPC has started background threads."""
    if os.name == "nt":
        os.system("cls")
        print()
        return
    print("\033[2J\033[H")


def _tos_notice_text() -> str:
    notice_path = Path(__file__).resolve().parents[1] / "common" / "tos_notice.txt"
    try:
        text = notice_path.read_text(encoding="utf-8").strip()
    except OSError:
        return f"Terms of Service: {DEFAULT_TOS_URL}"
    return text or f"Terms of Service: {DEFAULT_TOS_URL}"


def _tos_url() -> str:
    for line in reversed(_tos_notice_text().splitlines()):
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        if "terms of service" in label.lower():
            url = value.strip()
            if url:
                return url
    return DEFAULT_TOS_URL


def _parse_reservation_time(value: str) -> datetime.datetime:
    return datetime.datetime.strptime(value, SERVER_TIME_FORMAT)


def _format_clock_12h(value: datetime.datetime) -> str:
    hour = value.hour % 12 or 12
    return f"{hour}:{value.minute:02d} {value.strftime('%p')}"


def _format_reservation_range(start_time: datetime.datetime, end_time: datetime.datetime) -> str:
    start = f"{start_time.strftime('%Y-%m-%d')} {_format_clock_12h(start_time)}"
    end = _format_clock_12h(end_time)
    if start_time.date() != end_time.date():
        end = f"{end_time.strftime('%Y-%m-%d')} {end}"
    return f"{start} - {end}"

# ponytail: bounds mirror Global's policy (6-128 chars); not advertised by discovery.
PASSWORD_MIN, PASSWORD_MAX = 6, 128

def _ask(label, *, hidden=False):
    """One styled account field: indented bold label. Secrets go through
    getpass (tty echo off) because prompt_toolkit's is_password echoes
    plaintext on dumb terminals."""
    if not hidden:
        return session.prompt(stylize("  ", Sty.DEFAULT, f"{label}: ", Sty.BOLD))
    # getpass must write the prompt itself: it flushes pending tty input when
    # it switches echo off, so anything typed against an earlier print is lost.
    return getpass.getpass(f"  \x1b[1m{label}: \x1b[0m")


def _home_name():
    return account.backend.capabilities['display_name'] if account.is_https_home else server_addr


def _password_confirmation(*, enforce_policy=False):
    if enforce_policy:
        printf(f"  Password must be {PASSWORD_MIN}-{PASSWORD_MAX} characters.", Sty.GRAY)
    while True:
        password = _ask("Password (Hidden)", hidden=True)
        if enforce_policy and not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
            printf(f"  Password is {len(password)} characters; must be {PASSWORD_MIN}-{PASSWORD_MAX}. Try again.", Sty.WARNING)
            continue
        if password == _ask("Confirm Password", hidden=True):
            return password
        printf("  Passwords do not match. Try again.", Sty.WARNING)


def _account_command(command):
    policy = account.backend.capabilities['registration_policy'] if account.backend else local_capabilities()['registration_policy']
    if command in ('r', 'register'):
        if not policy['enabled']:
            raise AccountBackendError('Registration is unavailable at this home')
        printf(f"Register at {_home_name()}", (Sty.BOLD, Sty.BLUE))
        account.enrollment_code = _ask('Enrollment Code') if policy['enrollment_code_required'] else ''
        account.username = _ask('Username') if policy['username_required'] else ''
        account.password = _password_confirmation(enforce_policy=account.is_https_home)
        account.email = _ask('Email') if policy['email_required'] else ''
        return bool(account.create_user()) and not policy['email_verification_required']
    if command == 'verify':
        if not policy['email_verification_required']:
            raise AccountBackendError('This deployment does not require email verification')
        printf("Verify your email", (Sty.BOLD, Sty.BLUE))
        printf("  Paste the token from the verification email.", Sty.GRAY)
        account.backend.verify(_ask('Verification token', hidden=True))
        printf('Email verified. You can now log in.', (Sty.BOLD, Sty.GREEN))
        return False
    if command in ('forgot-password', 'reset-password'):
        if not account.is_https_home:
            raise AccountBackendError('Contact the local administrator to reset a password')
        if command == 'forgot-password':
            printf("Forgot password", (Sty.BOLD, Sty.BLUE))
            account.backend.forgot(_ask('Email'))
            printf('If eligible, check your email for a password reset token.', Sty.DEFAULT)
        else:
            printf("Reset password", (Sty.BOLD, Sty.BLUE))
            token = _ask('Password reset token', hidden=True)
            account.backend.reset(token, _password_confirmation(enforce_policy=True))
            printf('Password reset. Log in again.', (Sty.BOLD, Sty.GREEN))
        return False
    if command in ('l', 'login'):
        printf(f"Login to {_home_name()}", (Sty.BOLD, Sty.BLUE))
        account.username = _ask('Username or Email' if account.is_https_home else 'Username')
        account.password = _ask('Password (Hidden)', hidden=True)
        return bool(account.login_user())
    printf('Choose login or register' + (', verify, forgot-password or reset-password.' if account.is_https_home else '.'), Sty.WARNING)
    return False


def welcome(*, show_banner: bool = True, initial=()):
    """Authenticate. ``initial`` commands run first without prompting (``-l`` →
    login, ``-r`` → register/verify/login); any failure falls back to the prompt."""
    if show_banner:
        print_client_banner(print_my_version(), server=server_addr)
    if account.is_https_home and 'register' not in initial:
        try:
            if account.backend.resume():
                account.username = account.backend.credentials['username']
                return True
        except (AccountBackendError, ValidationError):
            account.backend.credentials = None
            account.backend.store.clear()
            printf('Stored session is unavailable. Log in again.', Sty.WARNING)
    queue = list(initial)
    while True:
        try:
            if queue:
                command = queue.pop(0)
            else:
                prompt = 'Please login or register to continue. (l/r): '
                if account.is_https_home:
                    prompt = 'Choose register, verify, login, forgot-password or reset-password: '
                command = session.prompt(stylize(prompt, Sty.DEFAULT)).strip().lower()
            if command in ('exit', 'quit'):
                return False
            if _account_command(command):
                return True
        except (AccountBackendError, ValidationError) as exc:
            printf(f'Account error: {exc}', Sty.WARNING)
            queue.clear()
        except (KeyboardInterrupt, EOFError):
            return False

def title():
    print_internal_banner(
        print_my_version(),
        server=server_addr,
        tos_url=_tos_url(),
    )
    printf("Input ", Sty.DEFAULT, "'help' ", Sty.BRIGHT_GREEN, "for a list of available commands.", Sty.DEFAULT)

def commands():
    printf("Commands:", (Sty.BOLD, Sty.BLUE))
    printf("'help' or 'h' ", Sty.MAGENTA, "   : ", Sty.GRAY, "Show this help message", Sty.DEFAULT)
    printf("'clear' ", Sty.MAGENTA, "         : ", Sty.GRAY, "Clear terminal", Sty.DEFAULT)
    printf("'getdev' ", Sty.MAGENTA, "        : ", Sty.GRAY, "View devices", Sty.DEFAULT)
    printf("'resdev' ", Sty.MAGENTA, "        : ", Sty.GRAY, "Reserve a device", Sty.DEFAULT)
    printf("'cancelres' ", Sty.MAGENTA, "     : ", Sty.GRAY, "Cancel a reservation", Sty.DEFAULT)
    printf("'getres' ", Sty.MAGENTA, "        : ", Sty.GRAY, "View all reservations", Sty.DEFAULT)
    printf("'myres' ", Sty.MAGENTA, "         : ", Sty.GRAY, "View my reservations", Sty.DEFAULT)
    printf("'perms' ", Sty.MAGENTA, "         : ", Sty.GRAY, "View permissions", Sty.DEFAULT)
    printf("'enroll' ", Sty.MAGENTA, "        : ", Sty.GRAY, "Enroll with an enrollment code", Sty.DEFAULT)
    printf("'logout' ", Sty.MAGENTA, "        : ", Sty.GRAY, "Log out" + (" and forget the stored session" if account.is_https_home else ""), Sty.DEFAULT)
    if account.is_https_home:
        printf("'reset-password' ", Sty.MAGENTA, ": ", Sty.GRAY, "Set a new password with an emailed token", Sty.DEFAULT)
    printf("'exit' or 'quit' ", Sty.MAGENTA, ": ", Sty.GRAY, "Exit", Sty.DEFAULT)
    # printf("'resdev -n' ", Sty.MAGENTA, "- naive reserve device", Sty.DEFAULT)
    # printf("'resdev s' ", Sty.MAGENTA, "- Reserve a Device (by single date)", Sty.DEFAULT)
    
    # if account.is_admin:
    #     print()
    #     printf("Admin Commands:", Sty.BOLD)
    #     printf("'admin printa' ", Sty.MAGENTA, " : Print all accounts", Sty.DEFAULT)
    #     printf("'admin printr' ", Sty.MAGENTA, " : Print all reservations", Sty.DEFAULT)
    #     printf("'admin printp' ", Sty.MAGENTA, " : Print all perms", Sty.DEFAULT)
    #     printf("'admin printd' ", Sty.MAGENTA, " : Print all devices", Sty.DEFAULT)
    #     printf("'admin rm a <username>' ", Sty.MAGENTA, " : Remove one account", Sty.DEFAULT)
    #     printf("'admin rm aa' ", Sty.MAGENTA, " : Remove all accounts", Sty.DEFAULT)
    #     printf("'admin rm ar' ", Sty.MAGENTA, " : Remove all reservations", Sty.DEFAULT)
    #     # If you expose set_account remotely:
    #     printf("'admin setacc <username> <U|P|A> [args...]' ", Sty.MAGENTA, " : Set perms", Sty.DEFAULT)
    
    
def clear():
    _clear_terminal()
    title()
    
def print_my_version():
    import sys
    latest = newest_version_pip("remoterf")
    try:
        import importlib.metadata as md  # Py3.8+
        top = __name__.split('.')[0]
        # Try mapping package → distribution (Py3.10+); fall back to same name.
        for dist in getattr(md, "packages_distributions", lambda: {})().get(top, []):
            installed = md.version(dist)
            if latest is None:
                return installed
            if latest == installed:
                return f"{installed} (latest)"
            return f"{installed} (OUTDATED)"
        return md.version(top)
    except Exception:
        # Last resort: __version__ attribute if you define it.
        return getattr(sys.modules.get(__name__.split('.')[0]), "__version__", "unknown")

def newest_version_pip(project="remoterf"):
    """Fetch the published version without spawning a process beside gRPC."""
    import json
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    try:
        request = Request(
            f"https://pypi.org/pypi/{quote(project)}/json",
            headers={"User-Agent": "RemoteRF-Client"},
        )
        with urlopen(request, timeout=2.0) as response:
            payload = json.load(response)
        return payload.get("info", {}).get("version")
    except (OSError, ValueError, TypeError):
        return None

    
# ── HTTPS (federated) home rendering ─────────────────────────────────
#
# At a Global HOME every device and reservation is an opaque
# ``<deployment_id>:<local_id>`` reference minted by the HOME.  Each
# destination's provenance is shown so an unreachable deployment is never
# mistaken for an empty one.

def _render_destinations(entries):
    entries = [
        dest for dest in entries
        if dest["status"] != "ok"
        # Not enrolled there yet: normal for every contract partner, not a fault.
        # `perms` still shows it; transport/other blocks stay visible here.
        and not (dest["status"] == "blocked" and dest["provenance"] == "destination_signed" and dest["error_code"] == "principal_unknown")
    ]
    for dest in entries:
        reason = "unavailable (transport, unsigned)" if dest["status"] == "unavailable" else f"blocked ({dest['provenance']}: {dest['error_code']})"
        printf("Deployment ", Sty.GRAY, f"{dest['display_name']}", Sty.MAGENTA, f": {reason}", Sty.WARNING)
    if any(dest["status"] != "ok" for dest in entries):
        print()


def _federated_devices():
    data = account.get_devices()
    _render_destinations(data["destinations"])
    if not data["devices"]:
        printf("No devices visible through this home.", Sty.BOLD)
        return data
    # Grouped by deployment; the opaque <deployment_uuid>:<id> reference is
    # never shown, `resdev` picks by index like the native shell.
    data["devices"].sort(key=lambda d: (d["deployment_name"], d["display_name"], d["local_device_id"]))
    printf("Devices:", (Sty.BOLD, Sty.BLUE))
    deployment = None
    for dev in data["devices"]:
        if dev["deployment_name"] != deployment:
            deployment = dev["deployment_name"]
            printf(f"  @{deployment}", (Sty.BOLD, Sty.MAGENTA))
        state = "online" if dev["online"] else "offline"
        printf("    Device Name: ", Sty.GRAY, f"{dev['display_name']}", Sty.DEFAULT, f" ({dev['device_type'] or 'unknown'}, {state})", Sty.GRAY)
    if data.get("incomplete"):
        printf(f"{data['omitted_count']} more deployment(s) were not queried.", Sty.WARNING)
    return data


def _federated_reservation_rows(*, mine_only):
    """HOME reservation rows with a native-style device label, sorted like native."""
    data = account.get_reservations()
    _render_destinations(data["destinations"])
    names = {d["deployment_id"]: d["display_name"] for d in data["destinations"]}
    rows = [r for r in data["reservations"] if r["mine"] or not mine_only]
    for r in rows:
        r["device_label"] = f"{r['device_id'].rpartition(':')[2]} @{names.get(r['deployment_id'], r['deployment_id'])}"
        r["start"], r["end"] = datetime.datetime.fromtimestamp(r["start_time"]), datetime.datetime.fromtimestamp(r["end_time"])
    rows.sort(key=lambda r: (r["device_label"], r["start"]))
    return rows


def _federated_reservations(*, mine_only):
    rows = _federated_reservation_rows(mine_only=mine_only)
    if not rows:
        printf("No reservations found.", Sty.BOLD)
        return rows
    if mine_only:
        printf("Current Reservations Held By ", (Sty.BOLD, Sty.BLUE), f'{account.username}:', Sty.MAGENTA)
    else:
        printf("Reservations:", (Sty.BOLD, Sty.BLUE))
    for r in rows:
        printf("Device ID: ", Sty.GRAY, f"{r['device_label']}", Sty.MAGENTA, ", Time: ", Sty.GRAY, _format_reservation_range(r["start"], r["end"]), Sty.CYAN)
    return rows


def _federated_cancel():
    rows = _federated_reservation_rows(mine_only=True)
    printf("Current Reservation(s) under ", (Sty.BOLD, Sty.BLUE), f'{account.username}:', Sty.MAGENTA)
    for i, r in enumerate(rows):
        printf("Reservation ID: ", Sty.GRAY, f'{i}', Sty.CYAN, " Device ID: ", Sty.GRAY, f"{r['device_label']}", Sty.MAGENTA, " Time: ", Sty.GRAY, _format_reservation_range(r["start"], r["end"]), Sty.CYAN)
    if not rows:
        printf("No reservations found.", Sty.BOLD)
        return
    inpu = session.prompt(stylize(
        "Enter the reservation ID you would like to cancel ", Sty.BOLD,
        "(abort with non-number input)", Sty.CYAN,
        ": ", Sty.BOLD,
    ))
    if not inpu.isdigit():
        print("Aborting. A non integer key was given.")
        return
    id = int(inpu)
    if id >= len(rows):
        print("Invalid ID.")
        return
    r = rows[id]
    if session.prompt(stylize(
        "Cancel reservation ID ", Sty.DEFAULT,
        f'{id}', Sty.CYAN,
        " Device ID: ", Sty.DEFAULT,
        f"{r['device_label']}", Sty.MAGENTA,
        " Time: ", Sty.GRAY,
        _format_reservation_range(r["start"], r["end"]), Sty.CYAN,
        "?", Sty.CYAN,
        " (y/n): ", (Sty.BOLD, Sty.GREEN),
    )) != 'y':
        printf("Aborting. User canceled action.", Sty.WARNING)
        return
    account.cancel_reservation(r["reservation_id"])
    printf("Reservation ID ", Sty.DEFAULT, f'{id}', Sty.CYAN, " successfully canceled.", (Sty.BOLD, Sty.GREEN))


def _federated_reservations_for_range(start_day, end_day):
    """Occupancy keyed like fetch_reservations_for_range, from the HOME's getres."""
    res_dict = {}
    for r in account.get_reservations()["reservations"]:
        start, end = datetime.datetime.fromtimestamp(r["start_time"]), datetime.datetime.fromtimestamp(r["end_time"])
        if start_day <= start.date() <= end_day:
            res_dict.setdefault((r["device_id"], start.date()), []).append((start, end))
    return res_dict


def _federated_reserve_slot(device_id, chosen_day, chosen_slot, slot_start_str, slot_end_str):
    handle = account.reserve_device(device_id, chosen_slot[0], chosen_slot[1])
    printf("Reservation successful for ", (Sty.BOLD, Sty.GREEN), f"{chosen_day.strftime('%Y-%m-%d')} {slot_start_str}-{slot_end_str}.", Sty.CYAN)
    printf("Your device handle -> ", Sty.BOLD, f"{handle}", (Sty.BOLD, Sty.GREEN))
    printf("Use it where a token goes, e.g. adi.Pluto(\"" + handle + "\"); it works while you are logged in to this home.", Sty.DEFAULT)


def _federated_reserve():
    """Same flow as the native `resdev` picker, sourced from the HOME's device rows."""
    data = account.get_devices()
    _render_destinations(data["destinations"])
    devs = sorted(data["devices"], key=lambda d: (d["deployment_name"], d["display_name"], d["local_device_id"]))
    if not devs:
        printf("No devices available for your permission level.", Sty.WARNING)
        return
    printf("Devices:", Sty.BOLD)
    deployment = None
    for idx, dev in enumerate(devs):
        if dev["deployment_name"] != deployment:
            deployment = dev["deployment_name"]
            printf(f"  @{deployment}", (Sty.BOLD, Sty.MAGENTA))
        dev["block_min"] = _auto_block_minutes(dev["max_reservation_time_sec"] or 0)
        printf(
            f"{idx}.", Sty.CYAN,
            "  Name: ", Sty.DEFAULT,
            f"{dev['display_name']}", Sty.GRAY,
            " Duration: ", Sty.DEFAULT,
            f"{dev['block_min']} min", Sty.GREEN,
        )
    sel = session.prompt(stylize(
        "Enter which device you want to reserve ", (Sty.BOLD, Sty.GREEN),
        "(enter the 0-based index): ", Sty.CYAN,
    )).strip()
    if not sel.isdigit():
        print("Invalid input. Please enter a number.")
        return
    if int(sel) >= len(devs):
        print("Invalid selection.")
        return
    dev = devs[int(sel)]
    if dev["block_min"] < 10:
        printf(
            "Your max reservation duration for device ", Sty.DEFAULT,
            f"{dev['display_name']}", Sty.MAGENTA,
            f" is < 10 minutes (max_sec={dev['max_reservation_time_sec'] or 0}). Cannot create valid reservations.", Sty.DEFAULT,
        )
        return
    _pick_slot_and_reserve(dev["device_id"], dev["display_name"], dev["block_min"], _federated_reservations_for_range, _federated_reserve_slot,
                           device_label=f"{dev['display_name']} @{dev['deployment_name']}")


def reservations():
    if account.is_https_home:
        _federated_reservations(mine_only=False)
        return
    data = account.get_reservations()
    if 'ace' in data.results:
        print(f"Error: {unmap_arg(data.results['ace'])}")
        return
    entries = []

    for key, value in data.results.items():
        parts = unmap_arg(value).split(',')
        # Create a dictionary for each entry with named fields
        entry = {
            'username': parts[0],
            'device_id': int(parts[1]),  # Convert device_id to integer for proper numerical sorting
            'start_time': _parse_reservation_time(parts[2]),  # Convert start_time to datetime
            'end_time': _parse_reservation_time(parts[3])
        }
        entries.append(entry)
        
    if (entries == []):
        printf("No reservations found.", Sty.BOLD)
        return
    
    printf("Reservations:", (Sty.BOLD, Sty.BLUE))

    # Sort the entries by device_id and then by start_time
    sorted_entries = sorted(entries, key=lambda x: (x['device_id'], x['start_time']))

    # Format the sorted entries into strings
    for entry in sorted_entries:
        printf("Device ID: ", Sty.GRAY, f'{entry["device_id"]}', Sty.MAGENTA, ", Time: ", Sty.GRAY, _format_reservation_range(entry["start_time"], entry["end_time"]), Sty.CYAN)
        
def my_reservations():
    if account.is_https_home:
        _federated_reservations(mine_only=True)
        return
    data = account.get_reservations()
    if 'ace' in data.results:
        print(f"Error: {unmap_arg(data.results['ace'])}")
        return
    entries = []

    for key, value in data.results.items():
        parts = unmap_arg(value).split(',')
        # Create a dictionary for each entry with named fields
        entry = {
            'username': parts[0],
            'device_id': int(parts[1]),  # Convert device_id to integer for proper numerical sorting
            'start_time': _parse_reservation_time(parts[2]),  # Convert start_time to datetime
            'end_time': _parse_reservation_time(parts[3])
        }
        entries.append(entry)
        
    if (entries == []):
        printf("No reservations found.", Sty.BOLD)
        return
    
    printf("Current Reservations Held By ", (Sty.BOLD, Sty.BLUE), f'{account.username}:', Sty.MAGENTA)

    # Sort the entries by device_id and then by start_time
    sorted_entries = sorted(entries, key=lambda x: (x['device_id'], x['start_time']))
    
    for entry in sorted_entries:
        if account.username == entry['username']:
            printf("Device ID: ", Sty.GRAY, f'{entry["device_id"]}', Sty.MAGENTA, ", Time: ", Sty.GRAY, _format_reservation_range(entry["start_time"], entry["end_time"]), Sty.CYAN)

def cancel_my_reservation():
    if account.is_https_home:
        _federated_cancel()
        return
    ## print all of ur reservations and their ids
    ## ask for id to cancel
    ## remove said reservation
    data = account.get_reservations()
    if 'ace' in data.results:
        print(f"Error: {unmap_arg(data.results['ace'])}")
        return
    
    entries:list = []

    for key, value in data.results.items():
        parts = unmap_arg(value).split(',')
        # Create a dictionary for each entry with named fields
        entry = {
            'id': -1,
            'internal_id': key,
            'username': parts[0],
            'device_id': int(parts[1]),  # Convert device_id to integer for proper numerical sorting
            'start_time': _parse_reservation_time(parts[2]),  # Convert start_time to datetime
            'end_time': _parse_reservation_time(parts[3])
        }
        if account.username == entry['username']:
            entries.append(entry)
    
    printf("Current Reservation(s) under ", (Sty.BOLD, Sty.BLUE), f'{account.username}:', Sty.MAGENTA)
    
    sorted_entries = sorted(entries, key=lambda x: (x['device_id'], x['start_time'])) # sort by device_id and start_time
    for i, entry in enumerate(sorted_entries):  # label all reservations with unique id
        entry['id'] = i
        printf("Reservation ID: ", Sty.GRAY, f'{i}', Sty.CYAN, " Device ID: ", Sty.GRAY, f'{entry["device_id"]}', Sty.MAGENTA, " Time: ", Sty.GRAY, _format_reservation_range(entry["start_time"], entry["end_time"]), Sty.CYAN)
        # print(f"Reservation ID {i}, Device ID: {entry['device_id']}, Time: {_format_reservation_range(entry['start_time'], entry['end_time'])}")
        
    if sorted_entries == []:
        printf("No reservations found.", Sty.BOLD)
        return    
        
    inpu = session.prompt(stylize(
        "Enter the reservation ID you would like to cancel ", Sty.BOLD,
        "(abort with non-number input)", Sty.CYAN,
        ": ", Sty.BOLD,
    ))
    
    if inpu.isdigit():
        id = int(inpu)
        if id >= len(sorted_entries):
            print("Invalid ID.")
            return
        
        # grab the reservation
        for entry in sorted_entries:
            if entry['id'] == id:
                db_id = entry['internal_id']
                if session.prompt(stylize(
                    "Cancel reservation ID ", Sty.DEFAULT,
                    f'{id}', Sty.CYAN,
                    " Device ID: ", Sty.DEFAULT,
                    f'{entry["device_id"]}', Sty.MAGENTA,
                    " Time: ", Sty.GRAY,
                    _format_reservation_range(entry["start_time"], entry["end_time"]), Sty.CYAN,
                    "?", Sty.CYAN,
                    " (y/n): ", (Sty.BOLD, Sty.GREEN),
                )) == 'y':
                    response = account.cancel_reservation(db_id)
                    if 'ace' in response.results:
                        print(f"Error: {unmap_arg(response.results['ace'])}")
                    elif 'UC' in response.results:
                        printf("Reservation ID ", Sty.DEFAULT, f'{id}', Sty.CYAN, " successfully canceled.", (Sty.BOLD, Sty.GREEN))
                else:
                    printf("Aborting. User canceled action.", Sty.WARNING)
                return
            
        print(f"Error: No reservation found with ID {id}.")
    else:
        print("Aborting. A non integer key was given.")

def devices():
    if account.is_https_home:
        _federated_devices()
        return
    data = account.get_devices()
    if 'ace' in data.results:
        print(f"Error: {unmap_arg(data.results['ace'])}")
        return
    printf("Devices:", (Sty.BOLD, Sty.BLUE))
    
    for key in sorted(data.results, key=int):
        printf("Device ID: ", Sty.GRAY, f'{key}', Sty.MAGENTA, " Device Name: ", Sty.GRAY, f"{unmap_arg(data.results[key])}", Sty.DEFAULT)
        

def get_datetime(question:str):
    timestamp = session.prompt(stylize(f'{question}', Sty.DEFAULT, ' (YYYY-MM-DD HH:MM): ', Sty.GRAY))
    return _parse_reservation_time(timestamp + ':00')

def reserve():
    try:
        if account.is_https_home:
            _federated_reserve()
            return
        id = session.prompt(stylize("Enter the device ID you would like to reserve: ", Sty.DEFAULT))
        token = account.reserve_device(int(id), get_datetime("Reserve Start Time"), get_datetime("Reserve End Time"))
        if token != '':
            printf(f"Reservation successful. Your Token -> ", Sty.BOLD, f"{token}", Sty.BG_GREEN)
            printf(f"Please keep this token safe, as it is not saved on the server and cannot be retrieved again. If you lose it, cancel your reservation and make a new one. ", Sty.DEFAULT)
    except Exception as e:
        printf(f"Error: {e}", Sty.BRIGHT_RED)

import ast
import json


def _render_federation_permissions(entries):
    """Render typed remote snapshots without upgrading transport text to fact."""

    if not entries:
        return
    printf("Federated Permissions:", (Sty.BOLD, Sty.BLUE))
    friendly_errors = {
        "principal_disabled": "principal disabled",
        "group_lifetime_invalid": "destination group data is invalid",
        "contract_not_active": "contract is not active",
        "contract_selection_ambiguous": "contract selection is ambiguous",
    }
    for entry in entries:
        print(f"  {entry['display_name']}")
        print(
            "    Contract: "
            f"{entry['contract_id']} v{entry['contract_version']} ({entry['state']})"
        )
        status = entry["status"]
        print(f"    Status: {status}")
        if status == "ok":
            groups = ", ".join(g["group_name"] for g in entry["groups"])
            print(f"    Groups: {groups or '(none)'}")
            checked = datetime.datetime.fromtimestamp(
                entry["retrieved_at"], tz=datetime.timezone.utc
            ).isoformat().replace("+00:00", "Z")
            print(f"    Source: {entry['display_name']} (destination-signed)")
            print(f"    Checked: {checked}")
        elif status == "blocked":
            reason = friendly_errors.get(
                entry["error_code"], entry["error_code"].replace("_", " ")
            )
            source = (
                f"{entry['display_name']} (destination-signed)"
                if entry["provenance"] == "destination_signed"
                else "local HOME policy"
            )
            print(f"    Reason: {reason}")
            print(f"    Source: {source}")
        else:
            print("    Reason: destination unavailable")
            print("    Source: unsigned transport status")


def perms():
    data = account.get_perms()
    if account.is_https_home:
        local = data.get("local") or {
            "display_name": account.backend.capabilities["display_name"],
            "groups": data["groups"],
        }
        print(local["display_name"])
        print(
            "  Groups: "
            + (", ".join(g["group_name"] for g in local["groups"]) or "(none)")
        )
        _render_federation_permissions(data.get("federation", []))
        if data.get("incomplete"):
            print(
                f"  Federation summary incomplete: {data.get('omitted_count', 0)} "
                "contracted destination(s) omitted by the HOME limit"
            )
        return
    if 'ace' in data.results:
        print(f"Error: {unmap_arg(data.results['ace'])}")
        return

    results = ast.literal_eval(unmap_arg(data.results['UC']))[0]
    perm_level = results[0]

    details_raw = unmap_arg(data.results.get("details", map_arg("{}")))
    try:
        details = json.loads(details_raw) if details_raw else {}
    except Exception:
        details = {}
    remote_permissions = []
    remote_permissions_incomplete = 0
    additive = details.get("home_permissions")
    if additive is not None:
        try:
            from remoterf_federation_core import validate_home_permissions_summary

            checked = validate_home_permissions_summary(additive)
            capabilities = getattr(getattr(account, "backend", None), "capabilities", {})
            advertised = (
                capabilities.get("deployment_id")
                if isinstance(capabilities, dict)
                else None
            )
            if advertised is not None and checked["local"]["deployment_id"] != advertised:
                raise ValueError("HOME identity mismatch")
            remote_permissions = checked["federation"]
            remote_permissions_incomplete = checked["omitted_count"]
        except (TypeError, ValueError):
            # The additive section is untrusted even over GenericRPC.  Keep
            # rendering the protected legacy permission fields and ignore only
            # malformed federation data.
            remote_permissions = []
            remote_permissions_incomplete = 0

    def render_remote_permissions():
        _render_federation_permissions(remote_permissions)
        if remote_permissions_incomplete:
            print(
                f"Federation summary incomplete: {remote_permissions_incomplete} "
                "destination(s) omitted"
            )

    printf("Permission Level: ", (Sty.BOLD, Sty.BLUE), f"{perm_level}", Sty.MAGENTA)

    if perm_level == "Normal User":
        devices = details.get("devices", []) or []
        caps = details.get("caps", {}) or {}
        groups = details.get("groups", []) or []

        def _cap_for(dev_id: int):
            return caps.get(str(dev_id)) or caps.get(dev_id) or {}

        # ---- Groups (NEW) ----
        if groups:
            # keep stable ordering
            groups = [str(g) for g in groups if str(g).strip() != ""]
            printf("User Groups:", (Sty.BOLD, Sty.BLUE))
            for g in groups:
                printf("  - ", Sty.CYAN, f"{g}", Sty.MAGENTA)
        else:
            printf("User Groups: ", (Sty.BOLD, Sty.BLUE), "(none)", Sty.GRAY)

        # ---- Devices ----
        if not devices:
            printf("Devices: ", Sty.DEFAULT, "None", Sty.MAGENTA)
            render_remote_permissions()
            return

        printf("Accessible Devices: ", (Sty.BOLD, Sty.BLUE), f"{devices}", Sty.MAGENTA)

        # Build per-device caps and group identical limits together
        buckets: dict[tuple[int, int], list[int]] = {}  # (max_r, max_t_sec) -> [dev_ids]
        for d in devices:
            try:
                did = int(d)
            except Exception:
                continue
            c = _cap_for(did)
            max_t = int(c.get("max_reservation_time_sec", 0) or 0)
            max_r = int(c.get("max_reservations", 0) or 0)
            buckets.setdefault((max_r, max_t), []).append(did)

        if not buckets:
            print("Limits per device: (none)")
            render_remote_permissions()
            return

        # If everything shares the same limits, print once
        if len(buckets) == 1:
            (max_r, max_t), _devs = next(iter(buckets.items()))
            printf("Permissions:", (Sty.BOLD, Sty.BLUE))
            printf("  Max Reservations: ", Sty.GRAY, f"{max_r}", Sty.CYAN)
            printf("  Reservation Duration (min): ", Sty.GRAY, f"{max_t // 60}", Sty.CYAN)
            render_remote_permissions()
            return

        # Otherwise print grouped limits
        printf("Permissions per device (grouped):", (Sty.BOLD, Sty.BLUE))
        for (max_r, max_t), devs in sorted(buckets.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[1])):
            devs = sorted(devs)

            # compress ranges like 0-3,5,7-9
            ranges = []
            start = prev = None
            for x in devs:
                if start is None:
                    start = prev = x
                    continue
                if x == prev + 1:
                    prev = x
                else:
                    ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
                    start = prev = x
            if start is not None:
                ranges.append(f"{start}-{prev}" if start != prev else f"{start}")

            dev_str = ",".join(ranges)
            printf("  devices[", Sty.GRAY, f"{dev_str}", Sty.MAGENTA, "]: ", Sty.GRAY, f"max_reservations={max_r}, max_time_min={max_t // 60}", Sty.CYAN)

        render_remote_permissions()

    elif perm_level == "Power User":
        printf("Max Reservations: ", (Sty.BOLD, Sty.BLUE), f"{results[3]}", Sty.CYAN)
        printf("Max Reservation Duration (min): ", (Sty.BOLD, Sty.BLUE), f"{int(results[4]/60)}", Sty.CYAN)
        printf("Device IDs allowed Access to: ", (Sty.BOLD, Sty.BLUE), f"{results[5]}", Sty.MAGENTA)
        render_remote_permissions()

    elif perm_level == "Admin":
        printf("No restrictions on reservation count or duration.", (Sty.BOLD, Sty.GREEN))
        render_remote_permissions()

    else:
        printf(f"Error: Unknown permission level {perm_level}", Sty.BRIGHT_RED)

        
def enroll(code=None):
    code = code or session.prompt(stylize("Enter your enrollment code: ", Sty.DEFAULT))
    account.enrollment_code = code
    data = account.set_enroll()

    if account.is_https_home:
        if data.get("status") != "enrolled":
            print("Error: enrollment_not_confirmed")
            return False
    elif 'ace' in data.results:
        provenance = (
            unmap_arg(data.results['federation_provenance'])
            if 'federation_provenance' in data.results else None
        )
        label = f" [{provenance}]" if provenance else ""
        print(f"Error{label}: {unmap_arg(data.results['ace'])}")
        return False
    elif 'UE' in data.results:
        provenance = (
            unmap_arg(data.results['federation_provenance'])
            if 'federation_provenance' in data.results else None
        )
        label = f" [{provenance}]" if provenance else ""
        print(f"Error{label}: {unmap_arg(data.results['UE'])}")
        return False
    printf("Enrollment successful.", Sty.BG_GREEN)
    return True

# New block scheduling

def fetch_all_reservations():
    data = account.get_reservations()
    if 'ace' in data.results:
        print(f"Error: {unmap_arg(data.results['ace'])}")
        return []
    entries = []

    for key, value in data.results.items():
        parts = unmap_arg(value).split(',')
        # Convert both start and end times to datetime objects.
        entry = {
            'username': parts[0],
            'device_id': int(parts[1]),  # Stored as an int
            'start_time': _parse_reservation_time(parts[2]),
            'end_time': _parse_reservation_time(parts[3])
        }
        entries.append(entry)
    return entries

def fetch_reservations_for_range(start_day: datetime.date, end_day: datetime.date):
    all_res = fetch_all_reservations()  # This calls the network only once.
    res_dict = {}
    for res in all_res:
        res_day = res['start_time'].date()
        if start_day <= res_day <= end_day:
            key = (str(res['device_id']), res_day)
            res_dict.setdefault(key, []).append((res['start_time'], res['end_time']))
    return res_dict

def is_slot_conflicting(slot: tuple, reservations: list):
    slot_start, slot_end = slot
    for res_start, res_end in reservations:
        if slot_start < res_end and slot_end > res_start:
            return True
    return False

def interactive_reserve_next_days(block_minutes=60):
    try:
        # --- 1) Fetch and display all devices ---
        data = account.get_devices()
        if 'ace' in data.results:
            print(f"Error: {unmap_arg(data.results['ace'])}")
            return
        
        print("Devices:")
        # Sort devices by integer key
        sorted_device_ids = sorted(data.results.keys(), key=int)
        
        for idx, dev_id in enumerate(sorted_device_ids):
            dev_name = unmap_arg(data.results[dev_id])
            print(f"{idx}. Device ID: {dev_id}   Name: {dev_name}")
        
        # --- 2) Prompt user to pick a device by 0-based index ---
        device_selection = input("Enter which device you want to reserve  (enter the 0-based index): ")
        try:
            device_selection = int(device_selection)
            if device_selection < 0 or device_selection >= len(sorted_device_ids):
                print("Invalid selection.")
                return
        except ValueError:
            print("Invalid input. Please enter a number.")
            return
        
        chosen_device_id = sorted_device_ids[device_selection]
        
        # --- 3) Prompt user for the number of days ---
        num_days = int(input("Enter the number of days to check for available reservations (starting today): "))
        
        # --- 4) Optionally override block_minutes ---
        # user_block_input = input(f"Enter block duration in minutes (e.g. 15, 30, 60, 120). Press Enter to default ({block_minutes}): ").strip()
        # if user_block_input:
        #     try:
        #         block_minutes = int(user_block_input)
        #     except ValueError:
        #         print("Invalid block duration. Using default of 60 minutes.")
        #         block_minutes = 60
        
        # --- 5) Find all free time slots for the chosen device over the next `num_days` days ---
        
        today = datetime.date.today()
        end_day = today + datetime.timedelta(days=num_days - 1)
        
        # We only need one call to fetch reservations for the range:
        reservations_range = fetch_reservations_for_range(today, end_day)
        
        # We'll keep a list of (day, (slot_start, slot_end)) for which the device is free
        available_slots = []
        
        now = datetime.datetime.now()
        
        # Helper to build time slots of length 'block_minutes' starting at 00:00 up to 24:00
        def build_time_slots(date: datetime.date, block_size: int):
            slots = []
            start_of_day = datetime.datetime.combine(date, datetime.time(0, 0))
            minutes_in_day = 24 * 60  # 1440
            current_offset = 0
            while current_offset < minutes_in_day:
                slot_start = start_of_day + datetime.timedelta(minutes=current_offset)
                slot_end   = slot_start + datetime.timedelta(minutes=block_size)
                # Stop if slot_end bleeds into the next calendar day
                if slot_end.date() != date and slot_end.time() != datetime.time.min:
                    break
                slots.append((slot_start, slot_end))
                current_offset += block_size
            return slots
        
        # Build free slots for each day in [today, end_day]
        for i in range(num_days):
            day = today + datetime.timedelta(days=i)
            all_slots = build_time_slots(day, block_minutes)
            
            # The reservations for the chosen device on this day:
            key = (str(chosen_device_id), day)
            day_reservations = reservations_range.get(key, [])
            
            for slot in all_slots:
                slot_start, slot_end = slot
                # Skip if it's for "today" and the slot ends in the past
                if day == today and slot_end <= now:
                    continue
                
                # Check for conflict
                if not is_slot_conflicting(slot, day_reservations):
                    available_slots.append((day, slot))
        
        if not available_slots:
            printf("No available time slots for device ", Sty.BOLD, f"{chosen_device_id}", Sty.MAGENTA, f" in the next {num_days} days.", Sty.DEFAULT)
            return
        
        # Sort by day, then by slot start time
        available_slots.sort(key=lambda x: (x[0], x[1][0]))
        
        # --- Display the available slots, using 0-based index ---
        print()
        printf("Available time slots for device ", Sty.BOLD, f"{chosen_device_id}", Sty.MAGENTA, f" over the next {num_days} days:", Sty.DEFAULT)
        last_day = None
        for idx, (day, slot) in enumerate(available_slots):
            slot_start_str = slot[0].strftime('%I:%M %p')
            slot_end_str   = slot[1].strftime('%I:%M %p')
            if day != last_day:
                # Print a header for the day
                day_header = f"{day.strftime('%Y-%m-%d')} ({day.strftime('%a')}) {day.strftime('%b')}. {day.day}"
                print()
                printf(day_header, (Sty.BOLD, Sty.BLUE))
                last_day = day
            
            printf("  ", Sty.DEFAULT, f"{idx}.", Sty.CYAN, f" {slot_start_str} - {slot_end_str}", Sty.DEFAULT)
        
        # Prompt user to pick a slot by 0-based index
        selection = session.prompt(stylize("Select a slot by index: ", (Sty.BOLD, Sty.GREEN)))
        try:
            selection = int(selection)
            if selection < 0 or selection >= len(available_slots):
                print("Invalid selection.")
                return
        except ValueError:
            print("Invalid input. Please enter a number.")
            return
        
        chosen_day, chosen_slot = available_slots[selection]
        slot_start_str = chosen_slot[0].strftime('%I:%M %p')
        slot_end_str   = chosen_slot[1].strftime('%I:%M %p')
        
        confirmation = session.prompt(stylize(
            "Reservation successful on ", Sty.DEFAULT,
            f"{chosen_day.strftime('%Y-%m-%d')}", (Sty.BOLD, Sty.BLUE),
            " from ", Sty.DEFAULT,
            f"{slot_start_str}", Sty.CYAN,
            " to ", Sty.DEFAULT,
            f"{slot_end_str}", Sty.CYAN,
            " on device ", Sty.DEFAULT,
            f"{chosen_device_id}", Sty.MAGENTA,
            ". Confirm reservation? (y/n): ", (Sty.BOLD, Sty.GREEN),
        )).strip().lower()
        
        if confirmation != 'y':
            printf("Reservation cancelled.", Sty.WARNING)
            return
        
        # print(f"device_id : {chosen_device_id}, start_time : {chosen_slot[0]}, end_time : {chosen_slot[1]}")
        
        # --- 6) Reserve the chosen slot on the chosen device ---
        token = account.reserve_device(int(chosen_device_id), chosen_slot[0], chosen_slot[1])
        if token:
            printf("Reservation successful on device ", (Sty.BOLD, Sty.GREEN), f"{chosen_device_id}", Sty.MAGENTA, " for ", Sty.DEFAULT, f"{chosen_day.strftime('%Y-%m-%d')} {slot_start_str}-{slot_end_str}.", Sty.CYAN)
            printf("Your Token -> ", Sty.BOLD, f"{token}", (Sty.BOLD, Sty.GREEN))
            printf("Please keep this token safe, as it is not saved on the server and cannot be retrieved again. If you lose it, cancel your reservation and make a new one.", Sty.DEFAULT)
        
    except Exception as e:
        print(f"Error: {e}")
        
def _auto_block_minutes(max_t_sec: int) -> int:
    try:
        max_t_sec = int(max_t_sec)
    except Exception:
        return 30

    if max_t_sec <= 0:
        return 30

    max_min = max_t_sec // 60
    if max_min < 10:
        # Can't make a valid reservation at all (server min = 10 minutes).
        return max_min

    # Pick the largest "nice" block <= max_min
    candidates = [10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360, 480, 720]
    best = 10
    for c in candidates:
        if c <= max_min:
            best = c
        else:
            break
    return best

def _pick_slot_and_reserve(chosen_device_id, chosen_device_name, block_minutes, reservations_for_range, do_reserve, *, device_label=None):
    """Shared `resdev` slot picker: days -> free slots -> pick -> confirm -> reserve.
    Native and federated homes differ only in how reservations are fetched and
    how the reservation is made (token vs. HOME handle)."""
    device_label = chosen_device_id if device_label is None else device_label
    num_days_s = session.prompt(stylize("Enter the number of days to check for available reservations (starting today): ", (Sty.BOLD, Sty.GREEN))).strip()
    try:
        num_days = int(num_days_s)
        if num_days <= 0:
            print("Invalid number of days.")
            return
    except ValueError:
        print("Invalid input. Please enter a number.")
        return

    # ---------------------------
    # 4) Compute availability (same logic you already have)
    # ---------------------------
    today = datetime.date.today()
    end_day = today + datetime.timedelta(days=num_days - 1)

    reservations_range = reservations_for_range(today, end_day)
    available_slots = []
    now = datetime.datetime.now()

    def build_time_slots(date: datetime.date, block_size: int):
        slots = []
        start_of_day = datetime.datetime.combine(date, datetime.time(0, 0))
        minutes_in_day = 24 * 60
        current_offset = 0
        while current_offset < minutes_in_day:
            slot_start = start_of_day + datetime.timedelta(minutes=current_offset)
            slot_end = slot_start + datetime.timedelta(minutes=block_size)
            if slot_end.date() != date and slot_end.time() != datetime.time.min:
                break
            slots.append((slot_start, slot_end))
            current_offset += block_size
        return slots

    for i in range(num_days):
        day = today + datetime.timedelta(days=i)
        all_slots = build_time_slots(day, block_minutes)

        key = (str(chosen_device_id), day)
        day_reservations = reservations_range.get(key, [])

        for slot in all_slots:
            slot_start, slot_end = slot
            if day == today and slot_end <= now:
                continue
            if not is_slot_conflicting(slot, day_reservations):
                available_slots.append((day, slot))

    if not available_slots:
        printf("No available ", Sty.DEFAULT, f"{block_minutes}-minute", Sty.CYAN, " slots for device ", Sty.DEFAULT, f"{device_label}", Sty.MAGENTA, f" in the next {num_days} days.", Sty.DEFAULT)
        return

    available_slots.sort(key=lambda x: (x[0], x[1][0]))

    print()
    printf("Available time slots for device ", Sty.BOLD, f"{device_label}", Sty.MAGENTA, f" ({block_minutes} min blocks) over the next {num_days} days:", Sty.DEFAULT)
    last_day = None
    for idx, (day, slot) in enumerate(available_slots):
        slot_start_str = slot[0].strftime("%I:%M %p")
        slot_end_str = slot[1].strftime("%I:%M %p")
        if day != last_day:
            print()
            printf(f"{day.strftime('%Y-%m-%d')} ({day.strftime('%a')})", (Sty.BOLD, Sty.BLUE))
            last_day = day
        printf("  ", Sty.DEFAULT, f"{idx}.", Sty.CYAN, f" {slot_start_str} - {slot_end_str}", Sty.DEFAULT)

    pick_s = session.prompt(stylize("Select a slot by index: ", (Sty.BOLD, Sty.GREEN))).strip()
    try:
        pick = int(pick_s)
        if pick < 0 or pick >= len(available_slots):
            print("Invalid selection.")
            return
    except ValueError:
        print("Invalid input. Please enter a number.")
        return

    chosen_day, chosen_slot = available_slots[pick]
    slot_start_str = chosen_slot[0].strftime("%I:%M %p")
    slot_end_str = chosen_slot[1].strftime("%I:%M %p")

    confirmation = session.prompt(stylize(
        "Reservation successful on ", Sty.DEFAULT,
        f"{chosen_day.strftime('%Y-%m-%d')}", (Sty.BOLD, Sty.BLUE),
        " from ", Sty.DEFAULT,
        f"{slot_start_str}", Sty.CYAN,
        " to ", Sty.DEFAULT,
        f"{slot_end_str}", Sty.CYAN,
        " for ", Sty.DEFAULT,
        f"{chosen_device_name}", Sty.GRAY,
        ". \nConfirm reservation? (y/n): ", (Sty.BOLD, Sty.GREEN),
    )).strip().lower()

    if confirmation != "y":
        printf("Reservation cancelled.", Sty.WARNING)
        return

    # ---------------------------
    # 5) Reserve
    # ---------------------------
    do_reserve(chosen_device_id, chosen_day, chosen_slot, slot_start_str, slot_end_str)


def _native_reserve_slot(chosen_device_id, chosen_day, chosen_slot, slot_start_str, slot_end_str):
    token = account.reserve_device(int(chosen_device_id), chosen_slot[0], chosen_slot[1])
    if token:
        printf("Reservation successful on device ", (Sty.BOLD, Sty.GREEN), f"{chosen_device_id}", Sty.MAGENTA, " for ", Sty.DEFAULT, f"{chosen_day.strftime('%Y-%m-%d')} {slot_start_str}-{slot_end_str}.", Sty.CYAN)
        printf("Your Token -> ", Sty.BOLD, f"{token}", (Sty.BOLD, Sty.GREEN))
        printf("Please keep this token safe, as it is not saved on the server and cannot be retrieved again. If you lose it, cancel your reservation and make a new one.", Sty.DEFAULT)


def interactive_reserve_next_days_auto():
    try:
        import ast, json, datetime

        # ---------------------------
        # 1) Fetch perms + per-device caps
        # ---------------------------
        perms_resp = account.get_perms()
        if "ace" in perms_resp.results:
            print(f"Error: {unmap_arg(perms_resp.results['ace'])}")
            return

        uc_raw = unmap_arg(perms_resp.results.get("UC", map_arg("[]")))
        try:
            perm_row = ast.literal_eval(uc_raw)[0]  # your server returns a list-of-rows
        except Exception:
            perm_row = []

        perm_level = perm_row[0] if perm_row else ""

        details = {}
        if perm_level == "Normal User":
            details_raw = unmap_arg(perms_resp.results.get("details", map_arg("{}")))
            try:
                details = json.loads(details_raw) if details_raw else {}
            except Exception:
                details = {}

        caps = details.get("caps", {}) or {}

        def _cap_for(dev_id: int) -> dict:
            # tolerate server sending str keys, or int keys
            return caps.get(str(dev_id)) or caps.get(dev_id) or {}

        # Allowed devices filter from perms:
        allowed_dev_ids: set[int] | None = None

        if perm_level == "Normal User":
            # server already computes allowed devices list for Normal User
            devs = details.get("devices", []) or []
            try:
                allowed_dev_ids = {int(d) for d in devs}
            except Exception:
                allowed_dev_ids = set()

        elif perm_level == "Power User":
            # results[5] is the allowed device list (string encoded) in your system
            try:
                allowed_dev_ids = {int(x) for x in str_to_list(perm_row[5])}
            except Exception:
                allowed_dev_ids = None

        elif perm_level == "Admin":
            allowed_dev_ids = None  # no filter

        # For Power User, one max duration applies to all allowed devices
        power_user_max_t_sec = None
        if perm_level == "Power User":
            try:
                power_user_max_t_sec = int(perm_row[4])
            except Exception:
                power_user_max_t_sec = None

        dev_resp = account.get_devices()
        if "ace" in dev_resp.results:
            print(f"Error: {unmap_arg(dev_resp.results['ace'])}")
            return

        sorted_device_ids = sorted(dev_resp.results.keys(), key=int)  # strings

        if allowed_dev_ids is not None:
            sorted_device_ids = [d for d in sorted_device_ids if int(d) in allowed_dev_ids]

        if not sorted_device_ids:
            printf("No devices available for your permission level.", Sty.WARNING)
            return

        printf("Devices:", Sty.BOLD)
        block_by_dev: dict[str, int] = {}
        max_by_dev: dict[str, int] = {}

        for idx, dev_id in enumerate(sorted_device_ids):
            dev_name = unmap_arg(dev_resp.results[dev_id])

            max_t_sec = 0
            if perm_level == "Normal User":
                c = _cap_for(int(dev_id))
                try:
                    max_t_sec = int(c.get("max_reservation_time_sec", 0) or 0)
                except Exception:
                    max_t_sec = 0
            elif perm_level == "Power User" and power_user_max_t_sec is not None:
                max_t_sec = int(power_user_max_t_sec)
            elif perm_level == "Admin":
                # Admin has no cap; keep UX sane
                max_t_sec = 60 * 60  # 60 min max display; only affects block sizing display

            block_min = _auto_block_minutes(max_t_sec)
            block_by_dev[str(dev_id)] = int(block_min)
            max_by_dev[str(dev_id)] = int(max_t_sec)

            # if max_t_sec > 0:
            #     print(f"{idx}. Device ID: {dev_id}   Name: {dev_name}   (max={max_t_sec//60} min, blocks={block_min} min)")
            # else:
            #     print(f"{idx}. Device ID: {dev_id}   Name: {dev_name}   (blocks={block_min} min)")

            printf(
                f"{idx}.", Sty.CYAN,
                "  Name: ", Sty.DEFAULT,
                f"{dev_name}", Sty.GRAY,
                " Duration: ", Sty.DEFAULT,
                f"{block_min} min", Sty.GREEN,
            )


        sel = session.prompt(stylize(
            "Enter which device you want to reserve ", (Sty.BOLD, Sty.GREEN),
            "(enter the 0-based index): ", Sty.CYAN,
        )).strip()
        try:
            sel_i = int(sel)
            if sel_i < 0 or sel_i >= len(sorted_device_ids):
                print("Invalid selection.")
                return
        except ValueError:
            print("Invalid input. Please enter a number.")
            return

        chosen_device_key = sorted_device_ids[sel_i]
        chosen_device_id = str(chosen_device_key)
        chosen_device_name = unmap_arg(dev_resp.results[chosen_device_key])
        block_minutes = int(block_by_dev.get(chosen_device_id, 30))

        if block_minutes < 10:
            printf(
                "Your max reservation duration for device ", Sty.DEFAULT,
                f"{chosen_device_id}", Sty.MAGENTA,
                f" is < 10 minutes (max_sec={max_by_dev.get(chosen_device_id, 0)}). Cannot create valid reservations.", Sty.DEFAULT,
            )
            return

        _pick_slot_and_reserve(chosen_device_id, chosen_device_name, block_minutes, fetch_reservations_for_range, _native_reserve_slot)

    except Exception as e:
        print(f"Error: {e}")


def run(backend=None, *, register=False):
    global account, session, server_addr
    from ..deployment.backend import selected_backend
    from .grpc_client import active_endpoint
    backend = backend or selected_backend()
    account = RemoteRFAccount(backend=backend)
    if account.is_https_home:
        from .grpc_client import bind_federated_backend
        bind_federated_backend(backend)
    session = PromptSession()
    server_addr = backend.transport.origin if account.is_https_home else active_endpoint()
    policy = backend.capabilities['registration_policy'] if account.is_https_home else local_capabilities()['registration_policy']
    if register:
        initial = ['register'] + (['verify'] if policy['email_verification_required'] else []) + ['login']
    else:
        initial = ['login'] if account.is_https_home else []
    try:
        if not welcome(initial=initial):
            return 0
        clear()
        while True:
            try:
                inpu = session.prompt(stylize(f'{account.username}@remoterf: ', Sty.BOLD))
                if inpu in ('register', 'verify', 'login', 'forgot-password', 'reset-password'):
                    authenticated = _account_command(inpu)
                    if inpu == 'reset-password' and not welcome(show_banner=False):
                        break
                elif inpu == 'refresh':
                    if not account.is_https_home:
                        raise AccountBackendError('Local passwords do not use refresh sessions')
                    account.backend.refresh()
                    print('Home session refreshed.')
                elif inpu == 'logout':
                    if account.is_https_home:
                        account.backend.logout()
                    account.password = None
                    account.username = None
                    printf('Logged out.', (Sty.BOLD, Sty.GREEN))
                    break
                elif inpu == "clear":
                    clear()
                elif inpu == "getdev":
                    devices()
                elif inpu == "help" or inpu == "h":
                    commands()
                elif inpu == "perms":
                    perms()
                elif inpu == "enroll":
                    enroll()
                elif inpu.startswith("enroll "):
                    inline_code = inpu[len("enroll "):]
                    if not inline_code or inline_code != inline_code.strip():
                        raise AccountBackendError("invalid_enrollment_code")
                    enroll(inline_code)
                elif inpu == "quit" or inpu == "exit":
                    break
                elif inpu == "getres":
                    reservations()
                elif inpu == "myres":
                    my_reservations()
                # elif inpu == "resdev s":
                #     interactive_reserve_all()
                elif inpu == "resdev":
                    if account.is_https_home:
                        reserve()
                    else:
                        interactive_reserve_next_days_auto()
                elif inpu == 'cancelres':
                    cancel_my_reservation()

                elif inpu == 'resdev -n':
                    # check if user is admin
                    # if account.get_perms().results['UC'] == 'Admin':
                    reserve()
                elif account.is_admin and inpu.strip().startswith("admin"):
                    handle_admin_command(inpu)
                else:
                    print(f"Unknown command: {inpu}")
            except AccountBackendError as exc:
                label = f" [{exc.provenance}]" if exc.provenance else ""
                print(f'Account error{label}: {exc}')
            except ValidationError as exc:
                print(f'Account error: {exc}')
            except KeyboardInterrupt:
                break
            except EOFError:
                break
        return 0
    finally:
        backend.close()
