from ..grpc import grpc_pb2, grpc_pb2_grpc
import json
import numpy as np

_JSON_TYPE_KEY = "__remoterf_json_type__"

def _json_safe(value):
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
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, complex):
        return {_JSON_TYPE_KEY: "complex", "real": float(value.real), "imag": float(value.imag)}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value

def _json_restore(value):
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

def unmap_arg(arg):
    if arg.HasField('int64_value'):
        return arg.int64_value
    elif arg.HasField('float_value'):
        return arg.float_value
    elif arg.HasField('string_value'):
        return arg.string_value
    elif arg.HasField('bool_value'):
        return arg.bool_value
    elif arg.HasField('real_array'):
        shape = tuple(arg.real_array.shape.dim)
        return np.array(arg.real_array.data, dtype=np.float64).reshape(shape)
    elif arg.HasField('complex_array'):
        shape = tuple(arg.complex_array.shape.dim)
        data = [complex(c.real, c.imag) for c in arg.complex_array.data]
        return np.array(data, dtype=np.complex64).reshape(shape)
    elif arg.HasField('json_value'):
        return _json_restore(json.loads(arg.json_value))
    else:
        raise ValueError(f"Unknown argument type during unmapping: {arg}")
    
def map_arg(value):
    arg = grpc_pb2.Argument()
    
    if isinstance(value, (bool, np.bool_)):
        arg.bool_value = bool(value)
    elif value is None or isinstance(value, dict):
        arg.json_value = json.dumps(_json_safe(value), separators=(",", ":"))
    elif isinstance(value, (int, np.integer)):
        arg.int64_value = int(value)
    elif isinstance(value, (float, np.floating)):
        arg.float_value = float(value)
    elif isinstance(value, str):
        arg.string_value = value
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
        if np.iscomplexobj(value):
            complex_array = arg.complex_array
            complex_array.shape.dim.extend(value.shape)
            for num in value.ravel():
                complex_num = complex_array.data.add()
                complex_num.real = float(num.real)
                complex_num.imag = float(num.imag)
        else:
            float_array = arg.real_array
            float_array.shape.dim.extend(value.shape)
            float_array.data.extend(np.asarray(value, dtype=np.float32).ravel())
    else:
        raise ValueError(f"Unknown argument type during mapping: {value}")
    return arg
        
def map_array_proto(np_array):
    arg = grpc_pb2.Argument()
    
    # Check if the array is complex
    if np.iscomplexobj(np_array):
        complex_array = grpc_pb2.ComplexArray()
        for num in np_array.flat:
            complex_number = complex_array.data.add()
            complex_number.real = num.real
            complex_number.imag = num.imag
        arg.complex_array.CopyFrom(complex_array)
    else:
        # Handle as a regular float array
        float_array = grpc_pb2.FloatArray()
        float_array.data.extend(np_array.flat)
        arg.float_array.CopyFrom(float_array)

    return arg

def unmap_array_proto(arg):
    # Check which type of array is available and convert appropriately
    if arg.HasField('complex_array'):
        # Convert ComplexArray to a numpy array of complex numbers
        data = [complex(cn.real, cn.imag) for cn in arg.complex_array.data]
        return np.array(data, dtype=np.complex64)
    elif arg.HasField('float_array'):
        # Convert FloatArray to a numpy array of floats
        return np.array(arg.float_array.data, dtype=np.float32)
    else:
        raise ValueError("Argument does not contain a recognizable array.")
