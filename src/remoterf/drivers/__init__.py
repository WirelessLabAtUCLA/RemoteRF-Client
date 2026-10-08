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

"""Driver discovery APIs with lazy network client initialization."""

__all__ = [
    "fetch_idl",
    "fetch_schema_v2",
    "install_driver",
    "install_driver_if_stale",
    "ensure_driver",
]


def __getattr__(name):
    if name in {
        "fetch_idl",
        "install_driver",
        "install_driver_if_stale",
        "ensure_driver",
    }:
        from . import dynamic_device

        return getattr(dynamic_device, name)
    if name == "fetch_schema_v2":
        from .dynamic_v2 import fetch_schema_v2

        return fetch_schema_v2
    raise AttributeError(name)
