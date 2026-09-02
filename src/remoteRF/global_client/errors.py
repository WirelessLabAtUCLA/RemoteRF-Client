# Copyright (C) 2026 RemoteRF
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Typed exception hierarchy for RemoteRF Global.

Every exception here is safe to print directly to a terminal: none of them
carry access tokens, refresh tokens, assertions, or local session material
in their message. Callers that catch a subclass may still inspect it for a
stable ``exit_category`` used by the CLI to choose a process exit code.
"""

from __future__ import annotations


class GlobalClientError(Exception):
    """Base class for all RemoteRF Global client-side errors."""

    #: Stable category name for CLI exit-code mapping. Subclasses override.
    exit_category = "error"


# --- Global authentication -------------------------------------------------


class NotLoggedInError(GlobalClientError):
    """No Global credentials are stored; the user must run `global login`."""

    exit_category = "authentication_required"


class GlobalAuthenticationExpiredError(GlobalClientError):
    """The Global access token expired and the refresh attempt also failed."""

    exit_category = "authentication_required"


class EmailNotVerifiedError(GlobalClientError):
    exit_category = "authentication_required"


class DeviceLoginExpiredError(GlobalClientError):
    """The device-code login flow expired before the user approved it."""

    exit_category = "authentication_required"


class DeviceLoginDeniedError(GlobalClientError):
    """The user explicitly denied the device-code login request."""

    exit_category = "authorization_denied"


# --- Global service reachability -------------------------------------------


class GlobalUnavailableError(GlobalClientError):
    """The Global control plane (global.remoterf.net) could not be reached
    or returned a server error. Direct/LAN RemoteRF is unaffected."""

    exit_category = "network_unavailable"


# --- Deployment discovery / selection ---------------------------------------


class DeploymentNotFoundError(GlobalClientError):
    exit_category = "deployment_unavailable"


class DeploymentDisabledError(GlobalClientError):
    exit_category = "deployment_unavailable"


class DeploymentOfflineError(GlobalClientError):
    exit_category = "deployment_unavailable"


class NoActiveDeploymentError(GlobalClientError):
    exit_category = "invalid_usage"


# --- Connection descriptor validation ---------------------------------------


class MalformedDescriptorError(GlobalClientError):
    exit_category = "trust_failure"


class UnsupportedRouteKindError(GlobalClientError):
    exit_category = "trust_failure"


class DescriptorExpiredError(GlobalClientError):
    exit_category = "trust_failure"


class ProtocolVersionError(GlobalClientError):
    exit_category = "trust_failure"


# --- Network / TLS bootstrap -------------------------------------------------


class DnsResolutionError(GlobalClientError):
    exit_category = "network_unavailable"


class CertificateBootstrapError(GlobalClientError):
    exit_category = "trust_failure"


class CaFingerprintMismatchError(GlobalClientError):
    """The fetched CA certificate's DER SHA-256 does not match the
    fingerprint in the authenticated connection descriptor. Fail closed."""

    exit_category = "trust_failure"


class TlsHostnameError(GlobalClientError):
    exit_category = "trust_failure"


class GrpcConnectionError(GlobalClientError):
    exit_category = "network_unavailable"


# --- GlobalAuthV1 / deployment-local sessions --------------------------------


class GlobalAuthUnavailableError(GlobalClientError):
    """The target deployment does not (yet) support the GlobalAuthV1
    assertion exchange. This is a protocol/contract gap, not a transient
    network failure -- callers must not fall back to a UCLA/owner password
    login on this error."""

    exit_category = "deployment_unavailable"


class AssertionRejectedError(GlobalClientError):
    """The deployment rejected a Global access assertion (expired, wrong
    audience, disabled local mapping, or owner policy)."""

    exit_category = "authorization_denied"


class LocalSessionExpiredError(GlobalClientError):
    exit_category = "authentication_required"


class OperationDeniedError(GlobalClientError):
    exit_category = "authorization_denied"


class InvalidUsageError(GlobalClientError):
    exit_category = "invalid_usage"


# --- CLI exit-code mapping ---------------------------------------------------

EXIT_CODES = {
    "success": 0,
    "authentication_required": 3,
    "network_unavailable": 4,
    "trust_failure": 5,
    "deployment_unavailable": 6,
    "authorization_denied": 7,
    "invalid_usage": 2,
    "error": 1,
}


def exit_code_for(exc: BaseException) -> int:
    category = getattr(exc, "exit_category", "error")
    return EXIT_CODES.get(category, 1)
