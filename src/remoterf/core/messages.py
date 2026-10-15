"""Plain English for every failure a person can see. A wire code (Global's HTTP
error, a lab's signed refusal, 'Partner refused: <code>' from a lab HOME) or a
gRPC status becomes a sentence naming the deployment and device; text a lab
wrote is already a sentence and passes through. The raw code goes to the
'remoterf' logger, and is appended when REMOTERF_DEBUG=1."""

import logging
import os
import re

import grpc

log = logging.getLogger("remoterf")


def debug():
    return os.environ.get("REMOTERF_DEBUG") == "1"


_DOWN = "{Where} turned the reservation down: "
_LAB_DOWN = "{Where} isn't responding right now; try again in a minute."
_NOT_SHARING = "{Where} isn't sharing with Global right now."
_GLOBAL_UPDATE = "RemoteRF Global couldn't read that request; update remoterf (pip install -U remoterf) and try again."
_LIMIT_TAIL = " Cancel one (cancelres) or wait for one to end."
# A tuple: the first template whose fields are all known.
_TEXT = {
    "reservation_refused": "{Where} turned the reservation down.",
    "reservation_limit": (_DOWN + "you already have the maximum of {held} reservations there." + _LIMIT_TAIL,
                          _DOWN + "you already have as many reservations there as you're allowed." + _LIMIT_TAIL),
    "reservation_too_long": (_DOWN + "{device} can be reserved for at most {minutes} minutes at a time.",
                             _DOWN + "that's longer than you may reserve {device} for."),
    "reservation_too_short": _DOWN + "a reservation must be at least 10 minutes long.",
    "reservation_too_far_ahead": (_DOWN + "your group there may reserve at most {days} days ahead.",
                                  _DOWN + "that starts further ahead than your group there may reserve."),
    "reservation_in_past": _DOWN + "that time has already passed. Pick a later slot.",
    "reservation_conflict": _DOWN + "someone else has {device} at that time. Pick another slot.",
    "device_not_permitted": _DOWN + "your group there doesn't include {device}.",
    "lab_reservation_limit": _DOWN + "it has reached its limit on reservations from your home deployment. Try again later.",
    "device_in_diagnostics": "{Where} is checking {device} right now; try again in a few minutes.",
    "device_not_visible": "{Device} isn't available to you any more; run getdev to see what is.",
    "principal_unknown": "You're not enrolled at {where} yet.",
    "principal_disabled": "Your account at {where} is disabled; ask its administrator.",
    "principal_not_federated": "Your account at {where} can't be used through RemoteRF Global; ask its administrator.",
    "code_invalid": "That enrollment code isn't valid; check it and try again.",
    "invalid_enrollment_code": "That enrollment code isn't valid; check it and try again.",
    "code_expired": "That enrollment code has expired; ask for a new one.",
    "code_exhausted": "That enrollment code has been used up; ask for a new one.",
    "code_restriction_mismatch": "That enrollment code is for a different account.",
    "group_expired": "The group that code enrolls you in has ended.",
    "group_lan_only": "That group only takes members on {where}'s own network.",
    "group_lifetime_invalid": "{Where} has your group's dates set up wrong; tell its administrator.",
    "enrollment_not_confirmed": "Enrollment didn't go through; check the code and try again.",
    "request_conflict": "That clashed with another request in progress; try again.",
    "contract_not_active": _NOT_SHARING,
    "contract_unknown": _NOT_SHARING,
    "federation_disabled": _NOT_SHARING,
    "contract_selection_ambiguous": "{Where} has more than one agreement with RemoteRF Global; tell its administrator.",
    "destination_unknown": "You don't have access to {where} through RemoteRF Global.",
    "route_unavailable": _LAB_DOWN,
    "transport": _LAB_DOWN,
    "invalid_transport_response": _LAB_DOWN,
    "invalid_signed_result": _LAB_DOWN,
    "invalid_grant": _LAB_DOWN,
    "internal_error": "{Where} hit an internal error; try again in a minute.",
    "unsupported_version": "{Where} runs a RemoteRF version that can't talk to this one; update remoterf (pip install -U remoterf).",
    "federation_unavailable": "RemoteRF Global can't reach deployments right now; try again later.",
    "action_unavailable": "{Where} doesn't offer that yet.",
    "direct_path_unavailable": "{Where} has no direct path for {device}; calls go through the relay.",
    "no_active_reservation": "You don't have an active reservation on {device} right now.",
    "device_unavailable": "{Device} isn't connected right now; try again in a minute.",
    "unsupported_device_rpc": "{Where} can't run that call on {device}; its RemoteRF may be older than yours.",
    "invalid_device_request": "{Where} couldn't read that request; update remoterf (pip install -U remoterf) and try again.",
    "response_too_large": "The reply from {device} is over the RemoteRF Global relay's 768 KiB limit for one call; read fewer samples per call (about 98k) or use a direct path.",
    "result_too_large": "{Where}'s answer was too large to send back; ask for less at once.",
    "finalize_failed": "{Where} couldn't finish that; try again.",
    "invalid_device_token": "That token isn't valid any more (the reservation ended or was cancelled); reserve again.",
    "invalid_device_id": "That device isn't in your list any more; run getdev again.",
    "invalid_reservation_id": "That reservation isn't in your list any more; run myres again.",
    "invalid_credentials": "Wrong username or password.",
    "invalid_access_token": "Your login has expired; log in again.",
    "invalid_refresh_token": "Your saved login has expired; log in again.",
    "refresh_token_reused": "Your saved login was used somewhere else; log in again.",
    "rate_limited": ("Too many requests; try again in {retry_after} s.", "Too many requests; wait a minute and try again."),
    "invalid_email": "That email address isn't valid.",
    "invalid_username": "That username isn't allowed; pick another.",
    "invalid_password": "Password must be 6-128 characters.",
    "invalid_token": "That link or code is invalid or has expired.",
    "registration_disabled": "Registration is closed at this home.",
    "account_disabled": "This account is disabled; ask the administrator.",
    "account_not_found": "No account has that name.",
    "account_already_verified": "That account is already verified.",
    "email_unavailable": "Email can't be sent right now; try again later.",
    "request_too_large": "That request is too large.",
    "invalid_request": _GLOBAL_UPDATE,
    "not_found": "That isn't available at this home yet.",
    "account_request_failed": "RemoteRF Global couldn't do that right now; try again in a minute.",
    "grpc_unavailable": "Can't reach {where} right now; check your connection and try again.",
    "grpc_deadline_exceeded": "{Where} took too long to answer; try again.",
    "grpc_unauthenticated": "{Where} didn't accept your login; log in again.",
    "grpc_permission_denied": "{Where} didn't accept your login; log in again.",
    "grpc_unimplemented": "{Where} runs an older RemoteRF that can't do this yet.",
    "grpc_resource_exhausted": "That reply was too large; ask for less data per call.",
}
_RELAYED = re.compile(r"(Partner refused|Partner unreachable|Federation error): ([a-z][a-z0-9_.-]{0,63})")
_RELAY_PROVENANCE = {"Partner refused": "destination", "Partner unreachable": "transport", "Federation error": "home"}
_CODE = re.compile(r"([a-z][a-z0-9_.-]{0,63})(?: \[(home|transport|destination)\])?(?::.*)?", re.S)


