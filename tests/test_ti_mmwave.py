from __future__ import annotations

import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np

from remoteRF.common.utils import map_arg, unmap_arg

fake_grpc_client = types.ModuleType("remoteRF.core.grpc_client")
fake_grpc_client.rpc_client = lambda *args, **kwargs: None
sys.modules.setdefault("remoteRF.core.grpc_client", fake_grpc_client)

from remoteRF.drivers import dynamic_device
from remoteRF.drivers.dynamic_device import _codegen
from remoteRF.drivers.support import ti_mmwave


def _tlv(tlv_type: int, payload: bytes) -> bytes:
    return struct.pack("<II", tlv_type, len(payload)) + payload


def _packet(*, frame_number=7, tlvs=(), padding=b"") -> bytes:
    body = b"".join(tlvs) + padding
    total = ti_mmwave.HEADER_SIZE + len(body)
    return struct.pack(
        "<8s8I",
        ti_mmwave.MAGIC_WORD,
        0x03060200,
        total,
        0xA6843,
        frame_number,
        123456,
        2,
        len(tlvs),
        0,
    ) + body


class TiMmWaveDecoderTests(unittest.TestCase):
    def test_decodes_common_xwr68xx_tlvs_and_preserves_unknown(self):
        points = np.array(
            [[1.0, 2.0, 3.0, -0.5], [4.0, 5.0, 6.0, 0.25]],
            dtype="<f4",
        )
        side_info = np.array([[120, 30], [80, 20]], dtype="<u2")
        stats = struct.pack("<6I", 100, 20, 900, 30, 10, 5)
        packet = _packet(
            tlvs=(
                _tlv(ti_mmwave.TLV_DETECTED_POINTS, points.tobytes()),
                _tlv(
                    ti_mmwave.TLV_DETECTED_POINTS_SIDE_INFO,
                    side_info.tobytes(),
                ),
                _tlv(ti_mmwave.TLV_STATS, stats),
                _tlv(0xFEED, b"future-format"),
            ),
            padding=b"\0\0\0\0",
        )

        frame = ti_mmwave.decode_frame(packet)

        self.assertEqual(frame.header.version_tuple, (3, 6, 2, 0))
        self.assertEqual(frame.header.frame_number, 7)
        np.testing.assert_array_equal(frame.points, points)
        np.testing.assert_array_equal(frame.side_info, side_info)
        self.assertEqual(
            frame.first(ti_mmwave.TLV_STATS).value[
                "inter_frame_processing_time_us"
            ],
            100,
        )
        self.assertEqual(frame.first(0xFEED).value, b"future-format")
        self.assertEqual(frame.padding, b"\0\0\0\0")

    def test_stream_decoder_recovers_split_packets_after_noise(self):
        first = _packet(frame_number=1)
        second = _packet(frame_number=2)
        decoder = ti_mmwave.FrameDecoder()

        self.assertEqual(decoder.feed(b"noise" + first[:17]), [])
        frames = decoder.feed(first[17:] + second)

        self.assertEqual([item.header.frame_number for item in frames], [1, 2])
        self.assertEqual(decoder.discarded_bytes, 5)

    def test_rejects_truncated_or_malformed_tlvs(self):
        with self.assertRaisesRegex(ValueError, "shorter"):
            ti_mmwave.decode_frame(b"short")

        malformed = _packet(tlvs=(_tlv(1, b"not-16-byte-aligned"),))
        with self.assertRaisesRegex(ValueError, "multiple of 16"):
            ti_mmwave.decode_frame(malformed)

        wrong_length = bytearray(_packet())
        struct.pack_into("<I", wrong_length, 12, len(wrong_length) + 1)
        with self.assertRaisesRegex(ValueError, "length mismatch"):
            ti_mmwave.decode_frame(wrong_length)

    def test_nonbaseline_profile_keeps_payloads_raw(self):
        payload = np.arange(8, dtype="<u2").tobytes()
        frame = ti_mmwave.decode_frame(
            _packet(tlvs=(_tlv(ti_mmwave.TLV_RANGE_PROFILE, payload),)),
            profile="custom_lab_firmware",
        )
        self.assertEqual(frame.first(ti_mmwave.TLV_RANGE_PROFILE).value, payload)


