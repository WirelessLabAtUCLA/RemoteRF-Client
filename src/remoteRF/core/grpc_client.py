import socket
import getpass
from pathlib import Path

import grpc
from ..common.grpc import grpc_pb2
from ..common.grpc import grpc_pb2_grpc
from ..common.utils import *

# wlab credentials
server_ip = '164.67.195.207'
server_port = '61005'

# Server.crt
certs_path = Path(__file__).resolve().parent.parent/'core'/'certs'/'server.crt'
with certs_path.open('rb') as f:
    trusted_certs = f.read()
    
credentials = grpc.ssl_channel_credentials(root_certificates=trusted_certs)
channel = grpc.secure_channel(f'{server_ip}:{server_port}', credentials)
stub = grpc_pb2_grpc.GenericRPCStub(channel)

tcp_calls = 0

def get_tcp_calls():
    return tcp_calls
        
def rpc_client(*, function_name, args):
    global tcp_calls
    tcp_calls += 1
    # print(tcp_calls)
    # if not is_connected:
    #     response = rpc_client(function_name="UserLogin", args={"username": grpc_pb2.Argument(string_value=input("Username: ")), "password": grpc_pb2.Argument(string_value=getpass.getpass("Password: ")), "client_ip": grpc_pb2.Argument(string_value=local_ip)})
    #     if (response.results['status'].string_value == 'Success'):
    #         print("Login successful.")
    #         is_connected = True
    
    # TODO: Handle user login
    
    # print(f"Opening connection to {server_ip}:{server_port}")
    
    # TODO: Handle Errors
    
    # print(f"Calling function: {function_name}")
    response = stub.Call(grpc_pb2.GenericRPCRequest(function_name=function_name, args=args))
    
    if 'a' in response.results:
        print(f"Error: {unmap_arg(response.results['a'])}")
        exit()
        
    if 'UE' in response.results:
        print(f"UserError: {unmap_arg(response.results['UE'])}")
        input("Hit enter to continue...")
            
    
    return response

#region Example Usage

# if __name__ == '__main__':
#     args={
#         "key1": grpc_pb2.Argument(string_value="Hello"),
#         "key2": grpc_pb2.Argument(int32_value=123),
#         "key3": grpc_pb2.Argument(float_value=4.56),
#         "key4": grpc_pb2.Argument(bool_value=True),
#         "client_ip": grpc_pb2.Argument(string_value=local_ip)
#     }
    
#     response = rpc_client(function_name="echo", args=args)
    
#     if 'client_ip' in response.results:
#         del response.results['client_ip']
    
#     # Print results
#     print("Received response:")
#     for key, arg in response.results.items():
#         # Decode the oneof fields
#         if arg.HasField('string_value'):
#             value = arg.string_value
#         elif arg.HasField('int32_value'):
#             value = arg.int32_value
#         elif arg.HasField('float_value'):
#             value = arg.float_value
#         elif arg.HasField('bool_value'):
#             value = arg.bool_value
#         else:
#             value = "Undefined"
            
#         print(f"{key}: {value}")

#endregion
    
