from __future__ import annotations

import asyncio
import datetime
import json
import os
import time
from typing import Any, Dict

import aiofiles
import aiofiles.os
import aiofiles.ospath

from .store_defaults import new_default_json


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
                self.data = new_default_json()
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
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = os.path.basename(self.path)
        backup_path = os.path.join(self.backup_dir, f"{base}.{timestamp}.bak")
        try:
            async with aiofiles.open(self.path, "rb") as src, aiofiles.open(backup_path, "wb") as dst:
                data = await src.read()
                await dst.write(data)
        except FileNotFoundError:
            return
