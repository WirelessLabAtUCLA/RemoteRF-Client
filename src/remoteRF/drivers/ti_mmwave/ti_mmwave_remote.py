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
# device_type: ti_mmwave  driver_version: 0.1.0  schema_hash: sha256:320a89d95075a472d7899ff2f98b9fdee05c0be96fa5651bf5c4f0e5d276a1c4

_PREFIX = "Ti_mmwave"
_SCHEMA_HASH = "sha256:320a89d95075a472d7899ff2f98b9fdee05c0be96fa5651bf5c4f0e5d276a1c4"
_CLIENT_MODULES = {'ti_mmwave': 'remoteRF.drivers.support.ti_mmwave'}
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

class TiMmWave:

    def __init__(self, token: str):
        self.token = token
        from ..dynamic_device import install_driver_if_stale
        install_driver_if_stale(token=token, current_hash=_SCHEMA_HASH)

    @property
    def device_info(self):
        'USB/UART identity, firmware profile, and current stream state.'
        return _try_get("device_info", self.token)

    @property
    def firmware_profile(self):
        return _try_get("firmware_profile", self.token)

    @property
    def is_running(self):
        return _try_get("is_running", self.token)

    @property
    def stream_stats(self):
        'Frame queue, resynchronization, and reader health counters.'
        return _try_get("stream_stats", self.token)

    def apply_config(self, config_text, start=_NO_ARG, command_timeout=_NO_ARG):
        'Apply newline-delimited TI CLI configuration commands in order.'
        return _try_calln("apply_config", self.token, {
            "config_text": config_text,
            "start": start,
            "command_timeout": command_timeout,
        })

    def flush_frames(self):
        'Discard queued and partially assembled data frames.'
        return _try_call("flush_frames", self.token)

    def query_version(self, timeout=_NO_ARG):
        return _try_calln("query_version", self.token, {
            "timeout": timeout,
        })

    def read_frame(self, timeout=_NO_ARG):
        'Return one complete raw TI UART packet as bytes.'
        return _try_calln("read_frame", self.token, {
            "timeout": timeout,
        })

    def send_command(self, command, timeout=_NO_ARG):
        'Send one runtime CLI command and return the textual response.'
        return _try_calln("send_command", self.token, {
            "command": command,
            "timeout": timeout,
        })

    def start(self, timeout=_NO_ARG):
        return _try_calln("start", self.token, {
            "timeout": timeout,
        })

    def stop(self, timeout=_NO_ARG):
        return _try_calln("stop", self.token, {
            "timeout": timeout,
        })
