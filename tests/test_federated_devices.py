"""Gate H: the stock Client reaches federated devices only through its HOME."""

import base64
from unittest.mock import Mock

import pytest
from remoterf_federation_core import local_capabilities

from remoteRF.common.grpc import grpc_pb2
from remoteRF.common.utils import map_arg, unmap_arg
import importlib
import importlib.util
from remoteRF.deployment.backend import HttpsJsonAccountBackend
from remoteRF.deployment.http import AccountBackendError

HOME = "550e8400-e29b-41d4-a716-446655440000"
DEST = "11111111-1111-4111-8111-111111111111"
REF = f"{DEST}:12"


def _caps(*ops):
    c = local_capabilities(deployment_id=HOME)
    c.update(account_transport="https-json", account_api_base="/v2/deployment")
    c["supported_operations"] = sorted(set(c["supported_operations"]) | set(ops))
    return c


def _backend(*ops):
    transport = Mock()
    transport.origin = "https://home.example"
    transport.request.return_value = _caps(*ops)
    backend = HttpsJsonAccountBackend(transport.origin, transport=transport, store=Mock())
    backend.credentials = {"access_token": "acc", "access_expires_at": 10**12, "subject_id": HOME}
    return backend, transport


def test_device_rpc_relays_serialized_frames_through_the_home(monkeypatch):
    # test_dynamic_device_codegen installs a fake remoteRF.core.grpc_client in
    # sys.modules at import; load the real module from its file instead.
    import remoteRF.core as core
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "remoteRF.core.grpc_client_real", Path(core.__file__).with_name("grpc_client.py")
    )
    grpc_client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(grpc_client)
    backend, transport = _backend("DEV:rpc")
    answer = grpc_pb2.GenericRPCResponse()
    answer.results["tx_lo"].CopyFrom(map_arg(2400000000))
    transport.request.return_value = {
        "provenance": "destination",
        "response_b64": base64.b64encode(answer.SerializeToString()).decode(),
    }
    grpc_client.bind_federated_backend(backend)
    monkeypatch.setattr(grpc_client, "get_active_connection", Mock(side_effect=AssertionError("no direct transport")))

    response = grpc_client.rpc_client(function_name="pluto:tx_lo:GET", args={"a": map_arg(REF)})
    assert unmap_arg(response.results["tx_lo"]) == 2400000000
    method, path = transport.request.call_args.args
    body = transport.request.call_args.kwargs
    assert (method, path) == ("POST", "/v2/deployment/devices/rpc")
    assert body["data"]["device_id"] == REF
    sent = grpc_pb2.GenericRPCRequest()
    sent.ParseFromString(base64.b64decode(body["data"]["request_b64"]))
    assert sent.function_name == "pluto:tx_lo:GET" and unmap_arg(sent.args["a"]) == REF

    # IDL lookups route by the reference in token/device_id, too.
    grpc_client.rpc_client(function_name="IDL:get_drivers", args={"device_id": map_arg(REF)})
    assert transport.request.call_args.kwargs["data"]["device_id"] == REF

    # A native bearer token never goes near the HOME.
    with pytest.raises(AssertionError, match="no direct transport"):
        grpc_client.rpc_client(function_name="pluto:tx_lo:GET", args={"a": map_arg("Zm9vYmFy")})

    # Destination refusals surface with provenance, as drivers expect RuntimeError.
    transport.request.side_effect = AccountBackendError("no_active_reservation", provenance="destination")
    with pytest.raises(RuntimeError, match=r"no_active_reservation \[destination\]"):
        grpc_client.rpc_client(function_name="pluto:tx_lo:GET", args={"a": map_arg(REF)})
    grpc_client.bind_federated_backend(None)


def test_reserve_and_cancel_use_home_references_and_verify_provenance():
    backend, transport = _backend("ACC:reserve_device", "ACC:cancel_res", "ACC:get_dev")
    transport.request.return_value = {
        "status": "reserved", "provenance": "destination",
        "reservation": {"reservation_id": f"{DEST}:9", "device_id": REF, "deployment_id": DEST,
                        "deployment_name": "Lab", "start_time": 1000, "end_time": 2000},
    }
    assert backend.reserve(REF, 1000, 2000)["device_id"] == REF
    assert transport.request.call_args.kwargs["data"] == {"device_id": REF, "start_time": 1000, "end_time": 2000}
    with pytest.raises(AccountBackendError, match="invalid_device_id"):
        backend.reserve("12", 1000, 2000)

    transport.request.return_value = {"provenance": "home", "reservation": {}}
    with pytest.raises(AccountBackendError, match="Invalid home reservation response"):
        backend.reserve(REF, 1000, 2000)

    transport.request.return_value = {"provenance": "destination", "cancelled": True}
    assert backend.cancel(f"{DEST}:9") is True
    transport.request.return_value = {"provenance": "destination", "cancelled": False}
    with pytest.raises(AccountBackendError):
        backend.cancel(f"{DEST}:9")

    unsupported, _ = _backend()
    with pytest.raises(AccountBackendError, match="Operation unavailable"):
        unsupported.device_rpc(REF, b"x")
