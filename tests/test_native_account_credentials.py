from unittest import mock
from remoteRF.common.utils import unmap_arg
from remoteRF.core.grpc_acc import RemoteRFAccount


def test_direct_credentials_remain_unchanged_for_normal_requests():
    account = RemoteRFAccount(username="local-user", password="local-password")
    with mock.patch("remoteRF.core.grpc_acc.rpc_client") as rpc:
        account.get_devices()
    args = rpc.call_args.kwargs["args"]
    assert unmap_arg(args["un"]) == "local-user"
    assert unmap_arg(args["pw"]) == "local-password"
