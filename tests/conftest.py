"""Every test runs against its own HOME.

Client state (saved homes, the selected target, credentials, the direct
profile) lives under ``~/.config/remoterf-client``. A test must never read or
write the developer's real configuration, so each test gets a fresh HOME.
"""

import os
import pathlib

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("REMOTERF_ADDR", raising=False)
    monkeypatch.delenv("REMOTERF_CA_CERT", raising=False)
    os.environ.pop("REMOTERF_TLS_SERVER_NAME", None)
    yield home
