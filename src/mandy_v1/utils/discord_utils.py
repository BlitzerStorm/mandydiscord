from __future__ import annotations

import discord


async def get_bot_member(bot: discord.Client, guild: discord.Guild) -> discord.Member | None:
    """
    Resolve the bot's Member object for a guild.

    `guild.me` can be None depending on cache state/intents; this helper tries cache
    and then falls back to an API fetch.
    """

    me = guild.me
    if me is not None:
        return me
    if bot.user is None:
        return None
    cached = guild.get_member(bot.user.id)
    if cached is not None:
        return cached
    try:
        return await guild.fetch_member(bot.user.id)
    except (discord.Forbidden, discord.HTTPException):
        return None

