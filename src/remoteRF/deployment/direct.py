"""The protected direct configuration reader, independent of Global profiles."""

from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import dotenv_values


@dataclass(frozen=True)
class DirectConnectionProfile:
    grpc_endpoint: str
    tls_server_name: str | None
    ca_path: Path
    mode: str = "direct"


ConnectionProfile = DirectConnectionProfile


def resolve_active_profile():
    values = dotenv_values(Path.home() / ".config/remoterf-client/.env")

    def setting(name):
        return (
            (os.getenv(name, values.get(name) or "") or "")
            .strip()
            .strip('"')
            .strip("'")
        )

    address, ca = setting("REMOTERF_ADDR"), setting("REMOTERF_CA_CERT")
    if not address or not ca:
        return None
    return DirectConnectionProfile(
        address, setting("REMOTERF_TLS_SERVER_NAME") or None, Path(ca).expanduser()
    )
