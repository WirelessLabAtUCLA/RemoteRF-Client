from setuptools import setup, find_packages

setup(
    name="remoteRF",
    version="0.0.0",
    author="Ethan Ge",
    author_email="ethoGalaxy@gmail.com",
    description="A package with core functionalities and device-specific submodules for remote emulation usage.",
    packages=find_packages(where="src"),  # Automatically finds subpackages like core, deviceA, deviceB
    package_dir={"": "src"},
    install_requires=[
        "grpcio", "protobuf", "numpy", "prompt_toolkit", "python-dotenv"
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
    entry_points={
        'console_scripts': [
            'remoterf-login=remoteRF.core.acc_login:main',
        ],
    },
)