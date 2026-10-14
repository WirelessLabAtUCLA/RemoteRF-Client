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

from remoterf.core.grpc_client import addr as server_addr, handle_admin_command, _key
from . import *
from ..common.utils import *

import getpass
import os
import re
import textwrap
import datetime
import functools
import time
import traceback
import ast
from pathlib import Path

from prompt_toolkit import PromptSession

from ..deployment.http import AccountBackendError
from .messages import explain, debug
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


def _format_reservation_range(start, end):
    """'2026-10-14 from 09:00 AM to 09:30 AM'; the end's date too when it differs."""
    to = f"{end:%Y-%m-%d %I:%M %p}" if end.date() != start.date() else f"{end:%I:%M %p}"
    return f"{start:%Y-%m-%d} from {start:%I:%M %p} to {to}"

# ponytail: bounds mirror Global's policy (6-128 chars); not advertised by discovery.
PASSWORD_MIN, PASSWORD_MAX = 6, 128
_PASSWORD_HINT = f"Password must be {PASSWORD_MIN}-{PASSWORD_MAX} characters."

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
    while True:
        password = _ask("Password (Hidden)", hidden=True)
        if enforce_policy and not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
            printf(f"  Password is {len(password)} characters; must be {PASSWORD_MIN}-{PASSWORD_MAX}. Try again.", Sty.WARNING)
            continue
        if password == _ask("Confirm Password", hidden=True):
            return password
        printf("  Passwords do not match. Try again.", Sty.WARNING)


# The code typed at `remoterf -r`, used once for this registration.
pending_enrollment_code = None
# A username already given (`name@lab` at `-l`): the login asks only the password.
pending_username = None
# The saved home this shell is attached to (None on a bare direct profile).
home_name = None
# The route it was reached over (None on a bare direct profile or an HTTPS home).
current_route = None

# What each generated driver package exposes, for the usage lines after a reservation.
DRIVER_CALLS = {"pluto": "adi.Pluto", "adalm_pluto": "adi.Pluto", "usrp": "uhd.usrp.MultiUSRP"}
DRIVER_IMPORTS = {"pluto": "adalm_pluto import adi", "adalm_pluto": "adalm_pluto import adi", "usrp": "usrp import uhd"}
_HANDLE_TAG = re.compile(r"^(?P<name>.*) \[(?P<handle>\S+)\]$")  # handles have no whitespace
_NO_HANDLE = "This device has no handle"
_UNROUTABLE = "Its handle {} can't be used from here: {}"


def _token_lab():
    """The lab `<token>@<lab>` routes to from here: this shell's home, else the
    direct profile's (a shell started by --register has no home yet)."""
    from .grpc_client import _active_home_name

    return home_name or _active_home_name()


def _routable(token):
    """The token as a driver takes it: @<this lab>, so it is never sent elsewhere."""
    lab = _token_lab()
    return f"{token}@{lab}" if lab else f"{token}"


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


def _device_label(name, handle, deployment):
    """'Bluetooth_Dongle@ucla': the handle form, also for a device without a handle."""
    label = handle or _key(name or "") or "unnamed device"
    return f"{label}@{deployment}" if deployment else label


def _native_label(label):
    """A lab's device label ('Pluto SDR [PlutoA] (online)', 'Pluto SDR @mit (online)') in handle form."""
    partner = _PARTNER_LABEL.match(label)
    if partner:
        return _device_label(partner["name"], None, partner["lab"])
    return _device_label(*_name_and_handle(_label_name(label)), _token_lab())


def _print_reserved(where, when, token, handle, logged_in_to, install, no_handle=_NO_HANDLE):
    """The token and, when the device has one, its handle (both @<deployment>).
    Boxed before the driver install, which can fail or stall; the usage box
    follows it, once the device type is known. `no_handle`: why no handle is
    shown."""
    width = len("Handle") if handle else len("Token")
    rows = [(where, (Sty.BOLD, Sty.MAGENTA), f"  {when}", Sty.CYAN), (),
            (f"{'Token':<{width}}  ", Sty.BOLD, token, (Sty.BOLD, Sty.GREEN))]
    if handle:
        rows.append(("Handle  ", Sty.BOLD, handle, (Sty.BOLD, Sty.GREEN)))
    rows.append(())
    if handle:
        rows += [("Token   works anywhere, no login needed.", Sty.DEFAULT),
                 ("        Keep it safe: it can't be shown again.", Sty.DEFAULT),
                 (f"Handle  works while you're logged in to {logged_in_to}", Sty.DEFAULT),
                 ("        and this reservation is active.", Sty.DEFAULT)]
    else:
        rows += [("Works anywhere, no login needed.", Sty.DEFAULT),
                 ("Keep it safe: it can't be shown again.", Sty.DEFAULT), (),
                 *[(line, Sty.GRAY) for line in textwrap.wrap(f"{no_handle}. Lost the token? Cancel and reserve again.", 64)]]
    _panel("Reserved", rows)
    _print_usage(install(), token, handle)


def _print_usage(device_type, token, handle=None):
    """The import and constructor lines for the installed driver. A type
    without a fixed form gets the dynamic-driver one."""
    if not device_type:
        return
    tokens = (token, handle) if handle else (token,)
    if call := DRIVER_CALLS.get(device_type):
        rows = [f"  from remoterf.drivers.{DRIVER_IMPORTS[device_type]}", *(f'  sdr = {call}(token="{text}")' for text in tokens)]
    else:
        from ..drivers.dynamic_device import _default_class_name

        cls = _client_class(device_type) or _default_class_name(device_type)
        # A lab's own driver (bluetooth, gps, ...): its class, from its IDL.
        rows = ["  from remoterf.drivers import ensure_driver",
                "  # installs the driver on any other machine",
                f'  ensure_driver(token="{token}")',
                f"  from remoterf.drivers.{device_type} import {cls}",
                *(f'  dev = {cls}(token="{text}")' for text in tokens)]
    _panel("Usage", [("Use either one as the token:" if handle else "Use it as the token:", Sty.DEFAULT),
                     *((row, Sty.CYAN) for row in rows)])


def _client_class(device_type):
    """The client class a generated driver package exports (beside its `adi` alias)."""
    from importlib import import_module

    try:
        names = import_module(f"remoterf.drivers.{device_type}").__all__
    except Exception:  # noqa: BLE001 - no usage lines is all a bad package costs
        return None
    return next((n for n in names if n != "adi"), None)


def _print_native_reserved(device_id, label, when, token):
    """`label` as the server lists the device: 'Pluto SDR [PlutoA] (online)',
    or a partner lab's 'Pluto SDR @mit (online)' (partner handles: none yet)."""
    partner = _PARTNER_LABEL.match(label)
    local = None if partner else _LOCAL_LABEL.match(label)
    handle = None if partner else _name_and_handle(local["name"] if local else label)[1]
    home = _token_lab()
    _print_reserved(_native_label(label), when, _routable(token),
                    f"{handle}@{home}" if handle and home else None, home,
                    lambda: account.install_reserved_driver(token, int(device_id)),
                    _UNROUTABLE.format(handle, "this deployment has no saved name") if handle else _NO_HANDLE)


