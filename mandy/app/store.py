import asyncio
import datetime
import json
import os
import time
from typing import Any, Dict

import aiofiles
import aiofiles.os
import aiofiles.ospath

from mandy.cooldown_store import CooldownStore

from . import config

DEFAULT_JSON: Dict[str, Any] = {
    "targets": {},
    "mirrors": {"interactive_controls_enabled": True},
    "mirror_rules": {},
    "mirror_status": {},
    "admin_servers": {},
    "server_status_messages": {},
    "server_info_messages": {},
    "mirror_message_map": {},
    "dm_bridges": {},
    "dm_bridge_controls": {},
    "dm_ai": {},
    "bot_status": {"state": "online", "text": ""},
    "presence": {
        "bio": "",
        "autopresence_enabled": False,
        "last_message_ts": 0,
        "last_super_interaction_ts": 0,
    },
    "ambient_engine": {"enabled": True, "last_typing": 0, "last_presence": 0},
    "permissions": {},
    "gate": {},
    "mirror_fail_threshold": config.MIRROR_FAIL_THRESHOLD,
    "logs": {"system": None, "audit": None, "debug": None, "mirror": None, "ai": None, "voice": None},
    "command_channels": {"user": "command-requests", "god": "admin-chat", "mode": "off"},
    "menu_messages": {},
    "rbac": {"role_levels": config.ROLE_LEVEL_DEFAULTS.copy()},
    "auto": {"setup": True, "backfill": True, "backfill_limit": 50, "backfill_per_channel": 20, "backfill_delay": 0.2},
    "tuning": {"setup_delay": 1.0},
    "ai": {
        "default_model": "gemini-2.5-flash-lite",
        "router_model": "gemini-2.5-flash-lite",
        "tts_model": "",
        "cooldown_seconds": 5,
        "limits": config.DEFAULT_AI_LIMITS.copy(),
        "queue": {},
        "rolling": {},
        "daily": {},
        "installed_extensions": [],
    },
    "mandy": {"mention_dm_cooldowns": {}, "power_mode": True},
    "sentience": {
        "enabled": True,
        "dialect": "sentient_core",
        "channels": {},
        "thoughts_rate_limit_seconds": 30,
        "menu_style": "default",
        "daily_reflection": {
            "enabled": False,
            "last_run_utc": 0,
            "hour_utc": None,
            "max_messages": 120,
            "fallback_enabled": False,
        },
        "internal_monologue": {
            "enabled": False,
            "last_run_utc": 0,
            "interval_minutes": 180,
            "max_lines": 4,
        },
        "maintenance": {"enabled": True, "ai_queue_max_age_hours": 6},
    },
    "diagnostics": {"channel_id": 0, "message_id": 0, "last_update": 0},
    "manual": {
        "channel_id": 0,
        "last_hash": "",
        "last_message_id": 0,
        "last_upload": 0,
        "auto_upload_enabled": False,
    },
    "memory": {"events": []},
    "ark_snapshots": {},
    "phoenix_keys": {},
    "onboarding": {"rules_channel_id": 0, "role_name": "Citizen", "phrases": ["i agree"]},
    "backfill_state": {"done": {}},
    "chat_stats": {},
    "chat_stats_backfill_done": {},
    "chat_stats_live_message": {},
    "chat_stats_global_live_message": {},
    "layout": {
        "categories": {
            "Welcome & Information": ["rules-and-guidelines", "announcements", "guest-briefing"],
            "Bot Control & Monitoring": ["bot-status", "command-requests", "error-reporting"],
            "Research & Development": ["algorithm-discussion", "data-analysis"],
            "Guest Access": ["guest-chat", "guest-feedback", "quarantine"],
            "Engineering Core": ["core-chat", "system-logs", "audit-logs", "debug-logs", "mirror-logs"],
            "Admin Backrooms": ["admin-chat", "server-management"],
            "DM Bridges": [],
        }
    },
    "channel_topics": {
        "rules-and-guidelines": "Read these first. Required for all members.",
        "announcements": "Server announcements and updates.",
        "guest-briefing": "How to join and get approved.",
        "guest-chat": "Guest chat (limited).",
        "guest-feedback": "Feedback and questions from guests.",
        "quarantine": "Restricted holding channel.",
        "bot-status": "Bot status updates and presence controls.",
        "command-requests": "User command requests. Commands outside this channel are removed.",
        "error-reporting": "Report issues or errors with commands.",
        "core-chat": "Core engineering discussion.",
        "algorithm-discussion": "Research ideas, algorithms, and experiments.",
        "data-analysis": "Data analysis, metrics, and reports.",
        "system-logs": "System log stream (general).",
        "audit-logs": "Audit trail for privileged actions.",
        "debug-logs": "Debug output and diagnostics.",
        "mirror-logs": "Mirror pipeline events and failures.",
        "admin-chat": "GOD-only commands and admin coordination.",
        "server-management": "Server ops notes and maintenance.",
    },
    "pinned_text": {
        "rules-and-guidelines": (
            "dY\"O **Rules & Guidelines**\n"
            "- Be respectful.\n"
            "- No spam.\n"
            "- Follow staff instructions.\n\n"
            "**Commands (prefix):**\n"
            "- `!menu`\n"
            "- `!godmenu`\n"
            "- `!setup fullsync`\n"
        ),
        "bot-status": ("dY\"O **Bot Status & Help**\n" "Menus auto-populate in command channels.\n"),
        "system-logs": ("dY\"O **System Logs**\n" "General system log stream.\n"),
        "command-requests": ("dY\"O **Command Requests**\n" "Use the menu panel below for user tools.\n"),
        "error-reporting": (
            "dY\"O **Error Reporting**\n"
            "Post issues with timestamps and screenshots if possible.\n"
        ),
        "audit-logs": ("dY\"O **Audit Logs**\n" "Privileged actions and security events.\n"),
        "debug-logs": ("dY\"O **Debug Logs**\n" "Diagnostic output and errors.\n"),
        "mirror-logs": ("dY\"O **Mirror Logs**\n" "Mirror events, failures, and status.\n"),
        "guest-briefing": (
            "dY\"O **Guest Briefing**\n"
            "This server uses a password gate. Ask staff if youź?Tre stuck.\n"
        ),
        "quarantine": ("dY\"O **Quarantine**\n" "Quarantined users wait here until staff releases them.\n"),
        "admin-chat": ("dY\"O **Admin Chat**\n" "GOD-only command channel. Use the panel below.\n"),
    },
}


