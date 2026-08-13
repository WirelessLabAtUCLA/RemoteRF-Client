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

"""Native-like UHD value objects used by declarative Dynamic v2 schemas."""
from __future__ import annotations

import math
from collections.abc import MutableMapping
from enum import Enum


def payload(value):
    if hasattr(value, "as_payload"):
        return payload(value.as_payload())
    if isinstance(value, dict):
        return {str(key): payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [payload(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


class _ValueObject:
    _uhd_type = ""

    def as_payload(self):
        out = {"__uhd_type__": self._uhd_type or type(self).__name__}
        for name in getattr(self, "__slots__", ()):
            if not name.startswith("_"):
                out[name] = payload(getattr(self, name))
        return out

    def update(self, snapshot):
        for key, value in dict(snapshot or {}).items():
            if key.startswith("__"):
                continue
            if hasattr(self, key):
                setattr(self, key, decode_snapshot(value))
        return self


class TimeSpec(_ValueObject):
    __slots__ = ("_secs",)
    _uhd_type = "TimeSpec"

    def __init__(self, full_secs=0.0, frac_secs=None):
        self._secs = (
            float(full_secs)
            if frac_secs is None
            else float(int(full_secs)) + float(frac_secs)
        )

    @classmethod
    def from_ticks(cls, ticks, tick_rate):
        return cls(float(ticks) / float(tick_rate))

    def get_real_secs(self):
        return self._secs

    def get_full_secs(self):
        return math.floor(self._secs)

    def get_frac_secs(self):
        return self._secs - self.get_full_secs()

    def to_ticks(self, tick_rate):
        return int(round(self._secs * float(tick_rate)))

    def get_tick_count(self, tick_rate):
        return self.to_ticks(tick_rate)

    def as_payload(self):
        return {"__uhd_type__": "TimeSpec", "secs": self._secs}

    def __float__(self):
        return self._secs

    def __add__(self, other):
        return TimeSpec(self._secs + float(other))

    def __radd__(self, other):
        return TimeSpec(float(other) + self._secs)

    def __sub__(self, other):
        if isinstance(other, TimeSpec):
            return TimeSpec(self._secs - other._secs)
        return TimeSpec(self._secs - float(other))

    def __rsub__(self, other):
        return TimeSpec(float(other) - self._secs)

    def __eq__(self, other):
        try:
            return self._secs == float(other)
        except (TypeError, ValueError):
            return False

    def __lt__(self, other):
        return self._secs < float(other)

    def __le__(self, other):
        return self._secs <= float(other)

    def __repr__(self):
        return f"TimeSpec({self.get_full_secs()}, {self.get_frac_secs()!r})"


class DeviceAddr(_ValueObject, MutableMapping):
    __slots__ = ("_items",)
    _uhd_type = "DeviceAddr"

    def __init__(self, value=None):
        self._items = {}
        if isinstance(value, str):
            for item in value.split(","):
                if "=" in item:
                    key, raw = item.split("=", 1)
                    self._items[key.strip()] = raw.strip()
        elif value:
            self._items.update({str(key): str(item) for key, item in dict(value).items()})

    def __getitem__(self, key):
        return self._items[key]

    def __setitem__(self, key, value):
        self._items[str(key)] = str(value)

    def __delitem__(self, key):
        del self._items[key]

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def to_string(self):
        return ",".join(f"{key}={value}" for key, value in self._items.items())

    def as_payload(self):
        return {"__uhd_type__": "DeviceAddr", "items": dict(self._items)}

    def __str__(self):
        return self.to_string()

    def __repr__(self):
        return f"DeviceAddr({self.to_string()!r})"


class Range(_ValueObject):
    __slots__ = ("_start", "_stop", "_step")
    _uhd_type = "Range"

    def __init__(self, start=0.0, stop=None, step=0.0):
        self._start = float(start)
        self._stop = float(start if stop is None else stop)
        self._step = float(step)

    def start(self):
        return self._start

    def stop(self):
        return self._stop

    def step(self):
        return self._step

    def clip(self, value, clip_step=False):
        clipped = min(max(float(value), self._start), self._stop)
        if clip_step and self._step:
            clipped = self._start + round((clipped - self._start) / self._step) * self._step
        return min(max(clipped, self._start), self._stop)

    def as_payload(self):
        return {
            "__uhd_type__": "Range",
            "start": self._start,
            "stop": self._stop,
            "step": self._step,
        }

    def __repr__(self):
        return f"Range({self._start!r}, {self._stop!r}, {self._step!r})"


class MetaRange(list):
    _uhd_type = "MetaRange"

    def __init__(self, ranges=()):
        super().__init__(
            item if isinstance(item, Range) else decode_snapshot(item)
            for item in ranges
        )

    def start(self):
        return self[0].start() if self else 0.0

    def stop(self):
        return self[-1].stop() if self else 0.0

    def step(self):
        return min((item.step() for item in self if item.step()), default=0.0)

    def clip(self, value, clip_step=False):
        if not self:
            return float(value)
        return min(
            (item.clip(value, clip_step) for item in self),
            key=lambda item: abs(item - float(value)),
        )

    def as_payload(self):
        return {"__uhd_type__": "MetaRange", "ranges": payload(list(self))}


class TuneRequestPolicy(str, Enum):
    NONE = "NONE"
    AUTO = "AUTO"
    MANUAL = "MANUAL"


class TuneRequest(_ValueObject):
    __slots__ = (
        "target_freq", "lo_offset", "rf_freq", "dsp_freq",
        "rf_freq_policy", "dsp_freq_policy", "args",
    )
    _uhd_type = "TuneRequest"

    def __init__(self, target_freq=0.0, lo_offset=None):
        self.target_freq = float(target_freq)
        self.lo_offset = None if lo_offset is None else float(lo_offset)
        self.rf_freq = 0.0
        self.dsp_freq = 0.0
        self.rf_freq_policy = TuneRequestPolicy.AUTO
        self.dsp_freq_policy = TuneRequestPolicy.AUTO
        self.args = DeviceAddr()

    def as_payload(self):
        return {
            "__uhd_type__": "TuneRequest",
            "target_freq": self.target_freq,
            "lo_offset": self.lo_offset,
            "rf_freq": self.rf_freq,
            "dsp_freq": self.dsp_freq,
            "rf_freq_policy": payload(self.rf_freq_policy),
            "dsp_freq_policy": payload(self.dsp_freq_policy),
            "args": payload(self.args),
        }


class TuneResult(_ValueObject):
    __slots__ = (
        "clipped_rf_freq", "target_rf_freq", "actual_rf_freq",
        "target_dsp_freq", "actual_dsp_freq",
    )
    _uhd_type = "TuneResult"

    def __init__(self, **values):
        for field in self.__slots__:
            setattr(self, field, float(values.get(field, 0.0) or 0.0))

    def __str__(self):
        return (
            f"Tune Result:\n"
            f"  Target RF  Freq: {self.target_rf_freq}\n"
            f"  Actual RF  Freq: {self.actual_rf_freq}\n"
            f"  Target DSP Freq: {self.target_dsp_freq}\n"
            f"  Actual DSP Freq: {self.actual_dsp_freq}"
        )


class SubdevSpecPair(_ValueObject):
    __slots__ = ("db_name", "sd_name")
    _uhd_type = "SubdevSpecPair"

    def __init__(self, db_name="", sd_name=""):
        self.db_name = str(db_name)
        self.sd_name = str(sd_name)

    def __str__(self):
        return f"{self.db_name}:{self.sd_name}"


class SubdevSpec(list):
    _uhd_type = "SubdevSpec"

    def __init__(self, markup=""):
        if isinstance(markup, str):
            pairs = []
            for item in markup.split():
                db_name, _, sd_name = item.partition(":")
                pairs.append(SubdevSpecPair(db_name, sd_name))
            super().__init__(pairs)
        else:
            super().__init__(markup)

    def to_string(self):
        return " ".join(str(item) for item in self)

    def as_payload(self):
        return {"__uhd_type__": "SubdevSpec", "spec": self.to_string()}

    def __str__(self):
        return self.to_string()


class StreamArgs(_ValueObject):
    __slots__ = ("cpu_format", "otw_format", "args", "channels")
    _uhd_type = "StreamArgs"

    def __init__(self, cpu_format, otw_format=""):
        self.cpu_format = str(cpu_format)
        self.otw_format = str(otw_format)
        self.args = DeviceAddr()
        self.channels = []


class StreamMode(str, Enum):
    num_done = "num_done"
    num_more = "num_more"
    stop_cont = "stop_cont"
    start_cont = "start_cont"


class StreamCMD(_ValueObject):
    __slots__ = ("mode", "num_samps", "stream_now", "time_spec")
    _uhd_type = "StreamCMD"

    def __init__(self, mode):
        self.mode = mode if isinstance(mode, StreamMode) else StreamMode(str(mode))
        self.num_samps = 0
        self.stream_now = True
        self.time_spec = TimeSpec(0.0)


class RXMetadataErrorCode(str, Enum):
    none = "none"
    timeout = "timeout"
    overflow = "overflow"
    late_command = "late_command"
    broken_chain = "broken_chain"
    alignment = "alignment"
    bad_packet = "bad_packet"


class RXMetadata(_ValueObject):
    __slots__ = (
        "error_code", "error_code_repr", "has_time_spec", "time_spec",
        "more_fragments", "fragment_offset", "out_of_sequence",
    )
    _uhd_type = "RXMetadata"

    def __init__(self):
        self.reset()

    def reset(self):
        self.error_code = RXMetadataErrorCode.none
        self.error_code_repr = "none"
        self.has_time_spec = False
        self.time_spec = TimeSpec(0.0)
        self.more_fragments = False
        self.fragment_offset = 0
        self.out_of_sequence = False

    def update(self, snapshot):
        snapshot = dict(snapshot or {})
        raw = snapshot.get("error_code", "none")
        try:
            self.error_code = RXMetadataErrorCode(raw)
        except ValueError:
            self.error_code = raw
        self.error_code_repr = snapshot.get("error_code_repr", str(raw))
        self.has_time_spec = bool(snapshot.get("has_time_spec", False))
        if snapshot.get("time_spec") is not None:
            self.time_spec = decode_snapshot(snapshot["time_spec"])
        self.more_fragments = bool(snapshot.get("more_fragments", False))
        self.fragment_offset = int(snapshot.get("fragment_offset", 0) or 0)
        self.out_of_sequence = bool(snapshot.get("out_of_sequence", False))
        return self

    def strerror(self):
        return self.error_code_repr


class TXMetadata(_ValueObject):
    __slots__ = ("has_time_spec", "time_spec", "start_of_burst", "end_of_burst")
    _uhd_type = "TXMetadata"

    def __init__(self):
        self.has_time_spec = False
        self.time_spec = TimeSpec(0.0)
        self.start_of_burst = False
        self.end_of_burst = False


class TXMetadataEventCode(str, Enum):
    burst_ack = "burst_ack"
    underflow = "underflow"
    seq_error = "seq_error"
    time_error = "time_error"
    underflow_in_packet = "underflow_in_packet"
    seq_error_in_burst = "seq_error_in_burst"


class TXAsyncMetadata(_ValueObject):
    __slots__ = (
        "event_code", "event_code_repr", "has_time_spec", "time_spec",
        "channel", "user_payload", "remoterf_queue_overflow",
    )
    _uhd_type = "TXAsyncMetadata"

    def __init__(self):
        self.event_code = None
        self.event_code_repr = ""
        self.has_time_spec = False
        self.time_spec = TimeSpec(0.0)
        self.channel = 0
        self.user_payload = None
        self.remoterf_queue_overflow = False

    def update(self, snapshot):
        snapshot = dict(snapshot or {})
        raw = snapshot.get("event_code")
        try:
            self.event_code = TXMetadataEventCode(raw) if raw else None
        except ValueError:
            self.event_code = raw
        self.event_code_repr = snapshot.get("event_code_repr", str(raw or ""))
        self.has_time_spec = bool(snapshot.get("has_time_spec", False))
        if snapshot.get("time_spec") is not None:
            self.time_spec = decode_snapshot(snapshot["time_spec"])
        self.channel = int(snapshot.get("channel", 0) or 0)
        self.user_payload = snapshot.get("user_payload")
        self.remoterf_queue_overflow = bool(
            snapshot.get("remoterf_queue_overflow", False)
        )
        return self


AsyncMetadata = TXAsyncMetadata


class SensorValue(_ValueObject):
    __slots__ = ("name", "value", "unit", "type", "pretty")
    _uhd_type = "SensorValue"

    def __init__(self, **values):
        for field in self.__slots__:
            setattr(self, field, values.get(field))

    def to_bool(self):
        if isinstance(self.value, bool):
            return self.value
        return str(self.value).strip().lower() in {"1", "true", "yes", "locked"}

    def to_int(self):
        return int(self.value)

    def to_real(self):
        return float(self.value)

    def to_pp_string(self):
        return self.pretty or f"{self.name}: {self.value} {self.unit or ''}".strip()

    def __str__(self):
        return self.to_pp_string()


class FilterType(str, Enum):
    analog_low_pass = "analog_low_pass"
    analog_band_pass = "analog_band_pass"
    digital_i16 = "digital_i16"
    digital_fir_i16 = "digital_fir_i16"


class FilterInfoBase(_ValueObject):
    _uhd_type = "FilterInfoBase"
    _native_class = "filter_info_base"

    def __init__(
        self,
        filter_type=None,
        bypassed=False,
        position=0,
        **values,
    ):
        self._values = {
            "native_class": values.pop("native_class", self._native_class),
            "filter_type": (
                filter_type.value
                if isinstance(filter_type, FilterType)
                else filter_type
            ),
            "bypassed": bool(bypassed),
            "position": int(position),
            **values,
        }

    def as_payload(self):
        return {"__uhd_type__": "FilterInfoBase", **payload(self._values)}

    def is_bypassed(self):
        return bool(self._values.get("bypassed", False))

    def get_type(self):
        value = self._values.get("filter_type")
        try:
            return FilterType(value)
        except ValueError:
            return value

    def __getattr__(self, name):
        try:
            return self._values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class AnalogFilterBase(FilterInfoBase):
    _native_class = "analog_filter_base"

    def __init__(
        self,
        filter_type,
        bypassed,
        position,
        analog_type,
        **values,
    ):
        super().__init__(
            filter_type,
            bypassed,
            position,
            analog_type=str(analog_type),
            **values,
        )

    def get_analog_type(self):
        return self._values["analog_type"]


class AnalogFilterLP(AnalogFilterBase):
    _native_class = "analog_filter_lp"

    def __init__(
        self,
        filter_type,
        bypassed,
        position,
        analog_type,
        cutoff,
        rolloff,
        **values,
    ):
        super().__init__(
            filter_type,
            bypassed,
            position,
            analog_type,
            cutoff=float(cutoff),
            rolloff=float(rolloff),
            **values,
        )

    def get_cutoff(self):
        return self._values["cutoff"]

    def get_rolloff(self):
        return self._values["rolloff"]

    def set_cutoff(self, cutoff):
        self._values["cutoff"] = float(cutoff)


class DigitalFilterBaseI16(FilterInfoBase):
    _native_class = "digital_filter_base_i16"

    def __init__(
        self,
        filter_type,
        bypassed,
        position,
        input_rate,
        interpolation,
        decimation,
        tap_full_scale,
        max_num_taps,
        taps,
        **values,
    ):
        super().__init__(
            filter_type,
            bypassed,
            position,
            input_rate=float(input_rate),
            interpolation=int(interpolation),
            decimation=int(decimation),
            tap_full_scale=int(tap_full_scale),
            max_num_taps=int(max_num_taps),
            taps=[int(item) for item in taps],
            **values,
        )

    def get_output_rate(self):
        return (
            self._values["input_rate"]
            * self._values["interpolation"]
            / self._values["decimation"]
        )

    def get_input_rate(self):
        return self._values["input_rate"]

    def get_interpolation(self):
        return self._values["interpolation"]

    def get_decimation(self):
        return self._values["decimation"]

    def get_tap_full_scale(self):
        return self._values["tap_full_scale"]

    def get_taps(self):
        return list(self._values["taps"])


class DigitalFilterFIRI16(DigitalFilterBaseI16):
    _native_class = "digital_filter_fir_i16"

    def set_taps(self, taps):
        values = [int(item) for item in taps]
        if len(values) > self._values["max_num_taps"]:
            raise ValueError("filter tap count exceeds max_num_taps")
        self._values["taps"] = values


FILTER_CLASSES = {
    "filter_info_base": FilterInfoBase,
    "analog_filter_base": AnalogFilterBase,
    "analog_filter_lp": AnalogFilterLP,
    "digital_filter_base_i16": DigitalFilterBaseI16,
    "digital_filter_fir_i16": DigitalFilterFIRI16,
}


CODEC_CLASSES = {
    "uhd.time_spec.v1": TimeSpec,
    "uhd.device_addr.v1": DeviceAddr,
    "uhd.range.v1": Range,
    "uhd.meta_range.v1": MetaRange,
    "uhd.tune_request.v1": TuneRequest,
    "uhd.tune_result.v1": TuneResult,
    "uhd.subdev_spec.v1": SubdevSpec,
    "uhd.subdev_spec_pair.v1": SubdevSpecPair,
    "uhd.stream_args.v1": StreamArgs,
    "uhd.stream_cmd.v1": StreamCMD,
    "uhd.rx_metadata.v1": RXMetadata,
    "uhd.tx_metadata.v1": TXMetadata,
    "uhd.tx_async_metadata.v1": TXAsyncMetadata,
    "uhd.sensor_value.v1": SensorValue,
    "uhd.filter.v1": FilterInfoBase,
}

TYPE_CLASSES = {
    "TimeSpec": TimeSpec,
    "DeviceAddr": DeviceAddr,
    "Range": Range,
    "MetaRange": MetaRange,
    "TuneRequest": TuneRequest,
    "TuneResult": TuneResult,
    "SubdevSpec": SubdevSpec,
    "SubdevSpecPair": SubdevSpecPair,
    "StreamArgs": StreamArgs,
    "StreamCMD": StreamCMD,
    "RXMetadata": RXMetadata,
    "TXMetadata": TXMetadata,
    "TXAsyncMetadata": TXAsyncMetadata,
    "AsyncMetadata": TXAsyncMetadata,
    "SensorValue": SensorValue,
    "FilterInfoBase": FilterInfoBase,
}


def decode_snapshot(value):
    if isinstance(value, list):
        return [decode_snapshot(item) for item in value]
    if not isinstance(value, dict):
        return value
    tag = value.get("__uhd_type__")
    if not tag:
        return {key: decode_snapshot(item) for key, item in value.items()}
    if tag == "TimeSpec":
        return TimeSpec(value.get("secs", 0.0))
    if tag == "DeviceAddr":
        return DeviceAddr(value.get("items", value.get("value", {})))
    if tag == "Range":
        return Range(value.get("start", 0.0), value.get("stop"), value.get("step", 0.0))
    if tag == "MetaRange":
        ranges = value.get("ranges") or ()
        if ranges:
            return MetaRange(ranges)
        # Some PyUHD MetaRange implementations expose only the aggregate
        # start/stop/step methods and are not iterable. Servers include those
        # aggregate fields so the client can still reconstruct a useful range.
        if any(key in value for key in ("start", "stop", "step")):
            return MetaRange(
                [
                    Range(
                        value.get("start", 0.0),
                        value.get("stop"),
                        value.get("step", 0.0),
                    )
                ]
            )
        return MetaRange()
    if tag == "SubdevSpec":
        return SubdevSpec(value.get("spec", ""))
    if tag == "SubdevSpecPair":
        return SubdevSpecPair(value.get("db_name", ""), value.get("sd_name", ""))
    if tag == "TuneRequest":
        obj = TuneRequest(value.get("target_freq", 0.0), value.get("lo_offset"))
        obj.update(value)
        for field in ("rf_freq_policy", "dsp_freq_policy"):
            raw = getattr(obj, field)
            try:
                setattr(obj, field, TuneRequestPolicy(raw))
            except ValueError:
                pass
        return obj
    if tag == "StreamArgs":
        return StreamArgs(
            value.get("cpu_format", ""),
            value.get("otw_format", ""),
        ).update(value)
    if tag == "StreamCMD":
        raw_mode = value.get("mode", StreamMode.start_cont)
        try:
            raw_mode = StreamMode(raw_mode)
        except ValueError:
            pass
        return StreamCMD(raw_mode).update(value)
    cls = TYPE_CLASSES.get(tag)
    if cls is None:
        return {key: decode_snapshot(item) for key, item in value.items()}
    if cls is FilterInfoBase:
        values = {
            key: decode_snapshot(item)
            for key, item in value.items()
            if not key.startswith("__")
        }
        target = FILTER_CLASSES.get(
            values.get("native_class"),
            FilterInfoBase,
        )
        return target(**values)
    if cls in {TuneResult, SensorValue}:
        return cls(**{key: decode_snapshot(item) for key, item in value.items() if not key.startswith("__")})
    obj = cls()
    return obj.update(value)
