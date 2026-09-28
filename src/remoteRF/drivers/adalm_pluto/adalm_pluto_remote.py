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

# Auto-generated from IDL schema — do not edit by hand.
# device_type: adalm_pluto  driver_version: 0.0.2  schema_hash: sha256:fa4603755fdfde9ff8286c95b2f30996df0d6253fdec608976b1aaae9f48354e

_PREFIX = "Adalm_pluto"
_SCHEMA_HASH = "sha256:fa4603755fdfde9ff8286c95b2f30996df0d6253fdec608976b1aaae9f48354e"
_CLIENT_MODULES = {}
_CLIENT_OBJECTS = {}

import os

from importlib import import_module as _import_module

from ...common.utils import map_arg, unmap_arg
from .._virtual import (
    is_virtual_token,
    make_virtual_token,
    virtual_call,
    virtual_get,
    virtual_set,
)

_NO_ARG = object()


def _rpc_client(*, function_name, args):
    from ...core.grpc_client import rpc_client

    return rpc_client(function_name=function_name, args=args)


for _alias, _module_path in _CLIENT_MODULES.items():
    globals()[_alias] = _import_module(_module_path)


def _resolve_client_target(target):
    parts = str(target or "").split(".")
    if not parts or not parts[0]:
        raise ValueError(f"Invalid client constructor target: {target!r}")
    if parts[0] not in globals():
        raise ValueError(
            f"Client helper module {parts[0]!r} is not declared in _CLIENT_MODULES"
        )
    obj = globals()[parts[0]]
    for part in parts[1:]:
        obj = getattr(obj, part)
    return obj


def _client_ctor_arg(spec, self_obj, result):
    if spec == "$self":
        return self_obj
    if spec == "$result":
        return result
    if isinstance(spec, dict) and "const" in spec:
        return spec["const"]
    return spec


def _wrap_client_return(spec, self_obj, result):
    if not spec:
        return result
    if spec.get("kind") != "constructor":
        raise ValueError(f"Unsupported client_return metadata: {spec!r}")
    ctor = _resolve_client_target(spec.get("target"))
    args = [
        _client_ctor_arg(item, self_obj, result)
        for item in spec.get("args", [])
    ]
    return ctor(*args)


def _try_get(prop, token):
    if is_virtual_token(token):
        return virtual_get(token, prop)
    return unmap_arg(_rpc_client(
        function_name=f"{_PREFIX}:{prop}:GET",
        args={'a': map_arg(token)},
    ).results[prop])


def _try_set(prop, value, token):
    if is_virtual_token(token):
        virtual_set(token, prop, value)
        return None
    _rpc_client(
        function_name=f"{_PREFIX}:{prop}:SET",
        args={prop: map_arg(value), 'a': map_arg(token)},
    )


def _try_call(prop, token, arg=_NO_ARG):
    if is_virtual_token(token):
        return virtual_call(token, prop, arg, has_arg=arg is not _NO_ARG)
    if arg is _NO_ARG:
        resp = _rpc_client(
            function_name=f"{_PREFIX}:{prop}:CALL0",
            args={'a': map_arg(token)},
        )
    else:
        resp = _rpc_client(
            function_name=f"{_PREFIX}:{prop}:CALL1",
            args={'a': map_arg(token), 'arg1': map_arg(arg)},
        )
    result = resp.results.get(prop)
    return unmap_arg(result) if result is not None else None


def _try_calln(prop, token, kwargs):
    if is_virtual_token(token):
        filtered = {key: value for key, value in dict(kwargs).items() if value is not _NO_ARG}
        return virtual_call(token, prop, filtered, has_arg=True)
    payload = {'a': map_arg(token)}
    for key, value in dict(kwargs).items():
        if value is _NO_ARG:
            continue
        payload[str(key)] = map_arg(value)
    resp = _rpc_client(
        function_name=f"{_PREFIX}:{prop}:CALLN",
        args=payload,
    )
    result = resp.results.get(prop)
    return unmap_arg(result) if result is not None else None

