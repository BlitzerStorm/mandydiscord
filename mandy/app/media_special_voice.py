from __future__ import annotations

import asyncio
from typing import Dict, Optional

import discord

from . import config, state
from .logging import debug
from .media_ytdl import YTDLSource
from .tasking import spawn_task

SPECIAL_VOICE_LEAVE_TASKS: Dict[int, asyncio.Task] = {}


async def start_special_user_voice(channel: discord.VoiceChannel) -> None:
    try:
        guild = channel.guild
        voice_client = discord.utils.get(state.bot.voice_clients, guild=guild) if state.bot else None
        if not voice_client:
            voice_client = await channel.connect()
        elif voice_client.channel != channel:
            await voice_client.move_to(channel)

        if voice_client.is_playing():
            voice_client.stop()

        loop = asyncio.get_running_loop()
        source = await YTDLSource.from_url(config.SPECIAL_VOICE_URL, loop=loop, stream=True)

        def _after_play(error: Optional[Exception] = None) -> None:
            if error:
                loop.call_soon_threadsafe(
                    lambda: spawn_task(debug(f"special voice playback failed: {error}"), "voice")
                )

        voice_client.play(source, after=_after_play)
    except Exception as exc:
        await debug(f"special voice setup failed: {exc}")


async def _special_voice_leave_flow(guild: discord.Guild) -> None:
    try:
        await asyncio.sleep(config.VOICE_QUIT_DELAY_SECONDS)
        voice_client = discord.utils.get(state.bot.voice_clients, guild=guild) if state.bot else None
        if voice_client and voice_client.is_connected():
            if voice_client.is_playing():
                voice_client.stop()
            await voice_client.disconnect()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        await debug(f"special voice tear-down failed: {exc}")


def cancel_special_voice_leave_task(guild_id: int) -> None:
    task = SPECIAL_VOICE_LEAVE_TASKS.pop(guild_id, None)
    if task and not task.done():
        task.cancel()


def schedule_special_voice_leave(guild: discord.Guild) -> None:
    cancel_special_voice_leave_task(guild.id)
    task = spawn_task(_special_voice_leave_flow(guild), "voice")
    SPECIAL_VOICE_LEAVE_TASKS[guild.id] = task
    task.add_done_callback(lambda _: SPECIAL_VOICE_LEAVE_TASKS.pop(guild.id, None))

