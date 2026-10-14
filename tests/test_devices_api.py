"""remoterf.devices(): handles + availability from the saved login's home."""

import json
import time
from types import SimpleNamespace

import pytest

import remoterf
from remoterf.common.utils import map_arg
from remoterf.core import app, availability, direct_path, grpc_client
from remoterf.deployment import state
from remoterf.deployment.http import AccountBackendError

NOW = int(time.time())


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(availability, "_cache", None)


def _lab(monkeypatch, replies, creds=("jdoe", "s3cret")):
    calls = []
    monkeypatch.setattr(state, "load_target", lambda: {"origin": None, "transport": "grpc", "deployment_id": None})
    monkeypatch.setattr(grpc_client, "_active_home_name", lambda: "ucla")
    monkeypatch.setattr(direct_path, "_credentials", lambda: creds)

    def rpc(*, function_name, args):
        calls.append(function_name)
        return SimpleNamespace(results=replies[function_name])

    monkeypatch.setattr(grpc_client, "rpc_client", rpc)
    return calls


def _ok(devices, interval=10):
    return {"UC": map_arg(json.dumps({"now": NOW, "interval": interval, "window": 21600, "devices": devices}))}


LAB_DEVICES = [
    {"local_device_id": "1", "display_name": "Pluto 01", "device_type": "pluto", "online": True,
     "handle": "Pluto_01", "state": "free", "until": None, "next": NOW + 3600},
    {"local_device_id": "2", "display_name": "Pluto 02", "device_type": "pluto", "online": True,
     "handle": "Pluto_02", "state": "busy", "until": NOW + 600, "next": None},
    {"local_device_id": "3", "display_name": "bench", "device_type": None, "online": False,
     "state": "mine", "until": NOW + 900, "next": None},
]


def test_lab_table_and_rows(monkeypatch, capsys):
    calls = _lab(monkeypatch, {"ACC:availability": _ok(LAB_DEVICES)})
    rows = remoterf.devices()
    assert calls == ["ACC:availability"]
    assert [(r["handle"], r["state"], r["online"]) for r in rows] == [
        ("Pluto_01@ucla", "free", True), ("Pluto_02@ucla", "busy", True), (None, "mine", False)]
    assert rows[1]["until"].timestamp() == NOW + 600
    out = capsys.readouterr().out
    assert "DEVICE (token=)" in out and "Pluto_01@ucla" in out
    assert "in use until" in out and "yours until" in out and "bench @ucla (no handle)" in out
    assert availability._hhmm(rows[0]["next"]) in out


def test_hhmm_and_bad_epochs():
    from datetime import datetime, timedelta

    noon = datetime.now().replace(hour=12, minute=5)
    assert availability._hhmm(noon) == "12:05"
    assert availability._hhmm(noon + timedelta(days=1)) == (noon + timedelta(days=1)).strftime("%b %d 12:05")
    assert availability._when(1_760_000_000_000) is None and availability._when(2**62) is None


def test_refusal_is_remembered_locally(monkeypatch, capsys):
    calls = _lab(monkeypatch, {"ACC:availability": {"ace": map_arg("Too many availability requests; try again in 8 s."),
                                                    "retry_after": map_arg(8)}})
    assert remoterf.devices() == [] and remoterf.devices() == []
    assert calls == ["ACC:availability"]
    out = capsys.readouterr().out
    assert out.count("try again in") == 2


def test_stale_saved_login(monkeypatch, capsys):
    _lab(monkeypatch, {"ACC:availability": {"ace": map_arg("Invalid login credentials.")}})
    assert remoterf.devices() == []
    assert "no longer valid" in capsys.readouterr().out


def test_cached_within_interval_and_server_retry_after(monkeypatch, capsys):
    calls = _lab(monkeypatch, {"ACC:availability": _ok(LAB_DEVICES)})
    first = remoterf.devices()
    assert remoterf.devices() is first
    assert calls == ["ACC:availability"]
    assert "(cached; refresh in" in capsys.readouterr().out

    # Cache expired locally but the server still refuses: show the cache and its wait.
    availability._cache = (*availability._cache[:3], 0.0)
    _lab(monkeypatch, {"ACC:availability": {"ace": map_arg("Too many availability requests; try again in 4 s."),
                                            "retry_after": map_arg(4)}})
    assert remoterf.devices() is first
    assert "(cached; refresh in 4 s)" in capsys.readouterr().out

    availability._cache = None
    assert remoterf.devices() == []
    assert "try again in 4 s" in capsys.readouterr().out


def test_old_lab_falls_back_to_get_dev(monkeypatch, capsys):
    calls = _lab(monkeypatch, {
        "ACC:availability": {"ace": map_arg("No matching server function to call: availability")},
        "ACC:get_dev": {"3": map_arg("Pluto 01 (online)"), "4": map_arg("Remote @mit (online)")},
    })
    rows = remoterf.devices()
    assert calls == ["ACC:availability", "ACC:get_dev"]
    assert [(r["handle"], r["state"]) for r in rows] == [("Pluto_01@ucla", "unknown")]
    assert "does not report availability" in capsys.readouterr().out