def _fallback(code, provenance, request):
    if code.startswith("grpc_"):
        return "Something went wrong talking to {where}; try again."
    if provenance == "destination":
        return "{Where} turned the " + request + " down."
    if provenance == "transport":
        return _LAB_DOWN
    if code.startswith("invalid_"):
        return _GLOBAL_UPDATE
    return "Your home couldn't do that right now; try again in a minute."


def explain(error, provenance=None, *, where=None, device=None, request="request", **facts):
    """`error`: an exception or its text. `where`/`device`: human names the caller
    knows (never ids); `facts`: held, minutes, days."""
    if isinstance(error, grpc.RpcError) and callable(getattr(error, "code", None)):
        code = f"grpc_{error.code().name.lower()}"
    else:
        text = str(error)
        provenance = provenance or getattr(error, "provenance", None)
        if m := _RELAYED.fullmatch(text):
            code, provenance = m[2], _RELAY_PROVENANCE[m[1]]
        elif m := _CODE.fullmatch(text):
            code, provenance = m[1], m[2] or provenance
        elif text == "Partner unreachable":
            code, provenance = "route_unavailable", "transport"
        elif text == "API Token Invalid" or text.startswith("No schema available for this token"):
            code = "invalid_device_token"
        elif text.startswith("No schema available for device"):  # '... device 5 (offline)'
            code = "device_unavailable"
        else:
            return text
    log.debug("%s [%s]", code, provenance)
    texts = _TEXT.get(code) or _fallback(code, provenance, request)
    values = {"Where": where or "That deployment", "where": where or "that deployment",
              "Device": device or "That device", "device": device or "that device",
              "retry_after": getattr(error, "retry_after", None), **facts}
    values = {k: v for k, v in values.items() if v is not None}
    for template in texts if isinstance(texts, tuple) else (texts,):
        try:
            sentence = template.format_map(values)
            break
        except KeyError:
            continue
    if debug():
        sentence += f" ({code} [{provenance}])" if provenance else f" ({code})"
    return sentence
