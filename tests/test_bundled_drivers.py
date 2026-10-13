"""Each bundled driver is what the client would generate from the Server's
schema, so a first device call does not rewrite site-packages."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from remoterf.drivers import dynamic_device

SERVER_SRC = Path(
    os.environ.get("REMOTERF_SERVER_SRC", Path(__file__).resolve().parents[2] / "RemoteRF-Server" / "src")
)
SCHEMAS = {
    "adalm_pluto": "remoteRF_server.drivers.adalm_pluto.pluto_schema:PlutoSchema",
    "hackrf": "remoteRF_server.drivers.hackrf.hackrf_schema:HackRFSchema",
    "rtl_sdr": "remoteRF_server.drivers.rtl_sdr.rtl_sdr_schema:RtlSdrSchema",
    "ti_mmwave": "remoteRF_server.drivers.ti_mmwave.ti_mmwave_schema:TiMmWaveSchema",
}
# A subprocess: the Server package stays out of this process's imports.
DUMP = """
import importlib, json, sys, types
try:
    import adi
except ImportError:
    sys.modules["adi"] = types.ModuleType("adi")  # the Pluto schema imports pyadi at load
out = {}
for device_type, spec in json.loads(sys.argv[1]).items():
    module, cls = spec.split(":")
    out[device_type] = getattr(importlib.import_module(module), cls)().get_idl()
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def server_schemas():
    if not (SERVER_SRC / "remoteRF_server").is_dir():
        pytest.skip(f"no Server source at {SERVER_SRC} (set REMOTERF_SERVER_SRC)")
    result = subprocess.run(
        [sys.executable, "-c", DUMP, json.dumps(SCHEMAS)],
        env={**os.environ, "PYTHONPATH": str(SERVER_SRC)},
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout.splitlines()[-1])


@pytest.mark.parametrize("device_type", sorted(SCHEMAS))
def test_bundled_driver_matches_the_server_schema(server_schemas, device_type):
    schema = server_schemas[device_type]
    path = dynamic_device._DRIVERS_DIR / device_type / f"{device_type}_remote.py"
    assert dynamic_device._read_schema_hash(path) == schema["schema_hash"]
    assert path.read_text(encoding="utf-8") == dynamic_device._codegen(schema)
