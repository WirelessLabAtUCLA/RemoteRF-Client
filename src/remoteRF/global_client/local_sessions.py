# Copyright (C) 2026 RemoteRF
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Deployment-scoped owner-local sessions obtained via ``GlobalAuthV1``.

Global access/refresh credentials authenticate only to Global. A
``GlobalDeploymentSession`` authenticates only to the deployment identified
by its immutable UUID. They are intentionally stored under different keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .credentials import SecretStore

_KEY_PREFIX = "deployment-session:"


@dataclass(frozen=True)
class GlobalDeploymentSession:
    deployment_id: str
    local_username: str
    local_session_token: str
    local_session_expiration: datetime
    tls_server_name: str
    obtained_at: datetime

    @property
    def expires_at(self) -> datetime:
        return self.local_session_expiration

    def is_expired(self, *, now: Optional[datetime] = None) -> bool:
        return (now or datetime.now(timezone.utc)) >= self.local_session_expiration

    def to_dict(self) -> dict:
        return {
            "deployment_id": self.deployment_id,
            "local_username": self.local_username,
            "local_session_token": self.local_session_token,
            "local_session_expiration": self.local_session_expiration.isoformat(),
            "tls_server_name": self.tls_server_name,
            "obtained_at": self.obtained_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GlobalDeploymentSession":
        return cls(
            deployment_id=data["deployment_id"],
            local_username=data["local_username"],
            local_session_token=data["local_session_token"],
            local_session_expiration=datetime.fromisoformat(data["local_session_expiration"]),
            tls_server_name=data["tls_server_name"],
            obtained_at=datetime.fromisoformat(data["obtained_at"]),
        )

    def __repr__(self) -> str:
        return (
            "GlobalDeploymentSession("
            f"deployment_id={self.deployment_id!r}, local_username={self.local_username!r}, "
            "local_session_token=<redacted>)"
        )


# Source-compatible name for prerelease callers. New code uses the explicit
# GlobalDeploymentSession name above.
LocalDeploymentSession = GlobalDeploymentSession


class LocalSessionStore:
    def __init__(self, secret_store: SecretStore):
        self._store = secret_store

    @staticmethod
    def _key(deployment_id: str) -> str:
        return f"{_KEY_PREFIX}{deployment_id}"

    def load(self, deployment_id: str) -> Optional[GlobalDeploymentSession]:
        raw = self._store.get(self._key(deployment_id))
        if raw is None:
            return None
        try:
            session = GlobalDeploymentSession.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            return None
        return session if session.deployment_id == deployment_id else None

    def save(self, session: GlobalDeploymentSession) -> None:
        self._store.set(self._key(session.deployment_id), session.to_dict())

    def clear(self, deployment_id: str) -> None:
        self._store.delete(self._key(deployment_id))
