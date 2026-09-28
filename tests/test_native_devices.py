"""`getdev` on a gRPC home: the home's devices first, partner labs after,
grouped by lab; server ids stay internal."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import remoteRF.core.app as client_app
from remoteRF.common.utils import map_arg


def test_native_rows_group_partner_devices_by_lab_after_the_home():
    response = SimpleNamespace(results={
        "16": map_arg("PlutoSDR (online)"),
        "1000001": map_arg("Pluto SDR (OTA) @ucla-deva (offline)"),
        "1000000": map_arg("USRP B210 @ucla-deva (online)"),
        "2": map_arg("HackRF (online, reserved)"),
    })
    fake = Mock(is_https_home=False)
    fake.get_devices.return_value = response
    with patch.object(client_app, "account", fake), patch.object(client_app, "home_name", "ucla"), \
         patch.object(client_app, "current_route", None):
        rows = client_app._native_device_rows()
    assert [(r["lab"], r["name"], r["state"], r["id"]) for r in rows] == [
        ("ucla", "HackRF", "online, reserved", 2),
        ("ucla", "PlutoSDR", "online", 16),
        ("ucla-deva", "Pluto SDR (OTA)", "offline", 1000001),
        ("ucla-deva", "USRP B210", "online", 1000000),
    ]


def test_enroll_strips_the_lab_prefix_from_a_pasted_code():
    from types import SimpleNamespace
    fake = Mock(is_https_home=False)
    fake.set_enroll.return_value = SimpleNamespace(results={"UC": map_arg("ok")})
    with patch.object(client_app, "account", fake), patch.object(client_app, "home_name", "otheruni"):
        assert client_app.enroll("otheruni.global.remoterf.net/cluster.875IO19HSA") is True
        assert fake.enrollment_code == "cluster.875IO19HSA"
        # A code for a different lab is refused here rather than sent.
        assert client_app.enroll("ucla/ABC123") is False
        assert fake.enrollment_code == "cluster.875IO19HSA"


def test_many_devices_across_labs_ask_for_a_lab_first():
    rows = [{"id": i, "name": f"d{i}", "lab": "ucla" if i < 8 else "otheruni", "state": "online", "away": i >= 8}
            for i in range(12)]
    with patch.object(client_app, "session") as session:
        session.prompt.return_value = "2"
        kept = client_app._pick_lab(rows)
    assert {r["lab"] for r in kept} == {"otheruni"} and len(kept) == 4
    with patch.object(client_app, "session") as session:
        session.prompt.return_value = ""
        assert client_app._pick_lab(rows) == rows  # Enter keeps everything
    # Few devices, or a single lab: no extra step at all.
    with patch.object(client_app, "session") as session:
        assert client_app._pick_lab(rows[:5]) == rows[:5]
        assert client_app._pick_lab([r for r in rows if r["lab"] == "ucla"] * 2) is not None
        session.prompt.assert_not_called()
