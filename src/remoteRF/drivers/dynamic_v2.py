"""Generic Dynamic IDL v2 runtime and generated UHD namespace builder."""
from __future__ import annotations

import functools
import hashlib
import json
import keyword
import numbers
import types
import weakref

import numpy as np

from ..core.dynamic_v2_transport import DynamicV2Transport
from ..core.v2_errors import RemoteRFProtocolError
from .support import uhd_v2

_SUPPORTED_SAMPLE_DTYPES = {
    np.dtype(np.complex64),
    np.dtype(np.complex128),
    np.dtype(np.int16),
    np.dtype(np.float32),
    np.dtype(np.float64),
}
_EXECUTION_KINDS = {
    "client_local",
    "server_control",
    "server_handle",
    "sample_stream",
    "hybrid",
    "server_optimized",
}
_PARAMETER_KINDS = {
    "positional_only",
    "positional_or_keyword",
    "var_positional",
    "keyword_only",
    "var_keyword",
}
_PARAMETER_DIRECTIONS = {"in", "out", "inout"}


def _canonical_native_version(value) -> str:
    """Strip distro/build metadata while retaining the pinned upstream API."""
    text = str(value or "").strip()
    if text.lower().startswith("uhd "):
        text = text[4:].strip()
    return text.partition("-")[0].strip()


def validate_schema_v2(schema: dict) -> dict:
    if not isinstance(schema, dict) or schema.get("schema_version") != "2.0":
        raise RemoteRFProtocolError("Dynamic schema_version must be '2.0'")
    device_type = str(schema.get("device_type") or "")
    client_class = str(schema.get("client_class") or "")
    if (
        not device_type.isidentifier()
        or keyword.iskeyword(device_type)
        or not client_class.isidentifier()
        or keyword.iskeyword(client_class)
    ):
        raise RemoteRFProtocolError(
            "Dynamic schema has an invalid device_type or client_class"
        )
    expected_hash = str(schema.get("schema_hash") or "")
    body = dict(schema)
    body.pop("schema_hash", None)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    actual_hash = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if expected_hash != actual_hash:
        raise RemoteRFProtocolError("Dynamic schema hash validation failed")

    object_paths = set()
    for item in schema.get("objects", ()):
        path = str(item.get("python_path") or "")
        if (
            not path
            or path in object_paths
            or any(
                not part.isidentifier() or keyword.iskeyword(part)
                for part in path.split(".")
            )
        ):
            raise RemoteRFProtocolError(
                f"invalid or duplicate schema object path: {path!r}"
            )
        object_paths.add(path)
        for alias in item.get("aliases", ()):
            if any(
                not part.isidentifier() or keyword.iskeyword(part)
                for part in str(alias).split(".")
            ):
                raise RemoteRFProtocolError(
                    f"invalid schema object alias: {alias!r}"
                )

    method_keys = set()
    for descriptor in schema.get("methods", ()):
        owner = str(descriptor.get("owner") or "")
        name = str(descriptor.get("name") or "")
        key = (owner, name)
        if (
            not owner
            or not name.isidentifier()
            or keyword.iskeyword(name)
            or key in method_keys
            or descriptor.get("execution") not in _EXECUTION_KINDS
        ):
            raise RemoteRFProtocolError(
                f"invalid or duplicate schema method: {key!r}"
            )
        method_keys.add(key)
        overload_ids = set()
        for overload in descriptor.get("overloads", ()):
            overload_id = str(overload.get("id") or "")
            if not overload_id or overload_id in overload_ids:
                raise RemoteRFProtocolError(
                    f"invalid overload id for {owner}.{name}"
                )
            overload_ids.add(overload_id)
            parameter_names = set()
            for parameter in overload.get("parameters", ()):
                parameter_name = str(parameter.get("name") or "")
                if (
                    not parameter_name.isidentifier()
                    or keyword.iskeyword(parameter_name)
                    or parameter_name in parameter_names
                    or parameter.get("kind") not in _PARAMETER_KINDS
                    or parameter.get("direction") not in _PARAMETER_DIRECTIONS
                ):
                    raise RemoteRFProtocolError(
                        f"invalid parameter in {owner}.{name}.{overload_id}"
                    )
                parameter_names.add(parameter_name)
    return schema


def _validate_sample_array(array, *, operation: str):
    dtype = np.dtype(array.dtype).newbyteorder("=")
    if dtype not in _SUPPORTED_SAMPLE_DTYPES:
        raise ValueError(f"{operation} uses unsupported sample dtype {array.dtype}")