class TiMmWaveCodegenTests(unittest.TestCase):
    @staticmethod
    def _schema():
        return {
            "schema_version": "1.0",
            "device_type": "ti_mmwave",
            "client_class": "TiMmWave",
            "client_modules": {
                "ti_mmwave": "remoteRF.drivers.support.ti_mmwave"
            },
            "driver_version": "0.1.0",
            "schema_hash": "sha256:ti-mmwave-test",
            "getters": {
                "get_device_info": {"doc": "identity"},
            },
            "setters": {},
            "calls": {
                "call_read_frame": {
                    "args": [
                        {
                            "name": "timeout",
                            "required": False,
                            "default": 1.0,
                            "kind": "positional_or_keyword",
                        }
                    ]
                }
            },
        }

    def test_generated_client_transports_bytes_and_uses_local_decoder(self):
        packet = _packet()
        seen = []

        class Response:
            def __init__(self, results):
                self.results = results

        def rpc_client(function_name, args):
            seen.append((function_name, args))
            prop = function_name.split(":")[1]
            if prop == "device_info":
                return Response({prop: map_arg({"bridge_serial": "00DF4F69"})})
            if prop == "read_frame":
                return Response({prop: map_arg(packet)})
            raise AssertionError(function_name)

        fake_grpc_client = __import__(
            "remoteRF.core.grpc_client",
            fromlist=["rpc_client"],
        )
        old_rpc = fake_grpc_client.rpc_client
        old_stale = dynamic_device.install_driver_if_stale
        fake_grpc_client.rpc_client = rpc_client
        dynamic_device.install_driver_if_stale = lambda **_kwargs: False
        try:
            module = types.ModuleType(
                "remoteRF.drivers.ti_mmwave.ti_mmwave_remote"
            )
            module.__package__ = "remoteRF.drivers.ti_mmwave"
            exec(_codegen(self._schema()), module.__dict__)
            radar = module.TiMmWave("token")
            self.assertEqual(radar.device_info["bridge_serial"], "00DF4F69")
            frame = module.ti_mmwave.decode_frame(radar.read_frame(timeout=0.5))
        finally:
            fake_grpc_client.rpc_client = old_rpc
            dynamic_device.install_driver_if_stale = old_stale

        self.assertEqual(frame.header.frame_number, 7)
        self.assertEqual(seen[-1][0], "Ti_mmwave:read_frame:CALLN")
        self.assertEqual(unmap_arg(seen[-1][1]["timeout"]), 0.5)

    def test_generated_package_exports_parser_helpers(self):
        old_drivers_dir = dynamic_device._DRIVERS_DIR
        try:
            with tempfile.TemporaryDirectory() as temp:
                dynamic_device._DRIVERS_DIR = Path(temp)
                package = dynamic_device._write_driver_files(self._schema())
                init_text = (package / "__init__.py").read_text(encoding="utf-8")
                remote_text = (package / "ti_mmwave_remote.py").read_text(
                    encoding="utf-8"
                )
        finally:
            dynamic_device._DRIVERS_DIR = old_drivers_dir

        self.assertIn("remoteRF.drivers.support.ti_mmwave", init_text)
        self.assertIn('__all__.append("ti_mmwave")', init_text)
        self.assertIn(
            'for _name in getattr(ti_mmwave, "__all__", ()):',
            init_text,
        )
        self.assertIn(
            "'ti_mmwave': 'remoteRF.drivers.support.ti_mmwave'",
            remote_text,
        )


if __name__ == "__main__":
    unittest.main()
