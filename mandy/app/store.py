from __future__ import annotations

from typing import Any, Dict

from mandy.cooldown_store import CooldownStore

from . import config
from .store_backend import JsonStore
from .store_defaults import DEFAULT_JSON


STORE = JsonStore(config.DB_JSON_PATH)
MENTION_COOLDOWN = CooldownStore(STORE)


def cfg() -> Dict[str, Any]:
    return STORE.data


def ai_cfg() -> Dict[str, Any]:
    return cfg().setdefault("ai", {})


__all__ = [
    "DEFAULT_JSON",
    "JsonStore",
    "STORE",
    "MENTION_COOLDOWN",
    "cfg",
    "ai_cfg",
]
