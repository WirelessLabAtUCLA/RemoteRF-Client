from uuid import uuid4
from pathlib import Path
from unittest.mock import patch, Mock
import json
import grpc
import httpx
import pytest
from remoterf_federation_core import local_capabilities, canonical_json, ValidationError
from remoteRF.deployment.backend import (
    LegacyGrpcAccountBackend,
    HttpsJsonAccountBackend,
)
from remoteRF.deployment.http import JsonTransport, AccountBackendError
from remoterf_federation_core import (
    MAX_HOME_PERMISSIONS_HTTP_RESPONSE_BYTES,
    PERMISSIONS_CLIENT_OVERALL_TIMEOUT_SECONDS,
)
from remoteRF.deployment.state import (
    CredentialStore,
    select_target,
    select_direct,
    load_target,
    origin,
)
from remoteRF.common.grpc import deployment_capabilities_pb2 as pb

ID = "550e8400-e29b-41d4-a716-446655440000"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    monkeypatch.delenv("REMOTERF_ADDR", raising=False)
    monkeypatch.delenv("REMOTERF_CA_CERT", raising=False)


def caps():
    c = local_capabilities(deployment_id=ID)
    c.update(account_transport="https-json", account_api_base="/v2/deployment")
    return c


class RpcError(grpc.RpcError):
    def __init__(self, code):
        self.status = code

    def code(self):
        return self.status


@pytest.mark.parametrize(
    "status",
    [
        grpc.StatusCode.UNIMPLEMENTED,
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.UNAUTHENTICATED,
    ],
)
def test_only_unimplemented_falls_back(status):
    stub = Mock()
    stub.GetCapabilities.side_effect = RpcError(status)
    with patch(
        "remoteRF.common.grpc.deployment_capabilities_pb2_grpc.DeploymentCapabilitiesV1Stub",
        return_value=stub,
    ):
        if status == grpc.StatusCode.UNIMPLEMENTED:
            assert (
                LegacyGrpcAccountBackend(channel=Mock()).capabilities
                == local_capabilities()
            )
        else:
            with pytest.raises(AccountBackendError):
                LegacyGrpcAccountBackend(channel=Mock())


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "{}",
        canonical_json({**local_capabilities(), "protocol_version": "3"}),
    ],
)
def test_invalid_grpc_document_never_downgrades(raw):
    stub = Mock()
    stub.GetCapabilities.return_value = pb.DeploymentCapabilitiesResponse(
        capabilities_json=raw
    )
    with (
        patch(
            "remoteRF.common.grpc.deployment_capabilities_pb2_grpc.DeploymentCapabilitiesV1Stub",
            return_value=stub,
        ),
        pytest.raises((ValidationError, AccountBackendError)),
    ):
        LegacyGrpcAccountBackend(channel=Mock())


def test_valid_grpc_without_identity():
    stub = Mock()
    stub.GetCapabilities.return_value = pb.DeploymentCapabilitiesResponse(
        capabilities_json=canonical_json(local_capabilities())
    )
    with patch(
        "remoteRF.common.grpc.deployment_capabilities_pb2_grpc.DeploymentCapabilitiesV1Stub",
        return_value=stub,
    ):
        assert (
            LegacyGrpcAccountBackend(channel=Mock()).capabilities["deployment_id"]
            is None
        )


def test_arbitrary_https_host_and_identity_change():
    transport = JsonTransport(
        "https://arbitrary.example",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=caps())
            )
        ),
    )
    assert (
        HttpsJsonAccountBackend(
            transport.origin, transport=transport, store=Mock()
        ).capabilities["deployment_id"]
        == ID
    )
    with pytest.raises(AccountBackendError):
        HttpsJsonAccountBackend(
            transport.origin,
            expected_id=str(uuid4()),
            transport=transport,
            store=Mock(),
        )


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_cross_origin_redirect_never_sends_credentials(status):
    seen = []

    def respond(request):
        seen.append(str(request.url))
        return httpx.Response(status, headers={"Location": "https://evil.example/auth"})

    transport = JsonTransport(
        "https://home.example",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    with pytest.raises(AccountBackendError):
        transport.request("POST", "/v2/deployment/login", data={"password": "secret"})
    assert seen == ["https://home.example/v2/deployment/login"]


def test_tls_failure_never_downgrades():
    def fail(request):
        raise httpx.ConnectError("TLS certificate verification failed")

    transport = JsonTransport(
        "https://home.example", client=httpx.Client(transport=httpx.MockTransport(fail))
    )
    with pytest.raises(AccountBackendError):
        HttpsJsonAccountBackend(transport.origin, transport=transport, store=Mock())


def test_permissions_has_operation_specific_time_and_byte_budget(monkeypatch):
    budget = MAX_HOME_PERMISSIONS_HTTP_RESPONSE_BYTES
    raw = b'{"x":"' + (b"a" * (budget - 8)) + b'"}'
    assert len(raw) == budget
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=raw))
    )
    transport = JsonTransport("https://home.example", client=client)
    # Deterministically model a response arriving after the ordinary five
    # second account budget but within the shared permissions budget.
    first_tick = [True]

    def clock():
        if first_tick:
            first_tick.pop()
            return 0.0
        return 6.0

    monkeypatch.setattr("remoteRF.deployment.http.time.monotonic", clock)
    assert transport.permissions("/v2/deployment/permissions", access="token")["x"]


