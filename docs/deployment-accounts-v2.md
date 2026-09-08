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
logout, forgot-password and reset-password. Verification/reset tokens are
entered in hidden prompts, never as command arguments. Groups display even
with zero devices. Gate B performs no remote enrollment or remote permissions.

All HTTPS requests stay on the verified origin, reject redirects and bind
credentials to origin/deployment UUID/subject UUID. New state lives under
`deployment/` and keyring namespace `remoterf-deployment`; file fallback is
0700/0600. Native configuration is not overwritten. Old Global profiles and
credentials are ignored; the old global/use/deployments CLI entry points are
removed. Dormant v1 support modules remain only for later Gate C/E work.

Install the verified `remoterf-federation-core 0.2.0` wheel before this Client
wheel (`remoterf 2.1.0.dev1`). The core source is Server commit
`a627550e1915c79d385734fb5b25f53deaa5d5e0`, wheel SHA-256
`512c4b57fa16ab656f9a48c9c50e1e8da5a83830f48d71802d97803ee563b4c0`.
The VPS repo records the lock and reproducible build/verification scripts.
No sibling runtime checkout, browser or server hardware dependency is needed.

VPS tests `test_v2_cli_https.py` and `test_v2_native_cli.py` drive the actual
CLI/main/account shell over isolated TLS services. They use fake email and
fake hardware; production is not contacted. This Client is development only.
