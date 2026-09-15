"""Phase 5: enrollment codes, named HOMEs, route fallback, Global identity."""

import hashlib
import json
from unittest import mock

import pytest
from remoterf_federation_core import ValidationError, local_capabilities

from remoteRF import remoterf_cli
from remoteRF.deployment import homes

CERT = b"-----BEGIN CERTIFICATE-----\nucla\n-----END CERTIFICATE-----\n"
OTHER_CERT = b"-----BEGIN CERTIFICATE-----\nnot-ucla\n-----END CERTIFICATE-----\n"
DIGEST = hashlib.sha256(CERT).hexdigest()

LAN = {"kind": "lan", "host": "192.168.1.20", "port": 12321, "cert_port": 12322}
RELAY = {
    "kind": "relay",
    "host": "ucla.global.remoterf.net",
    "port": 12321,
    "cert_port": 12322,
}


def document(routes=(LAN, RELAY), cert_sha256=DIGEST, name="UCLA Wireless Lab"):
    return local_capabilities(
        display_name=name,
        deployment_id="880a50a0-fb9f-4828-96be-ff6124643196",
        routes=list(routes),
        cert_sha256=cert_sha256,
    )


@pytest.fixture
def reachable(monkeypatch):
    """Control which route answers, and with which certificate."""
    served = {}

    def fetch(host, port, *, timeout_sec=3.0):
        if host not in served:
            raise OSError("unreachable")
        return served[host]

    monkeypatch.setattr("remoteRF.config.cert_fetcher.fetch_ca_bytes", fetch)
    return served


# --- enrollment codes -------------------------------------------------------

def test_code_forms():
    assert homes.parse_code("ucla.global.remoterf.net/QEHN7") == (
        "ucla.global.remoterf.net",
        "QEHN7",
    )
    # A short name expands to a Global deployment hostname.
    assert homes.parse_code("ucla/QEHN7") == ("ucla.global.remoterf.net", "QEHN7")
    assert homes.parse_code("lab.example.edu:8443/QEHN7") == (
        "lab.example.edu:8443",
        "QEHN7",
    )
    # A plain code keeps working against an already-configured target.
    assert homes.parse_code("QEHN7") == (None, "QEHN7")
    for invalid in ("/QEHN7", "ucla/", "-bad-/QEHN7"):
        with pytest.raises(ValidationError):
            homes.parse_code(invalid)


def test_home_name_comes_from_the_deployment_slug():
    assert homes.home_name_for("ucla.global.remoterf.net") == "ucla"
    assert homes.home_name_for("lab.example.edu:8443") == "lab-example-edu"


# --- saved homes ------------------------------------------------------------

def test_saving_a_home_records_routes_and_the_certificate_pin():
    home = homes.save_home("ucla", document())
    assert home["deployment_id"] == "880a50a0-fb9f-4828-96be-ff6124643196"
    assert home["cert_sha256"] == DIGEST
    assert [route["kind"] for route in home["routes"]] == ["lan", "relay"]
    assert homes.load_home("ucla") == home
    assert set(homes.homes()) == {"ucla"}


def test_a_home_name_may_not_be_reused_by_another_deployment():
    homes.save_home("ucla", document())
    stolen = document()
    stolen["deployment_id"] = "11111111-2222-4333-8444-555555555555"
    with pytest.raises(ValidationError):
        homes.save_home("ucla", stolen)


def test_a_global_account_needs_no_home_at_all():
    assert homes.homes() == {}
    assert not homes.has_global_account()
    homes.remember_global_account(
        "https://global.remoterf.net", "897541e5-b61f-45f7-a814-8ffeaad2ae69"
    )
    homes.set_last_target("global")
    assert homes.has_global_account()
    # A complete, supported state: a Global identity and zero deployment homes.
    assert homes.homes() == {}
    assert homes.last_target() == {"kind": "global", "name": None}


def test_homes_and_a_global_account_coexist_without_merging():
    homes.save_home("ucla", document())
    homes.remember_global_account(
        "https://global.remoterf.net", "897541e5-b61f-45f7-a814-8ffeaad2ae69"
    )
    state = homes.load_state()
    assert state["global_account"]["deployment_id"] != state["homes"]["ucla"]["deployment_id"]


