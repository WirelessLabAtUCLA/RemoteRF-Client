"""`remoterf --remote-admin`: pairing pins the lab's host key from the console's
code, logins exec OpenSSH strictly, and `--admin-pipe` pumps bytes."""

import io
import os
import shutil
import subprocess
from concurrent import futures
from unittest import mock

import grpc
import pytest

from remoterf import admin

LINE = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHk2h2pRk7h3V8m4c3d2b0wq4Wq9T5u7lXo2YxgM1sQe"


def test_split_login():
    assert admin.split_login("alice@ucla") == ("alice", "ucla")
    assert admin.split_login("jdoe@global@ucla") == ("jdoe@global", "ucla")
    assert admin.split_login("ucla") == (None, "ucla")
    assert admin.split_login("alice@192.0.2.7:12321") == ("alice", "192.0.2.7:12321")
    with pytest.raises(admin.AdminError):
        admin.split_login("alice@ucla; rm -rf /")


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs OpenSSH")
def test_fingerprint_matches_openssh(tmp_path):
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / "k")], check=True)
    line = " ".join((tmp_path / "k.pub").read_text().split()[:2])
    out = subprocess.run(["ssh-keygen", "-l", "-f", str(tmp_path / "k.pub")], capture_output=True, text=True).stdout
    assert admin.fingerprint(line) == out.split()[1]


def test_trust_line_replaces_only_that_lab():
    admin.trust_line("remoterf-a", LINE)
    admin.trust_line("remoterf-b", LINE)
    admin.trust_line("remoterf-a", LINE.replace("Hk2", "Xk2"))
    lines = admin.known_hosts_path().read_text().splitlines()
    assert lines == [f"remoterf-b {LINE}", f"remoterf-a {LINE.replace('Hk2', 'Xk2')}"]
    assert admin.trusted("remoterf-a") and not admin.trusted("remoterf-c")


def test_pair_refuses_a_host_key_the_code_does_not_name():
    with mock.patch.object(admin, "fetch_host_key", return_value=(LINE, "remoterf-x")), \
         mock.patch.object(admin, "_check_ssh"), mock.patch.object(admin, "_exec") as run:
        with pytest.raises(admin.AdminError, match="not the one the console"):
            admin.cmd_pair("alice@ucla", "K7Q2-9XPM-4TDA-HHWZ.AAAAAAAAAAAA")
        run.assert_not_called()
        assert not admin.trusted("remoterf-x")
        with pytest.raises(admin.AdminError, match="fingerprint"):
            admin.cmd_pair("alice@ucla", "K7Q2-9XPM-4TDA-HHWZ")


def test_pair_trusts_the_key_and_sends_the_code_inside_ssh():
    prefix = admin.fingerprint(LINE).split(":")[1][:12]
    with mock.patch.object(admin, "fetch_host_key", return_value=(LINE, "remoterf-x")), \
         mock.patch.object(admin, "_check_ssh"), mock.patch.object(admin, "ensure_device_key"), \
         mock.patch.object(admin, "_exec", return_value=0) as run:
        assert admin.cmd_pair("alice@ucla", f"K7Q2-9XPM-4TDA-HHWZ.{prefix}") == 0
    argv = run.call_args[0][0]
    assert argv[argv.index("-l") + 1] == "alice#K7Q2-9XPM-4TDA-HHWZ"
    assert "StrictHostKeyChecking=yes" in argv and "HostKeyAlias=remoterf-x" in argv
    assert admin.trusted("remoterf-x")
    assert admin.labs() == {"ucla": {"user": "alice", "alias": "remoterf-x"}}


