import json
import sys

import anyio
import pytest

pytest.importorskip("mcp")
from mcp.client import Client  # noqa: E402

from remoterf import mcp_server, ops  # noqa: E402

API = {"handle": "Pluto_00@ucla", "device_type": "pluto", "class_name": "adi.Pluto",
       "import_line": "from remoterf.drivers.adalm_pluto import adi", "constructor": 'adi.Pluto(token="Pluto_00@ucla")',
       "schema_version": "1.0", "methods": [], "notes": []}


def _talk(server, body):
    async def go():
        async with Client(server) as c:
            return await body(c)
    return anyio.run(go)


def _call(server, name, args=None):
    r = _talk(server, lambda c: c.call_tool(name, args or {}))
    return r.is_error, json.loads(r.content[0].text)


@pytest.fixture
def fake(monkeypatch):
    calls, sessions = [], []

    def record(name, result):
        def fn(*args, **kwargs):
            calls.append((name, args[1:], kwargs))
            return result
        monkeypatch.setattr(ops, name, fn)

    record("whoami", {"account": "jdoe@ucla", "home": "ucla", "home_kind": "deployment", "deployments": ["ucla"],
                      "client_version": "2.8.0", "reserve_enabled": True})
    record("list_devices", {"devices": [], "notes": [], "incomplete": False})
    record("find_slots", {"handle": "Pluto_00@ucla", "slot_minutes": 30, "timezone": "UTC", "days": 2, "slots": []})
    record("list_reservations", {"reservations": [], "notes": []})
    record("reserve", {"reservation_id": "7", "handle": "Pluto_00@ucla", "token_ref": "7", "use_as_token": "Pluto_00@ucla"})
    record("cancel", {"cancelled": True, "reservation_id": "7"})
    record("device_api", API)

    def session():
        sessions.append(1)
        return "session"

    return calls, sessions, session


def test_tools_match_catalogue():
    server = mcp_server.build_server(allow_reserve=False, session_factory=lambda: None)
    tools = _talk(server, lambda c: c.list_tools()).tools
    assert [t.name for t in tools] == [t["name"] for t in mcp_server.tool_catalogue()]
    for tool, spec in zip(tools, mcp_server.tool_catalogue()):
        assert (tool.title, tool.description, tool.input_schema) == (spec["title"], spec["description"], spec["input_schema"])
        assert tool.annotations.model_dump(by_alias=True, exclude_none=True) == spec["annotations"]


def test_read_tools_call_ops_with_a_fresh_session(fake):
    calls, sessions, session = fake
    server = mcp_server.build_server(allow_reserve=False, session_factory=session)
    assert sessions == []
    assert _call(server, "remoterf_whoami") == (False, {"account": "jdoe@ucla", "home": "ucla", "home_kind": "deployment",
                                                        "deployments": ["ucla"], "client_version": "2.8.0",
                                                        "reserve_enabled": False})
    assert _call(server, "remoterf_list_devices", {"deployment": "ucla", "type": "pluto"})[1]["incomplete"] is False
    assert _call(server, "remoterf_find_slots", {"handle": "Pluto_00@ucla", "from": "2026-10-15", "days": 2,
                                                 "timezone": "UTC"})[1]["slot_minutes"] == 30
    assert _call(server, "remoterf_list_reservations", {"mine_only": False})[1]["reservations"] == []
    assert _call(server, "remoterf_device_api", {"handle": "Pluto_00@ucla"}) == (False, API)
    assert len(sessions) == 5
    assert calls[1:] == [
        ("list_devices", (), {"deployment": "ucla", "online_only": True, "type": "pluto"}),
        ("find_slots", ("Pluto_00@ucla",), {"start_day": "2026-10-15", "days": 2, "tz": "UTC"}),
        ("list_reservations", (), {"mine_only": False}),
        ("device_api", ("Pluto_00@ucla",), {}),
    ]