def fetch_schema_v2(token: str, *, transport=None) -> dict:
    return validate_schema_v2(
        (transport or DynamicV2Transport()).get_schema(token)
    )


def _matches_type(value, wire_type: str) -> bool:
    choices = {item.strip() for item in str(wire_type or "any").split("|")}
    if value is None:
        return "None" in choices or "any" in choices
    if "any" in choices:
        return True
    for choice in choices:
        if choice == "None" and value is None:
            return True
        if choice == "bool" and isinstance(value, (bool, np.bool_)):
            return True
        if choice == "int" and isinstance(value, (numbers.Integral, np.integer)) and not isinstance(value, bool):
            return True
        if (
            choice == "float"
            and isinstance(value, (numbers.Real, np.integer, np.floating))
            and not isinstance(value, bool)
        ):
            return True
        if choice == "str" and isinstance(value, str):
            return True
        if choice == "bytes" and isinstance(value, (bytes, bytearray, memoryview)):
            return True
        if choice == "complex" and isinstance(value, (complex, np.complexfloating)):
            return True
        if choice.startswith("list") and isinstance(value, (list, tuple)):
            if "[" not in choice:
                return True
            item_type = choice.partition("[")[2].rpartition("]")[0]
            return all(_matches_type(item, item_type) for item in value)
        if choice.startswith("dict") and isinstance(value, dict):
            return True
        if choice == "ndarray" and isinstance(value, np.ndarray):
            return True
        if choice.startswith("uhd."):
            class_name = choice.rsplit(".", 1)[-1]
            expected = getattr(uhd_v2, class_name, None) or globals().get(class_name)
            if isinstance(expected, type) and isinstance(value, expected):
                return True
    return False


class OverloadBinder:
    def __init__(self, method_descriptor: dict):
        self.descriptor = method_descriptor

    def bind(self, args, kwargs):
        errors = []
        for candidate in self.descriptor.get("overloads", ()):
            try:
                return candidate["id"], self._bind_one(candidate, args, kwargs)
            except TypeError as exc:
                errors.append(str(exc))
        forms = ", ".join(
            str(item.get("id") or "native")
            for item in self.descriptor.get("overloads", ())
        )
        raise TypeError(
            f"{self.descriptor['name']}(): incompatible arguments; "
            f"supported overloads: {forms or 'none'}"
        )

    def _bind_one(self, candidate, args, kwargs):
        positional = list(args)
        keywords = dict(kwargs)
        bound = {}
        call_positional = []
        extra_positional = []
        extra_keywords = {}
        for spec in candidate.get("parameters", ()):
            name = spec["name"]
            kind = spec.get("kind", "positional_or_keyword")
            if kind == "var_positional":
                extra_positional = positional
                positional = []
                continue
            if kind == "var_keyword":
                extra_keywords = keywords
                keywords = {}
                continue

            supplied = False
            if kind in {"positional_only", "positional_or_keyword"} and positional:
                value = positional.pop(0)
                supplied = True
                if name in keywords:
                    raise TypeError(f"multiple values for argument {name!r}")
            elif kind != "positional_only" and name in keywords:
                value = keywords.pop(name)
                supplied = True
            elif not spec.get("required", True):
                value = spec.get("default")
            else:
                raise TypeError(f"missing required argument {name!r}")

            if supplied and not _matches_type(value, spec.get("type", "any")):
                raise TypeError(
                    f"argument {name!r} must match {spec.get('type')}, "
                    f"not {type(value).__name__}"
                )
            if kind == "positional_only":
                call_positional.append(value)
            else:
                bound[name] = value

        if positional:
            raise TypeError("too many positional arguments")
        if keywords:
            raise TypeError(f"unexpected keyword arguments: {sorted(keywords)}")
        if call_positional or extra_positional:
            bound["__args__"] = call_positional + extra_positional
        if extra_keywords:
            bound["__kwargs__"] = extra_keywords
        return bound


def _apply_mutations(bound, mutations):
    for name, snapshot in mutations.items():
        target = bound.get(name)
        if target is None:
            continue
        update = getattr(target, "update", None)
        if callable(update):
            update(snapshot)


