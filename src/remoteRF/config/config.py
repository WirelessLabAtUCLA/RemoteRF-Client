# src/remoteRF/core/remoterf_config.py

from __future__ import annotations

import os
import sys
import shutil
from pathlib import Path
from typing import Optional, Sequence, Tuple
import argparse

from .cert_fetcher import fetch_and_save_ca_cert



# -----------------------------
# Local config locations
# -----------------------------
def _config_root() -> Path:
    return Path(os.path.expanduser("~")) / ".config" / "remoterf"

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

def configure(host: str, port: int, cert_port: int) -> int:
    # Basic validation
    host = (host or "").strip()
    if not host:
        print("Error: host is empty", file=sys.stderr)
        return 2
    if port <= 0 or port > 65535:
        print("Error: port out of range", file=sys.stderr)
        return 2

    grpc_port = int(port)
    cert_port = int(cert_port)

    profile = "default"
    timeout_sec = 3.0
    overwrite = True

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

    print("RemoteRF configured successfully.")
    print(f"  gRPC target : {host}:{grpc_port}")
    print(f"  cert port   : {host}:{cert_port}")
    print(f"  CA cert     : {ca_out}")
    print(f"  env file    : {env_file}")

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