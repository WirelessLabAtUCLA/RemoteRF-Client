"""`remoterf guide|api|example|skill` and the pure guide()/example_script()."""

import json
import re

import pytest

from remoterf import agent_docs, ops
from remoterf.agent_docs import RECIPES, example_script, guide, main

_TX = re.compile(r"\btx\b|tx_|_tx|transmit|send_waveform|dds")


def test_guide_topics_and_sections():
    whole = guide()
    assert whole["topic"] is None and whole["topics"] == ["workflow", "tokens", "cli", "mcp", "safety", "errors"]
    for topic in whole["topics"]:
        part = guide(topic.upper())
        assert part["topic"] == topic and part["text"].startswith(f"## {topic}\n") and part["text"] in whole["text"]
    with pytest.raises(ops.RemoteRFOpsError) as exc:
        guide("nope")
    assert exc.value.exit_code == 2


def test_guide_covers_every_exit_code_and_mcp_tool():
    errors, mcp = guide("errors")["text"], guide("mcp")["text"]
    assert all(code in errors for code in ops.EXIT_CODES)
    tools = json.loads((agent_docs.GUIDE_PATH.parent / "mcp_tools.json").read_text())["tools"]
    assert all(f"`{t['name']}`" in mcp for t in tools)


@pytest.mark.parametrize("recipe", RECIPES)
@pytest.mark.parametrize("device_type,class_name,import_line,ctor", [
    ("pluto", "adi.Pluto", "from remoterf.drivers.adalm_pluto import adi", "sdr = adi.Pluto(token=TOKEN)"),
    ("usrp", "uhd.usrp.MultiUSRP", "from remoterf.drivers.usrp import uhd", "sdr = uhd.usrp.MultiUSRP(token=TOKEN)"),
    ("hackrf", "HackRF", "from remoterf.drivers.hackrf import HackRF", "sdr = HackRF(token=TOKEN)"),
    ("acme_scope", None, "from remoterf.drivers.acme_scope import AcmeScope", "sdr = AcmeScope(token=TOKEN)"),
])
def test_examples_compile_receive_only(device_type, class_name, import_line, ctor, recipe):
    ex = example_script(device_type, class_name, "Dev'\n1@ucla", recipe, token_ref="42")
    code = ex["code"]
    compile(code, ex["filename"], "exec")
    assert import_line in code and ctor in code and "finally:" in code
    assert """os.environ.get("REMOTERF_TOKEN", "Dev'\\n1@ucla")""" in code
    assert not _TX.search(code.lower())
    assert ("ensure_driver(token=TOKEN)" in code) == (device_type not in ("pluto", "usrp"))
    assert ex["filename"].endswith(".py") and "REMOTERF_TOKEN" in ex["run_instructions"]


def test_example_rejects_lab_text_that_is_not_python():
    with pytest.raises(ValueError):
        example_script("x; import os", None, "A@b", "connect")
    with pytest.raises(ValueError):
        example_script("scope", "Scope()\nimport os", "A@b", "connect")
    with pytest.raises(ValueError):
        example_script("scope", "Scope", "A@b", "connect", import_line="import os")
    with pytest.raises(ValueError):
        example_script("pluto", None, "A@b", "tx_tone")


def test_skill_install_claude_and_marked_blocks(isolated_home, tmp_path, capsys):
    assert main(["skill", "install"]) == 0
    skill = (isolated_home / ".claude/skills/remoterf/SKILL.md").read_text()
    assert skill.startswith("---\nname: remoterf\ndescription: ") and guide()["text"] in skill

    project = tmp_path / "proj"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Mine\nkeep me\n")
    assert main(["skill", "install", "--agents-md", "--gemini", "--dir", str(project)]) == 0
    assert main(["skill", "install", "--agents-md", "--dir", str(project)]) == 0
    agents = (project / "AGENTS.md").read_text()
    assert agents.startswith("# Mine\nkeep me\n\n<!-- remoterf:begin -->") and agents.count("remoterf:begin") == 1
    assert "## safety" in (project / "GEMINI.md").read_text()
    out = capsys.readouterr().out
    assert str(project / "AGENTS.md") in out and "unchanged" in out

    assert main(["skill", "install", "--codex"]) == 0
    assert (isolated_home / ".codex/AGENTS.md").read_text().startswith("<!-- remoterf:begin -->")

    skill_path = isolated_home / ".claude/skills/remoterf/SKILL.md"
    skill_path.write_text("my edits")
    assert main(["skill", "install"]) == 0
    assert skill_path.with_name("SKILL.md.bak").read_text() == "my edits"
    orphan = project / "AGENTS.md"
    orphan.write_text("<!-- remoterf:begin -->\nmine, no end marker\n")
    assert main(["skill", "install", "--agents-md", "--dir", str(project)]) == 1
    assert orphan.read_text() == "<!-- remoterf:begin -->\nmine, no end marker\n"


_API = {"handle": "Pluto_00@ucla", "device_type": "pluto", "class_name": "adi.Pluto",
        "import_line": "from remoterf.drivers.adalm_pluto import adi", "constructor": 'sdr = adi.Pluto(token="Pluto_00@ucla")',
        "schema_version": "1.0", "methods": [{"name": "rx_lo", "kind": "getter", "doc": "RX LO in Hz."}], "notes": []}


def test_api_and_example_commands(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ops, "saved_session", lambda: "session")
    monkeypatch.setattr(ops, "device_api", lambda s, h: dict(_API, handle=h))
    assert main(["api", "Pluto_00@ucla", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["handle"] == "Pluto_00@ucla"
    assert main(["api", "Pluto_00@ucla"]) == 0
    assert "rx_lo  RX LO in Hz." in capsys.readouterr().out

    out = tmp_path / "rx.py"
    assert main(["example", "Pluto_00@ucla", "--recipe", "rx_spectrum", "-o", str(out)]) == 0
    compile(out.read_text(), str(out), "exec")
    captured = capsys.readouterr()
    assert captured.out == "" and f"python {out}" in captured.err


def test_api_error_is_json_with_exit_code(monkeypatch, capsys):
    def no_login():
        raise ops.RemoteRFOpsError("not_logged_in")

    monkeypatch.setattr(ops, "saved_session", no_login)
    assert main(["api", "Pluto_00@ucla", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "not_logged_in"
    assert main(["guide", "nope"]) == 2
    capsys.readouterr()
    assert main(["api", "--json"]) == 2  # a usage error is JSON too
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "usage"


def test_api_text_strips_terminal_escapes(monkeypatch, capsys):
    monkeypatch.setattr(ops, "saved_session", lambda: "session")
    evil = dict(_API, methods=[{"name": "rx_lo", "kind": "getter", "doc": "RX\x1b]52;c;aGk=\x07 LO"}])
    monkeypatch.setattr(ops, "device_api", lambda s, h: evil)
    assert main(["api", "Pluto_00@ucla"]) == 0
    assert "\x1b" not in capsys.readouterr().out