def _connected_args(name, home, route):
    marker = '◆' if supports_unicode() else '*'
    title = home.get('display_name') or name
    via = 'LAN' if route['kind'] == 'lan' else route['kind']
    return (
        f'{marker} ', Sty.DEFAULT,
        title, (Sty.BOLD, Sty.BLUE),
        f'  {name}' if title != name else '', Sty.GRAY,
        f'  via {via} {route["host"]}:{route["port"]}', Sty.CYAN,
    )


def _print_connected(name, home, route):
    """One line, same voice as the banner: what we reached and how."""
    printf(*_connected_args(name, home, route))


def _device_path_label():
    """direct / relay / LAN: which path device calls take right now, or None."""
    if account.is_https_home or not current_route:
        return None
    if current_route['kind'] != 'relay':
        return 'LAN' if current_route['kind'] == 'lan' else current_route['kind']
    from . import direct_path

    return direct_path.describe()


def _open_direct_path():
    """Punch the device path after a login over the relay, and wait for the
    first attempt so the session line can say which path device calls use."""
    if account.is_https_home or not current_route:
        return
    from . import direct_path

    path = direct_path.start(
        username=account.username, secret=account.password, route=current_route
    )
    if path is not None:
        from ..remoterf_cli import _working

        with _working('Opening a direct device path…'):
            path.wait()


def status():
    """Where this shell is connected and which path its device calls take."""
    if current_route:
        rows = [_connected_args(home_name, current_route.get('home') or {}, current_route)]
    else:
        rows = [('◆ ' if supports_unicode() else '* ', Sty.DEFAULT, _home_name(), (Sty.BOLD, Sty.BLUE))]
    label = _device_path_label()
    if label:
        rows.append(('  devices via ', Sty.GRAY, label, Sty.CYAN))
    _panel("Status", rows)


def _remember_login():
    """A direct home keeps its session token so `remoterf -l` needs no prompt.

    Only a token is ever stored -- an older deployment that issues none simply
    asks again next time."""
    if home_name and not account.is_https_home and account.username and account.session_token:
        from ..deployment import homes

        homes.remember_login(home_name, account.username, account.session_token)


def _consume_enrollment_code(policy):
    global pending_enrollment_code
    if not policy['enrollment_code_required']:
        return ''
    code, pending_enrollment_code = pending_enrollment_code, None
    return code if code else _ask('Enrollment Code')


def _account_command(command):
    global pending_enrollment_code
    policy = account.backend.capabilities['registration_policy'] if account.backend else local_capabilities()['registration_policy']
    if command in ('r', 'register'):
        if not policy['enabled']:
            raise AccountBackendError('Registration is unavailable at this home')
        _panel("Register", [("Register at ", Sty.DEFAULT, _home_name(), (Sty.BOLD, Sty.BLUE)),
                            *([(_PASSWORD_HINT, Sty.GRAY)] if account.is_https_home else [])])
        account.enrollment_code = _consume_enrollment_code(policy)
        try:
            account.username = _ask('Username') if policy['username_required'] else ''
            account.password = _password_confirmation(enforce_policy=account.is_https_home)
            account.email = _ask('Email') if policy['email_required'] else ''
            if not account.create_user():
                raise AccountBackendError('Registration failed')
            if account.is_https_home:
                from ..deployment import homes
                _note(f"Log in from anywhere as {account.username}@{homes.GLOBAL_ACCOUNTS}", Sty.GRAY)
        except BaseException:
            # The code was fine; only the details failed. Keep it for the retry.
            pending_enrollment_code = account.enrollment_code or None
            raise
        # An HTTPS home still needs a login for a session; a direct deployment
        # is usable as soon as no verification stands in the way.
        done = not policy['email_verification_required'] and not account.is_https_home
        if done:
            # Trade the password for a session token right away, as a login would.
            try:
                account.login_user(remember=True)
            except Exception:  # noqa: BLE001 - registration stands even if this fails
                pass
            _remember_login()
        return done
    if command == 'verify':
        if not policy['email_verification_required']:
            raise AccountBackendError('This deployment does not require email verification')
        if not account.needs_verification:
            # The home activated the account without email (provider down or
            # out of quota); there is no token coming. Straight on to login.
            return False
        _panel("Verify email", [("Enter the 6-digit code from the verification email.", Sty.DEFAULT)])
        account.backend.verify(account.username or account.email, _ask('Verification code').strip())
        _note('Email verified. You can now log in.', (Sty.BOLD, Sty.GREEN))
        return False
    if command in ('forgot-password', 'reset-password'):
        if not account.is_https_home:
            raise AccountBackendError('Contact the local administrator to reset a password')
        if command == 'forgot-password':
            _note("Forgot password", (Sty.BOLD, Sty.BLUE))
            account.backend.forgot(_ask('Email'))
            _note('If eligible, check your email for a password reset token.', Sty.DEFAULT)
        else:
            _panel("Reset password", [("Set a new password with the emailed reset token.", Sty.DEFAULT), (_PASSWORD_HINT, Sty.GRAY)])
            token = _ask('Password reset token', hidden=True)
            account.backend.reset(token, _password_confirmation(enforce_policy=True))
            _note('Password reset. Log in again.', (Sty.BOLD, Sty.GREEN))
        return False
    if command in ('l', 'login'):
        _note(f"Login to {_home_name()}", (Sty.BOLD, Sty.BLUE))
        global pending_username
        from ..deployment import homes
        typed = pending_username or _ask('Username or Email' if account.is_https_home else 'Username')
        pending_username = None
        # `alice@ucla` typed at ucla is `alice` there.
        name, where = homes.split_account(typed)
        here = homes.GLOBAL_ACCOUNTS if account.is_https_home else home_name
        account.username = name if where == here else typed
        account.password = _ask('Password (Hidden)', hidden=True)
        ok = bool(account.login_user(remember=not account.is_https_home))
        if ok:
            _remember_login()
        return ok
    printf('Choose login or register' + (', verify, forgot-password or reset-password.' if account.is_https_home else '.'), Sty.WARNING)
    return False


