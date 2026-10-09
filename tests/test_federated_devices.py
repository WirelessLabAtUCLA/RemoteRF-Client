"""Gate H: the stock Client reaches federated devices only through its HOME."""

import base64
from unittest.mock import Mock

import pytest
from remoterf_federation_core import local_capabilities

from remoterf.common.grpc import grpc_pb2
from remoterf.common.utils import map_arg, unmap_arg
import importlib
import importlib.util
from remoterf.deployment.backend import HttpsJsonAccountBackend
from remoterf.deployment.http import AccountBackendError

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
    # test_dynamic_device_codegen installs a fake remoterf.core.grpc_client in
    # sys.modules at import; load the real module from its file instead.
    import remoterf.core as core
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "remoterf.core.grpc_client_real", Path(core.__file__).with_name("grpc_client.py")
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
    from remoterf.core import direct_path
    monkeypatch.setattr(direct_path, "global_path", lambda ref, deployment_id=None: None)
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

    # A setter's empty frame decodes to an empty results map.
    transport.request.return_value = {"provenance": "destination", "response_b64": ""}
    empty = grpc_client.rpc_client(function_name="pluto:rx_lo:SET", args={"rx_lo": map_arg(1), "a": map_arg(REF)})
    assert dict(empty.results) == {}
    transport.request.return_value = {
        "provenance": "destination",
        "response_b64": base64.b64encode(answer.SerializeToString()).decode(),
    }

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


def test_device_direct_posts_bearer_or_token_body():
    backend, transport = _backend()
    transport.request.return_value = {"provenance": "destination", "deployment_id": DEST, "ice": "ANSWER"}
    assert backend.device_direct(REF, None, "OFFER") == "ANSWER"
    assert transport.request.call_args.args == ("POST", "/v2/deployment/devices/direct")
    kwargs = transport.request.call_args.kwargs
    assert kwargs["data"] == {"device_id": REF, "ice": "OFFER"} and kwargs["access"] == "acc"

    rfg = "rfg_" + "A" * 43
    backend.device_direct(rfg, DEST, "OFFER")
    kwargs = transport.request.call_args.kwargs
    assert kwargs["data"] == {"token": rfg, "deployment_id": DEST, "ice": "OFFER"} and "access" not in kwargs
    backend.device_direct(rfg, None, "OFFER")
    assert transport.request.call_args.kwargs["data"] == {"token": rfg, "ice": "OFFER"}

    for bad in ({"provenance": "home", "ice": "x"}, {"provenance": "destination"}, {"provenance": "destination", "ice": 1}):
        transport.request.return_value = bad
        with pytest.raises(AccountBackendError, match="Invalid home device response"):
            backend.device_direct(REF, None, "OFFER")
    with pytest.raises(AccountBackendError, match="invalid_device_id"):
        backend.device_direct("12", None, "OFFER")


def test_concurrent_access_refreshes_once():
    import threading
    import time

    backend, transport = _backend("ACC:refresh_session")
    backend.credentials = {"access_token": "old", "refresh_token": "r1", "access_expires_at": 0, "subject_id": HOME}
    spent = []

    def refresh(method, path, **kwargs):
        spent.append(kwargs["data"]["refresh_token"])
        time.sleep(0.2)  # both threads see the expired token meanwhile
        return {"deployment_id": HOME, "subject_id": HOME, "username": "jdoe", "access_token": "new",
                "refresh_token": "r2", "token_type": "Bearer",
                "access_expires_at": 10**12, "refresh_expires_at": 10**12}

    transport.request.side_effect = refresh
    got = []
    threads = [threading.Thread(target=lambda: got.append(backend._access())) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert spent == ["r1"] and got == ["new", "new"]  # a replayed refresh token would end the session


def test_https_keeps_an_idle_connection_for_a_minute(monkeypatch):
    from remoterf.deployment import http

    seen = {}
    monkeypatch.setattr(http.httpx, "Client", lambda **kwargs: seen.update(kwargs) or Mock())
    http.JsonTransport("https://home.example")
    limits = seen["limits"]
    assert (limits.keepalive_expiry, limits.max_connections, limits.max_keepalive_connections) == (60, 100, 20)
