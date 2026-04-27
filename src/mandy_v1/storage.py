from __future__ import annotations

import asyncio
from pathlib import Path
import time
from typing import Any

import msgpack


DEFAULT_STORE: dict[str, Any] = {
    "meta": {"version": 1},
    "soc": {
        "user_tiers": {},
        "role_tiers": {
            "ACCESS:Guest": 1,
            "ACCESS:Member": 10,
            "ACCESS:Engineer": 50,
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
        "pending_access_rechecks": {},
    },
    "guest_access": {
        "password": "",
        "verified_user_ids": [],
    },
    "dm_bridges": {},
    "emotion": {
        "state": "neutral",
        "intensity": 0.5,
        "last_updated": 0,
        "event_log": [],
    },
    "episodic": {
        "episodes": {},
    },
    "identity": {
        "seeded": False,
        "opinions": {},
        "interests": [],
        "dislikes": [],
    },
    "personas": {},
    "culture": {},
    "expansion": {
        "target_users": {},
        "known_servers": {},
        "approach_log": [],
        "invite_links": {},
        "cooldowns": {},
        "queue": [],
        "last_scan_ts": 0,
    },
    "proactive": {
        "guild_cooldowns": {},
        "user_cooldowns": {},
        "nicknames": {},
        "last_loop_ts": 0,
    },
    "feature_requests": {
        "next_id": 1,
        "requests": {},
        "grants": {
            "once": {},
            "permanent": {},
        },
    },
    "autonomy_policy": {
        "mode": "assist",
        "allowed_actions": [],
        "action_log": [],
        "proposals": [],
        "next_proposal_id": 1,
        "require_approval": False,
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
        "guild_style": {},
        "reflections": {},
        "fun_modes": {},
        "capabilities": {},
        "prompt_injection": {
            "master_prompt": "",
            "master_learning_mode": "full",
            "guild_prompts": {},
            "guild_learning_modes": {},
            "audit_log": [],
        },
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
            try:
                loaded = msgpack.unpackb(raw, raw=False)
            except (msgpack.UnpackException, ValueError) as exc:
                backup = self._backup_corrupt_store(raw)
                self.data = _clone_defaults()
                await self._save_unlocked()
                raise RuntimeError(f"Store file was unreadable and was reset. Corrupt copy: {backup}") from exc
            if not isinstance(loaded, dict):
                backup = self._backup_corrupt_store(raw)
                self.data = _clone_defaults()
                await self._save_unlocked()
                raise RuntimeError(f"Store root was not a mapping and was reset. Corrupt copy: {backup}")
            self.data = loaded
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
        changed = _merge_defaults(self.data, defaults)
        if changed:
            self._dirty = True

    def _backup_corrupt_store(self, raw: bytes) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        suffix = 1
        while backup.exists():
            suffix += 1
            backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}-{suffix}")
        backup.write_bytes(raw)
        return backup


def _clone_defaults() -> dict[str, Any]:
    return msgpack.unpackb(msgpack.packb(DEFAULT_STORE, use_bin_type=True), raw=False)


def _merge_defaults(target: dict[str, Any], defaults: dict[str, Any]) -> bool:
    changed = False
    for key, default_value in defaults.items():
        if key not in target:
            target[key] = default_value
            changed = True
            continue
        current = target[key]
        if isinstance(default_value, dict):
            if not isinstance(current, dict):
                target[key] = default_value
                changed = True
                continue
            changed = _merge_defaults(current, default_value) or changed
        elif isinstance(default_value, list) and not isinstance(current, list):
            target[key] = default_value
            changed = True
    return changed
