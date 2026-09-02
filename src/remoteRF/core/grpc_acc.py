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
from ..global_client.credentials import DirectLocalCredentials
from ..global_client.errors import NoActiveDeploymentError, OperationDeniedError
from ..global_client.local_sessions import GlobalDeploymentSession
from ..global_client.profile import GlobalConnectionProfile, resolve_active_profile

import datetime

class RemoteRFAccount:
    def __init__(self, username:str=None, password:str=None, email:str=None):
        self.username = username
        self.password = password
        self.email = email
        self.enrollment_code = ""
        self.is_admin = False
        self._global_session: GlobalDeploymentSession | None = None
        self._global_session_refresher: Callable[[], GlobalDeploymentSession] | None = None

    @property
    def is_global_session(self) -> bool:
        return self._global_session is not None

    def use_global_session(
        self,
        session: GlobalDeploymentSession,
        *,
        refresher: Callable[[], GlobalDeploymentSession],
    ) -> None:
        """Attach deployment-local credentials without ever calling them a password."""
        self._global_session = session
        self._global_session_refresher = refresher
        self.username = session.local_username
        self.password = None

    def clear_global_session(self) -> None:
        self._global_session = None
        self._global_session_refresher = None

    def _rpc_credentials(self) -> tuple[str, str]:
        if self._global_session is None:
            direct = DirectLocalCredentials(username=self.username or "", password=self.password or "")
            return direct.username, direct.password

        active = resolve_active_profile()
        if not isinstance(active, GlobalConnectionProfile) or active.deployment_id != self._global_session.deployment_id:
            raise NoActiveDeploymentError(
                "The cached RemoteRF Global session does not belong to the active deployment. Run: remoterf use <deployment>"
            )
        if self._global_session.is_expired():
            if self._global_session_refresher is None:
                raise NoActiveDeploymentError("The RemoteRF Global deployment session expired. Run: remoterf use <deployment>")
            refreshed = self._global_session_refresher()
            if refreshed.deployment_id != active.deployment_id:
                raise NoActiveDeploymentError("Refreshed RemoteRF Global session belongs to a different deployment.")
            self._global_session = refreshed
            self.username = refreshed.local_username
        return self._global_session.local_username, self._global_session.local_session_token
    
    def create_user(self):
        if self.is_global_session:
            raise OperationDeniedError("Global-selected deployments do not permit local password account creation.")
        response = rpc_client(function_name="ACC:create_user", args={"un":map_arg(self.username), "pw":map_arg(self.password), "em":map_arg(self.email), "ec":map_arg(self.enrollment_code)})
        if 'UC' in response.results:
            print(f'User {unmap_arg(response.results["UC"])} successfully created.')
            return True
        elif 'UE' in response.results:
            print(f'Error: {unmap_arg(response.results["UE"])}')
            return False
    
    def login_user(self):
        username, credential_secret = self._rpc_credentials()
        response = rpc_client(function_name="ACC:login", args={"un":map_arg(username), "pw":map_arg(credential_secret)})
        if 'UC' in response.results:
            print(f'User {unmap_arg(response.results["UC"])} successful login.')
            return True
        elif 'UE' in response.results:
            print(f'Error: {unmap_arg(response.results["UE"])}')
            return False
    
    def reserve_device(self, device_id:int, start_time:datetime, end_time:datetime):
        username, credential_secret = self._rpc_credentials()
        response = rpc_client(function_name="ACC:reserve_device", args={"un":map_arg(username), "pw":map_arg(credential_secret), "dd":map_arg(device_id), "st":map_arg(int(start_time.timestamp())), "et":map_arg(int(end_time.timestamp()))})

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
        username, credential_secret = self._rpc_credentials()
        return rpc_client(function_name='ACC:get_res', args={"un":map_arg(username), "pw":map_arg(credential_secret)})
    
    def get_devices(self):
        username, credential_secret = self._rpc_credentials()
        return rpc_client(function_name='ACC:get_dev', args={"un":map_arg(username), "pw":map_arg(credential_secret)})
    
    def cancel_reservation(self, res_id:int):
        username, credential_secret = self._rpc_credentials()
        return rpc_client(function_name='ACC:cancel_res', args={"un":map_arg(username), "pw":map_arg(credential_secret), "res_id":map_arg(res_id)})
    
    def get_perms(self):
        username, credential_secret = self._rpc_credentials()
        return rpc_client(function_name='ACC:get_perms', args={"un":map_arg(username), "pw":map_arg(credential_secret)})
    
    def set_enroll(self):
        if self.is_global_session:
            raise OperationDeniedError("Global-selected deployments do not permit local enrollment changes.")
        username, credential_secret = self._rpc_credentials()
        return rpc_client(function_name='ACC:set_enroll', args={"un":map_arg(username), "pw":map_arg(credential_secret), "ec":map_arg(self.enrollment_code)})
    
    
