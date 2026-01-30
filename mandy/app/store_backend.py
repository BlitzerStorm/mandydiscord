from __future__ import annotations

import asyncio
import datetime
import json
import os
import time
from typing import Any, Dict, Iterable

import aiofiles
import aiofiles.os
import aiofiles.ospath

from . import config
from .store_defaults import new_default_json


LEGACY_LAYOUT_CATEGORIES = {
    "Welcome & Information",
    "Bot Control & Monitoring",
    "Research & Development",
    "Guest Access",
    "Engineering Core",
    "Admin Backrooms",
    "DM Bridges",
}
LEGACY_COMMAND_CHANNELS = {"command-requests", "command_requests"}

def _backup_interval_seconds() -> int:
    env = os.getenv("MANDY_DB_BACKUP_INTERVAL_SECONDS") or os.getenv("DB_BACKUP_INTERVAL_SECONDS") or ""
    if env:
        try:
            val = int(float(env))
        except Exception:
            val = 3600
    else:
        val = 3600
    if val < 0:
        return 0
    return max(0, min(7 * 24 * 3600, val))


def _has_required_channels(cats: Dict[str, Any], required: Dict[str, Iterable[str]]) -> bool:
    for cat, channels in required.items():
        existing = cats.get(cat)
        if not isinstance(existing, list):
            return False
        for ch in channels:
            if ch not in existing:
                return False
    return True


def _layout_needs_reset(layout: Any, defaults: Dict[str, Any]) -> bool:
    if not isinstance(layout, dict):
        return True
    cats = layout.get("categories")
    if not isinstance(cats, dict) or not cats:
        return True
    cat_names = {str(k) for k in cats.keys()}
    if cat_names & LEGACY_LAYOUT_CATEGORIES:
        return True
    required = defaults.get("layout", {}).get("categories", {})
    if not isinstance(required, dict) or not required:
        return False
    if not set(required.keys()).issubset(cat_names):
        return True
    if not _has_required_channels(cats, required):
        return True
    return False


def _normalize_loaded_data(data: Dict[str, Any]) -> bool:
    defaults = new_default_json()
    changed = False

    if data.get("admin_guild_id") != config.ADMIN_GUILD_ID:
        data["admin_guild_id"] = config.ADMIN_GUILD_ID
        changed = True
    if data.get("ADMIN_GUILD_ID") != config.ADMIN_GUILD_ID:
        data["ADMIN_GUILD_ID"] = config.ADMIN_GUILD_ID
        changed = True

    gate = data.get("gate_layout")
    if not isinstance(gate, dict):
        data["gate_layout"] = defaults.get("gate_layout", {})
        changed = True
    else:
        for key, value in (defaults.get("gate_layout") or {}).items():
            if not gate.get(key):
                gate[key] = value
                changed = True

    soc = data.get("soc_access")
    if not isinstance(soc, dict):
        data["soc_access"] = defaults.get("soc_access", {})
        changed = True
    else:
        if "sync_interval_minutes" not in soc:
            soc["sync_interval_minutes"] = defaults["soc_access"]["sync_interval_minutes"]
            changed = True
        if "initial_delay_seconds" not in soc:
            soc["initial_delay_seconds"] = defaults["soc_access"]["initial_delay_seconds"]
            changed = True
        sections = soc.get("sections")
        if not isinstance(sections, dict) or not sections:
            soc["sections"] = defaults["soc_access"]["sections"]
            changed = True
        if not isinstance(soc.get("users"), dict):
            soc["users"] = {}
            changed = True

    cmd = data.get("command_channels")
    if not isinstance(cmd, dict):
        data["command_channels"] = defaults.get("command_channels", {})
        changed = True
    else:
        user = str(cmd.get("user") or "").strip()
        if user in LEGACY_COMMAND_CHANNELS:
            cmd["user"] = defaults["command_channels"]["user"]
            changed = True
        if not cmd.get("user"):
            cmd["user"] = defaults["command_channels"]["user"]
            changed = True
        if not cmd.get("god"):
            cmd["god"] = defaults["command_channels"]["god"]
            changed = True
        if "mode" not in cmd:
            cmd["mode"] = defaults["command_channels"]["mode"]
            changed = True

    if _layout_needs_reset(data.get("layout"), defaults):
        data["layout"] = defaults.get("layout", {})
        data["channel_topics"] = defaults.get("channel_topics", {})
        data["pinned_text"] = defaults.get("pinned_text", {})
        changed = True

    return changed


class JsonStore:
    def __init__(self, path: str):
        self.path = path
        self.lock = asyncio.Lock()
        self.data: Dict[str, Any] = {}
        self.dirty = False
        self.last_mtime = 0.0
        self.backup_dir = os.path.join(os.path.dirname(path) or ".", "database_backups")
        self.backup_interval_seconds = _backup_interval_seconds()
        self.last_backup_ts = 0.0

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
                self.data = new_default_json()
                _normalize_loaded_data(self.data)
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
            self.data = self._deep_merge(new_default_json(), loaded)
            _normalize_loaded_data(self.data)
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
            self.data = self._deep_merge(new_default_json(), loaded)
            if _normalize_loaded_data(self.data):
                self.dirty = True
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
        if self.backup_interval_seconds <= 0:
            return
        now = time.time()
        if self.last_backup_ts and now - self.last_backup_ts < self.backup_interval_seconds:
            return
        try:
            await aiofiles.os.makedirs(self.backup_dir)
        except FileExistsError:
            pass
        except OSError:
            pass
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = os.path.basename(self.path)
        backup_path = os.path.join(self.backup_dir, f"{base}.{timestamp}.bak")
        try:
            async with aiofiles.open(self.path, "rb") as src, aiofiles.open(backup_path, "wb") as dst:
                data = await src.read()
                await dst.write(data)
            self.last_backup_ts = now
        except FileNotFoundError:
            return
