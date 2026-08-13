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

from setuptools import setup, find_packages
import os

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="remoterf",
    version="2.0.8",
    author="Ethan Ge",
    author_email="ethoGalaxy@gmail.com",
    description="A python API to remotely access signal centric hardware. Client-side only! Courtesy of Wireless Lab @ UCLA & Prof. Ian Roberts.",
    long_description=long_description,  # Set the README content here
    long_description_content_type="text/markdown",  # Specify that it's Markdown
    packages=find_packages(where="src"),  # Automatically finds subpackages like core, deviceA, deviceB
    package_dir={"": "src"},
    license='GPL-3.0-or-later',
    include_package_data=True,  # Includes files specified in MANIFEST.in
    install_requires=[
        "grpcio>=1.78.1,<2.0.0", "protobuf>=6.31.1,<7.0.0", "numpy", "prompt_toolkit", "python-dotenv", "prompt-toolkit"
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.10',
    # entry_points={
    #     'console_scripts': [
    #         'remoterf-login=remoteRF.core.acc_login:main',
    #         'remoterf-v=remoteRF.core.version:main',
    #         'remoterf-config=remoteRF.config.config_cli:main',
    #     ],
    # },
    
    entry_points={
    "console_scripts": [
        "remoterf=remoteRF.remoterf_cli:main",
        # "remoterf-login=remoteRF.core.acc_login:main",
        # "remoterf-v=remoteRF.core.version:main",
        # "remoterf-config=remoteRF.config.config_cli:main",
    ],
},

)
