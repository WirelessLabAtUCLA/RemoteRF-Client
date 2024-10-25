from ..grpc import grpc_pb2, grpc_pb2_grpc
import numpy as np

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
    else:
        raise ValueError(f"Unknown argument type during unmapping: {arg}")
    
def map_arg(value):
    arg = grpc_pb2.Argument()
    
    if isinstance(value, int):
        arg.int64_value = value
    elif isinstance(value, float):
        arg.float_value = value
    elif isinstance(value, str):
        arg.string_value = value
    elif isinstance(value, bool):
        arg.bool_value = value
    elif isinstance(value, np.ndarray):
        if np.iscomplexobj(value):
            complex_array = arg.complex_array
            complex_array.shape.dim.extend(value.shape)
            for num in value.ravel():
                complex_num = complex_array.data.add()
                complex_num.real = num.real
                complex_num.imag = num.imag
        else:
            float_array = arg.real_array
            float_array.shape.dim.extend(value.shape)
            float_array.data.extend(value.ravel())
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

