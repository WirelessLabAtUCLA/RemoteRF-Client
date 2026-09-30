"""The fixed dial and the punch run at once: whichever connects first is the
path, the other is cancelled, and when both fail the reason names both."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from remoteRF.core import direct_path

from test_gate_i_direct_path import SERVER_NAME, FakeServer, write_certs


class RaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ca_pem = write_certs(Path(self._tmp.name))

    def _path(self, offer):
        path = direct_path.DirectPath(offer=offer, server_name=SERVER_NAME, ca_pem=self.ca_pem, stun=None)
        self.addCleanup(path.stop)
        path.start()
        return path

    def test_the_punch_wins_while_the_fixed_dial_still_hangs(self):
        server = FakeServer(Path(self._tmp.name), lambda request: {})
        self.addCleanup(server.close)
        outcome = []

        async def hang(self, answer, configuration):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                outcome.append("cancelled")
                raise

        with mock.patch.object(direct_path.DirectPath, "_dial_fixed", hang):
            path = self._path(lambda offer: {**server.offer(offer), "udp": "127.0.0.1:9", "session": "s"})
            self.assertTrue(path.wait(15), path.describe())
        self.assertFalse(path.fixed)
        self.assertTrue(path.describe().startswith("direct "), path.describe())
        self.assertEqual(outcome, ["cancelled"])
        self.assertEqual(len(path._tasks), 3)

    def test_both_failing_names_both(self):
        async def fixed(self, answer, configuration):
            raise asyncio.TimeoutError

        async def punch(self, ice, answer, configuration):
            raise ConnectionError("ICE negotiation failed")

        with mock.patch.object(direct_path.DirectPath, "_dial_fixed", fixed), \
             mock.patch.object(direct_path.DirectPath, "_punch", punch):
            path = self._path(lambda offer: {"ufrag": "a", "pwd": "b", "candidates": [], "udp": "h:1", "session": "s"})
            self.assertFalse(path.wait(15))
        self.assertEqual(path.state, "down")
        self.assertEqual(path.reason, "fixed port: timed out; punch: ConnectionError: ICE negotiation failed")

    def test_a_punch_alone_keeps_its_own_reason(self):
        async def punch(self, ice, answer, configuration):
            raise asyncio.TimeoutError

        with mock.patch.object(direct_path.DirectPath, "_punch", punch):
            path = self._path(lambda offer: {"ufrag": "a", "pwd": "b", "candidates": []})
            self.assertFalse(path.wait(15))
        self.assertEqual(path.reason, "timed out")

    def test_the_offer_says_this_client_punches_at_once(self):
        seen = []

        def offer(request):
            seen.append(request)
            return {"ufrag": "a", "pwd": "b", "candidates": []}

        path = self._path(offer)
        path.wait(15)
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0]["race"], True)





class DescribeTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, direct_path, "_path", None)
        direct_path._path = None

    def _describe(self, routes, endpoint="10.0.0.5:12321"):
        profile = mock.Mock(home="lab", grpc_endpoint=endpoint)
        with mock.patch("remoteRF.core.grpc_client._current_profile", return_value=profile), \
             mock.patch("remoteRF.deployment.homes.load_home", return_value={"routes": routes}):
            return direct_path.describe()

    def test_a_lan_route_is_not_the_relay(self):
        self.assertEqual(self._describe([{"kind": "lan", "host": "10.0.0.5", "port": 12321}]), "LAN (direct)")
        self.assertEqual(self._describe([{"kind": "direct", "host": "10.0.0.5", "port": 12321}]), "direct")

    def test_a_relay_route_and_a_failed_lookup_keep_the_old_wording(self):
        self.assertEqual(self._describe([{"kind": "relay", "host": "10.0.0.5", "port": 12321}]), "relay (direct path off)")
        with mock.patch("remoteRF.core.grpc_client._current_profile", side_effect=RuntimeError("not configured")):
            self.assertEqual(direct_path.describe(), "relay (direct path off)")


class QuietWaiterTests(unittest.TestCase):
    def _run(self, exc):
        loop = asyncio.new_event_loop()
        seen = []
        try:
            loop.default_exception_handler = lambda context: seen.append(context)
            direct_path.quiet_aioice_errors(loop)
            loop.call_exception_handler({"message": "Future exception was never retrieved", "exception": exc})
        finally:
            loop.close()
        return seen

    def test_aioquic_orphaned_connect_waiter_is_silent(self):
        self.assertEqual(self._run(ConnectionError()), [])

    def test_other_unretrieved_exceptions_still_reach_the_default_handler(self):
        self.assertEqual(len(self._run(ValueError("not ours"))), 1)


if __name__ == "__main__":
    unittest.main()
