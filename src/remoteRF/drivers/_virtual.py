"""Client-only virtual device support.

This module intentionally models only enough device behavior for application
scripts to run without a RemoteRF server or physical hardware.  It is not an
RF or channel emulator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count
from typing import Any

import numpy as np


_VIRTUAL_IDS = count(1)


def _pluto_defaults() -> dict[str, Any]:
    return {
        "filter": None,
        "loopback": 0,
        "gain_control_mode_chan0": "slow_attack",
        "rx_hardwaregain_chan0": 0.0,
        "tx_hardwaregain_chan0": -10.0,
        "rx_rf_bandwidth": 2_000_000,
        "tx_rf_bandwidth": 2_000_000,
        "sample_rate": 2_500_000,
        "rx_lo": 2_400_000_000,
        "tx_lo": 2_400_000_000,
        "tx_cyclic_buffer": False,
        "rx_buffer_size": 1024,
        "rx_dec8_filter_en": False,
        "tx_int8_filter_en": False,
        "rx_enabled_channels": [0],
        "tx_enabled_channels": [0],
        "rx_annotated": False,
        "rx_output_type": "SI",
        "rx_channel_names": ["voltage0", "voltage1"],
        "tx_channel_names": ["voltage0", "voltage1"],
        "dds_frequencies": [0, 0, 0, 0],
        "dds_scales": [0.0, 0.0, 0.0, 0.0],
        "dds_phases": [0, 0, 0, 0],
        "dds_enabled": [False, False, False, False],
        "rates": (1, 1),
    }


@dataclass
class _VirtualDeviceState:
    device_type: str
    virtual_id: int = field(default_factory=lambda: next(_VIRTUAL_IDS))
    values: dict[str, Any] = field(default_factory=dict)
    last_tx: np.ndarray | None = None

    def __post_init__(self) -> None:
        if self.device_type in {"pluto", "adalm_pluto", "adalm_pluto_mimo"}:
            self.values.update(_pluto_defaults())

    @property
    def uri(self) -> str:
        return f"virtual:{self.device_type}:{self.virtual_id}"

    def get(self, name: str) -> Any:
        if name in {"__repr__", "repr"}:
            return f"<VirtualPluto uri={self.uri}>"
        if name == "rx":
            size = max(0, int(self.values.get("rx_buffer_size", 1024)))
            return np.zeros(size, dtype=np.complex64)
        return self.values.get(name)

    def set(self, name: str, value: Any) -> None:
        if isinstance(value, np.ndarray):
            value = value.copy()
        elif isinstance(value, list):
            value = list(value)
        self.values[name] = value

    def call(self, name: str, arg: Any = None, *, has_arg: bool = False) -> Any:
        if name == "ip":
            return self.uri
        if name == "rx":
            return self.get("rx")
        if name == "tx":
            self.last_tx = np.asarray(arg).copy() if has_arg else None
            return None
        if name == "tx_destroy_buffer":
            self.last_tx = None
            return None
        if name == "rx_destroy_buffer":
            return None
        if name == "disable_dds":
            self.values["dds_enabled"] = [False, False, False, False]
            return None
        if name in {"dds_single_tone", "dds_dual_tone"}:
            return None
        return None


class VirtualToken(str):
    """String-compatible token carrying isolated local virtual-device state."""

    def __new__(
        cls,
        label: str | None,
        *,
        device_type: str,
        state: _VirtualDeviceState | None = None,
    ):
        state = state or _VirtualDeviceState(device_type=device_type)
        text = str(label) if label not in (None, "") else state.uri
        obj = super().__new__(cls, text)
        obj.state = state
        return obj


def make_virtual_token(label: str | None = None, *, device_type: str) -> VirtualToken:
    return VirtualToken(label, device_type=device_type)


def retag_virtual_token(token: VirtualToken, label: str | None) -> VirtualToken:
    return VirtualToken(label, device_type=token.state.device_type, state=token.state)


def is_virtual_token(token: object) -> bool:
    return isinstance(token, VirtualToken)


def virtual_get(token: VirtualToken, name: str) -> Any:
    return token.state.get(name)


def virtual_set(token: VirtualToken, name: str, value: Any) -> None:
    token.state.set(name, value)


def virtual_call(
    token: VirtualToken,
    name: str,
    arg: Any = None,
    *,
    has_arg: bool = False,
) -> Any:
    return token.state.call(name, arg, has_arg=has_arg)
