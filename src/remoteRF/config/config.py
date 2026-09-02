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

# src/remoteRF/core/remoterf_config.py

from __future__ import annotations

import os
import socket
import sys
import shutil
from pathlib import Path
from typing import Optional, Sequence, Tuple
import argparse

from ..common.utils import Sty, printf
from .cert_fetcher import fetch_and_save_ca_cert

DEFAULT_TOS_NOTICE = (
    "RemoteRF 2025\n"
    "Terms of Service Acknowledgement\n"
    "\n"
    "By continuing, you confirm that you have reviewed and agree to the\n"
    "RemoteRF Terms of Service and understand that use of the client and\n"
    "service is at your own risk. RemoteRF is not liable for losses or\n"
    "damages arising from use of the platform.\n"
    "\n"
    "Terms of Service: https://remoterf.net/tos\n"
)

# -----------------------------
# Local config locations
# -----------------------------
def _config_root() -> Path:
    return Path(os.path.expanduser("~")) / ".config" / "remoterf-client"

def _env_path() -> Path:
    return _config_root() / ".env"

def _certs_dir() -> Path:
    return _config_root() / "certs"

def _parse_hostport(s: str) -> Tuple[str, int]:
    s = s.strip()
    if "://" in s:
        s = s.split("://", 1)[1]

    if ":" not in s:
        raise ValueError("Expected format host:port")

    host, port_str = s.rsplit(":", 1)
    host = host.strip()
    port = int(port_str.strip())
    if not host:
        raise ValueError("Host is empty")
    if port <= 0 or port > 65535:
        raise ValueError("Port out of range")
    return host, port


def _bare_host(host: str) -> str:
    """Strip IPv6 literal brackets (e.g. '[::1]' -> '::1') for name/address
    resolution and raw-socket use. IPv4 addresses and DNS hostnames
    (including plain multi-label names such as ucla.global.remoterf.net)
    are returned unchanged.
    """
    host = host.strip()
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def _host_resolves(host: str) -> bool:
    """Return True if `host` (an IPv4/IPv6 literal or DNS hostname) resolves."""
    try:
        socket.getaddrinfo(_bare_host(host), None)
        return True
    except socket.gaierror:
        return False

def _write_env_kv(path: Path, kv: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for k, v in kv.items():
        if any(c.isspace() for c in v) or any(c in v for c in ['"', "'"]):
            v = v.replace('"', '\\"')
            lines.append(f'{k}="{v}"')
        else:
            lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def _confirm_wipe(root: Path) -> bool:
    prompt = (
        f"This will permanently delete ALL RemoteRF config at:\n"
        f"  {root}\n\n"
        f"Type 'wipe' to confirm: "
    )
    try:
        return input(prompt).strip().lower() == "wipe"
    except KeyboardInterrupt:
        print("\nCancelled.")
        return False

def _wipe_config(root: Path) -> None:
    if not root.exists():
        print(f"No config found at: {root}")
        return
    if not root.is_dir():
        raise RuntimeError(f"Config root exists but is not a directory: {root}")
    shutil.rmtree(root)
    print(f"Wiped RemoteRF config: {root}")


def _tos_notice_path() -> Path:
    return Path(__file__).resolve().parents[1] / "common" / "tos_notice.txt"


def _read_tos_notice() -> str:
    try:
        text = _tos_notice_path().read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_TOS_NOTICE.strip()
    return text or DEFAULT_TOS_NOTICE.strip()


def _print_separator() -> None:
    printf("=" * 60, Sty.GRAY)


def _print_tos_notice() -> None:
    for i, line in enumerate(_read_tos_notice().splitlines()):
        stripped = line.strip()
        if not stripped:
            print()
            continue

        if i == 0:
            printf(stripped, (Sty.BOLD, Sty.BLUE))
            continue

        if i == 1:
            printf(stripped, (Sty.BOLD, Sty.MAGENTA))
            continue

        if stripped.lower().startswith("terms of service:"):
            label, value = stripped.split(":", 1)
            printf(f"{label}: ", (Sty.BOLD, Sty.DEFAULT), value.strip(), (Sty.CYAN, Sty.UNDERLINE))
            continue

        printf(stripped, Sty.DEFAULT)


def _print_config_summary(host: str, grpc_port: int, cert_port: int, ca_out: Path, env_file: Path) -> None:
    _print_separator()
    printf("Configuration Complete!", (Sty.BOLD, Sty.GREEN))
    printf("- Details:", Sty.BOLD)
    printf("  gRPC target: ", (Sty.BOLD, Sty.DEFAULT), f"{host}:{grpc_port}", Sty.CYAN)
    printf("  Cert port  : ", (Sty.BOLD, Sty.DEFAULT), f"{host}:{cert_port}", Sty.CYAN)
    printf("  CA cert    : ", (Sty.BOLD, Sty.DEFAULT), f"{ca_out}", Sty.GRAY)
    printf("  Env file   : ", (Sty.BOLD, Sty.DEFAULT), f"{env_file}", Sty.GRAY)
    _print_separator()


def _confirm_tos() -> bool:
    _print_separator()
    _print_tos_notice()
    _print_separator()
    try:
        reply = input("Continue with configuration? [y/N]: ").strip().lower()
    except KeyboardInterrupt:
        print("\nConfiguration cancelled.")
        return False
    except EOFError:
        print("\nConfiguration cancelled.")
        return False
    print()
    return reply in {"y", "yes"}

def configure(host: str, port: int, cert_port: int) -> int:
    # Basic validation
    host = (host or "").strip()
    if not host:
        print("Error: host is empty", file=sys.stderr)
        return 2
    if port <= 0 or port > 65535:
        print("Error: port out of range", file=sys.stderr)
        return 2

    if not _host_resolves(host):
        print(
            "Error: Could not resolve RemoteRF server hostname:\n"
            f"  {host}\n"
            "Check the address and your network/DNS connection, then re-run:\n"
            "  remoterf --config --addr <host:port>",
            file=sys.stderr,
        )
        return 1

    grpc_port = int(port)
    cert_port = int(cert_port)

    profile = "default"
    timeout_sec = 3.0
    overwrite = True

    if not _confirm_tos():
        print("Configuration cancelled. Accept the Terms of Service to continue.")
        return 1

    certs_dir = _certs_dir()
    certs_dir.mkdir(parents=True, exist_ok=True)
    ca_out = certs_dir / f"{profile}.crt"

    fetched_ok = fetch_and_save_ca_cert(
        host,
        cert_port,
        out_path=ca_out,
        timeout_sec=timeout_sec,
        overwrite=overwrite,
    )
    if not fetched_ok:
        print(f"Failed to fetch CA cert from {host}:{cert_port}.", file=sys.stderr)
        return 1

    env_file = _env_path()
    _write_env_kv(env_file, {
        "REMOTERF_ADDR": f"{host}:{grpc_port}",
        "REMOTERF_CA_CERT": str(ca_out),
        "REMOTERF_PROFILE": profile,
    })

    _print_config_summary(host, grpc_port, cert_port, ca_out, env_file)

def wipe_config(*, yes: bool = False) -> int:
    """
    Optional helper if you want wipe behavior without argparse.
    """
    root = _config_root()
    if not yes and not _confirm_wipe(root):
        print("Wipe aborted.")
        return 1
    try:
        _wipe_config(root)
        return 0
    except Exception as e:
        print(f"Error wiping config: {e}", file=sys.stderr)
        return 1
