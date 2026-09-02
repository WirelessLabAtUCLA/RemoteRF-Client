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

"""Debug logging for `global_client`, with secrets redacted by construction.

Fields that are safe to log: Global base URL, deployment UUID/slug, route
kind, endpoint hostname/port, descriptor/session expiration, a request ID,
and HTTP/gRPC status categories.

Fields that must never reach a log line: passwords, Global access/refresh
tokens, deployment assertions, local RemoteRF session tokens, full
Authorization headers, device codes (the low-entropy `user_code` is fine to
show a user directly, but is still not logged here), and CA private
material (this client never handles any -- it only verifies public CA
certificates).

`redact()` is defensive-in-depth for any value that might accidentally
flow into a log call (e.g. an exception's `args`); the real guarantee is
that call sites in this package simply never pass secret values to the
logger in the first place.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("remoteRF.global_client")

_SECRET_KEY_HINTS = (
    "access_token",
    "refresh_token",
    "assertion",
    "session_material",
    "session_token",
    "password",
    "authorization",
    "device_code",
)

# A bearer JWT-shaped value (header.payload.signature, base64url segments).
_JWT_RE = re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
# A generic long opaque-token-shaped value (>= 20 url-safe-base64 chars).
_OPAQUE_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{20,}\b")


def redact(value: object) -> str:
    """Best-effort redaction for a value that might end up in a log
    message. Do not rely on this instead of simply not logging secrets --
    it's a defensive backstop, not the primary control."""
    text = str(value)
    text = _JWT_RE.sub("<redacted>", text)
    text = _OPAQUE_TOKEN_RE.sub("<redacted>", text)
    return text


def is_sensitive_key(key: str) -> bool:
    key_lower = key.lower()
    return any(hint in key_lower for hint in _SECRET_KEY_HINTS)
