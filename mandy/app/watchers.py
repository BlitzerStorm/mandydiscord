"""Watcher helpers previously embedded in main.py."""

import asyncio
import random
from typing import Dict, List, Optional, Set

import discord

from mandy.app import state
from mandy.app.core import chunk_lines, now_ts, truncate
from mandy.app.db import db_all, db_exec, db_one
from mandy.app.store import STORE, cfg

MYSQL_WATCHER_CACHE = {"ids": set(), "last_sync": 0}
MYSQL_WATCHER_CACHE_DIRTY = True
MYSQL_WATCHER_CACHE_TTL = 30
MYSQL_WATCHER_CACHE_LOCK: Optional[asyncio.Lock] = None


def mark_mysql_watcher_cache_dirty() -> None:
    global MYSQL_WATCHER_CACHE_DIRTY
    MYSQL_WATCHER_CACHE_DIRTY = True


def mysql_watcher_id_set() -> Set[int]:
    ids = MYSQL_WATCHER_CACHE.get("ids")
    return ids if isinstance(ids, set) else set()


async def mysql_watchers_refresh(force: bool = False) -> None:
    global MYSQL_WATCHER_CACHE_DIRTY, MYSQL_WATCHER_CACHE_LOCK
    if not state.POOL:
        return
    now = now_ts()
    if not force and not MYSQL_WATCHER_CACHE_DIRTY:
        last_sync = int(MYSQL_WATCHER_CACHE.get("last_sync", 0))
        if now - last_sync < MYSQL_WATCHER_CACHE_TTL:
            return
    if MYSQL_WATCHER_CACHE_LOCK is None:
        MYSQL_WATCHER_CACHE_LOCK = asyncio.Lock()
    async with MYSQL_WATCHER_CACHE_LOCK:
        now = now_ts()
        if not force and not MYSQL_WATCHER_CACHE_DIRTY:
            last_sync = int(MYSQL_WATCHER_CACHE.get("last_sync", 0))
            if now - last_sync < MYSQL_WATCHER_CACHE_TTL:
                return
        rows = await db_all("SELECT user_id FROM watchers")
        MYSQL_WATCHER_CACHE["ids"] = {
            int(row["user_id"]) for row in rows if row and row.get("user_id") is not None
        }
        MYSQL_WATCHER_CACHE["last_sync"] = now
        MYSQL_WATCHER_CACHE_DIRTY = False


async def watcher_tick(message: discord.Message):
    if message.author.bot:
        return

    uid = str(message.author.id)

    targets = cfg().get("targets", {})
    if uid in targets:
        t = targets[uid]
        t["current"] = int(t.get("current", 0)) + 1

        if t["current"] >= int(t.get("count", 0)):
            t["current"] = 0
            replies = [x.strip() for x in str(t.get("text", "")).split("|") if x.strip()]
            if replies:
                try:
                    await message.reply(random.choice(replies))
                except Exception:
                    pass
        await STORE.mark_dirty()

    if state.POOL:
        should_check = True
        try:
            await mysql_watchers_refresh()
            should_check = message.author.id in mysql_watcher_id_set()
        except Exception:
            should_check = True
        if should_check:
            row = await db_one("SELECT threshold, current, text FROM watchers WHERE user_id=%s", (message.author.id,))
            if row:
                cur = int(row["current"]) + 1
                thr = int(row["threshold"])
                text = str(row["text"] or "")
                if cur >= thr:
                    cur = 0
                    replies = [x.strip() for x in text.split("|") if x.strip()]
                    if replies:
                        try:
                            await message.reply(random.choice(replies))
                        except Exception:
                            pass
                await db_exec("UPDATE watchers SET current=%s WHERE user_id=%s", (cur, message.author.id))


async def watchers_report(limit: int = 50) -> List[str]:
    def fmt(uid, count, current, text):
        return f"{uid} (<@{uid}>) | count={count} current={current} text={truncate(text, 120)}"

    lim = max(1, min(200, int(limit)))
    chunks: List[str] = []

    json_lines: List[str] = []
    targets = cfg().get("targets", {})
    for uid, data in targets.items():
        json_lines.append(fmt(uid, data.get("count", 0), data.get("current", 0), data.get("text", "")))
    header = f"JSON watchers ({len(json_lines)})"
    chunks.extend(chunk_lines((json_lines[:lim] if json_lines else ["None"]), header))

    if state.POOL:
        mysql_lines: List[str] = []
        try:
            rows = await db_all(
                "SELECT user_id, threshold, current, text FROM watchers ORDER BY updated_at DESC LIMIT %s",
                (lim,),
            )
            for row in rows:
                mysql_lines.append(fmt(row["user_id"], row["threshold"], row.get("current", 0), row.get("text", "")))
        except Exception:
            mysql_lines.append("(failed to read MySQL watchers)")
        header_mysql = f"MySQL watchers ({len(mysql_lines)})"
        chunks.extend(chunk_lines(mysql_lines or ["None"], header_mysql))
    return chunks
