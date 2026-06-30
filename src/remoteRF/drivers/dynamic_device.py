"""
IDL driver code-generator.

When a device is reserved, call install_driver(device_id) to:
  1. Fetch the IDL schema from the server via IDL:get_drivers.
  2. Generate a <device_type>_remote.py file that mirrors the style of the
     hand-written adalm_pluto/pluto_remote.py.
  3. Write an __init__.py so the package is importable.

After install_driver() the user can do:

    from remoteRF.drivers.pluto import *
    sdr = adi.Pluto(token)

On first device init, pass device_id to also check for schema updates:

    sdr = adi.Pluto(token, device_id=1)
"""

from __future__ import annotations

import json
import keyword
from pathlib import Path

from ..core.grpc_client import rpc_client
from ..common.utils import map_arg, unmap_arg


# ─────────────────────────────────────────────────────────────────────────────
# IDL fetch
# ─────────────────────────────────────────────────────────────────────────────

def fetch_idl(*, token: str = None, device_id: int = None, device_name: str = None) -> dict:
    """
    Fetch the IDL schema from the server.

    Pass one of: token (preferred — uses the reservation token to resolve
    the device), device_id, or device_name.

    Returns the parsed schema dict:
        {
            "schema_version": "1.0",
            "device_type":    "pluto",
            "client_class":   "Pluto",
            "driver_version": "0.0.1",
            "getters":  {"get_tx_lo": {"doc": "..."}, ...},
            "setters":  {"set_tx_lo": {"doc": "..."}, ...},
            "calls":    {"call_rx":   {"doc": "..."}, ...},
            "schema_hash": "sha256:...",
        }
    """
    args: dict = {}
    if token is not None:
        args['token'] = map_arg(str(token))
    elif device_id is not None:
        args['device_id'] = map_arg(int(device_id))
    elif device_name is not None:
        args['device_name'] = map_arg(str(device_name))
    else:
        raise ValueError("fetch_idl requires token, device_id, or device_name")

    resp = rpc_client(function_name="IDL:get_drivers", args=args)

    if 'error' in resp.results:
        raise RuntimeError(f"IDL fetch failed: {unmap_arg(resp.results['error'])}")

    return json.loads(unmap_arg(resp.results['schema']))


# ─────────────────────────────────────────────────────────────────────────────
# Code generator
# ─────────────────────────────────────────────────────────────────────────────

def _default_class_name(device_type: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in str(device_type or "unknown").split("_") if part)


def _client_class_name(schema: dict) -> str:
    return str(schema.get("client_class") or _default_class_name(schema.get("device_type", "unknown")))


_CLIENT_MODULE_PREFIX = "remoteRF.drivers.support."
_CLIENT_CTOR_SENTINELS = {"$self", "$result"}
_CLIENT_OBJECT_KINDS = {
    "module",
    "namespace",
    "payload_class",
    "strict_class",
    "proxy_class",
    "enum",
    "function",
    "method",
}
_CLIENT_FUNCTION_TEMPLATES = {
    "uhd_payload",
    "uhd_get_rx_stream",
    "uhd_get_tx_stream",
    "uhd_close_all_streams",
    "uhd_streamer",
    "uhd_tune_request",
    "uhd_subdev_spec",
}
_CLIENT_METHOD_TEMPLATES = {
    "payload_as_payload",
    "timespec_get_real_secs",
    "timespec_get_full_secs",
    "timespec_get_frac_secs",
    "timespec_to_ticks",
    "timespec_get_tick_count",
    "timespec_float",
    "metadata_update",
    "metadata_strerror",
    "streamer_get_max_num_samps",
    "streamer_issue_stream_cmd",
    "streamer_close",
    "rx_streamer_recv",
    "tx_streamer_send",
}
_CLIENT_INIT_TEMPLATES = {
    "timespec",
    "stream_args",
    "stream_cmd",
    "rx_metadata",
    "tx_metadata",
    "streamer_handle",
}
_CLIENT_OPERATOR_TEMPLATES = {"timespec_numeric"}


def _valid_identifier(value: str) -> bool:
    return value.isidentifier() and not keyword.iskeyword(value)


def _require_identifier(value, *, field: str) -> str:
    value = str(value or "").strip()
    if not _valid_identifier(value):
        raise ValueError(f"{field} must be a valid Python identifier: {value!r}")
    return value


def _require_package_name(value, *, field: str) -> str:
    value = str(value or "").strip()
    if not _valid_identifier(value):
        raise ValueError(f"{field} must be a valid Python package identifier: {value!r}")
    return value


def _require_dotted_identifier(value, *, field: str, required_prefix: str | None = None) -> str:
    value = str(value or "").strip()
    if required_prefix and not value.startswith(required_prefix):
        raise ValueError(f"{field} must be under {required_prefix!r}: {value!r}")
    if not value or any(not _valid_identifier(part) for part in value.split(".")):
        raise ValueError(f"{field} must be a dotted Python identifier: {value!r}")
    return value


def _json_checked(value, *, field: str):
    try:
        json.dumps(value)
    except TypeError as exc:
        raise ValueError(f"{field} must be JSON serializable") from exc
    return value


def _validate_client_modules(value) -> dict:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("client_modules must be a dict")

    out = {}
    for alias, module_path in value.items():
        alias = _require_identifier(alias, field="client_modules alias")
        module_path = _require_dotted_identifier(
            module_path,
            field=f"client_modules[{alias!r}]",
            required_prefix=_CLIENT_MODULE_PREFIX,
        )
        out[alias] = module_path
    return out


