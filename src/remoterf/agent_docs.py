# Copyright (C) 2026 RemoteRF
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Docs for AI agents: `remoterf guide|api|example|skill`, and the guide()
and example_script() the MCP servers serve."""

import json
import re
import shutil
import sys
from pathlib import Path

RECIPES = ("connect", "rx_once", "rx_spectrum")
GUIDE_PATH = Path(__file__).with_name("data") / "agent_guide.md"
_SECTION = re.compile(r"^## (\S+)\n", re.M)
_BEGIN, _END = "<!-- remoterf:begin -->", "<!-- remoterf:end -->"
_SKILL_HEAD = """---
name: remoterf
description: Use RemoteRF remote radios (ADALM-Pluto, USRP, RTL-SDR, HackRF) from Python. Use when the user wants to find RemoteRF devices or free slots, reserve or cancel a RemoteRF reservation, learn a device's Python API, or write a receive script for a remote SDR.
---

"""


def guide(topic=None) -> dict:
    """The whole guide, or one `## topic` section of it."""
    text = GUIDE_PATH.read_text(encoding="utf-8")
    parts = _SECTION.split(text)
    sections = {name: f"## {name}\n{body.rstrip()}\n" for name, body in zip(parts[1::2], parts[2::2])}
    if not topic:
        return {"topic": None, "text": text, "topics": list(sections)}
    key = str(topic).strip().lower()
    if key not in sections:
        from .ops import RemoteRFOpsError

        raise RemoteRFOpsError("invalid_argument", f"Unknown guide topic; topics: {', '.join(sections)}.")
    return {"topic": key, "text": sections[key], "topics": list(sections)}


def example_script(device_type, class_name, handle, recipe, token_ref=None, import_line=None) -> dict:
    """A receive-only script for one device. Stdlib only and self-contained:
    remoterf-vps-global keeps a copy. Lab-supplied names are validated before
    they reach code; the handle goes in only as a string literal."""
    if recipe not in RECIPES:
        raise ValueError(f"recipe must be one of {', '.join(RECIPES)}")
    device_type = str(device_type or "")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", device_type):
        raise ValueError("device type is not a driver package name")
    fixed = {"pluto": ("adalm_pluto import adi", "adi.Pluto"), "adalm_pluto": ("adalm_pluto import adi", "adi.Pluto"),
             "usrp": ("usrp import uhd", "uhd.usrp.MultiUSRP")}
    if device_type in fixed:
        imp, cls = fixed[device_type]
        import_line = f"from remoterf.drivers.{imp}"
    else:
        cls = class_name or "".join(p[:1].upper() + p[1:] for p in device_type.split("_") if p)
        import_line = import_line or f"from remoterf.drivers.{device_type} import {cls}"
    if not (all(p.isidentifier() for p in cls.split("."))
            and re.fullmatch(r"from remoterf\.drivers\.[a-z0-9_.]+ import \w+(, \w+)*", import_line)):
        raise ValueError("driver class or import line is not valid Python")

    # Receive steps per driver family; each leaves the IQ in `samples`.
    capture = {
        "adi": ["sdr.sample_rate = RATE", "sdr.rx_lo = FREQ", "sdr.rx_buffer_size = N", "samples = sdr.rx()"],
        "uhd": ["samples = sdr.recv_num_samps(N, FREQ, RATE)"],
        "read": ["sdr.sample_rate = RATE", "sdr.center_freq = FREQ", "samples = sdr.read_samples(N)"],
    }
    show = {
        "adi": ['print("rx_lo", sdr.rx_lo, "sample_rate", sdr.sample_rate)'],
        "uhd": ["print(sdr.get_pp_string())"],
        "read": ['print("center_freq", sdr.center_freq, "sample_rate", sdr.sample_rate)'],
        None: ['print("connected; API:", ", ".join(n for n in dir(sdr) if not n.startswith("_")))'],
    }
    family = {"pluto": "adi", "adalm_pluto": "adi", "sim_sdr": "adi", "usrp": "uhd",
              "rtl_sdr": "read", "hackrf": "read"}.get(device_type)
    note = ""
    if family is None and recipe != "connect":
        note = f"No {recipe} recipe for {device_type} devices yet: this script connects and lists the driver's API. "
        recipe = "connect"
    rx = recipe != "connect"
    what = {"connect": "connect and show the device settings",
            "rx_once": "receive N samples and print their mean power",
            "rx_spectrum": "receive N samples and print the strongest frequencies"}[recipe]

    lines = [f'"""RemoteRF {recipe} script, device type {device_type}: {what}. Receive only.',
             "", "Run it inside the reservation window. TOKEN defaults to the device handle,",
             "which needs a saved RemoteRF login; set REMOTERF_TOKEN to use a token instead.", '"""',
             "import os", ""]
    if rx:
        lines.append("import numpy as np")
    if device_type in fixed:
        lines += [import_line, "", f"TOKEN = os.environ.get(\"REMOTERF_TOKEN\", {str(handle)!r})"]
    else:
        lines += ["from remoterf.drivers import ensure_driver", "",
                  f"TOKEN = os.environ.get(\"REMOTERF_TOKEN\", {str(handle)!r})",
                  "ensure_driver(token=TOKEN)  # installs this device's driver once per machine",
                  f"{import_line}  # noqa: E402"]
    if rx:
        lines += ["FREQ = 915_000_000  # Hz", "RATE = 2_000_000  # samples per second", "N = 4096"]
    lines += ["", f"sdr = {cls}(token=TOKEN)", "try:"]
    body = (capture[family] + ["x = np.asarray(samples)", "x = x[0] if x.ndim > 1 else x"]) if rx else show[family]
    if recipe == "rx_once":
        body += ["power = 10 * np.log10(np.mean(np.abs(x) ** 2) + 1e-20)",
                 'print(f"{x.size} samples at {FREQ / 1e6:g} MHz, mean power {power:.1f} dB")']
    elif recipe == "rx_spectrum":
        body += ["psd = 20 * np.log10(np.abs(np.fft.fftshift(np.fft.fft(x * np.hanning(x.size)))) / x.size + 1e-20)",
                 "freqs = FREQ + np.fft.fftshift(np.fft.fftfreq(x.size, 1 / RATE))",
                 "for i in np.argsort(psd)[::-1][:5]:",
                 '    print(f"{freqs[i] / 1e6:10.4f} MHz  {psd[i]:6.1f} dB")']
    lines += ["    " + line for line in body]
    lines += ["finally:"]
    lines += ["    sdr.rx_destroy_buffer()"] if cls == "adi.Pluto" else ['    if hasattr(sdr, "close"):', "        sdr.close()"]

    filename = f"{device_type}_{recipe}.py"
    ref = f" (reservation {token_ref})" if token_ref else ""
    run = (f"{note}Save it as {filename} and run `python {filename}` on your own machine inside the reservation "
           f"window{ref}. With a saved RemoteRF login it uses the handle {handle}; on another machine first set "
           "REMOTERF_TOKEN to the reservation token. Needs `pip install remoterf`.")
    return {"filename": filename, "code": "\n".join(lines) + "\n", "run_instructions": run}


