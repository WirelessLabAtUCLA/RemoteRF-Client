"""Named deployment HOMEs, plus the optional RemoteRF Global identity.

A deployment-native account lives at exactly one HOME and that HOME owns it.
A Global-native account is a separate, complete identity that needs no HOME at
all. Neither is ever substituted for the other: changing *route* never changes
identity, and a failed login never falls through to a different account.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from remoterf_federation_core import (
    ValidationError,
    strict_json,
    validate_capabilities,
)

from .state import NAMESPACE, origin, private_write, root

HOMES_FILE = "homes-v1.json"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$")
GLOBAL_DOMAIN = "global.remoterf.net"

# A LAN route is only worth waiting on for as long as a LAN takes to answer.
LAN_CONNECT_TIMEOUT = 1.0
ROUTE_CONNECT_TIMEOUT = 5.0

_FIELDS = ("global_account", "homes", "last_target")


def _empty() -> dict:
    # Built fresh every call: a shared literal would let one caller's saved
    # homes leak into every other caller's view of an empty config.
    return {"global_account": None, "homes": {}, "last_target": None}


def homes_path() -> Path:
    return root() / HOMES_FILE


def load_state() -> dict:
    path = homes_path()
    if not path.exists():
        return _empty()
    data = strict_json(path.read_bytes())
    if type(data) is not dict or set(data) != set(_FIELDS):
        raise ValidationError("Invalid saved homes")
    return data


def save_state(data: dict) -> None:
    private_write(homes_path(), data)


def homes() -> dict:
    return load_state()["homes"]


def load_home(name: str) -> dict:
    home = homes().get(name)
    if home is None:
        raise ValidationError(f"No saved home named {name!r}")
    return home


def has_global_account() -> bool:
    return load_state()["global_account"] is not None


def remember_global_account(selected_origin: str, deployment_id: str) -> None:
    data = load_state()
    data["global_account"] = {
        "origin": origin(selected_origin),
        "deployment_id": deployment_id,
    }
    save_state(data)


def save_home(name: str, document: dict) -> dict:
    """Record one deployment HOME from its own discovery document."""
    if not NAME_RE.match(name):
        raise ValidationError("A home name must be a slug")
    document = validate_capabilities(document)
    data = load_state()
    home = {
        "deployment_id": document["deployment_id"],
        "display_name": document["display_name"],
        "transport": document["account_transport"],
        "routes": document["routes"],
        "cert_sha256": document["cert_sha256"],
    }
    existing = data["homes"].get(name)
    if existing and existing["deployment_id"] != home["deployment_id"]:
        raise ValidationError(
            f"Home {name!r} already belongs to a different deployment"
        )
    data["homes"][name] = home
    save_state(data)
    return home


def forget_home(name: str) -> None:
    data = load_state()
    data["homes"].pop(name, None)
    if (data["last_target"] or {}).get("name") == name:
        data["last_target"] = None
    save_state(data)


def last_target() -> dict | None:
    return load_state()["last_target"]


def set_last_target(kind: str, name: str | None = None) -> None:
    if kind not in ("global", "home"):
        raise ValidationError("A target is either the Global account or a home")
    if (kind == "home") != (name is not None):
        raise ValidationError("Only a home target carries a name")
    data = load_state()
    data["last_target"] = {"kind": kind, "name": name}
    save_state(data)


# -----------------------------
# Enrollment codes
# -----------------------------

def parse_code(value: str) -> tuple[str | None, str]:
    """Split ``<host>/<code>`` or ``<name>/<code>``; a bare code has no host.

    A short name expands to ``<name>.global.remoterf.net``; anything with a dot
    or a colon is already a host and is used as typed.
    """
    value = value.strip()
    if "/" not in value:
        return None, value
    locator, _, code = value.rpartition("/")
    locator, code = locator.strip(), code.strip()
    if not locator or not code:
        raise ValidationError("Expected <deployment>/<code>")
    if "." not in locator and ":" not in locator:
        if not NAME_RE.match(locator):
            raise ValidationError("Deployment name is not a valid slug")
        locator = f"{locator}.{GLOBAL_DOMAIN}"
    return locator, code


def home_name_for(host: str) -> str:
    """The saved name for a host: its deployment slug when it is a Global one."""
    bare = host.split(":")[0].lower()
    if bare.endswith("." + GLOBAL_DOMAIN):
        return bare[: -len("." + GLOBAL_DOMAIN)]
    return re.sub(r"[^a-z0-9-]+", "-", bare).strip("-") or "default"


def discover(host: str, *, transport=None) -> dict:
    """Fetch and validate a deployment's public discovery document."""
    from .http import JsonTransport

    transport = transport or JsonTransport(origin(host))
    try:
        return validate_capabilities(
            transport.request("GET", "/.well-known/remoterf-deployment")
        )
    finally:
        close = getattr(transport, "close", None)
        if close is not None:
            close()


# -----------------------------
# Routes
# -----------------------------

def _probe(route: dict, timeout: float) -> bytes | None:
    """Fetch the deployment's CA over this route, or None if it is unreachable."""
    from ..config.cert_fetcher import fetch_ca_bytes, looks_like_pem_cert

    try:
        certificate = fetch_ca_bytes(
            route["host"], route["cert_port"], timeout_sec=timeout
        )
    except Exception:  # noqa: BLE001 - any failure just means "try the next route"
        return None
    return certificate if looks_like_pem_cert(certificate) else None


