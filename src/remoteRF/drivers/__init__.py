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
