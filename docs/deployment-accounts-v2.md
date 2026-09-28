# Gate B deployment account homes

The ordinary `remoterf -l` shell selects native gRPC or HTTPS account services.
Configure a native server exactly as before: `remoterf -c -a lab:61005`.
Scheme-prefixed explicit host:port inputs still select gRPC. CA bootstrap,
SNI, native `.env` and environment overrides remain. Only UNIMPLEMENTED
permits pre-Global capability defaults; failures never downgrade policy.

Bare hosts use verified HTTPS `/.well-known/remoterf-deployment`. Select a
custom HTTPS port explicitly:

```sh
remoterf -c -a localhost:8443 --account-transport https-json
remoterf -l
```

Use register, verify, login and perms in that same shell. Prompts follow the
home's advertised registration policy. HTTPS accounts also support refresh,
logout, forgot-password, reset-password, and the ordinary `enroll` command.
Gate F accepts either the established prompt or `enroll <rrf2...>` and sends
the opaque value only to the authenticated home. The home—not the Client—owns
contract checks, grant issuance, broker routing, and signed-result validation.
Success is printed only after the home confirms an owner-signed result, and
bounded home/transport/destination failure provenance is retained. The Client
does not persist destination membership or expose a user-chosen request ID.
Verification/reset tokens are entered in hidden prompts, never as command
arguments. Groups display even with zero devices. Gate G extends the same
ordinary `perms` command with the exact HOME `local`/`federation` summary.
Remote successes show destination-signed source and retrieval time; local
policy blocks and unsigned transport failures remain visibly distinct.

All HTTPS requests stay on the verified origin, reject redirects and bind
credentials to origin/deployment UUID/subject UUID. New state lives under
`deployment/` and keyring namespace `remoterf-deployment`; file fallback is
0700/0600. Native configuration is not overwritten. Old Global profiles and
credentials are ignored; the old global/use/deployments CLI entry points are
removed. The retired v1 support modules and runtime are gone.

Install the verified `remoterf-federation-core` wheel before this Client
wheel. The exact Gate G source commits and wheel hashes are recorded in the
integration manifest and Gate G report.
The VPS repo records the lock and reproducible build/verification scripts.
No sibling runtime checkout, browser or server hardware dependency is needed.

VPS tests `test_v2_cli_https.py` and `test_v2_native_cli.py` drive the actual
CLI/main/account shell over isolated TLS services. They use fake email and
fake hardware; production is not contacted. This Client is development only.
