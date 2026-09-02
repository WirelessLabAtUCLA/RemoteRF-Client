import unittest
from unittest import mock

import httpx

from remoteRF.global_client.api_client import DevicePollOutcome, GlobalApiClient
from remoteRF.global_client.device_flow import run_device_login
from remoteRF.global_client.errors import DeviceLoginDeniedError, DeviceLoginExpiredError
from remoteRF.global_client.schemas import DeviceCodeResponse, TokenPairResponse


def _device_code(*, interval=5, expires_in=600) -> DeviceCodeResponse:
    return DeviceCodeResponse(
        device_code="super-secret-device-code",
        user_code="ABCD-1234",
        verification_uri="https://global.example/activate",
        verification_uri_complete="https://global.example/activate?user_code=ABCD-1234",
        expires_in=expires_in,
        interval=interval,
    )


class FakeApi:
    """Stand-in for GlobalApiClient that returns a scripted sequence of
    poll outcomes, so tests never sleep in real time or hit a network."""

    def __init__(self, device_code_response, poll_outcomes):
        self._device_code_response = device_code_response
        self._poll_outcomes = list(poll_outcomes)

    def request_device_code(self):
        return self._device_code_response

    def poll_device_token(self, device_code):
        self.last_device_code = device_code
        return self._poll_outcomes.pop(0)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def monotonic(self):
        return self.now


