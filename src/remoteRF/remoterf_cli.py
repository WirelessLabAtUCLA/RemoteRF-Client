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

# src/remoteRF/cli.py
from __future__ import annotations

import argparse
import sys
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
from typing import Optional, Sequence

from .common.utils import Sty, print_client_banner, printf

SERVER_CONNECT_TIMEOUT_SECONDS = 3.0


def _installed_version() -> str:
    try:
        return distribution_version("remoterf")
    except PackageNotFoundError:
        return "unknown"


def _connected_server(timeout_seconds: float = SERVER_CONNECT_TIMEOUT_SECONDS) -> str | None:
    """Return the configured endpoint only after its gRPC channel is ready."""
    import grpc

    from remoteRF.core.grpc_client import addr, channel

    try:
        grpc.channel_ready_future(channel).result(timeout=timeout_seconds)
    except (grpc.FutureTimeoutError, grpc.RpcError):
        return None
    return addr


def _print_server_unavailable() -> None:
    print_client_banner(_installed_version(), server="")
    printf("Please configure remoterf properly first.", Sty.DEFAULT)
    printf(
        "Run: ",
        Sty.GRAY,
        "remoterf --config --addr <host:port>",
        Sty.CYAN,
    )


def _config_root() -> Path:
    return Path.home() / ".config" / "remoterf-client"


def _env_path() -> Path:
    return _config_root() / ".env"

def _read_dotenv_kv(path: Path) -> dict[str, str]:
    """
    Tiny dotenv reader (KEY=VALUE lines). Good enough for your use-case.
    """
    out: dict[str, str] = {}
    if not path.exists():
        return out

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        out[k] = v
    return out

def _ensure_config_present() -> tuple[bool, str]:
    env_file = _env_path()
    if not env_file.exists():
        return (
            False,
            "RemoteRF is not configured.\n"
            "Run:\n"
            "  remoterf --config --addr <host:port>\n"
            "Example:\n"
            "  remoterf --config --addr 123.45.654.321:12321\n",
        )

    kv = _read_dotenv_kv(env_file)
    addr = kv.get("REMOTERF_ADDR", "").strip()
    ca = kv.get("REMOTERF_CA_CERT", "").strip()

    if not addr or not ca:
        return (
            False,
            "RemoteRF config is incomplete.\n"
            f"Expected REMOTERF_ADDR and REMOTERF_CA_CERT in:\n  {env_file}\n"
            "Fix by re-running:\n"
            "  remoterf --config --addr <host:port>\n",
        )

    ca_path = Path(ca).expanduser()
    if not ca_path.exists():
        return (
            False,
            "RemoteRF config points to a missing CA certificate.\n"
            f"REMOTERF_CA_CERT={ca}\n"
            "Fix by re-running:\n"
            "  remoterf --config --addr <host:port>\n",
        )

    return True, ""

def print_help() -> None:
    printf("RemoteRF CLI Help", (Sty.BOLD, Sty.BLUE))
    print()
    printf("Usage:", (Sty.BOLD, Sty.MAGENTA))
    printf("  remoterf", Sty.CYAN, "                          Show this help", Sty.DEFAULT)
    printf("  remoterf -h | --help", Sty.CYAN, "              Show this help", Sty.DEFAULT)
    print()
    printf("Commands:", (Sty.BOLD, Sty.MAGENTA))
    printf("  remoterf -l | --login", Sty.CYAN, "             Login / register", Sty.DEFAULT)
    printf("  remoterf -v | --version", Sty.CYAN, "           Print version", Sty.DEFAULT)
    print()
    printf("Config:", (Sty.BOLD, Sty.MAGENTA))
    printf("  remoterf -c | --config [options]", Sty.CYAN)
    printf("    -a, --addr, -addr <host:port>", Sty.CYAN, "   Set target server", Sty.DEFAULT)
    printf("    -w, --wipe, -wipe", Sty.CYAN, "               Delete all local config", Sty.DEFAULT)
    printf("    -y, --yes, -yes", Sty.CYAN, "                 Skip wipe confirmation", Sty.DEFAULT)
    print()
    printf("Examples:", (Sty.BOLD, Sty.MAGENTA))
    printf("  remoterf --login", Sty.GREEN)
    printf("  remoterf --version", Sty.GREEN)
    printf("  remoterf --config --addr 123.45.654.321:12321", Sty.GREEN)
    printf("  remoterf --config --wipe", Sty.GREEN)
    printf("  remoterf --config --wipe --yes", Sty.GREEN)

def main() -> int:
    argv = list(sys.argv[1:])

    # ---- Debug ----
    # print(f"argc={len(argv)}")
    # for i, a in enumerate(argv):
    #     print(f"argv[{i}] = {a!r}")

    if len(argv) == 0 or argv[0] in ("--help", "-help", "-h"):
        print_help()
        return 0

    if argv[0] in ("--login", "-login", "-l"):
        ok, _ = _ensure_config_present()
        if not ok:
            _print_server_unavailable()
            return 2

        if _connected_server() is None:
            _print_server_unavailable()
            return 2

        from remoteRF.core.acc_login import main as _
        return 0

    if argv[0] in ("--version", "-version", "-v"):
        from remoteRF.version import main as version_main
        
        print("RemoteRF version:", end=" ")
        version_main()
        return 0

    if argv[0] in ("--config", "-config", "-c"):
        from remoteRF.config.config import configure, wipe_config

        addr = None
        wipe = False
        yes = False

        i = 1
        while i < len(argv):
            tok = argv[i]

            if tok in ("--addr", "-a", "-addr"):
                if i + 1 >= len(argv):
                    print("ERROR: missing required argument after --addr/-a/-addr")
                    return 2
                addr = argv[i + 1]
                i += 2
                continue

            if tok in ("--wipe", "-w", "-wipe"):
                wipe = True
                i += 1
                continue

            if tok in ("--yes", "-y", "-yes"):
                yes = True
                i += 1
                continue

            print(f"ERROR: unknown config argument: {tok!r}")
            return 2

        # Mirror remoterf-config behavior:
        if wipe and addr is not None:
            print("ERROR: cannot use --wipe and --addr together")
            return 2

        if wipe:
            # wipe_config returns the proper exit code
            return int(wipe_config(yes=yes))

        if addr is not None:
            # parse host:port (minimal, strict)
            s = addr.strip()
            if "://" in s:
                s = s.split("://", 1)[1]
            if ":" not in s:
                print("ERROR: addr must be in 'host:port' form")
                return 2

            host, port_str = s.rsplit(":", 1)
            host = host.strip()
            try:
                port = int(port_str.strip())
            except Exception:
                print("ERROR: port must be an integer")
                return 2

            # configure returns the proper exit code
            cert_port = port + 1
            configure(host, port, cert_port)
            return 0

        # No args -> same behavior as remoterf-config missing addr (exit code 2)
        print(
            "\nError: missing required argument: host:port\n\n"
            "Usage:\n"
            "  remoterf --config --addr <host:port>\n"
            "  remoterf -c -a <host:port>\n"
            "  remoterf --config --wipe [--yes]\n\n"
            "Example:\n"
            "  remoterf --config --addr 123.45.678.901:12345\n"
        )
        return 2


    # fallback
    print(f"ERROR: unknown command: {argv[0]!r}")
    return 2