def _validate_client_template(spec, *, context: str, allowed: set[str]) -> dict:
    if not isinstance(spec, dict):
        raise ValueError(f"{context} must be a template metadata dict")
    template = str(spec.get("template", "")).strip()
    if template not in allowed:
        raise ValueError(f"{context} uses unknown template {template!r}")
    out = {"template": template}
    options = spec.get("options", {})
    if options:
        if not isinstance(options, dict):
            raise ValueError(f"{context}.options must be a dict")
        out["options"] = _json_checked(options, field=f"{context}.options")
    return out


def _validate_client_object(spec, *, context: str, root: bool = False) -> dict:
    if not isinstance(spec, dict):
        raise ValueError(f"{context} must be a client object metadata dict")
    kind = str(spec.get("kind", "")).strip()
    if kind not in _CLIENT_OBJECT_KINDS:
        raise ValueError(f"{context} has unknown client object kind {kind!r}")
    if root and kind != "module":
        raise ValueError(f"{context} must be a client module")
    if not root and kind == "module":
        raise ValueError(f"{context} cannot be a nested client module")

    if kind in {"module", "namespace"}:
        members = spec.get("members", {})
        if not isinstance(members, dict):
            raise ValueError(f"{context}.members must be a dict")
        checked_members = {}
        for name, member in members.items():
            name = _require_identifier(name, field=f"{context}.members key")
            checked_members[name] = _validate_client_object(
                member,
                context=f"{context}.{name}",
            )
        out = {"kind": kind, "members": checked_members}
        if kind == "module":
            exports = spec.get("exports", [])
            if exports:
                if not isinstance(exports, list):
                    raise ValueError(f"{context}.exports must be a list")
                checked_exports = []
                for export in exports:
                    export = _require_identifier(export, field=f"{context}.exports entry")
                    if export not in checked_members:
                        raise ValueError(f"{context}.exports references unknown member {export!r}")
                    checked_exports.append(export)
                out["exports"] = checked_exports
            bind_path = spec.get("bind_client_class_to")
            if bind_path:
                bind_path = _require_dotted_identifier(
                    bind_path,
                    field=f"{context}.bind_client_class_to",
                )
                _validate_client_bind_path(
                    bind_path,
                    checked_members,
                    context=f"{context}.bind_client_class_to",
                )
                out["bind_client_class_to"] = bind_path
        return out

    if kind in {"payload_class", "strict_class", "proxy_class"}:
        fields = spec.get("fields", [])
        if not isinstance(fields, list):
            raise ValueError(f"{context}.fields must be a list")
        checked_fields = []
        for field in fields:
            if not isinstance(field, dict):
                raise ValueError(f"{context}.fields entries must be dicts")
            name = _require_identifier(field.get("name"), field=f"{context}.field")
            checked_fields.append(dict(field, name=name))
        out = {"kind": kind, "fields": checked_fields}
        if kind == "payload_class":
            payload_type = str(spec.get("payload_type", "")).strip()
            if not payload_type:
                raise ValueError(f"{context}.payload_type is required")
            out["payload_type"] = payload_type
        if spec.get("init") is not None:
            out["init"] = _validate_client_template(
                spec["init"],
                context=f"{context}.init",
                allowed=_CLIENT_INIT_TEMPLATES,
            )
        methods = spec.get("methods", [])
        if methods:
            if not isinstance(methods, list):
                raise ValueError(f"{context}.methods must be a list")
            out["methods"] = [
                _validate_client_object(method, context=f"{context}.methods[{index}]")
                for index, method in enumerate(methods)
            ]
        operators = spec.get("operators", [])
        if operators:
            if not isinstance(operators, list):
                raise ValueError(f"{context}.operators must be a list")
            checked_operators = []
            for operator in operators:
                operator = str(operator).strip()
                if operator not in _CLIENT_OPERATOR_TEMPLATES:
                    raise ValueError(f"{context}.operators contains unknown template {operator!r}")
                checked_operators.append(operator)
            out["operators"] = checked_operators
        if "defaults" in spec:
            if not isinstance(spec["defaults"], dict):
                raise ValueError(f"{context}.defaults must be a dict")
            out["defaults"] = _json_checked(spec["defaults"], field=f"{context}.defaults")
        return _json_checked(out, field=context)

    if kind == "enum":
        values = spec.get("values", [])
        if not isinstance(values, list):
            raise ValueError(f"{context}.values must be a list")
        return {
            "kind": "enum",
            "values": [
                _require_identifier(value, field=f"{context}.values entry")
                for value in values
            ],
        }

    if kind == "function":
        return {
            "kind": "function",
            **_validate_client_template(
                spec,
                context=context,
                allowed=_CLIENT_FUNCTION_TEMPLATES,
            ),
        }

    if kind == "method":
        return {
            "kind": "method",
            **_validate_client_template(
                spec,
                context=context,
                allowed=_CLIENT_METHOD_TEMPLATES,
            ),
        }

    raise ValueError(f"{context} has unsupported client object kind {kind!r}")


def _validate_client_objects(value) -> dict:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("client_objects must be a dict")
    out = {}
    for alias, spec in value.items():
        alias = _require_identifier(alias, field="client_objects alias")
        out[alias] = _validate_client_object(
            spec,
            context=f"client_objects[{alias!r}]",
            root=True,
        )
    _validate_unique_client_object_symbols(out)
    return out


def _validate_client_bind_path(bind_path: str, members: dict, *, context: str) -> None:
    current_members = members
    for part in bind_path.split("."):
        target = current_members.get(part)
        if target is None:
            raise ValueError(f"{context} references unknown member {part!r}")
        if target.get("kind") != "namespace":
            raise ValueError(f"{context} must reference a client namespace")
        current_members = target.get("members", {})


