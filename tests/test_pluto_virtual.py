import sys
import unittest

import numpy as np

from remoteRF.drivers.adalm_pluto import adi


class VirtualPlutoTests(unittest.TestCase):
    def test_virtual_pluto_does_not_initialize_grpc(self):
        grpc_was_loaded = "remoteRF.core.grpc_client" in sys.modules
        sdr = adi.Pluto(virtual=True)
        self.assertTrue(sdr.virtual)
        self.assertEqual(
            "remoteRF.core.grpc_client" in sys.modules,
            grpc_was_loaded,
        )

    def test_properties_round_trip_locally(self):
        sdr = adi.Pluto(token="not-a-real-token", virtual=True)
        sdr.sample_rate = 1_000_000
        sdr.rx_lo = 915_000_000
        sdr.gain_control_mode_chan0 = "manual"

        self.assertEqual(sdr.sample_rate, 1_000_000)
        self.assertEqual(sdr.rx_lo, 915_000_000)
        self.assertEqual(sdr.gain_control_mode_chan0, "manual")

    def test_rx_returns_zero_filled_complex_buffer(self):
        sdr = adi.Pluto(virtual=True)
        sdr.rx_buffer_size = 32

        samples = sdr.rx()

        self.assertIsInstance(samples, np.ndarray)
        self.assertEqual(samples.dtype, np.dtype(np.complex64))
        self.assertEqual(samples.shape, (32,))
        self.assertTrue(np.all(samples == 0))

    def test_tx_is_a_local_no_op_sink(self):
        sdr = adi.Pluto(virtual=True)
        result = sdr.tx(np.array([1 + 2j, 3 + 4j], dtype=np.complex64))
        self.assertIsNone(result)
        sdr.tx_destroy_buffer()

    def test_virtual_instances_have_isolated_state(self):
        first = adi.Pluto(token="same-label", virtual=True)
        second = adi.Pluto(token="same-label", virtual=True)
        first.sample_rate = 123
        self.assertNotEqual(first.sample_rate, second.sample_rate)

    def test_real_mode_still_requires_token(self):
        with self.assertRaisesRegex(ValueError, "reservation token"):
            adi.Pluto()


if __name__ == "__main__":
    unittest.main()
