from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import discord

from . import state
from .core import now_ts
from .logging import debug
from .media_ytdl import YTDLSource
from .tasking import spawn_task

MOVIE_ACTIVE_GUILDS: set[int] = set()
MOVIE_STATES: Dict[int, Dict[str, Any]] = {}
MOVIE_STAY_TASKS: Dict[int, asyncio.Task] = {}


def movie_state(guild_id: int) -> Dict[str, Any]:
    state_val = MOVIE_STATES.setdefault(guild_id, {})
    state_val.setdefault("queue", [])
    state_val.setdefault("volume", 1.0)
    state_val.setdefault("stay_until", 0)
    state_val.setdefault("channel_id", 0)
    state_val.setdefault("now_title", "")
    state_val.setdefault("now_url", "")
    return state_val


def cancel_movie_stay_task(guild_id: int) -> None:
    task = MOVIE_STAY_TASKS.pop(guild_id, None)
    if task and not task.done():
        task.cancel()


def schedule_movie_stay_task(guild_id: int) -> None:
    cancel_movie_stay_task(guild_id)
    state_val = MOVIE_STATES.get(guild_id)
    if not state_val:
        return
    stay_until = int(state_val.get("stay_until", 0) or 0)
    if stay_until <= now_ts():
        return

    async def _wait_then_cleanup() -> None:
        await asyncio.sleep(max(0, stay_until - now_ts()))
        await _movie_cleanup(guild_id)

    task = spawn_task(_wait_then_cleanup(), "movie")
    MOVIE_STAY_TASKS[guild_id] = task
    task.add_done_callback(lambda _: MOVIE_STAY_TASKS.pop(guild_id, None))


async def _movie_cleanup(guild_id: int) -> None:
    cancel_movie_stay_task(guild_id)
    state_val = MOVIE_STATES.get(guild_id)
    if state_val:
        state_val["stay_until"] = 0
        state_val["channel_id"] = 0
        state_val["now_title"] = ""
        state_val["now_url"] = ""
        MOVIE_ACTIVE_GUILDS.discard(guild_id)
    guild = state.bot.get_guild(guild_id) if state.bot else None
    if not guild:
        return
    voice_client = discord.utils.get(state.bot.voice_clients, guild=guild) if state.bot else None
    if voice_client and voice_client.is_connected():
        try:
            if voice_client.is_playing():
                voice_client.stop()
            await voice_client.disconnect()
        except Exception:
            pass


async def movie_get_voice_client(guild_id: int) -> Optional[discord.VoiceClient]:
    if not state.bot:
        return None
    guild = state.bot.get_guild(guild_id)
    if not guild:
        return None
    return discord.utils.get(state.bot.voice_clients, guild=guild)


async def movie_start_playback(guild_id: int, url: str, title: str = "") -> bool:
    if not state.bot:
        return False
    voice_client = await movie_get_voice_client(guild_id)
    if not voice_client or not voice_client.is_connected():
        return False
    loop = asyncio.get_running_loop()
    try:
        source = await YTDLSource.from_url(url, loop=loop, stream=True)
    except Exception as exc:
        await debug(f"movie playback failed to load: {exc}")
        return False
    state_val = movie_state(guild_id)
    state_val["now_title"] = title or ""
    state_val["now_url"] = url

    def _after_play(error: Optional[Exception] = None) -> None:
        if error:
            loop.call_soon_threadsafe(lambda: spawn_task(debug(f"movie playback error: {error}"), "movie"))
        loop.call_soon_threadsafe(lambda: spawn_task(movie_handle_track_end(guild_id), "movie"))

    try:
        voice_client.play(source, after=_after_play)
        try:
            vol = float(state_val.get("volume", 1.0) or 1.0)
            source.volume = max(0.0, min(2.0, vol))
        except Exception:
            pass
        return True
    except Exception as exc:
        await debug(f"movie playback failed to start: {exc}")
        return False


async def movie_handle_track_end(guild_id: int) -> None:
    state_val = movie_state(guild_id)
    queue: List[Dict[str, Any]] = state_val.get("queue", [])
    if not queue:
        return
    nxt = queue.pop(0)
    url = str(nxt.get("url") or "")
    title = str(nxt.get("title") or "")
    if not url:
        return
    await movie_start_playback(guild_id, url, title=title)


