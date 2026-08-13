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
# device_type: rtl_sdr  driver_version: 0.1.1  schema_hash: sha256:1c84effc2c21be734398a87d24bb9da52ab5b7ea0def955c503da484b8d818c0

_PREFIX = "Rtl_sdr"
_SCHEMA_HASH = "sha256:1c84effc2c21be734398a87d24bb9da52ab5b7ea0def955c503da484b8d818c0"
_CLIENT_MODULES = {}
_CLIENT_OBJECTS = {}

from importlib import import_module as _import_module

from ...core.grpc_client import rpc_client
from ...common.utils import map_arg, unmap_arg

_NO_ARG = object()


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
    return unmap_arg(rpc_client(
        function_name=f"{_PREFIX}:{prop}:GET",
        args={'a': map_arg(token)},
    ).results[prop])


def _try_set(prop, value, token):
    rpc_client(
        function_name=f"{_PREFIX}:{prop}:SET",
        args={prop: map_arg(value), 'a': map_arg(token)},
    )


def _try_call(prop, token, arg=_NO_ARG):
    if arg is _NO_ARG:
        resp = rpc_client(
            function_name=f"{_PREFIX}:{prop}:CALL0",
            args={'a': map_arg(token)},
        )
    else:
        resp = rpc_client(
            function_name=f"{_PREFIX}:{prop}:CALL1",
            args={'a': map_arg(token), 'arg1': map_arg(arg)},
        )
    result = resp.results.get(prop)
    return unmap_arg(result) if result is not None else None


def _try_calln(prop, token, kwargs):
    payload = {'a': map_arg(token)}
    for key, value in dict(kwargs).items():
        if value is _NO_ARG:
            continue
        payload[str(key)] = map_arg(value)
    resp = rpc_client(
        function_name=f"{_PREFIX}:{prop}:CALLN",
        args=payload,
    )
    result = resp.results.get(prop)
    return unmap_arg(result) if result is not None else None

class RtlSdr:

    def __init__(self, token: str):
        self.token = token
        from ..dynamic_device import install_driver_if_stale
        install_driver_if_stale(token=token, current_hash=_SCHEMA_HASH)

    @property
    def agc_mode(self):
        return _try_get("agc_mode", self.token)

    @agc_mode.setter
    def agc_mode(self, value):
        _try_set("agc_mode", value, self.token)

    @property
    def bandwidth(self):
        'Tuner bandwidth in Hz; zero requests automatic selection.'
        return _try_get("bandwidth", self.token)

    @bandwidth.setter
    def bandwidth(self, value):
        _try_set("bandwidth", value, self.token)

    @property
    def bias_tee(self):
        return _try_get("bias_tee", self.token)

    @bias_tee.setter
    def bias_tee(self, value):
        _try_set("bias_tee", value, self.token)

    @property
    def center_freq(self):
        'Tuner center frequency in Hz.'
        return _try_get("center_freq", self.token)

    @center_freq.setter
    def center_freq(self, value):
        _try_set("center_freq", value, self.token)

    @property
    def device_index(self):
        return _try_get("device_index", self.token)

    @property
    def direct_sampling(self):
        return _try_get("direct_sampling", self.token)

    @direct_sampling.setter
    def direct_sampling(self, value):
        _try_set("direct_sampling", value, self.token)

    @property
    def dithering(self):
        return _try_get("dithering", self.token)

    @dithering.setter
    def dithering(self, value):
        _try_set("dithering", value, self.token)

    @property
    def fc(self):
        'Alias for center_freq, matching PyRtlSdr.'
        return _try_get("fc", self.token)

    @fc.setter
    def fc(self, value):
        _try_set("fc", value, self.token)

    @property
    def freq_correction(self):
        'Frequency correction in parts per million.'
        return _try_get("freq_correction", self.token)

    @freq_correction.setter
    def freq_correction(self, value):
        _try_set("freq_correction", value, self.token)

    @property
    def gain(self):
        'Tuner gain in dB, or auto when automatic gain is enabled.'
        return _try_get("gain", self.token)

    @gain.setter
    def gain(self, value):
        _try_set("gain", value, self.token)

    @property
    def offset_tuning(self):
        return _try_get("offset_tuning", self.token)

    @offset_tuning.setter
    def offset_tuning(self, value):
        _try_set("offset_tuning", value, self.token)

    @property
    def rs(self):
        'Alias for sample_rate, matching PyRtlSdr.'
        return _try_get("rs", self.token)

    @rs.setter
    def rs(self, value):
        _try_set("rs", value, self.token)

    @property
    def sample_rate(self):
        'Complex sample rate in samples per second.'
        return _try_get("sample_rate", self.token)

    @sample_rate.setter
    def sample_rate(self, value):
        _try_set("sample_rate", value, self.token)

    @property
    def serial_number(self):
        return _try_get("serial_number", self.token)

    @property
    def tuner_type(self):
        return _try_get("tuner_type", self.token)

    @property
    def usb_strings(self):
        return _try_get("usb_strings", self.token)

    @property
    def valid_gains_db(self):
        return _try_get("valid_gains_db", self.token)

    def read_bytes(self, num_bytes=_NO_ARG):
        'Read packed unsigned 8-bit interleaved IQ bytes.'
        return _try_calln("read_bytes", self.token, {
            "num_bytes": num_bytes,
        })

    def read_samples(self, num_samples=_NO_ARG):
        'Read normalized complex64 IQ samples.'
        return _try_calln("read_samples", self.token, {
            "num_samples": num_samples,
        })

    def reset_buffer(self):
        'Discard pending USB samples before a new capture.'
        return _try_call("reset_buffer", self.token)
