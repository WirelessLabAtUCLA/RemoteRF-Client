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

"""Typed HTTP client for the RemoteRF Global control-plane API.

Endpoint paths and request/response shapes are taken directly from
`remoterf-vps-global` (`src/remoterf_global/api/*.py`,
`src/remoterf_global/schemas/*.py`), which is this contract's source of
truth. Two response shapes are worth flagging because they're easy to get
wrong by guessing:

* Every endpoint except device-token polling reports errors as
  ``{"error": {"code": "...", "message": "..."}}`` (`errors.py` in the
  server: ``api_error_handler``).
* ``POST /v1/auth/device/token`` reports polling errors as
  ``{"error": "authorization_pending"}`` -- a bare string, not an object
  (`api/device_auth.py`: ``return JSONResponse(..., content={"error":
  exc.error}, ...)``). `poll_device_token` below parses that shape
  specifically; do not reuse the generic error parser for it.

This module only ever talks to the configured Global base URL -- it has no
notion of "the current deployment" and no code path that could attach a
Global bearer token to a request against a different origin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, urlsplit

import httpx

from .errors import GlobalClientError, GlobalUnavailableError, InvalidUsageError
from .schemas import (
    AccessAssertionResponse,
    ConnectionDescriptor,
    DeploymentSummary,
    DeviceCodeResponse,
    MeResponse,
    ResourceSummary,
    TokenPairResponse,
)

DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
DEFAULT_CLIENT_NAME = "remoterf-cli"


class GlobalApiHttpError(GlobalClientError):
    """A well-formed `{"error": {"code", "message"}}` response from Global."""

    def __init__(self, *, status_code: int, code: str, message: str, retry_after: Optional[float] = None):
        self.status_code = status_code
        self.code = code
        self.retry_after = retry_after
        super().__init__(message)

    @property
    def exit_category(self) -> str:  # type: ignore[override]
        if self.status_code == 401:
            return "authentication_required"
        if self.status_code == 403:
            return "authorization_denied"
        if self.status_code == 404:
            return "deployment_unavailable"
        if self.status_code == 429:
            return "network_unavailable"
        return "error"


class DevicePollHttpError(GlobalClientError):
    """One of authorization_pending / slow_down / expired_token /
    access_denied from `POST /v1/auth/device/token`."""

    exit_category = "authentication_required"

    def __init__(self, *, error: str, retry_after: Optional[float] = None):
        self.error = error
        self.retry_after = retry_after
        super().__init__(f"device token poll: {error}")


def _validate_base_url(url: str, *, allow_insecure_http: bool) -> str:
    url = url.rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.netloc:
        return url
    if parsed.scheme == "http" and parsed.netloc and allow_insecure_http:
        return url
    if parsed.scheme == "http" and parsed.netloc:
        raise InvalidUsageError(
            f"Refusing to use an insecure http:// RemoteRF Global URL ({url}) "
            "without explicit local-development opt-in "
            "(--global-url-allow-http / allow_insecure_http=True)."
        )
    raise InvalidUsageError(f"RemoteRF Global URL must be an http(s):// URL: {url!r}")


@dataclass(frozen=True)
class DevicePollOutcome:
    """Result of one poll: either a token pair, or a wait/stop signal."""

    token_pair: Optional[TokenPairResponse]
    error: Optional[str]  # authorization_pending | slow_down | expired_token | access_denied
    retry_after: Optional[float]


class GlobalApiClient:
    """Thin, typed HTTP client for one RemoteRF Global base URL.

    Redirects are never followed (httpx default `follow_redirects=False`,
    kept explicit here) so a bearer token can never leak to a different
    origin via a redirect response.
    """

    def __init__(
        self,
        base_url: str,
        *,
        allow_insecure_http: bool = False,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        client_name: str = DEFAULT_CLIENT_NAME,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.base_url = _validate_base_url(base_url, allow_insecure_http=allow_insecure_http)
        self.client_name = client_name
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GlobalApiClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # --- transport -----------------------------------------------------

    def _request(
        self, method: str, path: str, *, json_body: Optional[dict] = None, access_token: Optional[str] = None
    ) -> httpx.Response:
        headers = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        try:
            resp = self._client.request(method, path, json=json_body, headers=headers)
        except httpx.TimeoutException as exc:
            raise GlobalUnavailableError(f"Timed out contacting RemoteRF Global ({self.base_url}).") from exc
        except httpx.HTTPError as exc:
            raise GlobalUnavailableError(f"Could not reach RemoteRF Global ({self.base_url}): {exc}") from exc

        if resp.is_redirect:
            raise GlobalUnavailableError(
                f"RemoteRF Global returned an unexpected redirect for {path}; refusing to follow it."
            )
        return resp

    @staticmethod
    def _raise_generic_error(resp: httpx.Response) -> None:
        code, message = "unknown_error", resp.text[:200]
        try:
            body = resp.json()
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                err = body["error"]
                code = err.get("code", code)
                message = err.get("message", message)
        except ValueError:
            pass
        retry_after_hdr = resp.headers.get("Retry-After")
        retry_after = float(retry_after_hdr) if retry_after_hdr else None
        raise GlobalApiHttpError(status_code=resp.status_code, code=code, message=message, retry_after=retry_after)

    # --- device authorization grant (RFC 8628-inspired) -----------------

    def request_device_code(self) -> DeviceCodeResponse:
        resp = self._request("POST", "/v1/auth/device/code", json_body={"client_name": self.client_name})
        if resp.status_code != 200:
            self._raise_generic_error(resp)
        return DeviceCodeResponse.model_validate(resp.json())

    def poll_device_token(self, device_code: str) -> DevicePollOutcome:
        resp = self._request(
            "POST",
            "/v1/auth/device/token",
            json_body={"device_code": device_code, "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
        )
        if resp.status_code == 200:
            return DevicePollOutcome(token_pair=TokenPairResponse.model_validate(resp.json()), error=None, retry_after=None)

        # This endpoint reports errors as {"error": "<bare string>"}, not
        # the generic {"error": {"code","message"}} envelope.
        error = "unknown_error"
        try:
            body = resp.json()
            if isinstance(body, dict) and isinstance(body.get("error"), str):
                error = body["error"]
        except ValueError:
            pass
        retry_after_hdr = resp.headers.get("Retry-After")
        retry_after = float(retry_after_hdr) if retry_after_hdr else None
        return DevicePollOutcome(token_pair=None, error=error, retry_after=retry_after)

    # --- session lifecycle -----------------------------------------------

    def refresh(self, refresh_token: str) -> TokenPairResponse:
        resp = self._request("POST", "/v1/auth/refresh", json_body={"refresh_token": refresh_token})
        if resp.status_code != 200:
            self._raise_generic_error(resp)
        return TokenPairResponse.model_validate(resp.json())

    def logout(self, refresh_token: str) -> None:
        resp = self._request("POST", "/v1/auth/logout", json_body={"refresh_token": refresh_token})
        if resp.status_code != 200:
            self._raise_generic_error(resp)

    def me(self, access_token: str) -> MeResponse:
        resp = self._request("GET", "/v1/me", access_token=access_token)
        if resp.status_code != 200:
            self._raise_generic_error(resp)
        return MeResponse.model_validate(resp.json())

    # --- deployment discovery ---------------------------------------------

    def list_deployments(self, access_token: Optional[str] = None) -> list[DeploymentSummary]:
        resp = self._request("GET", "/v1/deployments", access_token=access_token)
        if resp.status_code != 200:
            self._raise_generic_error(resp)
        return [DeploymentSummary.model_validate(d) for d in resp.json()]

    def get_deployment(self, slug: str, access_token: Optional[str] = None) -> DeploymentSummary:
        resp = self._request("GET", f"/v1/deployments/{quote(slug, safe='')}", access_token=access_token)
        if resp.status_code != 200:
            self._raise_generic_error(resp)
        return DeploymentSummary.model_validate(resp.json())

    def list_resources(self, slug: str, access_token: Optional[str] = None) -> list[ResourceSummary]:
        resp = self._request("GET", f"/v1/deployments/{quote(slug, safe='')}/resources", access_token=access_token)
        if resp.status_code != 200:
            self._raise_generic_error(resp)
        return [ResourceSummary.model_validate(r) for r in resp.json()]

    def get_connection_descriptor(self, slug: str, *, access_token: str) -> ConnectionDescriptor:
        resp = self._request("GET", f"/v1/deployments/{quote(slug, safe='')}/connection", access_token=access_token)
        if resp.status_code != 200:
            self._raise_generic_error(resp)
        return ConnectionDescriptor.model_validate(resp.json())

    def request_access_assertion(self, slug: str, *, access_token: str) -> AccessAssertionResponse:
        resp = self._request(
            "POST", f"/v1/deployments/{quote(slug, safe='')}/access-assertion", access_token=access_token
        )
        if resp.status_code != 200:
            self._raise_generic_error(resp)
        return AccessAssertionResponse.model_validate(resp.json())