async def movie_queue_add(guild_id: int, url: str, title: str = "") -> int:
    state_val = movie_state(guild_id)
    queue: List[Dict[str, Any]] = state_val.setdefault("queue", [])
    queue.append({"url": url, "title": title})
    return len(queue)


async def movie_stop(guild_id: int) -> None:
    vc = await movie_get_voice_client(guild_id)
    if vc and vc.is_connected():
        try:
            if vc.is_playing():
                vc.stop()
            await vc.disconnect()
        except Exception:
            pass
    await _movie_cleanup(guild_id)


async def movie_set_volume(guild_id: int, volume: float) -> float:
    state_val = movie_state(guild_id)
    try:
        v = float(volume)
    except Exception:
        v = float(state_val.get("volume", 1.0) or 1.0)
    v = max(0.0, min(2.0, v))
    state_val["volume"] = v
    vc = await movie_get_voice_client(guild_id)
    if vc and vc.source and hasattr(vc.source, "volume"):
        try:
            vc.source.volume = v
        except Exception:
            pass
    return v


async def movie_pause(guild_id: int) -> bool:
    vc = await movie_get_voice_client(guild_id)
    if vc and vc.is_playing():
        try:
            vc.pause()
            return True
        except Exception:
            return False
    return False


async def movie_resume(guild_id: int) -> bool:
    vc = await movie_get_voice_client(guild_id)
    if vc and vc.is_paused():
        try:
            vc.resume()
            return True
        except Exception:
            return False
    return False


async def movie_skip(guild_id: int) -> bool:
    vc = await movie_get_voice_client(guild_id)
    if vc and (vc.is_playing() or vc.is_paused()):
        try:
            vc.stop()
            return True
        except Exception:
            return False
    return False


async def movie_find_voice_targets(user: discord.abc.User) -> Dict[Tuple[int, int], Tuple[discord.Guild, discord.VoiceChannel]]:
    targets: Dict[Tuple[int, int], Tuple[discord.Guild, discord.VoiceChannel]] = {}
    if not state.bot:
        return targets
    for guild in state.bot.guilds:
        try:
            member = guild.get_member(user.id) if isinstance(user, discord.User) else None
            if not member:
                member = guild.get_member(user.id)
            if not member or not getattr(member, "voice", None) or not member.voice.channel:
                continue
            ch = member.voice.channel
            if isinstance(ch, discord.VoiceChannel):
                targets[(guild.id, ch.id)] = (guild, ch)
        except Exception:
            continue
    return targets


async def movie_resolve_target(guild_id: int, channel_id: int) -> Optional[Tuple[discord.Guild, discord.VoiceChannel]]:
    if not state.bot:
        return None
    g = state.bot.get_guild(int(guild_id))
    if not g:
        return None
    ch = g.get_channel(int(channel_id))
    if isinstance(ch, discord.VoiceChannel):
        return g, ch
    return None


async def send_movie_menu(user: discord.abc.User, targets: Dict[Tuple[int, int], Tuple[discord.Guild, discord.VoiceChannel]]):
    from .media_views import MovieControlView

    view = MovieControlView(user.id, targets)
    try:
        await user.send("Movie controls:", view=view)
    except Exception:
        pass


async def _movie_connect(guild: discord.Guild, channel: discord.VoiceChannel) -> Optional[discord.VoiceClient]:
    if not state.bot:
        return None
    voice_client = discord.utils.get(state.bot.voice_clients, guild=guild)
    try:
        if not voice_client:
            voice_client = await channel.connect()
        elif voice_client.channel != channel:
            await voice_client.move_to(channel)
        return voice_client
    except Exception as exc:
        await debug(f"movie connect failed: {exc}")
        return None


async def movie_play_or_queue(guild: discord.Guild, channel: discord.VoiceChannel, url: str, title: str = "") -> bool:
    vc = await _movie_connect(guild, channel)
    if not vc:
        return False
    state_val = movie_state(guild.id)
    state_val["channel_id"] = channel.id
    MOVIE_ACTIVE_GUILDS.add(guild.id)
    if vc.is_playing() or vc.is_paused():
        await movie_queue_add(guild.id, url, title=title)
        return True
    return await movie_start_playback(guild.id, url, title=title)

