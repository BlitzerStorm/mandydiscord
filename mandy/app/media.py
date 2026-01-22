import asyncio
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord.ext import commands
import yt_dlp

from . import config, state
from .core import now_ts
from .logging import debug
from .tasking import spawn_task

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "extract_flat": False,
    "quiet": True,
    "no_warnings": True,
    "ignoreerrors": True,
    "default_search": "auto",
}
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

SPECIAL_VOICE_LEAVE_TASKS: Dict[int, asyncio.Task] = {}
MOVIE_ACTIVE_GUILDS: set[int] = set()
MOVIE_STATES: Dict[int, Dict[str, Any]] = {}
MOVIE_STAY_TASKS: Dict[int, asyncio.Task] = {}


class YTDLSource(discord.PCMVolumeTransformer):
    ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

    def __init__(self, source: discord.AudioSource, *, data: Dict[str, Any]):
        super().__init__(source)
        self.data = data
        self.url = data.get("url")

    @classmethod
    async def from_url(cls, url: str, *, loop: Optional[asyncio.AbstractEventLoop] = None, stream: bool = True):
        if loop is None:
            loop = asyncio.get_running_loop()

        data = await loop.run_in_executor(None, lambda: cls.ytdl.extract_info(url, download=not stream))
        if not data:
            raise ValueError("Unable to retrieve voice media metadata.")
        if "entries" in data:
            data = data["entries"][0]
        if not data:
            raise ValueError("Unable to resolve a playable voice entry.")

        source = discord.FFmpegPCMAudio(data["url"], **FFMPEG_OPTIONS)
        return cls(source, data=data)


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
        state_val["queue"] = []
        state_val["now_title"] = ""
        state_val["now_url"] = ""
        state_val["channel_id"] = 0
    guild = state.bot.get_guild(guild_id) if state.bot else None
    voice_client = discord.utils.get(state.bot.voice_clients, guild=guild) if state.bot else None
    if voice_client and voice_client.is_connected():
        try:
            await voice_client.disconnect()
        except Exception:
            pass
    MOVIE_ACTIVE_GUILDS.discard(guild_id)


async def movie_get_voice_client(
    guild: discord.Guild, channel: discord.VoiceChannel
) -> Optional[discord.VoiceClient]:
    if not state.bot:
        return None
    voice_client = discord.utils.get(state.bot.voice_clients, guild=guild)
    if not voice_client:
        voice_client = await channel.connect()
    elif voice_client.channel != channel:
        await voice_client.move_to(channel)
    return voice_client


async def movie_start_playback(
    guild: discord.Guild, channel: discord.VoiceChannel, url: str, clear_queue: bool = False
) -> Tuple[bool, str]:
    state_val = movie_state(guild.id)
    if clear_queue:
        state_val["queue"] = []
    voice_client = await movie_get_voice_client(guild, channel)
    if not voice_client:
        return False, "Unable to connect to voice."

    try:
        loop = asyncio.get_running_loop()
        source = await YTDLSource.from_url(url, loop=loop, stream=True)
    except Exception as exc:
        return False, f"Could not load media: {exc}"

    if voice_client.is_playing():
        voice_client.stop()

    state_val["now_title"] = str(source.data.get("title") or "Unknown")
    state_val["now_url"] = str(url)
    state_val["channel_id"] = int(channel.id)

    def _after_play(error: Optional[Exception] = None) -> None:
        if error:
            loop.call_soon_threadsafe(lambda: spawn_task(debug(f"movie playback failed: {error}"), "movie"))
        loop.call_soon_threadsafe(lambda: spawn_task(movie_handle_track_end(guild.id), "movie"))

    voice_client.play(source, after=_after_play)
    MOVIE_ACTIVE_GUILDS.add(guild.id)
    return True, f"Playing: {state_val['now_title']}"


async def movie_handle_track_end(guild_id: int) -> None:
    state_val = MOVIE_STATES.get(guild_id)
    if not state_val:
        await _movie_cleanup(guild_id)
        return
    queue = list(state_val.get("queue", []))
    if queue:
        next_url = queue.pop(0)
        state_val["queue"] = queue
        guild = state.bot.get_guild(guild_id) if state.bot else None
        if not guild:
            await _movie_cleanup(guild_id)
            return
        channel_id = int(state_val.get("channel_id", 0))
        channel = guild.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.VoiceChannel):
            await movie_start_playback(guild, channel, next_url)
            return
    await _movie_cleanup(guild_id)