class RemoteHandleProxy:
    _type_id = "remote_handle"

    def __init__(self, owner, handle, generation=0):
        self._owner = owner
        self._transport = owner._transport
        self._session_id = owner._session_id
        self._handle = str(handle)
        self._generation = int(generation)
        self._closed = False
        self._channels = []
        self._sequence = 1
        self._finalizer = weakref.finalize(
            self,
            type(self)._finalize,
            weakref.ref(owner),
            self._session_id,
            self._handle,
        )

    @staticmethod
    def _finalize(owner_ref, session_id, handle):
        owner = owner_ref()
        if owner is None or getattr(owner, "_closed", True):
            return
        try:
            owner._transport.close_handle(session_id, handle)
        except Exception:
            pass

    def _ensure_open(self):
        if self._closed or self._owner._closed:
            raise RemoteRFProtocolError("remote handle is closed or stale")

    def _invoke(self, descriptor, args, kwargs):
        self._ensure_open()
        overload_id, bound = OverloadBinder(descriptor).bind(args, kwargs)
        result, mutations = self._transport.invoke(
            self._session_id,
            self._handle,
            descriptor["name"],
            bound,
            overload_id=overload_id,
        )
        _apply_mutations(bound, mutations)
        return self._owner._wrap_result(result, descriptor, bound)

    def as_payload(self):
        self._ensure_open()
        return {
            "__remoterf_handle_arg__": self._handle,
            "type_id": self._type_id,
            "generation": self._generation,
        }

    def close(self):
        if self._closed:
            return False
        self._closed = True
        self._finalizer.detach()
        return self._transport.close_handle(self._session_id, self._handle)

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


class RXStreamer(RemoteHandleProxy):
    _type_id = "uhd.usrp.RXStreamer"

    def recv(self, buffs, *args, **kwargs):
        self._ensure_open()
        buffer = np.asarray(buffs)
        _validate_sample_array(buffer, operation="RX")
        if not buffer.flags.writeable:
            raise ValueError("RX buffer must be writable")
        if buffer.ndim not in {1, 2}:
            raise ValueError("RX buffer must be one- or two-dimensional")

        timeout = float(kwargs.pop("timeout", 0.1))
        one_packet = bool(kwargs.pop("one_packet", False))
        if kwargs:
            raise TypeError(f"unexpected keyword arguments: {sorted(kwargs)}")
        if not args:
            raise TypeError("recv() missing metadata argument")
        if isinstance(args[0], numbers.Integral):
            sample_count = int(args[0])
            if len(args) < 2:
                raise TypeError("recv() missing metadata argument")
            metadata = args[1]
            if len(args) >= 3:
                timeout = float(args[2])
            if len(args) >= 4:
                one_packet = bool(args[3])
            if len(args) > 4:
                raise TypeError("recv() received too many arguments")
        else:
            metadata = args[0]
            sample_count = int(buffer.shape[-1])
            if len(args) >= 2:
                timeout = float(args[1])
            if len(args) >= 3:
                one_packet = bool(args[2])
            if len(args) > 3:
                raise TypeError("recv() received too many arguments")
        if sample_count < 0 or sample_count > buffer.shape[-1]:
            raise ValueError("nsamps_per_buff exceeds RX buffer capacity")
        if buffer.ndim == 2 and self._channels and buffer.shape[0] != len(self._channels):
            raise ValueError("RX channel dimension does not match StreamArgs.channels")
        requested_shape = list(buffer.shape)
        requested_shape[-1] = sample_count
        requested_bytes = int(np.prod(requested_shape)) * buffer.dtype.itemsize
        maximum = int(
            self._owner._capabilities.get("remote_resource_limits", {}).get(
                "maximum_sample_payload_bytes",
                4 * 1024 * 1024,
            )
        )
        if requested_bytes > maximum:
            raise ValueError(f"RX request exceeds the {maximum}-byte remote limit")

        count, samples, metadata_snapshot = self._transport.recv(
            session_id=self._session_id,
            handle=self._handle,
            generation=self._generation,
            sequence=self._sequence,
            buffer=buffer,
            sample_count=sample_count,
            channels=self._channels,
            timeout=timeout,
            one_packet=one_packet,
            metadata=metadata,
        )
        self._sequence += 2
        if buffer.ndim == 1:
            buffer[:count] = samples[:count]
        else:
            buffer[:, :count] = samples[:, :count]
        update = getattr(metadata, "update", None)
        if not callable(update):
            raise TypeError("metadata must be a mutable RXMetadata-like object")
        update(metadata_snapshot)
        return count


