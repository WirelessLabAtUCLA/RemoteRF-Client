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
    home: str | None = None  # the saved HOME this profile was activated from
    cert_port: int | None = None  # `-c -a --cert-port`; None means grpc port + 1


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
        address, setting("REMOTERF_TLS_SERVER_NAME") or None, Path(ca).expanduser(),
        home=setting("REMOTERF_PROFILE") or None,
        cert_port=int(setting("REMOTERF_CERT_PORT")) if setting("REMOTERF_CERT_PORT").isdigit() else None,
    )
