from hashlib import sha256
from pathlib import Path

from remoteRF.common.grpc import global_auth_pb2


def test_client_proto_is_byte_identical_to_canonical_server_proto():
    client = Path(__file__).resolve().parents[1] / "src" / "remoteRF" / "common" / "grpc" / "global_auth.proto"
    server = Path(__file__).resolve().parents[2] / "RemoteRF-Server" / "src" / "remoteRF_server" / "common" / "grpc" / "global_auth.proto"
    assert client.read_bytes() == server.read_bytes()
    assert sha256(client.read_bytes()).hexdigest() == "af9df530280251cd3019f3a16e89fae66ba45eeea5f27b066519848a3916e4b9"


def test_generated_binding_exposes_canonical_service_and_field_numbers():
    response = global_auth_pb2.ExchangeAssertionResponse.DESCRIPTOR
    assert global_auth_pb2.DESCRIPTOR.services_by_name["GlobalAuthV1"].full_name == "remote_rf.GlobalAuthV1"
    assert response.fields_by_name["local_username"].number == 1
    assert response.fields_by_name["local_session_token"].number == 2
    assert response.fields_by_name["session_expires_at"].number == 4
    assert response.fields_by_name["deployment_id"].number == 5
    assert response.fields_by_name["error"].number == 8