# --- route selection --------------------------------------------------------

def test_lan_is_preferred_when_it_answers(reachable):
    reachable["192.168.1.20"] = CERT
    reachable["ucla.global.remoterf.net"] = CERT
    route, certificate = homes.select_route(homes.save_home("ucla", document()))
    assert route["kind"] == "lan"
    assert certificate == CERT


def test_relay_takes_over_when_the_lan_is_unreachable(reachable):
    reachable["ucla.global.remoterf.net"] = CERT
    route, _ = homes.select_route(homes.save_home("ucla", document()))
    assert route["kind"] == "relay"


def test_no_route_is_an_error_not_a_different_home(reachable):
    homes.save_home("ucla", document())
    homes.save_home("mit", document(name="MIT"))
    with pytest.raises(ValidationError, match="Could not reach"):
        homes.select_route(homes.load_home("ucla"))


def test_a_route_serving_the_wrong_certificate_is_refused(reachable):
    reachable["192.168.1.20"] = OTHER_CERT
    reachable["ucla.global.remoterf.net"] = CERT
    with pytest.raises(ValidationError, match="unexpected certificate"):
        homes.select_route(homes.save_home("ucla", document()))


def test_an_unpinned_deployment_still_connects(reachable):
    reachable["192.168.1.20"] = OTHER_CERT
    route, certificate = homes.select_route(
        homes.save_home("ucla", document(cert_sha256=None))
    )
    assert route["kind"] == "lan" and certificate == OTHER_CERT


def test_the_lan_gets_a_short_timeout_and_the_relay_a_longer_one(monkeypatch):
    seen = []

    def fetch(host, port, *, timeout_sec=3.0):
        seen.append((host, timeout_sec))
        raise OSError("unreachable")

    monkeypatch.setattr("remoteRF.config.cert_fetcher.fetch_ca_bytes", fetch)
    with pytest.raises(ValidationError):
        homes.select_route(homes.save_home("ucla", document()))
    assert sorted(seen) == [
        ("192.168.1.20", homes.LAN_CONNECT_TIMEOUT),
        ("ucla.global.remoterf.net", homes.ROUTE_CONNECT_TIMEOUT),
    ]


def test_routes_are_probed_concurrently_and_the_lan_still_wins(monkeypatch):
    import time

    def fetch(host, port, *, timeout_sec=3.0):
        time.sleep(0.3)  # both take 0.3s; serial would be 0.6s
        return CERT

    monkeypatch.setattr("remoteRF.config.cert_fetcher.fetch_ca_bytes", fetch)
    home = homes.save_home("ucla", document())
    started = time.time()
    route, certificate = homes.select_route(home)
    assert time.time() - started < 0.5
    assert route["kind"] == "lan" and certificate == CERT


# --- activation -------------------------------------------------------------

def test_connecting_writes_the_ordinary_direct_profile(reachable):
    """After connect, every command behaves as on a direct connection."""
    reachable["ucla.global.remoterf.net"] = CERT
    homes.save_home("ucla", document())
    route = homes.connect("ucla")
    assert route["kind"] == "relay"

    from remoteRF.deployment.direct import resolve_active_profile
    from remoteRF.deployment.state import load_target

    profile = resolve_active_profile()
    assert profile.grpc_endpoint == "ucla.global.remoterf.net:12321"
    assert profile.ca_path.read_bytes() == CERT
    # The device transport is plain gRPC, exactly as a manual -c -a target.
    assert load_target()["transport"] == "grpc"
    assert homes.last_target() == {"kind": "home", "name": "ucla"}


def test_changing_route_keeps_the_same_home_and_identity(reachable):
    reachable["ucla.global.remoterf.net"] = CERT
    homes.save_home("ucla", document())
    assert homes.connect("ucla")["kind"] == "relay"
    relay_id = homes.load_home("ucla")["deployment_id"]

    reachable["192.168.1.20"] = CERT
    assert homes.connect("ucla")["kind"] == "lan"
    assert homes.load_home("ucla")["deployment_id"] == relay_id
    assert homes.last_target() == {"kind": "home", "name": "ucla"}


