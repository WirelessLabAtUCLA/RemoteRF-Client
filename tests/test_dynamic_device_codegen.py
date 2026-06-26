import unittest
import sys
import types

fake_grpc_client = types.ModuleType("remoteRF.core.grpc_client")
fake_grpc_client.rpc_client = lambda *args, **kwargs: None
sys.modules.setdefault("remoteRF.core.grpc_client", fake_grpc_client)
from remoteRF.drivers.dynamic_device import _codegen


class DynamicDeviceCodegenTests(unittest.TestCase):
    def test_codegen_emits_calln_for_named_optional_or_multi_arg_methods(self):
        code = _codegen({
            "device_type": "fake_device",
            "client_class": "FakeDevice",
            "driver_version": "0.0.0",
            "schema_hash": "sha256:test",
            "getters": {},
            "setters": {},
            "calls": {
                "call_configure": {
                    "doc": "Configure",
                    "args": [
                        {"name": "freq", "required": True, "type": "float"},
                        {"name": "gain", "required": False, "type": "int", "default": 0},
                        {"name": "options", "required": False, "type": "dict", "default": None},
                    ],
                },
                "call_rx": {"args": []},
                "call_tx": {"args": [{"name": "samples", "required": True, "type": "any"}]},
                "call_legacy": {},
                "call_keyword": {
                    "args": [
                        {"name": "first", "required": False, "kind": "positional_or_keyword"},
                        {"name": "required", "required": True, "kind": "keyword_only"},
                    ],
                },
            },
        })

        compile(code, "<generated>", "exec")
        self.assertIn("def configure(self, freq, gain=_NO_ARG, options=_NO_ARG):", code)
        self.assertIn('return _try_calln("configure", self.token, {', code)
        self.assertIn('"options": options,', code)
        self.assertIn("def rx(self):", code)
        self.assertIn('return _try_call("rx", self.token)', code)
        self.assertIn("def tx(self, samples):", code)
        self.assertIn('return _try_call("tx", self.token, samples)', code)
        self.assertIn("def legacy(self, _v=_NO_ARG):", code)
        self.assertIn("def keyword(self, first=_NO_ARG, *, required):", code)

    def test_generated_helpers_include_calln_and_omit_no_arg_sentinel(self):
        code = _codegen({
            "device_type": "fake_device",
            "client_class": "FakeDevice",
            "driver_version": "0.0.0",
            "schema_hash": "sha256:test",
            "getters": {},
            "setters": {},
            "calls": {},
        })

        self.assertIn("def _try_calln(prop, token, kwargs):", code)
        self.assertIn("if value is _NO_ARG:", code)
        self.assertIn('function_name=f"{_PREFIX}:{prop}:CALLN"', code)


if __name__ == "__main__":
    unittest.main()
