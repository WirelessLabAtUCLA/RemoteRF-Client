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

"""Per-deployment CA bootstrap and fingerprint verification.

This is the security-critical trust-transfer step for RemoteRF Global: the
deployment's certificate-bootstrap endpoint (`route.certificate_endpoint`)
is unauthenticated HTTP/raw-TCP, exactly like direct mode's. What makes
Global mode trustworthy is that the *expected* fingerprint
(`route.ca_sha256`) came from an authenticated HTTPS response from Global
(`GlobalApiClient.get_connection_descriptor`, protected by TLS + a Global
bearer token) -- Global is the trusted introduction point, not the
deployment's own unauthenticated cert endpoint.

Rules enforced here, all fail-closed:

* the fetched bytes must parse as a PEM certificate;
* SHA-256 is computed over the *DER* encoding, not raw PEM bytes/text
  (`ssl.PEM_cert_to_DER_cert` -- stdlib, no extra crypto dependency needed
  just for this);
* the comparison against the expected fingerprint is constant-time
  (`hmac.compare_digest`);
* the on-disk CA is only replaced *after* a successful comparison
  (write to a temp file, verify, then atomic `os.replace`) -- a failed or
  mismatched fetch never touches the previously-trusted CA.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import ssl
import stat
import tempfile
from pathlib import Path

from ..config.cert_fetcher import fetch_ca_bytes, looks_like_pem_cert
from .errors import CaFingerprintMismatchError, CertificateBootstrapError

_CA_SHA256_RE = re.compile(r"^([0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}$")


def normalize_fingerprint(value: str) -> str:
    """Normalize a SHA-256 fingerprint to canonical `AA:BB:...` (64 hex
    chars, uppercase, colon-separated). Accepts colon-separated or bare hex,
    either case, as input.
    """
    hex_only = value.strip().replace(":", "").replace(" ", "").upper()
    if len(hex_only) != 64 or any(c not in "0123456789ABCDEF" for c in hex_only):
        raise CaFingerprintMismatchError(f"Malformed CA SHA-256 fingerprint: {value!r}")
    return ":".join(hex_only[i : i + 2] for i in range(0, 64, 2))


def validate_ca_sha256_syntax(value: str) -> None:
    if not _CA_SHA256_RE.match(value):
        raise CaFingerprintMismatchError(
            f"ca_sha256 in the connection descriptor is not 32 colon-separated hex byte pairs: {value!r}"
        )


def compute_der_sha256(pem_bytes: bytes) -> str:
    """SHA-256 over the DER encoding of the first certificate in `pem_bytes`,
    as canonical `AA:BB:...:ZZ`."""
    try:
        pem_text = pem_bytes.decode("ascii")
        der_bytes = ssl.PEM_cert_to_DER_cert(pem_text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise CertificateBootstrapError(f"Fetched CA data is not a valid PEM certificate: {exc}") from exc
    digest = hashlib.sha256(der_bytes).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, 64, 2))


def fetch_and_verify_ca(
    *, host: str, port: int, expected_ca_sha256: str, timeout_sec: float = 5.0
) -> bytes:
    """Fetch CA bytes from `host:port` and verify their DER SHA-256 matches
    `expected_ca_sha256` (already normalized by the caller). Returns the raw
    PEM bytes on success. Raises `CertificateBootstrapError` if nothing
    fetchable/parseable, or `CaFingerprintMismatchError` on mismatch.
    Never writes anything to disk -- that's `verify_and_store_ca`'s job,
    which only proceeds once this has already succeeded.
    """
    try:
        data = fetch_ca_bytes(host, port, timeout_sec=timeout_sec)
    except Exception as exc:
        raise CertificateBootstrapError(
            f"Could not fetch the deployment CA certificate from {host}:{port}: {exc}"
        ) from exc

    if not data or not looks_like_pem_cert(data):
        raise CertificateBootstrapError(
            f"No valid PEM certificate was returned from {host}:{port}."
        )

    actual = compute_der_sha256(data)
    expected = normalize_fingerprint(expected_ca_sha256)
    if not hmac.compare_digest(actual, expected):
        raise CaFingerprintMismatchError(
            "The deployment's certificate does not match the fingerprint RemoteRF Global "
            f"issued for it. Expected {expected}, fetched cert hashes to {actual}. "
            "Refusing to trust this certificate."
        )
    return data


def _atomic_write(path: Path, data: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, stat.S_IRWXU)  # 0700
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-ca-", suffix=".crt")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def verify_and_store_ca(
    *, host: str, port: int, expected_ca_sha256: str, dest: Path, timeout_sec: float = 5.0
) -> Path:
    """Fetch, verify, and only then atomically persist the deployment CA to
    `dest`. `dest`'s previous contents (if any) are left untouched unless
    verification succeeds -- a failed refresh never overwrites a
    known-good, previously-verified CA.
    """
    pem_bytes = fetch_and_verify_ca(
        host=host, port=port, expected_ca_sha256=expected_ca_sha256, timeout_sec=timeout_sec
    )
    _atomic_write(dest, pem_bytes, mode=stat.S_IRUSR | stat.S_IWUSR)  # 0600
    return dest
