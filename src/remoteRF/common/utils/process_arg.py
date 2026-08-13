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

"""Serialization helpers for the legacy GenericRPC protocol."""
from __future__ import annotations

import json
import math
from typing import Any

import numpy as np

from ..grpc import grpc_pb2

_JSON_TYPE_KEY = "__remoterf_json_type__"
_MAX_NDARRAY_BYTES = 64 * 1024 * 1024
_MAX_NDARRAY_DIMENSIONS = 8
_SUPPORTED_NDARRAY_KINDS = {"b", "i", "u", "f", "c"}


def _validate_ndarray(dtype: np.dtype, shape) -> int:
    """Return the expected payload length for a safe numeric ndarray."""
    if dtype.hasobject or dtype.kind not in _SUPPORTED_NDARRAY_KINDS:
        raise ValueError(f"unsupported ndarray dtype: {dtype}")

    normalized_shape = tuple(int(item) for item in shape)
    if (
        len(normalized_shape) > _MAX_NDARRAY_DIMENSIONS
        or any(item < 0 for item in normalized_shape)
    ):
        raise ValueError("invalid ndarray shape")

    expected = math.prod(normalized_shape) * dtype.itemsize
    if expected > _MAX_NDARRAY_BYTES:
        raise ValueError(f"ndarray exceeds {_MAX_NDARRAY_BYTES} bytes")
    return expected


def _decode_ndarray(descriptor: grpc_pb2.NDArrayValue) -> np.ndarray:
    try:
        dtype = np.dtype(descriptor.dtype)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid ndarray dtype: {descriptor.dtype!r}") from exc

    shape = tuple(int(item) for item in descriptor.shape)
    expected = _validate_ndarray(dtype, shape)
    actual = len(descriptor.data)
    if actual != expected:
        raise ValueError(
            f"ndarray byte length mismatch: expected {expected}, got {actual}"
        )
    return np.frombuffer(descriptor.data, dtype=dtype).reshape(shape).copy()


def _json_safe(value: Any):
    if hasattr(value, "as_payload"):
        return _json_safe(value.as_payload())
    if isinstance(value, np.ndarray):
        if value.dtype == object:
            raise ValueError(f"Cannot JSON-map object-dtype array: {value!r}")
        array = np.asarray(value)
        if np.iscomplexobj(array):
            data = [[float(num.real), float(num.imag)] for num in array.ravel()]
            return {
                _JSON_TYPE_KEY: "ndarray",
                "dtype": str(array.dtype),
                "shape": list(array.shape),
                "complex": True,
                "data": data,
            }
        return {
            _JSON_TYPE_KEY: "ndarray",
            "dtype": str(array.dtype),
            "shape": list(array.shape),
            "complex": False,
            "data": array.tolist(),
        }
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, complex):
        return {
            _JSON_TYPE_KEY: "complex",
            "real": float(value.real),
            "imag": float(value.imag),
        }
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _json_restore(value: Any):
    if isinstance(value, dict):
        kind = value.get(_JSON_TYPE_KEY)
        if kind == "ndarray":
            shape = tuple(value.get("shape", ()))
            dtype = value.get("dtype")
            if value.get("complex"):
                data = [complex(real, imag) for real, imag in value.get("data", [])]
                return np.array(data, dtype=dtype or np.complex64).reshape(shape)
            return np.array(value.get("data", []), dtype=dtype).reshape(shape)
        if kind == "complex":
            return complex(value.get("real", 0.0), value.get("imag", 0.0))
        return {key: _json_restore(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_restore(item) for item in value]
    return value


def unmap_arg(arg: grpc_pb2.Argument):
    kind = arg.WhichOneof("value")
    if kind == "int64_value":
        return arg.int64_value
    if kind == "float_value":
        return arg.float_value
    if kind == "string_value":
        return arg.string_value
    if kind == "bool_value":
        return arg.bool_value
    if kind == "ndarray_value":
        return _decode_ndarray(arg.ndarray_value)
    if kind == "real_array":
        shape = tuple(arg.real_array.shape.dim)
        return np.array(arg.real_array.data, dtype=np.float64).reshape(shape)
    if kind == "complex_array":
        shape = tuple(arg.complex_array.shape.dim)
        data = [complex(item.real, item.imag) for item in arg.complex_array.data]
        return np.array(data, dtype=np.complex64).reshape(shape)
    if kind == "json_value":
        return _json_restore(json.loads(arg.json_value))
    if kind == "bytes_value":
        return bytes(arg.bytes_value)
    raise ValueError(f"Unknown argument type during unmapping: {arg}")


def map_arg(value: Any) -> grpc_pb2.Argument:
    arg = grpc_pb2.Argument()

    if isinstance(value, (bool, np.bool_)):
        arg.bool_value = bool(value)
    elif hasattr(value, "as_payload"):
        return map_arg(value.as_payload())
    elif value is None or isinstance(value, dict):
        arg.json_value = json.dumps(_json_safe(value), separators=(",", ":"))
    elif isinstance(value, (int, np.integer)):
        arg.int64_value = int(value)
    elif isinstance(value, (float, np.floating)):
        arg.float_value = float(value)
    elif isinstance(value, str):
        arg.string_value = value
    elif isinstance(value, (bytes, bytearray, memoryview)):
        arg.bytes_value = bytes(value)
    elif isinstance(value, (list, tuple)):
        try:
            array = np.asarray(value)
        except Exception:
            arg.json_value = json.dumps(_json_safe(value), separators=(",", ":"))
            return arg
        if array.dtype == object:
            try:
                array = np.stack(value)
            except Exception:
                arg.json_value = json.dumps(_json_safe(value), separators=(",", ":"))
                return arg
        return map_arg(array)
    elif isinstance(value, np.ndarray):
        if value.dtype == object:
            raise ValueError(f"Cannot map object-dtype array: {value!r}")
        if value.dtype.kind not in _SUPPORTED_NDARRAY_KINDS:
            arg.json_value = json.dumps(
                _json_safe(value.tolist()), separators=(",", ":")
            )
            return arg

        array = np.asarray(value)
        _validate_ndarray(array.dtype, array.shape)
        if not array.flags.c_contiguous:
            array = np.ascontiguousarray(array)
        descriptor = arg.ndarray_value
        descriptor.dtype = array.dtype.str
        descriptor.shape.extend(array.shape)
        descriptor.data = array.tobytes(order="C")
    else:
        raise ValueError(f"Unknown argument type during mapping: {value}")
    return arg


def map_array_proto(np_array) -> grpc_pb2.Argument:
    """Backward-compatible wrapper around :func:`map_arg`."""
    return map_arg(np.asarray(np_array))


def unmap_array_proto(arg: grpc_pb2.Argument) -> np.ndarray:
    """Backward-compatible array-only wrapper around :func:`unmap_arg`."""
    value = unmap_arg(arg)
    if not isinstance(value, np.ndarray):
        raise ValueError("Argument does not contain a recognizable array.")
    return value
