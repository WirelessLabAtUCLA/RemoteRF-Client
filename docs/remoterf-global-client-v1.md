# RemoteRF Global v1.0 -- Client Guide

RemoteRF Global is an **optional** layer on top of ordinary RemoteRF. You
never need it to use RemoteRF directly on a LAN, and nothing in this
document changes that.

    # Existing direct/LAN mode -- unaffected, still the default
    remoterf --config --addr 164.67.195.207:61005
    remoterf --login

## Direct mode vs. Global mode

| | Direct mode | Global mode |
| --- | --- | --- |
| You configure | a `host:port` you already know | nothing -- you pick a deployment by name |
| Account | the deployment's own username/password | one RemoteRF Global account, reused across deployments |
| Certificate trust | fetched from the server, TLS-verified | fetched from the server, **but only trusted after** its SHA-256 fingerprint matches what RemoteRF Global says it should be |
| Works without Global? | yes, always | no -- Global mode always needs `global.remoterf.net` reachable to select/refresh a deployment |

The client does not know or care whether an endpoint it's configured with
is a directly reachable RemoteRF Server, a LAN server, or a RemoteRF Global
`tcp-relay` gateway (see [remoterf-global-v0 notes in the
README](../README.md#remoterf-global-v0--remote-internet-access)) -- from the
client's perspective it is simply a RemoteRF Server endpoint, reached over
the same v0 relay route.

## Commands

```
remoterf global login             # device-code sign-in to RemoteRF Global
remoterf global status            # show sign-in/session status (no secrets)
remoterf global logout            # sign out, clear local Global credentials

remoterf deployments              # list public deployments
remoterf deployments show <slug>
remoterf deployments resources <slug>

remoterf use <slug>               # select and connect to a deployment
remoterf use direct               # switch back to direct/LAN mode
```

Every one of these is additive to the existing `remoterf -c|-l|-v` verbs;
none of them change existing flags or defaults.

## Signing in: device-code login

```
$ remoterf global login
To sign in to RemoteRF Global, visit:
  https://global.remoterf.net/activate
and enter code: WXYZ-9999
A browser window has been opened for you.
```

This is the same flow GitHub CLI/`kubectl` use: a short human code you type
into a page in your normal browser, not a password typed into the
terminal. `remoterf` never asks for a RemoteRF Global password. If a
browser can't be opened (headless machine, SSH session), `--no-browser`
skips the attempt and the printed URL/code work the same way from any
other browser.

Credentials are stored using your OS keyring (macOS Keychain, Secret
Service on Linux, Windows Credential Locker) by default. On a headless
machine with no usable keyring, the client automatically falls back to an
encrypted-at-rest-by-permissions-only file under
`~/.config/remoterf-client/global/secrets/` and prints a warning every
time it does; pass `--credential-store file` to opt into that yourself
without the warning (e.g. for CI).

## Picking a deployment

```
$ remoterf deployments
SLUG          NAME                      STATUS    RESOURCES  PROTOCOL
ucla          UCLA WirelessLab          online     4          1

$ remoterf deployments show ucla
UCLA WirelessLab (ucla)
  Status      : online
  Protocol    : 1
  Resources   : 4

$ remoterf deployments resources ucla
Public resources exported by 'ucla':
  Pluto RX #1   remoterf://550e8400.../pluto-1
```

`deployments`/`deployments show`/`deployments resources` are public
discovery calls -- they work even when you are not signed in.
**This catalog is discovery only.** It never substitutes for the owning
deployment's own authoritative device/reservation state: if UCLA's actual
device list disagrees with what Global's catalog says, UCLA wins.

```
$ remoterf use ucla
```

`use <slug>` requires being signed in, and performs, in order:

1. resolve the deployment by slug (using its immutable UUID internally --
   a later slug rename doesn't create a second identity for the same
   deployment);
2. fetch a short-lived, authenticated connection descriptor from Global;
3. validate it (supported route kind, supported protocol version, not
   expired, syntactically valid endpoints/hostname/fingerprint);
4. fetch the deployment's CA certificate and verify its SHA-256
   fingerprint against the descriptor **before trusting it at all**;
5. open a secure (TLS-verified) gRPC channel to the deployment;
6. request a short-lived, deployment-targeted Global access assertion;
7. redeem it via the deployment's `GlobalAuthV1.ExchangeAssertion`.

Step 7 is where a real v1.0 install currently stops -- see [Current
limitations](#current-limitations) below.

## Global catalog vs. owner-authoritative state

Global stores route/catalog *metadata* only. It never creates a
reservation, never owns a physical device, and never mediates RX/TX
permission -- all of that stays with the owning deployment. Once
`GlobalAuthV1` and `remoterf use <slug>` are both fully wired up, ordinary
`remoterf` device/reservation operations after `use <slug>` will use that
deployment's own APIs exactly as direct mode does today, not a cached
Global catalog.

## Cached sessions and a Global outage

Once a deployment session exists, RemoteRF Global being briefly
unreachable does not tear down an already-established, unexpired session
to that deployment -- ordinary operations can continue. What a Global
outage *does* block: new `global login`, token refresh, a new `use`
selection, and re-authenticating an expired deployment session. There is
no fallback to a deployment's own username/password login when Global is
down for a Global-selected deployment, and Global tokens are never sent to
a deployment nor is a deployment's local session token ever sent to
Global.

## Switching back to direct mode

```
$ remoterf use direct
```

This only clears which deployment is "active" -- it never touches your
existing `~/.config/remoterf-client/.env`, so your direct/LAN configuration
is exactly as you left it and needs no reconfiguration.

## Current limitations (v1.0)

This client implements every Global-side step through requesting a Global
access assertion. The remaining step -- redeeming that assertion at the
deployment via `GlobalAuthV1.ExchangeAssertion` -- is not implemented,
because **no canonical `GlobalAuthV1` protobuf contract exists yet**
anywhere in the RemoteRF-Server repository (confirmed at the time this
client was built; only a federation *research* document exists there, not
an implementation). Rather than guess that wire contract, `remoterf use
<slug>` fails with a clear `GlobalAuthUnavailableError` at that exact step
and does **not** fall back to a deployment's username/password login. See
[remoterf-global-client-security.md](remoterf-global-client-security.md)
for the trust model this preserves, and
[remoterf-global-client-troubleshooting.md](remoterf-global-client-troubleshooting.md)
for what that error looks like and why it's expected today.

Not implemented in v1.0 (all deliberately out of scope, some by design for
a much later version):

- RemoteRF Global *accounts UI* (registration/verification/password reset
  happen on the Global website, not in this client);
- federation between independent deployments;
- NAT traversal, STUN/TURN, direct P2P, or any client-side VPN/WireGuard --
  v1.0 uses the existing v0 `tcp-relay` route exclusively;
- automatic routing between multiple simultaneously-active deployments;
- global resource IDs replacing a deployment's own device IDs.

## Library/programmatic usage

Everything under `remoteRF.global_client` is a plain, importable Python
API (typed dataclasses/Pydantic models, no CLI coupling) --
`GlobalApiClient`, `AuthenticatedGlobalClient`, `GlobalSessionManager`,
etc. It is new in this version and not yet declared stable; expect it to
evolve alongside `GlobalAuthV1` support. `remoterf_cli.py`'s existing
`--config`/`--login`/`--version` behavior and `remoteRF.core`/`remoteRF.drivers`
remain the stable, documented public surface.
