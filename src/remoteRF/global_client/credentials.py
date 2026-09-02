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

"""Secure storage for RemoteRF Global credentials.

Two credentials live here (see `local_sessions.py` for a third, deployment
owner-local sessions, which are stored separately and must never be mixed
with these):

* ``global_access_token``  -- short-lived bearer for global.remoterf.net
* ``global_refresh_token`` -- used only to mint a new access token

Both are opaque blobs to this module; storage never inspects or logs them.

Preferred backend is the OS keyring (macOS Keychain, Secret Service on
Linux desktops, Windows Credential Locker) via the `keyring` package. A
file-based fallback exists for headless environments where no OS keyring is
available (e.g. CI, a bare Linux server with no desktop session); it is
weaker than the OS keyring (protected only by filesystem permissions, not
OS-level encryption/access control) and callers are warned whenever it is
used automatically. It never appears in `.env` or in `global/state.json`.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

KEYRING_SERVICE = "remoterf-global"
_ACCESS_KEY = "global-credentials"


class CredentialStoreMode(str, Enum):
    KEYRING = "keyring"
    FILE = "file"


# --- generic opaque secret store --------------------------------------------


class SecretStore(ABC):
    """Backend-agnostic store for one opaque JSON-serializable secret per
    string key. Used both for Global credentials and for per-deployment
    local sessions (see `local_sessions.py`), each under its own key
    namespace, so the two credential classes never collide."""

    mode: CredentialStoreMode

    @abstractmethod
    def get(self, key: str) -> Optional[dict]: ...

    @abstractmethod
    def set(self, key: str, value: dict) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...


class KeyringSecretStore(SecretStore):
    mode = CredentialStoreMode.KEYRING

    def __init__(self, backend=None):
        import keyring  # local import: keyring is an optional dependency

        self._keyring = backend if backend is not None else keyring

    def get(self, key: str) -> Optional[dict]:
        raw = self._keyring.get_password(KEYRING_SERVICE, key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            # Corrupt/foreign entry under our service name: treat as absent
            # rather than raising, so a damaged keychain entry doesn't
            # crash every command.
            return None

    def set(self, key: str, value: dict) -> None:
        self._keyring.set_password(KEYRING_SERVICE, key, json.dumps(value))

    def delete(self, key: str) -> None:
        try:
            self._keyring.delete_password(KEYRING_SERVICE, key)
        except Exception:
            # Absent entry, locked keychain the user dismissed, etc. Logout
            # must still proceed and clear whatever local state it can.
            pass


class FileSecretStore(SecretStore):
    """Explicit-opt-in / automatic-fallback file store. One JSON file per
    key under `<config_root>/global/secrets/<key>.json`, directory mode
    0700, file mode 0600, atomic replace-on-write."""

    mode = CredentialStoreMode.FILE

    def __init__(self, secrets_dir: Path):
        self._dir = secrets_dir

    def _path(self, key: str) -> Path:
        # Keys are internal constants (not user input), but keep this
        # defensive against accidental path traversal.
        safe = key.replace("/", "_").replace("\\", "_").replace("..", "_")
        return self._dir / f"{safe}.json"

    def get(self, key: str) -> Optional[dict]:
        path = self._path(key)
        try:
            raw = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def set(self, key: str, value: dict) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._dir, stat.S_IRWXU)  # 0700

        path = self._path(key)
        fd, tmp_name = tempfile.mkstemp(dir=str(self._dir), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(value, f)
            os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)  # 0600
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass


def _keyring_is_usable() -> bool:
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
    except ImportError:
        return False
    try:
        backend = keyring.get_keyring()
    except Exception:
        return False
    return not isinstance(backend, FailKeyring)


def resolve_secret_store(
    *, config_root: Path, force_file: bool = False, warn=None
) -> SecretStore:
    """Pick keyring when usable, otherwise fall back to the file store.

    `force_file=True` is the explicit opt-in path (e.g. `--credential-store
    file` or `REMOTERF_GLOBAL_CREDENTIAL_STORE=file`) and stays quiet.
    Falling back automatically because no usable OS keyring exists instead
    emits a warning via `warn` (defaults to `print`) every time, per the
    file-storage-is-weaker-than-keyring requirement.
    """
    if warn is None:
        warn = print

    if not force_file and _keyring_is_usable():
        return KeyringSecretStore()

    if not force_file:
        warn(
            "Warning: no usable OS keyring was found on this system. "
            "Falling back to file-based credential storage at "
            f"{config_root / 'global' / 'secrets'}, protected only by "
            "filesystem permissions (mode 0600). This is weaker than OS "
            "keyring storage. Install/unlock an OS keyring for stronger "
            "protection."
        )

    return FileSecretStore(config_root / "global" / "secrets")


# --- Global credentials (access/refresh token pair) -------------------------


@dataclass(frozen=True)
class GlobalCredentials:
    access_token: str
    refresh_token: str
    obtained_at: datetime
    expires_at: datetime

    def is_access_token_expired(self, *, skew_seconds: int = 30) -> bool:
        return datetime.now(timezone.utc) >= (self.expires_at - timedelta(seconds=skew_seconds))

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "obtained_at": self.obtained_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GlobalCredentials":
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            obtained_at=datetime.fromisoformat(data["obtained_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
        )

    @classmethod
    def from_token_pair(cls, access_token: str, refresh_token: str, expires_in: int) -> "GlobalCredentials":
        now = datetime.now(timezone.utc)
        return cls(
            access_token=access_token,
            refresh_token=refresh_token,
            obtained_at=now,
            expires_at=now + timedelta(seconds=expires_in),
        )

    def __repr__(self) -> str:  # never leak tokens through logging/repr
        return "GlobalCredentials(access_token=<redacted>, refresh_token=<redacted>)"


class GlobalCredentialStore:
    """Typed wrapper around `SecretStore` for the single Global
    access/refresh token pair (one Global account per machine, v1.0)."""

    def __init__(self, secret_store: SecretStore):
        self._store = secret_store

    @property
    def mode(self) -> CredentialStoreMode:
        return self._store.mode

    def load(self) -> Optional[GlobalCredentials]:
        raw = self._store.get(_ACCESS_KEY)
        if raw is None:
            return None
        try:
            return GlobalCredentials.from_dict(raw)
        except (KeyError, ValueError):
            return None

    def save(self, creds: GlobalCredentials) -> None:
        self._store.set(_ACCESS_KEY, creds.to_dict())

    def clear(self) -> None:
        self._store.delete(_ACCESS_KEY)