def test_login_is_strict_once_trusted_and_warns_before_first_sight(capsys):
    admin._save_lab("ucla", {"user": "alice", "alias": "remoterf-x"})
    with mock.patch.object(admin, "_check_ssh"), mock.patch.object(admin, "ensure_device_key"), \
         mock.patch.object(admin, "_exec", return_value=0) as run:
        admin.cmd_login(None)
        argv = run.call_args[0][0]
        assert "StrictHostKeyChecking=ask" in argv and "not trusted" in capsys.readouterr().err
        admin.trust_line("remoterf-x", LINE)
        admin.cmd_login("ucla")
        argv = run.call_args[0][0]
    assert "StrictHostKeyChecking=yes" in argv and argv[argv.index("-l") + 1] == "alice"
    assert "-F" in argv and "IdentitiesOnly=yes" in argv and "ServerAliveInterval=30" in argv
    proxy = next(a for a in argv if a.startswith("ProxyCommand="))
    assert proxy.endswith("-m remoterf.admin --admin-pipe ucla")


def test_cli_routes_remote_admin(monkeypatch):
    from remoterf import remoterf_cli

    with mock.patch.object(admin, "main", return_value=7) as entry:
        monkeypatch.setattr("sys.argv", ["remoterf", "--remote-admin", "pair", "a@b", "c"])
        assert remoterf_cli.main() == 7
        entry.assert_called_with(["pair", "a@b", "c"])
        monkeypatch.setattr("sys.argv", ["remoterf", "--admin-pipe", "ucla"])
        remoterf_cli.main()
        entry.assert_called_with(["--admin-pipe", "ucla"])


def test_admin_pipe_pumps_bytes_both_ways():
    seen = {}

    def tunnel(requests, context):
        seen["label"] = dict(context.invocation_metadata()).get(admin.MD_LABEL)
        for chunk in requests:
            yield chunk.upper()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(
        "remote_rf.RemoteAdminV1", {"Tunnel": grpc.stream_stream_rpc_method_handler(tunnel)}),))
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    read, write = os.pipe()
    os.write(write, b"ssh-2.0-hello\n")
    os.close(write)
    saved = os.dup(0)
    out = mock.Mock(buffer=io.BytesIO())
    try:
        os.dup2(read, 0)
        with mock.patch.object(admin, "_channel", return_value=(grpc.insecure_channel(f"127.0.0.1:{port}"), "a")), \
             mock.patch("sys.stdout", out):
            assert admin.pipe("ucla") == 0
    finally:
        os.dup2(saved, 0)
        os.close(saved)
        os.close(read)
        server.stop(0)
    assert out.buffer.getvalue() == b"SSH-2.0-HELLO\n"
    assert seen["label"] == admin.device_label()


def test_lab_addresses():
    assert admin._address("192.0.2.7:12321") == ("192.0.2.7", 12321)
    assert admin._address("[2001:db8::7]:12321") == ("2001:db8::7", 12321)
    assert admin._address("192.0.2.7") == ("192.0.2.7", 12321)
    assert admin._address("ucla") is None
    with pytest.raises(admin.AdminError):
        admin._address("fe80::1")


def test_hostkey_says_when_the_lab_presents_another_key(capsys):
    admin.trust_line("remoterf-x", LINE)
    other = LINE.replace("Hk2", "Xk2")
    with mock.patch.object(admin, "fetch_host_key", return_value=(other, "remoterf-x")):
        admin.cmd_hostkey("ucla")
    assert "NOT the key trusted here" in capsys.readouterr().out


def test_paths_are_quoted_for_ssh_and_errors_never_trace(monkeypatch, capsys):
    argv = admin.ssh_argv("ucla", "alice", "remoterf-x")
    assert any(a.startswith('UserKnownHostsFile="') and a.endswith('known_hosts"') for a in argv)
    with mock.patch.object(admin, "cmd_trust", side_effect=ValueError("boom")):
        assert admin.main(["trust", "ucla", "SHA256:x"]) == 1
    assert "remoterf: boom" in capsys.readouterr().err
    with mock.patch.object(admin, "pipe", side_effect=KeyboardInterrupt), mock.patch("sys.stdout", io.StringIO()) as out:
        assert admin.main(["--admin-pipe", "ucla"]) == 130
    assert out.getvalue() == ""
