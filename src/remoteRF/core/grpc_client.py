import socket
import getpass
from pathlib import Path
import os
from dotenv import load_dotenv

import grpc
from ..common.grpc import grpc_pb2
from ..common.grpc import grpc_pb2_grpc
from ..common.utils import *

load_dotenv(Path.home() / ".config" / "remoterf" / ".env")
addr = os.getenv("REMOTERF_ADDR")  # "host:port"
ca_path = os.getenv("REMOTERF_CA_CERT")  # path to saved CA cert

options = [
      ('grpc.max_send_message_length', 100 * 1024 * 1024),
      ('grpc.max_receive_message_length', 100 * 1024 * 1024),
]

# Server.crt
certs_path = Path(ca_path).expanduser().resolve()
with certs_path.open('rb') as f:
    trusted_certs = f.read()
    
credentials = grpc.ssl_channel_credentials(root_certificates=trusted_certs)
channel = grpc.secure_channel(addr, credentials, options=options)
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
        
    if 'Message' in response.results:
        print(f"{unmap_arg(response.results['Message'])}")
            
    
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
    
from typing import Any, Dict, Optional

def handle_admin_command(inpu: str):
    """
    Parses commands like:
      admin help
      admin printa | printr | printp | printd
      admin rm aa | rm ar | rm a <username>
      admin setacc <username> <U|P|A> [devices=1,2,3] [max_res=5] [max_time=1800]

    Requires account.is_admin == True (client-side gate). Server still enforces.
    """
    if not getattr(account, "is_admin", False):
        print("Access denied: you are not an Admin.")
        return

    tokens = (inpu or "").strip().split()
    if len(tokens) < 2 or tokens[0].lower() != "admin":
        print("Usage: admin <command>. Try: admin help")
        return

    cmd = tokens[1].lower()

    def call_admin(fn: str, extra: dict | None = None):
        return remote_admin_rpc_client(
            function_name=fn,
            auth_un=account.username,
            auth_pw=account.password,
            args=extra or {},
            print_result=True,
        )

    if cmd in ("help", "h"):
        print("Admin commands:")
        print("  admin printa            - Print all accounts")
        print("  admin printr            - Print all reservations")
        print("  admin printp            - Print all perms")
        print("  admin printd            - Print all devices")
        print("  admin rm aa             - Remove all accounts")
        print("  admin rm ar             - Remove all reservations")
        print("  admin rm a <username>   - Remove one account")
        print("  admin setacc <u> <U|P|A> [devices=1,2] [max_res=3] [max_time=1800]")
        return

    # ----- print* -----
    if cmd in ("printa", "print_accounts", "print_all_accounts"):
        call_admin("print_all_accounts")
        return

    if cmd in ("printr", "print_res", "print_all_reservations"):
        call_admin("print_all_reservations")
        return

    if cmd in ("printp", "print_perms", "print_all_perms"):
        call_admin("print_all_perms")
        return

    if cmd in ("printd", "print_devices", "print_all_devices"):
        call_admin("print_all_devices")
        return

    # ----- rm -----
    if cmd == "rm":
        if len(tokens) < 3:
            print("Usage: admin rm <aa|ar|a> [username]")
            return

        sub = tokens[2].lower()

        if sub == "aa":
            call_admin("remove_all_users")
            return

        if sub == "ar":
            call_admin("remove_all_reservations")
            return

        if sub == "a":
            if len(tokens) < 4:
                print("Usage: admin rm a <username>")
                return
            call_admin("remove_user", {"username": tokens[3]})
            return

        print("Unknown rm subcommand. Use: aa, ar, a <username>")
        return

    # ----- setacc -----
    if cmd in ("setacc", "set_account", "setperm", "set"):
        if len(tokens) < 4:
            print("Usage: admin setacc <username> <U|P|A> [devices=1,2] [max_res=3] [max_time=1800]")
            return

        target_user = tokens[2]
        perm = tokens[3].upper()

        extra: dict = {"username": target_user, "permission": perm}

        # Parse optional k=v args (very lightweight parser)
        # Examples:
        #   devices=1,2,3
        #   max_res=5
        #   max_time=1800
        for t in tokens[4:]:
            if "=" not in t:
                continue
            k, v = t.split("=", 1)
            k = k.strip().lower()
            v = v.strip()

            if k in ("devices", "device_list", "devices_allowed", "device_ids"):
                # server-side remote_admin_call already supports "1,2,3" as string
                extra["device_list"] = v
            elif k in ("max_res", "max_reservations"):
                extra["max_reservations"] = int(v)
            elif k in ("max_time", "max_res_time", "max_reservation_time_sec"):
                extra["max_reservation_time_sec"] = int(v)

        call_admin("set_account", extra)
        return

    print(f"Unknown admin command: {cmd}. Try: admin help")


def remote_admin_rpc_client(
    *,
    function_name: str,
    auth_un: str,
    auth_pw: str,
    args: Optional[Dict[str, Any]] = None,
    raise_on_error: bool = False,
    print_result: bool = False,
) -> Dict[str, Any]:
    """
    Client helper for RemoteAdmin:<function> calls.

    Builds and sends:
      function_name = "RemoteAdmin:<function_name>"
      args include:
        auth_un, auth_pw (mapped via map_arg)
        + any additional args (mapped via map_arg if not already grpc_pb2.Argument)

    Returns:
      {
        "ok": bool,
        "result": str | None,
        "error": str | None,
        "traceback": str | None,
        "raw": grpc_pb2.GenericRPCResponse,
      }
    """
    fn = (function_name or "").strip()
    if fn.startswith("RemoteAdmin:"):
        rpc_fn = fn
    else:
        rpc_fn = f"RemoteAdmin:{fn}"

    payload: Dict[str, Any] = {
        "auth_un": map_arg(auth_un),
        "auth_pw": map_arg(auth_pw),
    }

    if args:
        for k, v in args.items():
            if v is None:
                continue
            # If caller already passed a grpc Argument, keep it.
            if isinstance(v, grpc_pb2.Argument):
                payload[str(k)] = v
            else:
                payload[str(k)] = map_arg(v)

    resp = rpc_client(function_name=rpc_fn, args=payload)

    # Parse the standardized RemoteAdmin response keys:
    #   Ok, Result, Error, Traceback (each typically map_arg-encoded)
    results = getattr(resp, "results", {}) or {}

    def _get_str(key: str) -> Optional[str]:
        if key not in results:
            return None
        try:
            return str(unmap_arg(results[key]))
        except Exception:
            try:
                return str(results[key])
            except Exception:
                return None

    ok_val = results.get("Ok", None)
    ok = False
    if ok_val is not None:
        try:
            ok = bool(unmap_arg(ok_val))
        except Exception:
            ok = False

    out = {
        "ok": ok,
        "result": _get_str("Result"),
        "error": _get_str("Error"),
        "traceback": _get_str("Traceback"),
        "raw": resp,
    }

    if print_result:
        if out["ok"]:
            print(out["result"] or "OK")
        else:
            print(out["error"] or "RemoteAdmin call failed.")
            if out["traceback"]:
                print(out["traceback"])

    if raise_on_error and not out["ok"]:
        msg = out["error"] or "RemoteAdmin call failed."
        raise RuntimeError(msg)

    return out
