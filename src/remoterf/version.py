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

from functools import lru_cache
from importlib.metadata import version, PackageNotFoundError

# What every request says this client is: Global and joined labs refuse a client too old to be safe there.
CLIENT_HEADER = "X-RemoteRF-Client"


@lru_cache(maxsize=None)
def current() -> str:
    try:
        return version("remoterf")
    except PackageNotFoundError:
        return "unknown"

def main():
    try:
        v = version("remoterf")
    except PackageNotFoundError:
        v = "unknown"
    print(v)