from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Pattern, Tuple


@dataclass
class IntentSpec:
    name: str
    capability: Optional[str]
    keywords: List[str]
    patterns: List[Pattern]
    confirm: bool = False


@dataclass
class IntentMatch:
    spec: IntentSpec
    score: float
    match: Optional[re.Match]


@dataclass
class ActionPlan:
    capability: Optional[str]
    actions: List[Dict[str, Any]]
    args: Dict[str, Any]
    confidence: float
    confirm: bool
    summary: str
    local_action: Optional[str] = None
    clarify: Optional[Dict[str, Any]] = None


class ContextCache:
    def __init__(self, ttl_seconds: int = 600):
        self.ttl_seconds = int(ttl_seconds)
        self._data: Dict[Tuple[int, int], Dict[str, Any]] = {}

    def _key(self, guild_id: int, user_id: int) -> Tuple[int, int]:
        return int(guild_id or 0), int(user_id or 0)

    def get(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        key = self._key(guild_id, user_id)
        entry = self._data.get(key)
        if not entry:
            return {}
        if time.time() - float(entry.get("at", 0)) > self.ttl_seconds:
            self._data.pop(key, None)
            return {}
        return dict(entry.get("ctx", {}))

    def update(self, guild_id: int, user_id: int, updates: Dict[str, Any]) -> None:
        key = self._key(guild_id, user_id)
        ctx = self._data.get(key, {}).get("ctx", {})
        ctx.update(updates)
        self._data[key] = {"at": time.time(), "ctx": ctx}


