"""Dynamic v2 for a partner lab's device: the HOME names the owner, and the
transport carries each request as a DynamicV2:<Operation>:WIRE device RPC."""

import json

import pytest

from remoteRF.common.grpc import grpc_pb2
from remoteRF.common.utils import map_arg, unmap_arg
from remoteRF.core import direct_path
from remoteRF.core import grpc_client
from remoteRF.core.dynamic_v2_transport import (
    CONTROL_PROTOCOL_VERSION,
    SCHEMA_VERSION,
    STREAMING_PROTOCOL_VERSION,
    DynamicV2Transport,
)
from remoteRF.core.v2_errors import RemoteRFReservationError

SCHEMA = {"schema_version": SCHEMA_VERSION, "schema_hash": "sha256:abc"}


class _Home:
    """The HOME's own v2 service: it refuses a partner's token, naming the owner."""

    def OpenSession(self, request, timeout=None):
        return grpc_pb2.OpenSessionResponse(error=grpc_pb2.ErrorEnvelope(
            code="RemoteRFProtocolError", message="partner devices take Dynamic v2 over the device plane",
            details_json=json.dumps({"federated": "owner-uuid"}),
        ))


def _owner(calls, refuse=None):
    def rpc_client(*, function_name, args, connection=None):
        calls.append((function_name, unmap_arg(args["a"])))
        if refuse:
            raise RuntimeError(refuse)
        operation = function_name.split(":")[1]
        if operation == "OpenSession":
            reply = grpc_pb2.OpenSessionResponse(
                session_id="sess_1", device_handle="dev_1", schema_json=json.dumps(SCHEMA),
                schema_hash=SCHEMA["schema_hash"], capabilities_json="{}",
            )
        elif operation == "SampleFrame":
            frame = grpc_pb2.SampleFrame.FromString(unmap_arg(args["wire"]))
            reply = grpc_pb2.SampleFrame(sequence=frame.sequence, kind=grpc_pb2.SAMPLE_FRAME_RESULT)
        else:
            raise AssertionError(operation)
        return grpc_pb2.GenericRPCResponse(results={"wire": map_arg(reply.SerializeToString())})

    return rpc_client


@pytest.fixture(autouse=True)
def _forget_partners():
    yield
    direct_path._partner_owners.clear()


def test_a_refused_partner_token_opens_over_the_device_plane(monkeypatch):
    calls = []
    monkeypatch.setattr(grpc_client, "rpc_client", _owner(calls))
    transport = DynamicV2Transport(control_stub=_Home(), sample_stub=object())

    opened = transport.open_session("partner-token", SCHEMA["schema_hash"])

    assert opened["session_id"] == "sess_1"
    assert calls == [("DynamicV2:OpenSession:WIRE", "partner-token")]
    # Marked: the token's device calls may now take the direct path to the owner.
    assert direct_path.partner_of("partner-token") == "owner-uuid"
    # Samples ride the same plane, one frame per call.
    frames = [grpc_pb2.SampleFrame(sequence=0), grpc_pb2.SampleFrame(sequence=1)]
    replies = transport._sample_call(frames, operation_timeout_sec=1.0)
    assert [reply.sequence for reply in replies] == [0, 1]
    assert [name for name, _ in calls[1:]] == ["DynamicV2:SampleFrame:WIRE"] * 2


def test_a_known_partner_token_skips_the_home_refusal(monkeypatch):
    calls = []
    monkeypatch.setattr(grpc_client, "rpc_client", _owner(calls))
    direct_path.mark_partner("partner-token", "owner-uuid")
    transport = DynamicV2Transport(control_stub=object(), sample_stub=object())

    assert transport.open_session("partner-token", SCHEMA["schema_hash"])["session_id"] == "sess_1"
    assert calls == [("DynamicV2:OpenSession:WIRE", "partner-token")]


def test_an_owner_refusal_is_a_reservation_error(monkeypatch):
    monkeypatch.setattr(grpc_client, "rpc_client", _owner([], refuse="Partner refused: no_active_reservation"))
    direct_path.mark_partner("partner-token", "owner-uuid")
    transport = DynamicV2Transport(control_stub=object(), sample_stub=object())

    with pytest.raises(RemoteRFReservationError, match="no_active_reservation"):
        transport.open_session("partner-token", SCHEMA["schema_hash"])

