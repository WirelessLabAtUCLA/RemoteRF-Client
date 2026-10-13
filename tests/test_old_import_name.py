"""Code written before 2.2.0 imports remoteRF: it gets the remoterf modules
themselves, with one FutureWarning pointing at that code."""

import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
PROBE = """
import site, sys, warnings
sys.path.insert(0, sys.argv[1])
site.addsitedir(sys.argv[2])  # runs the .pth, as site-packages does at startup
assert not {"remoterf", "grpc", "numpy"} & set(sys.modules)  # the hook alone stays light
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    import remoteRF
    from remoteRF.drivers.adalm_pluto import Pluto
    import remoteRF.drivers.adalm_pluto as old
import remoterf
import remoterf.drivers.adalm_pluto as new
assert remoteRF is remoterf and old is new and Pluto is new.Pluto
assert remoterf.__spec__.name == "remoterf" and new.__spec__.name == "remoterf.drivers.adalm_pluto"
assert [(w.category, w.filename) for w in caught if "remoteRF" in str(w.message)] == [(FutureWarning, "<string>")], caught
print("ok")
"""


def test_import_remoteRF_aliases_remoterf_with_one_warning(tmp_path):
    (tmp_path / "_remoterf_alias.pth").write_text("import _remoterf_alias\n")  # what setup.py ships
    result = subprocess.run([sys.executable, "-c", PROBE, str(SRC), str(tmp_path)], capture_output=True, text=True)
    assert result.stdout.strip() == "ok", result.stderr
