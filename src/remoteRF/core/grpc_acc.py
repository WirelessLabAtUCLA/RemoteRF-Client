from .grpc_client import rpc_client
from ..common.utils import *

import datetime

class RemoteRFAccount:
    def __init__(self, username:str=None, password:str=None, email:str=None):
        self.username = username
        self.password = password
        self.email = email
    
    def create_user(self):
        response = rpc_client(function_name="ACC:create_user", args={"un":map_arg(self.username), "pw":map_arg(self.password), "em":map_arg(self.email)})
        if 'UC' in response.results:
            print(f'User {unmap_arg(response.results["UC"])} successfully created.')
            return True
        elif 'UE' in response.results:
            print(f'Error: {unmap_arg(response.results["UE"])}')
            return False
    
    def login_user(self):
        username = self.username
        password = self.password
        response = rpc_client(function_name="ACC:login", args={"un":map_arg(username), "pw":map_arg(password)})
        if 'UC' in response.results:
            print(f'User {unmap_arg(response.results["UC"])} successful login.')
            return True
        elif 'UE' in response.results:
            print(f'Error: {unmap_arg(response.results["UE"])}')
            return False
    
    def reserve_device(self, device_id:int, start_time:datetime, end_time:datetime):
        response = rpc_client(function_name="ACC:reserve_device", args={"un":map_arg(self.username), "pw":map_arg(self.password), "dd":map_arg(device_id), "st":map_arg(int(start_time.timestamp())), "et":map_arg(int(end_time.timestamp()))})
        
        if 'ace' in response.results:
            raise Exception(f'{unmap_arg(response.results["ace"])}')
        elif 'Token' in response.results:
            return unmap_arg(response.results["Token"])
            
    def get_reservations(self):
        return rpc_client(function_name='ACC:get_res', args={"un":map_arg(self.username), "pw":map_arg(self.password)})
    
    def get_devices(self):
        return rpc_client(function_name='ACC:get_dev', args={"un":map_arg(self.username), "pw":map_arg(self.password)})
    
    def cancel_reservation(self, res_id:int):
        return rpc_client(function_name='ACC:cancel_res', args={"un":map_arg(self.username), "pw":map_arg(self.password), "res_id":map_arg(res_id)})
    
    def get_perms(self):
        return rpc_client(function_name='ACC:get_perms', args={"un":map_arg(self.username), "pw":map_arg(self.password)})
    
    
