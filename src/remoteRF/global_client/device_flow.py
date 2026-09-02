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

"""CLI device-code login (RFC 8628-inspired), matching the actual
`remoterf_global` server implementation (`services/tokens.py`:
`create_device_authorization` / `poll_device_token`).

The CLI never asks for a Global password. Only the short, low-entropy,
human `user_code` is displayed; the high-entropy `device_code` used for
polling is never printed or logged.
"""

from __future__ import annotations

import math
import time
import webbrowser
from dataclasses import dataclass
from typing import Callable, Optional

from .api_client import GlobalApiClient
from .errors import DeviceLoginDeniedError, DeviceLoginExpiredError, GlobalUnavailableError
from .schemas import TokenPairResponse

MIN_POLL_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class DeviceLoginPrompt:
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    browser_opened: bool


def run_device_login(
    api: GlobalApiClient,
    *,
    no_browser: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    open_browser: Callable[[str], bool] = webbrowser.open,
    on_prompt: Optional[Callable[[DeviceLoginPrompt], None]] = None,
) -> TokenPairResponse:
    """Run one full device-code login: request a code, (try to) open the
    browser, then poll until approval/denial/expiry. Raises on any
    non-success outcome; returns the Global token pair on success.

    Safe to interrupt with Ctrl-C at any point: nothing is persisted by
    this function (the caller stores the returned token pair), so a
    KeyboardInterrupt here can never leave stored credentials half-written.
    """
    device = api.request_device_code()

    browser_opened = False
    if not no_browser:
        try:
            browser_opened = bool(open_browser(device.verification_uri_complete))
        except Exception:
            browser_opened = False

    if on_prompt is not None:
        on_prompt(
            DeviceLoginPrompt(
                user_code=device.user_code,
                verification_uri=device.verification_uri,
                verification_uri_complete=device.verification_uri_complete,
                expires_in=device.expires_in,
                browser_opened=browser_opened,
            )
        )

    interval = max(float(device.interval), MIN_POLL_INTERVAL_SECONDS)
    deadline = monotonic() + device.expires_in

    while True:
        if monotonic() >= deadline:
            raise DeviceLoginExpiredError(
                "The RemoteRF Global login code expired before it was approved. Run: remoterf global login"
            )

        sleep(interval)

        outcome = api.poll_device_token(device.device_code)
        if outcome.token_pair is not None:
            return outcome.token_pair

        if outcome.error == "authorization_pending":
            continue
        if outcome.error == "slow_down":
            # Server tells us how much longer until the next allowed poll;
            # honor it and grow our interval so we don't immediately
            # collide with slow_down again.
            wait = outcome.retry_after if outcome.retry_after is not None else interval
            interval = max(interval, math.ceil(wait)) + 1.0
            continue
        if outcome.error == "expired_token":
            raise DeviceLoginExpiredError(
                "The RemoteRF Global login code expired or was already used. Run: remoterf global login"
            )
        if outcome.error == "access_denied":
            raise DeviceLoginDeniedError("RemoteRF Global login was denied.")

        raise GlobalUnavailableError(f"Unexpected device-login response from RemoteRF Global: {outcome.error!r}")
