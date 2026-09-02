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

## NI USRP-2901, Ettus B205-mini, B200-family, and USRP N210

The packaged client natively includes the shared USRP schema for the qualified
NI USRP-2901, Ettus Research USRP B205-mini, the broader USB/B200 family, and
the Ethernet-connected N210. No custom client driver is required. The B205-mini
uses the same native-style API and reports its live 1 RX/1 TX channel topology
through `remoterf_capabilities`.

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

## RTL-SDR

RTL-SDR clients are generated from the server's `rtl_sdr` schema. The machine
running client code does not need `librtlsdr` or PyRtlSdr:

```python
from remoteRF.drivers import ensure_driver

token = "reservation-token"
ensure_driver(token=token)

from remoteRF.drivers.rtl_sdr import RtlSdr

sdr = RtlSdr(token)
sdr.sample_rate = 2_048_000
sdr.center_freq = 100_000_000
sdr.gain = "auto"
sdr.agc_mode = True

samples = sdr.read_samples(16_384)  # NumPy complex64
raw_iq = sdr.read_bytes(4096)       # packed uint8 I/Q bytes
```

The server owns the USB handle for its lifetime, so the generated client does
not close the physical radio. `read_samples` and `read_bytes` are bounded to a
4 MiB response. Call them repeatedly for longer captures. Native asynchronous
callbacks are not transported remotely.

When a Tailscale address differs from the IP or DNS identity in the server
certificate, keep TLS verification enabled and set the expected identity:

```bash
export REMOTERF_ADDR=rrf2.example.ts.net:61005
export REMOTERF_TLS_SERVER_NAME=certificate-name.example
```

## TI mmWave radar

TI radar clients are generated from the server's `ti_mmwave` schema. Only the
machine physically connected to the radar needs `pyserial` or a CP210x driver.
The initial parser profile supports xWR68xx mmWave SDK 3 out-of-box TLV output.

```python
from remoteRF.drivers import ensure_driver

token = "reservation-token"
ensure_driver(token=token)

from remoteRF.drivers.ti_mmwave import TiMmWave, decode_frame

radar = TiMmWave(token)
print(radar.device_info)
print(radar.query_version())

radar.apply_config(open("profile.cfg").read(), start=True)
try:
    packet = radar.read_frame(timeout=1.0)
    frame = decode_frame(packet)
    print(frame.header.frame_number, frame.points, frame.side_info)
finally:
    radar.stop()
```

`read_frame()` returns one complete binary UART packet. Decoding is client-local
and retains unknown TLVs as raw bytes, allowing custom firmware to be captured
before a dedicated parser profile is added. `stream_stats` reports dropped
frames, queued frames, resynchronization bytes, invalid headers, and reader
health. Remote firmware flashing is deliberately unsupported.

If `pip install` doesn't work, you can clone the [source](https://github.com/WirelessLabAtUCLA/RemoteRF-Client) directly from github.

## RemoteRF Global v0 / Remote Internet Access

By default, RemoteRF assumes you're on the same network as the RemoteRF
Server, and you configure the client with that server's address:

```bash
remoterf --config --addr 192.168.1.20:12321
```

For deployments exposed through the initial RemoteRF Global gateway, the
same workflow works with a DNS hostname instead of a LAN IP:

```bash
remoterf --config --addr ucla.global.remoterf.net:12321
```

The client does not know or care whether `ucla.global.remoterf.net` is a
directly reachable RemoteRF Server, a LAN server, or a RemoteRF Global v0
transparent TCP gateway forwarding to a RemoteRF Server elsewhere. From the
client's perspective, it's simply a RemoteRF Server endpoint identified by
`host:port` — an IPv4 address, an IPv6 address (bracketed, e.g.
`[2001:db8::1]:12321`), or a normal DNS hostname all work the same way.
Certificate bootstrap (`host:cert_port`) and the gRPC connection
(`host:port`) are both made against whatever hostname you configure — the
client stores and uses that hostname as-is and never resolves it to an IP
before saving your configuration, so DNS/IP changes on the server side don't
require reconfiguration.

**TLS note:** RemoteRF Global v0 does not weaken certificate verification.
The RemoteRF Server's certificate must include the hostname you connect
with (e.g. `ucla.global.remoterf.net`) in its Subject Alternative Name
(SAN) for standard TLS hostname validation to succeed — this is a
server-side certificate requirement, not a client setting. If you're
instead connecting through something like Tailscale, where the address you
dial differs from the identity in the certificate, use the existing
`REMOTERF_TLS_SERVER_NAME` override described above; it still requires that
name to be present in the certificate.

v0 is intentionally minimal — it does **not** include RemoteRF Global
accounts, deployment discovery/selection, federation, NAT traversal, direct
P2P, automatic routing, or global resource IDs. Those are future versions.
The client-side software behaves identically whether you point it at a LAN
server or at a RemoteRF Global v0 endpoint.

### RemoteRF Global v1.0 — accounts and deployment discovery (optional)

Building on the v0 relay above, v1.0 adds an *optional* RemoteRF Global
account so you don't have to already know a deployment's `host:port`:

```bash
remoterf global login       # device-code sign-in (no password typed here)
remoterf deployments        # list public deployments
remoterf use ucla           # select one — CA-verified, TLS-secured
remoterf use direct         # back to direct/LAN mode any time
```

Direct mode (`remoterf --config --addr ...`) keeps working exactly as
before and needs no RemoteRF Global account. See
[docs/remoterf-global-client-v1.md](docs/remoterf-global-client-v1.md) for
the full command reference, current limitations, and why `remoterf use
<slug>` currently stops with a clear error at the final
deployment-authentication step (no canonical `GlobalAuthV1` server contract
exists yet — this client does not fall back to a password login when that
happens). See also
[docs/remoterf-global-client-security.md](docs/remoterf-global-client-security.md)
and
[docs/remoterf-global-client-troubleshooting.md](docs/remoterf-global-client-troubleshooting.md).

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

Reservation successful. Your Token -> example_token

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
