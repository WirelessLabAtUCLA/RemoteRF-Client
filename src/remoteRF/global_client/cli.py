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

"""CLI verbs for RemoteRF Global v1.0: `global login|status|logout`,
`deployments [show|resources]`, and `use <slug>|direct`.

Dispatched from `remoterf_cli.main()` exactly like the existing
`--login`/`--config` verbs -- same argv[0]-based dispatch, same `printf`/
`Sty` output style, same "return an int exit code" convention. Nothing here
runs, or is imported, unless one of these commands is actually invoked.
"""

from __future__ import annotations

import json
import os
from typing import Optional, Sequence

from ..common.utils import Sty, printf
from .api_client import GlobalApiClient
from .auth_client import AuthenticatedGlobalClient
from .credentials import GlobalCredentialStore, GlobalCredentials, resolve_secret_store
from .device_flow import DeviceLoginPrompt, run_device_login
from .errors import DeviceLoginDeniedError, GlobalClientError, NotLoggedInError, exit_code_for
from .local_sessions import LocalSessionStore
from .profile import default_config_root, load_direct_profile
from .session_manager import GlobalSessionManager
from .state import DEFAULT_GLOBAL_BASE_URL, load_state, save_state

_CLIENT_NAME = "remoterf-cli"


# --- shared flag parsing -----------------------------------------------------


def _flag_value(argv: Sequence[str], *names: str) -> Optional[str]:
    for i, tok in enumerate(argv):
        if tok in names and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _resolve_global_url(argv: Sequence[str]) -> tuple[str, bool]:
    override = _flag_value(argv, "--global-url")
    env_override = os.getenv("REMOTERF_GLOBAL_BASE_URL")
    base_url = override or env_override or DEFAULT_GLOBAL_BASE_URL
    allow_http = "--global-url-allow-http" in argv or (os.getenv("REMOTERF_GLOBAL_ALLOW_HTTP") == "1")
    return base_url, allow_http


def _wants_file_store(argv: Sequence[str]) -> bool:
    value = _flag_value(argv, "--credential-store")
    if value:
        return value.strip().lower() == "file"
    return (os.getenv("REMOTERF_GLOBAL_CREDENTIAL_STORE") or "").strip().lower() == "file"


def _api_client(argv: Sequence[str]) -> GlobalApiClient:
    base_url, allow_http = _resolve_global_url(argv)
    return GlobalApiClient(base_url, allow_insecure_http=allow_http, client_name=_CLIENT_NAME)


def _credential_store(argv: Sequence[str], *, quiet: bool = False):
    config_root = default_config_root()
    warn = (lambda *_: None) if quiet else print
    return resolve_secret_store(config_root=config_root, force_file=_wants_file_store(argv), warn=warn)


def _run(fn, argv: Sequence[str]) -> int:
    """Run a subcommand body, mapping any GlobalClientError to a clear
    message and the stable exit code its category implies."""
    try:
        return fn(argv)
    except GlobalClientError as exc:
        printf(f"Error: {exc}", Sty.BRIGHT_RED)
        return exit_code_for(exc)


# --- `remoterf global ...` ---------------------------------------------------


def print_global_help() -> None:
    printf("RemoteRF Global commands:", (Sty.BOLD, Sty.BLUE))
    printf("  remoterf global login", Sty.CYAN, "    Sign in via device-code flow", Sty.DEFAULT)
    printf("  remoterf global status", Sty.CYAN, "   Show Global sign-in/session status", Sty.DEFAULT)
    printf("  remoterf global logout", Sty.CYAN, "   Sign out and clear local Global credentials", Sty.DEFAULT)
    print()
    printf("Options:", (Sty.BOLD, Sty.MAGENTA))
    printf("  --no-browser", Sty.CYAN, "             Do not try to open a browser for login", Sty.DEFAULT)
    printf("  --json", Sty.CYAN, "                    Machine-readable output (status)", Sty.DEFAULT)
    printf("  --global-url <url>", Sty.CYAN, "        Override the Global base URL", Sty.DEFAULT)
    printf("  --credential-store <keyring|file>", Sty.CYAN, "  Force a credential backend", Sty.DEFAULT)