def _validate_unique_client_object_symbols(client_objects: dict) -> None:
    seen: dict[str, str] = {}

    def walk(spec: dict, context: str) -> None:
        for name, member in spec.get("members", {}).items():
            kind = member.get("kind")
            child_context = f"{context}.{name}"
            if kind == "namespace":
                walk(member, child_context)
                continue
            if kind == "module":
                raise ValueError(f"{child_context} cannot be a nested client module")
            previous = seen.get(name)
            if previous is not None:
                raise ValueError(
                    f"{child_context} duplicates generated client object name "
                    f"{name!r}; already declared at {previous}"
                )
            seen[name] = child_context

    for alias, spec in client_objects.items():
        walk(spec, f"client_objects[{alias!r}]")


def _validate_client_return(
    spec,
    *,
    client_modules: dict,
    client_objects: dict,
    context: str,
):
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise ValueError(f"{context}.client_return must be a dict")
    if spec.get("kind") != "constructor":
        raise ValueError(f"{context}.client_return kind must be 'constructor'")

    target = _require_dotted_identifier(
        spec.get("target"),
        field=f"{context}.client_return target",
    )
    root = target.split(".", 1)[0]
    if root not in client_modules and root not in client_objects:
        raise ValueError(
            f"{context}.client_return target root {root!r} is not declared in client_modules or client_objects"
        )

    args = spec.get("args", [])
    if not isinstance(args, list):
        raise ValueError(f"{context}.client_return args must be a list")

    checked_args = []
    for index, arg in enumerate(args):
        field = f"{context}.client_return args[{index}]"
        if isinstance(arg, str) and arg.startswith("$"):
            if arg not in _CLIENT_CTOR_SENTINELS:
                raise ValueError(
                    f"{field} uses unsupported sentinel {arg!r}; "
                    f"supported: {sorted(_CLIENT_CTOR_SENTINELS)}"
                )
            checked_args.append(arg)
            continue

        if isinstance(arg, dict):
            if set(arg.keys()) != {"const"}:
                raise ValueError(f"{field} dict must be exactly {{'const': value}}")
            checked_args.append({"const": _json_checked(arg["const"], field=field)})
            continue

        raise ValueError(
            f"{field} must be '$self', '$result', or {{'const': json_safe_value}}"
        )

    return {"kind": "constructor", "target": target, "args": checked_args}


def _schema_maps(schema: dict) -> tuple[str, str, str, str, str, dict, dict, dict, dict, dict]:
    device_type = _require_package_name(
        schema.get("device_type", "unknown"),
        field="device_type",
    )
    driver_version = str(schema.get("driver_version", "?"))
    schema_hash = str(schema.get("schema_hash", "?"))
    rpc_prefix = device_type[0].upper() + device_type[1:]
    class_name = _require_identifier(_client_class_name(schema), field="client_class")
    client_modules = _validate_client_modules(schema.get("client_modules", {}) or {})
    client_objects = _validate_client_objects(schema.get("client_objects", {}) or {})
    duplicate_roots = set(client_modules) & set(client_objects)
    if duplicate_roots:
        joined = ", ".join(sorted(duplicate_roots))
        raise ValueError(f"client_objects duplicates client_modules roots: {joined}")

    raw_getters = schema.get("getters", {})
    raw_setters = schema.get("setters", {})
    raw_calls = schema.get("calls", {})
    if not isinstance(raw_getters, dict):
        raise ValueError("getters must be a dict")
    if not isinstance(raw_setters, dict):
        raise ValueError("setters must be a dict")
    if not isinstance(raw_calls, dict):
        raise ValueError("calls must be a dict")

    getter_map = {}
    for key, info in raw_getters.items():
        if not str(key).startswith("get_"):
            continue
        prop = _require_identifier(str(key)[4:], field=f"getter {key!r} property")
        info = dict(info or {})
        info["client_return"] = _validate_client_return(
            info.get("client_return"),
            client_modules=client_modules,
            client_objects=client_objects,
            context=f"getter {key!r}",
        )
        getter_map[prop] = info

    setter_map = {}
    for key, info in raw_setters.items():
        if not str(key).startswith("set_"):
            continue
        prop = _require_identifier(str(key)[4:], field=f"setter {key!r} property")
        info = dict(info or {})
        if info.get("client_return") is not None:
            raise ValueError(f"setter {key!r} cannot declare client_return")
        setter_map[prop] = info

    call_map = {}
    for key, info in raw_calls.items():
        method = str(key)[5:] if str(key).startswith("call_") else str(key)
        method = _require_identifier(method, field=f"call {key!r} method")
        info = dict(info or {})
        info["client_return"] = _validate_client_return(
            info.get("client_return"),
            client_modules=client_modules,
            client_objects=client_objects,
            context=f"call {key!r}",
        )
        call_map[method] = info

    return (
        device_type,
        driver_version,
        schema_hash,
        rpc_prefix,
        class_name,
        client_modules,
        client_objects,
        getter_map,
        setter_map,
        call_map,
    )


def _append_doc(lines: list[str], indent: str, doc) -> None:
    if doc:
        lines.append(f"{indent}{str(doc)!r}")