def _skill_install(args):
    text = GUIDE_PATH.read_text(encoding="utf-8")
    base = Path(args.dir).expanduser() if args.dir else None
    home = Path.home()
    targets = {}  # path -> written as a marked block inside a shared file?
    if args.claude or not (args.codex or args.gemini or args.agents_md):
        targets[(base / ".claude" if base else home / ".claude") / "skills" / "remoterf" / "SKILL.md"] = False
    if args.codex:
        targets[base / "AGENTS.md" if base else home / ".codex" / "AGENTS.md"] = True
    if args.gemini:
        targets[base / "GEMINI.md" if base else home / ".gemini" / "GEMINI.md"] = True
    if args.agents_md:
        targets[(base or Path.cwd()) / "AGENTS.md"] = True
    status = 0
    for path, shared in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not shared:
            new, note = _SKILL_HEAD + text, ""
            if path.exists() and path.read_text(encoding="utf-8") != new:
                backup = path.with_name(path.name + ".bak")
                shutil.copy2(path, backup)
                note = f"; your previous copy is in {backup}"
            path.write_text(new, encoding="utf-8")
            print(f"wrote {path} (the RemoteRF agent guide as a Claude skill){note}")
            continue
        block = f"{_BEGIN}\n{text.strip()}\n{_END}"
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        start = old.find(_BEGIN)
        end = old.find(_END, start) if start >= 0 else -1
        if start >= 0 and end < 0:
            print(f"Not changing {path}: it has {_BEGIN} but no {_END} after it; fix the markers and run this again.",
                  file=sys.stderr)
            status = 1
        elif start >= 0:
            path.write_text(old[:start] + block + old[end + len(_END):], encoding="utf-8")
            print(f"replaced the RemoteRF block in {path}; the rest of the file is unchanged")
        else:
            sep = "" if not old else "\n" if old.endswith("\n") else "\n\n"
            path.write_text(old + sep + block + "\n", encoding="utf-8")
            print(f"added the RemoteRF agent guide to {path} between {_BEGIN} and {_END}"
                  + ("; the rest of the file is unchanged" if old else ""))
    return status


