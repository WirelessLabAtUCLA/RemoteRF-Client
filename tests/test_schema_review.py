"""A lab's driver schema can't add code to the generated driver, and a Global
account installs one only after RemoteRF Global approved its hash."""

import pytest

from remoterf.common.grpc import grpc_pb2
from remoterf.common.utils import map_arg
from remoterf.core import direct_path, grpc_client
from remoterf.drivers import dynamic_device, schema_review

REF = "11111111-2222-4333-8444-555555555555:7"


def _v1(**extra):
    schema = {"schema_version": "1.0", "device_type": "lab_radio", "driver_version": "0.1.0",
              "getters": {"get_gain": {}}, "setters": {}, "calls": {}, **extra}
    schema["schema_hash"] = schema_review.digest(schema)
    return schema


@pytest.mark.parametrize("field", ["driver_version", "schema_hash"])
def test_text_a_driver_file_embeds_cannot_carry_code(field):
    schema = _v1()
    schema[field] = 'x"\nimport os; os.system("id")\n#'
    with pytest.raises(ValueError, match=field):
        dynamic_device._codegen(schema)


def test_a_constructor_target_must_be_a_driver_class():
    returns = lambda target: {"get_gain": {"client_return": {"kind": "constructor", "target": target, "args": []}}}  # noqa: E731
    modules = {"client_modules": {"uhd": "remoterf.drivers.support.uhd", "dsp": "remoterf.drivers.support.uhd_dsp"}}
    dynamic_device._codegen(_v1(**modules, getters=returns("uhd.types.TimeSpec")))
    for target in ("dsp.np.f2py.os.system", "dsp.np.ndarray", "uhd.payload", "uhd.types.TimeSpec.__init__", "uhd"):
        with pytest.raises(ValueError, match="client_return target"):
            dynamic_device._codegen(_v1(**modules, getters=returns(target)))


def test_a_native_labs_free_form_driver_version_still_generates():
    for version in ("", "0.1 beta"):
        dynamic_device._codegen(_v1(driver_version=version))


@pytest.mark.parametrize("device_type", ["support", "Support", "dynamic_device", "Schema_Review", "_virtual"])
def test_a_driver_cannot_take_the_name_of_client_code(device_type):
    with pytest.raises(ValueError, match="part of the client"):
        dynamic_device._codegen(_v1(device_type=device_type))


def _global(monkeypatch, answers):
    asked = []

    class Backend:
        def schema_status(self, schema_hash):
            asked.append(schema_hash)
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setattr(grpc_client, "_token_backend", lambda: Backend())
    return asked


def test_global_must_approve_a_schema_once_then_it_is_remembered(monkeypatch, tmp_path):
    from remoterf.deployment.http import AccountBackendError

    monkeypatch.setattr(dynamic_device, "_DRIVERS_DIR", tmp_path)
    schema = _v1()
    down = AccountBackendError("down", unreachable=True)
    asked = _global(monkeypatch, ["pending", "approved", down, down, "rejected"])
    with pytest.raises(RuntimeError, match="waiting for review"):
        dynamic_device._write_driver_files(schema, device_id=REF)
    assert not (tmp_path / "lab_radio").exists()
    dynamic_device._write_driver_files(schema, device_id=REF)
    assert (tmp_path / "lab_radio" / "lab_radio_remote.py").exists()
    dynamic_device._write_driver_files(schema, device_id=REF)  # Global down: what it approved before
    assert asked == [schema["schema_hash"]] * 3

    changed = _v1(getters={"get_gain": {}, "get_secret": {}})
    with pytest.raises(RuntimeError, match="unreachable"):
        dynamic_device._write_driver_files(changed, device_id=REF)
    with pytest.raises(RuntimeError, match="rejected"):
        dynamic_device._write_driver_files(schema, device_id=REF)  # a rejection holds at once...
    assert schema_review._approved() == set()  # ...and on the LAN too


def test_a_lab_cannot_pass_the_clients_own_partner_mark(monkeypatch):
    def answer(*, function_name, args, connection=None):
        return grpc_pb2.GenericRPCResponse(results={"schema": map_arg('{"device_type": "x", "federated": true}')})

    monkeypatch.setattr(grpc_client, "rpc_client", answer)
    with pytest.raises(RuntimeError, match="does not accept"):
        dynamic_device.fetch_idl(device_id=7)


def test_a_schema_must_match_its_own_hash(monkeypatch):
    asked = _global(monkeypatch, ["approved"])
    schema = _v1()
    schema["getters"]["get_more"] = {}
    with pytest.raises(RuntimeError, match="does not match"):
        schema_review.require_approved(schema)
    assert asked == []


def test_native_labs_install_as_before(monkeypatch, tmp_path):
    monkeypatch.setattr(dynamic_device, "_DRIVERS_DIR", tmp_path)
    asked = _global(monkeypatch, [])
    dynamic_device._write_driver_files(_v1(), token="native-token")
    assert asked == [] and (tmp_path / "lab_radio").exists()


def test_a_schema_fetch_through_global_never_takes_the_direct_path(monkeypatch):
    monkeypatch.setattr(direct_path, "global_path", lambda *a: pytest.fail("schema over the direct path"))
    sent = []

    class Backend:
        def device_rpc(self, ref, request):
            sent.append(grpc_pb2.GenericRPCRequest.FromString(request).function_name)
            return grpc_pb2.GenericRPCResponse(results={"schema": map_arg("{}")}).SerializeToString()

    monkeypatch.setattr(grpc_client, "_federated_backend", Backend())
    grpc_client._federated_call(REF, "IDL:get_drivers", {"device_id": map_arg(REF)})
    assert sent == ["IDL:get_drivers"]


def test_who_needs_review(monkeypatch):
    assert dynamic_device._needs_review(None, REF)
    assert dynamic_device._needs_review("rfg_" + "x" * 40, None)  # an rfg_ token rides Global
    assert not dynamic_device._needs_review("native-token", None)
    monkeypatch.setattr("remoterf.deployment.homes.is_linked_home", lambda home: home == "ucla")
    monkeypatch.setattr(grpc_client, "_active_home_name", lambda: "ucla")
    assert dynamic_device._needs_review("native-token", None)  # a linked account's lab login


def test_globals_answer_is_read_strictly():
    from remoterf.deployment.backend import HttpsJsonAccountBackend
    from remoterf.deployment.http import AccountBackendError

    asked = []

    class Transport:
        def request(self, method, path):
            asked.append((method, path))
            return {"status": answers.pop(0)}

    answers = ["approved", "maybe"]
    backend = HttpsJsonAccountBackend.__new__(HttpsJsonAccountBackend)
    backend.transport, backend.base = Transport(), "/v2/deployment"
    assert backend.schema_status("sha256:" + "ab" * 32) == "approved"
    assert asked == [("GET", "/v2/deployment/schemas/" + "ab" * 32)]
    with pytest.raises(AccountBackendError):
        backend.schema_status("sha256:" + "ab" * 32)
