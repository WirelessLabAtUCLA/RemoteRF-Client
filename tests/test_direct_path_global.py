"""A Global device ref or rfg_ token gets a direct path to its lab, signaled
through Global; the relay carries every call the path cannot."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from remoterf.common.grpc import grpc_pb2
from remoterf.common.utils import map_arg, unmap_arg
from remoterf.core import direct_path, grpc_client
from remoterf.deployment.http import AccountBackendError

from test_direct_path_partner import FakePath

DEST = "11111111-1111-4111-8111-111111111111"
REF = f"{DEST}:0"
RFG = "rfg_" + "A" * 43
PIN = {"ufrag": "u", "pwd": "p", "candidates": [], "server_name": "lab.global.example", "ca_pem": "PEM"}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    direct_path.stop()
    FakePath.instances.clear()
    monkeypatch.setenv("REMOTERF_DIRECT", "1")
    monkeypatch.setattr(direct_path, "DirectPath", FakePath)
    relay = Mock()
    answer = grpc_pb2.GenericRPCResponse(results={"via": map_arg("relay")})
    relay.device_rpc.return_value = relay.device_token_rpc.return_value = answer.SerializeToString()
    relay.device_direct.return_value = json.dumps(PIN)
    monkeypatch.setattr(grpc_client, "_federated_backend", relay)
    monkeypatch.setattr(grpc_client, "_token_backend_cache", relay)
    yield relay
    direct_path.stop()


def test_a_global_ref_device_call_takes_the_direct_path_not_the_relay(fresh):
    response = grpc_client.rpc_client(function_name="pluto:rx_lo:GET", args={"a": map_arg(REF)})
    assert unmap_arg(response.results["echo"]) == "pluto:rx_lo:GET"
    path = FakePath.instances[0]
    assert path.calls == ["pluto:rx_lo:GET"]
    assert (path.server_name, path.ca_pem, path.memo) == ("", b"", DEST)
    fresh.device_rpc.assert_not_called()
    grpc_client.rpc_client(function_name="pluto:rx_lo:GET", args={"a": map_arg(REF)})
    assert len(FakePath.instances) == 1  # one path per ref, kept


def test_a_path_error_sends_that_call_to_the_relay_once(fresh, monkeypatch):
    class Broken(FakePath):
        def call(self, function_name, args):
            self.calls.append(function_name)
            raise direct_path.PathError("gone")

    monkeypatch.setattr(direct_path, "DirectPath", Broken)
    response = grpc_client.rpc_client(function_name="pluto:rx_lo:GET", args={"a": map_arg(REF)})
    assert unmap_arg(response.results["via"]) == "relay"
    assert FakePath.instances[0].calls == ["pluto:rx_lo:GET"]
    fresh.device_rpc.assert_called_once()


def test_a_path_refused_for_an_ended_reservation_lets_the_relay_try_a_newer_one(fresh, monkeypatch):
    class Refusing(FakePath):
        reason = "the reservation has ended"

        def call(self, function_name, args):
            self.calls.append(function_name)
            return grpc_pb2.GenericRPCResponse(results={"a": map_arg(self.reason)})

    monkeypatch.setattr(direct_path, "DirectPath", Refusing)
    response = grpc_client.rpc_client(function_name="pluto:rx_lo:GET", args={"a": map_arg(REF)})
    assert unmap_arg(response.results["via"]) == "relay"
    fresh.device_rpc.assert_called_once()
    Refusing.reason = "device error"  # a call that ran: never re-sent
    with pytest.raises(RuntimeError, match="device error"):
        grpc_client.rpc_client(function_name="pluto:rx_lo:GET", args={"a": map_arg(REF)})
    fresh.device_rpc.assert_called_once()


def test_non_device_calls_and_REMOTERF_DIRECT_0_stay_on_the_relay(fresh, monkeypatch):
    grpc_client.rpc_client(function_name="ACC:get_dev", args={"a": map_arg(REF)})
    assert FakePath.instances == []
    monkeypatch.setenv("REMOTERF_DIRECT", "0")
    response = grpc_client.rpc_client(function_name="pluto:rx_lo:GET", args={"a": map_arg(REF)})
    assert unmap_arg(response.results["via"]) == "relay"
    assert FakePath.instances == [] and fresh.device_rpc.call_count == 2


def test_an_rfg_token_signals_with_the_token_and_no_bearer(fresh, monkeypatch):
    monkeypatch.setattr(grpc_client, "federated_backend", Mock(side_effect=AssertionError("no login")))
    route = grpc_client.DeviceRoute(RFG, "global", deployment_id=DEST)
    grpc_client._federated_call(route, "pluto:rx_lo:GET", {"a": map_arg("")})
    path = FakePath.instances[0]
    assert path.memo == DEST
    assert path.offer({"id": "x"}) == PIN
    fresh.device_direct.assert_called_once_with(RFG, DEST, json.dumps({"id": "x"}))


@pytest.mark.parametrize("code, unavailable", [
    ("not_found", True), ("action_unavailable", True), ("direct_path_unavailable", True),
    ("no_active_reservation", False), ("rate_limited", False),
])
def test_offer_outcomes_classify_unavailable_vs_retry(fresh, code, unavailable):
    fresh.device_direct.side_effect = AccountBackendError(code, provenance="home")
    with pytest.raises(direct_path.PathError) as caught:
        direct_path._global_offer(REF, None)({"id": "x"})
    assert isinstance(caught.value, direct_path.PathUnavailable) is unavailable


def test_a_lab_answer_without_its_pin_is_unavailable(fresh):
    fresh.device_direct.return_value = json.dumps({"ufrag": "u", "pwd": "p", "candidates": []})
    with pytest.raises(direct_path.PathUnavailable, match="predates"):
        direct_path._global_offer(REF, None)({"id": "x"})


def test_the_login_backend_is_built_once_when_a_path_thread_asks_too(monkeypatch):
    # Two backends would each refresh the stored session; Global revokes a replayed refresh token.
    import threading
    import time

    monkeypatch.setattr(grpc_client, "_federated_backend", None)
    built = []

    def selected_backend():
        time.sleep(0.2)
        backend = Mock(capabilities={"account_transport": "https-json"})
        built.append(backend)
        return backend

    monkeypatch.setattr("remoterf.deployment.backend.selected_backend", selected_backend)
    got = []
    threads = [threading.Thread(target=lambda: got.append(grpc_client.federated_backend())) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert len(built) == 1 and got == built * 2