def test_reserve_and_cancel_need_allow_reserve(fake):
    calls, _, session = fake
    off = mcp_server.build_server(allow_reserve=False, session_factory=session)
    for name, args in (("remoterf_reserve", {"handle": "Pluto_00@ucla", "start": "2026-10-15T21:00:00Z",
                                             "end": "2026-10-15T21:30:00Z"}),
                       ("remoterf_cancel", {"reservation_id": "7"})):
        is_error, body = _call(off, name, args)
        assert is_error and body["error"]["code"] == "reserve_disabled" and not body["error"]["retryable"]
    assert calls == []

    on = mcp_server.build_server(allow_reserve=True, session_factory=session)
    assert _call(on, "remoterf_reserve", {"handle": "Pluto_00@ucla", "start": "2026-10-15T21:00:00Z",
                                          "end": "2026-10-15T21:30:00Z", "reveal_token": True})[1]["token_ref"] == "7"
    assert _call(on, "remoterf_cancel", {"reservation_id": "7"}) == (False, {"cancelled": True, "reservation_id": "7"})
    assert calls == [("reserve", ("Pluto_00@ucla", "2026-10-15T21:00:00Z", "2026-10-15T21:30:00Z"), {"reveal_token": True}),
                     ("cancel", ("7",), {})]
    assert _call(on, "remoterf_whoami")[1]["reserve_enabled"] is True


def test_errors_are_json_and_hide_internals(monkeypatch):
    def limited(s, **kw):
        raise ops.RemoteRFOpsError("rate_limited", "Asked too recently; try again in 10 s.", retry_after=10)

    def broken(s, **kw):
        raise KeyError("secret detail")

    def no_login():
        raise ops.RemoteRFOpsError("not_logged_in", ops.NOT_LOGGED_IN)

    server = mcp_server.build_server(allow_reserve=False, session_factory=lambda: None)
    monkeypatch.setattr(ops, "list_devices", limited)
    assert _call(server, "remoterf_list_devices") == (True, {"error": {
        "code": "rate_limited", "message": "Asked too recently; try again in 10 s.", "retryable": True, "retry_after": 10}})
    monkeypatch.setattr(ops, "list_reservations", broken)
    is_error, body = _call(server, "remoterf_list_reservations")
    assert is_error and body["error"]["code"] == "internal" and "secret" not in json.dumps(body)
    server = mcp_server.build_server(allow_reserve=False, session_factory=no_login)
    assert _call(server, "remoterf_whoami")[1]["error"]["code"] == "not_logged_in"


def test_stray_print_never_reaches_stdout(monkeypatch, capsys):
    def chatty(s, **kw):
        print("driver chatter")
        return {"devices": [], "notes": [], "incomplete": False}

    monkeypatch.setattr(ops, "list_devices", chatty)
    server = mcp_server.build_server(allow_reserve=False, session_factory=lambda: None)
    assert _call(server, "remoterf_list_devices")[0] is False
    out, err = capsys.readouterr()
    assert "driver chatter" not in out and "driver chatter" in err


def test_guide_example_resources_and_prompts(fake, monkeypatch):
    _, _, session = fake
    server = mcp_server.build_server(allow_reserve=False, session_factory=session)
    with mcp_server._lock:  # a lab call that hangs must not hold the guide
        assert "workflow" in _call(server, "remoterf_guide")[1]["topics"]
    assert "mcp" in _call(server, "remoterf_guide")[1]["topics"]
    assert _call(server, "remoterf_guide", {"topic": "nope"})[1]["error"]["code"] == "invalid_argument"
    script = _call(server, "remoterf_example_script", {"handle": "Pluto_00@ucla", "recipe": "connect"})[1]
    assert API["import_line"] in script["code"]
    compile(script["code"], script["filename"], "exec")

    async def body(c):
        guide = await c.read_resource("remoterf://guide")
        tokens = await c.read_resource("remoterf://docs/tokens")
        api = await c.read_resource("remoterf://devices/Pluto_00@ucla/api")
        prompts = await c.list_prompts()
        prompt = await c.get_prompt("reserve-and-receive", {"device": "Pluto_00@ucla"})
        return guide, tokens, api, prompts, prompt

    guide, tokens, api, prompts, prompt = _talk(server, body)
    assert "## tokens" in guide.contents[0].text and tokens.contents[0].text.startswith("## tokens")
    assert json.loads(api.contents[0].text) == API
    assert [p.name for p in prompts.prompts] == ["reserve-and-receive", "explain-my-reservations"]
    assert "Pluto_00@ucla" in prompt.messages[0].content.text
    monkeypatch.setattr(ops, "device_api", lambda s, h: {**API, "device_type": "Bad Type"})
    assert _call(server, "remoterf_example_script", {"handle": "Pluto_00@ucla"})[1]["error"]["code"] == "error"


