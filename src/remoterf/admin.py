"""`remoterf --remote-admin`: a lab's console, for its pinned admins, from anywhere.

The lab runs an SSH server reachable only inside its gRPC port
(RemoteAdminV1.Tunnel); this side is the stock OpenSSH client with
`--admin-pipe` as its ProxyCommand, over the same routes `-l` uses (LAN first,
then the Global relay). SSH does the security: the lab's host key is pinned
in our known_hosts (first from the console's pairing code), this machine's
device key is pinned at the lab, and the remote-admin password is asked every
time. Nothing secret is cached here, and this process only pipes bytes.

    remoterf --remote-admin [lab]                 log in (a menu without a lab)
    remoterf --remote-admin pair <user>@<lab> <code>
    remoterf --remote-admin trust <lab> SHA256:…  trust a host key read somewhere trusted
    remoterf --remote-admin hostkey <lab>         show the lab's current host key
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path

SERVICE = "/remote_rf.RemoteAdminV1/"
MD_LABEL = "x-remoterf-device-label"
LAB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:_\[\]-]*$")
INSTALL_SSH = ("OpenSSH (ssh and ssh-keygen) is required for remote admin. Install it: macOS and most "
               "Linux have it; Debian/Ubuntu: sudo apt install openssh-client; Windows 10+: "
               "Settings > Apps > Optional features > OpenSSH Client.")


class AdminError(Exception):
    pass


# -----------------------------
# Files: ~/.config/remoterf-client/admin/
# -----------------------------

def admin_dir() -> Path:
    from .config.config import _config_root

    return _config_root() / "admin"


def key_path() -> Path:
    return admin_dir() / "id_ed25519"


def known_hosts_path() -> Path:
    return admin_dir() / "known_hosts"


def _labs_path() -> Path:
    return admin_dir() / "labs.json"


def labs() -> dict:
    try:
        return json.loads(_labs_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_lab(name: str, entry: dict) -> None:
    data = labs()
    data[name] = entry
    admin_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
    _labs_path().write_text(json.dumps(data, indent=2, sort_keys=True))


def _check_ssh() -> None:
    if shutil.which("ssh") is None or shutil.which("ssh-keygen") is None:
        raise AdminError(INSTALL_SSH)


def ensure_device_key() -> Path:
    path = key_path()
    if not path.exists():
        _check_ssh()
        admin_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"remoterf-admin@{device_label()}",
                        "-f", str(path)], check=True)
    return path


def device_label() -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "", socket.gethostname().split(".")[0])[:40] or "unnamed"


# -----------------------------
# Labs, routes, the tunnel
# -----------------------------

def split_login(text: str) -> tuple[str | None, str]:
    """`alice@ucla` -> ("alice", "ucla"); `jdoe@global@ucla` -> ("jdoe@global", "ucla"); `ucla` -> (None, "ucla")."""
    user, at, lab = text.strip().rpartition("@")
    lab = lab if at else text.strip()
    if not LAB_RE.match(lab):
        raise AdminError(f"Not a lab: {lab!r} (a name like ucla, or host:port)")
    return (user or None), lab


def _alias(lab: str, deployment_id: str | None) -> str:
    return "remoterf-" + (deployment_id or re.sub(r"[^A-Za-z0-9.-]", "-", lab))


def _address(lab: str) -> tuple[str, int] | None:
    """`host:port`, `[v6]:port` or a bare IPv4 (port 12321): a lab that never joined Global."""
    if lab.startswith("["):
        host, _, port = lab[1:].partition("]:")
    elif lab.count(":") == 1:
        host, _, port = lab.partition(":")
    elif ":" in lab:
        raise AdminError(f"Write an IPv6 lab as [address]:port, not {lab!r}")
    elif lab.count(".") == 3 and lab.replace(".", "").isdigit():
        host, port = lab, "12321"
    else:
        return None
    if not host or not port.isdigit():
        raise AdminError(f"Not a lab address: {lab!r}")
    return host, int(port)


def _resolve(lab: str):
    """(grpc target, CA PEM, TLS server name, HostKeyAlias) for a lab: a saved home,
    a Global name (discovered and saved), or a host:port that never joined Global."""
    from .deployment import homes

    address = _address(lab)
    if address is not None:
        from .config.cert_fetcher import fetch_ca_bytes

        host, port = address
        entry = labs().get(lab, {})
        try:
            certificate = fetch_ca_bytes(host, int(entry.get("cert_port") or int(port) + 1), timeout_sec=5.0)
        except Exception as exc:  # noqa: BLE001
            raise AdminError(f"Could not reach {lab}: {exc}") from None
        # TLS here is transport only: the SSH host key, pinned from the console, is the lab's identity.
        return f"[{host}]:{port}" if ":" in host else f"{host}:{port}", certificate, None, _alias(lab, None)
    try:
        home = homes.load_home(lab)
    except Exception:  # noqa: BLE001 - not saved yet: Global's discovery names it
        home = _rediscover(lab)
    try:
        route, certificate = homes.select_route(home, homes._configured_ca(lab, home))
    except Exception as exc:  # noqa: BLE001
        # A new CA (`certs reset`): Global's current pin, as LAN fallback does. Anything else is the answer.
        if home.get("deployment_id") is None or "unexpected certificate" not in str(exc):
            raise AdminError(f"Could not reach {lab}: {exc}") from None
        home = _rediscover(lab)
        try:
            route, certificate = homes.select_route(home)
        except Exception as exc2:  # noqa: BLE001
            raise AdminError(f"Could not reach {lab}: {exc2}") from None
    relay = next((r["host"] for r in home["routes"] if r["kind"] == "relay"), None)
    return (f"{route['host']}:{route['port']}", certificate, relay if route["kind"] != "relay" else None,
            _alias(lab, home.get("deployment_id")))


def _rediscover(lab: str) -> dict:
    from .deployment import homes

    if not homes.NAME_RE.match(lab):
        raise AdminError(f"No saved home {lab!r}, and it is not a RemoteRF Global lab name")
    try:
        return homes.save_home(lab, homes.discover(f"{lab}.{homes.GLOBAL_DOMAIN}"))
    except Exception as exc:  # noqa: BLE001
        raise AdminError(f"Could not find lab {lab!r}: {exc}") from None


def _channel(lab: str):
    from .core.secure_channel import build_secure_channel

    target, certificate, server_name, alias = _resolve(lab)
    return build_secure_channel(target, certificate, tls_server_name=server_name), alias


def fetch_host_key(lab: str) -> tuple[str, str]:
    """(OpenSSH public key line, HostKeyAlias). Not trusted by itself."""
    import grpc

    channel, alias = _channel(lab)
    try:
        line = channel.unary_unary(SERVICE + "HostKey")(b"", timeout=15).decode("ascii", "replace").strip()
    except grpc.RpcError as exc:
        raise AdminError(f"{lab}: {exc.details() or exc.code().name}") from None
    finally:
        channel.close()
    try:
        fingerprint(line)
    except Exception:  # noqa: BLE001
        raise AdminError(f"{lab} sent something that is not a host key") from None
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]+={0,2}", line):
        raise AdminError(f"{lab} sent something that is not a host key")
    return line, alias


def fingerprint(line: str) -> str:
    blob = base64.b64decode(line.split()[1], validate=True)
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def trusted(alias: str, key_line: str | None = None) -> bool:
    """Whether this alias has a pinned key here (that very key, when given)."""
    try:
        lines = known_hosts_path().read_text().splitlines()
    except OSError:
        return False
    return any(l.split()[:1] == [alias] and (key_line is None or l.split()[1:3] == key_line.split()[:2])
               for l in lines)


def trust_line(alias: str, line: str) -> None:
    path = known_hosts_path()
    try:
        kept = [l for l in path.read_text().splitlines() if l.split()[:1] != [alias]]
    except OSError:
        kept = []
    admin_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text("\n".join([*kept, f"{alias} {line}"]) + "\n")


def pipe(lab: str) -> int:
    """The ProxyCommand: stdin/stdout <-> RemoteAdminV1.Tunnel. Errors go to stderr only."""
    import grpc

    try:
        channel, _ = _channel(lab)
    except Exception as exc:  # noqa: BLE001
        print(f"remoterf: {exc}", file=sys.stderr)
        return 255

    def chunks():
        while True:
            data = os.read(0, 65536)
            if not data:
                return
            yield data

    out = sys.stdout.buffer
    try:
        for data in channel.stream_stream(SERVICE + "Tunnel")(chunks(), metadata=((MD_LABEL, device_label()),)):
            out.write(data)
            out.flush()
    except grpc.RpcError as exc:
        print(f"remoterf: {lab}: {exc.details() or exc.code().name}", file=sys.stderr)
        return 255
    finally:
        channel.close()
    return 0


# -----------------------------
# ssh
# -----------------------------

def _ssh_path(path) -> str:
    """A path as an ssh option value: quoted (spaces), `%` escaped (ssh expands tokens)."""
    return '"' + str(path).replace("%", "%%") + '"'


def ssh_argv(lab: str, login: str, alias: str, *, strict: bool = True) -> list[str]:
    # -P: never import from the directory ssh happens to run in (3.11+).
    python = [sys.executable, *(["-P"] if sys.version_info >= (3, 11) else [])]
    words = [*python, "-m", "remoterf.admin", "--admin-pipe", lab]
    proxy = subprocess.list2cmdline(words) if os.name == "nt" else " ".join(shlex.quote(a) for a in words)
    return ["ssh", "-F", "none", "-tt",
            "-o", "ProxyCommand=" + proxy.replace("%", "%%"),
            "-o", f"HostKeyAlias={alias}",
            "-o", f"UserKnownHostsFile={_ssh_path(known_hosts_path())}",
            "-o", f"GlobalKnownHostsFile={_ssh_path(os.devnull)}",
            "-o", f"StrictHostKeyChecking={'yes' if strict else 'ask'}", "-o", "UpdateHostKeys=no",
            "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none", "-i", str(key_path()).replace("%", "%%"),
            "-o", "PreferredAuthentications=publickey,keyboard-interactive", "-o", "PasswordAuthentication=no",
            "-o", "ServerAliveInterval=30", "-o", "ForwardAgent=no", "-o", "ForwardX11=no",
            "-o", "ClearAllForwardings=yes",  # no LogLevel=ERROR: it hides the lab's instructions
            "-l", login, alias]


def _exec(argv: list[str]) -> int:
    sys.stdout.flush()
    if os.name == "posix":
        os.execvp(argv[0], argv)
    return subprocess.call(argv)


# -----------------------------
# Commands
# -----------------------------

def cmd_pair(target: str, code: str) -> int:
    user, lab = split_login(target)
    code, _, prefix = code.strip().partition(".")
    if not user:
        user = input(f"Your account at {lab}: ").strip()
    if len(prefix) < 12:
        raise AdminError("That code is missing its .<fingerprint> part; copy the whole line the console printed.")
    _check_ssh()
    line, alias = fetch_host_key(lab)
    fp = fingerprint(line)
    if not fp.split(":", 1)[1].startswith(prefix):
        raise AdminError(f"{lab} presented host key {fp}, which is not the one the console's code names. "
                         "Nothing was trusted. Get a fresh code at the lab console (serverrf admins code).")
    trust_line(alias, line)
    ensure_device_key()
    _save_lab(lab, {"user": user, "alias": alias})
    print(f"Trusted {lab}'s admin host key {fp}.")
    # The code rides in the login name: inside SSH, after the host key is verified.
    return _exec(ssh_argv(lab, f"{user}#{code}", alias))


def cmd_login(target: str | None) -> int:
    saved = labs()
    if target is None:
        if not saved:
            raise AdminError("No labs yet. Pair first: remoterf --remote-admin pair <user>@<lab> <code> "
                             "(the lab console prints it: serverrf admins add <user>).")
        names = sorted(saved)
        if len(names) == 1:
            target = names[0]
        else:
            for i, name in enumerate(names, 1):
                print(f"  {i}) {saved[name].get('user', '?')}@{name}")
            pick = input("Lab: ").strip()
            target = names[int(pick) - 1] if pick.isdigit() and 0 < int(pick) <= len(names) else pick
    user, lab = split_login(target)
    entry = saved.get(lab, {})
    user = user or entry.get("user") or input(f"Your account at {lab}: ").strip()
    _check_ssh()
    ensure_device_key()
    alias = entry.get("alias") or _resolve(lab)[3]
    strict = trusted(alias)
    if not strict:
        print(f"This machine has not trusted {lab}'s admin host key. Safer options:\n"
              "• Use a code from the lab console.\n"
              f"• Or run `remoterf --remote-admin trust {lab} SHA256:…` with a verified fingerprint.\n"
              f"• Copy {known_hosts_path()} from a paired machine.\n"
              "Accepting it below on first sight trusts the "
              "network you are on.", file=sys.stderr)
    _save_lab(lab, {**entry, "user": user, "alias": alias})
    return _exec(ssh_argv(lab, user, alias, strict=strict))


def cmd_trust(lab: str, fp: str) -> int:
    _, lab = split_login(lab)
    line, alias = fetch_host_key(lab)
    actual = fingerprint(line)
    if actual != (fp if fp.startswith("SHA256:") else "SHA256:" + fp):
        raise AdminError(f"{lab} presented {actual}, not {fp}. Nothing was trusted.")
    trust_line(alias, line)
    _save_lab(lab, {**labs().get(lab, {}), "alias": alias})
    print(f"Trusted {lab}'s admin host key {actual}.")
    return 0


def cmd_hostkey(lab: str) -> int:
    _, lab = split_login(lab)
    line, alias = fetch_host_key(lab)
    state = ("trusted here" if trusted(alias, line) else
             "NOT the key trusted here" if trusted(alias) else "not trusted here")
    print(f"{lab} presents {fingerprint(line)} ({state}).")
    print("Compare it with `serverrf admins hostkey` at the lab before trusting it.")
    return 0


USAGE = """Usage:
  remoterf --remote-admin [<user>@]<lab>            open a lab's admin console (prompts if <lab> is omitted)
  remoterf --remote-admin pair <user>@<lab> <code>  first time on this machine (code from: serverrf admins add/code)
  remoterf --remote-admin trust <lab> SHA256:…      trust a verified host key fingerprint
  remoterf --remote-admin hostkey <lab>             show the lab's current host key"""


def main(argv: list[str]) -> int:
    """argv after `--remote-admin` (or `--admin-pipe <lab>`)."""
    if argv[:1] == ["--admin-pipe"]:
        try:  # stdout carries SSH bytes only, even on the way out
            return pipe(argv[1]) if len(argv) == 2 else 2
        except (KeyboardInterrupt, BrokenPipeError):
            return 130
    try:
        if argv[:1] in (["-h"], ["--help"]):
            print(USAGE)
            return 0
        verb = argv[0] if argv else None
        if verb == "pair" and len(argv) == 3:
            return cmd_pair(argv[1], argv[2])
        if verb == "trust" and len(argv) == 3:
            return cmd_trust(argv[1], argv[2])
        if verb == "hostkey" and len(argv) == 2:
            return cmd_hostkey(argv[1])
        if verb in ("pair", "trust", "hostkey") or len(argv) > 1:
            print(USAGE, file=sys.stderr)
            return 2
        return cmd_login(verb)
    except (EOFError, KeyboardInterrupt):
        print()
        return 130
    except Exception as exc:  # noqa: BLE001 - AdminError and anything below it (ssh-keygen, routes)
        print(f"remoterf: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