def welcome(*, show_banner: bool = True, initial=(), route=None):
    """Authenticate. ``initial`` commands run first without prompting (``-l`` →
    login, ``-r`` → register/verify/login); any failure falls back to the prompt."""
    if show_banner:
        print_client_banner(print_my_version(), server=server_addr)
    if route is not None:
        _print_connected(home_name, route.get('home') or {}, route)
    if home_name and not account.is_https_home and 'register' not in initial:
        from ..deployment import homes

        saved = homes.recall_login(home_name)
        if saved:
            account.username, account.password = saved['username'], saved['secret']
            account.session_token = saved['secret']
            try:
                if account.login_user():
                    return True
            except Exception:  # noqa: BLE001 - a stored login must never block the prompt
                pass
            homes.forget_login(home_name)
            account.username = account.password = None
            printf('Stored login is no longer valid. Log in again.', Sty.WARNING)
    if account.is_https_home and 'register' not in initial:
        try:
            if account.backend.resume():
                account.username = account.backend.credentials['username']
                return True
        except Exception:  # noqa: BLE001 - same rule for the HTTPS session
            account.backend.credentials = None
            try:
                account.backend.store.clear()
            except Exception:  # noqa: BLE001
                pass
            printf('Stored session is unavailable. Log in again.', Sty.WARNING)
    queue = list(initial)
    last = None
    while True:
        try:
            if queue:
                command = last = queue.pop(0)
            elif last is not None:
                # `-l` is a login and `-r` a registration: a failed step is
                # asked again, never turned into a menu.
                command = last
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
            _error(exc)
            queue.clear()
        except (KeyboardInterrupt, EOFError):
            return False
        except Exception as exc:  # noqa: BLE001 - a failed command never ends the shell
            if debug():
                traceback.print_exc()
            _error(exc if isinstance(exc, RuntimeError) else _UNEXPECTED)
            queue.clear()

def title():
    print_internal_banner(
        print_my_version(),
        server=server_addr,
        tos_url=_tos_url(),
    )
    printf("Input ", Sty.DEFAULT, "'help' ", Sty.BRIGHT_GREEN, "for a list of available commands.", Sty.DEFAULT)

def session_line():
    """The session facts the full panel carries, on one line under the banner."""
    marker = '◆' if supports_unicode() else '*'
    display_tos = str(_tos_url()).removeprefix("https://").removeprefix("http://")
    label = _device_path_label()
    printf(
        f'{marker} ', Sty.DEFAULT,
        f'Logged in as {account.username}', (Sty.BOLD, Sty.GREEN),
        f'   devices via {label}' if label else '', Sty.CYAN,
        f'   TOS {display_tos}', Sty.GRAY,
        "   'help'", Sty.BRIGHT_GREEN, ' for commands', Sty.GRAY,
    )


def _panel(label, rows):
    """serverrf's labelled frame around rows of printf (text, style, ...)
    pairs, padded to the widest row; () is a blank row."""
    uni = supports_unicode()
    h, v = ("─", "│") if uni else ("-", "|")
    tl, tr, bl, br = ("╭", "╮", "╰", "╯") if uni else ("+",) * 4
    title = f"{h} {label.upper()} "
    width = max(len(title) - 2, 24, *(sum(map(len, row[::2])) for row in rows))
    printf(tl + title + h * (width + 2 - len(title)) + tr, Sty.DEFAULT)
    for row in rows:
        printf(f"{v} ", Sty.DEFAULT, *row, " " * (width - sum(map(len, row[::2]))) + f" {v}", Sty.DEFAULT)
    printf(bl + h * (width + 2) + br, Sty.DEFAULT)


def _note(*args):
    """One line in the banner's voice: a marker, then printf (text, style) pairs."""
    printf('◆ ' if supports_unicode() else '* ', Sty.DEFAULT, *args)


_UNEXPECTED = "That command failed unexpectedly; run with REMOTERF_DEBUG=1 for details."


def _error(error, provenance=None, **facts):
    """A one-line failure in plain English (core/messages.py)."""
    _note(explain(error, provenance, **facts), Sty.WARNING)


def _confirm(question):
    """serverrf's yes/no prompt: `(y/N)`, Enter is no, y/yes/n/no accepted."""
    while True:
        try:
            answer = session.prompt(stylize("  ", Sty.DEFAULT, f"{question} ", Sty.BOLD, "(y/N): ", (Sty.BOLD, Sty.GREEN))).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if answer in ("", "n", "no"):
            return False
        if answer in ("y", "yes"):
            return True
        print("  Please answer y or n.")


def _offline_rows(count):
    """The gray footer of a reserve picker that left offline devices out."""
    return [(), (f"{count} offline device{'s' * (count != 1)} not shown", Sty.GRAY)] if count else []


def commands():
    rows = [
        ("'help' or 'h'", "Show this help message"),
        ("'clear'", "Clear terminal"),
        ("'getdev'", "View devices"),
        ("'resdev'", "Reserve a device"),
        ("'cancelres'", "Cancel a reservation"),
        ("'getres'", "View all reservations"),
        ("'myres'", "View my reservations"),
        *([] if account.is_https_home else [("'perms'", "View permissions")]),
        ("'enroll'", "Enroll with an enrollment code"),
        ("'logout'", "Log out and forget the stored login"),
        ("'status'", "Connection and the path device calls use"),
        ("'direct on|off'", "Use, or stop using, a direct device path"),
        *([("'reset-password'", "Set a new password with an emailed token")] if account.is_https_home else []),
        ("'exit' or 'quit'", "Exit"),
    ]
    width = max(len(name) for name, _ in rows)
    _panel("Commands", [(f"{name:<{width}}  ", Sty.MAGENTA, text, Sty.DEFAULT) for name, text in rows])
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
    """One warning per deployment that refused; returns the names (None: unnamed)
    of those that didn't answer, shown as gray '(offline)' rows."""
    offline, refused = [], False
    for dest in entries:
        if dest["status"] == "ok" or (dest["status"] == "blocked" and dest["provenance"] == "destination_signed"
                                      and dest["error_code"] == "principal_unknown"):
            continue  # not enrolled there yet: normal, `perms` shows it
        # An older Global names a deployment that didn't answer by its id only.
        name = dest["display_name"] if dest["display_name"] != dest["deployment_id"] else None
        if dest["status"] == "unavailable":
            offline.append(name)
        else:
            _error(dest["error_code"] or "blocked",
                   "destination" if dest["provenance"] == "destination_signed" else "home", where=name)
            refused = True
    if refused:
        print()
    return sorted(offline, key=lambda n: (n is None, n or ""))


def _down_rows(offline):
    return [(f"@{n} (offline)" if n else "unnamed deployment (offline)", Sty.GRAY) for n in offline]


def _none_line(message, offline, style=Sty.WARNING):
    """A list with no rows: one line, naming deployments that didn't answer."""
    if offline:
        names = ", ".join(f"@{n}" if n else "an unnamed deployment" for n in offline)
        message = f"{message[:-1]}; {names} {'is' if len(offline) == 1 else 'are'} offline."
    printf(message, style)


def _handle_tag(dev):
    """' [handle]' only when it isn't just the name."""
    h = dev.get("handle")
    return f" [{h}]" if h and _key(dev.get("display_name") or "") != _key(h) else ""


