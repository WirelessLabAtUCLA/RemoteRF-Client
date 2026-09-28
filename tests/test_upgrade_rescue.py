"""remoteRF.py rescues an install whose package folder kept the lowercase name
of remoterf 2.0.7-2.0.13, and never shadows a correctly named package folder."""

import shutil
import subprocess
import sys
from pathlib import Path

RESCUE = Path(__file__).resolve().parents[1] / "src" / "remoteRF.py"
PROBE = (
    "import os, remoteRF, remoteRF.sub; "
    "print(remoteRF.MARK, remoteRF.sub.MARK, os.path.basename(os.path.dirname(remoteRF.__file__)))"
)


def _site(tmp_path, folder=None):
    site = tmp_path / "site"
    site.mkdir()
    if folder:
        (site / folder / "sub").mkdir(parents=True)
        (site / folder / "__init__.py").write_text("MARK = 'package'\n")
        # Inside the package, code imports itself by its canonical name.
        (site / folder / "sub" / "__init__.py").write_text("from remoteRF import MARK\n")
    shutil.copy(RESCUE, site / "remoteRF.py")
    return site


def _import(site):
    # -S: no site-packages, so only this directory can answer the import.
    return subprocess.run([sys.executable, "-S", "-c", PROBE], cwd=site, capture_output=True, text=True)


def test_a_package_left_in_the_lowercase_folder_still_imports_as_remoteRF(tmp_path):
    result = _import(_site(tmp_path, "remoterf"))
    assert result.stdout.split() == ["package", "package", "remoterf"], result.stderr


def test_a_correctly_named_package_folder_wins_over_the_rescue(tmp_path):
    result = _import(_site(tmp_path, "remoteRF"))
    assert result.stdout.split() == ["package", "package", "remoteRF"], result.stderr


def test_without_any_package_folder_the_rescue_says_to_reinstall(tmp_path):
    result = _import(_site(tmp_path))
    assert "reinstall" in result.stderr
