"""One account interface for direct gRPC and HTTPS native homes."""

import re
import time
from remoterf_federation_core import (
    validate_capabilities,
    local_capabilities,
    canonical_uuid,
)
from .http import JsonTransport, AccountBackendError
from .state import CredentialStore, load_target


class DeploymentAccountBackend:
    capabilities: dict

    def close(self):
        pass


class LegacyGrpcAccountBackend(DeploymentAccountBackend):
    def __init__(self, channel=None):
        import grpc
        from ..common.grpc import (
            deployment_capabilities_pb2 as pb,
            deployment_capabilities_pb2_grpc as rpc,
        )

        if channel is None:
            from ..core.grpc_client import get_active_connection

            self.connection = get_active_connection()
            channel = self.connection.channel
        else:
            from types import SimpleNamespace
            from ..common.grpc.grpc_pb2_grpc import GenericRPCStub

            self.connection = SimpleNamespace(
                channel=channel, stub=GenericRPCStub(channel)
            )
        try:
            response = rpc.DeploymentCapabilitiesV1Stub(channel).GetCapabilities(
                pb.DeploymentCapabilitiesRequest(protocol_version="2"), timeout=3
            )
        except grpc.RpcError as exc:
            if exc.code() != grpc.StatusCode.UNIMPLEMENTED:
                raise AccountBackendError(
                    "Deployment capability request failed"
                ) from exc
            self.capabilities = local_capabilities()
        else:
            if response.HasField("error") or not response.capabilities_json:
                raise AccountBackendError("Invalid capability response")
            self.capabilities = validate_capabilities(response.capabilities_json)
            if self.capabilities["account_transport"] != "grpc":
                raise AccountBackendError(
                    "Direct target cannot redirect account credentials"
                )

    def call(self, name, args):
        from ..core.grpc_client import rpc_client

        return rpc_client(function_name=name, args=args, connection=self.connection)


