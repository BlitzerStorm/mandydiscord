import inspect
from typing import Any, Dict, List, Optional, Tuple


CAPABILITY_DEFS: Dict[str, Dict[str, Any]] = {
    "send_message": {
        "description": "Send a message to a channel.",
        "required": ["channel_id", "text"],
        "optional": [],
        "args": {
            "channel_id": {"type": "int", "min": 1},
            "text": {"type": "str", "min_len": 1, "max_len": 1900},
        },
        "permissions": "GOD",
        "side_effects": "state-changing",
        "cost_tier": "cheap",
        "pass_actor": False,
    },
    "reply_to_message": {
        "description": "Reply to a specific message in a channel.",
        "required": ["channel_id", "message_id", "text"],
        "optional": [],
        "args": {
            "channel_id": {"type": "int", "min": 1},
            "message_id": {"type": "int", "min": 1},
            "text": {"type": "str", "min_len": 1, "max_len": 1900},
        },
        "permissions": "GOD",
        "side_effects": "state-changing",
        "cost_tier": "cheap",
        "pass_actor": False,
    },
    "set_bot_status": {
        "description": "Set the bot presence state and status text.",
        "required": ["state", "text"],
        "optional": [],
        "args": {
            "state": {"type": "str", "choices": ["online", "idle", "dnd", "invisible"]},
            "text": {"type": "str", "min_len": 0, "max_len": 120},
        },
        "permissions": "GOD",
        "side_effects": "state-changing",
        "cost_tier": "cheap",
        "pass_actor": False,
    },
    "get_recent_transcript": {
        "description": "Fetch recent messages from a channel for context.",
        "required": ["channel_id"],
        "optional": ["limit"],
        "args": {
            "channel_id": {"type": "int", "min": 1},
            "limit": {"type": "int", "min": 1, "max": 80},
        },
        "permissions": "GOD",
        "side_effects": "read-only",
        "cost_tier": "normal",
        "pass_actor": False,
    },
    "add_watcher": {
        "description": "Add or update a watcher for a user.",
        "required": ["target_user_id", "count", "text"],
        "optional": [],
        "args": {
            "target_user_id": {"type": "int", "min": 1},
            "count": {"type": "int", "min": 1, "max": 1000000},
            "text": {"type": "str", "min_len": 1, "max_len": 500},
        },
        "permissions": "GOD",
        "side_effects": "state-changing",
        "cost_tier": "cheap",
        "pass_actor": True,
    },
    "remove_watcher": {
        "description": "Remove a watcher for a user.",
        "required": ["target_user_id"],
        "optional": [],
        "args": {
            "target_user_id": {"type": "int", "min": 1},
        },
        "permissions": "GOD",
        "side_effects": "state-changing",
        "cost_tier": "cheap",
        "pass_actor": True,
    },
    "list_watchers": {
        "description": "List configured watchers.",
        "required": [],
        "optional": [],
        "args": {},
        "permissions": "GOD",
        "side_effects": "read-only",
        "cost_tier": "cheap",
        "pass_actor": False,
    },
    "list_mirror_rules": {
        "description": "List mirror rules.",
        "required": [],
        "optional": [],
        "args": {},
        "permissions": "GOD",
        "side_effects": "read-only",
        "cost_tier": "cheap",
        "pass_actor": False,
    },
    "create_mirror": {
        "description": "Create a mirror rule between two channels.",
        "required": ["source_channel_id", "target_channel_id"],
        "optional": [],
        "args": {
            "source_channel_id": {"type": "int", "min": 1},
            "target_channel_id": {"type": "int", "min": 1},
        },
        "permissions": "GOD",
        "side_effects": "state-changing",
        "cost_tier": "normal",
        "pass_actor": True,
    },
    "disable_mirror_rule": {
        "description": "Disable a mirror rule by rule id.",
        "required": ["rule_id"],
        "optional": [],
        "args": {
            "rule_id": {"type": "str", "min_len": 1, "max_len": 96},
        },
        "permissions": "GOD",
        "side_effects": "state-changing",
        "cost_tier": "cheap",
        "pass_actor": True,
    },
    "show_stats": {
        "description": "Show chat statistics for a window, optionally scoped to a user.",
        "required": ["scope"],
        "optional": ["user_id", "guild_id"],
        "args": {
            "scope": {
                "type": "str",
                "choices": ["daily", "weekly", "monthly", "yearly", "rolling24"],
            },
            "user_id": {"type": "int", "min": 1},
            "guild_id": {"type": "int", "min": 1},
        },
        "permissions": "GOD",
        "side_effects": "read-only",
        "cost_tier": "normal",
        "pass_actor": False,
    },
    "send_dm": {
        "description": "Send a direct message to a user.",
        "required": ["user_id", "text"],
        "optional": [],
        "args": {
            "user_id": {"type": "int", "min": 1},
            "text": {"type": "str", "min_len": 1, "max_len": 1900},
        },
        "permissions": "GOD",
        "side_effects": "state-changing",
        "cost_tier": "normal",
        "pass_actor": False,
    },
    "list_capabilities": {
        "description": "Summarize available tools, extensions, models, and queue status.",
        "required": [],
        "optional": [],
        "args": {},
        "permissions": "GOD",
        "side_effects": "read-only",
        "cost_tier": "cheap",
        "pass_actor": False,
    },
}