def test_registering_a_home_discovers_saves_and_connects(reachable, monkeypatch):
    reachable["ucla.global.remoterf.net"] = CERT
    monkeypatch.setattr(homes, "discover", lambda host, **kw: document())
    name, home, route = homes.register_home("ucla.global.remoterf.net")
    assert name == "ucla"
    assert home["display_name"] == "UCLA Wireless Lab"
    assert route["kind"] == "relay"


# --- migration --------------------------------------------------------------

def test_a_pre_homes_configuration_becomes_the_default_home(tmp_path, monkeypatch):
    from remoteRF.config.config import _certs_dir, _env_path, _write_env_kv

    certs = _certs_dir()
    certs.mkdir(parents=True, exist_ok=True)
    (certs / "default.crt").write_bytes(CERT)
    _write_env_kv(
        _env_path(),
        {
            "REMOTERF_ADDR": "164.67.195.207:61005",
            "REMOTERF_CA_CERT": str(certs / "default.crt"),
        },
    )
    assert homes.migrate_legacy_target() == "default"
    home = homes.load_home("default")
    assert home["routes"] == [
        {"kind": "direct", "host": "164.67.195.207", "port": 61005, "cert_port": 61006}
    ]
    assert home["cert_sha256"] == DIGEST
    # Idempotent: a second start does not re-adopt or duplicate it.
    assert homes.migrate_legacy_target() is None


def test_reconfiguring_the_direct_target_replaces_a_stale_default_home(monkeypatch):
    """A pinned "default" home from an old `-c -a` must not outlive the next one:
    once the server's CA changed, its pin would refuse the very server that
    `-c -a` just fetched a fresh CA from."""
    from remoteRF.config import config as cfg

    certs = cfg._certs_dir()
    certs.mkdir(parents=True, exist_ok=True)
    (certs / "default.crt").write_bytes(CERT)
    cfg._write_env_kv(
        cfg._env_path(),
        {"REMOTERF_ADDR": "164.67.195.207:61005", "REMOTERF_CA_CERT": str(certs / "default.crt")},
    )
    assert homes.migrate_legacy_target() == "default"
    homes.set_last_target("home", "default")

    new_cert = CERT.replace(b"BEGIN", b"BEGIN ")  # a different CA
    monkeypatch.setattr(cfg, "_host_resolves", lambda host: True)
    monkeypatch.setattr(cfg, "_confirm_tos", lambda: True)

    def fetch(host, port, *, out_path, timeout_sec, overwrite):
        out_path.write_bytes(new_cert)
        return True

    monkeypatch.setattr(cfg, "fetch_and_save_ca_cert", fetch)
    cfg.configure("164.67.195.210", 61005, 61006)

    assert homes.homes() == {}
    assert homes.last_target() is None
    assert homes.migrate_legacy_target() == "default"
    home = homes.load_home("default")
    assert home["routes"][0]["host"] == "164.67.195.210"
    assert home["cert_sha256"] == hashlib.sha256(new_cert).hexdigest()


def test_migration_does_nothing_without_a_previous_configuration():
    assert homes.migrate_legacy_target() is None
    assert homes.homes() == {}


# --- the CLI ----------------------------------------------------------------

def run_cli(argv, monkeypatch, **patches):
    monkeypatch.setattr(remoterf_cli.sys, "argv", ["remoterf"] + argv)
    for name, replacement in patches.items():
        monkeypatch.setattr(remoterf_cli, name, replacement)
    return remoterf_cli.main()


def test_register_with_a_code_lands_in_the_home_shell(monkeypatch, reachable):
    reachable["ucla.global.remoterf.net"] = CERT
    monkeypatch.setattr(homes, "discover", lambda host, **kw: document())
    monkeypatch.setattr("builtins.input", lambda *_: "ucla/QEHN7")
    shell = mock.Mock(return_value=0)

    assert run_cli(["-r"], monkeypatch, _account_shell=shell) == 0
    kwargs = shell.call_args.kwargs
    assert (kwargs["register"], kwargs["enrollment_code"], kwargs["server_label"]) == (
        True, "QEHN7", "ucla (relay)"
    )
    assert kwargs["show_banner"] is False and kwargs["home"] == "ucla"
    assert kwargs["route"]["kind"] == "relay" and kwargs["route"]["home"]["display_name"]
    assert homes.last_target() == {"kind": "home", "name": "ucla"}


