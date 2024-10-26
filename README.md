# Remote RF

A python API to remotely access signal centric hardware. Courtesy of Wireless Lab @ UCLA.

## Prerequisites

- **Python 3.12 or later**: This package requires Python 3.12+. If you don’t have Python installed, you can download it from the [official Python website](https://www.python.org/downloads/).

To check your current Python version, open a terminal and run:

```bash
python --version
```

- **UCLA VPN**: Please ensure that you are connected to the UCLA VPN. You can download and configure the VPN client from the following link: [UCLA VPN Client Download](https://www.it.ucla.edu/it-support-center/services/virtual-private-network-vpn-clients). If you’re not connected to the VPN, you will not have access to the lab servers.

## Installation

Use the package manager [pip](https://pip.pypa.io/en/stable/) to install remoteRF. It is recommended that you install this package within a [virtual environment](https://docs.python.org/3/library/venv.html).

```bash
pip install remoteRF
```

If `pip install` doesn't work, you can clone the [source](https://github.com/WirelessLabAtUCLA/RemoteRF-Client) directly from github.

<!-- 1. **Clone the repository:**
```bash
git clone https://github.com/WirelessLabAtUCLA/RemoteRF-Client
cd repository-name
```
2. **Install the package using** `pip` **in editable mode:**
```bash
pip install -e .
```
This command installs the package in "editable" mode, allowing for modifications to the local code without reinstalling. For more details on installing packages from local directories, refer to Python Packaging: [Installing from Local Archives](https://packaging.python.org/en/latest/tutorials/installing-packages/#installing-packages-from-local-archives). -->

## Reservation

Usage of the platform requires you to register a account and reserve a device in order to run scripts remotely. 

### 1. **Start UCLA VPN**

- Start the CISCO Secure client, login and connect to any of the options.

### 2. **Register a account**:
```bash
remoterf-login  # Run in the terminal where the Python library is installed

# Typically, this will be the terminal where you’ve activated the virtual environment if you’re using one
```

- Input `r` to register a account, or `l` to login to a existing one.

<!-- 2. **You will be prompted with this**: -->
```bash
Welcome to Remote RF Account System.
Please login or register to continue. (l/r):
```

- Once in, input `help` to see all avaliable commands.

### 3. **Reserve Device**:
```bash
getdev  # To view all avaliable devices

# Note the device ID. You will need this later to reserve said device
```

```bash
getres  # To view times not avaliable

# Optionally, you can also view all reservations, and determine a time slot you want a specific device reserved
```
```bash
perms   # To view your permissions

# Depending on your permission levels, you will be given different restrictions 
```

```bash
resdev # To reserve a device

Enter the device ID you would like to reserve: 0
Reserve Start Time (YYYY-MM-DD HH:MM): 2024-11-20 10:20
Reserve End Time (YYYY-MM-DD HH:MM): 2024-11-20 11:20

# Time is military time. You can also use arrow keys to cycle between previous commands.

Reservation successful. Thy Token -> example_token

# Take note of this token. You will need it to actually access the device.
```

## Remote Access

With this token, you can now run scripts remotely. Please keep in mind that you MUST be connected to the UCLA VPN for this to work.
Here is a explained sample script to get you going!

#### Python Script:

```python
from remoteRF.drivers.adalm_pluto import *  # Imports device Pluto SDR remote drivers. Change depending on desired device.

sdr = adi.Pluto(    # Device initialization.
    token = 'sample_token'     # Place the prior token here.
)

# You can now use this 'sdr' as you normally would with the default Pluto drivers.
```

If converting a existing `non-remoteRF` compatible script:

```diff
- import existing_device_drivers 

+ from remoteRF.drivers.device_drivers import *

- device = device(init)

+ device = device(token = 'sample_token')
```

Nothing else needs changing! 

## Closing

This is fundamentally a experimental platform, and there will be many unknown bugs and issues. Some devices do not have universal support for all its functions at the moment, I am working on that aspect. 

**So please submit feedback!**