class DeviceFlowTests(unittest.TestCase):
    def test_successful_login_after_pending_polls(self):
        outcomes = [
            DevicePollOutcome(token_pair=None, error="authorization_pending", retry_after=None),
            DevicePollOutcome(token_pair=None, error="authorization_pending", retry_after=None),
            DevicePollOutcome(
                token_pair=TokenPairResponse(access_token="a", refresh_token="r", expires_in=900),
                error=None, retry_after=None,
            ),
        ]
        api = FakeApi(_device_code(), outcomes)
        clock = FakeClock()

        pair = run_device_login(
            api, no_browser=True, sleep=clock.sleep, monotonic=clock.monotonic, open_browser=lambda url: True,
        )
        self.assertEqual(pair.access_token, "a")

    def test_device_code_is_used_for_polling_but_never_returned_to_caller(self):
        prompts = []
        outcomes = [DevicePollOutcome(
            token_pair=TokenPairResponse(access_token="a", refresh_token="r", expires_in=900),
            error=None, retry_after=None,
        )]
        api = FakeApi(_device_code(), outcomes)
        clock = FakeClock()

        run_device_login(
            api, no_browser=True, sleep=clock.sleep, monotonic=clock.monotonic,
            open_browser=lambda url: True, on_prompt=prompts.append,
        )

        self.assertEqual(api.last_device_code, "super-secret-device-code")
        self.assertEqual(len(prompts), 1)
        # The prompt object must not carry the raw device_code anywhere.
        prompt_fields = vars(prompts[0]).values()
        self.assertNotIn("super-secret-device-code", prompt_fields)

    def test_only_user_code_and_urls_are_shown_via_on_prompt(self):
        prompts = []
        outcomes = [DevicePollOutcome(
            token_pair=TokenPairResponse(access_token="a", refresh_token="r", expires_in=900),
            error=None, retry_after=None,
        )]
        api = FakeApi(_device_code(), outcomes)
        clock = FakeClock()
        run_device_login(
            api, no_browser=True, sleep=clock.sleep, monotonic=clock.monotonic,
            open_browser=lambda url: True, on_prompt=prompts.append,
        )
        self.assertEqual(prompts[0].user_code, "ABCD-1234")
        self.assertEqual(prompts[0].verification_uri, "https://global.example/activate")

    def test_no_browser_flag_skips_open_browser(self):
        called = []
        outcomes = [DevicePollOutcome(
            token_pair=TokenPairResponse(access_token="a", refresh_token="r", expires_in=900),
            error=None, retry_after=None,
        )]
        api = FakeApi(_device_code(), outcomes)
        clock = FakeClock()
        run_device_login(
            api, no_browser=True, sleep=clock.sleep, monotonic=clock.monotonic,
            open_browser=lambda url: called.append(url) or True,
        )
        self.assertEqual(called, [])

    def test_browser_open_failure_still_completes_login(self):
        def failing_browser(url):
            raise RuntimeError("no display")

        outcomes = [DevicePollOutcome(
            token_pair=TokenPairResponse(access_token="a", refresh_token="r", expires_in=900),
            error=None, retry_after=None,
        )]
        api = FakeApi(_device_code(), outcomes)
        clock = FakeClock()
        prompts = []
        pair = run_device_login(
            api, no_browser=False, sleep=clock.sleep, monotonic=clock.monotonic,
            open_browser=failing_browser, on_prompt=prompts.append,
        )
        self.assertEqual(pair.access_token, "a")
        self.assertFalse(prompts[0].browser_opened)
        # falls back to showing the manual completion URL -- exercised via on_prompt

    def test_slow_down_grows_the_polling_interval(self):
        outcomes = [
            DevicePollOutcome(token_pair=None, error="slow_down", retry_after=10.0),
            DevicePollOutcome(
                token_pair=TokenPairResponse(access_token="a", refresh_token="r", expires_in=900),
                error=None, retry_after=None,
            ),
        ]
        api = FakeApi(_device_code(interval=1), outcomes)
        clock = FakeClock()
        run_device_login(api, no_browser=True, sleep=clock.sleep, monotonic=clock.monotonic, open_browser=lambda u: True)
        # First sleep is the initial interval (1s); second sleep must honor
        # the server's slow_down retry_after (>= 10s), not stay at 1s.
        self.assertGreaterEqual(clock.now, 1.0 + 10.0)

    def test_expired_token_raises(self):
        outcomes = [DevicePollOutcome(token_pair=None, error="expired_token", retry_after=None)]
        api = FakeApi(_device_code(), outcomes)
        clock = FakeClock()
        with self.assertRaises(DeviceLoginExpiredError):
            run_device_login(api, no_browser=True, sleep=clock.sleep, monotonic=clock.monotonic, open_browser=lambda u: True)

    def test_access_denied_raises(self):
        outcomes = [DevicePollOutcome(token_pair=None, error="access_denied", retry_after=None)]
        api = FakeApi(_device_code(), outcomes)
        clock = FakeClock()
        with self.assertRaises(DeviceLoginDeniedError):
            run_device_login(api, no_browser=True, sleep=clock.sleep, monotonic=clock.monotonic, open_browser=lambda u: True)

    def test_wall_clock_expiry_raises_even_without_an_expired_token_response(self):
        # expires_in=1 second; the clock jumps past it during the first
        # sleep. The server is still saying authorization_pending (it has
        # its own, separate expiry check) -- the client must still stop on
        # its own wall-clock deadline rather than polling forever.
        outcomes = [DevicePollOutcome(token_pair=None, error="authorization_pending", retry_after=None)] * 5
        api = FakeApi(_device_code(expires_in=1), outcomes)
        clock = FakeClock()

        def advancing_sleep(seconds):
            clock.now += seconds + 100  # jump well past the deadline

        with self.assertRaises(DeviceLoginExpiredError):
            run_device_login(
                api, no_browser=True, sleep=advancing_sleep, monotonic=clock.monotonic, open_browser=lambda u: True,
            )

    def test_ctrl_c_during_poll_propagates_cleanly(self):
        def raising_sleep(seconds):
            raise KeyboardInterrupt()

        api = FakeApi(_device_code(), [])
        with self.assertRaises(KeyboardInterrupt):
            run_device_login(
                api, no_browser=True, sleep=raising_sleep, monotonic=lambda: 0.0, open_browser=lambda u: True,
            )


if __name__ == "__main__":
    unittest.main()