async def movie_queue_add(guild: discord.Guild, channel: discord.VoiceChannel, url: str) -> Tuple[bool, str]:
    state_val = movie_state(guild.id)
    queue = list(state_val.get("queue", []))
    if len(queue) >= config.MOVIE_QUEUE_LIMIT:
        return False, f"Queue full (limit {config.MOVIE_QUEUE_LIMIT})."
    queue.append(url)
    state_val["queue"] = queue
    if guild.id not in MOVIE_ACTIVE_GUILDS:
        await movie_start_playback(guild, channel, url)
    return True, "Queued."


async def movie_stop(guild_id: int) -> None:
    cancel_movie_stay_task(guild_id)
    guild = state.bot.get_guild(guild_id) if state.bot else None
    voice_client = discord.utils.get(state.bot.voice_clients, guild=guild) if state.bot else None
    if voice_client and voice_client.is_connected():
        if voice_client.is_playing():
            voice_client.stop()
        try:
            await voice_client.disconnect()
        except Exception:
            pass
    await _movie_cleanup(guild_id)


async def movie_set_volume(guild_id: int, volume: float) -> None:
    state_val = movie_state(guild_id)
    state_val["volume"] = float(volume)
    guild = state.bot.get_guild(guild_id) if state.bot else None
    voice_client = discord.utils.get(state.bot.voice_clients, guild=guild) if state.bot else None
    if voice_client and voice_client.source:
        voice_client.source.volume = float(volume)


async def movie_pause(guild_id: int) -> bool:
    guild = state.bot.get_guild(guild_id) if state.bot else None
    voice_client = discord.utils.get(state.bot.voice_clients, guild=guild) if state.bot else None
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        return True
    return False


async def movie_resume(guild_id: int) -> bool:
    guild = state.bot.get_guild(guild_id) if state.bot else None
    voice_client = discord.utils.get(state.bot.voice_clients, guild=guild) if state.bot else None
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        return True
    return False


async def movie_skip(guild_id: int) -> bool:
    guild = state.bot.get_guild(guild_id) if state.bot else None
    voice_client = discord.utils.get(state.bot.voice_clients, guild=guild) if state.bot else None
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        return True
    return False


async def movie_find_voice_targets(user_id: int) -> List[Tuple[discord.Guild, discord.VoiceChannel]]:
    targets = []
    if not state.bot:
        return targets
    for guild in state.bot.guilds:
        member = guild.get_member(user_id)
        if not member or not member.voice or not member.voice.channel:
            continue
        if isinstance(member.voice.channel, discord.VoiceChannel):
            targets.append((guild, member.voice.channel))
    return targets


async def movie_resolve_target(
    ctx: commands.Context,
) -> Tuple[Optional[discord.Guild], Optional[discord.VoiceChannel], Optional[str]]:
    targets = await movie_find_voice_targets(ctx.author.id)
    if not targets:
        return None, None, "Join a voice channel first."
    if len(targets) == 1:
        return targets[0][0], targets[0][1], None
    return None, None, "Multiple voice targets. Use the menu."


async def send_movie_menu(ctx: commands.Context) -> None:
    targets = await movie_find_voice_targets(ctx.author.id)
    if not targets:
        await ctx.send("Join a voice channel first.", delete_after=8)
        return
    target_map = {(g.id, ch.id): (g, ch) for g, ch in targets}
    view = MovieControlView(ctx.author.id, target_map)
    await ctx.send("Movie controls:", view=view)


