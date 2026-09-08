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
    from remoteRF.deployment.state import load_target
    target = load_target()
    if target and target['transport'] == 'https-json':
        from remoteRF.deployment.backend import selected_backend
        backend = selected_backend()
        try:
            return backend.transport.origin
        finally:
            backend.close()
    import grpc

    from remoteRF.core.grpc_client import active_endpoint, get_active_channel

    try:
        channel = get_active_channel()
        grpc.channel_ready_future(channel).result(timeout=timeout_seconds)
        return active_endpoint()
    except (RuntimeError, grpc.FutureTimeoutError, grpc.RpcError):
        return None


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
    from remoteRF.deployment.state import load_target
    from remoteRF.deployment.direct import resolve_active_profile
    target = load_target()
    if target and target['transport'] == 'https-json':
        return True, ''
    profile = resolve_active_profile()
    if profile and profile.ca_path.exists():
        return True, ''

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
    printf("    --account-transport https-json", Sty.CYAN, "  Select an HTTPS home on a custom port", Sty.DEFAULT)
    print()
    printf("Examples:", (Sty.BOLD, Sty.MAGENTA))
    printf("  remoterf --login", Sty.GREEN)
    printf("  remoterf --version", Sty.GREEN)
    printf("  remoterf --config --addr 123.45.654.321:12321", Sty.GREEN)
    printf("  remoterf --config --addr ucla.global.remoterf.net:12321", Sty.GREEN)
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

        try:
            if _connected_server() is None:
                _print_server_unavailable()
                return 2
            from remoteRF.core.acc_login import main as account_main
            return int(account_main() or 0)
        except (RuntimeError, ValueError) as exc:
            print(f'Account connection failed: {exc}')
            return 2

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
        account_transport = None

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

            if tok == '--account-transport':
                if i + 1 >= len(argv) or argv[i + 1] not in ('grpc', 'https-json'):
                    print('ERROR: account transport must be grpc or https-json')
                    return 2
                account_transport = argv[i + 1]
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
            from remoteRF.deployment.state import origin, select_target, select_direct
            # Explicit host:port (even with a scheme) remains direct by default.
            stripped = addr.strip().split('://', 1)[-1]
            use_https = account_transport == 'https-json' or (':' not in stripped and account_transport != 'grpc')
            if use_https:
                from remoteRF.deployment.backend import HttpsJsonAccountBackend
                from remoteRF.config.config import _confirm_tos
                try:
                    selected_origin = origin(addr)
                    if not _confirm_tos():
                        return 1
                    backend = HttpsJsonAccountBackend(selected_origin)
                    try:
                        select_target({'origin':selected_origin,'transport':'https-json','deployment_id':backend.capabilities['deployment_id']})
                        print('Configuration Complete!')
                        print('Account home:', backend.capabilities['display_name'])
                        print('HTTPS origin:', selected_origin)
                    finally:
                        backend.close()
                    return 0
                except (RuntimeError, ValueError) as exc:
                    print(f'Configuration failed: {exc}')
                    return 2
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
            result = configure(host, port, cert_port)
            if result not in (None, 0):
                return int(result)
            select_direct()
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
