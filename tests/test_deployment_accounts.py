import json
from uuid import uuid4
from pathlib import Path
from unittest.mock import patch,Mock
import grpc
import httpx
import pytest
from remoterf_federation_core import local_capabilities,canonical_json,ValidationError
from remoteRF.deployment.backend import LegacyGrpcAccountBackend,HttpsJsonAccountBackend
from remoteRF.deployment.http import JsonTransport,AccountBackendError
from remoteRF.deployment.state import CredentialStore,select_target,select_direct,load_target,origin
from remoteRF.common.grpc import deployment_capabilities_pb2 as pb

ID='550e8400-e29b-41d4-a716-446655440000'

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path))
    monkeypatch.setenv('PYTHON_KEYRING_BACKEND','keyring.backends.null.Keyring')
    monkeypatch.delenv('REMOTERF_ADDR',raising=False)
    monkeypatch.delenv('REMOTERF_CA_CERT',raising=False)


def caps():
    c=local_capabilities(deployment_id=ID)
    c.update(account_transport='https-json',account_api_base='/v2/deployment')
    return c

class RpcError(grpc.RpcError):
    def __init__(self,code):self.status=code
    def code(self):return self.status

@pytest.mark.parametrize('status',[grpc.StatusCode.UNIMPLEMENTED,grpc.StatusCode.DEADLINE_EXCEEDED,grpc.StatusCode.UNAVAILABLE,grpc.StatusCode.UNAUTHENTICATED])
def test_only_unimplemented_falls_back(status):
    stub=Mock();stub.GetCapabilities.side_effect=RpcError(status)
    with patch('remoteRF.common.grpc.deployment_capabilities_pb2_grpc.DeploymentCapabilitiesV1Stub',return_value=stub):
        if status==grpc.StatusCode.UNIMPLEMENTED:
            assert LegacyGrpcAccountBackend(channel=Mock()).capabilities==local_capabilities()
        else:
            with pytest.raises(AccountBackendError):LegacyGrpcAccountBackend(channel=Mock())

@pytest.mark.parametrize('raw',['not json','{}',canonical_json({**local_capabilities(),'protocol_version':'3'})])
def test_invalid_grpc_document_never_downgrades(raw):
    stub=Mock();stub.GetCapabilities.return_value=pb.DeploymentCapabilitiesResponse(capabilities_json=raw)
    with patch('remoteRF.common.grpc.deployment_capabilities_pb2_grpc.DeploymentCapabilitiesV1Stub',return_value=stub),pytest.raises((ValidationError,AccountBackendError)):
        LegacyGrpcAccountBackend(channel=Mock())


def test_valid_grpc_without_identity():
    stub=Mock();stub.GetCapabilities.return_value=pb.DeploymentCapabilitiesResponse(capabilities_json=canonical_json(local_capabilities()))
    with patch('remoteRF.common.grpc.deployment_capabilities_pb2_grpc.DeploymentCapabilitiesV1Stub',return_value=stub):
        assert LegacyGrpcAccountBackend(channel=Mock()).capabilities['deployment_id'] is None


def test_arbitrary_https_host_and_identity_change():
    transport=JsonTransport('https://arbitrary.example',client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=caps()))))
    assert HttpsJsonAccountBackend(transport.origin,transport=transport,store=Mock()).capabilities['deployment_id']==ID
    with pytest.raises(AccountBackendError):HttpsJsonAccountBackend(transport.origin,expected_id=str(uuid4()),transport=transport,store=Mock())

@pytest.mark.parametrize('status',[301,302,307,308])
def test_cross_origin_redirect_never_sends_credentials(status):
    seen=[]
    def respond(request):
        seen.append(str(request.url));return httpx.Response(status,headers={'Location':'https://evil.example/auth'})
    transport=JsonTransport('https://home.example',client=httpx.Client(transport=httpx.MockTransport(respond)))
    with pytest.raises(AccountBackendError):transport.request('POST','/v2/deployment/login',data={'password':'secret'})
    assert seen==['https://home.example/v2/deployment/login']


def test_tls_failure_never_downgrades():
    def fail(request):raise httpx.ConnectError('TLS certificate verification failed')
    transport=JsonTransport('https://home.example',client=httpx.Client(transport=httpx.MockTransport(fail)))
    with pytest.raises(AccountBackendError):HttpsJsonAccountBackend(transport.origin,transport=transport,store=Mock())

@pytest.mark.parametrize('value',['http://host','https://host/path','https://user:pw@host','https://host?x=1','https://host#secret','https://host:99999'])
def test_invalid_credential_origin(value):
    with pytest.raises(ValidationError):origin(value)

@pytest.mark.parametrize('value',['lab:61005','https://lab:61005','grpc://lab:61005'])
def test_cli_keeps_explicit_hostport_direct(value):
    from remoteRF import remoterf_cli
    with patch.object(remoterf_cli.sys,'argv',['remoterf','-c','-a',value]),patch('remoteRF.config.config.configure',return_value=0) as configure:
        assert remoterf_cli.main()==0
    configure.assert_called_once_with('lab',61005,61006)
    assert load_target()['transport']=='grpc'


def test_custom_https_selector_and_bare_host():
    from remoteRF import remoterf_cli
    for value,extra in [('arbitrary.example',[]),('https://arbitrary.example:8443',['--account-transport','https-json'])]:
        backend=Mock();backend.capabilities=caps()
        with patch.object(remoterf_cli.sys,'argv',['remoterf','-c','-a',value]+extra),patch('remoteRF.config.config._confirm_tos',return_value=True),patch('remoteRF.deployment.backend.HttpsJsonAccountBackend',return_value=backend) as factory:
            assert remoterf_cli.main()==0
            factory.assert_called_once_with(origin(value))
            assert load_target()['origin']==origin(value)


def test_credentials_are_private_and_bound_without_touching_native_config(tmp_path):
    native=tmp_path/'.config/remoterf-client/.env';native.parent.mkdir(parents=True)
    native.write_text('REMOTERF_ADDR=lab:61005\nREMOTERF_CA_CERT=local.crt\n');before=native.read_bytes()
    subject=str(uuid4());store=CredentialStore('https://a.example',ID,keyring_backend=False)
    pair={'subject_id':subject,'refresh_token':'secret'}
    store.save(pair);assert store.load()==pair
    assert CredentialStore('https://b.example',ID,keyring_backend=False).load() is None
    assert CredentialStore('https://a.example',str(uuid4()),keyring_backend=False).load() is None
    assert (store.directory/(subject+'.json')).stat().st_mode&0o777==0o600
    assert store.directory.stat().st_mode&0o777==0o700
    select_target({'origin':'https://a.example','transport':'https-json','deployment_id':ID})
    select_direct();assert native.read_bytes()==before
    store.clear();assert store.load() is None


def test_global_v1_profile_cannot_select_new_account_or_device_home(tmp_path):
    old=tmp_path/'.config/remoterf-client/global';old.mkdir(parents=True)
    (old/'state.json').write_text('not valid JSON')
    from remoteRF.deployment.direct import resolve_active_profile
    assert load_target() is None and resolve_active_profile() is None


def test_proto_copy_and_descriptor():
    import hashlib
    path=Path(__file__).parents[1]/'src/remoteRF/common/grpc/deployment_capabilities.proto'
    assert pb.DeploymentCapabilitiesResponse.DESCRIPTOR.fields_by_name['error'].message_type.full_name=='remote_rf.ErrorEnvelope'
    assert 'FederationV2' not in path.read_text()
