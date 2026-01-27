from __future__ import annotations

from typing import Dict, Optional, Tuple

import discord

from . import config
from .core import now_ts
from .media_movie import (
    movie_pause,
    movie_play_or_queue,
    movie_resume,
    movie_set_volume,
    movie_skip,
    movie_state,
    movie_stop,
    movie_resolve_target,
    schedule_movie_stay_task,
)


class MovieTargetSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="Select a voice channel", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not view or not isinstance(view, MovieControlView):
            return
        view.selected = self.values[0] if self.values else None
        await interaction.response.send_message("Selected.", ephemeral=True)


class MovieLinkModal(discord.ui.Modal, title="Movie Link"):
    link = discord.ui.TextInput(label="YouTube URL", placeholder="https://youtu.be/...", required=True, max_length=240)

    def __init__(self, guild_id: int, channel_id: int, mode: str):
        super().__init__()
        self.guild_id = int(guild_id)
        self.channel_id = int(channel_id)
        self.mode = str(mode or "play")

    async def on_submit(self, interaction: discord.Interaction):
        url = str(self.link.value or "").strip()
        target = await movie_resolve_target(self.guild_id, self.channel_id)
        if not target:
            return await interaction.response.send_message("Target not found.", ephemeral=True)
        guild, channel = target
        ok = await movie_play_or_queue(guild, channel, url, title="")
        if ok:
            await interaction.response.send_message("Queued/playing.", ephemeral=True)
        else:
            await interaction.response.send_message("Failed.", ephemeral=True)


class MovieVolumeModal(discord.ui.Modal, title="Movie Volume"):
    volume = discord.ui.TextInput(label="Volume (0.0 - 2.0)", placeholder="1.0", required=True, max_length=8)

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = int(guild_id)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            v = float(str(self.volume.value).strip())
        except Exception:
            v = 1.0
        v = await movie_set_volume(self.guild_id, v)
        await interaction.response.send_message(f"Volume set to {v:.2f}", ephemeral=True)


class MovieStayModal(discord.ui.Modal, title="Movie Stay"):
    minutes = discord.ui.TextInput(label="Minutes to stay", placeholder="15", required=True, max_length=4)

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = int(guild_id)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            mins = int(str(self.minutes.value).strip())
        except Exception:
            mins = config.MOVIE_STAY_DEFAULT_MINUTES
        mins = max(0, min(config.MOVIE_STAY_MAX_MINUTES, mins))
        state_val = movie_state(self.guild_id)
        state_val["stay_until"] = now_ts() + mins * 60
        schedule_movie_stay_task(self.guild_id)
        await interaction.response.send_message(f"Stay set for {mins} minutes.", ephemeral=True)


class MovieControlView(discord.ui.View):
    def __init__(self, user_id: int, targets: Dict[Tuple[int, int], Tuple[discord.Guild, discord.VoiceChannel]]):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.targets = targets
        self.selected: Optional[str] = None
        options = []
        for (gid, cid), (g, ch) in targets.items():
            options.append(discord.SelectOption(label=f"{g.name} / {ch.name}", value=f"{gid}:{cid}"))
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