class HttpsJsonAccountBackend(DeploymentAccountBackend):
    def __init__(
        self,
        selected_origin,
        *,
        expected_id=None,
        transport=None,
        store=None,
        clock=time.time,
    ):
        self.transport = transport or JsonTransport(selected_origin)
        self.capabilities = validate_capabilities(
            self.transport.request("GET", "/.well-known/remoterf-deployment")
        )
        if self.capabilities["account_transport"] != "https-json":
            raise AccountBackendError("HTTPS target requires HTTPS account policy")
        ident = self.capabilities["deployment_id"]
        if expected_id is not None and ident != expected_id:
            raise AccountBackendError(
                "Selected deployment identity changed; configure explicitly"
            )
        self.base = self.capabilities["account_api_base"]
        self.clock = clock
        self.store = store or CredentialStore(self.transport.origin, ident)
        self.credentials = None

    def _operation(self, name):
        if name not in self.capabilities["supported_operations"]:
            raise AccountBackendError("Operation unavailable at this account home")

    def register(self, name, email, password):
        self._operation("ACC:create_user")
        return self.transport.request(
            "POST",
            self.base + "/register",
            data={"username": name, "email": email, "password": password},
        )

    def verify(self, token):
        self._operation("ACC:verify_email")
        return self.transport.request(
            "POST", self.base + "/verify-email", data={"token": token}
        )

    def _validate_session(self, value, expected_subject=None):
        required = {
            "deployment_id",
            "subject_id",
            "username",
            "access_token",
            "refresh_token",
            "token_type",
            "access_expires_at",
            "refresh_expires_at",
        }
        if set(value) != required:
            raise AccountBackendError("Invalid home session response")
        if value["deployment_id"] != self.capabilities["deployment_id"]:
            raise AccountBackendError("Home credential identity mismatch")
        canonical_uuid(value["subject_id"])
        if expected_subject and value["subject_id"] != expected_subject:
            raise AccountBackendError("Home subject changed")
        if not isinstance(value["username"], str) or not re.fullmatch(
            "[A-Za-z0-9_.-]{3,64}", value["username"]
        ):
            raise AccountBackendError("Invalid home username")
        for field in ["access_token", "refresh_token"]:
            if (
                not isinstance(value[field], str)
                or not 1 <= len(value[field]) <= 8192
                or any(c.isspace() for c in value[field])
            ):
                raise AccountBackendError("Invalid home credential")
        if value["token_type"] != "Bearer" or any(
            type(value[f]) is not int
            for f in ["access_expires_at", "refresh_expires_at"]
        ):
            raise AccountBackendError("Invalid home expiry")
        if value["refresh_expires_at"] <= self.clock():
            raise AccountBackendError("Home refresh credential expired")
        return value

    def login(self, login, password):
        self._operation("ACC:login")
        self.credentials = None
        self.store.clear()
        pair = self._validate_session(
            self.transport.request(
                "POST",
                self.base + "/login",
                data={"login": login, "password": password},
            )
        )
        # Bind to authenticated /me before persisting or exposing the session.
        self.credentials = pair
        try:
            self.me()
        except Exception:
            self.credentials = None
            raise
        self.store.save(self.credentials)
        return self.credentials

    def resume(self):
        value = self.store.load()
        if value:
            self.credentials = self._validate_session(value)
            self.me()
            return True
        return False

    def refresh(self):
        self._operation("ACC:refresh_session")
        if not self.credentials:
            raise AccountBackendError("Log in first")
        old = self.credentials
        try:
            pair = self._validate_session(
                self.transport.request(
                    "POST",
                    self.base + "/refresh",
                    data={"refresh_token": old["refresh_token"]},
                ),
                old["subject_id"],
            )
        except Exception:
            # Ambiguous rotation must not automatically replay a spent refresh token.
            self.credentials = None
            self.store.clear()
            raise
        self.credentials = pair
        self.store.save(pair)
        return pair

    def _access(self):
        if not self.credentials:
            raise AccountBackendError("Log in first")
        if self.credentials["access_expires_at"] <= self.clock() + 5:
            self.refresh()
        return self.credentials["access_token"]

    def _own(self, value):
        if (
            value.get("deployment_id") != self.capabilities["deployment_id"]
            or value.get("subject_id") != self.credentials["subject_id"]
        ):
            raise AccountBackendError("Authenticated home identity mismatch")
        groups = value.get("groups")
        if type(groups) is not list or len(groups) > 256:
            raise AccountBackendError("Invalid home groups")
        for group in groups:
            if (
                type(group) is not dict
                or set(group) != {"group_id", "group_name"}
                or not isinstance(group["group_id"], str)
                or not re.fullmatch("[0-9]{1,20}", group["group_id"])
                or not isinstance(group["group_name"], str)
                or not 1 <= len(group["group_name"]) <= 128
                or any(ord(c) < 32 or ord(c) == 127 for c in group["group_name"])
            ):
                raise AccountBackendError("Invalid home group")
        return value

    def me(self):
        return self._own(
            self.transport.request("GET", self.base + "/me", access=self._access())
        )

    def permissions(self):
        self._operation("ACC:get_perms")
        return self._own(
            self.transport.request(
                "GET", self.base + "/permissions", access=self._access()
            )
        )

    def logout(self):
        self._operation("ACC:logout")
        if self.credentials:
            self.transport.request(
                "POST",
                self.base + "/logout",
                data={"refresh_token": self.credentials["refresh_token"]},
            )
        self.credentials = None
        self.store.clear()

    def forgot(self, email):
        self._operation("ACC:forgot_password")
        return self.transport.request(
            "POST", self.base + "/password/forgot", data={"email": email}
        )

    def reset(self, token, password):
        self._operation("ACC:reset_password")
        result = self.transport.request(
            "POST",
            self.base + "/password/reset",
            data={"token": token, "new_password": password},
        )
        self.credentials = None
        self.store.clear()
        return result

    def close(self):
        self.transport.close()


def selected_backend():
    target = load_target()
    if target and target["transport"] == "https-json":
        return HttpsJsonAccountBackend(
            target["origin"], expected_id=target["deployment_id"]
        )
    return LegacyGrpcAccountBackend()
