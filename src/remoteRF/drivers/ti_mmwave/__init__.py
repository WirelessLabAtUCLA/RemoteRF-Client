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

from importlib import import_module as _import_module

from . import ti_mmwave_remote as adi
from .ti_mmwave_remote import TiMmWave

__all__ = ["adi", "TiMmWave"]
ti_mmwave = _import_module("remoteRF.drivers.support.ti_mmwave")
__all__.append("ti_mmwave")
if hasattr(ti_mmwave, "bind_client_class"):
    ti_mmwave.bind_client_class("TiMmWave", TiMmWave)
for _name in getattr(ti_mmwave, "__all__", ()):
    globals().setdefault(_name, getattr(ti_mmwave, _name))
    if _name not in __all__:
        __all__.append(_name)