_CLIENT_OBJECT_HELPERS = '''\
import math as _math


class _ClientNamespace:
    pass


def _slots_for(obj):
    slots = []
    for cls in type(obj).__mro__:
        value = getattr(cls, "__slots__", ())
        if isinstance(value, str):
            slots.append(value)
        else:
            slots.extend(value)
    return tuple(name for name in slots if name != "__dict__")


class _StrictObject:
    __slots__ = ()

    def __setattr__(self, name, value):
        allowed = _slots_for(self)
        if name not in allowed:
            joined = ", ".join(allowed) or "none"
            raise AttributeError(
                f"{type(self).__name__} does not support attribute {name!r}. "
                f"Supported attributes: {joined}"
            )
        object.__setattr__(self, name, value)


class _PayloadDict(dict):
    __slots__ = ()

    def as_payload(self):
        return dict(self)


def payload(value):
    if hasattr(value, "as_payload"):
        return payload(value.as_payload())
    if isinstance(value, dict):
        return {key: payload(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [payload(item) for item in value]
    if isinstance(value, list):
        return [payload(item) for item in value]
    return value


def _real_secs(value):
    if hasattr(value, "get_real_secs"):
        return value.get_real_secs()
    if isinstance(value, dict):
        value = value.get("secs", value.get("time_spec", 0.0))
        if isinstance(value, dict):
            return _real_secs(value)
        return float(value)
    return float(value)


class _EnumValue(_StrictObject):
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return self.value

    def __repr__(self):
        return self.value

    def __eq__(self, other):
        return self.value == (other.value if hasattr(other, "value") else other)


class _Streamer(_StrictObject):
    __slots__ = ("usrp", "handle")

    def __init__(self, usrp_obj, handle):
        self.usrp = usrp_obj
        self.handle = handle.get("handle") if isinstance(handle, dict) else handle

    def get_max_num_samps(self):
        return self.usrp.stream_get_max_num_samps(self.handle)

    def issue_stream_cmd(self, stream_cmd):
        return self.usrp.stream_issue_stream_cmd({
            "handle": self.handle,
            "stream_cmd": payload(stream_cmd),
        })

    def close(self):
        return self.usrp.stream_close(self.handle)
'''


def _class_init_template(spec: dict) -> str:
    return ((spec.get("init") or {}).get("template") or "").strip()


def _method_templates(spec: dict) -> set[str]:
    return {
        str(method.get("template", "")).strip()
        for method in spec.get("methods", [])
        if isinstance(method, dict)
    }


def _emit_enum_class(name: str, spec: dict) -> list[str]:
    lines = [f"class {name}:"]
    for value in spec.get("values", []):
        lines.append(f'    {value} = _EnumValue("{value}")')
    if len(lines) == 1:
        lines.append("    pass")
    lines.append("")
    return lines


def _emit_timespec_class(name: str) -> list[str]:
    return [
        f"class {name}(_PayloadDict):",
        '    __slots__ = ("secs",)',
        "",
        "    def __init__(self, secs=0.0):",
        "        secs = _real_secs(secs) if isinstance(secs, dict) else float(secs)",
        f'        dict.__init__(self, __uhd_type__="{name}", secs=secs)',
        "        self.secs = secs",
        "",
        "    def __setattr__(self, name, value):",
        '        if name != "secs":',
        "            raise AttributeError(",
        "                f\"TimeSpec does not support attribute {name!r}. Supported attributes: secs\"",
        "            )",
        "        object.__setattr__(self, name, float(value))",
        '        self["secs"] = float(value)',
        "",
        "    @classmethod",
        "    def from_ticks(cls, ticks, rate):",
        "        return cls(float(ticks) / float(rate))",
        "",
        "    def get_real_secs(self):",
        "        return self.secs",
        "",
        "    def get_full_secs(self):",
        "        return _math.floor(self.secs)",
        "",
        "    def get_frac_secs(self):",
        "        return self.secs - self.get_full_secs()",
        "",
        "    def to_ticks(self, rate):",
        "        return int(round(self.secs * float(rate)))",
        "",
        "    def get_tick_count(self, rate):",
        "        return self.to_ticks(rate)",
        "",
        "    def __float__(self):",
        "        return self.secs",
        "",
        "    def __add__(self, other):",
        f"        return {name}(self.secs + _real_secs(other))",
        "",
        "    def __radd__(self, other):",
        f"        return {name}(_real_secs(other) + self.secs)",
        "",
        "    def __sub__(self, other):",
        f"        return {name}(self.secs - _real_secs(other))",
        "",
        "    def __rsub__(self, other):",
        f"        return {name}(_real_secs(other) - self.secs)",
        "",
        "    def as_payload(self):",
        '        self["secs"] = self.secs',
        "        return dict(self)",
        "",
    ]


def _emit_stream_args_class(name: str) -> list[str]:
    return [
        f"class {name}(_PayloadDict):",
        '    __slots__ = ("cpu_format", "otw_format", "args", "channels")',
        "",
        "    def __init__(self, cpu_format, otw_format):",
        "        dict.__init__(",
        "            self,",
        f'            __uhd_type__="{name}",',
        "            cpu_format=str(cpu_format),",
        "            otw_format=str(otw_format),",
        "            args={},",
        "            channels=[],",
        "        )",
        "        self.cpu_format = str(cpu_format)",
        "        self.otw_format = str(otw_format)",
        "        self.args = {}",
        "        self.channels = []",
        "",
        "    def __setattr__(self, name, value):",
        "        if name not in self.__slots__:",
        '            joined = ", ".join(self.__slots__)',
        "            raise AttributeError(",
        "                f\"StreamArgs does not support attribute {name!r}. Supported attributes: {joined}\"",
        "            )",
        "        object.__setattr__(self, name, value)",
        '        if name == "channels":',
        "            self[name] = list(value)",
        '        elif name == "args":',
        "            self[name] = payload(value)",
        "        else:",
        "            self[name] = str(value)",
        "",
        "    def as_payload(self):",
        '        self["cpu_format"] = self.cpu_format',
        '        self["otw_format"] = self.otw_format',
        '        self["args"] = payload(self.args)',
        '        self["channels"] = list(self.channels)',
        "        return dict(self)",
        "",
    ]