class TXStreamer(RemoteHandleProxy):
    _type_id = "uhd.usrp.TXStreamer"

    def send(self, buffs, *args, **kwargs):
        self._ensure_open()
        samples = np.asarray(buffs)
        _validate_sample_array(samples, operation="TX")
        if samples.ndim not in {1, 2}:
            raise ValueError("TX samples must be one- or two-dimensional")
        timeout = float(kwargs.pop("timeout", 0.1))
        if kwargs:
            raise TypeError(f"unexpected keyword arguments: {sorted(kwargs)}")
        if not args:
            raise TypeError("send() missing metadata argument")
        if isinstance(args[0], numbers.Integral):
            sample_count = int(args[0])
            if len(args) < 2:
                raise TypeError("send() missing metadata argument")
            metadata = args[1]
            if len(args) >= 3:
                timeout = float(args[2])
            if len(args) > 3:
                raise TypeError("send() received too many arguments")
        else:
            metadata = args[0]
            sample_count = int(samples.shape[-1])
            if len(args) >= 2:
                timeout = float(args[1])
            if len(args) > 2:
                raise TypeError("send() received too many arguments")
        if sample_count < 0 or sample_count > samples.shape[-1]:
            raise ValueError("nsamps_per_buff exceeds TX sample capacity")
        if samples.ndim == 2 and self._channels and samples.shape[0] != len(self._channels):
            raise ValueError("TX channel dimension does not match StreamArgs.channels")
        subset = samples[..., :sample_count]
        maximum = int(
            self._owner._capabilities.get("remote_resource_limits", {}).get(
                "maximum_sample_payload_bytes",
                4 * 1024 * 1024,
            )
        )
        if subset.nbytes > maximum:
            raise ValueError(f"TX request exceeds the {maximum}-byte remote limit")
        count = self._transport.send(
            session_id=self._session_id,
            handle=self._handle,
            generation=self._generation,
            sequence=self._sequence,
            samples=subset,
            channels=self._channels,
            timeout=timeout,
            metadata=metadata,
        )
        self._sequence += 2
        return count


class GenericRemoteHandle(RemoteHandleProxy):
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        descriptor = {
            "owner": self._type_id,
            "name": name,
            "execution": "server_handle",
            "mutations": [],
            "overloads": [
                {
                    "id": "native",
                    "parameters": [
                        {
                            "name": "args",
                            "type": "list",
                            "kind": "var_positional",
                            "direction": "in",
                            "required": True,
                        },
                        {
                            "name": "kwargs",
                            "type": "dict",
                            "kind": "var_keyword",
                            "direction": "in",
                            "required": True,
                        },
                    ],
                    "returns": "any",
                }
            ],
        }

        def invoke(*args, **kwargs):
            return self._invoke(descriptor, args, kwargs)

        invoke.__name__ = name
        return invoke


def _method_impl(descriptor):
    @functools.wraps(_method_impl)
    def generated(self, *args, **kwargs):
        return self._invoke(descriptor, args, kwargs)

    generated.__name__ = descriptor["name"]
    generated.__qualname__ = descriptor["name"]
    generated.__doc__ = descriptor.get("doc") or (
        f"Remote {descriptor['owner']}.{descriptor['name']} "
        f"({descriptor['execution']})."
    )
    generated.__text_signature__ = _text_signature(descriptor)
    return generated


def _text_signature(descriptor):
    overloads = list(descriptor.get("overloads") or ())
    if not overloads:
        return "($self, /, *args, **kwargs)"
    parameters = ["$self"]
    positional_only = 0
    has_var_positional = False
    inserted_keyword_marker = False
    for spec in overloads[0].get("parameters", ()):
        kind = spec.get("kind", "positional_or_keyword")
        name = str(spec["name"])
        if kind == "var_positional":
            parameters.append(f"*{name}")
            has_var_positional = True
            inserted_keyword_marker = True
            continue
        if kind == "var_keyword":
            parameters.append(f"**{name}")
            continue
        if kind == "keyword_only" and not inserted_keyword_marker:
            parameters.append("*")
            inserted_keyword_marker = True
        text = name
        if not spec.get("required", True):
            text += f"={spec.get('default')!r}"
        parameters.append(text)
        if kind == "positional_only":
            positional_only = len(parameters)
    if positional_only:
        parameters.insert(positional_only, "/")
    elif not has_var_positional:
        parameters.insert(1, "/")
    return "(" + ", ".join(parameters) + ")"


def _assign_path(root, dotted_path, value):
    parts = dotted_path.split(".")
    if parts[0] != "uhd":
        return
    target = root
    for part in parts[1:-1]:
        current = getattr(target, part, None)
        if current is None:
            current = types.SimpleNamespace()
            setattr(target, part, current)
        target = current
    setattr(target, parts[-1], value)


