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