def _emit_stream_cmd_class(name: str) -> list[str]:
    return [
        f"class {name}(_PayloadDict):",
        '    __slots__ = ("mode", "stream_now", "time_spec", "num_samps")',
        "",
        "    def __init__(self, mode):",
        "        dict.__init__(",
        "            self,",
        f'            __uhd_type__="{name}",',
        "            mode=str(mode),",
        "            stream_now=True,",
        "            time_spec=None,",
        "            num_samps=None,",
        "        )",
        "        self.mode = mode",
        "        self.stream_now = True",
        "        self.time_spec = None",
        "        self.num_samps = None",
        "",
        "    def __setattr__(self, name, value):",
        "        if name not in self.__slots__:",
        '            joined = ", ".join(self.__slots__)',
        "            raise AttributeError(",
        "                f\"StreamCMD does not support attribute {name!r}. Supported attributes: {joined}\"",
        "            )",
        "        object.__setattr__(self, name, value)",
        '        if name == "mode":',
        '            self[name] = value.value if hasattr(value, "value") else str(value)',
        '        elif name == "time_spec":',
        "            self[name] = payload(value) if value is not None else None",
        "        else:",
        "            self[name] = value",
        "",
        "    def as_payload(self):",
        '        mode = self.mode.value if hasattr(self.mode, "value") else str(self.mode)',
        '        self["mode"] = mode',
        '        self["stream_now"] = bool(self.stream_now)',
        '        self["time_spec"] = payload(self.time_spec) if self.time_spec is not None else None',
        '        self["num_samps"] = self.num_samps',
        "        return dict(self)",
        "",
    ]


def _emit_rx_metadata_class(name: str) -> list[str]:
    return [
        f"class {name}(_StrictObject):",
        "    __slots__ = (",
        '        "error_code",',
        '        "error_code_repr",',
        '        "time_spec",',
        '        "out_of_sequence",',
        '        "fragment_offset",',
        '        "more_fragments",',
        "    )",
        "",
        "    def __init__(self):",
        "        self.error_code = RXMetadataErrorCode.none",
        '        self.error_code_repr = "none"',
        "        self.time_spec = TimeSpec(0.0)",
        "        self.out_of_sequence = None",
        "        self.fragment_offset = None",
        "        self.more_fragments = None",
        "",
        "    def update(self, value):",
        "        value = value or {}",
        '        error_code = value.get("error_code", "none")',
        "        self.error_code_repr = value.get(\"error_code_repr\", error_code)",
        "        self.error_code = _EnumValue(error_code)",
        '        if value.get("time_spec") is not None:',
        '            self.time_spec = TimeSpec(_real_secs(value["time_spec"]))',
        '        self.out_of_sequence = value.get("out_of_sequence")',
        '        self.fragment_offset = value.get("fragment_offset")',
        '        self.more_fragments = value.get("more_fragments")',
        "",
        "    def strerror(self):",
        "        return self.error_code_repr",
        "",
    ]


def _emit_tx_metadata_class(name: str) -> list[str]:
    return [
        f"class {name}(_StrictObject):",
        '    __slots__ = ("has_time_spec", "time_spec", "end_of_burst")',
        "",
        "    def __init__(self):",
        "        self.has_time_spec = False",
        "        self.time_spec = None",
        "        self.end_of_burst = False",
        "",
        "    def as_payload(self):",
        "        return {",
        f'            "__uhd_type__": "{name}",',
        '            "has_time_spec": bool(self.has_time_spec),',
        '            "time_spec": payload(self.time_spec) if self.time_spec is not None else None,',
        '            "end_of_burst": bool(self.end_of_burst),',
        "        }",
        "",
    ]


def _emit_rx_streamer_class(name: str) -> list[str]:
    return [
        f"class {name}(_Streamer):",
        "    __slots__ = ()",
        "",
        "    def recv(self, recv_buffer, metadata, timeout=0.1, one_packet=False):",
        "        count, buffer, metadata_value = self.usrp.stream_recv({",
        '            "handle": self.handle,',
        '            "recv_buffer": recv_buffer,',
        '            "timeout": float(timeout),',
        '            "one_packet": bool(one_packet),',
        "        })",
        "        recv_buffer[...] = buffer",
        "        metadata.update(metadata_value)",
        "        return count",
        "",
    ]


def _emit_tx_streamer_class(name: str) -> list[str]:
    return [
        f"class {name}(_Streamer):",
        "    __slots__ = ()",
        "",
        "    def send(self, samples, metadata, timeout=0.1):",
        "        return self.usrp.stream_send({",
        '            "handle": self.handle,',
        '            "samples": samples,',
        '            "metadata": payload(metadata),',
        '            "timeout": float(timeout),',
        "        })",
        "",
    ]


def _emit_function(name: str, template: str) -> list[str]:
    if template == "uhd_payload":
        return []
    if template == "uhd_get_rx_stream":
        return [
            f"def {name}(usrp_obj, stream_args):",
            "    return streamer(usrp_obj, usrp_obj.get_rx_stream(payload(stream_args)))",
            "",
        ]
    if template == "uhd_get_tx_stream":
        return [
            f"def {name}(usrp_obj, stream_args):",
            "    return streamer(usrp_obj, usrp_obj.get_tx_stream(payload(stream_args)))",
            "",
        ]
    if template == "uhd_close_all_streams":
        return [
            f"def {name}(usrp_obj):",
            "    return usrp_obj.close_all_streams()",
            "",
        ]
    if template == "uhd_streamer":
        return [
            f"def {name}(usrp_obj, value):",
            "    if isinstance(value, _Streamer):",
            "        return value",
            "    if not isinstance(value, dict):",
            "        raise TypeError(f\"Expected streamer payload from server, got {type(value).__name__}\")",
            '    kind = value.get("__uhd_type__")',
            '    if kind == "RXStreamer":',
            "        return RXStreamer(usrp_obj, value)",
            '    if kind == "TXStreamer":',
            "        return TXStreamer(usrp_obj, value)",
            "    raise ValueError(f\"Unknown streamer payload type: {kind!r}\")",
            "",
        ]
    if template == "uhd_tune_request":
        return [
            f"def {name}(*args, **kwargs):",
            "    return {",
            '        "__uhd_type__": "TuneRequest",',
            '        "args": payload(args),',
            '        "kwargs": payload(kwargs),',
            "    }",
            "",
        ]
    if template == "uhd_subdev_spec":
        return [
            f"def {name}(spec):",
            '    return {"__uhd_type__": "SubdevSpec", "spec": str(spec)}',
            "",
        ]
    raise ValueError(f"Unsupported client function template {template!r}")


