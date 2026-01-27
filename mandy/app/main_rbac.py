from __future__ import annotations

from typing import Dict, Optional

import discord
from discord.ext import commands

from . import config, state
from .db import db_one
from .store import cfg


def is_super(uid: int) -> bool:
    return uid == config.SUPER_USER_ID


async def get_user_level(uid: int) -> int:
    if uid == config.SUPER_USER_ID:
        return 100
    if uid == config.AUTO_GOD_ID:
        return 90

    if state.POOL:
        row = await db_one("SELECT level FROM users_permissions WHERE user_id=%s", (uid,))
        if row:
            return int(row["level"])

    return int(cfg().get("permissions", {}).get(str(uid), 0))


def role_level_map() -> Dict[str, int]:
    return cfg().get("rbac", {}).get("role_levels", {}) or {}


async def effective_level(member: discord.abc.User) -> int:
    lvl = await get_user_level(member.id)
    if isinstance(member, discord.Member):
        mp = role_level_map()
        max_role = 0
        for r in member.roles:
            max_role = max(max_role, int(mp.get(r.name, 0)))
        lvl = max(lvl, max_role)
    return lvl


def mandy_power_mode_enabled(_: Optional[discord.abc.User] = None) -> bool:
    mandy = cfg().get("mandy", {}) or {}
    return bool(mandy.get("power_mode", False))


async def require_level_ctx(ctx: commands.Context, min_level: int) -> bool:
    lvl = await effective_level(ctx.author)
    if lvl >= min_level:
        return True
    try:
        await ctx.message.delete()
    except Exception:
        pass
    return False

