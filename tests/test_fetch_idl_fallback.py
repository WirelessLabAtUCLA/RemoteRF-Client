"""A schema fetch takes the v1 device plane when v2 cannot serve the token."""

from remoterf.common.grpc import grpc_pb2
from remoterf.common.utils import map_arg
from remoterf.core import direct_path, v2_errors
from remoterf.drivers import dynamic_device


def _v1(monkeypatch, extra=None):
    calls = []

    def fake_rpc(*, function_name, args, connection=None):
        calls.append(function_name)
        results = {"schema": map_arg("{\"device_type\": \"sim_sdr\"}"), **(extra or {})}
        return grpc_pb2.GenericRPCResponse(results=results)

    import remoterf.core.grpc_client as grpc_client

    monkeypatch.setattr(grpc_client, "rpc_client", fake_rpc)
    return calls


def test_a_reservation_error_from_v2_falls_back_to_v1_and_marks_the_partner(monkeypatch):
    def refuse(token):
        raise v2_errors.RemoteRFReservationError("invalid token or inactive reservation")

    import remoterf.drivers.dynamic_v2 as dynamic_v2

    monkeypatch.setattr(dynamic_v2, "fetch_schema_v2", refuse)
    direct_path._partner_owners.clear()
    calls = _v1(monkeypatch, {"federated": map_arg("owner-uuid")})
    assert dynamic_device.fetch_idl(token="tok", prefer_v2=True) == {"device_type": "sim_sdr", "federated": True}
    assert calls == ["IDL:get_drivers"] and direct_path.partner_of("tok") == "owner-uuid"


def test_a_protocol_error_from_v2_falls_back_too(monkeypatch):
    def refuse(token):
        raise v2_errors.RemoteRFProtocolError("partner devices use the v1 device plane")

    import remoterf.drivers.dynamic_v2 as dynamic_v2

    monkeypatch.setattr(dynamic_v2, "fetch_schema_v2", refuse)
    calls = _v1(monkeypatch)
    assert dynamic_device.fetch_idl(token="tok2", prefer_v2=True)["device_type"] == "sim_sdr"
    assert calls == ["IDL:get_drivers"]


def _schema_rpc(monkeypatch, schema_hash):
    import json

    import remoterf.core.grpc_client as grpc_client

    calls = []

    def fake_rpc(*, function_name, args, connection=None):
        calls.append(function_name)
        schema = {"device_type": "sim_sdr", "schema_hash": schema_hash}
        return grpc_pb2.GenericRPCResponse(results={"schema": map_arg(json.dumps(schema))})

    monkeypatch.setattr(grpc_client, "rpc_client", fake_rpc)
    dynamic_device._verified.clear()
    return calls


def test_a_matching_v1_driver_costs_one_rpc_and_none_again_in_this_process(monkeypatch):
    import remoterf.drivers.dynamic_v2 as dynamic_v2

    def probe(token):
        raise AssertionError("no v2 probe for a matching v1 driver")

    monkeypatch.setattr(dynamic_v2, "fetch_schema_v2", probe)
    calls = _schema_rpc(monkeypatch, "sha256:v1")
    assert dynamic_device.install_driver_if_stale(token="tok3", current_hash="sha256:v1") is False
    assert calls == ["IDL:get_drivers"]
    assert dynamic_device.install_driver_if_stale(token="tok3", current_hash="sha256:v1") is False
    assert calls == ["IDL:get_drivers"]  # re-created driver: no schema RPC
    dynamic_device.install_driver_if_stale(token="tok4", current_hash="sha256:v1")
    assert len(calls) == 2  # another reservation is checked again


def test_a_v1_hash_that_differs_still_lets_v2_decide_and_reinstall(monkeypatch, tmp_path):
    import remoterf.drivers.dynamic_v2 as dynamic_v2

    v2 = {"schema_version": "2.0", "device_type": "sim_sdr", "schema_hash": "sha256:v2"}
    probes = []
    monkeypatch.setattr(dynamic_v2, "fetch_schema_v2", lambda token: probes.append(token) or v2)
    written = []
    monkeypatch.setattr(dynamic_device, "_write_driver_files", lambda schema, **_: written.append(schema) or tmp_path)
    calls = _schema_rpc(monkeypatch, "sha256:v1")

    # A v2 driver whose Server reported a mismatch: v2 still matches, never memoized.
    for _ in range(2):
        assert dynamic_device.install_driver_if_stale(token="tok5", current_hash="sha256:v2") is False
    assert probes == ["tok5", "tok5"] and calls == ["IDL:get_drivers"] * 2 and written == []

    # A stale v1 driver: reinstalled from what v2 says, exactly as before.
    assert dynamic_device.install_driver_if_stale(token="tok5", current_hash="sha256:old") is True
    assert written == [v2]


def test_a_read_only_install_keeps_its_driver(monkeypatch, capsys):
    import remoterf.drivers.dynamic_v2 as dynamic_v2

    def v1_only(token):
        raise v2_errors.RemoteRFProtocolError("partner devices use the v1 device plane")

    monkeypatch.setattr(dynamic_v2, "fetch_schema_v2", v1_only)
    calls = _schema_rpc(monkeypatch, "sha256:new")

    def read_only(schema, **_):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(dynamic_device, "_write_driver_files", read_only)
    assert dynamic_device.install_driver_if_stale(token="tok5", current_hash="sha256:old") is False
    assert "could not be updated" in capsys.readouterr().out
    dynamic_device.install_driver_if_stale(token="tok5", current_hash="sha256:old")
    assert len(calls) == 2  # fetched once (v1, then the v2 probe's fallback), not per device