def test_a_blank_code_is_refused_and_global_has_its_own_spelling(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "")
    used_global = mock.Mock(return_value=0)
    assert run_cli(["-r"], monkeypatch, _use_global=used_global) == 2
    used_global.assert_not_called()
    assert run_cli(["-r", "global"], monkeypatch, _use_global=used_global) == 0
    used_global.assert_called_once_with(register=True)


def test_login_resumes_the_last_target(monkeypatch, reachable):
    reachable["ucla.global.remoterf.net"] = CERT
    homes.save_home("ucla", document())
    homes.connect("ucla")
    shell = mock.Mock(return_value=0)
    assert run_cli(["-l"], monkeypatch, _account_shell=shell) == 0
    assert shell.call_args.kwargs["server_label"] == "ucla (relay)"


def test_login_names_a_home_explicitly(monkeypatch, reachable):
    reachable["192.168.1.20"] = CERT
    homes.save_home("ucla", document())
    homes.save_home("mit", document(name="MIT"))
    shell = mock.Mock(return_value=0)
    assert run_cli(["-l", "ucla"], monkeypatch, _account_shell=shell) == 0
    assert shell.call_args.kwargs["server_label"] == "ucla (LAN)"

    assert run_cli(["-l", "nope"], monkeypatch, _account_shell=shell) == 2


def test_login_global_requires_a_global_account(monkeypatch):
    homes.save_home("ucla", document())
    assert run_cli(["-l", "global"], monkeypatch, _account_shell=mock.Mock()) == 2

    homes.remember_global_account(
        "https://global.remoterf.net", "897541e5-b61f-45f7-a814-8ffeaad2ae69"
    )
    used_global = mock.Mock(return_value=0)
    assert run_cli(["-l", "global"], monkeypatch, _use_global=used_global) == 0
    used_global.assert_called_once_with(register=False)


def test_a_global_only_user_logs_back_in_without_any_home(monkeypatch):
    homes.remember_global_account(
        "https://global.remoterf.net", "897541e5-b61f-45f7-a814-8ffeaad2ae69"
    )
    homes.set_last_target("global")
    assert homes.homes() == {}
    used_global = mock.Mock(return_value=0)
    assert run_cli(["-l"], monkeypatch, _use_global=used_global) == 0
    used_global.assert_called_once_with(register=False)


def test_a_failed_home_never_falls_back_to_global(monkeypatch, reachable):
    homes.save_home("ucla", document())
    homes.remember_global_account(
        "https://global.remoterf.net", "897541e5-b61f-45f7-a814-8ffeaad2ae69"
    )
    used_global = mock.Mock(return_value=0)
    shell = mock.Mock(return_value=0)
    # No route answers for ucla.
    assert run_cli(
        ["-l", "ucla"], monkeypatch, _use_global=used_global, _account_shell=shell
    ) == 2
    used_global.assert_not_called()
    shell.assert_not_called()


def test_homes_listing(monkeypatch):
    homes.save_home("ucla", document())
    homes.remember_global_account(
        "https://global.remoterf.net", "897541e5-b61f-45f7-a814-8ffeaad2ae69"
    )
    # printf renders through prompt_toolkit, so collect its arguments directly.
    lines = []
    assert run_cli(
        ["homes"], monkeypatch, printf=lambda *args: lines.append("".join(map(str, args[::2])))
    ) == 0
    printed = "\n".join(lines)
    assert "ucla" in printed and "UCLA Wireless Lab" in printed
    assert "lan, relay" in printed
    assert "RemoteRF Global account" in printed


# --- post-login behaviour ---------------------------------------------------