def _federated_devices():
    data = account.get_devices()
    offline = _render_destinations(data["destinations"])
    if not data["devices"]:
        _none_line("No devices visible through this home.", offline, Sty.BOLD)
        return data
    # Grouped by deployment; the opaque <deployment_uuid>:<id> reference is
    # never shown, `resdev` picks by index like the native shell.
    data["devices"].sort(key=lambda d: (d["deployment_name"], d["display_name"], d["local_device_id"]))
    width = max(len(f"{dev['display_name']}{_handle_tag(dev)}") for dev in data["devices"])
    rows, deployment = [], None
    for dev in data["devices"]:
        if dev["deployment_name"] != deployment:
            deployment = dev["deployment_name"]
            rows.append((f"@{deployment}", (Sty.BOLD, Sty.MAGENTA)))
        rows.append((f"  {dev['display_name'] + _handle_tag(dev):<{width}}  ", Sty.DEFAULT,
                     "online" if dev["online"] else "offline", Sty.GREEN if dev["online"] else Sty.GRAY))
    _panel("Devices", rows + _down_rows(offline))
    if data.get("incomplete"):
        printf(f"{data['omitted_count']} more deployment(s) were not queried.", Sty.WARNING)
    return data


def _federated_reservation_rows(*, mine_only):
    """HOME reservation rows labelled like `Bluetooth_Dongle@ucla`, sorted like
    native, and the deployments that didn't answer."""
    data = account.get_reservations()
    offline = _render_destinations(data["destinations"])
    names = {d["deployment_id"]: d["display_name"] for d in data["destinations"] if d["display_name"] != d["deployment_id"]}
    rows = [r for r in data["reservations"] if r["mine"] or not mine_only]
    labels = {}
    if rows:
        try:
            labels = {d["device_id"]: _device_label(d["display_name"], d.get("handle"), d["deployment_name"])
                      for d in account.get_devices()["devices"]}
        except AccountBackendError:
            pass
    for r in rows:
        dep = names.get(r["deployment_id"])
        r["device_label"] = labels.get(r["device_id"]) or (f"unlisted device@{dep}" if dep else "unlisted device")
        r["start"], r["end"] = datetime.datetime.fromtimestamp(r["start_time"]), datetime.datetime.fromtimestamp(r["end_time"])
    rows.sort(key=lambda r: (r["device_label"], r["start"]))
    return rows, offline


def _reservation_panel(label, items, *, numbered=False, offline=()):
    """`items` are sorted (device label, start, end) rows."""
    width = max(len(device) for device, _, _ in items)
    index_width = len(str(len(items) - 1))
    _panel(label, [
        ((f"{i:>{index_width}}. ", Sty.CYAN) if numbered else ())
        + (f"{device:<{width}}  ", Sty.MAGENTA, _format_reservation_range(start, end), Sty.CYAN)
        for i, (device, start, end) in enumerate(items)
    ] + ([(), *_down_rows(offline)] if offline else []))


def _federated_reservations(*, mine_only):
    rows, offline = _federated_reservation_rows(mine_only=mine_only)
    if not rows:
        _none_line("No reservations found.", offline, Sty.BOLD)
        return rows
    _reservation_panel("My reservations" if mine_only else "Reservations",
                       [(r["device_label"], r["start"], r["end"]) for r in rows], offline=offline)
    return rows


def _confirm_cancel(index, device, start, end):
    _panel("Cancel reservation", [
        ("Reservation  ", Sty.GRAY, f"{index}", Sty.CYAN),
        ("Device       ", Sty.GRAY, device, Sty.MAGENTA),
        ("Time         ", Sty.GRAY, _format_reservation_range(start, end), Sty.CYAN),
    ])
    return _confirm("Cancel this reservation?")


def _federated_cancel():
    rows, offline = _federated_reservation_rows(mine_only=True)
    if not rows:
        _none_line("No reservations found.", offline, Sty.BOLD)
        return
    _reservation_panel("My reservations", [(r["device_label"], r["start"], r["end"]) for r in rows], numbered=True,
                       offline=offline)
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
    if not _confirm_cancel(id, r["device_label"], r["start"], r["end"]):
        printf("Aborting. User canceled action.", Sty.WARNING)
        return
    account.cancel_reservation(r["reservation_id"])
    _note("Reservation ID ", Sty.DEFAULT, f'{id}', Sty.CYAN, " successfully canceled.", (Sty.BOLD, Sty.GREEN))


def _federated_reservations_for_range(start_day, end_day):
    """Occupancy keyed like fetch_reservations_for_range, from the HOME's getres."""
    res_dict = {}
    for r in account.get_reservations()["reservations"]:
        start, end = datetime.datetime.fromtimestamp(r["start_time"]), datetime.datetime.fromtimestamp(r["end_time"])
        if start_day <= start.date() <= end_day:
            res_dict.setdefault((r["device_id"], start.date()), []).append((start, end))
    return res_dict


def _global_slug(device_id, deployment_name):
    """The lab's name when it is its own Global slug (checked by discovery),
    else None: then the bare token, which works as is."""
    from .grpc_client import _deployment_id
    from ..deployment.homes import NAME_RE

    name = str(deployment_name or "").lower()
    try:
        if NAME_RE.match(name) and _deployment_id(name) == str(device_id).split(":")[0]:
            return name
    except RuntimeError:
        pass
    return None


def _held_at(device_id):
    """Your live reservations at the device's deployment; None if unknown."""
    dep, now = str(device_id).split(":")[0], datetime.datetime.now().timestamp()
    try:
        return sum(1 for r in account.get_reservations()["reservations"]
                   if r["mine"] and r["deployment_id"] == dep and r["end_time"] > now) or None
    except Exception:  # noqa: BLE001 - the sentence then leaves the number out
        return None


