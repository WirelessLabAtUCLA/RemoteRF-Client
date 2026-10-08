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

"""Core client APIs.

Keep network-configured modules lazy so protocol helpers can be imported for
explicit channels (for example localhost integration tests) before the legacy
global RemoteRF client has been configured.
"""

__all__ = ["rpc_client", "RemoteRFAccount"]


def __getattr__(name):
    if name == "rpc_client":
        from .grpc_client import rpc_client

        return rpc_client
    if name == "RemoteRFAccount":
        from .grpc_acc import RemoteRFAccount

        return RemoteRFAccount
    raise AttributeError(name)
