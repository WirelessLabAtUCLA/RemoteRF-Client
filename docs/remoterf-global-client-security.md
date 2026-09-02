# RemoteRF Global v1.0 -- Client Security Model

This document is the security reference for `remoteRF.global_client`. If
something here and the code disagree, the code is a bug -- please report
it.

## Three credentials, never confused

| Credential | Identifies you to | Stored | Never sent to |
| --- | --- | --- | --- |
| **Global access/refresh token** (`credentials.GlobalCredentials`) | `https://global.remoterf.net` only | OS keyring (or opt-in file fallback) | any deployment |
| **Deployment access assertion** (`assertion_exchange.GlobalAuthExchangeRequest.assertion`) | one specific deployment, once | never persisted at all | any origin other than that deployment's `GlobalAuthV1` service |
| **Owner-local RemoteRF session** (`local_sessions.LocalDeploymentSession`) | that deployment's own RemoteRF APIs | OS keyring (or file fallback), keyed by **immutable deployment UUID** | RemoteRF Global |

These are enforced as separate types, separate storage namespaces
(`credentials.py` vs. `local_sessions.py`), and separate keyring/file keys
-- not just a naming convention. `GlobalApiClient` only ever talks to the
configured Global base URL and only ever attaches a Global bearer token to
requests it makes itself; it has no code path that could send that token
to a deployment.

## What "Global is the trusted introduction point" means

A deployment's certificate-bootstrap endpoint
(`route.certificate_endpoint`) is unauthenticated HTTP/raw-TCP -- exactly
like direct mode's always has been. That is fine, because Global mode
never trusts that endpoint by itself:

1. The client asks Global for a connection descriptor over **TLS-verified
   HTTPS**, authenticated with a Global bearer token
   (`GET /v1/deployments/{slug}/connection`).
2. That descriptor carries `route.ca_sha256` -- the SHA-256 fingerprint
   Global expects the deployment's certificate to have.
3. Only *after* the client fetches a certificate from
   `route.certificate_endpoint` and its DER SHA-256 fingerprint matches
   `route.ca_sha256` (`ca_store.fetch_and_verify_ca`, constant-time
   comparison via `hmac.compare_digest`) is that certificate trusted at
   all, and only then is it persisted to disk.

If the fingerprints don't match: `CaFingerprintMismatchError`, and nothing
is written to disk -- a previously-verified, cached CA for that deployment
(if any) is left exactly as it was. `ca_store.verify_and_store_ca` fetches
into memory, verifies, and only then does an atomic `os.replace` onto the
real path; a crash or failed fetch mid-operation can never leave a
partially-written or unverified CA file in place.

## TLS is never weakened

The gRPC channel to a deployment is built by
`core.secure_channel.build_secure_channel` -- the exact same function
direct mode uses, not a parallel "Global mode" implementation with its own
(potentially laxer) rules. `route.tls_server_name` only selects *which*
identity gRPC must find presented in the certificate
(`grpc.ssl_target_name_override`); it never disables or weakens hostname
verification, and it is always populated from the same authenticated
descriptor the CA fingerprint came from -- never inferred, never guessed
from an IP.

Nothing in this client ever sets `verify=False` (for `httpx`) or skips
gRPC's certificate/hostname checks. If you see either of those, that is a
bug, not an intended "Global mode" behavior.

## Connection descriptor validation (`route_resolver.py`)

Before anything else acts on a descriptor, `resolve_route()` validates,
and raises a specific typed error for the first thing that's wrong,
without reinterpreting or downgrading it:

- `route.kind` is a route this client actually implements (`tcp-relay`
  only in v1.0) -- `UnsupportedRouteKindError` otherwise;
- `protocol_version` is one this client supports -- `ProtocolVersionError`
  otherwise;
- the descriptor has not expired (with a small skew margin so it isn't
  used right up to the wire) -- `DescriptorExpiredError`;
- `route.grpc_endpoint` / `route.certificate_endpoint` parse as valid
  `host:port` (reusing the same hostname/IPv4/bracketed-IPv6 parser as
  direct mode -- see the RemoteRF Global v0 client work; there is no
  separate Global address parser) -- `MalformedDescriptorError`;