class JsonStore:
    def __init__(self, path: str):
        self.path = path
        self.lock = asyncio.Lock()
        self.data: Dict[str, Any] = {}
        self.dirty = False
        self.last_mtime = 0.0
        self.backup_dir = os.path.join(os.path.dirname(path) or ".", "database_backups")

    def _deep_merge(self, base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
        for k, v in overlay.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k] = self._deep_merge(base[k], v)
            else:
                base[k] = v
        return base

    async def load(self) -> None:
        async with self.lock:
            if not await aiofiles.ospath.exists(self.path):
                self.data = json.loads(json.dumps(DEFAULT_JSON))
                self.dirty = True
                await self.flush_locked()
                return
            try:
                self.last_mtime = await aiofiles.ospath.getmtime(self.path)
            except (FileNotFoundError, OSError):
                self.last_mtime = 0.0
            async with aiofiles.open(self.path, "r", encoding="utf-8") as f:
                raw = await f.read()
            try:
                loaded = json.loads(raw) if raw else {}
            except Exception:
                loaded = {}
            self.data = self._deep_merge(json.loads(json.dumps(DEFAULT_JSON)), loaded)
            self.dirty = True

    async def reload_if_changed(self) -> None:
        async with self.lock:
            if not await aiofiles.ospath.exists(self.path):
                return
            try:
                mtime = await aiofiles.ospath.getmtime(self.path)
            except (FileNotFoundError, OSError):
                return
            if mtime <= self.last_mtime:
                return
            async with aiofiles.open(self.path, "r", encoding="utf-8") as f:
                raw = await f.read()
            try:
                loaded = json.loads(raw) if raw else {}
            except Exception:
                loaded = {}
            self.data = self._deep_merge(json.loads(json.dumps(DEFAULT_JSON)), loaded)
            self.last_mtime = mtime

    async def flush(self) -> None:
        async with self.lock:
            await self.flush_locked()

    async def flush_locked(self) -> None:
        if not self.dirty:
            return
        tmp_path = self.path + ".tmp"
        await self._ensure_backup()
        async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(self.data, indent=2))
        await aiofiles.os.replace(tmp_path, self.path)
        try:
            self.last_mtime = await aiofiles.ospath.getmtime(self.path)
        except (FileNotFoundError, OSError):
            self.last_mtime = time.time()
        self.dirty = False

    async def mark_dirty(self) -> None:
        self.dirty = True

    async def _ensure_backup(self) -> None:
        if not await aiofiles.ospath.exists(self.path):
            return
        try:
            await aiofiles.os.makedirs(self.backup_dir)
        except FileExistsError:
            pass
        except OSError:
            pass
        timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        base = os.path.basename(self.path)
        backup_path = os.path.join(self.backup_dir, f"{base}.{timestamp}.bak")
        try:
            async with aiofiles.open(self.path, "rb") as src, aiofiles.open(backup_path, "wb") as dst:
                data = await src.read()
                await dst.write(data)
        except FileNotFoundError:
            return


STORE = JsonStore(config.DB_JSON_PATH)
MENTION_COOLDOWN = CooldownStore(STORE)


def cfg() -> Dict[str, Any]:
    return STORE.data


def ai_cfg() -> Dict[str, Any]:
    return cfg().setdefault("ai", {})
