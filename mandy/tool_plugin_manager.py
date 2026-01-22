import asyncio
import importlib.util
import inspect
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from extensions.validator import validate_extension_path, validate_extension_source

ALLOWED_TYPES = {"int", "float", "bool", "str", "enum", "optional"}


@dataclass
class ToolContext:
    bot: Any
    guild: Any
    channel: Any
    author: Any
    message_id: int

    async def send(self, text: str):
        if self.channel:
            return await self.channel.send(text)
        return None


class ToolPluginManager:
    def __init__(self, bot: Any, tool_registry: Any, log_fn=None):
        self.bot = bot
        self.tool_registry = tool_registry
        self.log_fn = log_fn

    async def load_all(self) -> None:
        base = "extensions"
        if not os.path.isdir(base):
            return
        for name in os.listdir(base):
            if not name.endswith(".py"):
                continue
            if name in ("__init__.py", "validator.py"):
                continue
            path = os.path.join(base, name)
            await self.load_plugin(path)

    async def load_plugin(self, path: str) -> None:
        ok, err = validate_extension_path(path.replace("\\", "/"))
        if not ok:
            raise ValueError(err)
        try:
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
        except Exception as exc:
            raise ValueError(f"failed to read plugin: {exc}") from exc
        valid, errors = validate_extension_source("", source)
        if not valid:
            raise ValueError("; ".join(errors[:6]))

        module_name = os.path.splitext(path.replace("\\", "/"))[0].replace("/", ".")
        spec = importlib.util.spec_from_file_location(module_name, path)
        if not spec or not spec.loader:
            raise ValueError("failed to load plugin spec")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore

        exports = getattr(module, "TOOL_EXPORTS", None)
        if exports is None:
            return
        if not isinstance(exports, dict):
            raise ValueError("TOOL_EXPORTS must be a dict")

        for tool_name, meta in exports.items():
            self._register_tool(tool_name, meta, module_name)

    def build_context(self, guild: Any, channel: Any, author: Any, message_id: int) -> ToolContext:
        return ToolContext(bot=self.bot, guild=guild, channel=channel, author=author, message_id=message_id)

    def _register_tool(self, tool_name: str, meta: Dict[str, Any], module_name: str) -> None:
        if not isinstance(tool_name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", tool_name):
            raise ValueError(f"invalid tool name: {tool_name}")
        if tool_name in self.tool_registry.__dict__ or hasattr(self.tool_registry, tool_name):
            raise ValueError(f"tool name collides with built-in: {tool_name}")
        existing = self.tool_registry.get_dynamic_tool(tool_name)
        if existing:
            if existing.get("module") == module_name:
                return
            raise ValueError(f"tool name already registered: {tool_name}")
        if not isinstance(meta, dict):
            raise ValueError("tool meta must be dict")

        description = meta.get("description")
        args_schema = meta.get("args_schema")
        side_effect = meta.get("side_effect")
        cost = meta.get("cost")
        handler = meta.get("handler")

        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"{tool_name}: description required")
        if not isinstance(args_schema, dict):
            raise ValueError(f"{tool_name}: args_schema must be dict")
        if side_effect not in ("read", "write"):
            raise ValueError(f"{tool_name}: side_effect must be read or write")
        if cost not in ("cheap", "normal", "expensive"):
            raise ValueError(f"{tool_name}: cost must be cheap|normal|expensive")
        if not inspect.iscoroutinefunction(handler):
            raise ValueError(f"{tool_name}: handler must be async")

        sig = inspect.signature(handler)
        params = list(sig.parameters.values())
        if not params or params[0].name not in ("ctx", "bot_ctx"):
            raise ValueError(f"{tool_name}: handler must accept ctx or bot_ctx")

        for arg, schema in args_schema.items():
            if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", str(arg)):
                raise ValueError(f"{tool_name}: invalid arg name {arg}")
            if not isinstance(schema, dict):
                raise ValueError(f"{tool_name}: schema for {arg} must be dict")
            arg_type = schema.get("type")
            if arg_type not in ALLOWED_TYPES:
                raise ValueError(f"{tool_name}: unsupported type for {arg}")
            if arg_type == "enum":
                choices = schema.get("enum")
                if not isinstance(choices, list) or not choices:
                    raise ValueError(f"{tool_name}: enum choices required for {arg}")
            if arg_type in ("int", "float"):
                if "min" in schema and not isinstance(schema["min"], (int, float)):
                    raise ValueError(f"{tool_name}: min must be number for {arg}")
                if "max" in schema and not isinstance(schema["max"], (int, float)):
                    raise ValueError(f"{tool_name}: max must be number for {arg}")
            if arg_type == "str":
                if "min_len" in schema and not isinstance(schema["min_len"], int):
                    raise ValueError(f"{tool_name}: min_len must be int for {arg}")
                if "max_len" in schema and not isinstance(schema["max_len"], int):
                    raise ValueError(f"{tool_name}: max_len must be int for {arg}")

        meta_copy = {
            "description": description,
            "args_schema": args_schema,
            "side_effects": "read-only" if side_effect == "read" else "state-changing",
            "cost_tier": cost,
            "handler": handler,
            "module": module_name,
            "pass_actor": False,
        }
        self.tool_registry.register_dynamic_tool(tool_name, meta_copy)

        if self.log_fn:
            coro = self.log_fn("audit", f"[Plugin] Loaded tool {tool_name} from {module_name}")
            if asyncio.iscoroutine(coro):
                try:
                    asyncio.create_task(coro)
                except Exception:
                    pass