- `route.tls_server_name` is a syntactically valid hostname --
  `MalformedDescriptorError`;
- `route.ca_sha256` is 32 colon-separated hex byte pairs --
  `CaFingerprintMismatchError`.

Deployment identity always comes from the descriptor's own
`deployment_id`/`slug` fields, never inferred from a resolved IP. The
client never assumes `certificate_port == grpc_port + 1` the way the v0
CLI's own `--config` convenience convention does -- it only ever uses the
descriptor's explicit `certificate_endpoint`; if that field is absent, CA
bootstrap fails clearly (`CertificateBootstrapError`) instead of guessing a
port.

## Global access-token lifecycle

`auth_client.AuthenticatedGlobalClient` is the single place that attaches
a Global bearer token to a request:

- loads the stored access token; if it's within 30 seconds of expiring, or
  the very first attempt of a call comes back `401`, it uses the refresh
  token to mint a new pair;
- persists the rotated pair (`SecretStore.set` -- an atomic keyring write
  or an atomic `os.replace` for the file store) **before** retrying;
- retries the original call **at most once** -- never loops;
- if the refresh itself fails, clears the stored (now-invalid)
  credentials and raises `GlobalAuthenticationExpiredError`, requiring a
  fresh `global login`.

Redirects are never followed by the Global HTTP client
(`httpx.Client(..., follow_redirects=False)`), so a bearer token can never
leak to a different origin via a redirect response.

## Deployment access assertions

- requested only for the deployment currently being used
  (`POST /v1/deployments/{slug}/access-assertion`, itself a Global
  bearer-token-authenticated call);
- never written to disk, never put in `global/state.json`, and never
  logged;
- sent only to that same deployment's `GlobalAuthV1.ExchangeAssertion` --
  see [Current limitations](remoterf-global-client-v1.md#current-limitations)
  for why that step isn't implemented yet;
- **not blindly retried.** `session_manager.GlobalSessionManager._exchange`
  distinguishes:
  - `GlobalAuthUnavailableError` (the deployment doesn't speak the
    protocol at all) and `AssertionRejectedError` (owner denial is
    authoritative) -- both propagate immediately, no retry;
  - any other/ambiguous failure (timeout, dropped connection, `UNAVAILABLE`
    after the RPC may have already been processed) -- the assertion is
    discarded and exactly **one** retry is made with a **fresh** assertion
    and a fresh `client_request_id`. Never more than two total attempts.

## Owner-local sessions

Stored per **immutable deployment UUID**
(`local_sessions.LocalSessionStore`), never per slug -- a slug rename can't
make one deployment's session usable against a different deployment, and a
session is only ever reused if the descriptor's `tls_server_name` still
matches what it was issued under. `remoterf global logout` clears every
cached local session it can find on disk, in addition to Global
credentials themselves -- but never touches direct mode's `.env`/CA.

## Why credential storage falls back to a file, and how that's guarded

Not every machine has a usable OS keyring (headless Linux, some CI
environments). `credentials.resolve_secret_store()` prefers the OS keyring
whenever it's usable; when it isn't, and the user hasn't explicitly opted
into file storage, it falls back automatically **and prints a warning
every time** that this is weaker than an OS keyring (filesystem
permissions only, not OS-level encryption/access control). The file store
itself: `~/.config/remoterf-client/global/secrets/`, directory mode
`0700`, file mode `0600`, atomic writes (write to a temp file in the same
directory, `os.replace` onto the real path) -- so a crash mid-write can
never leave a partially-written credential file behind.

## What is deliberately *not* handled client-side

- The client never decodes a Global access token's JWT payload to make an
  authorization decision -- the server is authoritative; a locally-decoded
  `exp` claim, if ever used, is a UX nicety only (avoiding an unnecessary
  round trip), never a security check.
- The client does not implement its own JWT/PASETO verification, its own
  encryption-at-rest scheme, or a home-grown key derivation -- OS keyring
  APIs are used as-is; there is no "home-grown crypto" anywhere in this
  package.
