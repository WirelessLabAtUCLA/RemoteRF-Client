# RemoteRF Global v1.0 -- Troubleshooting

## "GlobalAuthUnavailableError" / "This deployment's RemoteRF Global authentication service ... is not available"

**This is expected in the current v1.0 release**, not a bug in your setup.
`remoterf use <slug>` completes deployment discovery, connection-descriptor
validation, CA fingerprint verification, and a secure TLS gRPC connection
to the real deployment -- then stops at the last step
(`GlobalAuthV1.ExchangeAssertion`) because no deployment currently
implements that RPC; there is no canonical protobuf contract for it yet.
See [remoterf-global-client-v1.md's Current
limitations](remoterf-global-client-v1.md#current-limitations). Once
RemoteRF-Server ships `GlobalAuthV1` and this client is updated to match
it, this step will complete instead of erroring. Until then, use direct
mode (`remoterf --config --addr <host>:<port>` + your existing account) for
that deployment if you have LAN/direct access.

## "Not logged in to RemoteRF Global"

Run `remoterf global login`. If you were previously signed in and see this
after a while, your refresh token likely expired or was rotated/revoked
elsewhere -- sign in again.

## "Your RemoteRF Global session expired and could not be refreshed"

The stored refresh token was rejected by Global (expired, revoked, or --
per the server's reuse-detection -- replayed after already being rotated
away, which revokes every session for that account defensively). Local
credentials are cleared automatically; run `remoterf global login` again.

## "Could not reach RemoteRF Global (...)" / timeouts

RemoteRF Global (`global.remoterf.net` by default) is unreachable or slow.
This never affects direct/LAN RemoteRF. If you already have a valid,
unexpired session to a specific deployment, that deployment keeps working
during a Global outage -- only *new* Global sign-in, token refresh, `use`
selection, and re-authentication of an *expired* deployment session are
blocked until Global is reachable again.

## "The deployment's certificate does not match the fingerprint RemoteRF Global issued for it"

Fail-closed, by design (`CaFingerprintMismatchError`) -- never bypass this.
Possible causes:

- the deployment's operator rotated its TLS certificate without updating
  its route registration with RemoteRF Global (an operator-side fix, not a
  client workaround);
- a genuine on-path interception between you and the deployment's
  certificate-bootstrap endpoint.

Do not reconfigure anything to make this pass. If you believe the mismatch
is spurious, re-run `remoterf use <slug>` to fetch a fresh descriptor (the
old one may simply be stale) and contact the deployment operator if it
persists.

## "This RemoteRF client does not support route kind: ..."

Your client version only understands `tcp-relay` in v1.0. A deployment
advertising a different route kind (e.g. a future P2P/relay-less route)
needs a newer client.

## "Deployment requires RemoteRF Global protocol N; this client supports ..."

Update `remoterf` (`pip install --upgrade remoterf`).

## No usable OS keyring / "Falling back to file-based credential storage"

Expected on many headless Linux hosts and some CI environments. The
warning is not an error -- login still works, credentials are just stored
under `~/.config/remoterf-client/global/secrets/` (mode `0600`) instead of
the OS keyring. Pass `--credential-store file` to make this an explicit,
silent choice instead of an automatic fallback-with-warning; installing
and unlocking an OS keyring (e.g. `gnome-keyring`, `kwallet`) gets you back
to the stronger default.

## Browser didn't open during `global login`

The device code and URL printed to the terminal work from *any* browser,
on any device -- you don't need the browser to open automatically. Pass
`--no-browser` to skip the attempt entirely (e.g. over SSH).

## "Could not resolve RemoteRF server hostname: ..." (direct mode)

This is the existing direct/LAN DNS-resolution error, unrelated to Global
mode -- check the hostname/IP you passed to `--config --addr` and your
network/DNS.

## `remoterf use direct` didn't restore my old server

`use direct` only clears which Global deployment is "active"; it never
recreates or modifies `~/.config/remoterf-client/.env`. If direct mode
looks unconfigured after switching back, your `.env` was never set up (or
was wiped) independently of Global mode -- run
`remoterf --config --addr <host>:<port>` as usual.

## I want to see exactly what's stored, without printing secrets

- `remoterf global status --json` -- signed-in state, account email/UUID,
  active deployment, token *expiry* (never the token value).
- `~/.config/remoterf-client/global/state.json` -- non-secret only
  (Global base URL, active deployment identity, cache timestamps). If you
  ever see something that looks like a token or assertion in this file,
  please report it as a bug.
- Actual credentials live in your OS keyring (or
  `~/.config/remoterf-client/global/secrets/*.json` in file-fallback mode)
  and are never printed by any `remoterf` command.
