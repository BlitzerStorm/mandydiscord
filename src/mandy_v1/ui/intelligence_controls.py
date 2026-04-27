from __future__ import annotations

import discord


class AutonomyProposalReviewView(discord.ui.View):
    def __init__(self, bot: discord.Client, proposal_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.proposal_id = int(proposal_id)

    @discord.ui.button(label="Approve & Execute", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        handler = getattr(self.bot, "handle_autonomy_proposal_interaction", None)
        if handler is None:
            await interaction.response.send_message("Autonomy handler unavailable.", ephemeral=True)
            return
        await handler(interaction=interaction, proposal_id=self.proposal_id, decision="approve")

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        handler = getattr(self.bot, "handle_autonomy_proposal_interaction", None)
        if handler is None:
            await interaction.response.send_message("Autonomy handler unavailable.", ephemeral=True)
            return
        await handler(interaction=interaction, proposal_id=self.proposal_id, decision="deny")


class MemoryFactSelect(discord.ui.Select):
    def __init__(self, bot: discord.Client, guild_id: int, user_id: int, rows: list[dict[str, object]]):
        options: list[discord.SelectOption] = []
        for row in rows[:25]:
            index = int(row.get("index", 0) or 0)
            fact = str(row.get("fact", ""))[:80] or "memory fact"
            options.append(discord.SelectOption(label=f"#{index} {fact}"[:100], value=str(index)))
        super().__init__(placeholder="Select memory fact", min_values=1, max_values=1, options=options)
        self.bot = bot
        self.guild_id = int(guild_id)
        self.user_id = int(user_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, MemoryControlView):
            view.selected_index = int(self.values[0])
        await interaction.response.send_message(f"Selected memory `#{self.values[0]}`.", ephemeral=True)


class MemoryControlView(discord.ui.View):
    def __init__(self, bot: discord.Client, guild_id: int, user_id: int, rows: list[dict[str, object]]):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = int(guild_id)
        self.user_id = int(user_id)
        self.selected_index = int(rows[0].get("index", 0) or 0) if rows else 0
        if rows:
            self.add_item(MemoryFactSelect(bot, guild_id, user_id, rows))

    async def _run(self, interaction: discord.Interaction, action: str) -> None:
        handler = getattr(self.bot, "handle_memory_control_interaction", None)
        if handler is None:
            await interaction.response.send_message("Memory handler unavailable.", ephemeral=True)
            return
        await handler(
            interaction=interaction,
            guild_id=self.guild_id,
            user_id=self.user_id,
            index=self.selected_index,
            action=action,
        )

    @discord.ui.button(label="Pin", style=discord.ButtonStyle.success)
    async def pin(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._run(interaction, "pin")

    @discord.ui.button(label="Unpin", style=discord.ButtonStyle.secondary)
    async def unpin(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._run(interaction, "unpin")

    @discord.ui.button(label="Forget", style=discord.ButtonStyle.danger)
    async def forget(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._run(interaction, "forget")

    @discord.ui.button(label="Export", style=discord.ButtonStyle.primary)
    async def export(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._run(interaction, "export")
