import datetime
import json
from typing import Dict, Optional

import discord

from . import config, state
from .core import now_ts
from .store import cfg

LOG_SUBSYSTEMS = {
    "system": "SYNAPTIC",
    "audit": "IMMUNE",
    "mirror": "SENSORY",
    "ai": "AI",
    "voice": "VOICE",
    "debug": "SYNAPTIC",
}
LOG_SEVERITY_DEFAULTS = {
    "system": "INFO",
    "audit": "INFO",
    "mirror": "INFO",
    "ai": "INFO",
    "voice": "INFO",
    "debug": "DEBUG",
}
LOG_DEDUP_WINDOW = 60
_LOG_DEDUP: Dict[str, float] = {}


def _log_now() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _clean_log_message(text: str) -> str:
    cleaned = " ".join(str(text or "").replace("**", "").replace("`", "").split())
    return cleaned


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _format_log_line(
    which: str,
    text: str,
    subsystem: Optional[str] = None,
    severity: Optional[str] = None,
    details: Optional[dict] = None,
) -> str:
    msg = _clean_log_message(text)
    sub = subsystem or LOG_SUBSYSTEMS.get(which, "SYSTEM")
    sev = severity or LOG_SEVERITY_DEFAULTS.get(which, "INFO")
    detail_text = ""
    if details:
        try:
            detail_text = " | " + _truncate_text(json.dumps(details, ensure_ascii=True), 240)
        except Exception:
            detail_text = ""
    return f"{_log_now()} | {sub} | {sev} | {_truncate_text(msg, 460)}{detail_text}"


def _should_emit_log(key: str, now_ts_val: int) -> bool:
    last = _LOG_DEDUP.get(key)
    if last and now_ts_val - last < LOG_DEDUP_WINDOW:
        return False
    _LOG_DEDUP[key] = now_ts_val
    return True


async def _resolve_log_channel(which: str) -> Optional[discord.TextChannel]:
    logs = cfg().get("logs", {})
    ch_id = logs.get(which)
    fallback_id = logs.get("system") or logs.get("debug")
    try_ids = [ch_id, fallback_id] if ch_id else [fallback_id]
    for target_id in try_ids:
        if not target_id:
            continue
        if state.bot is None:
            return None
        ch = state.bot.get_channel(int(target_id))
        if ch:
            return ch
        try:
            ch = await state.bot.fetch_channel(int(target_id))
            if ch:
                return ch
        except Exception:
            continue
    return None


async def log_to(
    which: str,
    text: str,
    subsystem: Optional[str] = None,
    severity: Optional[str] = None,
    details: Optional[dict] = None,
):
    line = _format_log_line(which, text, subsystem=subsystem, severity=severity, details=details)
    key_parts = [
        which,
        subsystem or LOG_SUBSYSTEMS.get(which, "SYSTEM"),
        severity or LOG_SEVERITY_DEFAULTS.get(which, "INFO"),
        _clean_log_message(text),
        json.dumps(details, ensure_ascii=True) if details else "",
    ]
    dedup_key = "|".join(key_parts)
    if not _should_emit_log(dedup_key, now_ts()):
        return
    ch = await _resolve_log_channel(which)
    if not ch:
        print(line)
        return
    try:
        await ch.send(line[:1900])
    except Exception:
        print(line)


async def audit(actor_id: int, action: str, meta: Optional[dict] = None):
    if state.POOL:
        try:
            from .db import db_exec

            await db_exec(
                "INSERT INTO audit_logs (actor_id, action, meta) VALUES (%s,%s,%s)",
                (actor_id, action, json.dumps(meta or {}, ensure_ascii=False)),
            )
        except Exception:
            pass
    await log_to("audit", action, subsystem="IMMUNE", severity="INFO", details=meta)


async def debug(text: str):
    await log_to("debug", text, subsystem="SYNAPTIC", severity="DEBUG")


async def ensure_debug_channel() -> Optional[discord.TextChannel]:
    if state.bot is None:
        return None
    admin = state.bot.get_guild(config.ADMIN_GUILD_ID)
    if not admin:
        return None
    ch = discord.utils.get(admin.text_channels, name="debug-logs")
    if ch:
        return ch
    ch = discord.utils.get(admin.text_channels, name="debug")
    if ch:
        return ch
    try:
        cat = discord.utils.get(admin.categories, name="Engineering Core")
        if not cat:
            cat = await admin.create_category("Engineering Core")
            from .setup import setup_pause

            await setup_pause()
        ch = await admin.create_text_channel("debug-logs", category=cat)
        from .setup import setup_pause

        await setup_pause()
        return ch
    except Exception:
        try:
            ch = await admin.create_text_channel("debug-logs")
            from .setup import setup_pause

            await setup_pause()
            return ch
        except Exception:
            return None


async def setup_log(text: str):
    await log_to("system", text, subsystem="SYNAPTIC", severity="INFO")
    await log_to("debug", text, subsystem="SYNAPTIC", severity="DEBUG")
    ch = await ensure_debug_channel()
    if ch:
        try:
            await ch.send(text[:1900])
        except Exception:
            pass
