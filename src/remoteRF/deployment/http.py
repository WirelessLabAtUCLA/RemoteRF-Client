"""Verified same-origin JSON transport with bounded responses and no retries."""

import ssl
import httpx
from remoterf_federation_core import strict_json, ValidationError
from .state import origin


class AccountBackendError(RuntimeError):
    pass


class JsonTransport:
    def __init__(self, selected_origin, *, client=None):
        self.origin = origin(selected_origin)
        self.client = client or httpx.Client(
            verify=ssl.create_default_context(),
            timeout=5,
            follow_redirects=False,
            trust_env=False,
        )

    def request(self, method, path, *, data=None, access=None):
        if (
            not path.startswith("/")
            or path.startswith("//")
            or any(c in path for c in ("?", "#", "\\", "%"))
        ):
            raise ValidationError("Invalid same-origin account path")
        headers = {"Accept": "application/json"}
        if access:
            headers["Authorization"] = "Bearer " + access
        try:
            with self.client.stream(
                method,
                self.origin + path,
                json=data,
                headers=headers,
                follow_redirects=False,
            ) as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise AccountBackendError(
                            "Response exceeds account protocol limit"
                        )
                if 300 <= response.status_code < 400:
                    raise AccountBackendError("Account redirects are not allowed")
                value = strict_json(bytes(raw), max_bytes=65536)
                if response.status_code >= 400:
                    # Never print arbitrary server text (could contain secrets/terminal escapes).
                    error = value.get("error") if isinstance(value, dict) else None
                    code = error.get("code") if isinstance(error, dict) else None
                    import re

                    if not isinstance(code, str) or not re.fullmatch(
                        "[a-z_]{1,64}", code
                    ):
                        code = "account_request_failed"
                    raise AccountBackendError(code)
                if type(value) is not dict:
                    raise AccountBackendError("Invalid account response")
                return value
        except httpx.HTTPError as exc:
            raise AccountBackendError(
                "Account connection failed; verify the target and TLS configuration"
            ) from exc

    def close(self):
        self.client.close()