def test_permissions_exact_consumer_limit_and_one_byte_over(monkeypatch):
    budget = MAX_HOME_PERMISSIONS_HTTP_RESPONSE_BYTES
    good = b'{"x":"' + (b"a" * (budget - 8)) + b'"}'
    responses = iter(
        [
            httpx.Response(200, content=good),
            httpx.Response(200, content=good[:-2] + b'a"}'),
        ]
    )
    transport = JsonTransport(
        "https://home.example",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _request: next(responses))
        ),
    )
    monkeypatch.setattr("remoteRF.deployment.http.time.monotonic", lambda: 0.0)
    assert transport.permissions("/v2/deployment/permissions", access="token")["x"]
    with pytest.raises(AccountBackendError, match="protocol limit"):
        transport.permissions("/v2/deployment/permissions", access="token")


def test_permissions_overall_deadline_is_enforced(monkeypatch):
    transport = JsonTransport(
        "https://home.example",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b'{"ok":true}')
            )
        ),
    )
    clock = iter([0.0, PERMISSIONS_CLIENT_OVERALL_TIMEOUT_SECONDS + 0.001])
    monkeypatch.setattr("remoteRF.deployment.http.time.monotonic", lambda: next(clock))
    with pytest.raises(AccountBackendError, match="deadline"):
        transport.permissions("/v2/deployment/permissions", access="token")


@pytest.mark.parametrize(
    "value",
    [
        "http://host",
        "https://host/path",
        "https://user:pw@host",
        "https://host?x=1",
        "https://host#secret",
        "https://host:99999",
    ],
)
def test_invalid_credential_origin(value):
    with pytest.raises(ValidationError):
        origin(value)


@pytest.mark.parametrize(
    "value", ["lab:61005", "https://lab:61005", "grpc://lab:61005"]
)
def test_cli_keeps_explicit_hostport_direct(value):
    from remoteRF import remoterf_cli

    with (
        patch.object(remoterf_cli.sys, "argv", ["remoterf", "-c", "-a", value]),
        patch("remoteRF.config.config.configure", return_value=0) as configure,
    ):
        assert remoterf_cli.main() == 0
    configure.assert_called_once_with("lab", 61005, 61006)
    assert load_target()["transport"] == "grpc"


def test_custom_https_selector_and_bare_host():
    from remoteRF import remoterf_cli

    for value, extra in [
        ("arbitrary.example", []),
        ("https://arbitrary.example:8443", ["--account-transport", "https-json"]),
    ]:
        backend = Mock()
        backend.capabilities = caps()
        with (
            patch.object(
                remoterf_cli.sys, "argv", ["remoterf", "-c", "-a", value] + extra
            ),
            patch("remoteRF.config.config._confirm_tos", return_value=True),
            patch(
                "remoteRF.deployment.backend.HttpsJsonAccountBackend",
                return_value=backend,
            ) as factory,
        ):
            assert remoterf_cli.main() == 0
            factory.assert_called_once_with(origin(value))
            assert load_target()["origin"] == origin(value)


def test_credentials_are_private_and_bound_without_touching_native_config(tmp_path):
    native = tmp_path / ".config/remoterf-client/.env"
    native.parent.mkdir(parents=True)
    native.write_text("REMOTERF_ADDR=lab:61005\nREMOTERF_CA_CERT=local.crt\n")
    before = native.read_bytes()
    subject = str(uuid4())
    store = CredentialStore("https://a.example", ID, keyring_backend=False)
    pair = {"subject_id": subject, "refresh_token": "secret"}
    store.save(pair)
    assert store.load() == pair
    assert (
        CredentialStore("https://b.example", ID, keyring_backend=False).load() is None
    )
    assert (
        CredentialStore("https://a.example", str(uuid4()), keyring_backend=False).load()
        is None
    )
    assert (store.directory / (subject + ".json")).stat().st_mode & 0o777 == 0o600
    assert store.directory.stat().st_mode & 0o777 == 0o700
    select_target(
        {"origin": "https://a.example", "transport": "https-json", "deployment_id": ID}
    )
    select_direct()
    assert native.read_bytes() == before
    store.clear()
    assert store.load() is None


