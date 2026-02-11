# cert_fetcher.py

from __future__ import annotations

import hashlib
import os
import socket
import urllib.request
from pathlib import Path
from typing import Optional

CERT_FILENAME_DEFAULT = "ca.crt"

def _default_config_dir() -> Path:
    return Path(os.path.expanduser("~")) / ".config" / "remoterf"

def _default_env_path() -> Path:
    return _default_config_dir() / ".env"

def _ensure_parent_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)

def _looks_like_pem_cert(data: bytes) -> bool:
    return b"BEGIN CERTIFICATE" in data and b"END CERTIFICATE" in data

def sha256_fingerprint_pem(pem_bytes: bytes) -> str:
    h = hashlib.sha256(pem_bytes).hexdigest()
    return ":".join(h[i:i+2] for i in range(0, len(h), 2))

def _fetch_http(host: str, port: int, timeout_sec: float) -> bytes:
    url = f"http://{host}:{port}/ca.crt"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        return resp.read()

def _fetch_raw_tcp(host: str, port: int, timeout_sec: float) -> bytes:
    chunks: list[bytes] = []
    with socket.create_connection((host, port), timeout=timeout_sec) as s:
        s.settimeout(timeout_sec)
        while True:
            try:
                b = s.recv(4096)
            except socket.timeout:
                break
            if not b:
                break
            chunks.append(b)
    return b"".join(chunks)

def _dotenv_escape_value_no_quotes(value: str) -> str:
    """
    Returns a .env-safe value WITHOUT surrounding quotes.
    We use forward slashes to avoid backslash-escape weirdness.
    """
    # Trim outer whitespace; keep internal spaces.
    v = value.strip()

    # Convert Windows backslashes to forward slashes.
    v = v.replace("\\", "/")

    # Remove any accidental surrounding quotes.
    if (len(v) >= 2) and ((v[0] == v[-1]) and v[0] in ("'", '"')):
        v = v[1:-1]

    # Disallow newlines (dotenv lines are single-line)
    v = v.replace("\r", "").replace("\n", "")

    return v

def _dotenv_set_key(env_path: Path, key: str, value: str) -> None:
    """
    Create/update KEY=value in env_path. Writes value WITHOUT quotes.
    Preserves unrelated lines. Comments are kept.
    """
    _ensure_parent_dir(env_path)

    new_line = f"{key}={_dotenv_escape_value_no_quotes(value)}\n"

    if not env_path.exists():
        env_path.write_text(new_line, encoding="utf-8")
        return

    lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)

    # Replace existing KEY=... (ignores leading whitespace); doesn't touch commented-out keys.
    replaced = False
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            out.append(line)
            continue

        # Match "KEY=" at start (after lstrip). Keep any leading whitespace.
        if stripped.startswith(key + "="):
            prefix_ws = line[: len(line) - len(stripped)]
            out.append(prefix_ws + new_line)
            replaced = True
        else:
            out.append(line)

    if not replaced:
        # Append with a newline boundary
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(new_line)

    env_path.write_text("".join(out), encoding="utf-8")

def fetch_and_save_ca_cert(
    host: str,
    port: int,
    *,
    out_path: Optional[str | Path] = None,
    profile: Optional[str] = None,
    timeout_sec: float = 3.0,
    overwrite: bool = True,
    write_env: bool = True,
    env_path: Optional[str | Path] = None,
) -> bool:
    """
    Fetch CA cert and save. Optionally writes REMOTERF_CA_CERT into ~/.config/remoterf/.env
    WITHOUT quotes.

    Args:
        write_env: if True, updates REMOTERF_CA_CERT in env file to point at saved cert
        env_path: override env file location; default ~/.config/remoterf/.env
    """
    try:
        if not isinstance(port, int):
            port = int(port)

        # Determine destination path
        if out_path is not None:
            dest = Path(out_path).expanduser().resolve()
        else:
            cfg = _default_config_dir()
            certs_dir = cfg / "certs"
            name = f"{profile}.crt" if profile else CERT_FILENAME_DEFAULT
            dest = certs_dir / name

        _ensure_parent_dir(dest)

        if dest.exists() and not overwrite:
            # Still ensure env points to it (helpful for idempotency)
            if write_env:
                ep = Path(env_path).expanduser().resolve() if env_path else _default_env_path()
                _dotenv_set_key(ep, "REMOTERF_CA_CERT", dest.as_posix())
            return True

        # Fetch (HTTP first, then raw TCP fallback)
        try:
            data = _fetch_http(host, port, timeout_sec)
        except Exception:
            data = _fetch_raw_tcp(host, port, timeout_sec)

        if not data or not _looks_like_pem_cert(data):
            return False

        dest.write_bytes(data)

        # Update .env without quotes
        if write_env:
            ep = Path(env_path).expanduser().resolve() if env_path else _default_env_path()
            _dotenv_set_key(ep, "REMOTERF_CA_CERT", dest.as_posix())

        return True

    except Exception:
        return False