def cmd_global(argv: Sequence[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print_global_help()
        return 0

    sub, rest = argv[0], argv[1:]
    if sub == "login":
        return _run(_cmd_login, rest)
    if sub == "status":
        return _run(_cmd_status, rest)
    if sub == "logout":
        return _run(_cmd_logout, rest)

    print(f"ERROR: unknown 'global' subcommand: {sub!r}")
    return 2


def _cmd_login(argv: Sequence[str]) -> int:
    no_browser = "--no-browser" in argv
    base_url, allow_http = _resolve_global_url(argv)
    config_root = default_config_root()

    store = _credential_store(argv)
    cred_store = GlobalCredentialStore(store)

    api = _api_client(argv)
    try:
        def on_prompt(prompt: DeviceLoginPrompt) -> None:
            printf("To sign in to RemoteRF Global, visit:", Sty.BOLD)
            printf("  ", Sty.DEFAULT, prompt.verification_uri, (Sty.CYAN, Sty.UNDERLINE))
            printf("and enter code: ", Sty.DEFAULT, prompt.user_code, (Sty.BOLD, Sty.GREEN))
            if prompt.browser_opened:
                printf("A browser window has been opened for you.", Sty.GRAY)
            else:
                printf("Or open directly:", Sty.GRAY)
                printf("  ", Sty.DEFAULT, prompt.verification_uri_complete, (Sty.CYAN, Sty.UNDERLINE))

        try:
            pair = run_device_login(api, no_browser=no_browser, on_prompt=on_prompt)
        except KeyboardInterrupt:
            printf("Login cancelled.", Sty.WARNING)
            return 130
        except DeviceLoginDeniedError:
            printf("RemoteRF Global login was denied.", Sty.BRIGHT_RED)
            return exit_code_for(DeviceLoginDeniedError())

        creds = GlobalCredentials.from_token_pair(pair.access_token, pair.refresh_token, pair.expires_in)
        cred_store.save(creds)

        auth = AuthenticatedGlobalClient(api, cred_store)
        me = None
        try:
            me = auth.me()
        except GlobalClientError as exc:
            printf(f"Signed in, but could not fetch account details: {exc}", Sty.WARNING)
    finally:
        api.close()

    state = load_state(config_root, global_base_url=base_url)
    state = state.with_(global_base_url=base_url, credential_store_mode=store.mode.value)
    if me is not None:
        state = state.with_(user_id=me.id, user_email=me.email)
    save_state(config_root, state)

    printf("Signed in to RemoteRF Global.", (Sty.BOLD, Sty.GREEN))
    if me is not None:
        printf("  Account: ", Sty.GRAY, me.email, Sty.CYAN)
        if not me.email_verified:
            printf("  Note: your email is not verified yet; deployment access requires a verified account.", Sty.WARNING)
    return 0


def _cmd_status(argv: Sequence[str]) -> int:
    json_out = "--json" in argv
    config_root = default_config_root()
    state = load_state(config_root)
    store = _credential_store(argv, quiet=True)
    cred_store = GlobalCredentialStore(store)
    creds = cred_store.load()

    if creds is None:
        if json_out:
            print(json.dumps({"signed_in": False}))
        else:
            printf("Signed out of RemoteRF Global.", Sty.GRAY)
            printf("Run: ", Sty.GRAY, "remoterf global login", Sty.CYAN)
        return 0

    base_url, allow_http = _resolve_global_url(argv)
    api = GlobalApiClient(base_url or state.global_base_url, allow_insecure_http=allow_http, client_name=_CLIENT_NAME)
    auth = AuthenticatedGlobalClient(api, cred_store)
    me = None
    warning = None
    try:
        me = auth.me()
    except GlobalClientError as exc:
        warning = str(exc)
    finally:
        api.close()

    payload = {
        "signed_in": True,
        "global_issuer": state.global_base_url,
        "user_id": (me.id if me else state.user_id),
        "user_email": (me.email if me else state.user_email),
        "email_verified": (me.email_verified if me else None),
        "access_token_expires_at": creds.expires_at.isoformat(),
        "credential_store": store.mode.value,
        "active_deployment_slug": state.active_deployment_slug,
        "active_deployment_display_name": state.active_deployment_display_name,
    }
    if warning:
        payload["warning"] = warning

    if json_out:
        print(json.dumps(payload))
        return 0

    printf("Signed in to RemoteRF Global", (Sty.BOLD, Sty.GREEN))
    printf("  Issuer        : ", Sty.GRAY, state.global_base_url, Sty.CYAN)
    if payload["user_id"]:
        printf("  User UUID     : ", Sty.GRAY, payload["user_id"], Sty.CYAN)
    if payload["user_email"]:
        printf("  Email         : ", Sty.GRAY, payload["user_email"], Sty.CYAN)
    if payload["email_verified"] is not None:
        printf("  Verified      : ", Sty.GRAY, "yes" if payload["email_verified"] else "no", Sty.CYAN)
    printf("  Token expires : ", Sty.GRAY, payload["access_token_expires_at"], Sty.CYAN)
    printf("  Cred storage  : ", Sty.GRAY, payload["credential_store"], Sty.CYAN)
    if state.active_deployment_slug:
        printf(
            "  Active deploy : ", Sty.GRAY,
            f"{state.active_deployment_display_name} ({state.active_deployment_slug})", Sty.CYAN,
        )
    else:
        printf("  Active deploy : ", Sty.GRAY, "(none -- direct mode)", Sty.GRAY)
    if warning:
        printf(f"  Warning: {warning}", Sty.WARNING)
    return 0


def _cmd_logout(argv: Sequence[str]) -> int:
    config_root = default_config_root()
    store = _credential_store(argv, quiet=True)
    cred_store = GlobalCredentialStore(store)
    state = load_state(config_root)

    base_url, allow_http = _resolve_global_url(argv)
    api = GlobalApiClient(base_url or state.global_base_url, allow_insecure_http=allow_http, client_name=_CLIENT_NAME)
    auth = AuthenticatedGlobalClient(api, cred_store)
    try:
        auth.logout()  # revokes server-side + clears local credentials regardless of network result
    finally:
        api.close()

    # Clear every cached per-deployment local session this store knows
    # about (not just the active one) -- but never touch direct-mode .env.
    sessions = LocalSessionStore(store)
    deployments_dir = config_root / "global" / "deployments"
    if deployments_dir.is_dir():
        for entry in deployments_dir.iterdir():
            if entry.is_dir():
                sessions.clear(entry.name)

    save_state(config_root, state.cleared_user())

    printf("Signed out of RemoteRF Global.", (Sty.BOLD, Sty.GREEN))
    printf("Direct/LAN RemoteRF configuration was not modified.", Sty.GRAY)
    return 0


# --- `remoterf deployments ...` ---------------------------------------------


def print_deployments_help() -> None:
    printf("RemoteRF Global deployment discovery:", (Sty.BOLD, Sty.BLUE))
    printf("  remoterf deployments", Sty.CYAN, "                  List public deployments", Sty.DEFAULT)
    printf("  remoterf deployments show <slug>", Sty.CYAN, "      Show one deployment", Sty.DEFAULT)
    printf("  remoterf deployments resources <slug>", Sty.CYAN, " List its public resource catalog", Sty.DEFAULT)
    printf("  --json", Sty.CYAN, "  Machine-readable output", Sty.DEFAULT)


def cmd_deployments(argv: Sequence[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print_deployments_help()
        return 0
    if argv and argv[0] == "show":
        return _run(_cmd_deployments_show, argv[1:])
    if argv and argv[0] == "resources":
        return _run(_cmd_deployments_resources, argv[1:])
    return _run(_cmd_deployments_list, argv)


def _cmd_deployments_list(argv: Sequence[str]) -> int:
    json_out = "--json" in argv
    api = _api_client(argv)
    try:
        deployments = api.list_deployments()
    finally:
        api.close()

    if json_out:
        print(json.dumps([d.model_dump() for d in deployments]))
        return 0

    if not deployments:
        printf("No public RemoteRF Global deployments found.", Sty.GRAY)
        return 0

    printf(f"{'SLUG':<14}{'NAME':<26}{'STATUS':<10}{'RESOURCES':<11}{'PROTOCOL'}", (Sty.BOLD, Sty.BLUE))
    for d in deployments:
        status = "online" if d.online else "offline"
        printf(f"{d.slug:<14}{d.display_name:<26}{status:<10}{d.resource_count:<11}{d.protocol_version}", Sty.DEFAULT)
    return 0


def _cmd_deployments_show(argv: Sequence[str]) -> int:
    if not argv:
        print("Usage: remoterf deployments show <slug>")
        return 2
    slug = argv[0]
    json_out = "--json" in argv
    api = _api_client(argv)
    try:
        deployment = api.get_deployment(slug)
    finally:
        api.close()

    if json_out:
        print(json.dumps(deployment.model_dump()))
        return 0

    printf(f"{deployment.display_name} ", (Sty.BOLD, Sty.BLUE), f"({deployment.slug})", Sty.GRAY)
    printf("  Status      : ", Sty.GRAY, "online" if deployment.online else "offline", Sty.CYAN)
    printf("  Protocol    : ", Sty.GRAY, deployment.protocol_version, Sty.CYAN)
    printf("  Resources   : ", Sty.GRAY, str(deployment.resource_count), Sty.CYAN)
    if deployment.description:
        printf("  Description : ", Sty.GRAY, deployment.description, Sty.DEFAULT)
    return 0


def _cmd_deployments_resources(argv: Sequence[str]) -> int:
    if not argv:
        print("Usage: remoterf deployments resources <slug>")
        return 2
    slug = argv[0]
    json_out = "--json" in argv
    api = _api_client(argv)
    try:
        resources = api.list_resources(slug)
    finally:
        api.close()

    if json_out:
        print(json.dumps([r.model_dump() for r in resources]))
        return 0

    if not resources:
        printf(f"No public resources exported by {slug!r}.", Sty.GRAY)
        return 0

    printf(f"Public resources exported by {slug!r}:", (Sty.BOLD, Sty.BLUE))
    for r in resources:
        printf("  ", Sty.DEFAULT, r.display_name, Sty.CYAN, "  ", Sty.DEFAULT, r.resource_ref, Sty.GRAY)
    return 0


# --- `remoterf use <slug>|direct` -------------------------------------------


def cmd_use(argv: Sequence[str]) -> int:
    if not argv:
        print("Usage: remoterf use <slug>|direct")
        return 2
    return _run(_cmd_use, argv)


def _cmd_use(argv: Sequence[str]) -> int:
    target = argv[0]
    config_root = default_config_root()

    if target == "direct":
        state = load_state(config_root)
        save_state(config_root, state.cleared_active_deployment())
        printf("Switched to direct RemoteRF mode.", (Sty.BOLD, Sty.GREEN))
        direct = load_direct_profile(config_root)
        if direct is not None:
            printf("  Endpoint: ", Sty.GRAY, direct.grpc_endpoint, Sty.CYAN)
        else:
            printf("  No direct configuration found yet. Run: remoterf --config --addr <host:port>", Sty.GRAY)
        return 0

    slug = target
    store = _credential_store(argv)
    cred_store = GlobalCredentialStore(store)
    if cred_store.load() is None:
        raise NotLoggedInError("Not logged in to RemoteRF Global. Run: remoterf global login")

    state = load_state(config_root)
    base_url, allow_http = _resolve_global_url(argv)
    api = GlobalApiClient(
        base_url or state.global_base_url,
        allow_insecure_http=allow_http,
        client_name=_CLIENT_NAME,
    )
    auth = AuthenticatedGlobalClient(api, cred_store)
    manager = GlobalSessionManager(config_root=config_root, api=auth, local_sessions=LocalSessionStore(store))
    try:
        result = manager.use_deployment(slug)
    finally:
        api.close()

    save_state(
        config_root,
        state.with_(
            active_deployment_id=result.deployment.id,
            active_deployment_slug=result.deployment.slug,
            active_deployment_display_name=result.deployment.display_name,
        ),
    )

    printf("Selected deployment: ", (Sty.BOLD, Sty.GREEN), result.deployment.display_name, Sty.CYAN)
    printf("  Route     : ", Sty.GRAY, result.route.kind, Sty.CYAN)
    printf("  Endpoint  : ", Sty.GRAY, f"{result.route.grpc_host}:{result.route.grpc_port}", Sty.CYAN)
    printf("  RemoteRF authentication: ", Sty.GRAY, "established", Sty.GREEN)
    return 0