def _federated_reserve_slot(device_id, chosen_day, chosen_slot, slot_start_str, slot_end_str,
                            deployment_name=None, device_name=None, device_handle=None, max_sec=None, max_days=None):
    try:
        token = account.reserve_device(device_id, chosen_slot[0], chosen_slot[1])
    except AccountBackendError as exc:
        _error(exc, where=deployment_name, device=_device_label(device_name, device_handle, deployment_name),
               request="reservation", held=_held_at(device_id) if exc.code == "reservation_limit" else None,
               minutes=(max_sec or 0) // 60 or None, days=max_days)
        return
    when = _format_reservation_range(*chosen_slot)
    install = lambda: account.install_reserved_driver(token, device_id)
    slug = _global_slug(device_id, deployment_name) if token.startswith("rfg_") else None
    label = _device_label(device_name, device_handle, slug or deployment_name)
    if not token.startswith("rfg_"):
        # An older Global: the HOME reference goes where a token goes.
        _panel("Reserved", [(label, (Sty.BOLD, Sty.MAGENTA), f"  {when}", Sty.CYAN), (),
                            ("Token  ", Sty.BOLD, token, (Sty.BOLD, Sty.GREEN))])
        _print_usage(install(), token)
        return
    _print_reserved(label, when, f"{token}@{slug}" if slug else token,
                    f"{device_handle}@{slug}" if device_handle and slug else None, "RemoteRF Global", install,
                    _UNROUTABLE.format(device_handle, "this deployment's Global name is unknown") if device_handle else _NO_HANDLE)


def _federated_reserve():
    """Same flow as the native `resdev` picker, sourced from the HOME's device rows."""
    data = account.get_devices()
    offline = _render_destinations(data["destinations"])
    listed = sorted(data["devices"], key=lambda d: (d["deployment_name"], d["display_name"], d["local_device_id"]))
    if not listed:
        _none_line("No devices available for your permission level.", offline)
        return
    devs = [dev for dev in listed if dev["online"]]
    if not devs:
        _none_line("No devices are online right now.", offline)
        return
    width = max(len(f"{dev['display_name']}{_handle_tag(dev)}") for dev in devs)
    index_width = len(str(len(devs) - 1))
    rows, deployment = [], None
    for idx, dev in enumerate(devs):
        if dev["deployment_name"] != deployment:
            deployment = dev["deployment_name"]
            rows.append((f"@{deployment}", (Sty.BOLD, Sty.MAGENTA)))
        dev["block_min"] = _auto_block_minutes(dev["max_reservation_time_sec"] or 0)
        rows.append((
            f"  {idx:>{index_width}}. ", Sty.CYAN,
            f"{dev['display_name'] + _handle_tag(dev):<{width}}  ", Sty.DEFAULT,
            f"{dev['block_min']} min slots", Sty.GREEN,
        ))
    _panel("Devices", rows + _down_rows(offline) + _offline_rows(len(listed) - len(devs)))
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
            _device_label(dev["display_name"], dev.get("handle"), dev["deployment_name"]), Sty.MAGENTA,
            f" can be reserved for at most {(dev['max_reservation_time_sec'] or 0) // 60} minutes, shorter than the 10-minute minimum.", Sty.DEFAULT,
        )
        return
    max_days = -(-int(dev.get("max_horizon_sec") or 0) // 86400) or None
    reserve_slot = functools.partial(_federated_reserve_slot, deployment_name=dev["deployment_name"],
                                     device_name=dev["display_name"], device_handle=dev.get("handle"),
                                     max_sec=dev["max_reservation_time_sec"], max_days=max_days)
    _pick_slot_and_reserve(dev["device_id"], dev["display_name"], dev["block_min"], _federated_reservations_for_range, reserve_slot,
                           device_label=_device_label(dev["display_name"], dev.get("handle"), dev["deployment_name"]),
                           max_days=max_days)


def reservations():
    if account.is_https_home:
        _federated_reservations(mine_only=False)
        return
    data = account.get_reservations()
    if 'ace' in data.results:
        _error(unmap_arg(data.results['ace']))
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

    labels = _native_labels()
    sorted_entries = sorted(((labels.get(e['device_id'], "unlisted device"), e['start_time'], e['end_time']) for e in entries))
    _reservation_panel("Reservations", sorted_entries)

def my_reservations():
    if account.is_https_home:
        _federated_reservations(mine_only=True)
        return
    data = account.get_reservations()
    if 'ace' in data.results:
        _error(unmap_arg(data.results['ace']))
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
        if account.username == entry['username']:
            entries.append(entry)

    if (entries == []):
        printf("No reservations found.", Sty.BOLD)
        return

    labels = _native_labels()
    sorted_entries = sorted(((labels.get(e['device_id'], "unlisted device"), e['start_time'], e['end_time']) for e in entries))
    _reservation_panel("My reservations", sorted_entries)

def cancel_my_reservation():
    if account.is_https_home:
        _federated_cancel()
        return
    ## print all of ur reservations and their ids
    ## ask for id to cancel
    ## remove said reservation
    data = account.get_reservations()
    if 'ace' in data.results:
        _error(unmap_arg(data.results['ace']))
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
    
    labels = _native_labels() if entries else {}
    for entry in entries:
        entry['label'] = labels.get(entry['device_id'], "unlisted device")
    sorted_entries = sorted(entries, key=lambda x: (x['label'], x['start_time']))
    for i, entry in enumerate(sorted_entries):  # label all reservations with unique id
        entry['id'] = i

    if sorted_entries == []:
        printf("No reservations found.", Sty.BOLD)
        return
    _reservation_panel("My reservations", [(e['label'], e['start_time'], e['end_time']) for e in sorted_entries], numbered=True)
        
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
                if _confirm_cancel(id, entry["label"], entry["start_time"], entry["end_time"]):
                    response = account.cancel_reservation(db_id)
                    if 'ace' in response.results:
                        _error(unmap_arg(response.results['ace']))
                    elif 'UC' in response.results:
                        _note("Reservation ID ", Sty.DEFAULT, f'{id}', Sty.CYAN, " successfully canceled.", (Sty.BOLD, Sty.GREEN))
                else:
                    printf("Aborting. User canceled action.", Sty.WARNING)
                return
            
        _error(f"No reservation found with ID {id}.")
    else:
        print("Aborting. A non integer key was given.")

_PARTNER_LABEL = re.compile(r"^(?P<name>.*) @(?P<lab>.+?) \((?P<state>[^)]*)\)$")
# The state always starts with online/offline; a PoE fault after it can hold brackets.
_LOCAL_LABEL = re.compile(r"^(?P<name>.*?) \((?P<state>(?:online|offline)\b.*)\)$")


def _label_name(label):
    """'Pluto SDR [PlutoA] (online)' -> 'Pluto SDR [PlutoA]'; a partner's
    'Pluto SDR @mit (online)' -> 'Pluto SDR @mit'."""
    match = _LOCAL_LABEL.match(label)
    return match["name"] if match else label


def _native_labels():
    """Device id -> 'Bluetooth_Dongle@ucla' for reservation rows; {} if the lab won't list devices."""
    try:
        rows = _native_device_rows()
    except Exception:  # noqa: BLE001 - rows then read 'unlisted device'
        return {}
    home = _token_lab()
    return {r["id"]: _device_label(r["name"], None, r["lab"]) if r["away"]
            else _device_label(*_name_and_handle(r["name"]), home or r["lab"]) for r in rows}


def _label_online(label):
    match = _LOCAL_LABEL.match(label)
    return bool(match) and match["state"].startswith("online")


def _native_device_rows():
    """The home's devices plus any a partner lab shares with us, grouped by
    lab (home first). The server's ids stay internal; the shell numbers them."""
    data = account.get_devices()
    if 'ace' in data.results:
        raise RuntimeError(unmap_arg(data.results['ace']))
    home = (current_route.get('home') or {}).get('display_name') if current_route else None
    home = home or home_name or _home_name()
    rows = []
    for key in sorted(data.results, key=int):
        label = str(unmap_arg(data.results[key]))
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


LAB_PICKER_MIN_DEVICES = 10


def _pick_lab(rows):
    """With many devices across several labs, narrow to one lab first: a
    numbered list of labs with their device counts. Returns the kept rows,
    or None if the user backed out."""
    labs = []
    for row in rows:
        if row["lab"] not in labs:
            labs.append(row["lab"])
    if len(rows) <= LAB_PICKER_MIN_DEVICES or len(labs) < 2:
        return rows
    width = max(len(lab) for lab in labs)
    index_width = len(str(len(labs)))
    lines = []
    for index, lab in enumerate(labs, 1):
        count = sum(1 for row in rows if row["lab"] == lab)
        lines.append((f"{index:>{index_width}}. ", Sty.CYAN, f"@{lab:<{width}}", (Sty.BOLD, Sty.MAGENTA),
                      f"  {count} device{'s' if count != 1 else ''}", Sty.GRAY))
    _panel("Deployments", lines)
    pick = session.prompt(stylize("Which deployment? (number, Enter for all): ", Sty.DEFAULT)).strip()
    if not pick:
        return rows
    if not pick.isdigit() or not 1 <= int(pick) <= len(labs):
        printf("Pick a number from the list.", Sty.WARNING)
        return None
    chosen = labs[int(pick) - 1]
    return [row for row in rows if row["lab"] == chosen]


def _print_native_devices(rows, *, numbered=False, offline=0):
    width = max(len(row["name"]) for row in rows)
    index_width = len(str(len(rows)))
    lines, lab = [], None
    for index, row in enumerate(rows, 1):
        if row["lab"] != lab:
            lab = row["lab"]
            lines.append((f"@{lab}", (Sty.BOLD, Sty.MAGENTA)))
        state = row["state"] or ""
        lines.append((f"  {index:>{index_width}}. " if numbered else "  ", Sty.CYAN,
                      f"{row['name']:<{width}}  ", Sty.DEFAULT,
                      state, Sty.GREEN if state.startswith("online") else Sty.GRAY))
    _panel("Devices", lines + _offline_rows(offline))


def devices():
    if account.is_https_home:
        _federated_devices()
        return
    try:
        rows = _native_device_rows()
    except RuntimeError as exc:
        _error(exc)
        return
    if not rows:
        printf("No devices available for your permission level.", Sty.WARNING)
        return
    rows = _pick_lab(rows)
    if rows is None:
        return
    _print_native_devices(rows)


def get_datetime(question:str):
    timestamp = session.prompt(stylize(f'{question}', Sty.DEFAULT, ' (YYYY-MM-DD HH:MM): ', Sty.GRAY))
    try:
        return _parse_reservation_time(timestamp.strip() + ':00')
    except ValueError:
        raise RuntimeError("Enter the time as YYYY-MM-DD HH:MM.") from None

def reserve():
    try:
        if account.is_https_home:
            _federated_reserve()
            return
        rows = _native_device_rows()
        if not rows:
            printf("No devices available for your permission level.", Sty.WARNING)
            return
        rows = _pick_lab(rows)
        if rows is None:
            return
        online = [row for row in rows if row["state"].startswith("online")]
        if not online:
            printf("No devices are online right now.", Sty.WARNING)
            return
        _print_native_devices(online, numbered=True, offline=len(rows) - len(online))
        pick = session.prompt(stylize("Enter the number of the device to reserve: ", Sty.DEFAULT)).strip()
        if not pick.isdigit() or not 1 <= int(pick) <= len(online):
            printf("Pick a number from the list.", Sty.WARNING)
            return
        row = online[int(pick) - 1]
        start, end = get_datetime("Reserve Start Time"), get_datetime("Reserve End Time")
        token = account.reserve_device(row["id"], start, end)
        if token != '':
            name, handle = (row["name"], None) if row["away"] else _name_and_handle(row["name"])
            home = _token_lab()
            lab = row["lab"] if row["away"] else home or row["lab"]
            _print_reserved(_device_label(name, handle, lab),
                            _format_reservation_range(start, end), _routable(token),
                            f"{handle}@{home}" if handle and home else None, home,
                            lambda: account.install_reserved_driver(token, row["id"]),
                            _UNROUTABLE.format(handle, "this deployment has no saved name") if handle else _NO_HANDLE)
    except Exception as e:
        _error(e if isinstance(e, RuntimeError) else _UNEXPECTED)

import ast
import json


def _render_federation_permissions(entries):
    """Render typed remote snapshots without upgrading transport text to fact."""

    if not entries:
        return
    rows = []
    for entry in entries:
        if rows:
            rows.append(())
        name = entry["display_name"]
        name = None if name == entry.get("deployment_id") else name
        rows.append((name or "A deployment", Sty.BOLD))
        rows.append((f"  Contract: {entry['state']}", Sty.GRAY))
        status = entry["status"]
        rows.append((f"  Status: {status if status == 'ok' else 'refused' if status == 'blocked' else 'offline'}", Sty.GRAY))
        if status == "ok":
            groups = ", ".join(g["group_name"] for g in entry["groups"])
            rows.append((f"  Groups: {groups or '(none)'}", Sty.GRAY))
            checked = datetime.datetime.fromtimestamp(
                entry["retrieved_at"], tz=datetime.timezone.utc
            ).isoformat().replace("+00:00", "Z")
            rows.append((f"  Checked: {checked}", Sty.GRAY))
        elif status == "blocked":
            provenance = "destination" if entry.get("provenance") == "destination_signed" else "home"
            rows.append((f"  Reason: {explain(entry['error_code'], provenance, where=name)}", Sty.GRAY))
    _panel("Federated permissions", rows)


def perms():
    data = account.get_perms()
    if account.is_https_home:
        local = data.get("local") or {
            "display_name": account.backend.capabilities["display_name"],
            "groups": data["groups"],
        }
        _panel("Permissions", [
            (local["display_name"], Sty.BOLD),
            ("  Groups: ", Sty.GRAY, ", ".join(g["group_name"] for g in local["groups"]) or "(none)", Sty.MAGENTA),
        ])
        _render_federation_permissions(data.get("federation", []))
        if data.get("incomplete"):
            print(
                f"  Federation summary incomplete: {data.get('omitted_count', 0)} "
                "contracted destination(s) omitted by the HOME limit"
            )
        return
    if 'ace' in data.results:
        _error(unmap_arg(data.results['ace']))
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

    rows = []

    def render_remote_permissions():
        _panel("Permissions", rows)
        _render_federation_permissions(remote_permissions)
        if remote_permissions_incomplete:
            print(
                f"Federation summary incomplete: {remote_permissions_incomplete} "
                "destination(s) omitted"
            )

    rows.append(("Permission Level: ", (Sty.BOLD, Sty.BLUE), f"{perm_level}", Sty.MAGENTA))

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
            rows.append(("User Groups:", (Sty.BOLD, Sty.BLUE)))
            for g in groups:
                rows.append(("  - ", Sty.CYAN, f"{g}", Sty.MAGENTA))
        else:
            rows.append(("User Groups: ", (Sty.BOLD, Sty.BLUE), "(none)", Sty.GRAY))

        # ---- Devices ----
        if not devices:
            rows.append(("Devices: ", Sty.DEFAULT, "None", Sty.MAGENTA))
            render_remote_permissions()
            return

        rows.append(("Accessible Devices: ", (Sty.BOLD, Sty.BLUE), f"{devices}", Sty.MAGENTA))

        # Build per-device caps and group identical limits together
        buckets: dict[tuple[int, int, int], list[int]] = {}  # (max_r, max_t_sec, horizon_sec) -> [dev_ids]
        for d in devices:
            try:
                did = int(d)
            except Exception:
                continue
            c = _cap_for(did)
            max_t = int(c.get("max_reservation_time_sec", 0) or 0)
            max_r = int(c.get("max_reservations", 0) or 0)
            max_h = int(c.get("max_horizon_sec", 0) or 0)
            buckets.setdefault((max_r, max_t, max_h), []).append(did)

        if not buckets:
            rows.append(("Limits per device: (none)", Sty.GRAY))
            render_remote_permissions()
            return

        # If everything shares the same limits, print once
        if len(buckets) == 1:
            (max_r, max_t, max_h), _devs = next(iter(buckets.items()))
            rows.append(("Permissions:", (Sty.BOLD, Sty.BLUE)))
            rows.append(("  Max Reservations: ", Sty.GRAY, f"{max_r}", Sty.CYAN))
            rows.append(("  Reservation Duration (min): ", Sty.GRAY, f"{max_t // 60}", Sty.CYAN))
            rows.append(("  Days Ahead: ", Sty.GRAY, f"{max_h / 86400:g}" if max_h else "no limit", Sty.CYAN))
            render_remote_permissions()
            return

        # Otherwise print grouped limits
        rows.append(("Permissions per device (grouped):", (Sty.BOLD, Sty.BLUE)))
        for (max_r, max_t, max_h), devs in sorted(buckets.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2], kv[1])):
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
            rows.append(("  devices[", Sty.GRAY, f"{dev_str}", Sty.MAGENTA, "]: ", Sty.GRAY, f"max_reservations={max_r}, max_time_min={max_t // 60}, days_ahead={f'{max_h / 86400:g}' if max_h else 'no limit'}", Sty.CYAN))

        render_remote_permissions()

    elif perm_level == "Power User":
        rows.append(("Max Reservations: ", (Sty.BOLD, Sty.BLUE), f"{results[3]}", Sty.CYAN))
        rows.append(("Max Reservation Duration (min): ", (Sty.BOLD, Sty.BLUE), f"{int(results[4]/60)}", Sty.CYAN))
        rows.append(("Device IDs allowed Access to: ", (Sty.BOLD, Sty.BLUE), f"{results[5]}", Sty.MAGENTA))
        render_remote_permissions()

    elif perm_level == "Admin":
        rows.append(("No restrictions on reservation count or duration.", (Sty.BOLD, Sty.GREEN)))
        render_remote_permissions()

    else:
        _error(f"Unknown permission level {perm_level}")

        
