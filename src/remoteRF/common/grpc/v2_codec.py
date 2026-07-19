"""Wire codec for Dynamic protocol v2 control values."""
from __future__ import annotations

import json
import math

import numpy as np

from . import grpc_pb2

_JSON_TYPE_KEY = "__remoterf_json_type__"
_MAX_CONTROL_ARRAY_BYTES = 16 * 1024 * 1024
_MAX_CONTROL_ARRAY_DIMENSIONS = 8
_MIN_INT64 = -(1 << 63)
_MAX_INT64 = (1 << 63) - 1


def _validate_array(dtype: np.dtype, shape) -> int:
    if dtype.hasobject or dtype.kind not in {"b", "i", "u", "f", "c"}:
        raise ValueError(f"unsupported ndarray dtype: {dtype}")
    shape = tuple(int(item) for item in shape)
    if len(shape) > _MAX_CONTROL_ARRAY_DIMENSIONS or any(item < 0 for item in shape):
        raise ValueError("invalid ndarray shape")
    expected = math.prod(shape) * dtype.itemsize
    if expected > _MAX_CONTROL_ARRAY_BYTES:
        raise ValueError(
            f"control ndarray exceeds {_MAX_CONTROL_ARRAY_BYTES} bytes"
        )
    return expected


def _json_safe(value):
    if hasattr(value, "as_payload"):
        return _json_safe(value.as_payload())
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, complex):
        return {
            _JSON_TYPE_KEY: "complex",
            "real": float(value.real),
            "imag": float(value.imag),
        }
    if isinstance(value, int) and not _MIN_INT64 <= value <= _MAX_INT64:
        return {
            _JSON_TYPE_KEY: "integer",
            "value": str(value),
        }
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _json_restore(value):
    if isinstance(value, dict):
        if value.get(_JSON_TYPE_KEY) == "complex":
            return complex(value.get("real", 0.0), value.get("imag", 0.0))
        if value.get(_JSON_TYPE_KEY) == "integer":
            return int(value["value"])
        return {key: _json_restore(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_restore(item) for item in value]
    return value


def encode_value(value) -> grpc_pb2.DynamicValue:
    out = grpc_pb2.DynamicValue()
    if value is None:
        out.null_value = True
    elif isinstance(value, (bool, np.bool_)):
        out.bool_value = bool(value)
    elif isinstance(value, (int, np.integer)):
        integer = int(value)
        if _MIN_INT64 <= integer <= _MAX_INT64:
            out.int64_value = integer
        else:
            out.json_value = json.dumps(
                _json_safe(integer),
                sort_keys=True,
                separators=(",", ":"),
            )
    elif isinstance(value, (float, np.floating)):
        out.double_value = float(value)
    elif isinstance(value, str):
        out.string_value = value
    elif isinstance(value, (bytes, bytearray, memoryview)):
        out.bytes_value = bytes(value)
    elif isinstance(value, np.ndarray):
        array = np.ascontiguousarray(value)
        _validate_array(array.dtype, array.shape)
        out.ndarray_value.dtype = array.dtype.str
        out.ndarray_value.shape.extend(array.shape)
        out.ndarray_value.data = array.tobytes(order="C")
    else:
        out.json_value = json.dumps(
            _json_safe(value),
            sort_keys=True,
            separators=(",", ":"),
        )
    return out


def decode_value(value: grpc_pb2.DynamicValue):
    kind = value.WhichOneof("value")
    if kind in (None, "null_value"):
        return None
    if kind == "bool_value":
        return value.bool_value
    if kind == "int64_value":
        return value.int64_value
    if kind == "double_value":
        return value.double_value
    if kind == "string_value":
        return value.string_value
    if kind == "bytes_value":
        return bytes(value.bytes_value)
    if kind == "json_value":
        return _json_restore(json.loads(value.json_value))
    if kind == "ndarray_value":
        descriptor = value.ndarray_value
        dtype = np.dtype(descriptor.dtype)
        shape = tuple(int(item) for item in descriptor.shape)
        expected = _validate_array(dtype, shape)
        if expected != len(descriptor.data):
            raise ValueError(
                f"ndarray byte length mismatch: expected {expected}, got {len(descriptor.data)}"
            )
        return np.frombuffer(descriptor.data, dtype=dtype).reshape(shape).copy()
    raise ValueError(f"unsupported DynamicValue kind: {kind!r}")
