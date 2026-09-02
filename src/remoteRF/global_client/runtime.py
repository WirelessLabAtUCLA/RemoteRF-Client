# Copyright (C) 2026 RemoteRF
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Bridge an active Global profile to ordinary RemoteRF account operations.

This module deliberately opens Global only when an owner-local deployment
session is absent or expired. A still-valid deployment session continues to
work during a Global outage, and Global credentials are never handed to the
deployment's ordinary RPC client.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .api_client import GlobalApiClient
from .auth_client import AuthenticatedGlobalClient
from .credentials import CredentialStoreMode, GlobalCredentialStore, resolve_secret_store
from .errors import NoActiveDeploymentError
from .local_sessions import GlobalDeploymentSession, LocalSessionStore
from .profile import default_config_root, load_global_profile
from .session_manager import GlobalSessionManager
from .state import load_state


def get_active_global_session(*, config_root: Optional[Path] = None) -> GlobalDeploymentSession:
    """Return a valid owner-local session, refreshing it only when necessary."""
    root = config_root or default_config_root()
    profile = load_global_profile(root)
    if profile is None:
        raise NoActiveDeploymentError("No active RemoteRF Global deployment. Run: remoterf use <deployment>")

    state = load_state(root)
    force_file = state.credential_store_mode == CredentialStoreMode.FILE.value
    store = resolve_secret_store(config_root=root, force_file=force_file, warn=lambda *_: None)
    sessions = LocalSessionStore(store)
    cached = sessions.load(profile.deployment_id)
    if cached is not None and not cached.is_expired() and cached.tls_server_name == profile.tls_server_name:
        return cached

    api = GlobalApiClient(state.global_base_url)
    try:
        manager = GlobalSessionManager(
            config_root=root,
            api=AuthenticatedGlobalClient(api, GlobalCredentialStore(store)),
            local_sessions=sessions,
        )
        result = manager.use_deployment(profile.deployment_slug, force_reauth=True)
    finally:
        api.close()

    if result.session.deployment_id != profile.deployment_id:
        raise NoActiveDeploymentError("The selected RemoteRF Global deployment identity changed; select it again.")
    return result.session
