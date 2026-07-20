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