def _emit_client_member_definitions(spec: dict, lines: list[str], emitted: set[str]) -> None:
    for name, member in spec.get("members", {}).items():
        kind = member.get("kind")
        if kind in {"module", "namespace"}:
            _emit_client_member_definitions(member, lines, emitted)
            continue

        if name in emitted:
            continue

        if kind == "enum":
            lines.extend(_emit_enum_class(name, member))
            emitted.add(name)
        elif kind == "function":
            lines.extend(_emit_function(name, member.get("template")))
            emitted.add(name)
        elif kind in {"payload_class", "strict_class", "proxy_class"}:
            init_template = _class_init_template(member)
            methods = _method_templates(member)
            if init_template == "timespec":
                lines.extend(_emit_timespec_class(name))
            elif init_template == "stream_args":
                lines.extend(_emit_stream_args_class(name))
            elif init_template == "stream_cmd":
                lines.extend(_emit_stream_cmd_class(name))
            elif init_template == "rx_metadata":
                lines.extend(_emit_rx_metadata_class(name))
            elif init_template == "tx_metadata":
                lines.extend(_emit_tx_metadata_class(name))
            elif "rx_streamer_recv" in methods:
                lines.extend(_emit_rx_streamer_class(name))
            elif "tx_streamer_send" in methods:
                lines.extend(_emit_tx_streamer_class(name))
            else:
                raise ValueError(f"Unsupported client class template for {name!r}")
            emitted.add(name)


def _emit_client_assignments(spec: dict, target: str, lines: list[str]) -> None:
    for name, member in spec.get("members", {}).items():
        kind = member.get("kind")
        if kind == "namespace":
            lines.append(f"{target}.{name} = _ClientNamespace()")
            _emit_client_assignments(member, f"{target}.{name}", lines)
        elif kind in {"payload_class", "strict_class", "proxy_class", "enum", "function"}:
            lines.append(f"{target}.{name} = {name}")
        else:
            raise ValueError(f"Unsupported client object member kind {kind!r}")


def _emit_client_objects(client_objects: dict, *, class_name: str) -> list[str]:
    if not client_objects:
        return []

    lines = [_CLIENT_OBJECT_HELPERS]
    emitted: set[str] = set()
    for spec in client_objects.values():
        _emit_client_member_definitions(spec, lines, emitted)

    for alias, spec in sorted(client_objects.items()):
        lines.append(f"{alias} = _ClientNamespace()")
        _emit_client_assignments(spec, alias, lines)
        exports = spec.get("exports", [])
        lines.append(f"{alias}.__all__ = {exports!r}")
        lines.append("")
    return lines


def _emit_client_object_bindings(client_objects: dict, *, class_name: str) -> list[str]:
    lines = []
    for alias, spec in sorted(client_objects.items()):
        bind_path = spec.get("bind_client_class_to")
        if not bind_path:
            continue
        target = alias
        for part in bind_path.split("."):
            target = f"{target}.{part}"
        lines.append(f"{target}.{class_name} = {class_name}")
    return lines


# Static helper block written verbatim into every generated driver file.
# Regular string (not f-string) — {_PREFIX}/{prop}/{e} are literal text
# that become f-strings inside the *generated* file.
_HELPERS = '''\
from importlib import import_module as _import_module

from ...core.grpc_client import rpc_client
from ...common.utils import map_arg, unmap_arg

_NO_ARG = object()


for _alias, _module_path in _CLIENT_MODULES.items():
    globals()[_alias] = _import_module(_module_path)


def _resolve_client_target(target):
    parts = str(target or "").split(".")
    if not parts or not parts[0]:
        raise ValueError(f"Invalid client constructor target: {target!r}")
    if parts[0] not in globals():
        raise ValueError(
            f"Client helper module {parts[0]!r} is not declared in _CLIENT_MODULES"
        )
    obj = globals()[parts[0]]
    for part in parts[1:]:
        obj = getattr(obj, part)
    return obj


def _client_ctor_arg(spec, self_obj, result):
    if spec == "$self":
        return self_obj
    if spec == "$result":
        return result
    if isinstance(spec, dict) and "const" in spec:
        return spec["const"]
    return spec


def _wrap_client_return(spec, self_obj, result):
    if not spec:
        return result
    if spec.get("kind") != "constructor":
        raise ValueError(f"Unsupported client_return metadata: {spec!r}")
    ctor = _resolve_client_target(spec.get("target"))
    args = [
        _client_ctor_arg(item, self_obj, result)
        for item in spec.get("args", [])
    ]
    return ctor(*args)


def _try_get(prop, token):
    return unmap_arg(rpc_client(
        function_name=f"{_PREFIX}:{prop}:GET",
        args={'a': map_arg(token)},
    ).results[prop])


def _try_set(prop, value, token):
    rpc_client(
        function_name=f"{_PREFIX}:{prop}:SET",
        args={prop: map_arg(value), 'a': map_arg(token)},
    )


def _try_call(prop, token, arg=_NO_ARG):
    if arg is _NO_ARG:
        resp = rpc_client(
            function_name=f"{_PREFIX}:{prop}:CALL0",
            args={'a': map_arg(token)},
        )
    else:
        resp = rpc_client(
            function_name=f"{_PREFIX}:{prop}:CALL1",
            args={'a': map_arg(token), 'arg1': map_arg(arg)},
        )
    result = resp.results.get(prop)
    return unmap_arg(result) if result is not None else None


def _try_calln(prop, token, kwargs):
    payload = {'a': map_arg(token)}
    for key, value in dict(kwargs).items():
        if value is _NO_ARG:
            continue
        payload[str(key)] = map_arg(value)
    resp = rpc_client(
        function_name=f"{_PREFIX}:{prop}:CALLN",
        args=payload,
    )
    result = resp.results.get(prop)
    return unmap_arg(result) if result is not None else None
'''