def _print_api(api):
    from .agent_cli import _clean  # lab text: no terminal escapes

    print(_clean(f"{api['handle']}  {api['device_type']}  (IDL {api['schema_version'] or '?'})"))
    for line in (api["import_line"], api["constructor"]):
        if line:
            print(_clean(line))
    for kind in ("getter", "setter", "call"):
        methods = [m for m in api["methods"] if m["kind"] == kind]
        if methods:
            print(f"\n{kind}s:")
        for m in methods:
            doc = " ".join(m["doc"].split())
            print(_clean(f"  {m['name']}" + (f"  {doc}" if doc else "")))
    for note in api["notes"]:
        print(f"\n{_clean(note)}")


def main(argv) -> int:
    """`remoterf guide|api|example|skill ...`; argv starts with the command."""
    from .agent_cli import _Parser, _clean
    from .ops import RemoteRFOpsError

    parser = _Parser(prog="remoterf")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("guide", help="print the agent guide, or one topic of it")
    p.add_argument("topic", nargs="?")
    p = sub.add_parser("api", help="a device's Python API; needs no reservation")
    p.add_argument("handle")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("example", help="a receive-only example script for a device")
    p.add_argument("handle")
    p.add_argument("--recipe", choices=RECIPES, default="rx_once")
    p.add_argument("-o", dest="out", metavar="FILE")
    p = sub.add_parser("skill", help="install the agent guide where an AI assistant reads it")
    p.add_argument("action", choices=("install",))
    for flag in ("--claude", "--codex", "--gemini", "--agents-md"):
        p.add_argument(flag, action="store_true")
    p.add_argument("--dir", metavar="PATH", help="a project directory instead of your home configuration")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --help
        return exc.code or 0
    except RemoteRFOpsError as exc:  # usage
        if "--json" in argv:
            print(json.dumps(exc.to_dict()))
        print(_clean(f"remoterf: {exc.message}"), file=sys.stderr)
        return exc.exit_code

    try:
        if args.cmd == "guide":
            print(guide(args.topic)["text"], end="")
            return 0
        if args.cmd == "skill":
            return _skill_install(args)
        from . import ops

        api = ops.device_api(ops.saved_session(), args.handle)
        if args.cmd == "api":
            if args.json:
                print(json.dumps(api, indent=2))
            else:
                _print_api(api)
            return 0
        ex = example_script(api["device_type"], api["class_name"], api["handle"], args.recipe,
                            import_line=api["import_line"])
        if args.out:
            Path(args.out).write_text(ex["code"], encoding="utf-8")
            print(f"wrote {args.out}", file=sys.stderr)
        else:
            print(ex["code"], end="")
        print(_clean(ex["run_instructions"].replace(ex["filename"], args.out or ex["filename"])), file=sys.stderr)
        return 0
    except RemoteRFOpsError as exc:
        if getattr(args, "json", False):
            print(json.dumps(exc.to_dict()))
        else:
            print(_clean(f"remoterf: {exc.message}"), file=sys.stderr)
        if exc.exit_code == 3:
            print(f"remoterf: `{args.cmd}` asks the device's lab for its driver, so it needs a saved login.",
                  file=sys.stderr)
        return exc.exit_code
    except (ValueError, OSError) as exc:
        print(f"remoterf: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
