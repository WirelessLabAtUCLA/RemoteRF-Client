from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    """The wheel also ships _remoterf_alias.pth, so `import remoteRF` works
    (see src/_remoterf_alias.py)."""

    def run(self):
        super().run()
        Path(self.build_lib, "_remoterf_alias.pth").write_text("import _remoterf_alias\n")


setup(cmdclass={"build_py": BuildPy})
