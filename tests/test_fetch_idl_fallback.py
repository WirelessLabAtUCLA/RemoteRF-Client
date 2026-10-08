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
