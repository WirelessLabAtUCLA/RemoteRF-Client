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

"""Client-local decoding for TI mmWave UART packets.

The initial profile implements the classic xWR68xx mmWave SDK out-of-box
header and common TLVs. Unknown TLVs are retained as bytes so newer firmware
can be transported without requiring a simultaneous server upgrade.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np

MAGIC_WORD = b"\x02\x01\x04\x03\x06\x05\x08\x07"
HEADER_SIZE = 40
MAX_FRAME_BYTES = 4 * 1024 * 1024

TLV_DETECTED_POINTS = 1
TLV_RANGE_PROFILE = 2
TLV_NOISE_PROFILE = 3
TLV_AZIMUTH_STATIC_HEAT_MAP = 4
TLV_RANGE_DOPPLER_HEAT_MAP = 5
TLV_STATS = 6
TLV_DETECTED_POINTS_SIDE_INFO = 7
TLV_AZIMUTH_ELEVATION_STATIC_HEAT_MAP = 8
TLV_TEMPERATURE_STATS = 9

TLV_NAMES = {
    TLV_DETECTED_POINTS: "detected_points",
    TLV_RANGE_PROFILE: "range_profile",
    TLV_NOISE_PROFILE: "noise_profile",
    TLV_AZIMUTH_STATIC_HEAT_MAP: "azimuth_static_heat_map",
    TLV_RANGE_DOPPLER_HEAT_MAP: "range_doppler_heat_map",
    TLV_STATS: "stats",
    TLV_DETECTED_POINTS_SIDE_INFO: "detected_points_side_info",
    TLV_AZIMUTH_ELEVATION_STATIC_HEAT_MAP: "azimuth_elevation_static_heat_map",
    TLV_TEMPERATURE_STATS: "temperature_stats",
}

_HEADER = struct.Struct("<8s8I")
_TLV_HEADER = struct.Struct("<II")


@dataclass(frozen=True)
class TiMmWaveHeader:
    version: int
    total_packet_length: int
    platform: int
    frame_number: int
    time_cpu_cycles: int
    num_detected_objects: int
    num_tlvs: int
    subframe_number: int

    @property
    def version_tuple(self) -> tuple[int, int, int, int]:
        return (
            (self.version >> 24) & 0xFF,
            (self.version >> 16) & 0xFF,
            (self.version >> 8) & 0xFF,
            self.version & 0xFF,
        )


@dataclass(frozen=True)
class TiMmWaveTlv:
    type: int
    name: str
    length: int
    raw: bytes
    value: Any


@dataclass(frozen=True)
class TiMmWaveFrame:
    header: TiMmWaveHeader
    tlvs: tuple[TiMmWaveTlv, ...]
    padding: bytes
    raw: bytes
    profile: str

    def first(self, tlv_type: int) -> TiMmWaveTlv | None:
        return next((item for item in self.tlvs if item.type == int(tlv_type)), None)

    def all(self, tlv_type: int) -> tuple[TiMmWaveTlv, ...]:
        return tuple(item for item in self.tlvs if item.type == int(tlv_type))

    @property
    def points(self) -> np.ndarray | None:
        item = self.first(TLV_DETECTED_POINTS)
        return item.value if item is not None else None

    @property
    def side_info(self) -> np.ndarray | None:
        item = self.first(TLV_DETECTED_POINTS_SIDE_INFO)
        return item.value if item is not None else None


def _require_multiple(payload: bytes, item_size: int, *, label: str) -> None:
    if len(payload) % item_size:
        raise ValueError(
            f"{label} payload length {len(payload)} is not a multiple of {item_size}"
        )


def _decode_tlv(tlv_type: int, payload: bytes, *, profile: str):
    if profile != "xwr68xx_oob_sdk3":
        return payload

    if tlv_type == TLV_DETECTED_POINTS:
        _require_multiple(payload, 16, label="detected-points")
        return np.frombuffer(payload, dtype="<f4").reshape(-1, 4).copy()
    if tlv_type in {TLV_RANGE_PROFILE, TLV_NOISE_PROFILE}:
        _require_multiple(payload, 2, label=TLV_NAMES[tlv_type])
        return np.frombuffer(payload, dtype="<u2").copy()
    if tlv_type in {
        TLV_AZIMUTH_STATIC_HEAT_MAP,
        TLV_AZIMUTH_ELEVATION_STATIC_HEAT_MAP,
    }:
        _require_multiple(payload, 4, label=TLV_NAMES[tlv_type])
        iq = np.frombuffer(payload, dtype="<i2").reshape(-1, 2)
        # TI serializes imaginary then real int16 components.
        return (iq[:, 1].astype(np.float32) + 1j * iq[:, 0]).astype(
            np.complex64,
            copy=False,
        )
    if tlv_type == TLV_RANGE_DOPPLER_HEAT_MAP:
        _require_multiple(payload, 2, label="range-doppler heat map")
        return np.frombuffer(payload, dtype="<u2").copy()
    if tlv_type == TLV_STATS:
        if len(payload) < 24:
            raise ValueError("stats TLV must contain at least 24 bytes")
        values = struct.unpack_from("<6I", payload)
        return {
            "inter_frame_processing_time_us": values[0],
            "transmit_output_time_us": values[1],
            "inter_frame_processing_margin_us": values[2],
            "inter_chirp_processing_margin_us": values[3],
            "active_frame_cpu_load_percent": values[4],
            "inter_frame_cpu_load_percent": values[5],
            "extra": payload[24:],
        }
    if tlv_type == TLV_DETECTED_POINTS_SIDE_INFO:
        _require_multiple(payload, 4, label="detected-points side-info")
        return np.frombuffer(payload, dtype="<u2").reshape(-1, 2).copy()
    return payload


def decode_frame(
    packet: bytes | bytearray | memoryview,
    *,
    profile: str = "xwr68xx_oob_sdk3",
) -> TiMmWaveFrame:
    """Decode one complete packet produced by ``TiMmWave.read_frame()``."""
    raw = bytes(packet)
    if len(raw) < HEADER_SIZE:
        raise ValueError(f"TI mmWave packet is shorter than {HEADER_SIZE} bytes")
    unpacked = _HEADER.unpack_from(raw)
    if unpacked[0] != MAGIC_WORD:
        raise ValueError("TI mmWave packet has an invalid magic word")

    header = TiMmWaveHeader(*unpacked[1:])
    if not HEADER_SIZE <= header.total_packet_length <= MAX_FRAME_BYTES:
        raise ValueError(
            f"invalid TI mmWave total packet length: {header.total_packet_length}"
        )
    if len(raw) != header.total_packet_length:
        raise ValueError(
            "TI mmWave packet length mismatch: "
            f"header={header.total_packet_length}, actual={len(raw)}"
        )

    offset = HEADER_SIZE
    tlvs: list[TiMmWaveTlv] = []
    for index in range(header.num_tlvs):
        if offset + _TLV_HEADER.size > len(raw):
            raise ValueError(f"TLV {index} header extends beyond the packet")
        tlv_type, payload_length = _TLV_HEADER.unpack_from(raw, offset)
        offset += _TLV_HEADER.size
        end = offset + payload_length
        if end > len(raw):
            raise ValueError(
                f"TLV {index} payload extends beyond the packet: "
                f"type={tlv_type}, length={payload_length}"
            )
        payload = raw[offset:end]
        offset = end
        tlvs.append(
            TiMmWaveTlv(
                type=tlv_type,
                name=TLV_NAMES.get(tlv_type, f"unknown_{tlv_type}"),
                length=payload_length,
                raw=payload,
                value=_decode_tlv(tlv_type, payload, profile=profile),
            )
        )

    return TiMmWaveFrame(
        header=header,
        tlvs=tuple(tlvs),
        padding=raw[offset:],
        raw=raw,
        profile=profile,
    )


class FrameDecoder:
    """Recover and decode packets from arbitrary serial or network chunks."""

    def __init__(
        self,
        *,
        profile: str = "xwr68xx_oob_sdk3",
        maximum_frame_bytes: int = MAX_FRAME_BYTES,
    ):
        self.profile = str(profile)
        self.maximum_frame_bytes = int(maximum_frame_bytes)
        self.buffer = bytearray()
        self.discarded_bytes = 0
        self.invalid_headers = 0

    def clear(self) -> None:
        self.buffer.clear()

    def feed(
        self,
        data: bytes | bytearray | memoryview,
    ) -> list[TiMmWaveFrame]:
        if data:
            self.buffer.extend(data)
        frames: list[TiMmWaveFrame] = []
        while True:
            magic_at = self.buffer.find(MAGIC_WORD)
            if magic_at < 0:
                keep = min(len(self.buffer), len(MAGIC_WORD) - 1)
                discarded = len(self.buffer) - keep
                if discarded:
                    del self.buffer[:discarded]
                    self.discarded_bytes += discarded
                break
            if magic_at:
                del self.buffer[:magic_at]
                self.discarded_bytes += magic_at
            if len(self.buffer) < 16:
                break

            total_length = struct.unpack_from("<I", self.buffer, 12)[0]
            if not HEADER_SIZE <= total_length <= self.maximum_frame_bytes:
                del self.buffer[0]
                self.discarded_bytes += 1
                self.invalid_headers += 1
                continue
            if len(self.buffer) < total_length:
                break
            packet = bytes(self.buffer[:total_length])
            del self.buffer[:total_length]
            frames.append(decode_frame(packet, profile=self.profile))
        return frames


def decode_chunks(
    chunks: Iterable[bytes],
    *,
    profile: str = "xwr68xx_oob_sdk3",
) -> list[TiMmWaveFrame]:
    decoder = FrameDecoder(profile=profile)
    frames: list[TiMmWaveFrame] = []
    for chunk in chunks:
        frames.extend(decoder.feed(chunk))
    return frames


__all__ = [
    "FrameDecoder",
    "HEADER_SIZE",
    "MAGIC_WORD",
    "MAX_FRAME_BYTES",
    "TLV_AZIMUTH_ELEVATION_STATIC_HEAT_MAP",
    "TLV_AZIMUTH_STATIC_HEAT_MAP",
    "TLV_DETECTED_POINTS",
    "TLV_DETECTED_POINTS_SIDE_INFO",
    "TLV_NAMES",
    "TLV_NOISE_PROFILE",
    "TLV_RANGE_DOPPLER_HEAT_MAP",
    "TLV_RANGE_PROFILE",
    "TLV_STATS",
    "TLV_TEMPERATURE_STATS",
    "TiMmWaveFrame",
    "TiMmWaveHeader",
    "TiMmWaveTlv",
    "decode_chunks",
    "decode_frame",
]

