"""Verified same-origin JSON transport with bounded responses and no retries."""

import json
import os
import ssl
import time
import certifi
import httpx
from remoterf_federation_core import (
    MAX_HOME_PERMISSIONS_HTTP_RESPONSE_BYTES,
    PERMISSIONS_CLIENT_OVERALL_TIMEOUT_SECONDS,
    PERMISSIONS_CONNECT_TIMEOUT_SECONDS,
    strict_json,
    ValidationError,
)
from .state import origin

# Device RPC bodies: u32be len(envelope) || JSON envelope || raw frame. The
# core's FRAMED_CONTENT_TYPE, kept here while the client pins an older core.
FRAMED_CONTENT_TYPE = "application/x-remoterf-framed"


class AccountBackendError(RuntimeError):
    def __init__(self, code, *, provenance=None, retry_after=None, unreachable=False, sent=True):
        self.code = code
        self.provenance = provenance
        self.retry_after = retry_after
        # The home did not answer at all (network, DNS, its proxy's 5xx): the
        # client may try a lab directly. `sent=False`: the request never left.
        self.unreachable = unreachable
        self.sent = sent
        super().__init__(code)


class JsonTransport:
    def __init__(self, selected_origin, *, client=None):
        self.origin = origin(selected_origin)
        self.client = client or httpx.Client(
            verify=ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or certifi.where()),
            timeout=5,
            follow_redirects=False,
            trust_env=False,
            # httpx drops an idle connection after 5 s; a notebook's next call
            # would pay a fresh TCP+TLS handshake. Stay under Caddy's 5 min.
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=60),
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
        frame=None,
        accept_framed=False,
    ):
        if (
            not path.startswith("/")
            or path.startswith("//")
            or any(c in path for c in ("?", "#", "\\", "%"))
        ):
            raise ValidationError("Invalid same-origin account path")
        headers = {"Accept": f"{FRAMED_CONTENT_TYPE}, application/json" if accept_framed else "application/json"}
        content = None
        if frame is not None:
            envelope = json.dumps(data, separators=(",", ":")).encode()
            content, data = len(envelope).to_bytes(4, "big") + envelope + bytes(frame), None
            headers["Content-Type"] = FRAMED_CONTENT_TYPE
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
                content=content,
                headers=headers,
                follow_redirects=False,
                timeout=timeout,
            ) as response:
                raw = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if time.monotonic() >= deadline:
                        raise AccountBackendError("Account operation deadline exceeded")
                    raw.extend(chunk)
                    if len(raw) > max_response_bytes:
                        raise AccountBackendError(
                            "Response exceeds account protocol limit"
                        )
                if 300 <= response.status_code < 400:
                    raise AccountBackendError("Account redirects are not allowed")
                if response.status_code >= 500 and not bytes(raw).lstrip().startswith(b"{"):
                    # The proxy answered for an app that is down (502/503): it never saw the request.
                    raise AccountBackendError("Account service unavailable", unreachable=True,
                                              sent=response.status_code not in (502, 503))
                if accept_framed and response.status_code < 300 and (
                    response.headers.get("content-type", "").split(";")[0].strip() == FRAMED_CONTENT_TYPE
                ):
                    return _unframe(bytes(raw), max_response_bytes)
                value = strict_json(bytes(raw), max_bytes=max_response_bytes)
                if response.status_code >= 400:
                    # Never print arbitrary server text (could contain secrets/terminal escapes).
                    error = value.get("error") if isinstance(value, dict) else None
                    code = error.get("code") if isinstance(error, dict) else None
                    import re

                    if not isinstance(code, str) or not re.fullmatch(
                        "[a-z_]{1,64}", code
                    ):
                        # A bare 404 is a route this server does not have.
                        code = "not_found" if response.status_code == 404 else "account_request_failed"
                    provenance = (
                        error.get("provenance") if isinstance(error, dict) else None
                    )
                    if provenance not in {None, "home", "transport", "destination"}:
                        provenance = None
                    wait = error.get("retry_after") if isinstance(error, dict) else None
                    if type(wait) is not int or not 1 <= wait <= 3600:
                        wait = None
                    raise AccountBackendError(code, provenance=provenance, retry_after=wait)
                if type(value) is not dict:
                    raise AccountBackendError("Invalid account response")
                return value
        except httpx.HTTPError as exc:
            raise AccountBackendError(
                "Account connection failed; verify the target and TLS configuration",
                unreachable=True,
                sent=not isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)),
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


def _unframe(raw, max_bytes):
    """A framed body -> {**envelope, "frame": frame}."""
    size = int.from_bytes(raw[:4], "big")
    if len(raw) < 4 or 4 + size > len(raw):
        raise AccountBackendError("Invalid home device response")
    try:
        value = strict_json(raw[4:4 + size], max_bytes=max_bytes)
    except ValidationError:
        raise AccountBackendError("Invalid home device response") from None
    if type(value) is not dict:
        raise AccountBackendError("Invalid home device response")
    return {**value, "frame": raw[4 + size:]}
