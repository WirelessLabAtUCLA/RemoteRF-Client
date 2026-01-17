# src/remoteRF/core/remoterf_config.py

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Tuple

from .cert_fetcher import fetch_and_save_ca_cert  # your earlier module

# -----------------------------
# Local config locations
# -----------------------------
def _config_root() -> Path:
    # Keep it simple and consistent with earlier suggestions.
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
    """
    Writes/overwrites a simple dotenv-style file.
    (We overwrite the whole file for simplicity and correctness.)
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for k, v in kv.items():
        # Quote only if needed; keep simple and safe.
        if any(c.isspace() for c in v) or any(c in v for c in ['"', "'"]):
            v = v.replace('"', '\\"')
            lines.append(f'{k}="{v}"')
        else:
            lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="remoterf-config",
        add_help=True,
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            "Configure RemoteRF to talk to a server.\n"
            "Fetches the server CA certificate and saves the gRPC target locally."
        ),
    )

    parser.add_argument(
        "addr",
        nargs="?",
        help="Server address in host:port form. Example: 164.67.195.207:61005",
    )

    args = parser.parse_args()

    if not args.addr:
        print(
            "\nError: missing required argument: host:port\n\n"
            "Usage:\n"
            "  remoterf-config <host:port>\n\n"
            "Example:\n"
            "  remoterf-config 164.67.195.207:61005\n"
        )
        sys.exit(2)

    try:
        host, grpc_port = _parse_hostport(args.addr)
    except Exception as e:
        print(f"Error: invalid addr '{args.addr}': {e}", file=sys.stderr)
        sys.exit(2)

    # Single-arg policy
    cert_port = grpc_port + 1      # cert-provider convention
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
        sys.exit(1)

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

if __name__ == "__main__":
    main()