def build_uhd_bindings(schema: dict, *, transport_factory=DynamicV2Transport):
    validate_schema_v2(schema)
    method_descriptors = list(schema.get("methods", ()))
    by_owner = {}
    for descriptor in method_descriptors:
        by_owner.setdefault(descriptor["owner"], []).append(descriptor)
    generated_handle_classes = {}

    class MultiUSRP:
        def __init__(self, token: str):
            self.token = str(token)
            self._transport = transport_factory()
            self._transport.negotiate()
            opened = self._transport.open_session(self.token, schema["schema_hash"])
            required_uhd = str(
                schema.get("native_api", {}).get("version") or ""
            )
            actual_uhd = str(opened.get("uhd_version") or "")
            if (
                required_uhd
                and _canonical_native_version(actual_uhd)
                != _canonical_native_version(required_uhd)
            ):
                try:
                    self._transport.close_session(opened["session_id"])
                finally:
                    raise RemoteRFProtocolError(
                        f"client requires UHD {required_uhd}; "
                        f"server reports {actual_uhd or 'unknown'}"
                    )
            self._session_id = opened["session_id"]
            self._handle = opened["device_handle"]
            self._generation = 0
            self._closed = False
            self._capabilities = opened["capabilities"]
            self._schema = opened["schema"]
            self._handle_classes = {
                "uhd.usrp.RXStreamer": RXStreamer,
                "uhd.usrp.TXStreamer": TXStreamer,
                **generated_handle_classes,
            }
            self._finalizer = weakref.finalize(
                self,
                type(self)._finalize,
                self._transport,
                self._session_id,
            )

        @staticmethod
        def _finalize(transport, session_id):
            try:
                transport.close_session(session_id)
            except Exception:
                pass

        @property
        def remoterf_capabilities(self):
            return dict(self._capabilities)

        def _ensure_open(self):
            if self._closed:
                raise RemoteRFProtocolError("USRP device session is closed")

        def _invoke(self, descriptor, args, kwargs):
            self._ensure_open()
            overload_id, bound = OverloadBinder(descriptor).bind(args, kwargs)
            result, mutations = self._transport.invoke(
                self._session_id,
                self._handle,
                descriptor["name"],
                bound,
                overload_id=overload_id,
            )
            _apply_mutations(bound, mutations)
            return self._wrap_result(result, descriptor, bound)

        def _wrap_result(self, result, descriptor, bound):
            if isinstance(result, dict) and "__remoterf_handle__" in result:
                type_id = result["__remoterf_type__"]
                cls = self._handle_classes.get(type_id, GenericRemoteHandle)
                proxy = cls(
                    self,
                    result["__remoterf_handle__"],
                    result.get("generation", 0),
                )
                stream_args = bound.get("stream_args")
                if stream_args is None and bound.get("__args__"):
                    stream_args = bound["__args__"][0]
                if stream_args is not None:
                    proxy._channels = list(getattr(stream_args, "channels", ()) or ())
                return proxy
            return uhd_v2.decode_snapshot(result)

        def close(self):
            if self._closed:
                return False
            self._closed = True
            self._finalizer.detach()
            return self._transport.close_session(self._session_id)

        def __enter__(self):
            self._ensure_open()
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()
            return False

        def __repr__(self):
            state = "closed" if self._closed else "open"
            profile = self._capabilities.get("hardware_profile", "usrp")
            return f"<RemoteRF {profile} MultiUSRP session={state}>"

    MultiUSRP.__name__ = str(schema.get("client_class") or "MultiUSRP")
    MultiUSRP.__qualname__ = MultiUSRP.__name__
    MultiUSRP.__init__.__text_signature__ = "($self, /, token)"

    for object_descriptor in schema.get("objects", ()):
        path = str(object_descriptor.get("python_path") or "")
        if (
            object_descriptor.get("kind") == "remote_handle"
            and path not in {
                "uhd.usrp.MultiUSRP",
                "uhd.usrp.RXStreamer",
                "uhd.usrp.TXStreamer",
            }
        ):
            generated_handle_classes[path] = type(
                path.replace(".", "_"),
                (GenericRemoteHandle,),
                {"_type_id": path},
            )

    owner_classes = {
        "uhd.usrp.MultiUSRP": MultiUSRP,
        "uhd.usrp.RXStreamer": RXStreamer,
        "uhd.usrp.TXStreamer": TXStreamer,
        **generated_handle_classes,
    }
    for owner, descriptors in by_owner.items():
        cls = owner_classes.get(owner)
        if cls is None:
            continue
        for descriptor in descriptors:
            if descriptor["execution"] == "sample_stream":
                continue
            if hasattr(cls, descriptor["name"]):
                continue
            setattr(cls, descriptor["name"], _method_impl(descriptor))

    uhd = types.ModuleType("uhd")
    uhd.usrp = types.SimpleNamespace(
        MultiUSRP=MultiUSRP,
        RXStreamer=RXStreamer,
        TXStreamer=TXStreamer,
        StreamArgs=uhd_v2.StreamArgs,
        SubdevSpec=uhd_v2.SubdevSpec,
        SubdevSpecPair=uhd_v2.SubdevSpecPair,
    )
    uhd.types = types.SimpleNamespace(
        TimeSpec=uhd_v2.TimeSpec,
        DeviceAddr=uhd_v2.DeviceAddr,
        Range=uhd_v2.Range,
        MetaRange=uhd_v2.MetaRange,
        TuneRequest=uhd_v2.TuneRequest,
        TuneResult=uhd_v2.TuneResult,
        StreamCMD=uhd_v2.StreamCMD,
        StreamMode=uhd_v2.StreamMode,
        RXMetadata=uhd_v2.RXMetadata,
        RXMetadataErrorCode=uhd_v2.RXMetadataErrorCode,
        TXMetadata=uhd_v2.TXMetadata,
        TXAsyncMetadata=uhd_v2.TXAsyncMetadata,
        AsyncMetadata=uhd_v2.AsyncMetadata,
        TXMetadataEventCode=uhd_v2.TXMetadataEventCode,
        SensorValue=uhd_v2.SensorValue,
    )
    uhd.filters = types.SimpleNamespace(
        FilterType=uhd_v2.FilterType,
        FilterInfoBase=uhd_v2.FilterInfoBase,
        AnalogFilterBase=uhd_v2.AnalogFilterBase,
        AnalogFilterLP=uhd_v2.AnalogFilterLP,
        DigitalFilterBaseI16=uhd_v2.DigitalFilterBaseI16,
        DigitalFilterFIRI16=uhd_v2.DigitalFilterFIRI16,
    )
    uhd.libpyuhd = types.SimpleNamespace(
        types=types.SimpleNamespace(
            time_spec=uhd_v2.TimeSpec,
            device_addr=uhd_v2.DeviceAddr,
            tune_request=uhd_v2.TuneRequest,
            tune_result=uhd_v2.TuneResult,
        )
    )

    for object_descriptor in schema.get("objects", ()):
        path = object_descriptor.get("python_path", "")
        handle_class = generated_handle_classes.get(path)
        if handle_class is not None:
            _assign_path(uhd, path, handle_class)
        codec = object_descriptor.get("codec")
        cls = uhd_v2.CODEC_CLASSES.get(codec)
        if cls is not None:
            _assign_path(uhd, path, cls)
            for alias in object_descriptor.get("aliases", ()):
                _assign_path(uhd, alias, cls)
    return uhd, MultiUSRP


