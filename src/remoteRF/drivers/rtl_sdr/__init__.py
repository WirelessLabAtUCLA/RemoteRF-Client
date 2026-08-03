from importlib import import_module as _import_module

from . import rtl_sdr_remote as adi
from .rtl_sdr_remote import RtlSdr

__all__ = ["adi", "RtlSdr"]
