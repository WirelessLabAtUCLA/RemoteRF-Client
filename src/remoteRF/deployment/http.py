"""Verified same-origin JSON transport with bounded responses and no retries."""

import ssl
import time
import httpx
from remoterf_federation_core import (
    MAX_HOME_PERMISSIONS_HTTP_RESPONSE_BYTES,
    PERMISSIONS_CLIENT_OVERALL_TIMEOUT_SECONDS,
    PERMISSIONS_CONNECT_TIMEOUT_SECONDS,
    strict_json,
    ValidationError,
)
from .state import origin


class AccountBackendError(RuntimeError):
    def __init__(self, code, *, provenance=None):
        self.code = code
        self.provenance = provenance
        super().__init__(code)


class JsonTransport:
    def __init__(self, selected_origin, *, client=None):
        self.origin = origin(selected_origin)
        self.client = client or httpx.Client(
            verify=ssl.create_default_context(),
            timeout=5,
            follow_redirects=False,
            trust_env=False,
        )

    def request(
        self,
        method,
        path,
        *,
        data=None,
        access=None,
        overall_timeout=None,
        max_response_bytes=65536,
    ):
        if (
            not path.startswith("/")
            or path.startswith("//")
            or any(c in path for c in ("?", "#", "\\", "%"))
        ):
            raise ValidationError("Invalid same-origin account path")
        headers = {"Accept": "application/json"}
        if access:
            headers["Authorization"] = "Bearer " + access
        overall_timeout = (
            5.0 if overall_timeout is None else float(overall_timeout)
        )
        if overall_timeout <= 0 or max_response_bytes <= 0:
            raise ValueError("invalid account operation budget")
        deadline = time.monotonic() + overall_timeout
        timeout = httpx.Timeout(
            connect=min(PERMISSIONS_CONNECT_TIMEOUT_SECONDS, overall_timeout),
            read=overall_timeout,
            write=overall_timeout,
            pool=min(PERMISSIONS_CONNECT_TIMEOUT_SECONDS, overall_timeout),
        )
        try:
            with self.client.stream(
                method,
                self.origin + path,
                json=data,
                headers=headers,
                follow_redirects=False,
                timeout=timeout,
            ) as response:
                raw = bytearray()
                for chunk in response.iter_bytes(chunk_size=1):
                    if time.monotonic() >= deadline:
                        raise AccountBackendError("Account operation deadline exceeded")
                    raw.extend(chunk)
                    if len(raw) > max_response_bytes:
                        raise AccountBackendError(
                            "Response exceeds account protocol limit"
                        )
                if 300 <= response.status_code < 400:
                    raise AccountBackendError("Account redirects are not allowed")
                value = strict_json(bytes(raw), max_bytes=max_response_bytes)
                if response.status_code >= 400:
                    # Never print arbitrary server text (could contain secrets/terminal escapes).
                    error = value.get("error") if isinstance(value, dict) else None
                    code = error.get("code") if isinstance(error, dict) else None
                    import re

                    if not isinstance(code, str) or not re.fullmatch(
                        "[a-z_]{1,64}", code
                    ):
                        code = "account_request_failed"
                    provenance = (
                        error.get("provenance") if isinstance(error, dict) else None
                    )
                    if provenance not in {None, "home", "transport", "destination"}:
                        provenance = None
                    raise AccountBackendError(code, provenance=provenance)
                if type(value) is not dict:
                    raise AccountBackendError("Invalid account response")
                return value
        except httpx.HTTPError as exc:
            raise AccountBackendError(
                "Account connection failed; verify the target and TLS configuration"
            ) from exc

    def permissions(self, path, *, access):
        return self.request(
            "GET",
            path,
            access=access,
            overall_timeout=PERMISSIONS_CLIENT_OVERALL_TIMEOUT_SECONDS,
            max_response_bytes=MAX_HOME_PERMISSIONS_HTTP_RESPONSE_BYTES,
        )

    def close(self):
        self.client.close()