class MovieTargetSelect(discord.ui.Select):
    def __init__(self, options: List[discord.SelectOption]):
        super().__init__(options=options, placeholder="Select a target", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user:
            return
        view = self.view
        if isinstance(view, MovieControlView):
            view.selected = self.values[0] if self.values else None
        await interaction.response.defer()


class MovieLinkModal(discord.ui.Modal):
    def __init__(self, guild_id: int, channel_id: int, mode: str):
        super().__init__(title=f"Movie {mode.title()}", timeout=300)
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.mode = mode
        self.link = discord.ui.TextInput(
            label="URL",
            style=discord.TextStyle.short,
            required=True,
            max_length=400,
            placeholder="https://...",
        )
        self.add_item(self.link)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user:
            return
        url = str(self.link.value or "").strip()
        if not url:
            return await interaction.response.send_message("Missing URL.", ephemeral=True)
        guild = state.bot.get_guild(self.guild_id) if state.bot else None
        channel = guild.get_channel(self.channel_id) if guild else None
        if not isinstance(channel, discord.VoiceChannel):
            return await interaction.response.send_message("Voice channel missing.", ephemeral=True)
        if self.mode == "queue":
            ok, msg = await movie_queue_add(guild, channel, url)
        else:
            ok, msg = await movie_start_playback(guild, channel, url, clear_queue=True)
        await interaction.response.send_message(msg, ephemeral=True)


class MovieVolumeModal(discord.ui.Modal):
    def __init__(self, guild_id: int):
        super().__init__(title="Movie Volume", timeout=300)
        self.guild_id = guild_id
        self.value = discord.ui.TextInput(
            label="Volume (0-100)",
            style=discord.TextStyle.short,
            required=True,
            max_length=4,
            placeholder="100",
        )
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user:
            return
        try:
            value = max(0, min(100, int(self.value.value)))
        except Exception:
            return await interaction.response.send_message("Enter a number 0-100.", ephemeral=True)
        await movie_set_volume(self.guild_id, value / 100.0)
        await interaction.response.send_message("Volume updated.", ephemeral=True)


class MovieStayModal(discord.ui.Modal):
    def __init__(self, guild_id: int):
        super().__init__(title="Movie Stay", timeout=300)
        self.guild_id = guild_id
        self.value = discord.ui.TextInput(
            label="Stay minutes (1-60)",
            style=discord.TextStyle.short,
            required=True,
            max_length=3,
            placeholder=str(config.MOVIE_STAY_DEFAULT_MINUTES),
        )
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user:
            return
        try:
            mins = max(1, min(config.MOVIE_STAY_MAX_MINUTES, int(self.value.value)))
        except Exception:
            return await interaction.response.send_message("Enter a number.", ephemeral=True)
        state_val = movie_state(self.guild_id)
        state_val["stay_until"] = now_ts() + int(mins * 60)
        schedule_movie_stay_task(self.guild_id)
        await interaction.response.send_message("Stay timer updated.", ephemeral=True)


class MovieControlView(discord.ui.View):
    def __init__(self, user_id: int, targets: Dict[Tuple[int, int], Tuple[discord.Guild, discord.VoiceChannel]]):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.targets = targets
        self.selected: Optional[str] = None
        options = []
        for (gid, cid), (g, ch) in targets.items():
            options.append(
                discord.SelectOption(label=f"{g.name} / {ch.name}", value=f"{gid}:{cid}")
            )
        if options:
            self.add_item(MovieTargetSelect(options))

    def get_selected_target(self) -> Optional[Tuple[int, int, str]]:
        if not self.selected:
            return None
        try:
            gid, cid = self.selected.split(":", 1)
            return int(gid), int(cid), self.selected
        except Exception:
            return None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user and interaction.user.id == self.user_id

    @discord.ui.button(label="Play", style=discord.ButtonStyle.green, row=1)
    async def play_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, channel_id, _ = target
        await interaction.response.send_modal(MovieLinkModal(guild_id, channel_id, "play"))

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.blurple, row=1)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, channel_id, _ = target
        await interaction.response.send_modal(MovieLinkModal(guild_id, channel_id, "queue"))

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.red, row=1)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, _, _ = target
        skipped = await movie_skip(guild_id)
        msg = "Skipped." if skipped else "Nothing playing."
        await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="Volume", style=discord.ButtonStyle.gray, row=1)
    async def volume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, _, _ = target
        await interaction.response.send_modal(MovieVolumeModal(guild_id))

    @discord.ui.button(label="Stay", style=discord.ButtonStyle.gray, row=1)
    async def stay_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, _, _ = target
        await interaction.response.send_modal(MovieStayModal(guild_id))

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.red, row=2)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, _, _ = target
        await movie_stop(guild_id)
        await interaction.response.send_message("Stopped.", ephemeral=True)

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.gray, row=2)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, _, _ = target
        paused = await movie_pause(guild_id)
        msg = "Paused." if paused else "Nothing playing."
        await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="Resume", style=discord.ButtonStyle.gray, row=2)
    async def resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, _, _ = target
        resumed = await movie_resume(guild_id)
        msg = "Resumed." if resumed else "Nothing paused."
        await interaction.response.send_message(msg, ephemeral=True)
