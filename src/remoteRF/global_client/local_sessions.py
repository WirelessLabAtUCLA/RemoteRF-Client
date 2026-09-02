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

"""Owner-local RemoteRF sessions obtained via GlobalAuthV1 assertion
exchange, one per deployment UUID.

This is deliberately a *separate* credential class and a separate storage
namespace from `credentials.py`'s `GlobalCredentials`:

* `GlobalCredentials` authenticates the user to https://global.remoterf.net
  and must never be sent to a deployment.
* `LocalDeploymentSession` is what a deployment (e.g. UCLA) hands back
  after a successful GlobalAuthV1 exchange, scoped to that one deployment's
  existing RemoteRF APIs, and must never be sent to Global.

Sessions are indexed by immutable deployment UUID (never by slug) so a
later slug rename cannot cause one deployment's session to be reused
against a different deployment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from .credentials import SecretStore

_KEY_PREFIX = "deployment-session:"


@dataclass(frozen=True)
class LocalDeploymentSession:
    deployment_id: str
    tls_server_name: str
    session_material: dict[str, Any]
    obtained_at: datetime
    expires_at: Optional[datetime]

    def is_expired(self, *, now: Optional[datetime] = None) -> bool:
        if self.expires_at is None:
            return False
        now = now or datetime.now(timezone.utc)
        return now >= self.expires_at

    def to_dict(self) -> dict:
        return {
            "deployment_id": self.deployment_id,
            "tls_server_name": self.tls_server_name,
            "session_material": self.session_material,
            "obtained_at": self.obtained_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LocalDeploymentSession":
        return cls(
            deployment_id=data["deployment_id"],
            tls_server_name=data["tls_server_name"],
            session_material=data["session_material"],
            obtained_at=datetime.fromisoformat(data["obtained_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
        )

    def __repr__(self) -> str:  # never leak session material through logging/repr
        return f"LocalDeploymentSession(deployment_id={self.deployment_id!r}, session_material=<redacted>)"


class LocalSessionStore:
    """Typed wrapper around `SecretStore`, namespaced per deployment UUID.

    Deliberately reuses the same `SecretStore` backend (keyring or file)
    selected for Global credentials, but under a distinct key per
    deployment so that UCLA's session can never be looked up, or
    accidentally reused, under another deployment's identity.
    """

    def __init__(self, secret_store: SecretStore):
        self._store = secret_store

    @staticmethod
    def _key(deployment_id: str) -> str:
        return f"{_KEY_PREFIX}{deployment_id}"

    def load(self, deployment_id: str) -> Optional[LocalDeploymentSession]:
        raw = self._store.get(self._key(deployment_id))
        if raw is None:
            return None
        try:
            session = LocalDeploymentSession.from_dict(raw)
        except (KeyError, ValueError):
            return None
        if session.deployment_id != deployment_id:
            # Defense in depth: never hand back a session for a different
            # deployment than the one asked for, even if storage is shared.
            return None
        return session

    def save(self, session: LocalDeploymentSession) -> None:
        self._store.set(self._key(session.deployment_id), session.to_dict())

    def clear(self, deployment_id: str) -> None:
        self._store.delete(self._key(deployment_id))