def test_global_v1_profile_cannot_select_new_account_or_device_home(tmp_path):
    old = tmp_path / ".config/remoterf-client/global"
    old.mkdir(parents=True)
    (old / "state.json").write_text("not valid JSON")
    from remoteRF.deployment.direct import resolve_active_profile

    assert load_target() is None and resolve_active_profile() is None


def test_proto_copy_and_descriptor():
    path = (
        Path(__file__).parents[1]
        / "src/remoteRF/common/grpc/deployment_capabilities.proto"
    )
    assert (
        pb.DeploymentCapabilitiesResponse.DESCRIPTOR.fields_by_name[
            "error"
        ].message_type.full_name
        == "remote_rf.ErrorEnvelope"
    )
    assert "FederationV2" not in path.read_text()


def test_failed_new_login_does_not_retain_another_session():
    transport = Mock()
    transport.origin = "https://home.example"
    transport.request.return_value = caps()
    store = Mock()
    backend = HttpsJsonAccountBackend(
        transport.origin, transport=transport, store=store
    )
    backend.credentials = {"subject_id": str(uuid4())}
    transport.request.side_effect = AccountBackendError("invalid_credentials")
    with pytest.raises(AccountBackendError):
        backend.login("different-user", "wrong password")
    assert backend.credentials is None
    store.clear.assert_called_once()


@pytest.mark.parametrize(
    "value",
    [{"error": None}, {"error": ["bad"]}, {"error": {"code": "\x1bsecret"}}, []],
)
def test_untrusted_error_shape_is_redacted(value):
    transport = JsonTransport(
        "https://home.example",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(400, json=value)
            )
        ),
    )
    with pytest.raises(AccountBackendError, match="account_request_failed"):
        transport.request("POST", "/v2/deployment/login", data={"password": "secret"})


def test_enrollment_uses_authenticated_account_api_and_never_echoes_code():
    transport = Mock()
    transport.origin = "https://home.example"
    transport.request.return_value = caps()
    backend = HttpsJsonAccountBackend(
        transport.origin, transport=transport, store=Mock(), clock=lambda: 100
    )
    backend.credentials = {
        "access_token": "access-token",
        "access_expires_at": 200,
    }
    transport.request.return_value = {
        "status": "enrolled",
        "provenance": "destination",
        "enrollment": {"membership_present": True},
    }
    result = backend.enroll("rrf2.opaque-invitation")
    assert result["status"] == "enrolled"
    assert "rrf2.opaque-invitation" not in repr(result)
    transport.request.assert_called_with(
        "POST",
        "/v2/deployment/enroll",
        data={"code": "rrf2.opaque-invitation"},
        access="access-token",
    )


def test_enrollment_failure_preserves_bounded_provenance():
    transport = JsonTransport(
        "https://home.example",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    409,
                    json={
                        "error": {
                            "code": "code_exhausted",
                            "provenance": "destination",
                        }
                    },
                )
            )
        ),
    )
    with pytest.raises(AccountBackendError) as raised:
        transport.request("POST", "/v2/deployment/enroll", data={"code": "secret"})
    assert raised.value.code == "code_exhausted"
    assert raised.value.provenance == "destination"


def test_grpc_account_operations_remain_on_the_negotiated_channel():
    # Some protected driver tests replace grpc_client during collection;
    # a fresh interpreter verifies the real transport implementation.
    import subprocess
    import sys
    code = """
from unittest.mock import Mock, patch
from remoterf_federation_core import local_capabilities, canonical_json
from remoteRF.deployment.backend import LegacyGrpcAccountBackend
from remoteRF.common.grpc import deployment_capabilities_pb2 as pb
from remoteRF.common.grpc.grpc_pb2 import GenericRPCResponse
capability_stub=Mock()
capability_stub.GetCapabilities.return_value=pb.DeploymentCapabilitiesResponse(capabilities_json=canonical_json(local_capabilities()))
with patch('remoteRF.common.grpc.deployment_capabilities_pb2_grpc.DeploymentCapabilitiesV1Stub',return_value=capability_stub):
    backend=LegacyGrpcAccountBackend(channel=Mock())
backend.connection.stub.Call=Mock(return_value=GenericRPCResponse())
with patch('remoteRF.core.grpc_client.get_active_connection',side_effect=AssertionError('target changed')):
    backend.call('ACC:login',{})
backend.connection.stub.Call.assert_called_once()
"""
    subprocess.run([sys.executable, '-c', code], check=True)