def _codegen(schema: dict) -> str:
    """Return the source code of a complete driver module for this schema."""
    (
        device_type,
        driver_version,
        schema_hash,
        rpc_prefix,
        class_name,
        client_modules,
        client_objects,
        getter_map,
        setter_map,
        call_map,
    ) = _schema_maps(schema)

    lines: list[str] = []

    # ── File header ───────────────────────────────────────────────────
    lines += [
        "# Auto-generated from IDL schema — do not edit by hand.",
        f"# device_type: {device_type}  "
        f"driver_version: {driver_version}  "
        f"schema_hash: {schema_hash}",
        "",
        f'_PREFIX = "{rpc_prefix}"',
        f'_SCHEMA_HASH = "{schema_hash}"',
        f"_CLIENT_MODULES = {client_modules!r}",
        f"_CLIENT_OBJECTS = {client_objects!r}",
        "",
        _HELPERS,
    ]
    lines.extend(_emit_client_objects(client_objects, class_name=class_name))
    if client_objects:
        lines.append("")

    # ── Class ─────────────────────────────────────────────────────────
    lines += [
        f"class {class_name}:",
        "",
        "    def __init__(self, token: str):",
        "        self.token = token",
        "        from ..dynamic_device import install_driver_if_stale",
        "        install_driver_if_stale(token=token, current_hash=_SCHEMA_HASH)",
    ]
    if "ip" in call_map:
        lines.append('        _try_call("ip", token)')
    lines.append("")

    # ── Properties ────────────────────────────────────────────────────
    all_props = sorted(set(getter_map) | set(setter_map))
    for prop in all_props:
        doc = (getter_map.get(prop) or setter_map.get(prop) or {}).get("doc", "")
        has_getter = prop in getter_map
        has_setter = prop in setter_map

        lines.append("    @property")
        lines.append(f"    def {prop}(self):")
        _append_doc(lines, "        ", doc)
        if has_getter:
            getter_info = getter_map.get(prop) or {}
            client_return = getter_info.get("client_return")
            if client_return is not None:
                lines.append(f'        _result = _try_get("{prop}", self.token)')
                lines.append(
                    f"        return _wrap_client_return({client_return!r}, self, _result)"
                )
            else:
                lines.append(f'        return _try_get("{prop}", self.token)')
        else:
            lines.append(f'        raise AttributeError("{prop} is write-only")')
        lines.append("")

        if has_setter:
            lines.append(f"    @{prop}.setter")
            lines.append(f"    def {prop}(self, value):")
            lines.append(f'        _try_set("{prop}", value, self.token)')
            lines.append("")

    # ── Call methods ──────────────────────────────────────────────────
    for method, info in sorted(call_map.items()):
        doc = info.get("doc", "")
        args_meta = info.get("args")

        if args_meta is None:
            lines.append(f"    def {method}(self, _v=_NO_ARG):")
            _append_doc(lines, "        ", doc)
            client_return = info.get("client_return")
            if client_return is not None:
                lines.append(f'        _result = _try_call("{method}", self.token, _v)')
                lines.append(
                    f"        return _wrap_client_return({client_return!r}, self, _result)"
                )
            else:
                lines.append(f'        return _try_call("{method}", self.token, _v)')
            lines.append("")
            continue

        if not isinstance(args_meta, list):
            raise ValueError(f"call {method!r} args must be a list")

        params = []
        inserted_keyword_marker = False
        saw_optional_positional = False
        for arg in args_meta:
            if not isinstance(arg, dict):
                raise ValueError(f"call {method!r} args entries must be dicts")
            raw_name = str(arg.get("name", "")).strip()
            if not raw_name:
                continue
            name = _require_identifier(raw_name, field=f"call {method!r} arg")
            if arg.get("kind") == "keyword_only" and not inserted_keyword_marker:
                params.append("*")
                inserted_keyword_marker = True
            is_keyword_only = arg.get("kind") == "keyword_only"
            is_required = arg.get("required", True)
            if is_required and saw_optional_positional and not is_keyword_only:
                raise ValueError(
                    f"call {method!r} has required positional arg {name!r} "
                    "after an optional positional arg"
                )
            if is_required:
                params.append(name)
            else:
                params.append(f"{name}=_NO_ARG")
                if not is_keyword_only:
                    saw_optional_positional = True

        signature = ", ".join(["self", *params])
        lines.append(f"    def {method}({signature}):")
        _append_doc(lines, "        ", doc)

        arg_names = [
            _require_identifier(str(arg.get("name", "")).strip(), field=f"call {method!r} arg")
            for arg in args_meta
            if str(arg.get("name", "")).strip()
        ]
        client_return = info.get("client_return")
        if len(arg_names) == 0:
            if client_return is not None:
                lines.append(f'        _result = _try_call("{method}", self.token)')
                lines.append(
                    f"        return _wrap_client_return({client_return!r}, self, _result)"
                )
            else:
                lines.append(f'        return _try_call("{method}", self.token)')
        elif len(arg_names) == 1 and args_meta[0].get("required", True):
            if client_return is not None:
                lines.append(
                    f'        _result = _try_call("{method}", self.token, {arg_names[0]})'
                )
                lines.append(
                    f"        return _wrap_client_return({client_return!r}, self, _result)"
                )
            else:
                lines.append(f'        return _try_call("{method}", self.token, {arg_names[0]})')
        else:
            if client_return is not None:
                lines.append(f'        _result = _try_calln("{method}", self.token, {{')
            else:
                lines.append(f'        return _try_calln("{method}", self.token, {{')
            for arg_name in arg_names:
                lines.append(f'            "{arg_name}": {arg_name},')
            lines.append("        })")
            if client_return is not None:
                lines.append(
                    f"        return _wrap_client_return({client_return!r}, self, _result)"
                )
        lines.append("")

    binding_lines = _emit_client_object_bindings(client_objects, class_name=class_name)
    if binding_lines:
        lines.extend(binding_lines)
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# File writer (shared by install_driver and install_driver_if_stale)
# ─────────────────────────────────────────────────────────────────────────────

