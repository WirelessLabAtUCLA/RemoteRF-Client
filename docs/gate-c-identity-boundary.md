# Gate C identity boundary

This development branch has no Global v1 destination-principal or local-session
runtime. Gate C removed the `GlobalAuthV1` protobuf, assertion exchanger,
deployment-local session storage and orchestration, runtime bridge, and the
unwired legacy Global CLI that depended on them.

The ordinary `remoterf` shell continues to support the protected native gRPC
account path and the clean v2 HTTPS deployment-home account path. Both are
selected explicitly by the deployment target configuration. A home access
token is scoped to its issuing HTTPS home and is never accepted as a native
Server password, device token, reservation token, or destination session.

The old `remoteRF.global_client` package and its Global-specific credential,
profile, catalog, route, and CLI compatibility surface are absent. Useful
generic account-home functionality lives under `remoteRF.deployment`; future
federation transport and resource access are outside Gate C.