class CapabilityRegistry:
    def __init__(self, tool_registry: Any, defs: Optional[Dict[str, Dict[str, Any]]] = None):
        self._tool_registry = tool_registry
        self._defs = defs or CAPABILITY_DEFS

    def tool_names(self) -> List[str]:
        dynamic = []
        if self._tool_registry:
            dynamic = list(getattr(self._tool_registry, "dynamic_tools", {}).keys())
        return sorted(set(self._defs.keys()) | set(dynamic))

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        if name in self._defs:
            return self._defs.get(name)
        dynamic = getattr(self._tool_registry, "dynamic_tools", {})
        return dynamic.get(name)

    def requires_actor(self, name: str) -> bool:
        if name in self._defs:
            return bool(self._defs.get(name, {}).get("pass_actor"))
        dynamic = getattr(self._tool_registry, "dynamic_tools", {})
        return bool(dynamic.get(name, {}).get("pass_actor"))

    def snapshot(self) -> List[Dict[str, Any]]:
        tools = []
        for name in self.tool_names():
            spec = self.get(name) or {}
            required, optional, args = self._normalize_spec(name, spec)
            tools.append({
                "name": name,
                "description": spec.get("description", ""),
                "required": required,
                "optional": optional,
                "args": args,
                "permissions": spec.get("permissions", ""),
                "side_effects": spec.get("side_effects", ""),
                "cost_tier": spec.get("cost_tier", ""),
            })
        return tools

    def validate_tool_call(self, tool: str, args: Dict[str, Any]) -> Tuple[bool, str]:
        spec = self.get(tool)
        if not spec:
            return False, f"tool not allowed: {tool}"
        if not isinstance(args, dict):
            return False, "args must be dict"
        required, optional, arg_defs = self._normalize_spec(tool, spec)
        missing = [k for k in required if k not in args]
        if missing:
            return False, f"missing args for {tool}: {sorted(missing)}"
        extra = set(args.keys()) - set(required) - set(optional)
        if extra:
            return False, f"extra args for {tool}: {sorted(extra)}"
        for key, value in args.items():
            ok, err = self._validate_arg(key, value, arg_defs.get(key, {}))
            if not ok:
                return False, f"{tool}.{key}: {err}"
        return True, ""

    def format_tools_summary(self, include_args: bool = True) -> str:
        lines = []
        for name in self.tool_names():
            spec = self.get(name) or {}
            line = f"- {name}: {spec.get('description', '').strip()}"
            if include_args:
                line += f" args={self._format_args(spec)}"
            lines.append(line.strip())
        return "\n".join(lines)

    def verify_tool_registry(self) -> Tuple[bool, List[str]]:
        errors: List[str] = []
        method_names = {
            name
            for name, member in inspect.getmembers(self._tool_registry, predicate=callable)
            if not name.startswith("_")
        }
        allowed = set(self._defs.keys())
        missing = allowed - method_names
        if missing:
            errors.append(f"tool registry missing: {sorted(missing)}")
        return len(errors) == 0, errors

    def _format_args(self, spec: Dict[str, Any]) -> str:
        parts = []
        required, optional, args = self._normalize_spec("", spec)
        for key in required:
            parts.append(self._format_arg(key, args.get(key, {}), required=True))
        for key in optional:
            parts.append(self._format_arg(key, args.get(key, {}), required=False))
        return "{" + ", ".join(parts) + "}"

    def _format_arg(self, key: str, meta: Dict[str, Any], required: bool) -> str:
        type_name = meta.get("type", "any")
        suffix = ""
        choices = meta.get("choices")
        if choices:
            suffix = f" choices={choices}"
        if "min" in meta or "max" in meta:
            suffix += f" range={meta.get('min')}-{meta.get('max')}"
        if "min_len" in meta or "max_len" in meta:
            suffix += f" len={meta.get('min_len')}-{meta.get('max_len')}"
        flag = "required" if required else "optional"
        return f"{key}:{type_name}({flag}{suffix})"

    def _validate_arg(self, key: str, value: Any, meta: Dict[str, Any]) -> Tuple[bool, str]:
        if not meta:
            return True, ""
        arg_type = meta.get("type")
        if arg_type == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                return False, "must be int"
            if "min" in meta and value < int(meta["min"]):
                return False, f"must be >= {meta['min']}"
            if "max" in meta and value > int(meta["max"]):
                return False, f"must be <= {meta['max']}"
        elif arg_type == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False, "must be float"
            if "min" in meta and float(value) < float(meta["min"]):
                return False, f"must be >= {meta['min']}"
            if "max" in meta and float(value) > float(meta["max"]):
                return False, f"must be <= {meta['max']}"
        elif arg_type == "bool":
            if not isinstance(value, bool):
                return False, "must be bool"
        elif arg_type == "enum":
            choices = meta.get("enum") or meta.get("choices")
            if not isinstance(choices, list) or not choices:
                return False, "enum choices missing"
            if value not in choices:
                return False, f"must be one of {choices}"
        elif arg_type == "str":
            if not isinstance(value, str):
                return False, "must be str"
            if "min_len" in meta and len(value) < int(meta["min_len"]):
                return False, f"length must be >= {meta['min_len']}"
            if "max_len" in meta and len(value) > int(meta["max_len"]):
                return False, f"length must be <= {meta['max_len']}"
            choices = meta.get("choices")
            if choices and value not in choices:
                return False, f"must be one of {choices}"
        return True, ""

    def _normalize_spec(self, name: str, spec: Dict[str, Any]) -> Tuple[List[str], List[str], Dict[str, Any]]:
        if "required" in spec or "optional" in spec:
            return list(spec.get("required", [])), list(spec.get("optional", [])), dict(spec.get("args", {}))
        args_schema = dict(spec.get("args_schema", {}) or {})
        required: List[str] = []
        optional: List[str] = []
        normalized: Dict[str, Any] = {}
        for arg, meta in args_schema.items():
            arg_meta = dict(meta or {})
            arg_type = arg_meta.get("type")
            is_optional = bool(arg_meta.get("required") is False or arg_type == "optional")
            if is_optional:
                optional.append(arg)
            else:
                required.append(arg)
            normalized[arg] = arg_meta
        return required, optional, normalized
