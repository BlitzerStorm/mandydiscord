from __future__ import annotations

from typing import Optional

import discord
from discord.ext import commands

from .config import CLEANUP_RESPONSE_TTL


async def safe_delete(msg: discord.Message):
    try:
        await msg.delete()
    except Exception:
        pass


async def say_clean(ctx: commands.Context, content: str):
    await safe_delete(ctx.message)
    return await ctx.send(content)


async def safe_ctx_send(ctx: commands.Context, content: str, delete_after: Optional[float] = None):
    try:
        return await ctx.send(content, delete_after=delete_after)
    except discord.NotFound:
        try:
            return await ctx.author.send(content)
        except Exception:
            return None
    except Exception:
        return None


async def temp_reply(ctx: commands.Context, content: str, ttl: Optional[int] = CLEANUP_RESPONSE_TTL):
    """Send a short-lived reply to keep channels cleaner."""
    return await safe_ctx_send(ctx, content, delete_after=ttl)

