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

"""Rescue for an upgrade from remoterf 2.0.7-2.0.13 on Windows and macOS.

Those releases installed the package in a lowercase ``remoterf`` folder. When
that folder survives an upgrade (it holds the generated drivers pip does not
know about), the new files land inside it, and on a case-insensitive disk
``import remoteRF`` no longer finds the package. Python only reaches this
module then: a correctly named ``remoteRF`` folder always takes precedence.
"""

import importlib.util
import os
import sys

_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "remoterf")
_SPEC = importlib.util.spec_from_file_location(
    __name__, os.path.join(_FOLDER, "__init__.py"), submodule_search_locations=[_FOLDER]
)
if _SPEC is None or not os.path.isfile(os.path.join(_FOLDER, "__init__.py")):
    raise ImportError("the remoteRF package folder is missing; reinstall with: pip install --force-reinstall remoterf")
_MODULE = importlib.util.module_from_spec(_SPEC)
# The import system returns whatever this name maps to once this module has
# run: the package itself, so ``remoteRF.<submodule>`` resolves inside it.
sys.modules[__name__] = _MODULE
_SPEC.loader.exec_module(_MODULE)
