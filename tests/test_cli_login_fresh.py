"""`remoterf -l` on a fresh install is `remoterf -r`; a configured client logs in."""

import os
import tempfile
from unittest import mock


def _fresh_home():
    tmp = tempfile.TemporaryDirectory()
    env = {k: v for k, v in os.environ.items() if not k.startswith("REMOTERF_")}
    env["HOME"] = tmp.name
    return tmp, mock.patch.dict(os.environ, env, clear=True)


def test_login_on_a_fresh_install_registers_on_enter():
    from remoterf import remoterf_cli

    tmp, env = _fresh_home()
    with tmp, env, \
            mock.patch("remoterf.config.config.require_tos_for_login", return_value=True), \
            mock.patch("builtins.input", return_value=""), \
            mock.patch.object(remoterf_cli, "_register", return_value=0) as register, \
            mock.patch.object(remoterf_cli, "_account_shell") as shell:
        assert remoterf_cli._login(None) == 0
    register.assert_called_once_with()
    shell.assert_not_called()


def test_an_account_name_says_where_it_lives():
    from remoterf.deployment.homes import split_account

    assert split_account(" jdoe@ucla ") == ("jdoe", "ucla")
    assert split_account("JDoe@UCLA") == ("JDoe", "ucla")
    assert split_account("jdoe@global") == ("jdoe", "global")
    assert split_account("jdoe") == ("jdoe", None)
    assert split_account("jdoe@ucla.edu") == ("jdoe@ucla.edu", None)  # an email
    assert split_account("a@b@ucla") == ("a@b", "ucla")
    assert split_account("@ucla") == ("@ucla", None)
    assert split_account("jdoe@") == ("jdoe@", None)


def test_login_on_a_fresh_install_goes_to_the_lab_in_the_account_name():
    from remoterf import remoterf_cli

    tmp, env = _fresh_home()
    route = {"kind": "relay", "host": "ucla.global.remoterf.net", "port": 12321}
    with tmp, env, \
            mock.patch("remoterf.config.config.require_tos_for_login", return_value=True), \
            mock.patch("builtins.input", return_value="jdoe@ucla"), \
            mock.patch("remoterf.deployment.homes.register_home",
                       return_value=("ucla", {"display_name": "UCLA"}, route)) as register_home, \
            mock.patch.object(remoterf_cli, "_account_shell", return_value=0) as shell:
        assert remoterf_cli._login(None) == 0
    register_home.assert_called_once_with("ucla.global.remoterf.net")
    assert shell.call_args.kwargs["register"] is False
    assert shell.call_args.kwargs["home"] == "ucla"
    assert shell.call_args.kwargs["username"] == "jdoe"


def test_login_on_a_fresh_install_global_bare_names_and_emails_go_to_global():
    from remoterf import remoterf_cli

    for typed, username in (("jdoe@global", "jdoe"), ("jdoe", "jdoe"), ("jdoe@ucla.edu", "jdoe@ucla.edu")):
        tmp, env = _fresh_home()
        with tmp, env, \
                mock.patch("remoterf.config.config.require_tos_for_login", return_value=True), \
                mock.patch("builtins.input", return_value=typed), \
                mock.patch("remoterf.deployment.homes.register_home") as register_home, \
                mock.patch.object(remoterf_cli, "_use_global", return_value=0) as use_global:
            assert remoterf_cli._login(None) == 0
        use_global.assert_called_once_with(register=False, username=username)
        register_home.assert_not_called()


def test_login_to_a_lab_that_does_not_answer():
    from remoterf import remoterf_cli

    tmp, env = _fresh_home()
    with tmp, env, \
            mock.patch("remoterf.config.config.require_tos_for_login", return_value=True), \
            mock.patch("builtins.input", return_value="jdoe@nolab"), \
            mock.patch("remoterf.deployment.homes.register_home",
                       side_effect=RuntimeError("no such deployment")) as register_home, \
            mock.patch.object(remoterf_cli, "_account_shell") as shell:
        assert remoterf_cli._login(None) == 2
    register_home.assert_called_once_with("nolab.global.remoterf.net")
    shell.assert_not_called()


def test_login_with_an_account_uses_the_saved_home():
    from remoterf import remoterf_cli

    tmp, env = _fresh_home()
    with tmp, env, \
            mock.patch("remoterf.config.config.require_tos_for_login", return_value=True), \
            mock.patch("remoterf.deployment.homes.homes", return_value={"ucla": {}}), \
            mock.patch("remoterf.deployment.homes.register_home") as register_home, \
            mock.patch.object(remoterf_cli, "_use_home", return_value=0) as use_home:
        assert remoterf_cli._login("jdoe@ucla") == 0
    use_home.assert_called_once_with("ucla", register=False, username="jdoe")
    register_home.assert_not_called()


def test_login_global_on_a_fresh_install_logs_in_to_global():
    from remoterf import remoterf_cli

    tmp, env = _fresh_home()
    with tmp, env, \
            mock.patch("remoterf.config.config.require_tos_for_login", return_value=True), \
            mock.patch.object(remoterf_cli, "_use_global", return_value=0) as use_global:
        assert remoterf_cli._login("global") == 0
    use_global.assert_called_once_with(register=False)


