import datetime
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import discord

from .store import STORE, cfg


def chunk_lines(lines: list, header: str, limit: int = 1900) -> list:
    """Chunk text with a header repeated per chunk."""
    chunks = []
    cur = header
    for line in lines:
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = header
        cur += "\n" + line
    if cur:
        chunks.append(cur)
    return chunks


def now_ts() -> int:
    return int(time.time())


def fmt_ts(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts).isoformat()


def truncate(text: str, limit: int = 180) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def memory_state() -> Dict[str, Any]:
    return cfg().setdefault("memory", {}).setdefault("events", [])


async def memory_add(kind: str, text: str, meta: Optional[Dict[str, Any]] = None):
    events = cfg().setdefault("memory", {}).setdefault("events", [])
    events.append(
        {
            "ts": now_ts(),
            "kind": kind,
            "text": truncate(text, 500),
            "meta": meta or {},
        }
    )
    if len(events) > 200:
        del events[:-200]
    await STORE.mark_dirty()


def memory_recent(limit: int = 10) -> List[Dict[str, Any]]:
    events = list(cfg().setdefault("memory", {}).get("events", []))
    return list(events[-limit:])


def ark_snapshots() -> Dict[str, Any]:
    return cfg().setdefault("ark_snapshots", {})


def phoenix_keys() -> Dict[str, str]:
    return cfg().setdefault("phoenix_keys", {})


async def request_elevation(action: str, reason: str, meta: Optional[Dict[str, Any]] = None):
    """Ask SUPERUSER for help when Mandy lacks permissions."""
    from . import config
    from .logging import audit
    from .state import bot

    await audit(config.SUPER_USER_ID, f"Assist requested: {action}", {"reason": reason, **(meta or {})})
    try:
        if bot is None:
            return
        owner = await bot.fetch_user(config.SUPER_USER_ID)
        msg = f"Assist requested for `{action}`: {reason}"
        if meta:
            msg += f"\nmeta: {meta}"
        await owner.send(msg)
    except Exception:
        pass


def classify_mood(text: str) -> str:
    lower = (text or "").lower()
    negative = ("angry", "mad", "hate", "annoyed", "wtf", "stupid", "dumb", "trash")
    positive = ("love", "awesome", "great", "thanks", "thank you", "nice", "cool")
    if any(w in lower for w in positive):
        return "positive"
    if any(w in lower for w in negative):
        return "negative"
    return "neutral"


def bot_missing_permissions(guild: discord.Guild) -> List[str]:
    m = guild.me
    if not m:
        return ["unknown"]
    perms = m.guild_permissions
    missing = []
    for name in (
        "view_channel",
        "read_message_history",
        "send_messages",
        "manage_channels",
        "manage_roles",
        "manage_messages",
    ):
        if not getattr(perms, name, False):
            missing.append(name)
    return missing


async def send_owner_server_report(guild: discord.Guild, reason: str = ""):
    if not guild:
        return
    from .state import bot

    owner = guild.owner
    if not owner and guild.owner_id:
        try:
            if bot is None:
                return
            owner = await bot.fetch_user(guild.owner_id)
        except Exception:
            owner = None
    if not owner:
        return

    members = list(guild.members or [])
    member_names = [m.display_name for m in members][:25]
    targets = cfg().get("targets", {})
    watcher_hits = [uid for uid in targets.keys() if guild.get_member(int(uid))]
    missing = bot_missing_permissions(guild)

    lines = [
        f"Server report ({reason}): {guild.name} ({guild.id})",
        f"Members: {len(members)}",
        "Sample members: " + (", ".join(member_names) if member_names else "none"),
        f"Watchers in this server: {len(watcher_hits)}",
    ]
    if watcher_hits:
        lines.append("Watcher IDs: " + ", ".join(watcher_hits[:15]))
    if missing and missing != ["unknown"]:
        lines.append("Missing permissions: " + ", ".join(missing))
    else:
        lines.append("Missing permissions: none")
    try:
        await owner.send("\n".join(lines))
    except Exception:
        pass


def serialize_overwrites(channel: discord.abc.GuildChannel) -> Dict[str, Any]:
    ow = {}
    try:
        for target, perms in channel.overwrites.items():
            key = f"role:{target.id}" if isinstance(target, discord.Role) else f"user:{target.id}"
            ow[key] = {
                "allow": perms.value,
                "deny": perms._from_pair()[1].value
                if hasattr(perms, "_from_pair")
                else perms.value ^ (~perms).value,
            }
    except Exception:
        pass
    return ow


def deserialize_overwrites(
    guild: discord.Guild, data: Dict[str, Any]
) -> Dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    result: Dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {}
    for key, perms in (data or {}).items():
        try:
            kind, sid = key.split(":", 1)
            sid = int(sid)
            target = guild.get_role(sid) if kind == "role" else guild.get_member(sid)
            if not target:
                continue
            allow_val = int(perms.get("allow", 0))
            deny_val = int(perms.get("deny", 0))
            overw = discord.Permissions(allow_val).pair(discord.Permissions(deny_val))
            result[target] = overw
        except Exception:
            continue
    return result


def strip_bot_mentions(text: str, bot_id: int) -> str:
    if not text or not bot_id:
        return ""
    cleaned = re.sub(rf"<@!?{bot_id}>", "", text)
    return " ".join(cleaned.split())


def is_youtube_url(url: str) -> bool:
    if not url:
        return False
    return "youtube.com" in url or "youtu.be" in url


def normalize_youtube_url(url: str) -> str:
    if not url:
        return ""
    return url.split("&")[0].strip()


def get_role(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    for r in guild.roles:
        if r.name.lower() == name.lower():
            return r
    return None


def admin_category_name(guild: discord.Guild) -> str:
    return f"{guild.name} Admin"