def enroll(code=None):
    code = code or session.prompt(stylize("Enter your enrollment code: ", Sty.DEFAULT))
    # Codes are handed out as <lab>/<code>; the lab part only says where to
    # register. Here we are already logged in somewhere, so strip it -- and
    # say so if it names a different lab than this one.
    typed = code
    try:
        from remoterf.deployment import homes

        host, code = homes.parse_code(code)
    except Exception:  # noqa: BLE001 - not in that form; send as typed
        host = None
    if account.is_https_home:
        code = typed  # Global enrols at the lab the invite names, so it needs the whole invite
    elif host and home_name and homes.home_name_for(host) != home_name:
        _error(f"That code is for {host}, not {home_name}. Register there with: remoterf -r")
        return False
    account.enrollment_code = code
    data = account.set_enroll()

    if account.is_https_home:
        if data.get("status") != "enrolled":
            _error("enrollment_not_confirmed")
            return False
    elif 'ace' in data.results or 'UE' in data.results:
        provenance = (
            unmap_arg(data.results['federation_provenance'])
            if 'federation_provenance' in data.results else None
        )
        _error(unmap_arg(data.results['ace' if 'ace' in data.results else 'UE']), provenance)
        return False
    _note("Enrollment successful.", (Sty.BOLD, Sty.GREEN))
    return True

