import unittest
import sys
import tempfile
import types
from pathlib import Path

import numpy as np

from remoteRF.common.utils import map_arg, unmap_arg

fake_grpc_client = types.ModuleType("remoteRF.core.grpc_client")
fake_grpc_client.rpc_client = lambda *args, **kwargs: None
sys.modules.setdefault("remoteRF.core.grpc_client", fake_grpc_client)
import remoteRF.drivers.dynamic_device as dynamic_device
from remoteRF.drivers.dynamic_device import _codegen


def _method(template):
    return {"kind": "method", "template": template}


def _function(template):
    return {"kind": "function", "template": template}


def _uhd_client_objects():
    return {
        "uhd": {
            "kind": "module",
            "exports": [
                "usrp",
                "types",
                "libpyuhd",
                "payload",
                "get_rx_stream",
                "get_tx_stream",
                "close_all_streams",
                "streamer",
                "RXStreamer",
                "TXStreamer",
            ],
            "bind_client_class_to": "usrp",
            "members": {
                "payload": _function("uhd_payload"),
                "get_rx_stream": _function("uhd_get_rx_stream"),
                "get_tx_stream": _function("uhd_get_tx_stream"),
                "close_all_streams": _function("uhd_close_all_streams"),
                "streamer": _function("uhd_streamer"),
                "RXStreamer": {
                    "kind": "proxy_class",
                    "fields": [{"name": "usrp"}, {"name": "handle"}],
                    "init": {"template": "streamer_handle"},
                    "methods": [
                        _method("streamer_get_max_num_samps"),
                        _method("streamer_issue_stream_cmd"),
                        _method("streamer_close"),
                        _method("rx_streamer_recv"),
                    ],
                },
                "TXStreamer": {
                    "kind": "proxy_class",
                    "fields": [{"name": "usrp"}, {"name": "handle"}],
                    "init": {"template": "streamer_handle"},
                    "methods": [
                        _method("streamer_get_max_num_samps"),
                        _method("streamer_issue_stream_cmd"),
                        _method("streamer_close"),
                        _method("tx_streamer_send"),
                    ],
                },
                "usrp": {
                    "kind": "namespace",
                    "members": {
                        "SubdevSpec": _function("uhd_subdev_spec"),
                        "StreamArgs": {
                            "kind": "payload_class",
                            "payload_type": "StreamArgs",
                            "fields": [
                                {"name": "cpu_format"},
                                {"name": "otw_format"},
                                {"name": "args"},
                                {"name": "channels"},
                            ],
                            "init": {"template": "stream_args"},
                            "methods": [_method("payload_as_payload")],
                        },
                    },
                },
                "types": {
                    "kind": "namespace",
                    "members": {
                        "TimeSpec": {
                            "kind": "payload_class",
                            "payload_type": "TimeSpec",
                            "fields": [{"name": "secs", "default": 0.0}],
                            "init": {"template": "timespec"},
                            "methods": [
                                _method("timespec_get_real_secs"),
                                _method("timespec_get_full_secs"),
                                _method("timespec_get_frac_secs"),
                                _method("timespec_to_ticks"),
                                _method("timespec_get_tick_count"),
                                _method("timespec_float"),
                                _method("payload_as_payload"),
                            ],
                            "operators": ["timespec_numeric"],
                        },
                        "StreamCMD": {
                            "kind": "payload_class",
                            "payload_type": "StreamCMD",
                            "fields": [
                                {"name": "mode"},
                                {"name": "stream_now"},
                                {"name": "time_spec"},
                                {"name": "num_samps"},
                            ],
                            "init": {"template": "stream_cmd"},
                            "methods": [_method("payload_as_payload")],
                        },
                        "StreamMode": {
                            "kind": "enum",
                            "values": ["num_done", "num_more", "stop_cont", "start_cont"],
                        },
                        "RXMetadata": {
                            "kind": "strict_class",
                            "fields": [
                                {"name": "error_code"},
                                {"name": "error_code_repr"},
                                {"name": "time_spec"},
                                {"name": "out_of_sequence"},
                                {"name": "fragment_offset"},
                                {"name": "more_fragments"},
                            ],
                            "init": {"template": "rx_metadata"},
                            "methods": [
                                _method("metadata_update"),
                                _method("metadata_strerror"),
                            ],
                        },
                        "TXMetadata": {
                            "kind": "strict_class",
                            "fields": [
                                {"name": "has_time_spec"},
                                {"name": "time_spec"},
                                {"name": "end_of_burst"},
                            ],
                            "init": {"template": "tx_metadata"},
                            "methods": [_method("payload_as_payload")],
                        },
                        "RXMetadataErrorCode": {
                            "kind": "enum",
                            "values": [
                                "none",
                                "timeout",
                                "overflow",
                                "late_command",
                                "broken_chain",
                                "alignment",
                                "bad_packet",
                            ],
                        },
                    },
                },
                "libpyuhd": {
                    "kind": "namespace",
                    "members": {
                        "types": {
                            "kind": "namespace",
                            "members": {
                                "tune_request": _function("uhd_tune_request"),
                            },
                        },
                    },
                },
            },
        },
    }


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

    def test_constructor_ip_ping_only_emitted_when_schema_declares_ip(self):
        base = {
            "device_type": "fake_device",
            "client_class": "FakeDevice",
            "driver_version": "0.0.0",
            "schema_hash": "sha256:test",
            "getters": {},
            "setters": {},
        }

        with_ip = _codegen(dict(base, calls={"call_ip": {"args": []}}))
        without_ip = _codegen(dict(base, calls={"call_rx": {"args": []}}))

        self.assertIn('_try_call("ip", token)', with_ip)
        self.assertNotIn('_try_call("ip", token)', without_ip)

    def test_pluto_style_schema_preserves_get_set_call0_and_call1(self):
        code = _codegen({
            "device_type": "adalm_pluto",
            "client_class": "Pluto",
            "driver_version": "0.0.1",
            "schema_hash": "sha256:pluto",
            "getters": {
                "get_sample_rate": {"args": []},
            },
            "setters": {
                "set_sample_rate": {
                    "args": [{"name": "value", "required": True, "type": "any"}],
                },
            },
            "calls": {
                "call_rx": {"args": []},
                "call_tx": {
                    "args": [{"name": "value", "required": True, "type": "any"}],
                },
                "call_disable_dds": {"args": []},
            },
        })

        compile(code, "<generated-pluto>", "exec")
        self.assertIn('function_name=f"{_PREFIX}:{prop}:GET"', code)
        self.assertIn('function_name=f"{_PREFIX}:{prop}:SET"', code)
        self.assertIn("def rx(self):", code)
        self.assertIn('return _try_call("rx", self.token)', code)
        self.assertIn("def tx(self, value):", code)
        self.assertIn('return _try_call("tx", self.token, value)', code)
        self.assertNotIn('return _try_calln("tx"', code)

    def test_legacy_schema_runtime_returns_raw_values_unchanged(self):
        schema = {
            "device_type": "fake_device",
            "client_class": "FakeDevice",
            "driver_version": "0.0.0",
            "schema_hash": "sha256:legacy",
            "getters": {
                "get_status": {"doc": 'Status with """quotes""" and newline\nok'},
            },
            "setters": {},
            "calls": {
                "call_ip": {"args": []},
                "call_echo": {"args": [{"name": "value", "required": True}]},
                "call_configure": {
                    "args": [
                        {"name": "freq", "required": True},
                        {"name": "gain", "required": False},
                    ],
                },
            },
        }
        seen = []

        class Response:
            def __init__(self, results):
                self.results = results

        def rpc_client(function_name, args):
            prop = function_name.split(":")[1]
            seen.append((function_name, args))
            if prop == "ip":
                return Response({prop: map_arg("ok")})
            if prop == "status":
                return Response({prop: map_arg({"raw": True})})
            if prop == "echo":
                return Response({prop: args["arg1"]})
            if prop == "configure":
                return Response({prop: map_arg({"configured": True})})
            raise AssertionError(function_name)

        old_rpc_client = fake_grpc_client.rpc_client
        old_stale_check = dynamic_device.install_driver_if_stale
        fake_grpc_client.rpc_client = rpc_client
        dynamic_device.install_driver_if_stale = lambda **kwargs: False
        try:
            module = types.ModuleType("remoteRF.drivers.fake_device.fake_device_remote")
            module.__package__ = "remoteRF.drivers.fake_device"
            code = _codegen(schema)
            compile(code, "<generated-legacy>", "exec")
            exec(code, module.__dict__)

            device = module.FakeDevice("token")
            self.assertEqual(device.status, {"raw": True})
            self.assertEqual(device.echo({"payload": 3}), {"payload": 3})
            self.assertEqual(device.configure(1.0), {"configured": True})
        finally:
            fake_grpc_client.rpc_client = old_rpc_client
            dynamic_device.install_driver_if_stale = old_stale_check

        self.assertEqual(
            [item[0] for item in seen],
            [
                "Fake_device:ip:CALL0",
                "Fake_device:status:GET",
                "Fake_device:echo:CALL1",
                "Fake_device:configure:CALLN",
            ],
        )

    def test_codegen_wraps_declared_client_return_constructors(self):
        schema = {
            "device_type": "usrp",
            "client_class": "MultiUSRP",
            "client_modules": {"uhd": "remoteRF.drivers.support.uhd"},
            "driver_version": "0.0.1",
            "schema_hash": "sha256:usrp",
            "getters": {},
            "setters": {},
            "calls": {
                "call_ip": {"args": []},
                "call_get_time_now": {
                    "args": [],
                    "client_return": {
                        "kind": "constructor",
                        "target": "uhd.types.TimeSpec",
                        "args": ["$result"],
                    },
                },
                "call_get_rx_stream": {
                    "args": [
                        {"name": "stream_args", "required": True, "type": "any"},
                    ],
                    "client_return": {
                        "kind": "constructor",
                        "target": "uhd.RXStreamer",
                        "args": ["$self", "$result"],
                    },
                },
            },
        }

        seen_args = {}

        class Response:
            def __init__(self, results):
                self.results = results

        def rpc_client(function_name, args):
            prop = function_name.split(":")[1]
            seen_args[prop] = args
            if prop == "ip":
                return Response({prop: map_arg("ok")})
            if prop == "get_time_now":
                return Response({prop: map_arg({"__uhd_type__": "TimeSpec", "secs": 42.5})})
            if prop == "get_rx_stream":
                return Response({prop: map_arg({"__uhd_type__": "RXStreamer", "handle": "rx:1"})})
            raise AssertionError(function_name)

        old_rpc_client = fake_grpc_client.rpc_client
        old_stale_check = dynamic_device.install_driver_if_stale
        fake_grpc_client.rpc_client = rpc_client
        dynamic_device.install_driver_if_stale = lambda **kwargs: False
        try:
            module = types.ModuleType("remoteRF.drivers.usrp.usrp_remote")
            module.__package__ = "remoteRF.drivers.usrp"
            exec(_codegen(schema), module.__dict__)

            usrp = module.MultiUSRP("token")
            time_spec = usrp.get_time_now()
            stream_args = module.uhd.usrp.StreamArgs("fc32", "sc16")
            stream_args.channels = [0, 1]
            streamer = usrp.get_rx_stream(stream_args)
        finally:
            fake_grpc_client.rpc_client = old_rpc_client
            dynamic_device.install_driver_if_stale = old_stale_check

        self.assertEqual(time_spec.get_real_secs(), 42.5)
        self.assertIsInstance(streamer, module.uhd.RXStreamer)
        self.assertIs(streamer.usrp, usrp)
        self.assertEqual(streamer.handle, "rx:1")
        self.assertEqual(
            unmap_arg(seen_args["get_rx_stream"]["arg1"]),
            {
                "__uhd_type__": "StreamArgs",
                "cpu_format": "fc32",
                "otw_format": "sc16",
                "args": {},
                "channels": [0, 1],
            },
        )

    def test_codegen_generates_declared_client_objects_without_support_module(self):
        schema = {
            "device_type": "usrp",
            "client_class": "MultiUSRP",
            "client_objects": _uhd_client_objects(),
            "driver_version": "0.0.1",
            "schema_hash": "sha256:usrp-generated",
            "getters": {},
            "setters": {},
            "calls": {
                "call_ip": {"args": []},
                "call_get_time_now": {
                    "args": [],
                    "client_return": {
                        "kind": "constructor",
                        "target": "uhd.types.TimeSpec",
                        "args": ["$result"],
                    },
                },
                "call_get_rx_stream": {
                    "args": [{"name": "stream_args", "required": True}],
                    "client_return": {
                        "kind": "constructor",
                        "target": "uhd.RXStreamer",
                        "args": ["$self", "$result"],
                    },
                },
                "call_get_tx_stream": {
                    "args": [{"name": "stream_args", "required": True}],
                    "client_return": {
                        "kind": "constructor",
                        "target": "uhd.TXStreamer",
                        "args": ["$self", "$result"],
                    },
                },
                "call_stream_get_max_num_samps": {
                    "args": [{"name": "handle", "required": True}],
                },
                "call_stream_issue_stream_cmd": {
                    "args": [{"name": "value", "required": True}],
                },
                "call_stream_recv": {
                    "args": [{"name": "value", "required": True}],
                },
                "call_stream_send": {
                    "args": [{"name": "value", "required": True}],
                },
                "call_stream_close": {
                    "args": [{"name": "handle", "required": True}],
                },
                "call_close_all_streams": {
                    "args": [],
                },
            },
        }

        seen_args = {}

        class Response:
            def __init__(self, results):
                self.results = results

        def rpc_client(function_name, args):
            prop = function_name.split(":")[1]
            seen_args[prop] = args
            if prop == "ip":
                return Response({prop: map_arg("ok")})
            if prop == "get_time_now":
                return Response({prop: map_arg({"__uhd_type__": "TimeSpec", "secs": 42.5})})
            if prop == "get_rx_stream":
                return Response({prop: map_arg({"__uhd_type__": "RXStreamer", "handle": "rx:1"})})
            if prop == "get_tx_stream":
                return Response({prop: map_arg({"__uhd_type__": "TXStreamer", "handle": "tx:1"})})
            if prop == "stream_get_max_num_samps":
                return Response({prop: map_arg(256)})
            if prop == "stream_issue_stream_cmd":
                return Response({prop: map_arg(None)})
            if prop == "stream_recv":
                payload = unmap_arg(args["arg1"])
                return Response({
                    prop: map_arg((
                        2,
                        payload["recv_buffer"] + 1,
                        {
                            "error_code": "none",
                            "error_code_repr": "none",
                            "time_spec": {"__uhd_type__": "TimeSpec", "secs": 1.25},
                        },
                    ))
                })
            if prop == "stream_send":
                return Response({prop: map_arg(3)})
            if prop == "stream_close":
                return Response({prop: map_arg(None)})
            if prop == "close_all_streams":
                return Response({prop: map_arg(2)})
            raise AssertionError(function_name)

        old_rpc_client = fake_grpc_client.rpc_client
        old_stale_check = dynamic_device.install_driver_if_stale
        fake_grpc_client.rpc_client = rpc_client
        dynamic_device.install_driver_if_stale = lambda **kwargs: False
        try:
            code = _codegen(schema)
            self.assertNotIn("remoteRF.drivers.support.uhd", code)
            self.assertIn("_CLIENT_MODULES = {}", code)
            compile(code, "<generated-client-objects>", "exec")

            module = types.ModuleType("remoteRF.drivers.usrp.usrp_remote")
            module.__package__ = "remoteRF.drivers.usrp"
            exec(code, module.__dict__)

            usrp = module.MultiUSRP("token")
            self.assertIs(module.uhd.usrp.MultiUSRP, module.MultiUSRP)

            time_spec = usrp.get_time_now()
            self.assertEqual(time_spec.get_real_secs(), 42.5)
            self.assertEqual(time_spec.get_full_secs(), 42)
            self.assertAlmostEqual(time_spec.get_frac_secs(), 0.5)
            self.assertEqual(time_spec.to_ticks(10), 425)
            self.assertEqual((time_spec + 0.5).get_real_secs(), 43.0)
            self.assertEqual(float(time_spec - module.uhd.types.TimeSpec(2.5)), 40.0)
            self.assertEqual(module.uhd.types.TimeSpec.from_ticks(25, 10).get_real_secs(), 2.5)
            self.assertEqual(
                unmap_arg(map_arg(module.uhd.types.TimeSpec({"secs": 3.0}))),
                {"__uhd_type__": "TimeSpec", "secs": 3.0},
            )
            self.assertEqual(
                module.uhd.payload({
                    "when": module.uhd.types.TimeSpec(1.5),
                    "nested": (module.uhd.types.TimeSpec(2.5), [module.uhd.types.TimeSpec(3.5)]),
                }),
                {
                    "when": {"__uhd_type__": "TimeSpec", "secs": 1.5},
                    "nested": [
                        {"__uhd_type__": "TimeSpec", "secs": 2.5},
                        [{"__uhd_type__": "TimeSpec", "secs": 3.5}],
                    ],
                },
            )

            stream_args = module.uhd.usrp.StreamArgs("fc32", "sc16")
            stream_args.channels = [0, 1]
            rx_streamer = usrp.get_rx_stream(stream_args)
            tx_streamer = usrp.get_tx_stream(stream_args)
            self.assertIsInstance(rx_streamer, module.uhd.RXStreamer)
            self.assertIsInstance(tx_streamer, module.uhd.TXStreamer)
            self.assertEqual(rx_streamer.get_max_num_samps(), 256)
            self.assertIsInstance(module.uhd.get_rx_stream(usrp, stream_args), module.uhd.RXStreamer)
            self.assertIsInstance(module.uhd.get_tx_stream(usrp, stream_args), module.uhd.TXStreamer)
            self.assertEqual(module.uhd.close_all_streams(usrp), 2)
            with self.assertRaisesRegex(ValueError, "Unknown streamer payload type"):
                module.uhd.streamer(usrp, {"__uhd_type__": "BogusStreamer", "handle": "x"})
            with self.assertRaisesRegex(TypeError, "Expected streamer payload"):
                module.uhd.streamer(usrp, "rx:1")

            cmd = module.uhd.types.StreamCMD(module.uhd.types.StreamMode.num_done)
            cmd.stream_now = False
            cmd.time_spec = module.uhd.types.TimeSpec(2.5)
            cmd.num_samps = 128
            rx_streamer.issue_stream_cmd(cmd)
            self.assertEqual(str(module.uhd.types.StreamMode.num_done), "num_done")
            self.assertEqual(module.uhd.types.StreamMode.num_done, "num_done")
            self.assertNotEqual(module.uhd.types.StreamMode.num_done, "num_more")

            recv_buffer = np.array([1 + 0j, 2 + 0j], dtype=np.complex64)
            metadata = module.uhd.types.RXMetadata()
            with self.assertRaisesRegex(AttributeError, "does not support attribute"):
                metadata.unexpected = True
            self.assertEqual(rx_streamer.recv(recv_buffer, metadata), 2)
            np.testing.assert_allclose(recv_buffer, np.array([2 + 0j, 3 + 0j]))
            self.assertEqual(metadata.strerror(), "none")
            self.assertEqual(metadata.time_spec.get_real_secs(), 1.25)

            tx_metadata = module.uhd.types.TXMetadata()
            tx_metadata.time_spec = module.uhd.types.TimeSpec(5.0)
            tx_metadata.has_time_spec = True
            self.assertEqual(tx_streamer.send(np.array([1, 2, 3]), tx_metadata), 3)
            self.assertIsNone(tx_streamer.close())

            tune = module.uhd.libpyuhd.types.tune_request(915e6)
            self.assertEqual(tune["__uhd_type__"], "TuneRequest")
            self.assertEqual(module.uhd.usrp.SubdevSpec("A:0"), {
                "__uhd_type__": "SubdevSpec",
                "spec": "A:0",
            })
        finally:
            fake_grpc_client.rpc_client = old_rpc_client
            dynamic_device.install_driver_if_stale = old_stale_check

        self.assertEqual(
            unmap_arg(seen_args["get_rx_stream"]["arg1"]),
            {
                "__uhd_type__": "StreamArgs",
                "cpu_format": "fc32",
                "otw_format": "sc16",
                "args": {},
                "channels": [0, 1],
            },
        )
        self.assertEqual(
            unmap_arg(seen_args["stream_issue_stream_cmd"]["arg1"]),
            {
                "handle": "rx:1",
                "stream_cmd": {
                    "__uhd_type__": "StreamCMD",
                    "mode": "num_done",
                    "stream_now": False,
                    "time_spec": {"__uhd_type__": "TimeSpec", "secs": 2.5},
                    "num_samps": 128,
                },
            },
        )

    def test_generated_client_objects_are_exported_from_package_init(self):
        schema = {
            "device_type": "usrp",
            "client_class": "MultiUSRP",
            "client_objects": _uhd_client_objects(),
            "driver_version": "0.0.1",
            "schema_hash": "sha256:usrp-generated",
            "getters": {},
            "setters": {},
            "calls": {"call_ip": {"args": []}},
        }

        old_drivers_dir = dynamic_device._DRIVERS_DIR
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                dynamic_device._DRIVERS_DIR = Path(temp_dir)
                package_dir = dynamic_device._write_driver_files(schema)
                init_text = (package_dir / "__init__.py").read_text(encoding="utf-8")
                remote_text = (package_dir / "usrp_remote.py").read_text(encoding="utf-8")
        finally:
            dynamic_device._DRIVERS_DIR = old_drivers_dir

        self.assertIn("from .usrp_remote import uhd", init_text)
        self.assertIn('__all__.append("uhd")', init_text)
        self.assertIn("globals().setdefault('types', getattr(uhd, 'types'))", init_text)
        self.assertIn("uhd.usrp.MultiUSRP = MultiUSRP", remote_text)
        self.assertNotIn("remoteRF.drivers.support.uhd", remote_text)

    def test_codegen_rejects_invalid_client_metadata_before_compile(self):
        base = {
            "device_type": "usrp",
            "client_class": "MultiUSRP",
            "driver_version": "0.0.1",
            "schema_hash": "sha256:bad",
            "getters": {},
            "setters": {},
            "calls": {
                "call_ip": {"args": []},
            },
        }

        bad_alias = dict(base, client_modules={"bad-alias": "remoteRF.drivers.support.uhd"})
        with self.assertRaisesRegex(ValueError, "alias"):
            _codegen(bad_alias)

        bad_module = dict(base, client_modules={"uhd": "os"})
        with self.assertRaisesRegex(ValueError, "remoteRF.drivers.support"):
            _codegen(bad_module)

        bad_class = dict(base, client_class="Bad Class")
        with self.assertRaisesRegex(ValueError, "client_class"):
            _codegen(bad_class)

        bad_device_type = dict(base, device_type="../usrp")
        with self.assertRaisesRegex(ValueError, "device_type"):
            _codegen(bad_device_type)

        bad_return = dict(
            base,
            client_modules={"uhd": "remoteRF.drivers.support.uhd"},
            calls={
                "call_ip": {"args": []},
                "call_get_time_now": {
                    "args": [],
                    "client_return": {
                        "kind": "constructor",
                        "target": "missing.TimeSpec",
                        "args": ["$result"],
                    },
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "not declared"):
            _codegen(bad_return)

        bad_setter_return = dict(
            base,
            client_modules={"uhd": "remoteRF.drivers.support.uhd"},
            setters={
                "set_level": {
                    "client_return": {
                        "kind": "constructor",
                        "target": "uhd.types.TimeSpec",
                        "args": ["$result"],
                    },
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "setter"):
            _codegen(bad_setter_return)

        duplicate_roots = dict(
            base,
            client_modules={"uhd": "remoteRF.drivers.support.uhd"},
            client_objects={"uhd": {"kind": "module", "members": {}}},
        )
        with self.assertRaisesRegex(ValueError, "duplicates"):
            _codegen(duplicate_roots)

        bad_object_template = dict(
            base,
            client_objects={
                "x": {
                    "kind": "module",
                    "members": {
                        "Thing": {
                            "kind": "payload_class",
                            "payload_type": "Thing",
                            "fields": [{"name": "value"}],
                            "init": {"template": "unknown"},
                        },
                    },
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "unknown template"):
            _codegen(bad_object_template)

        bad_object_field = dict(
            base,
            client_objects={
                "x": {
                    "kind": "module",
                    "members": {
                        "Thing": {
                            "kind": "payload_class",
                            "payload_type": "Thing",
                            "fields": [{"name": "bad-name"}],
                        },
                    },
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "field"):
            _codegen(bad_object_field)

        bad_object_default = dict(
            base,
            client_objects={
                "x": {
                    "kind": "module",
                    "members": {
                        "Thing": {
                            "kind": "payload_class",
                            "payload_type": "Thing",
                            "fields": [{"name": "value", "default": object()}],
                        },
                    },
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "JSON serializable"):
            _codegen(bad_object_default)

        nested_module = dict(
            base,
            client_objects={
                "x": {
                    "kind": "module",
                    "members": {
                        "nested": {"kind": "module", "members": {}},
                    },
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "nested client module"):
            _codegen(nested_module)

        duplicate_generated_name = dict(
            base,
            client_objects={
                "x": {
                    "kind": "module",
                    "members": {
                        "first": {
                            "kind": "namespace",
                            "members": {
                                "Thing": {
                                    "kind": "payload_class",
                                    "payload_type": "Thing",
                                    "fields": [{"name": "value"}],
                                },
                            },
                        },
                        "second": {
                            "kind": "namespace",
                            "members": {
                                "Thing": {
                                    "kind": "proxy_class",
                                    "fields": [{"name": "handle"}],
                                },
                            },
                        },
                    },
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "duplicates generated"):
            _codegen(duplicate_generated_name)

        missing_bind_path = dict(
            base,
            client_objects={
                "x": {
                    "kind": "module",
                    "members": {},
                    "bind_client_class_to": "missing",
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "unknown member"):
            _codegen(missing_bind_path)

        non_namespace_bind_path = dict(
            base,
            client_objects={
                "x": {
                    "kind": "module",
                    "members": {
                        "Thing": {
                            "kind": "payload_class",
                            "payload_type": "Thing",
                            "fields": [{"name": "value"}],
                        },
                    },
                    "bind_client_class_to": "Thing",
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "client namespace"):
            _codegen(non_namespace_bind_path)

        unknown_function_template = dict(
            base,
            client_objects={
                "x": {
                    "kind": "module",
                    "members": {
                        "helper": {"kind": "function", "template": "exec_python"},
                    },
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "unknown template"):
            _codegen(unknown_function_template)

        unknown_method_template = dict(
            base,
            client_objects={
                "x": {
                    "kind": "module",
                    "members": {
                        "Thing": {
                            "kind": "payload_class",
                            "payload_type": "Thing",
                            "fields": [{"name": "value"}],
                            "methods": [{"kind": "method", "template": "exec_python"}],
                        },
                    },
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "unknown template"):
            _codegen(unknown_method_template)

        non_json_options = dict(
            base,
            client_objects={
                "x": {
                    "kind": "module",
                    "members": {
                        "helper": {
                            "kind": "function",
                            "template": "uhd_subdev_spec",
                            "options": {"callback": object()},
                        },
                    },
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "JSON serializable"):
            _codegen(non_json_options)

    def test_codegen_rejects_invalid_method_and_argument_shapes(self):
        with self.assertRaisesRegex(ValueError, "method"):
            _codegen({
                "device_type": "fake_device",
                "client_class": "FakeDevice",
                "driver_version": "0.0.0",
                "schema_hash": "sha256:test",
                "getters": {},
                "setters": {},
                "calls": {"call_bad-name": {"args": []}},
            })

        with self.assertRaisesRegex(ValueError, "required positional"):
            _codegen({
                "device_type": "fake_device",
                "client_class": "FakeDevice",
                "driver_version": "0.0.0",
                "schema_hash": "sha256:test",
                "getters": {},
                "setters": {},
                "calls": {
                    "call_bad_args": {
                        "args": [
                            {"name": "optional", "required": False},
                            {"name": "required", "required": True},
                        ],
                    },
                },
            })


if __name__ == "__main__":
    unittest.main()
