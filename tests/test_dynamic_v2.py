import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from remoteRF.common.grpc import grpc_pb2
from remoteRF.core.dynamic_v2_transport import DynamicV2Transport
from remoteRF.core.v2_errors import RemoteRFPolicyError, raise_for_envelope
from remoteRF.drivers import dynamic_device
from remoteRF.drivers.dynamic_v2 import (
    OverloadBinder,
    build_uhd_bindings,
    validate_schema_v2,
)
from remoteRF.drivers.support import uhd_v2


def param(name, type_name="any", *, required=True, default=None):
    value = {
        "name": name,
        "type": type_name,
        "kind": "positional_or_keyword",
        "direction": "in",
        "required": required,
    }
    if not required:
        value["default"] = default
    return value


def overload(overload_id, parameters=(), returns="None"):
    return {"id": overload_id, "parameters": list(parameters), "returns": returns}


def descriptor(owner, name, overloads, execution="server_control", mutations=()):
    return {
        "owner": owner,
        "name": name,
        "overloads": list(overloads),
        "execution": execution,
        "mutations": list(mutations),
    }


def schema():
    value = {
        "schema_version": "2.0",
        "device_type": "usrp",
        "client_class": "MultiUSRP",
        "driver_version": "test",
        "objects": [],
        "methods": [
            descriptor(
                "uhd.usrp.MultiUSRP",
                "set_rx_gain",
                [
                    overload(
                        "gain_chan",
                        [param("gain", "float"), param("chan", "int", required=False, default=0)],
                    ),
                    overload(
                        "gain_name_chan",
                        [
                            param("gain", "float"),
                            param("name", "str"),
                            param("chan", "int", required=False, default=0),
                        ],
                    ),
                ],
            ),
            descriptor(
                "uhd.usrp.MultiUSRP",
                "get_time_now",
                [overload("default", returns="uhd.types.TimeSpec")],
            ),
            descriptor(
                "uhd.usrp.MultiUSRP",
                "get_rx_stream",
                [overload("default", [param("stream_args", "uhd.usrp.StreamArgs")])],
            ),
            descriptor(
                "uhd.usrp.MultiUSRP",
                "get_tx_stream",
                [overload("default", [param("stream_args", "uhd.usrp.StreamArgs")])],
            ),
            descriptor(
                "uhd.usrp.RXStreamer",
                "get_max_num_samps",
                [overload("default", returns="int")],
                "server_handle",
            ),
            descriptor(
                "uhd.usrp.RXStreamer",
                "recv",
                [overload("default")],
                "sample_stream",
            ),
            descriptor(
                "uhd.usrp.TXStreamer",
                "send",
                [overload("default")],
                "sample_stream",
            ),
            descriptor(
                "uhd.usrp.TXStreamer",
                "recv_async_msg",
                [
                    overload(
                        "default",
                        [
                            param("async_metadata", "uhd.types.TXAsyncMetadata"),
                            param("timeout", "float", required=False, default=0.1),
                        ],
                        "bool",
                    )
                ],
                "server_handle",
                ["async_metadata"],
            ),
        ],
    }
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    value["schema_hash"] = (
        "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )
    return value


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.closed_handles = []
        self.closed_sessions = []

    def negotiate(self):
        return None

    def open_session(self, token, schema_hash):
        return {
            "session_id": "session",
            "device_handle": "device",
            "schema": schema(),
            "schema_hash": schema_hash,
            "capabilities": {"hardware_profile": "usrp2901"},
            "uhd_version": "4.10.0.0",
            "uhd_abi": "test",
            "hardware_profile": "usrp2901",
        }

    def invoke(self, session_id, handle, method, args, overload_id=""):
        self.calls.append((handle, method, args, overload_id))
        if method == "get_time_now":
            return {"__uhd_type__": "TimeSpec", "secs": 2.5}, {}
        if method == "get_rx_stream":
            return {
                "__remoterf_handle__": "rx-handle",
                "__remoterf_type__": "uhd.usrp.RXStreamer",
                "generation": 9,
            }, {}
        if method == "get_tx_stream":
            return {
                "__remoterf_handle__": "tx-handle",
                "__remoterf_type__": "uhd.usrp.TXStreamer",
                "generation": 10,
            }, {}
        if method == "get_max_num_samps":
            return 256, {}
        if method == "recv_async_msg":
            return True, {
                "async_metadata": {
                    "__uhd_type__": "TXAsyncMetadata",
                    "event_code": "burst_ack",
                    "channel": 1,
                }
            }
        return None, {}

    def recv(self, **kwargs):
        return (
            2,
            np.array([3 + 4j, 5 + 6j], dtype=kwargs["buffer"].dtype),
            {
                "__uhd_type__": "RXMetadata",
                "error_code": "none",
                "time_spec": {"__uhd_type__": "TimeSpec", "secs": 7.0},
            },
        )

    def send(self, **kwargs):
        return int(kwargs["samples"].shape[-1])

    def close_handle(self, session_id, handle):
        self.closed_handles.append(handle)
        return True

    def close_session(self, session_id):
        self.closed_sessions.append(session_id)
        return True


class CapturingSampleStub:
    def __init__(self):
        self.frames = []

    def SampleStream(self, request_iterator):
        self.frames = list(request_iterator)
        opened, request = self.frames
        yield grpc_pb2.SampleFrame(
            session_id=opened.session_id,
            handle=opened.handle,
            generation=opened.generation,
            operation_id=opened.operation_id,
            sequence=opened.sequence,
            direction=opened.direction,
            kind=grpc_pb2.SAMPLE_FRAME_RESULT,
            credits=1,
        )
        if request.direction == grpc_pb2.SAMPLE_DIRECTION_RX:
            samples = np.array([7 + 8j, 9 + 10j], dtype=np.complex64)
            yield grpc_pb2.SampleFrame(
                session_id=request.session_id,
                handle=request.handle,
                generation=request.generation,
                operation_id=request.operation_id,
                sequence=request.sequence,
                direction=request.direction,
                kind=grpc_pb2.SAMPLE_FRAME_DATA,
                dtype=samples.dtype.str,
                shape=[2],
                channels=list(request.channels),
                sample_count=2,
                payload=samples.tobytes(),
                metadata_json='{"__uhd_type__":"RXMetadata","error_code":"none"}',
            )
        else:
            yield grpc_pb2.SampleFrame(
                session_id=request.session_id,
                handle=request.handle,
                generation=request.generation,
                operation_id=request.operation_id,
                sequence=request.sequence,
                direction=request.direction,
                kind=grpc_pb2.SAMPLE_FRAME_RESULT,
                sample_count=request.sample_count,
            )


class DynamicV2Tests(unittest.TestCase):
    def test_schema_hash_and_identifiers_are_validated_before_generation(self):
        tampered = schema()
        tampered["driver_version"] = "tampered"
        with self.assertRaisesRegex(Exception, "hash validation"):
            validate_schema_v2(tampered)
        tampered = schema()
        tampered["client_class"] = "Injected;raise RuntimeError()"
        body = dict(tampered)
        body.pop("schema_hash")
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        tampered["schema_hash"] = (
            "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        )
        with self.assertRaisesRegex(Exception, "client_class"):
            validate_schema_v2(tampered)

    def test_overload_binding_and_native_like_values(self):
        transport = FakeTransport()
        uhd, MultiUSRP = build_uhd_bindings(
            schema(), transport_factory=lambda: transport
        )
        device = MultiUSRP("token")

        self.assertIsNone(device.set_rx_gain(12.5))
        self.assertEqual(transport.calls[-1][2], {"gain": 12.5, "chan": 0})
        self.assertEqual(transport.calls[-1][3], "gain_chan")
        self.assertIn("gain", device.set_rx_gain.__text_signature__)

        device.set_rx_gain(8.0, "PGA", 1)
        self.assertEqual(transport.calls[-1][3], "gain_name_chan")
        with self.assertRaisesRegex(TypeError, "incompatible arguments"):
            device.set_rx_gain(8.0, object())

        now = device.get_time_now()
        self.assertIsInstance(now, uhd.types.TimeSpec)
        self.assertEqual(now.get_real_secs(), 2.5)
        self.assertEqual((uhd.types.TimeSpec(1, 0.25) + 1).get_real_secs(), 2.25)
        self.assertEqual(uhd.types.DeviceAddr("type=b200,serial=abc")["serial"], "abc")

        self.assertEqual(device.remoterf_capabilities["hardware_profile"], "usrp2901")
        self.assertTrue(device.close())
        self.assertEqual(transport.closed_sessions, ["session"])

    def test_stream_handles_mutate_only_received_buffer_prefix_and_metadata(self):
        transport = FakeTransport()
        uhd, MultiUSRP = build_uhd_bindings(
            schema(), transport_factory=lambda: transport
        )
        device = MultiUSRP("token")
        stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
        stream_args.channels = [0]
        rx = device.get_rx_stream(stream_args)
        self.assertEqual(rx.get_max_num_samps(), 256)

        buffer = np.full(5, 99 + 0j, dtype=np.complex64)
        metadata = uhd.types.RXMetadata()
        count = rx.recv(buffer, 4, metadata, 0.25, True)
        self.assertEqual(count, 2)
        np.testing.assert_allclose(buffer[:2], np.array([3 + 4j, 5 + 6j]))
        np.testing.assert_allclose(buffer[2:], np.full(3, 99 + 0j))
        self.assertEqual(metadata.time_spec.get_real_secs(), 7.0)

        tx = device.get_tx_stream(stream_args)
        tx_metadata = uhd.types.TXMetadata()
        self.assertEqual(
            tx.send(np.zeros(0, dtype=np.complex64), 0, tx_metadata),
            0,
        )
        async_metadata = uhd.types.TXAsyncMetadata()
        self.assertTrue(tx.recv_async_msg(async_metadata, 0.2))
        self.assertEqual(async_metadata.event_code.value, "burst_ack")
        self.assertEqual(async_metadata.channel, 1)

        self.assertTrue(rx.close())
        with self.assertRaisesRegex(Exception, "closed"):
            rx.get_max_num_samps()

    def test_v2_codegen_writes_runtime_module_and_stub(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = dynamic_device._DRIVERS_DIR
            dynamic_device._DRIVERS_DIR = Path(tmp)
            try:
                package = dynamic_device._write_driver_files(schema())
            finally:
                dynamic_device._DRIVERS_DIR = old
            source = (package / "usrp_remote.py").read_text(encoding="utf-8")
            stub = (package / "usrp_remote.pyi").read_text(encoding="utf-8")
            compile(source, "<usrp_remote>", "exec")
            self.assertIn("build_uhd_bindings", source)
            self.assertIn("@overload", stub)
            self.assertIn("def set_rx_gain", stub)

    def test_transport_uses_raw_binary_sample_payloads(self):
        sample_stub = CapturingSampleStub()
        transport = DynamicV2Transport(
            control_stub=object(),
            sample_stub=sample_stub,
        )
        source = np.array([1 + 2j, 3 + 4j], dtype=np.complex64)
        sent = transport.send(
            session_id="session",
            handle="tx",
            generation=4,
            sequence=1,
            samples=source,
            channels=[0],
            timeout=0.1,
            metadata=uhd_v2.TXMetadata(),
        )
        self.assertEqual(sent, 2)
        self.assertEqual(sample_stub.frames[1].payload, source.tobytes())
        self.assertNotIn(b"base64", sample_stub.frames[1].payload)

        count, received, metadata = transport.recv(
            session_id="session",
            handle="rx",
            generation=5,
            sequence=1,
            buffer=np.empty(4, dtype=np.complex64),
            sample_count=4,
            channels=[0],
            timeout=0.1,
            one_packet=False,
            metadata=uhd_v2.RXMetadata(),
        )
        self.assertEqual(count, 2)
        np.testing.assert_array_equal(
            received,
            np.array([7 + 8j, 9 + 10j], dtype=np.complex64),
        )
        self.assertEqual(metadata["error_code"], "none")

    def test_snapshot_codecs_and_typed_error_envelopes(self):
        address = uhd_v2.decode_snapshot(
            {"__uhd_type__": "DeviceAddr", "items": {"serial": "abc"}}
        )
        self.assertEqual(address["serial"], "abc")
        args = uhd_v2.decode_snapshot(
            {
                "__uhd_type__": "StreamArgs",
                "cpu_format": "fc32",
                "otw_format": "sc16",
                "channels": [0, 1],
                "args": {
                    "__uhd_type__": "DeviceAddr",
                    "items": {"spp": "256"},
                },
            }
        )
        self.assertEqual(args.channels, [0, 1])
        self.assertEqual(args.args["spp"], "256")
        with self.assertRaises(RemoteRFPolicyError):
            raise_for_envelope(
                grpc_pb2.ErrorEnvelope(
                    code="RemoteRFPolicyError",
                    message="TX denied",
                    details_json='{"rule":"tx_enabled"}',
                )
            )


if __name__ == "__main__":
    unittest.main()