# New block scheduling

def fetch_all_reservations():
    data = account.get_reservations()
    if 'ace' in data.results:
        _error(unmap_arg(data.results['ace']))
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
            _error(unmap_arg(data.results['ace']))
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
        
        if not _confirm_reserve(f"Device {chosen_device_id}", *chosen_slot):
            printf("Reservation cancelled.", Sty.WARNING)
            return
        
        # print(f"device_id : {chosen_device_id}, start_time : {chosen_slot[0]}, end_time : {chosen_slot[1]}")
        
        # --- 6) Reserve the chosen slot on the chosen device ---
        token = account.reserve_device(int(chosen_device_id), chosen_slot[0], chosen_slot[1])
        if token:
            _print_native_reserved(chosen_device_id, str(unmap_arg(data.results[chosen_device_id])),
                                   _format_reservation_range(*chosen_slot), token)

    except Exception as e:
        _error(e)
        
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

def _confirm_reserve(device, start, end):
    _panel("Reserve", [(device, Sty.MAGENTA), (_format_reservation_range(start, end), Sty.CYAN)])
    return _confirm("Confirm reservation?")


def _pick_slot_and_reserve(chosen_device_id, chosen_device_name, block_minutes, reservations_for_range, do_reserve, *, device_label=None, max_days=None):
    """Shared `resdev` slot picker: days -> free slots -> pick -> confirm -> reserve.
    Native and federated homes differ only in how reservations are fetched and
    how the reservation is made (token vs. HOME handle). `max_days` is the
    group's reservation horizon; the server refuses starts beyond it anyway."""
    device_label = chosen_device_id if device_label is None else device_label
    limit = f" (up to {max_days})" if max_days else ""
    num_days_s = session.prompt(stylize(f"Enter the number of days to check for available reservations (starting today){limit}: ", (Sty.BOLD, Sty.GREEN))).strip()
    try:
        num_days = int(num_days_s)
        if num_days <= 0 or (max_days and num_days > max_days):
            print("Invalid number of days." if num_days <= 0 else f"Your group may reserve at most {max_days} days ahead.")
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
    index_width = len(str(len(available_slots) - 1))
    rows = [(f"{device_label}", Sty.MAGENTA, f"  {block_minutes} min blocks, next {num_days} days", Sty.GRAY)]
    last_day = None
    for idx, (day, slot) in enumerate(available_slots):
        slot_start_str = slot[0].strftime("%I:%M %p")
        slot_end_str = slot[1].strftime("%I:%M %p")
        if day != last_day:
            rows += [(), (f"{day.strftime('%Y-%m-%d')} ({day.strftime('%a')})", (Sty.BOLD, Sty.BLUE))]
            last_day = day
        rows.append((f"  {idx:>{index_width}}. ", Sty.CYAN, f"{slot_start_str} - {slot_end_str}", Sty.DEFAULT))
    _panel("Available slots", rows)

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

    if not _confirm_reserve(device_label, *chosen_slot):
        printf("Reservation cancelled.", Sty.WARNING)
        return

    # ---------------------------
    # 5) Reserve
    # ---------------------------
    do_reserve(chosen_device_id, chosen_day, chosen_slot, slot_start_str, slot_end_str)