# drivers/ lives next to this file.
_DRIVERS_DIR = Path(__file__).parent


def _write_driver_files(schema: dict) -> Path:
    (
        device_type,
        _driver_version,
        _schema_hash,
        _rpc_prefix,
        class_name,
        client_modules,
        client_objects,
        _getter_map,
        _setter_map,
        _call_map,
    ) = _schema_maps(schema)
    pkg_dir = _DRIVERS_DIR / device_type
    pkg_dir.mkdir(exist_ok=True)
    (pkg_dir / f"{device_type}_remote.py").write_text(_codegen(schema), encoding="utf-8")
    init_lines = [
        "from importlib import import_module as _import_module",
        "",
        f"from . import {device_type}_remote as adi",
        f"from .{device_type}_remote import {class_name}",
        "",
        f'__all__ = ["adi", "{class_name}"]',
    ]

    for alias, module_path in sorted(client_modules.items()):
        init_lines.extend([
            f'{alias} = _import_module("{module_path}")',
            f'__all__.append("{alias}")',
            f'if hasattr({alias}, "bind_client_class"):',
            f'    {alias}.bind_client_class("{class_name}", {class_name})',
            f'for _name in getattr({alias}, "__all__", ()):',
            f'    globals().setdefault(_name, getattr({alias}, _name))',
            f'    if _name not in __all__:',
            f'        __all__.append(_name)',
            "",
        ])

    for alias, spec in sorted(client_objects.items()):
        exports = list(spec.get("exports", []))
        init_lines.extend([
            f"from .{device_type}_remote import {alias}",
            f'__all__.append("{alias}")',
        ])
        for export in exports:
            init_lines.extend([
                f"globals().setdefault({export!r}, getattr({alias}, {export!r}))",
                f'if {export!r} not in __all__:',
                f'    __all__.append({export!r})',
            ])
        init_lines.append("")

    (pkg_dir / "__init__.py").write_text("\n".join(init_lines) + "\n", encoding="utf-8")
    return pkg_dir


def _print_driver_cached(pkg_dir: Path) -> None:
    print("Device drivers updated successfully.")


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def install_driver(*, token: str = None, device_id: int = None, device_name: str = None) -> Path:
    """
    Fetch the IDL schema and write a driver package to disk.

    Creates:
        drivers/<device_type>/__init__.py
        drivers/<device_type>/<device_type>_remote.py

    Returns the path to the generated package directory.
    """
    schema = fetch_idl(token=token, device_id=device_id, device_name=device_name)
    pkg_dir = _write_driver_files(schema)
    _print_driver_cached(pkg_dir)
    return pkg_dir


def ensure_driver(*, token: str = None, device_id: int = None, device_name: str = None) -> None:
    """
    Install the driver if it isn't already on disk; skip if it is.

    Call this on any machine that needs to use the device but didn't perform
    the reservation (and therefore never had install_driver run automatically).
    Pass the reservation token — the server resolves which device it belongs to.

        from remoteRF.drivers import ensure_driver
        ensure_driver(token=token)

        from remoteRF.drivers.pluto import *
        sdr = adi.Pluto(token)

    One IDL:get_drivers RPC is always made (to resolve device_type and hash).
    Files are only written when missing or stale.
    """
    schema = fetch_idl(token=token, device_id=device_id, device_name=device_name)
    device_type = _schema_maps(schema)[0]
    remote_path = _DRIVERS_DIR / device_type / f"{device_type}_remote.py"

    if not remote_path.exists():
        pkg_dir = _write_driver_files(schema)
        _print_driver_cached(pkg_dir)
        return

    # File exists — check staleness via the baked-in _SCHEMA_HASH line.
    current_hash = _read_schema_hash(remote_path)
    if current_hash != schema.get("schema_hash"):
        _write_driver_files(schema)
        print(
            f"Driver for '{device_type}' updated "
            f"(was {(current_hash or '?')[:16]}…, "
            f"now {schema.get('schema_hash','?')[:16]}…).\n"
            f"Reimport before continuing: from remoteRF.drivers.{device_type} import *"
        )


def _read_schema_hash(path: Path) -> str | None:
    """Extract _SCHEMA_HASH value from a generated driver file without importing it."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("_SCHEMA_HASH"):
                # _SCHEMA_HASH = "sha256:..."
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return None


def install_driver_if_stale(*, token: str, current_hash: str) -> bool:
    """
    Fetch the server schema hash via the reservation token and reinstall the
    driver only if it has changed since the file on disk was generated.

    Called automatically by every generated device __init__:

        sdr = adi.Pluto(token)

    Returns True if the driver was reinstalled, False if already up to date.
    If the driver was reinstalled, reimport the package before using the
    new class:

        from remoteRF.drivers.pluto import *
    """
    schema = fetch_idl(token=token)
    if schema.get("schema_hash") == current_hash:
        return False

    device_type = _schema_maps(schema)[0]
    _write_driver_files(schema)
    print(
        f"Driver for '{device_type}' updated "
        f"(was {current_hash[:16]}…, now {schema.get('schema_hash','?')[:16]}…).\n"
        f"Reimport before continuing: from remoteRF.drivers.{device_type} import *"
    )
    return True