def _remote_permission(**changes):
    value = {
        "deployment_id": "22222222-2222-4222-8222-222222222222",
        "display_name": "Destination",
        "contract_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "contract_version": 1,
        "state": "active",
        "groups": [{"group_id": "7", "group_name": "remote-readers"}],
        "retrieved_at": 100,
        "status": "ok",
        "provenance": "destination_signed",
        "error_code": None,
    }
    value.update(changes)
    return value


def test_https_permissions_enforces_typed_provenance_and_alias_binding():
    subject = str(uuid4())
    transport = Mock(origin="https://home.example")
    transport.request.return_value = caps()
    backend = HttpsJsonAccountBackend(
        transport.origin, transport=transport, store=Mock(), clock=lambda: 100
    )
    backend.credentials = {
        "subject_id": subject,
        "access_token": "access",
        "access_expires_at": 200,
    }
    response = {
        "deployment_id": ID,
        "subject_id": subject,
        "display_name": "RemoteRF Server",
        "groups": [{"group_id": "3", "group_name": "local-readers"}],
        "local": {
            "deployment_id": ID,
            "display_name": "RemoteRF Server",
            "groups": [{"group_id": "3", "group_name": "local-readers"}],
        },
        "federation": [_remote_permission()],
    }
    transport.request.return_value = response
    assert backend.permissions()["federation"][0]["status"] == "ok"

    response["federation"] = [
        _remote_permission(status="unavailable", provenance="transport")
    ]
    with pytest.raises(AccountBackendError, match="Invalid home permissions"):
        backend.permissions()

    response["federation"] = []
    response["display_name"] = "Unsigned alias substitution"
    with pytest.raises(AccountBackendError, match="identity mismatch"):
        backend.permissions()


def test_ordinary_perms_renders_remote_results_even_with_zero_local_devices():
    summary = {
        "local": {
            "deployment_id": ID,
            "display_name": "Home",
            "groups": [],
        },
        "federation": [
            _remote_permission(),
            _remote_permission(
                deployment_id="33333333-3333-4333-8333-333333333333",
                display_name="Offline destination",
                contract_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                groups=[],
                retrieved_at=None,
                status="unavailable",
                provenance="transport",
            ),
        ],
    }
    # Protected driver tests install a deliberately partial grpc_client module
    # during collection. Exercise the real shell in a fresh interpreter.
    import subprocess
    import sys

    details = json.dumps(
        {"devices": [], "caps": {}, "groups": [], "home_permissions": summary}
    )
    code = f"""
from types import SimpleNamespace
from unittest.mock import Mock, patch
from remoteRF.common.utils import map_arg
import remoteRF.core.app as client_app
response=SimpleNamespace(results={{
    'UC': map_arg(str([['Normal User']])),
    'details': map_arg({details!r}),
}})
fake=Mock(is_https_home=False)
fake.get_perms.return_value=response
with patch.object(client_app, 'account', fake):
    client_app.perms()
"""
    completed = subprocess.run(
        [sys.executable, "-c", code], check=True, text=True, capture_output=True
    )
    rendered = completed.stdout
    assert "Devices: None" in rendered
    assert "Federated Permissions:" in rendered
    assert "Destination" in rendered and "remote-readers" in rendered
    assert "Offline destination" in rendered and "destination unavailable" in rendered
    assert "destination-signed" in rendered


def test_grpc_perms_always_renders_explicit_incomplete_notice():
    summary = {
        "local": {"deployment_id": ID, "display_name": "Home", "groups": []},
        "federation": [],
        "incomplete": True,
        "omitted_count": 3,
    }
    import subprocess
    import sys

    details = json.dumps(
        {
            "devices": [1],
            "caps": {"1": {"max_reservations": 1, "max_reservation_time_sec": 60}},
            "groups": [],
            "home_permissions": summary,
        }
    )
    code = f"""
from types import SimpleNamespace
from unittest.mock import Mock, patch
from remoteRF.common.utils import map_arg
import remoteRF.core.app as client_app
response=SimpleNamespace(results={{
    'UC': map_arg(str([['Normal User']])),
    'details': map_arg({details!r}),
}})
fake=Mock(is_https_home=False)
fake.get_perms.return_value=response
with patch.object(client_app, 'account', fake):
    client_app.perms()
"""
    completed = subprocess.run(
        [sys.executable, "-c", code], check=True, text=True, capture_output=True
    )
    assert "Federation summary incomplete: 3 destination(s) omitted" in completed.stdout
