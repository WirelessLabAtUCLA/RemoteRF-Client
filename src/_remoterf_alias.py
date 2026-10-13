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

"""``import remoteRF``, the package's name before 2.2.0, still works.

_remoterf_alias.pth imports this module at startup. It maps ``remoteRF`` and
every ``remoteRF.*`` to the same module objects as ``remoterf``, warning once.
Until something imports ``remoteRF``, it imports nothing (no remoterf, grpc, numpy).
A ``remoteRF.py`` file cannot do this: on a case-insensitive disk it would
collide with the ``remoterf.py`` rescue module.
"""

import importlib
import importlib.machinery
import sys
import warnings

_OLD, _NEW = "remoteRF", "remoterf"


class _Alias:  # a meta path finder and its loader
    warned = False

    def find_spec(self, name, path=None, target=None):
        if name == _OLD or name.startswith(_OLD + "."):
            return importlib.machinery.ModuleSpec(name, self)
        return None

    def create_module(self, spec):
        if not _Alias.warned:
            _Alias.warned = True
            # FutureWarning: shown by default (a DeprecationWarning is hidden outside __main__).
            # stacklevel skips the import machinery, so it points at the importing line.
            warnings.warn(
                "'remoteRF' was renamed 'remoterf' in 2.2.0; import remoterf instead",
                FutureWarning, stacklevel=2,
            )
        module = importlib.import_module(_NEW + spec.name[len(_OLD):])
        spec.loader_state = module.__spec__
        return module

    def exec_module(self, module):
        module.__spec__ = module.__spec__.loader_state  # loading set it to the alias's spec


# First: on an upgraded machine a surviving remoteRF folder would load a second copy.
if not any(isinstance(finder, _Alias) for finder in sys.meta_path):
    sys.meta_path.insert(0, _Alias())