def _native_reserve_slot(chosen_device_id, chosen_day, chosen_slot, slot_start_str, slot_end_str, device_label=""):
    try:
        token = account.reserve_device(int(chosen_device_id), chosen_slot[0], chosen_slot[1])
    except Exception as exc:  # noqa: BLE001 - shown as a sentence, the shell carries on
        partner = _PARTNER_LABEL.match(device_label)
        _error(exc if isinstance(exc, RuntimeError) else _UNEXPECTED,
               where=partner["lab"] if partner else _token_lab(), device=_native_label(device_label), request="reservation")
        return
    if token:
        _print_native_reserved(chosen_device_id, device_label, _format_reservation_range(*chosen_slot), token)


def interactive_reserve_next_days_auto():
    try:
        import ast, json, datetime

        # ---------------------------
        # 1) Fetch perms + per-device caps
        # ---------------------------
        perms_resp = account.get_perms()
        if "ace" in perms_resp.results:
            _error(unmap_arg(perms_resp.results['ace']))
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
            _error(unmap_arg(dev_resp.results['ace']))
            return

        sorted_device_ids = sorted(dev_resp.results.keys(), key=int)  # strings

        if allowed_dev_ids is not None:
            # A partner lab's device is listed only when this account is enrolled
            # there; it needs no local group (and has no local caps: 30 min blocks).
            sorted_device_ids = [d for d in sorted_device_ids
                                 if int(d) in allowed_dev_ids or _PARTNER_LABEL.match(str(unmap_arg(dev_resp.results[d])))]

        if not sorted_device_ids:
            printf("No devices available for your permission level.", Sty.WARNING)
            return

        listed = len(sorted_device_ids)
        sorted_device_ids = [d for d in sorted_device_ids if _label_online(str(unmap_arg(dev_resp.results[d])))]
        if not sorted_device_ids:
            printf("No devices are online right now.", Sty.WARNING)
            return

        names = [_label_name(str(unmap_arg(dev_resp.results[d]))) for d in sorted_device_ids]
        name_width = max(map(len, names))
        index_width = len(str(len(names) - 1))
        rows = []
        block_by_dev: dict[str, int] = {}
        max_by_dev: dict[str, int] = {}
        days_by_dev: dict[str, int] = {}

        for idx, dev_id in enumerate(sorted_device_ids):
            max_t_sec = 0
            if perm_level == "Normal User":
                c = _cap_for(int(dev_id))
                try:
                    max_t_sec = int(c.get("max_reservation_time_sec", 0) or 0)
                    days_by_dev[str(dev_id)] = -(-int(c.get("max_horizon_sec", 0) or 0) // 86400)
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

            rows.append((f"{idx:>{index_width}}. ", Sty.CYAN, f"{names[idx]:<{name_width}}  ", Sty.DEFAULT,
                         f"{block_min} min slots", Sty.GREEN))

        _panel("Devices", rows + _offline_rows(listed - len(names)))
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
        chosen_device_name = str(unmap_arg(dev_resp.results[chosen_device_key]))
        shown = _native_label(chosen_device_name)
        block_minutes = int(block_by_dev.get(chosen_device_id, 30))

        if block_minutes < 10:
            printf(
                shown, Sty.MAGENTA,
                f" can be reserved for at most {max_by_dev.get(chosen_device_id, 0) // 60} minutes, shorter than the 10-minute minimum.", Sty.DEFAULT,
            )
            return

        _pick_slot_and_reserve(chosen_device_id, shown, block_minutes, fetch_reservations_for_range,
                               functools.partial(_native_reserve_slot, device_label=chosen_device_name),
                               device_label=shown, max_days=days_by_dev.get(chosen_device_id) or None)

    except Exception as e:
        _error(e if isinstance(e, RuntimeError) else _UNEXPECTED)


def run(backend=None, *, register=False, enrollment_code=None, server_label=None,
        show_banner=True, home=None, route=None, username=None):
    global account, session, server_addr, pending_enrollment_code, home_name, current_route, pending_username
    pending_username = username
    home_name = home
    current_route = route
    from ..deployment.backend import selected_backend
    from .grpc_client import active_endpoint
    backend = backend or selected_backend()
    account = RemoteRFAccount(backend=backend)
    if account.is_https_home:
        from .grpc_client import bind_federated_backend
        bind_federated_backend(backend)
    session = PromptSession()
    pending_enrollment_code = enrollment_code
    # A HOME reached over a chosen route is still named by the HOME, not by the
    # address that route happens to use.
    server_addr = server_label or (
        backend.transport.origin if account.is_https_home else active_endpoint()
    )
    policy = backend.capabilities['registration_policy'] if account.is_https_home else local_capabilities()['registration_policy']
    if register:
        initial = ['register'] + (['verify'] if policy['email_verification_required'] else []) + ['login']
    else:
        initial = ['login']
    try:
        if not welcome(initial=initial, show_banner=show_banner, route=route):
            return 0
        _open_direct_path()
        # Continue below the banner rather than wiping the screen: with a
        # stored login the wipe came a split second after everything appeared.
        session_line()
        from ..deployment import homes
        # The prompt is the account's full name: alice@ucla, alice@global.
        where = homes.GLOBAL_ACCOUNTS if account.is_https_home else (home_name or 'remoterf')
        while True:
            try:
                inpu = session.prompt(stylize(f'{account.username}@{where}> ', Sty.BOLD))
                if inpu in ('register', 'verify', 'login', 'forgot-password', 'reset-password'):
                    authenticated = _account_command(inpu)
                    if inpu == 'reset-password' and not welcome(show_banner=False):
                        break
                elif inpu == 'refresh':
                    if not account.is_https_home:
                        raise AccountBackendError('Local passwords do not use refresh sessions')
                    account.backend.refresh()
                    _note('Home session refreshed.', (Sty.BOLD, Sty.GREEN))
                elif inpu == 'logout':
                    account.logout()
                    if home_name and not account.is_https_home:
                        from ..deployment import homes
                        homes.forget_login(home_name)
                    account.password = None
                    account.username = None
                    _note('Logged out.', (Sty.BOLD, Sty.GREEN))
                    break
                elif inpu == "clear":
                    clear()
                elif inpu == "status":
                    status()
                elif inpu in ("direct off", "direct on"):
                    from . import direct_path

                    direct_path.stop()
                    if inpu == "direct on":
                        _open_direct_path()
                    status()
                elif inpu == "getdev":
                    devices()
                elif inpu == "help" or inpu == "h":
                    commands()
                elif inpu == "perms" and not account.is_https_home:
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
                elif inpu.strip():
                    print(f"Unknown command: {inpu}")
            except (AccountBackendError, ValidationError) as exc:
                _error(exc)
            except KeyboardInterrupt:
                break
            except EOFError:
                break
            except Exception as exc:  # noqa: BLE001 - a failed command never ends the shell
                if debug():
                    traceback.print_exc()
                _error(exc if isinstance(exc, RuntimeError) else _UNEXPECTED)
        return 0
    finally:
        from . import direct_path

        direct_path.stop()
        backend.close()
