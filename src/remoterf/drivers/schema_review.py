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

"""A Global account installs a lab's device driver only once RemoteRF Global
has approved its schema. A schema is known by the hash of its content, which
this client computes itself; the Wireless Lab reviews each new one. Global is
asked every time (a rejection holds at once); its approvals are kept here, so a
linked lab's LAN login works while Global is down for every schema approved
before."""

from __future__ import annotations

import hashlib
import json

APPROVED_FILE = "approved-schemas-v1.json"

_REFUSED = {
    "pending": "is waiting for review by the RemoteRF team; try again once it is approved",
    "rejected": "was rejected by the RemoteRF team",
    "unknown": "has not been reviewed by the RemoteRF team yet",
    None: "could not be checked: RemoteRF Global is unreachable, and only drivers it approved before install",
}


def digest(schema: dict) -> str:
    """The hash a Server gives its schema: sha256 of the canonical JSON without it
    (and without the mark fetch_idl adds to a partner lab's answer)."""
    body = {key: value for key, value in schema.items() if key not in ("schema_hash", "federated")}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _path():
    from ..deployment.homes import homes_path

    return homes_path().with_name(APPROVED_FILE)


def _approved() -> set:
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {item for item in value if isinstance(item, str)} if isinstance(value, list) else set()


def _keep(schema_hash: str, approved: bool) -> None:
    from ..deployment.state import private_write

    known = _approved()
    if (schema_hash in known) != approved:
        try:
            private_write(_path(), sorted(known | {schema_hash} if approved else known - {schema_hash}))
        except OSError:
            pass  # a read-only config: Global's answer still stands for this install


def _status(schema_hash: str):
    """Global's answer, or None when it cannot be asked."""
    from ..core.grpc_client import _token_backend
    from ..deployment.http import AccountBackendError

    try:
        return _token_backend().schema_status(schema_hash)
    except (AccountBackendError, OSError):
        return None


def require_approved(schema: dict) -> None:
    schema_hash = digest(schema)
    if schema.get("schema_hash") != schema_hash:
        raise RuntimeError("The deployment's driver schema does not match its own hash; it was not installed.")
    status = _status(schema_hash)
    if status is None and schema_hash in _approved():
        return  # Global unreachable (a lab's LAN login): what it approved before
    if status is not None:
        _keep(schema_hash, status == "approved")
    if status == "approved":
        return
    device_type = str(schema.get("device_type") or "device")
    raise RuntimeError(
        f"The {device_type!r} driver this deployment shares (schema {schema_hash[:19]}…) "
        f"{_REFUSED.get(status, _REFUSED['unknown'])}. It was not installed."
    )
