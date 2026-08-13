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
# device_type: hackrf  driver_version: 0.1.1  schema_hash: sha256:1d6e935e2e33959e753b89d943a1a5696d814ec28aac52a7c39142b3fce89693

_PREFIX = "Hackrf"
_SCHEMA_HASH = "sha256:1d6e935e2e33959e753b89d943a1a5696d814ec28aac52a7c39142b3fce89693"
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

class HackRF:

    def __init__(self, token: str):
        self.token = token
        from ..dynamic_device import install_driver_if_stale
        install_driver_if_stale(token=token, current_hash=_SCHEMA_HASH)

    @property
    def amplifier_on(self):
        'Whether the 14 dB RF front-end amplifier is enabled.'
        return _try_get("amplifier_on", self.token)

    @amplifier_on.setter
    def amplifier_on(self, value):
        _try_set("amplifier_on", value, self.token)

    @property
    def bias_tee_on(self):
        'Whether 3.3 V antenna bias (50 mA maximum) is enabled.'
        return _try_get("bias_tee_on", self.token)

    @bias_tee_on.setter
    def bias_tee_on(self, value):
        _try_set("bias_tee_on", value, self.token)

    @property
    def center_freq(self):
        'Center frequency in Hz.'
        return _try_get("center_freq", self.token)

    @center_freq.setter
    def center_freq(self, value):
        _try_set("center_freq", value, self.token)

    @property
    def filter_bandwidth(self):
        'Baseband filter bandwidth in Hz.'
        return _try_get("filter_bandwidth", self.token)

    @filter_bandwidth.setter
    def filter_bandwidth(self, value):
        _try_set("filter_bandwidth", value, self.token)

    @property
    def lna_gain(self):
        'Receive IF LNA gain in dB (0 to 40, in 8 dB steps).'
        return _try_get("lna_gain", self.token)

    @lna_gain.setter
    def lna_gain(self, value):
        _try_set("lna_gain", value, self.token)

    @property
    def sample_count_limit(self):
        'Asynchronous receive limit in raw IQ bytes; zero means unlimited.'
        return _try_get("sample_count_limit", self.token)

    @sample_count_limit.setter
    def sample_count_limit(self, value):
        _try_set("sample_count_limit", value, self.token)

    @property
    def sample_rate(self):
        'Complex sample rate in samples per second.'
        return _try_get("sample_rate", self.token)

    @sample_rate.setter
    def sample_rate(self, value):
        _try_set("sample_rate", value, self.token)

    @property
    def txvga_gain(self):
        'Transmit VGA gain in dB (0 to 47).'
        return _try_get("txvga_gain", self.token)

    @txvga_gain.setter
    def txvga_gain(self, value):
        _try_set("txvga_gain", value, self.token)

    @property
    def vga_gain(self):
        'Receive baseband VGA gain in dB (0 to 62, in 2 dB steps).'
        return _try_get("vga_gain", self.token)

    @vga_gain.setter
    def vga_gain(self, value):
        _try_set("vga_gain", value, self.token)

    def clear_buffer(self):
        'Clear any captured or queued IQ bytes while the radio is idle.'
        return _try_call("clear_buffer", self.token)

    def enumerate(self):
        'Return attached serials as a JSON array for broad client compatibility.'
        return _try_call("enumerate", self.token)

    def get_serial_no(self):
        'Read the serial number from the open device.'
        return _try_call("get_serial_no", self.token)

    def load_tx_iq(self, samples):
        'Convert normalized IQ samples and load the native transmit buffer.'
        return _try_call("load_tx_iq", self.token, samples)

    def read_samples(self, num_samples=_NO_ARG):
        'Synchronously read a bounded complex64 IQ array.'
        return _try_calln("read_samples", self.token, {
            "num_samples": num_samples,
        })

    def start_tx(self):
        'Start transmitting the IQ bytes previously loaded with load_tx_iq().'
        return _try_call("start_tx", self.token)

    def stop_tx(self):
        'Stop an active HackRF transmission.'
        return _try_call("stop_tx", self.token)
