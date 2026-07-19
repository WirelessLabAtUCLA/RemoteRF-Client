# Remote RF

A python API to remotely access signal centric hardware. 

Courtesy of Wireless Lab @ UCLA. - Ethan Ge

## Prerequisites

- **Python 3.10**: This package works in Python 3.10+. If you don’t have Python installed, you can download it from the [official Python website](https://www.python.org/downloads/).

To check your current Python version, open a terminal and run:

```bash
python --version
```

- **UCLA VPN**: Please ensure that you are connected to the UCLA VPN. You can download and configure the VPN client from the following link: [UCLA VPN Client Download](https://www.it.ucla.edu/it-support-center/services/virtual-private-network-vpn-clients). If you’re not connected to the VPN, you will not have access to the lab servers.

## Installation

Use the package manager [pip](https://pip.pypa.io/en/stable/) to install remoteRF. It is recommended that you install this package within a [virtual environment](https://docs.python.org/3/library/venv.html).

```bash
python3 -m venv venv        # Create virtual environment
source venv/bin/activate    # Activate virtual environment

pip install remoterf        # Install remoteRF
```

## NI USRP-2901 / B200-family

After reserving a server-local `usrp` inventory entry, fetch or refresh its
Dynamic v2 package with the reservation token:

```python
from remoteRF.drivers import ensure_driver

ensure_driver(token="reservation-token")
```

Both supported initialization forms then use the same generated runtime:

```python
from remoteRF.drivers.usrp import uhd

with uhd.usrp.MultiUSRP(token="reservation-token") as usrp:
    print(usrp.remoterf_capabilities)
    usrp.set_rx_rate(1e6)
```

```python
from remoteRF.drivers.usrp import MultiUSRP

usrp = MultiUSRP("reservation-token")
try:
    print(usrp.get_pp_string())
finally:
    usrp.close()
```

Streaming uses NumPy buffers and native-like UHD value objects. Only the
leading region reported by native UHD is changed on a partial receive:

```python
import numpy as np
from remoteRF.drivers.usrp import uhd

with uhd.usrp.MultiUSRP(token="reservation-token") as usrp:
    stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
    stream_args.channels = [0]
    with usrp.get_rx_stream(stream_args) as rx:
        samples = np.empty(4096, dtype=np.complex64)
        metadata = uhd.types.RXMetadata()
        count = rx.recv(samples, metadata, 0.25)
        valid_samples = samples[:count]
```

The generated package includes native-like namespaces, aliases, overload
stubs, deterministic `close()`, context managers, opaque session-bound
handles, and typed errors in `remoteRF.core.v2_errors`. Streamers are never
silently recreated after a disconnect; reopen a new device session and
configure a new stream explicitly.

Methods marked `deferred` in the fetched schema remain available through
generic native dispatch, but are not release-qualified as exact UHD overload
parity until the server's UHD 4.10 target-introspection and hardware
differential gates pass.

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
<!-- 
## Reservation

Usage of the platform requires you to register a account and reserve a device in order to run scripts remotely. 

### 1. **Start UCLA VPN**

- Start the CISCO Secure client, login and connect to any of the options.

### 2. **Register a account**:
```bash
remoterf-login  
# Run in the terminal 
# where the Python library is installed

# Typically, this will be the terminal where you’ve activated the virtual environment if you’re using one
```

- Input `r` to register a account, or `l` to login to a existing one.

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

# Input the number of days you want to view, and it will display available reservations in that time span.

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
    token = 'example_token'     # Place the prior token here.
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

**So please submit feedback!** -->
