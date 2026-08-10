from importlib import import_module as _import_module

from . import hackrf_remote as adi
from .hackrf_remote import HackRF

__all__ = ["adi", "HackRF"]
