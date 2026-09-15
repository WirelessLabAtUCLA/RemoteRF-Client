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

import ast
from collections.abc import Callable
from .grpc_client import rpc_client
from ..common.utils import *

import datetime

class RemoteRFAccount:
    def __init__(self, username:str=None, password:str=None, email:str=None, *, backend=None):
        self.username = username
        self.password = password
        self.email = email
        self.enrollment_code = ""
        self.is_admin = False
        self.backend = backend
        # Set by an HTTPS registration: the home says whether a verification
        # email is actually on its way, or the account is already usable.
        self.needs_verification = True
        # A deployment-issued session token (login with remember=True). It
        # replaces the password for every later call and is what gets stored.
        self.session_token = None

    @property
    def is_https_home(self):
        return self.backend is not None and self.backend.capabilities['account_transport'] == 'https-json'

    def _rpc_credentials(self):
        if self.is_https_home:
            from ..deployment.http import AccountBackendError
            raise AccountBackendError('This operation is unavailable at this account home')
        return self.username or '', self.password or ''

    def _call(self, *, function_name, args):
        if self.backend is not None:
            self._rpc_credentials()
            return self.backend.call(function_name, args)
        return rpc_client(function_name=function_name, args=args)

    def create_user(self):
        if self.is_https_home:
            result = self.backend.register(self.username, self.email, self.password) or {}
            self.password = None
            self.needs_verification = result.get('status') != 'active'
            print(result.get('message') or 'Registration received.')
            return True
        response = self._call(function_name="ACC:create_user", args={"un":map_arg(self.username), "pw":map_arg(self.password), "em":map_arg(self.email), "ec":map_arg(self.enrollment_code)})
        if 'UC' in response.results:
            print(f'User {unmap_arg(response.results["UC"])} successfully created.')
            return True
        elif 'UE' in response.results:
            print(f'Error: {unmap_arg(response.results["UE"])}')
            return False
    
    def login_user(self, *, remember=False):
        if self.is_https_home:
            try:
                result = self.backend.login(self.username, self.password)
                self.username = result['username']
                print(f'User {self.username} successful login.')
                return True
            finally:
                self.password = None
        username, credential_secret = self._rpc_credentials()
        args = {"un": map_arg(username), "pw": map_arg(credential_secret)}
        if remember:
            args["remember"] = map_arg(True)
        response = self._call(function_name="ACC:login", args=args)
        if 'UC' in response.results:
            print(f'User {unmap_arg(response.results["UC"])} successful login.')
            if 'token' in response.results:
                # From here on the token is the credential; the password is gone.
                self.session_token = unmap_arg(response.results['token'])
                self.password = self.session_token
            return True
        elif 'UE' in response.results:
            print(f'Error: {unmap_arg(response.results["UE"])}')
            return False
    
    def reserve_device(self, device_id, start_time:datetime, end_time:datetime):
        if self.is_https_home:
            reservation = self.backend.reserve(device_id, int(start_time.timestamp()), int(end_time.timestamp()))
            try:
                from ..drivers.dynamic_device import install_driver
                install_driver(device_id=reservation['device_id'])
            except Exception as e:
                print(f"Warning: could not install driver for device {reservation['device_id']}: {e}")
            # The HOME reference is the driver handle: adi.Pluto("<deployment>:<id>")
            return reservation['device_id']
        username, credential_secret = self._rpc_credentials()
        response = self._call(function_name="ACC:reserve_device", args={"un":map_arg(username), "pw":map_arg(credential_secret), "dd":map_arg(device_id), "st":map_arg(int(start_time.timestamp())), "et":map_arg(int(end_time.timestamp()))})

        if 'ace' in response.results:
            raise Exception(f'{unmap_arg(response.results["ace"])}')
        elif 'Token' in response.results:
            token = unmap_arg(response.results["Token"])
            try:
                from ..drivers.dynamic_device import install_driver
                install_driver(device_id=device_id)
            except Exception as e:
                print(f"Warning: could not install driver for device {device_id}: {e}")
            return token
            
    def get_reservations(self):
        if self.is_https_home:
            return self.backend.reservations()
        username, credential_secret = self._rpc_credentials()
        return self._call(function_name='ACC:get_res', args={"un":map_arg(username), "pw":map_arg(credential_secret)})
    
    def get_devices(self):
        if self.is_https_home:
            return self.backend.devices()
        username, credential_secret = self._rpc_credentials()
        return self._call(function_name='ACC:get_dev', args={"un":map_arg(username), "pw":map_arg(credential_secret)})
    
    def cancel_reservation(self, res_id):
        if self.is_https_home:
            return self.backend.cancel(res_id)
        username, credential_secret = self._rpc_credentials()
        return self._call(function_name='ACC:cancel_res', args={"un":map_arg(username), "pw":map_arg(credential_secret), "res_id":map_arg(res_id)})
    
    def get_perms(self):
        if self.is_https_home:
            return self.backend.permissions()
        username, credential_secret = self._rpc_credentials()
        return self._call(function_name='ACC:get_perms', args={"un":map_arg(username), "pw":map_arg(credential_secret)})
    
    def logout(self):
        """Revoke the stored session on the deployment (best effort)."""
        if self.is_https_home:
            return self.backend.logout()
        if self.session_token:
            try:
                self._call(function_name="ACC:logout", args={"un": map_arg(self.username or ''), "pw": map_arg(self.session_token)})
            except Exception:  # noqa: BLE001 - revocation is best effort
                pass
            self.session_token = None

    def set_enroll(self):
        if self.is_https_home:
            return self.backend.enroll(self.enrollment_code)
        username, credential_secret = self._rpc_credentials()
        return self._call(function_name='ACC:set_enroll', args={"un":map_arg(username), "pw":map_arg(credential_secret), "ec":map_arg(self.enrollment_code)})
    
    
