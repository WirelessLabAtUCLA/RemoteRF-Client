from setuptools import setup, find_packages
import os

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="remoterf",
    version="0.0.7.35",
    author="Ethan Ge",
    author_email="ethoGalaxy@gmail.com",
    description="A python API to remotely access signal centric hardware. Client-side only!",
    long_description=long_description,  # Set the README content here
    long_description_content_type="text/markdown",  # Specify that it's Markdown
    packages=find_packages(where="src"),  # Automatically finds subpackages like core, deviceA, deviceB
    package_dir={"": "src"},
    license_file='MIT',
    include_package_data=True,  # Includes files specified in MANIFEST.in
    install_requires=[
        "grpcio==1.71.0", "protobuf>=5.0.0,<6.0.0", "numpy", "prompt_toolkit", "python-dotenv"
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.10',
    entry_points={
        'console_scripts': [
            'remoterf-login=remoteRF.core.acc_login:main',
            'remoterf-v=remoteRF.core.version:main'
        ],
    },
)