def test_every_command_after_login_goes_native_to_the_home(reachable, monkeypatch):
    """`perms`, `getdev`, `resdev` and the rest behave exactly as on a direct
    connection: the same ACC: calls, over the HOME's own gRPC channel."""
    import datetime

    from remoteRF.common.grpc import grpc_pb2
    from remoteRF.common.utils import map_arg
    from remoteRF.core import app, grpc_acc

    reachable["192.168.1.20"] = CERT
    homes.save_home("ucla", document())
    assert homes.connect("ucla")["kind"] == "lan"

    sent = []

    def rpc_client(*, function_name, args, connection=None):
        sent.append((function_name, {k: v for k, v in args.items()}))
        response = grpc_pb2.GenericRPCResponse()
        if function_name == "ACC:get_dev":
            response.results["0"].CopyFrom(map_arg("pluto-a"))
        elif function_name == "ACC:get_perms":
            response.results["UC"].CopyFrom(map_arg("User"))
        elif function_name == "ACC:reserve_device":
            response.results["Token"].CopyFrom(map_arg("reservation-token"))
        return response

    monkeypatch.setattr(grpc_acc, "rpc_client", rpc_client)
    account = grpc_acc.RemoteRFAccount("ethan", "secret")
    # A deployment-native account is not an HTTPS home: no Global in the path.
    assert not account.is_https_home

    account.get_perms()
    account.get_devices()
    account.get_reservations()
    monkeypatch.setattr(
        "remoteRF.drivers.dynamic_device.install_driver", lambda **kw: None
    )
    now = datetime.datetime.now()
    token = account.reserve_device(0, now, now + datetime.timedelta(hours=1))

    assert [name for name, _ in sent] == [
        "ACC:get_perms",
        "ACC:get_dev",
        "ACC:get_res",
        "ACC:reserve_device",
    ]
    assert token == "reservation-token"
    # Credentials go to the HOME itself, never through any broker.
    for _, args in sent:
        assert args["un"].string_value == "ethan"

    # And the transport those calls ride is this HOME's own direct channel.
    monkeypatch.setattr("grpc.secure_channel", lambda *a, **k: mock.Mock())
    monkeypatch.setattr("grpc.ssl_channel_credentials", lambda **k: "creds")
    from remoteRF.core import grpc_client as real_client

    assert real_client.active_endpoint() == "192.168.1.20:12321"


# --- registration without email --------------------------------------------

def test_an_https_home_that_activates_without_email_skips_verify(monkeypatch):
    """If the home answers `status: active` (email unavailable), the shell
    must not sit waiting for a verification token that will never arrive, and
    it must still log in to obtain a session."""
    from remoteRF.core import app as shell
    from remoteRF.core.grpc_acc import RemoteRFAccount as Account

    class Backend:
        capabilities = {
            "account_transport": "https-json",
            "registration_policy": {
                "enabled": True, "username_required": True, "email_required": True,
                "email_verification_required": True, "enrollment_code_required": False,
            },
        }
        def register(self, name, email, password):
            return {"status": "active", "message": "ready"}

    account = Account(backend=Backend())
    monkeypatch.setattr(shell, "account", account)
    monkeypatch.setattr(shell, "_ask", lambda *a, **k: "alice@example.org")
    monkeypatch.setattr(shell, "_password_confirmation", lambda **k: "Sup3r-secret-pw")
    monkeypatch.setattr(shell, "_home_name", lambda: "global")

    assert shell._account_command("register") is False      # not authenticated yet
    assert account.needs_verification is False
    assert shell._account_command("verify") is False         # skipped, no prompt


# --- stored logins -----------------------------------------------------------

def test_a_direct_home_keeps_its_login_in_the_keyring_or_a_private_file(monkeypatch):
    """The gRPC protocol has no sessions, so the stored login is the password
    itself: keyring first, owner-only file otherwise, gone on logout."""
    monkeypatch.setattr(homes, "_keyring", lambda: None)  # force the file path
    assert homes.recall_login("ucla") is None
    homes.remember_login("ucla", "alice", "tok-abc")
    assert homes.recall_login("ucla") == {"username": "alice", "secret": "tok-abc"}
    assert oct(homes._login_file("ucla").stat().st_mode & 0o777) == "0o600"
    homes.forget_login("ucla")
    assert homes.recall_login("ucla") is None