class Pluto:

    def __init__(self, token: str = None, *, virtual: bool = False):
        self.virtual = bool(virtual)
        if self.virtual:
            self.token = make_virtual_token(token, device_type="adalm_pluto")
            return
        if token is None:
            token = os.getenv("REMOTERF_TOKEN")
        if not token:
            raise ValueError("A reservation token is required; pass token= or set REMOTERF_TOKEN")
        self.token = token
        from ..dynamic_device import install_driver_if_stale
        install_driver_if_stale(token=token, current_hash=_SCHEMA_HASH)
        _try_call("ip", token)

    @property
    def dds_enabled(self):
        return _try_get("dds_enabled", self.token)

    @dds_enabled.setter
    def dds_enabled(self, value):
        _try_set("dds_enabled", value, self.token)

    @property
    def dds_frequencies(self):
        return _try_get("dds_frequencies", self.token)

    @dds_frequencies.setter
    def dds_frequencies(self, value):
        _try_set("dds_frequencies", value, self.token)

    @property
    def dds_phases(self):
        return _try_get("dds_phases", self.token)

    @dds_phases.setter
    def dds_phases(self, value):
        _try_set("dds_phases", value, self.token)

    @property
    def dds_scales(self):
        return _try_get("dds_scales", self.token)

    @dds_scales.setter
    def dds_scales(self, value):
        _try_set("dds_scales", value, self.token)

    @property
    def filter(self):
        return _try_get("filter", self.token)

    @filter.setter
    def filter(self, value):
        _try_set("filter", value, self.token)

    @property
    def gain_control_mode_chan0(self):
        return _try_get("gain_control_mode_chan0", self.token)

    @gain_control_mode_chan0.setter
    def gain_control_mode_chan0(self, value):
        _try_set("gain_control_mode_chan0", value, self.token)

    @property
    def loopback(self):
        return _try_get("loopback", self.token)

    @loopback.setter
    def loopback(self, value):
        _try_set("loopback", value, self.token)

    @property
    def rates(self):
        'Get decimation/interpolation rates from the FPGA filter block.'
        return _try_get("rates", self.token)

    @property
    def repr(self):
        'Return repr() of the underlying device object.'
        return _try_get("repr", self.token)

    @property
    def rx_annotated(self):
        return _try_get("rx_annotated", self.token)

    @rx_annotated.setter
    def rx_annotated(self, value):
        _try_set("rx_annotated", value, self.token)

    @property
    def rx_buffer_size(self):
        return _try_get("rx_buffer_size", self.token)

    @rx_buffer_size.setter
    def rx_buffer_size(self, value):
        _try_set("rx_buffer_size", value, self.token)

    @property
    def rx_channel_names(self):
        return _try_get("rx_channel_names", self.token)

    @property
    def rx_dec8_filter_en(self):
        'rx_dec8_filter_en: Enable decimate-by-8 filter in FPGA'
        return _try_get("rx_dec8_filter_en", self.token)

    @rx_dec8_filter_en.setter
    def rx_dec8_filter_en(self, value):
        _try_set("rx_dec8_filter_en", value, self.token)

    @property
    def rx_enabled_channels(self):
        return _try_get("rx_enabled_channels", self.token)

    @rx_enabled_channels.setter
    def rx_enabled_channels(self, value):
        _try_set("rx_enabled_channels", value, self.token)

    @property
    def rx_hardwaregain_chan0(self):
        'RX hardware gain (dB). pyadi-iio attribute: rx_hardwaregain_chan0'
        return _try_get("rx_hardwaregain_chan0", self.token)

    @rx_hardwaregain_chan0.setter
    def rx_hardwaregain_chan0(self, value):
        _try_set("rx_hardwaregain_chan0", value, self.token)

    @property
    def rx_lo(self):
        return _try_get("rx_lo", self.token)

    @rx_lo.setter
    def rx_lo(self, value):
        _try_set("rx_lo", value, self.token)

    @property
    def rx_output_type(self):
        return _try_get("rx_output_type", self.token)

    @rx_output_type.setter
    def rx_output_type(self, value):
        _try_set("rx_output_type", value, self.token)

    @property
    def rx_rf_bandwidth(self):
        return _try_get("rx_rf_bandwidth", self.token)

    @rx_rf_bandwidth.setter
    def rx_rf_bandwidth(self, value):
        _try_set("rx_rf_bandwidth", value, self.token)

    @property
    def sample_rate(self):
        return _try_get("sample_rate", self.token)

    @sample_rate.setter
    def sample_rate(self, value):
        _try_set("sample_rate", value, self.token)

    @property
    def tx_channel_names(self):
        return _try_get("tx_channel_names", self.token)

    @property
    def tx_cyclic_buffer(self):
        return _try_get("tx_cyclic_buffer", self.token)

    @tx_cyclic_buffer.setter
    def tx_cyclic_buffer(self, value):
        _try_set("tx_cyclic_buffer", value, self.token)

    @property
    def tx_enabled_channels(self):
        return _try_get("tx_enabled_channels", self.token)

    @tx_enabled_channels.setter
    def tx_enabled_channels(self, value):
        _try_set("tx_enabled_channels", value, self.token)

    @property
    def tx_hardwaregain_chan0(self):
        return _try_get("tx_hardwaregain_chan0", self.token)

    @tx_hardwaregain_chan0.setter
    def tx_hardwaregain_chan0(self, value):
        _try_set("tx_hardwaregain_chan0", value, self.token)

    @property
    def tx_int8_filter_en(self):
        'tx_int8_filter_en: Enable interpolate-by-8 filter in FPGA'
        return _try_get("tx_int8_filter_en", self.token)

    @tx_int8_filter_en.setter
    def tx_int8_filter_en(self, value):
        _try_set("tx_int8_filter_en", value, self.token)

    @property
    def tx_lo(self):
        return _try_get("tx_lo", self.token)

    @tx_lo.setter
    def tx_lo(self, value):
        _try_set("tx_lo", value, self.token)

    @property
    def tx_rf_bandwidth(self):
        return _try_get("tx_rf_bandwidth", self.token)

    @tx_rf_bandwidth.setter
    def tx_rf_bandwidth(self, value):
        _try_set("tx_rf_bandwidth", value, self.token)

    def dds_dual_tone(self, frequency1, scale1, frequency2, scale2, channel=_NO_ARG):
        'Generate two complex DDS tones on the selected TX channel.'
        return _try_calln("dds_dual_tone", self.token, {
            "frequency1": frequency1,
            "scale1": scale1,
            "frequency2": frequency2,
            "scale2": scale2,
            "channel": channel,
        })

    def dds_single_tone(self, frequency, scale, channel=_NO_ARG):
        'Generate one complex DDS tone on the selected TX channel.'
        return _try_calln("dds_single_tone", self.token, {
            "frequency": frequency,
            "scale": scale,
            "channel": channel,
        })

    def disable_dds(self):
        'Disable the internal DDS tone generator.'
        return _try_call("disable_dds", self.token)

    def ip(self):
        'Return a connection identifier for this device (ping / verify).'
        return _try_call("ip", self.token)

    def rx(self):
        return _try_call("rx", self.token)

    def rx_destroy_buffer(self):
        return _try_call("rx_destroy_buffer", self.token)

    def tx(self, value):
        return _try_call("tx", self.token, value)

    def tx_destroy_buffer(self):
        return _try_call("tx_destroy_buffer", self.token)
