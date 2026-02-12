from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import msgpack


DEFAULT_STORE: dict[str, Any] = {
    "meta": {"version": 1},
    "soc": {
        "user_tiers": {},
        "role_tiers": {
            "ACCESS:Guest": 1,
            "ACCESS:Member": 10,
            "ACCESS:Staff": 50,
            "ACCESS:Admin": 70,
            "ACCESS:SOC": 90,
        },
    },
    "watchers": {},
    "watcher_counts": {},
    "mirrors": {
        "servers": {},
        "ignored_user_ids": [],
    },
    "onboarding": {
        "bypass_user_ids": [],
    },
    "guest_access": {
        "password": "",
        "verified_user_ids": [],
    },
    "dm_bridges": {},
    "feature_requests": {
        "next_id": 1,
        "requests": {},
        "grants": {
            "once": {},
            "permanent": {},
        },
    },
    "ai": {
        "guild_modes": {},
        "long_term_memory": {},
        "last_api_test": {},
        "auto_model": "",
        "auto_vision_model": "",
        "profiles": {},
        "memory_facts": {},
        "relationships": {},
        "warmup": {},
        "shadow_brain": {
            "events": [],
            "last_plan_text": "",
        },
        "dm_brain": {
            "events": [],
        },
        "hive_brain": {
            "notes": [],
            "last_sync_ts": 0.0,
        },
    },
    "shadow_league": {
        "pending_user_ids": [],
        "member_user_ids": [],
        "nickname_map": {},
        "blocked_user_ids": [],
        "invite_min_affinity": 0.15,
        "invite_cooldown_sec": 7 * 24 * 60 * 60,
        "ai_enabled": True,
        "loop_interval_sec": 150,
        "max_actions_per_cycle": 3,
        "last_cycle_ts": 0.0,
        "last_cycle_results": [],
    },
    "ui": {
        "global_menu_message_id": 0,
    },
    "logs": [],
}


class MessagePackStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._dirty = False
        self.data: dict[str, Any] = {}

    async def load(self) -> None:
        async with self._lock:
            if not self.path.exists():
                self.data = _clone_defaults()
                await self._save_unlocked()
                return
            raw = self.path.read_bytes()
            self.data = msgpack.unpackb(raw, raw=False)
            self._ensure_schema()

    async def autosave_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            if self._dirty:
                await self.save()

    async def save(self) -> None:
        async with self._lock:
            await self._save_unlocked()

    async def _save_unlocked(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        packed = msgpack.packb(self.data, use_bin_type=True)
        tmp.write_bytes(packed)
        tmp.replace(self.path)
        self._dirty = False

    def touch(self) -> None:
        self._dirty = True

    def _ensure_schema(self) -> None:
        defaults = _clone_defaults()
        for key, value in defaults.items():
            if key not in self.data:
                self.data[key] = value
        self._dirty = True


def _clone_defaults() -> dict[str, Any]:
    return msgpack.unpackb(msgpack.packb(DEFAULT_STORE, use_bin_type=True), raw=False)