def render_stub(schema: dict) -> str:
    """Render a compact .pyi with declared overloads for editor support."""
    lines = [
        "from typing import Any, overload",
        "",
        "class MultiUSRP:",
        "    def __init__(self, token: str) -> None: ...",
        "    @property",
        "    def remoterf_capabilities(self) -> dict[str, Any]: ...",
        "    def close(self) -> bool: ...",
    ]
    for descriptor in schema.get("methods", ()):
        if descriptor.get("owner") != "uhd.usrp.MultiUSRP":
            continue
        overloads = descriptor.get("overloads", ())
        for candidate in overloads:
            if len(overloads) > 1:
                lines.append("    @overload")
            params = ["self"]
            for spec in candidate.get("parameters", ()):
                kind = spec.get("kind")
                if kind == "var_positional":
                    params.append(f"*{spec['name']}: Any")
                    continue
                if kind == "var_keyword":
                    params.append(f"**{spec['name']}: Any")
                    continue
                text = f"{spec['name']}: Any"
                if not spec.get("required", True):
                    text += f" = {spec.get('default')!r}"
                params.append(text)
            lines.append(
                f"    def {descriptor['name']}({', '.join(params)}) -> Any: ..."
            )
    lines.extend(["", "uhd: Any", ""])
    return "\n".join(lines)
