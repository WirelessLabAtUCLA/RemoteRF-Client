"""The secret a Global account sends a lab it is linked to, instead of its password.

A copy of remoterf_federation_core.lan.lan_secret (this client stays on an
older core); tests/test_lanpass.py pins core's test vector so they cannot drift.
"""

import hashlib


def lan_secret(password: str, deployment_id: str, username: str) -> str:
    salt = b"remoterf-lan-v1\0" + deployment_id.encode() + b"\0" + username.lower().encode()
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32).hex()
