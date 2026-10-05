"""`remoterf -l` on a fresh install is `remoterf -r`; a configured client logs in."""

import os
import tempfile
from unittest import mock


def _fresh_home():
    tmp = tempfile.TemporaryDirectory()
    env = {k: v for k, v in os.environ.items() if not k.startswith("REMOTERF_")}
    env["HOME"] = tmp.name
    return tmp, mock.patch.dict(os.environ, env, clear=True)


def test_login_on_a_fresh_install_registers():
    from remoteRF import remoterf_cli

    tmp, env = _fresh_home()
    with tmp, env, \
            mock.patch("remoteRF.config.config.require_tos_for_login", return_value=True), \
            mock.patch.object(remoterf_cli, "_register", return_value=0) as register, \
            mock.patch.object(remoterf_cli, "_account_shell") as shell:
        assert remoterf_cli._login(None) == 0
    register.assert_called_once_with()
    shell.assert_not_called()


def test_login_with_a_legacy_address_still_logs_in_there():
    from remoteRF import remoterf_cli

    tmp, env = _fresh_home()
    with tmp, env, \
            mock.patch("remoteRF.config.config.require_tos_for_login", return_value=True), \
            mock.patch.object(remoterf_cli, "_ensure_config_present", return_value=(True, "")), \
            mock.patch.object(remoterf_cli, "_register") as register, \
            mock.patch.object(remoterf_cli, "_account_shell", return_value=0) as shell:
        assert remoterf_cli._login(None) == 0
    shell.assert_called_once_with(register=False)
    register.assert_not_called()


def test_register_without_a_code_makes_a_global_account(monkeypatch):
    from remoteRF import remoterf_cli

    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    with mock.patch("remoteRF.config.config._confirm_tos", return_value=True), \
            mock.patch("remoteRF.config.config.forget_tos_agreement"), \
            mock.patch("remoteRF.config.config.remember_tos_agreement"), \
            mock.patch.object(remoterf_cli, "print_client_banner"), \
            mock.patch.object(remoterf_cli, "_use_global", return_value=0) as use_global, \
            mock.patch.object(remoterf_cli, "_account_shell") as shell:
        assert remoterf_cli._register() == 0
    use_global.assert_called_once_with(register=True, show_banner=False)
    shell.assert_not_called()
