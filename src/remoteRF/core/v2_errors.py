"""Typed errors reconstructed from Dynamic protocol v2 envelopes."""
from __future__ import annotations

import json


class RemoteRFError(Exception):
    retryable = False
    fatal_to_session = False

    def __init__(
        self,
        message: str,
        *,
        details=None,
        method: str = "",
        native_exception: str = "",
        uhd_version: str = "",
        retryable: bool = False,
        fatal_to_session: bool = False,
    ):
        super().__init__(message)
        self.details = dict(details or {})
        self.method = method
        self.native_exception = native_exception
        self.uhd_version = uhd_version
        self.retryable = bool(retryable)
        self.fatal_to_session = bool(fatal_to_session)


class RemoteRFTransportError(RemoteRFError):
    pass


class RemoteRFSessionExpiredError(RemoteRFError):
    pass


class RemoteRFReservationError(RemoteRFError):
    pass


class RemoteRFPolicyError(RemoteRFError):
    pass


class RemoteRFStaleHandleError(RemoteRFError):
    pass


class RemoteRFProtocolError(RemoteRFError):
    pass


class RemoteRFOverloadError(RemoteRFError):
    pass


class RemoteRFServerOverloadedError(RemoteRFError):
    pass


class RemoteRFNativeUHDError(RemoteRFError):
    pass


_ERRORS = {
    cls.__name__: cls
    for cls in (
        RemoteRFError,
        RemoteRFTransportError,
        RemoteRFSessionExpiredError,
        RemoteRFReservationError,
        RemoteRFPolicyError,
        RemoteRFStaleHandleError,
        RemoteRFProtocolError,
        RemoteRFOverloadError,
        RemoteRFServerOverloadedError,
        RemoteRFNativeUHDError,
    )
}


def raise_for_envelope(envelope) -> None:
    if envelope is None or not getattr(envelope, "code", ""):
        return
    try:
        details = json.loads(envelope.details_json or "{}")
    except json.JSONDecodeError:
        details = {"raw_details": envelope.details_json}
    cls = _ERRORS.get(envelope.code, RemoteRFError)
    raise cls(
        envelope.message or envelope.code,
        details=details,
        method=envelope.method,
        native_exception=envelope.native_exception,
        uhd_version=envelope.uhd_version,
        retryable=envelope.retryable,
        fatal_to_session=envelope.fatal_to_session,
    )
