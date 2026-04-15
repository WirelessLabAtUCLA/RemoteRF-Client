"""
IDL driver code-generator.

When a device is reserved, call install_driver(device_id) to:
  1. Fetch the IDL schema from the server via IDL:get_drivers.
  2. Generate a <device_type>_remote.py file that mirrors the style of the
     hand-written adalm_pluto/pluto_remote.py.
  3. Write an __init__.py so the package is importable.

After install_driver() the user can do:

    from remoteRF.drivers.pluto import *
    sdr = adi.Pluto(token)

On first device init, pass device_id to also check for schema updates:

    sdr = adi.Pluto(token, device_id=1)
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.grpc_client import rpc_client
from ..common.utils import map_arg, unmap_arg


# ─────────────────────────────────────────────────────────────────────────────
# IDL fetch
# ─────────────────────────────────────────────────────────────────────────────

def fetch_idl(*, token: str = None, device_id: int = None, device_name: str = None) -> dict:
    """
    Fetch the IDL schema from the server.

    Pass one of: token (preferred — uses the reservation token to resolve
    the device), device_id, or device_name.

    Returns the parsed schema dict:
        {
            "schema_version": "1.0",
            "device_type":    "pluto",
            "driver_version": "0.0.1",
            "getters":  {"get_tx_lo": {"doc": "..."}, ...},
            "setters":  {"set_tx_lo": {"doc": "..."}, ...},
            "calls":    {"call_rx":   {"doc": "..."}, ...},
            "schema_hash": "sha256:...",
        }
    """
    args: dict = {}
    if token is not None:
        args['token'] = map_arg(str(token))
    elif device_id is not None:
        args['device_id'] = map_arg(int(device_id))
    elif device_name is not None:
        args['device_name'] = map_arg(str(device_name))
    else:
        raise ValueError("fetch_idl requires token, device_id, or device_name")

    resp = rpc_client(function_name="IDL:get_drivers", args=args)

    if 'error' in resp.results:
        raise RuntimeError(f"IDL fetch failed: {unmap_arg(resp.results['error'])}")

    return json.loads(unmap_arg(resp.results['schema']))


# ─────────────────────────────────────────────────────────────────────────────
# Code generator
# ─────────────────────────────────────────────────────────────────────────────

# Static helper block written verbatim into every generated driver file.
# Regular string (not f-string) — {_PREFIX}/{prop}/{e} are literal text
# that become f-strings inside the *generated* file.
_HELPERS = '''\
from ...core.grpc_client import rpc_client
from ...common.utils import map_arg, unmap_arg

_NO_ARG = object()


def _try_get(prop, token):
    return unmap_arg(rpc_client(
        function_name=f"{_PREFIX}:{prop}:GET",
        args={'a': map_arg(token)},
    ).results[prop])


def _try_set(prop, value, token):
    rpc_client(
        function_name=f"{_PREFIX}:{prop}:SET",
        args={prop: map_arg(value), 'a': map_arg(token)},
    )


def _try_call(prop, token, arg=_NO_ARG):
    if arg is _NO_ARG:
        resp = rpc_client(
            function_name=f"{_PREFIX}:{prop}:CALL0",
            args={'a': map_arg(token)},
        )
    else:
        resp = rpc_client(
            function_name=f"{_PREFIX}:{prop}:CALL1",
            args={'a': map_arg(token), 'arg1': map_arg(arg)},
        )
    result = resp.results.get(prop)
    return unmap_arg(result) if result is not None else None
'''


def _codegen(schema: dict) -> str:
    """Return the source code of a complete driver module for this schema."""
    device_type    = schema.get("device_type", "unknown")
    driver_version = schema.get("driver_version", "?")
    schema_hash    = schema.get("schema_hash", "?")
    rpc_prefix     = device_type[0].upper() + device_type[1:]   # "pluto" → "Pluto"
    class_name     = rpc_prefix

    getters: dict = schema.get("getters", {})   # "get_tx_lo" → {"doc": ...}
    setters: dict = schema.get("setters", {})   # "set_tx_lo" → {"doc": ...}
    calls:   dict = schema.get("calls",   {})   # "call_rx"   → {"doc": ...}

    # Strip prefixes to get bare property / method names.
    getter_map = {k[4:]: v for k, v in getters.items() if k.startswith("get_")}
    setter_map = {k[4:]: v for k, v in setters.items() if k.startswith("set_")}
    call_map   = {(k[5:] if k.startswith("call_") else k): v for k, v in calls.items()}

    lines: list[str] = []

    # ── File header ───────────────────────────────────────────────────
    lines += [
        "# Auto-generated from IDL schema — do not edit by hand.",
        f"# device_type: {device_type}  "
        f"driver_version: {driver_version}  "
        f"schema_hash: {schema_hash}",
        "",
        f'_PREFIX = "{rpc_prefix}"',
        f'_SCHEMA_HASH = "{schema_hash}"',
        "",
        _HELPERS,
    ]

    # ── Class ─────────────────────────────────────────────────────────
    lines += [
        f"class {class_name}:",
        "",
        "    def __init__(self, token: str):",
        "        self.token = token",
        "        from ..dynamic_device import install_driver_if_stale",
        "        install_driver_if_stale(token=token, current_hash=_SCHEMA_HASH)",
        '        _try_call("ip", token)',
        "",
    ]

    # ── Properties ────────────────────────────────────────────────────
    all_props = sorted(set(getter_map) | set(setter_map))
    for prop in all_props:
        doc = (getter_map.get(prop) or setter_map.get(prop) or {}).get("doc", "")
        has_getter = prop in getter_map
        has_setter = prop in setter_map

        lines.append("    @property")
        lines.append(f"    def {prop}(self):")
        if doc:
            lines.append(f'        """{doc}"""')
        if has_getter:
            lines.append(f'        return _try_get("{prop}", self.token)')
        else:
            lines.append(f'        raise AttributeError("{prop} is write-only")')
        lines.append("")

        if has_setter:
            lines.append(f"    @{prop}.setter")
            lines.append(f"    def {prop}(self, value):")
            lines.append(f'        _try_set("{prop}", value, self.token)')
            lines.append("")

    # ── Call methods ──────────────────────────────────────────────────
    for method, info in sorted(call_map.items()):
        doc = info.get("doc", "")
        lines.append(f"    def {method}(self, _v=_NO_ARG):")
        if doc:
            lines.append(f'        """{doc}"""')
        lines.append(f'        return _try_call("{method}", self.token, _v)')
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# File writer (shared by install_driver and install_driver_if_stale)
# ─────────────────────────────────────────────────────────────────────────────

# drivers/ lives next to this file.
_DRIVERS_DIR = Path(__file__).parent


def _write_driver_files(schema: dict) -> Path:
    device_type = schema.get("device_type", "unknown")
    class_name = device_type[0].upper() + device_type[1:]  # "pluto" → "Pluto"
    pkg_dir = _DRIVERS_DIR / device_type
    pkg_dir.mkdir(exist_ok=True)
    (pkg_dir / f"{device_type}_remote.py").write_text(_codegen(schema), encoding="utf-8")
    (pkg_dir / "__init__.py").write_text(
        f"from . import {device_type}_remote as adi\n"
        f"from .{device_type}_remote import {class_name}\n",
        encoding="utf-8",
    )
    return pkg_dir


def _print_driver_cached(pkg_dir: Path) -> None:
    print("Device drivers updated successfully.")


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def install_driver(*, token: str = None, device_id: int = None, device_name: str = None) -> Path:
    """
    Fetch the IDL schema and write a driver package to disk.

    Creates:
        drivers/<device_type>/__init__.py
        drivers/<device_type>/<device_type>_remote.py

    Returns the path to the generated package directory.
    """
    schema = fetch_idl(token=token, device_id=device_id, device_name=device_name)
    pkg_dir = _write_driver_files(schema)
    _print_driver_cached(pkg_dir)
    return pkg_dir


def ensure_driver(*, token: str = None, device_id: int = None, device_name: str = None) -> None:
    """
    Install the driver if it isn't already on disk; skip if it is.

    Call this on any machine that needs to use the device but didn't perform
    the reservation (and therefore never had install_driver run automatically).
    Pass the reservation token — the server resolves which device it belongs to.

        from remoteRF.drivers import ensure_driver
        ensure_driver(token=token)

        from remoteRF.drivers.pluto import *
        sdr = adi.Pluto(token)

    One IDL:get_drivers RPC is always made (to resolve device_type and hash).
    Files are only written when missing or stale.
    """
    schema = fetch_idl(token=token, device_id=device_id, device_name=device_name)
    device_type = schema.get("device_type", "unknown")
    remote_path = _DRIVERS_DIR / device_type / f"{device_type}_remote.py"

    if not remote_path.exists():
        pkg_dir = _write_driver_files(schema)
        _print_driver_cached(pkg_dir)
        return

    # File exists — check staleness via the baked-in _SCHEMA_HASH line.
    current_hash = _read_schema_hash(remote_path)
    if current_hash != schema.get("schema_hash"):
        _write_driver_files(schema)
        print(
            f"Driver for '{device_type}' updated "
            f"(was {(current_hash or '?')[:16]}…, "
            f"now {schema.get('schema_hash','?')[:16]}…).\n"
            f"Reimport before continuing: from remoteRF.drivers.{device_type} import *"
        )


def _read_schema_hash(path: Path) -> str | None:
    """Extract _SCHEMA_HASH value from a generated driver file without importing it."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("_SCHEMA_HASH"):
                # _SCHEMA_HASH = "sha256:..."
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return None


def install_driver_if_stale(*, token: str, current_hash: str) -> bool:
    """
    Fetch the server schema hash via the reservation token and reinstall the
    driver only if it has changed since the file on disk was generated.

    Called automatically by every generated device __init__:

        sdr = adi.Pluto(token)

    Returns True if the driver was reinstalled, False if already up to date.
    If the driver was reinstalled, reimport the package before using the
    new class:

        from remoteRF.drivers.pluto import *
    """
    schema = fetch_idl(token=token)
    if schema.get("schema_hash") == current_hash:
        return False

    device_type = schema.get("device_type", "unknown")
    _write_driver_files(schema)
    print(
        f"Driver for '{device_type}' updated "
        f"(was {current_hash[:16]}…, now {schema.get('schema_hash','?')[:16]}…).\n"
        f"Reimport before continuing: from remoteRF.drivers.{device_type} import *"
    )
    return True
