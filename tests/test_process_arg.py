import unittest

import numpy as np

from remoteRF.common.utils.process_arg import map_arg, unmap_arg


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


if __name__ == "__main__":
    unittest.main()