def test_lab_not_logged_in(monkeypatch, capsys):
    calls = _lab(monkeypatch, {}, creds=None)
    assert remoterf.devices() == []
    assert calls == []
    assert "Not logged in" in capsys.readouterr().out


class FakeBackend:
    def __init__(self, error=None):
        self.error = error

    def availability(self):
        if self.error:
            raise self.error
        return {"devices": [
            {"device_id": "u1:1", "deployment_id": "u1", "deployment_name": "ucla", "local_device_id": "1",
             "display_name": "Pluto 01", "device_type": "pluto", "online": True, "handle": "Pluto_01",
             "state": "mine", "until": NOW + 60, "next": None},
            {"device_id": "u2:5", "deployment_id": "u2", "deployment_name": "Some Lab", "local_device_id": "5",
             "display_name": "Pluto 05", "device_type": "pluto", "online": True, "handle": "Pluto_05",
             "state": "unknown", "until": None, "next": None},
        ], "destinations": [], "interval": 10}

    def devices(self):
        return {"devices": [{"device_id": "u1:1", "deployment_id": "u1", "deployment_name": "ucla",
                             "local_device_id": "1", "display_name": "Pluto 01", "device_type": "pluto",
                             "online": False, "handle": "Pluto_01"}], "destinations": []}


def _global(monkeypatch, backend):
    monkeypatch.setattr(state, "load_target", lambda: {"origin": "https://global.example.org",
                                                       "transport": "https-json", "deployment_id": "x"})
    if isinstance(backend, Exception):
        def fail():
            raise backend
        monkeypatch.setattr(grpc_client, "federated_backend", fail)
    else:
        monkeypatch.setattr(grpc_client, "federated_backend", lambda: backend)
    monkeypatch.setattr(app, "_global_slug", lambda device_id, name: name if name == "ucla" else None)


def test_global_rows(monkeypatch, capsys):
    _global(monkeypatch, FakeBackend())
    rows = remoterf.devices()
    assert [(r["handle"], r["state"], r["lab"]) for r in rows] == [
        ("Pluto_01@ucla", "mine", "ucla"), (None, "unknown", "Some Lab")]
    out = capsys.readouterr().out
    assert "yours until" in out and "Pluto 05 @Some Lab (no handle)" in out and "incomplete" not in out


def test_old_global_falls_back_to_devices(monkeypatch, capsys):
    _global(monkeypatch, FakeBackend(AccountBackendError("not_found")))
    rows = remoterf.devices()
    assert [(r["handle"], r["state"], r["online"]) for r in rows] == [("Pluto_01@ucla", "unknown", False)]
    assert "does not report availability" in capsys.readouterr().out


def test_old_global_over_http_falls_back(monkeypatch, capsys):
    import httpx
    from remoterf_federation_core import local_capabilities

    from remoterf.deployment.backend import HttpsJsonAccountBackend
    from remoterf.deployment.http import JsonTransport

    caps = local_capabilities(deployment_id="550e8400-e29b-41d4-a716-446655440000")
    caps.update(account_transport="https-json", account_api_base="/v2/deployment")
    listing = FakeBackend().devices()
    listing["destinations"] = [{"status": "unavailable", "display_name": "Some Lab"}]
    seen = []

    def handle(request):
        seen.append(request.url.path)
        if request.url.path == "/.well-known/remoterf-deployment":
            return httpx.Response(200, json=caps)
        if request.url.path == "/v2/deployment/devices":
            return httpx.Response(200, json=listing)
        return httpx.Response(404, json={"detail": "Not Found"})  # FastAPI, no such route

    transport = JsonTransport("https://global.example.org", client=httpx.Client(transport=httpx.MockTransport(handle)))
    backend = HttpsJsonAccountBackend(transport.origin, transport=transport, store=object())
    backend.credentials = {"access_token": "t", "access_expires_at": 2**31}
    _global(monkeypatch, backend)
    rows = remoterf.devices()
    assert seen[1:] == ["/v2/deployment/availability", "/v2/deployment/devices"]
    assert [(r["handle"], r["state"]) for r in rows] == [("Pluto_01@ucla", "unknown")]
    out = capsys.readouterr().out
    assert "does not report availability" in out and "may be incomplete" in out


def test_global_rate_limited_without_cache(monkeypatch, capsys):
    _global(monkeypatch, FakeBackend(AccountBackendError("rate_limited", retry_after=7)))
    assert remoterf.devices() == []
    assert "try again in 7 s" in capsys.readouterr().out


def test_global_not_logged_in(monkeypatch, capsys):
    _global(monkeypatch, RuntimeError("Log in to the account home first (run `remoterf`, then `login`)"))
    assert remoterf.devices() == []
    assert "Log in to the account home first" in capsys.readouterr().out


def test_http_error_carries_retry_after():
    import httpx

    from remoterf.deployment.http import JsonTransport

    body = {"error": {"code": "rate_limited", "retry_after": 9}}
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429, json=body)))
    with pytest.raises(AccountBackendError) as raised:
        JsonTransport("https://global.example.org", client=client).request("GET", "/v2/deployment/availability")
    assert (raised.value.code, raised.value.retry_after) == ("rate_limited", 9)