def test_install_merges_json_and_toml(isolated_home, capsys):
    gemini = isolated_home / ".gemini" / "settings.json"
    gemini.parent.mkdir()
    gemini.write_text(json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "other"}}}))
    assert mcp_server.main(["mcp", "install", "--gemini"]) == 0
    data = json.loads(gemini.read_text())
    assert data["theme"] == "dark" and data["mcpServers"]["other"] == {"command": "other"}
    command = data["mcpServers"]["remoterf"]["command"]
    assert data["mcpServers"]["remoterf"]["args"] == ["mcp"] and command.endswith(("remoterf", "remoterf.exe"))
    assert json.loads((gemini.parent / "settings.json.bak").read_text())["theme"] == "dark"

    assert mcp_server.main(["install", "--cursor"]) == 0
    assert json.loads((isolated_home / ".cursor" / "mcp.json").read_text())["mcpServers"]["remoterf"]["command"] == command

    codex = isolated_home / ".codex" / "config.toml"
    codex.parent.mkdir()
    codex.write_text('model = "o3"\n\n[mcp_servers.other]\ncommand = "other"\n\n'
                     '[mcp_servers.remoterf]\ncommand = "stale"\nargs = []\n\n[mcp_servers.remoterf.env]\nX = "1"\n')
    if sys.version_info < (3, 11):  # no tomllib: a merge it can't check is refused
        assert mcp_server.main(["install", "--codex"]) == 1 and "stale" in codex.read_text()
    else:
        import tomllib

        assert mcp_server.main(["install", "--codex"]) == 0
        assert mcp_server.main(["install", "--codex"]) == 0
        text = codex.read_text()
        assert text.count("[mcp_servers.remoterf]") == 1 and "stale" not in text
        servers = tomllib.loads(text)["mcp_servers"]
        assert servers["other"] == {"command": "other"}
        assert servers["remoterf"] == {"command": command, "args": ["mcp"], "env": {"X": "1"}}

    capsys.readouterr()
    assert mcp_server.main(["install", "--claude-code"]) == 0
    assert capsys.readouterr().out.startswith("claude mcp add --scope user remoterf -- ")
    assert mcp_server.main(["install", "--claude-desktop", "--print"]) == 0
    assert json.loads(capsys.readouterr().out)["mcpServers"]["remoterf"]["command"] == command
    assert not (isolated_home / "Library").exists() and not (isolated_home / ".config" / "Claude").exists()


def test_install_refuses_a_file_it_cannot_parse(isolated_home):
    cursor = isolated_home / ".cursor" / "mcp.json"
    cursor.parent.mkdir()
    cursor.write_text("{ not json")
    assert mcp_server.main(["install", "--cursor"]) == 1
    assert cursor.read_text() == "{ not json" and not (cursor.parent / "mcp.json.bak").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and symlinks")
def test_install_writes_through_a_symlink_and_keeps_the_mode(isolated_home):
    real = isolated_home / "dotfiles" / "mcp.json"
    real.parent.mkdir()
    real.write_text(json.dumps({"mcpServers": {"other": {"command": "other", "env": {"KEY": "x"}}}}))
    real.chmod(0o600)
    link = isolated_home / ".cursor" / "mcp.json"
    link.parent.mkdir()
    link.symlink_to(real)
    assert mcp_server.main(["install", "--cursor"]) == 0
    assert link.is_symlink() and "remoterf" in json.loads(real.read_text())["mcpServers"]
    assert real.stat().st_mode & 0o777 == 0o600
