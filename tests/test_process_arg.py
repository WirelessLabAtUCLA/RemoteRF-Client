import unittest

import numpy as np

from remoteRF.common.utils.process_arg import map_arg, unmap_arg
from remoteRF.drivers.support import uhd


class ProcessArgTests(unittest.TestCase):
    def test_json_values_round_trip_dict_and_none(self):
        value = {"nested": {"x": 1}, "flag": True, "nothing": None}

        self.assertEqual(unmap_arg(map_arg(value)), value)
        self.assertIsNone(unmap_arg(map_arg(None)))

    def test_existing_scalar_values_still_round_trip(self):
        self.assertIs(unmap_arg(map_arg(True)), True)
        self.assertEqual(unmap_arg(map_arg(12)), 12)
        self.assertAlmostEqual(unmap_arg(map_arg(1.5)), 1.5)
        self.assertEqual(unmap_arg(map_arg("abc")), "abc")

    def test_arrays_and_list_values_still_round_trip_as_arrays(self):
        real = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
        complex_value = np.array([1 + 2j, 3 + 4j], dtype=np.complex64)

        np.testing.assert_allclose(unmap_arg(map_arg(real)), real)
        np.testing.assert_allclose(unmap_arg(map_arg([1, 2, 3])), np.array([1, 2, 3]))
        np.testing.assert_allclose(unmap_arg(map_arg(complex_value)), complex_value)

    def test_json_values_can_contain_arrays_and_mixed_return_tuples(self):
        samples = np.array([[1 + 2j, 3 + 4j]], dtype=np.complex64)
        value = (2, samples, {"error_code": "none"})

        count, restored_samples, metadata = unmap_arg(map_arg(value))

        self.assertEqual(count, 2)
        np.testing.assert_allclose(restored_samples, samples)
        self.assertEqual(metadata, {"error_code": "none"})

        nested = unmap_arg(map_arg({"samples": samples}))
        np.testing.assert_allclose(nested["samples"], samples)

    def test_payload_objects_round_trip_through_json_mapping(self):
        class PayloadObject:
            def as_payload(self):
                return {"kind": "payload", "value": 7}

        self.assertEqual(
            unmap_arg(map_arg(PayloadObject())),
            {"kind": "payload", "value": 7},
        )
        self.assertEqual(
            unmap_arg(map_arg({"wrapped": PayloadObject()})),
            {"wrapped": {"kind": "payload", "value": 7}},
        )

    def test_payload_objects_can_contain_nested_numpy_and_complex_values(self):
        samples = np.array([1 + 2j, 3 + 4j], dtype=np.complex64)

        class PayloadObject:
            def as_payload(self):
                return {
                    "samples": samples,
                    "scale": np.float32(0.5),
                    "offset": 1 + 0.25j,
                    "nested": (np.int64(3), {"flag": np.bool_(True)}),
                }

        restored = unmap_arg(map_arg(PayloadObject()))

        np.testing.assert_allclose(restored["samples"], samples)
        self.assertEqual(restored["scale"], 0.5)
        self.assertEqual(restored["offset"], 1 + 0.25j)
        self.assertEqual(restored["nested"], [3, {"flag": True}])

    def test_uhd_payload_objects_round_trip_as_plain_rpc_payloads(self):
        stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
        stream_args.channels = [0, 1]
        stream_args.args = {"foo": "bar"}

        self.assertEqual(
            unmap_arg(map_arg(stream_args)),
            {
                "__uhd_type__": "StreamArgs",
                "cpu_format": "fc32",
                "otw_format": "sc16",
                "args": {"foo": "bar"},
                "channels": [0, 1],
            },
        )

        cmd = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
        cmd.stream_now = False
        cmd.time_spec = uhd.types.TimeSpec({"secs": 2.5})
        cmd.num_samps = 128

        self.assertEqual(
            unmap_arg(map_arg(cmd)),
            {
                "__uhd_type__": "StreamCMD",
                "mode": "num_done",
                "stream_now": False,
                "time_spec": {"__uhd_type__": "TimeSpec", "secs": 2.5},
                "num_samps": 128,
            },
        )


if __name__ == "__main__":
    unittest.main()
