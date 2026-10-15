"""A Global account's labs: the lab-bound secret, the linked-lab record, and
`-l` opening a lab directly when RemoteRF Global does not answer."""

import hashlib
from unittest import mock

import httpx
import pytest
from remoterf_federation_core import local_capabilities

from remoterf import remoterf_cli
from remoterf.deployment import homes
from remoterf.deployment.http import AccountBackendError, JsonTransport
from remoterf.deployment.lanpass import lan_secret

LAB = "880a50a0-fb9f-4828-96be-ff6124643196"
CERT = b"-----BEGIN CERTIFICATE-----\nucla\n-----END CERTIFICATE-----\n"
LAN = {"kind": "lan", "host": "192.168.1.20", "port": 12321, "cert_port": 12322}
RELAY = {"kind": "relay", "host": "ucla.global.remoterf.net", "port": 12321, "cert_port": 12322}


def document(deployment_id=LAB):
    return local_capabilities(display_name="UCLA", deployment_id=deployment_id, routes=[LAN, RELAY],
                              cert_sha256=hashlib.sha256(CERT).hexdigest())


def test_lan_secret_matches_core():
    # remoterf-federation-core tests/test_lan.py pins the same vector.
    assert lan_secret("correct horse battery", LAB, "jdoe") == \
        "93167784b0fab1e28a1e832140fc75851ba6bd7b4cbfdbe38eddb0598c202fbb"


def test_sync_records_the_labs_and_the_lab_gets_a_bound_secret(monkeypatch):
    monkeypatch.setattr(homes, "discover", lambda host: document())
    homes.sync_linked({"username": "JDoe", "labs": [{"deployment_id": LAB, "slug": "ucla"}]})
    assert homes.linked() == {"username": "jdoe", "labs": {LAB: "ucla"}}
    assert homes.linked_homes() == ["ucla"]
    assert homes.lab_password("ucla", "jdoe", "pw-123456") == lan_secret("pw-123456", LAB, "jdoe")
    assert homes.lab_password("ucla", "someone-else", "pw-123456") == "pw-123456"
    homes.save_home("mit", document(deployment_id="2b0a50a0-fb9f-4828-96be-ff6124643197"))
    assert homes.lab_password("mit", "jdoe", "pw-123456") == "pw-123456"  # not linked there
    # The same lab set up by `-c -a` (no deployment id) is known by its CA.
    data = homes.load_state()
    data["homes"]["default"] = {**data["homes"]["ucla"], "deployment_id": None}
    homes.save_state(data)
    assert homes.lab_password("default", "jdoe", "pw-123456") == lan_secret("pw-123456", LAB, "jdoe")


def test_a_lan_route_of_a_global_lab_verifies_its_relay_name(monkeypatch):
    from remoterf.config import config as cfg

    home = homes.save_home("ucla", document())
    monkeypatch.setattr("remoterf.deployment.state.select_direct", lambda: None)
    homes.activate("ucla", home, LAN, CERT, remember=False)
    env = cfg._env_path().read_text()
    assert "REMOTERF_ADDR=192.168.1.20:12321" in env and "REMOTERF_TLS_SERVER_NAME=ucla.global.remoterf.net" in env
    assert homes.last_target() is None  # a fallback does not move `-l`


def test_an_unanswered_request_is_unreachable_and_never_sent():
    def refuse(request):
        raise httpx.ConnectError("down", request=request)

    transport = JsonTransport("https://global.remoterf.net", client=httpx.Client(transport=httpx.MockTransport(refuse)))
    with pytest.raises(AccountBackendError) as caught:
        transport.request("GET", "/.well-known/remoterf-deployment")
    assert caught.value.unreachable and not caught.value.sent

    proxy = JsonTransport("https://global.remoterf.net", client=httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(502, text="<html>Bad Gateway</html>"))))
    with pytest.raises(AccountBackendError) as caught:
        proxy.request("GET", "/.well-known/remoterf-deployment")
    assert caught.value.unreachable


def test_login_opens_a_linked_lab_when_global_is_down(monkeypatch):
    monkeypatch.setattr(homes, "discover", lambda host: document())
    homes.sync_linked({"username": "jdoe", "labs": [{"deployment_id": LAB, "slug": "ucla"}]})
    down = AccountBackendError("Account connection failed", unreachable=True, sent=False)
    monkeypatch.setattr("remoterf.deployment.backend.HttpsJsonAccountBackend", mock.Mock(side_effect=down))
    monkeypatch.setattr(homes, "connect", lambda name, remember=True: {**LAN, "remember": remember})
    with mock.patch.object(remoterf_cli, "_account_shell", return_value=0) as shell:
        assert remoterf_cli._use_global(register=False) == 0
    kwargs = shell.call_args.kwargs
    assert (kwargs["home"], kwargs["username"], kwargs["route"]["remember"]) == ("ucla", "jdoe", False)

    # A wrong password is not "down"; and registering needs Global.
    monkeypatch.setattr("remoterf.deployment.backend.HttpsJsonAccountBackend",
                        mock.Mock(side_effect=AccountBackendError("invalid_credentials")))
    with mock.patch.object(remoterf_cli, "_account_shell", return_value=0) as shell:
        assert remoterf_cli._use_global(register=False) == 2 and not shell.called
    monkeypatch.setattr("remoterf.deployment.backend.HttpsJsonAccountBackend", mock.Mock(side_effect=down))
    with mock.patch.object(remoterf_cli, "_account_shell", return_value=0) as shell:
        assert remoterf_cli._use_global(register=True) == 2 and not shell.called


def test_enroll_can_carry_the_password_for_a_lab_login():
    from remoterf.deployment.backend import HttpsJsonAccountBackend

    backend = mock.Mock()
    backend.base = "/v2/deployment"
    backend._access.return_value = "tok"
    backend.transport.request.return_value = {"status": "enrolled", "provenance": "destination"}
    HttpsJsonAccountBackend.enroll(backend, "ucla/ABC123", password="pw-123456")
    assert backend.transport.request.call_args.kwargs["data"] == {"code": "ucla/ABC123", "password": "pw-123456"}
    HttpsJsonAccountBackend.enroll(backend, "ucla/ABC123")
    assert backend.transport.request.call_args.kwargs["data"] == {"code": "ucla/ABC123"}


def test_a_lab_on_global_sends_registration_there():
    from remoterf.common.utils import map_arg
    from remoterf.core.grpc_acc import RemoteRFAccount

    account = RemoteRFAccount("jdoe", "pw-123456", "jdoe@example.org")
    reply = mock.Mock(results={"UE": map_arg("This lab registers through RemoteRF Global"), "GS": map_arg("ucla")})
    with mock.patch("remoterf.core.grpc_acc.rpc_client", return_value=reply):
        assert account.create_user() is False
    assert account.register_at_global == "ucla"
