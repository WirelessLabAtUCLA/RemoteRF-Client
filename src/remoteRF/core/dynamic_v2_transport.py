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

"""Client transport for Dynamic control v2 and binary sample streaming v1."""
from __future__ import annotations

import json
import math
import uuid

import grpc
import numpy as np

from ..common.grpc import grpc_pb2, grpc_pb2_grpc
from ..common.grpc.v2_codec import decode_value, encode_value
from .v2_errors import (
    RemoteRFError,
    RemoteRFProtocolError,
    RemoteRFTransportError,
    raise_for_envelope,
)

SCHEMA_VERSION = "2.0"
CONTROL_PROTOCOL_VERSION = "2.0"
STREAMING_PROTOCOL_VERSION = "1.0"
_SAMPLE_DTYPES = {
    "<c8": np.dtype("<c8"),
    "<c16": np.dtype("<c16"),
    "<i2": np.dtype("<i2"),
    "<f4": np.dtype("<f4"),
    "<f8": np.dtype("<f8"),
}


def _wire_dtype(dtype) -> np.dtype:
    dtype = np.dtype(dtype)
    if dtype.hasobject or dtype.fields is not None or dtype.subdtype is not None:
        raise ValueError("sample dtype must be a scalar numeric dtype")
    if dtype.itemsize > 1:
        dtype = dtype.newbyteorder("<")
    if dtype.str not in _SAMPLE_DTYPES:
        raise ValueError(f"unsupported sample dtype: {dtype}")
    return dtype


def _response_dtype(value: str) -> np.dtype:
    dtype = _SAMPLE_DTYPES.get(str(value))
    if dtype is None:
        raise RemoteRFProtocolError(
            f"server returned an unsupported or non-canonical sample dtype: "
            f"{value!r}"
        )
    return dtype


def _payload(value):
    if hasattr(value, "as_payload"):
        return _payload(value.as_payload())
    if isinstance(value, dict):
        return {str(key): _payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_payload(item) for item in value]
    return value


