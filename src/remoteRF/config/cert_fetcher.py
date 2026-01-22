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
    # Cross-platform-ish default; adjust if you already have a standard in your project.
    # Linux: ~/.config/remoterf
    # macOS: also acceptable; if you want Apple-standard, use ~/Library/Application Support/remoterf
    base = Path(os.path.expanduser("~")) / ".config" / "remoterf"
    return base


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
        data = resp.read()
    return data


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


def fetch_and_save_ca_cert(
    host: str,
    port: int,
    *,
    out_path: Optional[str | Path] = None,
    profile: Optional[str] = None,
    timeout_sec: float = 3.0,
    overwrite: bool = True,
) -> bool:
    """
    Fetch CA cert from the server bootstrap endpoint and save it to disk.

    Args:
        host: server host/ip running cert_provider
        port: cert_provider port (NOT the TLS gRPC port)
        out_path: explicit output path (overrides profile/default location)
        profile: if provided, saves as ~/.config/remoterf/certs/<profile>.crt
        timeout_sec: network timeout
        overwrite: whether to overwrite existing file

    Returns:
        True on success, False on any failure.
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
            return True  # already present, treat as success

        # Fetch (HTTP first, then raw TCP fallback)
        data = b""
        try:
            data = _fetch_http(host, port, timeout_sec)
        except Exception:
            data = _fetch_raw_tcp(host, port, timeout_sec)

        if not data or not _looks_like_pem_cert(data):
            return False

        # Save
        dest.write_bytes(data)

        # Optional: you may want to return/print fingerprint, but requested API is bool.
        return True

    except Exception:
        return False
