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

"""Centralized Global access-token lifecycle: one authenticated client
wrapping `GlobalApiClient` + `GlobalCredentialStore`.

Behavior (per spec):

1. Load the current access token.
2. If it's near expiration, or the call comes back 401, use the refresh
   token to mint a new pair.
3. Store the newly rotated refresh token atomically (`SecretStore.set` is
   atomic per backend -- os.replace for the file store, a single keyring
   write for the keyring store).
4. Retry the original request at most once.
5. If refresh fails, clear the invalid credentials and require login.

All calls here are read-only discovery calls or the assertion-request call
(which always mints a fresh, single-use assertion, so re-issuing it after a
transport-level retry is safe) -- never a Global mutation that would be
unsafe to send twice.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from .api_client import GlobalApiClient, GlobalApiHttpError
from .credentials import GlobalCredentials, GlobalCredentialStore
from .errors import GlobalAuthenticationExpiredError, NotLoggedInError
from .schemas import AccessAssertionResponse, ConnectionDescriptor, DeploymentSummary, MeResponse, ResourceSummary

T = TypeVar("T")


class AuthenticatedGlobalClient:
    def __init__(self, api: GlobalApiClient, credential_store: GlobalCredentialStore):
        self._api = api
        self._store = credential_store

    # --- token lifecycle -------------------------------------------------

    def _require_credentials(self) -> GlobalCredentials:
        creds = self._store.load()
        if creds is None:
            raise NotLoggedInError("Not logged in to RemoteRF Global. Run: remoterf global login")
        return creds

    def _refresh(self, creds: GlobalCredentials) -> GlobalCredentials:
        try:
            pair = self._api.refresh(creds.refresh_token)
        except Exception as exc:  # GlobalApiHttpError or GlobalUnavailableError
            self._store.clear()
            raise GlobalAuthenticationExpiredError(
                "Your RemoteRF Global session expired and could not be refreshed. "
                "Run: remoterf global login"
            ) from exc
        new_creds = GlobalCredentials.from_token_pair(pair.access_token, pair.refresh_token, pair.expires_in)
        self._store.save(new_creds)
        return new_creds

    def _current_access_token(self) -> str:
        creds = self._require_credentials()
        if creds.is_access_token_expired():
            creds = self._refresh(creds)
        return creds.access_token

    def _call(self, fn: Callable[[str], T]) -> T:
        token = self._current_access_token()
        try:
            return fn(token)
        except GlobalApiHttpError as exc:
            if exc.status_code != 401:
                raise
            # One bounded retry after a fresh refresh -- never loop.
            creds = self._refresh(self._require_credentials())
            return fn(creds.access_token)

    def logout(self) -> None:
        """Best-effort revoke + unconditional local clear (see errors.py's
        GlobalUnavailableError being swallowed here deliberately: logout
        must still remove local credentials even if Global is unreachable)."""
        creds = self._store.load()
        if creds is not None:
            try:
                self._api.logout(creds.refresh_token)
            except Exception:
                pass
        self._store.clear()

    # --- authenticated calls -----------------------------------------------

    def me(self) -> MeResponse:
        return self._call(self._api.me)

    def list_deployments(self) -> list[DeploymentSummary]:
        return self._call(self._api.list_deployments)

    def get_deployment(self, slug: str) -> DeploymentSummary:
        return self._call(lambda token: self._api.get_deployment(slug, access_token=token))

    def list_resources(self, slug: str) -> list[ResourceSummary]:
        return self._call(lambda token: self._api.list_resources(slug, access_token=token))

    def get_connection_descriptor(self, slug: str) -> ConnectionDescriptor:
        return self._call(lambda token: self._api.get_connection_descriptor(slug, access_token=token))

    def request_access_assertion(self, slug: str) -> AccessAssertionResponse:
        return self._call(lambda token: self._api.request_access_assertion(slug, access_token=token))