class DynamicV2Transport:
    def __init__(
        self,
        *,
        control_stub=None,
        sample_stub=None,
        control_timeout_sec: float = 30.0,
        stream_timeout_margin_sec: float = 5.0,
    ):
        self.control_timeout_sec = float(control_timeout_sec)
        self.stream_timeout_margin_sec = float(stream_timeout_margin_sec)
        if (
            not math.isfinite(self.control_timeout_sec)
            or self.control_timeout_sec <= 0
        ):
            raise ValueError("control_timeout_sec must be finite and positive")
        if (
            not math.isfinite(self.stream_timeout_margin_sec)
            or self.stream_timeout_margin_sec <= 0
        ):
            raise ValueError(
                "stream_timeout_margin_sec must be finite and positive"
            )
        self._stream_poisoned = False
        self._stream_failure_details = {}
        if control_stub is None or sample_stub is None:
            from .grpc_client import channel
            control_stub = control_stub or grpc_pb2_grpc.DynamicControlV2Stub(channel)
            sample_stub = sample_stub or grpc_pb2_grpc.SampleDataV1Stub(channel)
        self.control = control_stub
        self.samples = sample_stub

    def _call(self, fn, request):
        try:
            return fn(request, timeout=self.control_timeout_sec)
        except grpc.RpcError as exc:
            raise RemoteRFTransportError(
                f"Dynamic v2 RPC failed: {exc.code().name}: {exc.details()}",
                details={"grpc_code": exc.code().name},
                retryable=exc.code() in {
                    grpc.StatusCode.UNAVAILABLE,
                    grpc.StatusCode.DEADLINE_EXCEEDED,
                },
            ) from exc

    def negotiate(self):
        response = self._call(
            self.control.Negotiate,
            grpc_pb2.NegotiateRequest(
                schema_versions=[SCHEMA_VERSION],
                control_protocol_versions=[CONTROL_PROTOCOL_VERSION],
                streaming_protocol_versions=[STREAMING_PROTOCOL_VERSION],
            ),
        )
        raise_for_envelope(response.error)
        if (
            response.schema_version != SCHEMA_VERSION
            or response.control_protocol_version != CONTROL_PROTOCOL_VERSION
            or response.streaming_protocol_version != STREAMING_PROTOCOL_VERSION
        ):
            raise RemoteRFProtocolError(
                "server selected unexpected Dynamic protocol versions"
            )
        return response

    def get_schema(self, token: str) -> dict:
        self.negotiate()
        response = self._call(
            self.control.GetSchema,
            grpc_pb2.GetSchemaRequest(
                token=str(token),
                schema_versions=[SCHEMA_VERSION],
            ),
        )
        raise_for_envelope(response.error)
        try:
            schema = json.loads(response.schema_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RemoteRFProtocolError("server returned invalid schema JSON") from exc
        if (
            response.schema_version != SCHEMA_VERSION
            or schema.get("schema_version") != SCHEMA_VERSION
            or response.schema_hash != schema.get("schema_hash")
        ):
            raise RemoteRFProtocolError(
                "server schema version/hash fields are inconsistent"
            )
        return schema

    def open_session(self, token: str, schema_hash: str):
        response = self._call(
            self.control.OpenSession,
            grpc_pb2.OpenSessionRequest(
                token=str(token),
                requested_schema_hash=str(schema_hash),
                control_protocol_version=CONTROL_PROTOCOL_VERSION,
                streaming_protocol_version=STREAMING_PROTOCOL_VERSION,
            ),
        )
        raise_for_envelope(response.error)
        try:
            schema = json.loads(response.schema_json)
            capabilities = json.loads(response.capabilities_json or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise RemoteRFProtocolError(
                "server returned invalid session JSON"
            ) from exc
        if (
            response.schema_hash != str(schema_hash)
            or schema.get("schema_hash") != str(schema_hash)
            or schema.get("schema_version") != SCHEMA_VERSION
        ):
            raise RemoteRFProtocolError(
                "opened session does not match the requested schema"
            )
        return {
            "session_id": response.session_id,
            "device_handle": response.device_handle,
            "schema": schema,
            "schema_hash": response.schema_hash,
            "capabilities": capabilities,
            "uhd_version": response.uhd_version,
            "uhd_abi": response.uhd_abi,
            "hardware_profile": response.hardware_profile,
        }

    def invoke(
        self,
        session_id: str,
        handle: str,
        method: str,
        args: dict,
        *,
        overload_id: str = "",
    ):
        request = grpc_pb2.InvokeRequest(
            session_id=session_id,
            handle=handle,
            method=method,
            overload_id=overload_id,
        )
        for name, value in args.items():
            request.args.add(name=str(name), value=encode_value(_payload(value)))
        response = self._call(self.control.Invoke, request)
        raise_for_envelope(response.error)
        result = decode_value(response.result)
        mutations = {
            item.target: decode_value(item.value)
            for item in response.mutations
        }
        return result, mutations

    def close_handle(self, session_id: str, handle: str) -> bool:
        response = self._call(
            self.control.CloseHandle,
            grpc_pb2.CloseHandleRequest(session_id=session_id, handle=handle),
        )
        raise_for_envelope(response.error)
        return bool(response.closed)

    def close_session(self, session_id: str) -> bool:
        response = self._call(
            self.control.CloseSession,
            grpc_pb2.CloseSessionRequest(session_id=session_id),
        )
        raise_for_envelope(response.error)
        return bool(response.closed)

    def _sample_call(self, frames, *, operation_timeout_sec: float):
        if self._stream_poisoned:
            raise RemoteRFTransportError(
                "sample transport previously failed; open a new device "
                "session and streamer",
                details={
                    **self._stream_failure_details,
                    "requires_new_session": True,
                },
                retryable=False,
                fatal_to_session=True,
            )
        operation_timeout_sec = float(operation_timeout_sec)
        if not math.isfinite(operation_timeout_sec) or operation_timeout_sec < 0:
            raise ValueError(
                "sample operation timeout must be finite and non-negative"
            )
        rpc_timeout = (
            operation_timeout_sec + self.stream_timeout_margin_sec
        )
        try:
            responses = list(
                self.samples.SampleStream(
                    iter(frames),
                    timeout=rpc_timeout,
                )
            )
        except grpc.RpcError as exc:
            self._stream_poisoned = True
            self._stream_failure_details = {
                "grpc_code": exc.code().name,
                "requires_new_session": True,
            }
            raise RemoteRFTransportError(
                f"sample stream failed: {exc.code().name}: {exc.details()}",
                details=dict(self._stream_failure_details),
                retryable=exc.code() in {
                    grpc.StatusCode.UNAVAILABLE,
                    grpc.StatusCode.DEADLINE_EXCEEDED,
                },
                fatal_to_session=True,
            ) from exc
        for response in responses:
            try:
                raise_for_envelope(response.error)
            except RemoteRFError as exc:
                if exc.fatal_to_session:
                    self._stream_poisoned = True
                    self._stream_failure_details = {
                        **exc.details,
                        "requires_new_session": True,
                    }
                raise
        return responses

    @staticmethod
    def _validate_sample_responses(responses, common, request_sequence):
        if not responses:
            raise RemoteRFProtocolError("sample stream returned no frames")
        for response in responses:
            if (
                response.session_id != common["session_id"]
                or response.handle != common["handle"]
                or response.generation != common["generation"]
                or response.operation_id != common["operation_id"]
                or response.direction != common["direction"]
            ):
                raise RemoteRFProtocolError(
                    "sample response identity does not match the request"
                )
            if response.sequence not in {
                max(0, request_sequence - 1),
                request_sequence,
            }:
                raise RemoteRFProtocolError(
                    "sample response sequence does not match the request"
                )

    def recv(
        self,
        *,
        session_id: str,
        handle: str,
        generation: int,
        sequence: int,
        buffer: np.ndarray,
        sample_count: int,
        channels,
        timeout: float,
        one_packet: bool,
        metadata,
    ):
        operation_id = uuid.uuid4().hex
        shape = list(buffer.shape)
        shape[-1] = int(sample_count)
        wire_dtype = _wire_dtype(buffer.dtype)
        common = dict(
            session_id=session_id,
            handle=handle,
            generation=int(generation),
            operation_id=operation_id,
            direction=grpc_pb2.SAMPLE_DIRECTION_RX,
        )
        responses = self._sample_call(
            [
                grpc_pb2.SampleFrame(
                    **common,
                    sequence=max(0, sequence - 1),
                    kind=grpc_pb2.SAMPLE_FRAME_OPEN,
                    credits=1,
                ),
                grpc_pb2.SampleFrame(
                    **common,
                    sequence=sequence,
                    kind=grpc_pb2.SAMPLE_FRAME_REQUEST,
                    dtype=wire_dtype.str,
                    shape=shape,
                    channels=list(channels),
                    sample_count=int(sample_count),
                    timeout_sec=float(timeout),
                    one_packet=bool(one_packet),
                    metadata_json=json.dumps(_payload(metadata), separators=(",", ":")),
                ),
            ],
            operation_timeout_sec=timeout,
        )
        self._validate_sample_responses(responses, common, sequence)
        data_frames = [
            item for item in responses
            if item.kind == grpc_pb2.SAMPLE_FRAME_DATA
            and item.sequence == sequence
        ]
        if len(data_frames) != 1:
            raise RemoteRFProtocolError(
                "RX operation must return exactly one DATA frame"
            )
        data = data_frames[0]
        dtype = _response_dtype(data.dtype)
        if dtype != wire_dtype:
            raise RemoteRFProtocolError(
                "RX response dtype does not match the requested buffer"
            )
        received_shape = tuple(int(item) for item in data.shape)
        if (
            len(received_shape) not in {1, 2}
            or any(item < 0 for item in received_shape)
            or not received_shape
            or int(data.sample_count) != received_shape[-1]
            or int(data.sample_count) > int(sample_count)
        ):
            raise RemoteRFProtocolError("RX response has invalid sample shape/count")
        if len(received_shape) != buffer.ndim:
            raise RemoteRFProtocolError(
                "RX response dimensionality does not match the requested buffer"
            )
        if (
            len(received_shape) == 2
            and received_shape[0] != len(channels)
        ) or (
            len(received_shape) == 1
            and len(channels) != 1
        ):
            raise RemoteRFProtocolError(
                "RX response channel dimension does not match channel metadata"
            )
        expected_bytes = math.prod(received_shape) * dtype.itemsize
        if expected_bytes != len(data.payload):
            raise RemoteRFProtocolError(
                "RX response byte length does not match dtype/shape"
            )
        if list(data.channels) != list(channels):
            raise RemoteRFProtocolError("RX response channels do not match")
        try:
            samples = np.frombuffer(
                data.payload,
                dtype=dtype,
            ).reshape(received_shape)
            metadata = json.loads(data.metadata_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RemoteRFProtocolError(
                "RX response contains malformed binary data or metadata"
            ) from exc
        if not isinstance(metadata, dict):
            raise RemoteRFProtocolError(
                "RX response metadata must be a JSON object"
            )
        return int(data.sample_count), samples.copy(), metadata

    def send(
        self,
        *,
        session_id: str,
        handle: str,
        generation: int,
        sequence: int,
        samples: np.ndarray,
        channels,
        timeout: float,
        metadata,
    ) -> int:
        wire_dtype = _wire_dtype(samples.dtype)
        array = np.ascontiguousarray(samples, dtype=wire_dtype)
        operation_id = uuid.uuid4().hex
        common = dict(
            session_id=session_id,
            handle=handle,
            generation=int(generation),
            operation_id=operation_id,
            direction=grpc_pb2.SAMPLE_DIRECTION_TX,
        )
        responses = self._sample_call(
            [
                grpc_pb2.SampleFrame(
                    **common,
                    sequence=max(0, sequence - 1),
                    kind=grpc_pb2.SAMPLE_FRAME_OPEN,
                    credits=1,
                ),
                grpc_pb2.SampleFrame(
                    **common,
                    sequence=sequence,
                    kind=grpc_pb2.SAMPLE_FRAME_DATA,
                    dtype=array.dtype.str,
                    shape=list(array.shape),
                    channels=list(channels),
                    sample_count=int(array.shape[-1] if array.ndim else 0),
                    payload=array.tobytes(order="C"),
                    timeout_sec=float(timeout),
                    metadata_json=json.dumps(_payload(metadata), separators=(",", ":")),
                ),
            ],
            operation_timeout_sec=timeout,
        )
        self._validate_sample_responses(responses, common, sequence)
        result_frames = [
            item for item in responses
            if item.kind == grpc_pb2.SAMPLE_FRAME_RESULT
            and item.sequence == sequence
        ]
        if len(result_frames) != 1:
            raise RemoteRFProtocolError(
                "TX operation must return exactly one RESULT frame"
            )
        result = result_frames[0]
        if int(result.sample_count) > int(array.shape[-1]):
            raise RemoteRFProtocolError("TX response sample count exceeds request")
        return int(result.sample_count)