def test_login_resumes_a_stored_direct_login_without_prompting(monkeypatch):
    from remoteRF.core import app as shell
    from remoteRF.core.grpc_acc import RemoteRFAccount as Account

    monkeypatch.setattr(homes, "_keyring", lambda: None)  # never the real keychain
    homes.remember_login("ucla", "alice", "tok-abc")
    account = Account()
    calls = []
    account.login_user = lambda **kw: calls.append((account.username, account.password, kw)) or True
    monkeypatch.setattr(shell, "account", account)
    monkeypatch.setattr(shell, "home_name", "ucla")
    monkeypatch.setattr(shell, "_ask", lambda *a, **k: pytest.fail("must not prompt"))
    assert shell.welcome(show_banner=False, initial=["login"]) is True
    assert calls == [("alice", "tok-abc", {})]  # resume never asks for a new token


def test_a_broken_stored_login_never_blocks_the_prompt(monkeypatch):
    from remoteRF.core import app as shell
    from remoteRF.core.grpc_acc import RemoteRFAccount as Account

    # Corrupt file: read as "nothing stored".
    path = homes._login_file("ucla")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"{not json")
    assert homes.recall_login("ucla") is None
    # Wrong shape: also nothing.
    path.write_bytes(b'{"username": 5}')
    assert homes.recall_login("ucla") is None
    # A stored login the server rejects, or that blows up: forgotten, then the prompt.
    homes.remember_login("ucla", "alice", "old-pw")
    account = Account()
    account.login_user = lambda **kw: (_ for _ in ()).throw(RuntimeError("server down"))
    prompted = []
    monkeypatch.setattr(shell, "account", account)
    monkeypatch.setattr(shell, "home_name", "ucla")
    monkeypatch.setattr(shell, "_account_command", lambda cmd: prompted.append(cmd) or True)
    assert shell.welcome(show_banner=False, initial=["login"]) is True
    assert prompted == ["login"]
    assert homes.recall_login("ucla") is None
    # Writes that fail are ignored, not raised.
    monkeypatch.setattr(homes, "private_write", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    homes.remember_login("ucla", "alice", "tok")


def test_the_os_keyring_is_opt_in():
    from remoteRF.deployment.state import keyring_if_enabled

    assert keyring_if_enabled() is None


def test_a_direct_login_keeps_the_token_never_the_password(monkeypatch):
    from remoteRF.common.utils import map_arg
    from remoteRF.core import app as shell
    from remoteRF.core.grpc_acc import RemoteRFAccount as Account

    class Response:
        results = {"UC": map_arg("alice"), "token": map_arg("tok-from-server")}

    account = Account(username="alice", password="Sup3r-secret-pw")
    sent = []
    account._call = lambda *, function_name, args: sent.append((function_name, args)) or Response()
    monkeypatch.setattr(shell, "account", account)
    monkeypatch.setattr(shell, "home_name", "ucla")
    monkeypatch.setattr(shell, "_ask", lambda prompt, **k: "alice" if "User" in prompt else "Sup3r-secret-pw")
    assert shell._account_command("login") is True
    assert sent[0][0] == "ACC:login" and "remember" in sent[0][1]
    assert account.password == "tok-from-server"
    assert homes.recall_login("ucla") == {"username": "alice", "secret": "tok-from-server"}
    assert "Sup3r-secret-pw" not in homes._login_file("ucla").read_text()


def test_login_flag_means_login_and_a_failure_asks_again(monkeypatch):
    from remoteRF.core import app as shell
    from remoteRF.core.grpc_acc import RemoteRFAccount as Account

    account = Account()
    attempts = []
    account.login_user = lambda **kw: attempts.append(kw) or len(attempts) == 2  # fail once
    monkeypatch.setattr(shell, "account", account)
    monkeypatch.setattr(shell, "home_name", None)
    monkeypatch.setattr(shell, "_ask", lambda prompt, **k: "alice")
    monkeypatch.setattr(shell, "session", mock.Mock(prompt=lambda *a, **k: pytest.fail("no l/r menu on -l")))
    assert shell.welcome(show_banner=False, initial=["login"]) is True
    assert len(attempts) == 2