def test_login_with_a_legacy_address_still_logs_in_there():
    from remoterf import remoterf_cli

    tmp, env = _fresh_home()
    with tmp, env, \
            mock.patch("remoterf.config.config.require_tos_for_login", return_value=True), \
            mock.patch.object(remoterf_cli, "_ensure_config_present", return_value=(True, "")), \
            mock.patch.object(remoterf_cli, "_register") as register, \
            mock.patch.object(remoterf_cli, "_account_shell", return_value=0) as shell:
        assert remoterf_cli._login(None) == 0
    shell.assert_called_once_with(register=False)
    register.assert_not_called()


def test_register_defaults_to_a_global_account_without_asking_for_a_code(monkeypatch):
    from remoterf import remoterf_cli

    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(AssertionError(prompt)))
    with mock.patch("remoterf.config.config._confirm_tos", return_value=True), \
            mock.patch("remoterf.config.config.forget_tos_agreement"), \
            mock.patch("remoterf.config.config.remember_tos_agreement"), \
            mock.patch.object(remoterf_cli, "print_client_banner"), \
            mock.patch.object(remoterf_cli, "_use_global", return_value=0) as use_global, \
            mock.patch.object(remoterf_cli, "_account_shell") as shell:
        assert remoterf_cli._register() == 0
    use_global.assert_called_once_with(register=True, show_banner=False)
    shell.assert_not_called()


def test_register_global_asks_for_the_tos_like_register(monkeypatch):
    from remoterf import remoterf_cli

    for arg in ("global", "-global"):
        monkeypatch.setattr("sys.argv", ["remoterf", "-r", arg])
        with mock.patch("remoterf.config.config._confirm_tos", return_value=True) as confirm, \
                mock.patch("remoterf.config.config.forget_tos_agreement") as forget, \
                mock.patch("remoterf.config.config.remember_tos_agreement") as remember, \
                mock.patch.object(remoterf_cli, "print_client_banner"), \
                mock.patch.object(remoterf_cli, "_use_global", return_value=0) as use_global:
            assert remoterf_cli.main() == 0
        forget.assert_called_once_with()
        confirm.assert_called_once()
        remember.assert_called_once_with()
        use_global.assert_called_once_with(register=True, show_banner=False)

    # Declining stops before any account is made.
    monkeypatch.setattr("sys.argv", ["remoterf", "-r", "global"])
    with mock.patch("remoterf.config.config._confirm_tos", return_value=False), \
            mock.patch("remoterf.config.config.forget_tos_agreement"), \
            mock.patch("remoterf.config.config.remember_tos_agreement") as remember, \
            mock.patch.object(remoterf_cli, "print_client_banner"), \
            mock.patch.object(remoterf_cli, "_use_global") as use_global:
        assert remoterf_cli.main() == 1
    remember.assert_not_called()
    use_global.assert_not_called()


def test_reset_password_uses_the_global_email_code_flow(monkeypatch):
    from remoterf import remoterf_cli

    backend = mock.Mock()
    monkeypatch.setattr("builtins.input", lambda prompt="": "ethange@example.org")
    monkeypatch.setattr(remoterf_cli.getpass, "getpass", mock.Mock(side_effect=["reset-code", "Sup3r-secret-pw", "Sup3r-secret-pw"]))
    with mock.patch("remoterf.deployment.backend.HttpsJsonAccountBackend", return_value=backend), \
            mock.patch.object(remoterf_cli, "print_client_banner"):
        assert remoterf_cli._reset_password() == 0
    backend.forgot.assert_called_once_with("ethange@example.org")
    backend.reset.assert_called_once_with("reset-code", "Sup3r-secret-pw")
    backend.close.assert_called_once_with()


def test_reset_pass_dispatches_without_an_existing_login(monkeypatch):
    from remoterf import remoterf_cli

    monkeypatch.setattr("sys.argv", ["remoterf", "--reset-pass"])
    with mock.patch.object(remoterf_cli, "_reset_password", return_value=0) as reset:
        assert remoterf_cli.main() == 0
    reset.assert_called_once_with()


def _login_at(home, typed, *, https=False, pending=None):
    from remoterf.core import app

    account = mock.Mock(is_https_home=https, backend=None)
    account.login_user.return_value = True
    with mock.patch.object(app, "account", account), \
            mock.patch.object(app, "home_name", home), \
            mock.patch.object(app, "pending_username", pending), \
            mock.patch.object(app, "local_capabilities",
                              return_value={"registration_policy": {}}), \
            mock.patch.object(app, "_home_name", return_value=home), \
            mock.patch.object(app, "_remember_login"), \
            mock.patch.object(app, "_ask", side_effect=[typed, "secret"] if typed else ["secret"]) as ask:
        if https:
            account.backend = mock.Mock(capabilities={"registration_policy": {}})
        assert app._account_command("login") is True
        assert app.pending_username is None
    return account.username, ask


def test_a_found_username_is_not_asked_again():
    username, ask = _login_at("ucla", None, pending="jdoe")
    assert username == "jdoe"
    ask.assert_called_once_with("Password (Hidden)", hidden=True)


def test_the_full_account_name_works_at_its_own_home_only():
    assert _login_at("ucla", "jdoe@ucla")[0] == "jdoe"
    assert _login_at("ucla", "jdoe")[0] == "jdoe"
    assert _login_at("otheruni", "jdoe@ucla")[0] == "jdoe@ucla"  # not this lab's: no such name here
    assert _login_at(None, "jdoe@global", https=True)[0] == "jdoe"
    assert _login_at(None, "jdoe@ucla.edu", https=True)[0] == "jdoe@ucla.edu"  # an email
