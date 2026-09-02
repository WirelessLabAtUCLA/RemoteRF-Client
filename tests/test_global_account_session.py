from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import pytest

from remoteRF.common.utils import unmap_arg
from remoteRF.core.grpc_acc import RemoteRFAccount
from remoteRF.global_client.errors import NoActiveDeploymentError, OperationDeniedError
from remoteRF.global_client.local_sessions import GlobalDeploymentSession
from remoteRF.global_client.profile import GlobalConnectionProfile


def _session(*, deployment_id="dep-ucla", token="owner-local-session"):
    return GlobalDeploymentSession(
        deployment_id=deployment_id,
        local_username="global-test-user",
        local_session_token=token,
        local_session_expiration=datetime.now(timezone.utc) + timedelta(minutes=10),
        tls_server_name="ucla.global.example",
        obtained_at=datetime.now(timezone.utc),
    )


def _profile(tmp_path, deployment_id="dep-ucla"):
    ca = tmp_path / "ca.crt"
    ca.write_text("test-ca", encoding="utf-8")
    return GlobalConnectionProfile(
        deployment_id=deployment_id,
        deployment_slug="ucla",
        display_name="UCLA",
        grpc_endpoint="ucla.global.example:61005",
        tls_server_name="ucla.global.example",
        ca_path=ca,
    )


def test_direct_credentials_remain_unchanged_for_normal_requests():
    account = RemoteRFAccount(username="local-user", password="local-password")
    with mock.patch("remoteRF.core.grpc_acc.rpc_client") as rpc:
        account.get_devices()
    args = rpc.call_args.kwargs["args"]
    assert unmap_arg(args["un"]) == "local-user"
    assert unmap_arg(args["pw"]) == "local-password"


def test_global_session_is_sent_only_as_existing_local_rpc_credential(tmp_path):
    account = RemoteRFAccount()
    account.use_global_session(_session(), refresher=_session)
    with mock.patch("remoteRF.core.grpc_acc.resolve_active_profile", return_value=_profile(tmp_path)), mock.patch(
        "remoteRF.core.grpc_acc.rpc_client"
    ) as rpc:
        account.get_devices()
    args = rpc.call_args.kwargs["args"]
    assert unmap_arg(args["un"]) == "global-test-user"
    assert unmap_arg(args["pw"]) == "owner-local-session"
    assert account.password is None


def test_expired_global_session_is_refreshed_before_safe_request_once(tmp_path):
    expired = _session(token="expired-token")
    expired = GlobalDeploymentSession(
        **{**expired.__dict__, "local_session_expiration": datetime.now(timezone.utc) - timedelta(seconds=1)}
    )
    refreshed = _session(token="fresh-owner-local-session")
    refresher = mock.Mock(return_value=refreshed)
    account = RemoteRFAccount()
    account.use_global_session(expired, refresher=refresher)
    with mock.patch("remoteRF.core.grpc_acc.resolve_active_profile", return_value=_profile(tmp_path)), mock.patch(
        "remoteRF.core.grpc_acc.rpc_client"
    ) as rpc:
        account.get_devices()
    assert refresher.call_count == 1
    assert unmap_arg(rpc.call_args.kwargs["args"]["pw"]) == "fresh-owner-local-session"


def test_global_session_cannot_be_used_after_switching_deployments(tmp_path):
    account = RemoteRFAccount()
    account.use_global_session(_session(), refresher=_session)
    with mock.patch("remoteRF.core.grpc_acc.resolve_active_profile", return_value=_profile(tmp_path, "dep-other")):
        with pytest.raises(NoActiveDeploymentError):
            account.get_devices()


def test_global_mode_does_not_offer_local_password_account_creation(tmp_path):
    account = RemoteRFAccount()
    account.use_global_session(_session(), refresher=_session)
    with pytest.raises(OperationDeniedError):
        account.create_user()
