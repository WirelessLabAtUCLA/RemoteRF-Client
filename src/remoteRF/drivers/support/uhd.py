import math


def _slots_for(obj):
    slots = []
    for cls in type(obj).__mro__:
        value = getattr(cls, "__slots__", ())
        if isinstance(value, str):
            slots.append(value)
        else:
            slots.extend(value)
    return tuple(name for name in slots if name != "__dict__")


class _StrictObject:
    __slots__ = ()

    def __setattr__(self, name, value):
        allowed = _slots_for(self)
        if name not in allowed:
            joined = ", ".join(allowed) or "none"
            raise AttributeError(
                f"{type(self).__name__} does not support attribute {name!r}. "
                f"Supported attributes: {joined}"
            )
        object.__setattr__(self, name, value)


class _PayloadDict(dict):
    __slots__ = ()

    def as_payload(self):
        return dict(self)


def payload(value):
    if hasattr(value, "as_payload"):
        return payload(value.as_payload())
    if isinstance(value, dict):
        return {key: payload(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [payload(item) for item in value]
    if isinstance(value, list):
        return [payload(item) for item in value]
    return value


def _real_secs(value):
    if hasattr(value, "get_real_secs"):
        return value.get_real_secs()
    if isinstance(value, dict):
        value = value.get("secs", value.get("time_spec", 0.0))
        if isinstance(value, dict):
            return _real_secs(value)
        return float(value)
    return float(value)


def get_rx_stream(usrp_obj, stream_args):
    return streamer(usrp_obj, usrp_obj.get_rx_stream(payload(stream_args)))


def get_tx_stream(usrp_obj, stream_args):
    return streamer(usrp_obj, usrp_obj.get_tx_stream(payload(stream_args)))


def close_all_streams(usrp_obj):
    return usrp_obj.close_all_streams()


def streamer(usrp_obj, value):
    if isinstance(value, _Streamer):
        return value
    if not isinstance(value, dict):
        raise TypeError(f"Expected streamer payload from server, got {type(value).__name__}")
    kind = value.get("__uhd_type__")
    if kind == "RXStreamer":
        return RXStreamer(usrp_obj, value)
    if kind == "TXStreamer":
        return TXStreamer(usrp_obj, value)
    raise ValueError(f"Unknown streamer payload type: {kind!r}")


class TimeSpec(_PayloadDict):
    __slots__ = ("secs",)

    def __init__(self, secs=0.0):
        secs = _real_secs(secs) if isinstance(secs, dict) else float(secs)
        dict.__init__(self, __uhd_type__="TimeSpec", secs=secs)
        self.secs = secs

    def __setattr__(self, name, value):
        if name != "secs":
            raise AttributeError(
                f"TimeSpec does not support attribute {name!r}. Supported attributes: secs"
            )
        object.__setattr__(self, name, float(value))
        self["secs"] = float(value)

    @classmethod
    def from_ticks(cls, ticks, rate):
        return cls(float(ticks) / float(rate))

    def get_real_secs(self):
        return self.secs

    def get_full_secs(self):
        return math.floor(self.secs)

    def get_frac_secs(self):
        return self.secs - self.get_full_secs()

    def to_ticks(self, rate):
        return int(round(self.secs * float(rate)))

    def get_tick_count(self, rate):
        return self.to_ticks(rate)

    def __float__(self):
        return self.secs

    def __add__(self, other):
        return TimeSpec(self.secs + _real_secs(other))

    def __radd__(self, other):
        return TimeSpec(_real_secs(other) + self.secs)

    def __sub__(self, other):
        return TimeSpec(self.secs - _real_secs(other))

    def __rsub__(self, other):
        return TimeSpec(_real_secs(other) - self.secs)

    def as_payload(self):
        self["secs"] = self.secs
        return dict(self)


def tune_request(*args, **kwargs):
    return {
        "__uhd_type__": "TuneRequest",
        "args": payload(args),
        "kwargs": payload(kwargs),
    }


def subdev_spec(spec):
    return {"__uhd_type__": "SubdevSpec", "spec": str(spec)}


class StreamArgs(_PayloadDict):
    __slots__ = ("cpu_format", "otw_format", "args", "channels")

    def __init__(self, cpu_format, otw_format):
        dict.__init__(
            self,
            __uhd_type__="StreamArgs",
            cpu_format=str(cpu_format),
            otw_format=str(otw_format),
            args={},
            channels=[],
        )
        self.cpu_format = str(cpu_format)
        self.otw_format = str(otw_format)
        self.args = {}
        self.channels = []

    def __setattr__(self, name, value):
        if name not in self.__slots__:
            joined = ", ".join(self.__slots__)
            raise AttributeError(
                f"StreamArgs does not support attribute {name!r}. Supported attributes: {joined}"
            )
        object.__setattr__(self, name, value)
        if name == "channels":
            self[name] = list(value)
        elif name == "args":
            self[name] = payload(value)
        else:
            self[name] = str(value)

    def as_payload(self):
        self["cpu_format"] = self.cpu_format
        self["otw_format"] = self.otw_format
        self["args"] = payload(self.args)
        self["channels"] = list(self.channels)
        return dict(self)


class StreamCMD(_PayloadDict):
    __slots__ = ("mode", "stream_now", "time_spec", "num_samps")

    def __init__(self, mode):
        dict.__init__(
            self,
            __uhd_type__="StreamCMD",
            mode=str(mode),
            stream_now=True,
            time_spec=None,
            num_samps=None,
        )
        self.mode = mode
        self.stream_now = True
        self.time_spec = None
        self.num_samps = None

    def __setattr__(self, name, value):
        if name not in self.__slots__:
            joined = ", ".join(self.__slots__)
            raise AttributeError(
                f"StreamCMD does not support attribute {name!r}. Supported attributes: {joined}"
            )
        object.__setattr__(self, name, value)
        if name == "mode":
            self[name] = value.value if hasattr(value, "value") else str(value)
        elif name == "time_spec":
            self[name] = payload(value) if value is not None else None
        else:
            self[name] = value

    def as_payload(self):
        mode = self.mode.value if hasattr(self.mode, "value") else str(self.mode)
        self["mode"] = mode
        self["stream_now"] = bool(self.stream_now)
        self["time_spec"] = payload(self.time_spec) if self.time_spec is not None else None
        self["num_samps"] = self.num_samps
        return dict(self)


class _EnumValue(_StrictObject):
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return self.value

    def __repr__(self):
        return self.value

    def __eq__(self, other):
        return self.value == (other.value if hasattr(other, "value") else other)


class StreamMode:
    num_done = _EnumValue("num_done")
    num_more = _EnumValue("num_more")
    stop_cont = _EnumValue("stop_cont")
    start_cont = _EnumValue("start_cont")


class RXMetadataErrorCode:
    none = _EnumValue("none")
    timeout = _EnumValue("timeout")
    overflow = _EnumValue("overflow")
    late_command = _EnumValue("late_command")
    broken_chain = _EnumValue("broken_chain")
    alignment = _EnumValue("alignment")
    bad_packet = _EnumValue("bad_packet")


class RXMetadata(_StrictObject):
    __slots__ = (
        "error_code",
        "error_code_repr",
        "time_spec",
        "out_of_sequence",
        "fragment_offset",
        "more_fragments",
    )

    def __init__(self):
        self.error_code = RXMetadataErrorCode.none
        self.error_code_repr = "none"
        self.time_spec = TimeSpec(0.0)
        self.out_of_sequence = None
        self.fragment_offset = None
        self.more_fragments = None

    def update(self, value):
        value = value or {}
        error_code = value.get("error_code", "none")
        self.error_code_repr = value.get("error_code_repr", error_code)
        self.error_code = _EnumValue(error_code)
        if value.get("time_spec") is not None:
            self.time_spec = TimeSpec(_real_secs(value["time_spec"]))
        self.out_of_sequence = value.get("out_of_sequence")
        self.fragment_offset = value.get("fragment_offset")
        self.more_fragments = value.get("more_fragments")

    def strerror(self):
        return self.error_code_repr


class TXMetadata(_StrictObject):
    __slots__ = ("has_time_spec", "time_spec", "end_of_burst")

    def __init__(self):
        self.has_time_spec = False
        self.time_spec = None
        self.end_of_burst = False

    def as_payload(self):
        return {
            "__uhd_type__": "TXMetadata",
            "has_time_spec": bool(self.has_time_spec),
            "time_spec": payload(self.time_spec) if self.time_spec is not None else None,
            "end_of_burst": bool(self.end_of_burst),
        }


class _Streamer(_StrictObject):
    __slots__ = ("usrp", "handle")

    def __init__(self, usrp_obj, handle):
        self.usrp = usrp_obj
        self.handle = handle.get("handle") if isinstance(handle, dict) else handle

    def get_max_num_samps(self):
        return self.usrp.stream_get_max_num_samps(self.handle)

    def issue_stream_cmd(self, stream_cmd):
        return self.usrp.stream_issue_stream_cmd({
            "handle": self.handle,
            "stream_cmd": payload(stream_cmd),
        })

    def close(self):
        return self.usrp.stream_close(self.handle)


class RXStreamer(_Streamer):
    __slots__ = ()

    def recv(self, recv_buffer, metadata, timeout=0.1, one_packet=False):
        count, buffer, metadata_value = self.usrp.stream_recv({
            "handle": self.handle,
            "recv_buffer": recv_buffer,
            "timeout": float(timeout),
            "one_packet": bool(one_packet),
        })
        recv_buffer[...] = buffer
        metadata.update(metadata_value)
        return count


class TXStreamer(_Streamer):
    __slots__ = ()

    def send(self, samples, metadata, timeout=0.1):
        return self.usrp.stream_send({
            "handle": self.handle,
            "samples": samples,
            "metadata": payload(metadata),
            "timeout": float(timeout),
        })


class usrp:
    SubdevSpec = staticmethod(subdev_spec)
    StreamArgs = StreamArgs


class types:
    TimeSpec = TimeSpec
    StreamCMD = StreamCMD
    StreamMode = StreamMode
    RXMetadata = RXMetadata
    TXMetadata = TXMetadata
    RXMetadataErrorCode = RXMetadataErrorCode


class _LibPyUhdTypes:
    tune_request = staticmethod(tune_request)


class libpyuhd:
    types = _LibPyUhdTypes


def bind_client_class(class_name, cls):
    setattr(usrp, class_name, cls)


__all__ = [
    "usrp",
    "types",
    "libpyuhd",
    "payload",
    "get_rx_stream",
    "get_tx_stream",
    "close_all_streams",
    "streamer",
    "RXStreamer",
    "TXStreamer",
]
