import pytest

from remoterf.common.grpc import grpc_pb2
from remoterf.common.utils import map_arg, unmap_arg
from remoterf.core import direct_path, dynamic_v2_transport, grpc_client
from remoterf.core.v2_errors import RemoteRFPolicyError
from remoterf.deployment.http import AccountBackendError

REF = "00000000-0000-4000-8000-000000000001:7"
WHOLE = grpc_pb2.GenericRPCResponse(results={"rx": map_arg(bytes(range(256)) * 4000)}).SerializeToString()


class Lab:
    def __init__(self, pages=True):
        self.pages = pages
        self.requests = []

    def device_rpc(self, ref, raw):
        request = grpc_pb2.GenericRPCRequest.FromString(raw)
        self.requests.append(request)
        if request.function_name != "Federation:page:CALL2":
            raise AccountBackendError("response_too_large", provenance="destination")
        if not self.pages:  # an older lab: not a device function
            raise AccountBackendError("device_unavailable", provenance="destination")
        offset = unmap_arg(request.args["arg1"])
        return grpc_pb2.GenericRPCResponse(results={
            "page": map_arg(WHOLE[offset:offset + 600_000]),
            "size": map_arg(len(WHOLE)),
            "nonce": map_arg("n1"),
        }).SerializeToString()


@pytest.fixture
def lab(monkeypatch):
    lab = Lab()
    monkeypatch.setattr(direct_path, "wants", lambda function_name: False)
    monkeypatch.setattr(grpc_client, "federated_backend", lambda: lab)
    return lab


def test_an_oversized_reply_is_fetched_in_pages(lab):
    response = grpc_client._federated_call(REF, "Rtl_sdr:read_samples:CALL1", {"a": map_arg(REF)})
    assert response.SerializeToString() == WHOLE
    pages = lab.requests[1:]
    assert [unmap_arg(r.args["arg1"]) for r in pages] == [0, 600_000]
    assert [unmap_arg(r.args["arg2"]) for r in pages] == ["", "n1"]


def test_an_older_lab_gets_a_clear_error(lab):
    lab.pages = False
    with pytest.raises(RuntimeError, match=r"response_too_large \[destination\].*768 KiB"):
        grpc_client._federated_call(REF, "Rtl_sdr:read_samples:CALL1", {"a": map_arg(REF)})
    assert len(lab.requests) == 2


def test_dynamic_v2_reports_it_as_a_policy_error(monkeypatch):
    def refused(**_):
        raise RuntimeError("response_too_large [destination]: over the relay limit")

    monkeypatch.setattr(grpc_client, "rpc_client", refused)
    stub = dynamic_v2_transport._DevicePlaneStub("token")
    with pytest.raises(RemoteRFPolicyError, match="response_too_large"):
        stub.Invoke(grpc_pb2.InvokeRequest())
