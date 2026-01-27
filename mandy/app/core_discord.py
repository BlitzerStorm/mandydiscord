from __future__ import annotations

from typing import Any, Dict, List, Optional

import discord

from .core_text import truncate
from .core_time import now_ts
from .store import cfg


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


def get_role(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    for r in guild.roles:
        if r.name.lower() == name.lower():
            return r
    return None


def admin_category_name(guild: discord.Guild) -> str:
    return f"{guild.name} Admin"