def select_route(home: dict) -> tuple[dict, bytes]:
    """The first working route for this HOME, and the certificate it served.

    Routes are tried in the order the deployment advertised them — LAN first,
    on a short timeout, so a client that is not on the LAN falls through to the
    relay quickly. Fallback never leaves this HOME.
    """
    pin = home.get("cert_sha256")
    failures = []
    for route in home["routes"]:
        timeout = LAN_CONNECT_TIMEOUT if route["kind"] == "lan" else ROUTE_CONNECT_TIMEOUT
        certificate = _probe(route, timeout)
        if certificate is None:
            failures.append(route["kind"])
            continue
        digest = hashlib.sha256(certificate).hexdigest()
        if pin and digest != pin:
            # A route that serves the wrong certificate is not a fallback
            # candidate; it is a different server wearing this name.
            raise ValidationError(
                f"The {route['kind']} route for this deployment served an "
                "unexpected certificate"
            )
        return route, certificate
    raise ValidationError(
        "Could not reach this deployment on any route ("
        + ", ".join(failures or ["none advertised"])
        + ")"
    )


def activate(name: str, home: dict, route: dict, certificate: bytes) -> Path:
    """Point the client's device transport at this HOME over this route.

    Writes the same direct-connection profile a manually configured client
    uses, so every post-login command behaves exactly as it does on a direct
    connection to that deployment.
    """
    from ..config.config import _certs_dir, _write_env_kv, _env_path

    certs = _certs_dir()
    certs.mkdir(parents=True, exist_ok=True)
    ca_path = certs / f"{name}.crt"
    ca_path.write_bytes(certificate)
    _write_env_kv(
        _env_path(),
        {
            "REMOTERF_ADDR": f"{route['host']}:{route['port']}",
            "REMOTERF_CA_CERT": str(ca_path),
            "REMOTERF_PROFILE": name,
        },
    )
    from .state import select_direct

    # No need to tear the channel down: the connection cache keys on the
    # endpoint and the CA file, so it replaces a stale channel by itself.
    select_direct()
    set_last_target("home", name)
    return ca_path


def connect(name: str) -> dict:
    """Select a saved HOME and bring its transport up. Returns the route used."""
    home = load_home(name)
    route, certificate = select_route(home)
    activate(name, home, route, certificate)
    return route


def register_home(host: str) -> tuple[str, dict, dict]:
    """Learn a deployment from its code, save it, and connect to it."""
    document = discover(host)
    name = home_name_for(host)
    home = save_home(name, document)
    route = connect(name)
    return name, home, route


def migrate_legacy_target() -> str | None:
    """Adopt a pre-homes single-target config as a HOME named "default"."""
    from .direct import resolve_active_profile

    data = load_state()
    if data["homes"]:
        return None
    profile = resolve_active_profile()
    if profile is None or not profile.ca_path.exists():
        return None
    host, _, port = profile.grpc_endpoint.rpartition(":")
    data["homes"]["default"] = {
        "deployment_id": None,
        "display_name": profile.grpc_endpoint,
        "transport": "grpc",
        "routes": [
            {
                "kind": "direct",
                "host": host,
                "port": int(port),
                "cert_port": int(port) + 1,
            }
        ],
        "cert_sha256": hashlib.sha256(profile.ca_path.read_bytes()).hexdigest(),
    }
    if data["last_target"] is None:
        data["last_target"] = {"kind": "home", "name": "default"}
    save_state(data)
    return "default"


# -----------------------------
# Stored logins (direct / gRPC homes)
# -----------------------------
#
# A deployment issues a session token at login (never the password) and the
# client keeps that, in an owner-only file under the client config (or the OS
# keyring, if opted in -- see state.keyring_if_enabled). Nothing here may ever
# stop a login: any failure reads as "nothing stored" and the shell simply asks.

def _login_key(name: str) -> str:
    return f"home-login:{name}"


def _login_file(name: str) -> Path:
    return root() / "credentials" / "homes" / f"{name}.json"


def _keyring():
    from .state import keyring_if_enabled

    return keyring_if_enabled()


def remember_login(name: str, username: str, secret: str) -> None:
    value = {"username": username, "secret": secret}
    try:
        ring = _keyring()
        if ring is not None:
            import json

            ring.set_password(NAMESPACE, _login_key(name), json.dumps(value))
            _login_file(name).unlink(missing_ok=True)
            return
        private_write(_login_file(name), value)
    except Exception:  # noqa: BLE001 - not remembering is fine; failing login is not
        pass


def recall_login(name: str) -> dict | None:
    try:
        raw = None
        ring = _keyring()
        if ring is not None:
            raw = ring.get_password(NAMESPACE, _login_key(name))
            raw = raw.encode() if raw else None
        if raw is None:
            path = _login_file(name)
            if not path.exists():
                return None
            raw = path.read_bytes()
        value = strict_json(raw)
        if (
            type(value) is not dict
            or type(value.get("username")) is not str
            or type(value.get("secret")) is not str
        ):
            return None
        return value
    except Exception:  # noqa: BLE001 - unreadable is the same as absent
        return None


def forget_login(name: str) -> None:
    try:
        ring = _keyring()
        if ring is not None:
            ring.delete_password(NAMESPACE, _login_key(name))
    except Exception:  # noqa: BLE001
        pass
    try:
        _login_file(name).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
