from typing import Any, Awaitable, Callable, List, Optional

import discord


Callback = Callable[..., Awaitable[None]]


class RestrictedView(discord.ui.View):
    def __init__(self, user_id: int, on_timeout: Optional[Callback] = None, timeout: float = 120):
        super().__init__(timeout=timeout)
        self.user_id = int(user_id)
        self._on_timeout_cb = on_timeout
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        if callable(self._on_timeout_cb):
            await self._on_timeout_cb()
        if self.message:
            try:
                await self.message.edit(view=None)
            except Exception:
                pass


class ConfirmActionView(RestrictedView):
    def __init__(self, user_id: int, on_confirm: Callback, on_cancel: Callback, on_timeout: Optional[Callback] = None):
        super().__init__(user_id, on_timeout=on_timeout, timeout=120)
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=None)
        await self._on_confirm()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=None)
        await self._on_cancel()


class _Select(discord.ui.Select):
    def __init__(self, options: List[discord.SelectOption], on_pick: Callback, placeholder: str = "Select one"):
        super().__init__(options=options, placeholder=placeholder, min_values=1, max_values=1)
        self._on_pick = on_pick

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=None)
        await self._on_pick(self.values[0])


class CandidateSelectView(RestrictedView):
    def __init__(
        self,
        user_id: int,
        options: List[discord.SelectOption],
        on_pick: Callback,
        on_cancel: Callback,
        on_timeout: Optional[Callback] = None,
        placeholder: str = "Select one",
    ):
        super().__init__(user_id, on_timeout=on_timeout, timeout=120)
        self.add_item(_Select(options=options, on_pick=on_pick, placeholder=placeholder))
        self._on_cancel = on_cancel

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=None)
        await self._on_cancel()


class QuickNumberView(RestrictedView):
    def __init__(
        self,
        user_id: int,
        numbers: List[int],
        on_pick: Callback,
        on_custom: Callback,
        on_cancel: Callback,
        on_timeout: Optional[Callback] = None,
    ):
        super().__init__(user_id, on_timeout=on_timeout, timeout=120)
        self._on_pick = on_pick
        self._on_custom = on_custom
        self._on_cancel = on_cancel
        for value in numbers:
            self.add_item(self._build_number_button(value))
        self.add_item(self._build_custom_button())
        self.add_item(self._build_cancel_button())

    def _build_number_button(self, value: int) -> discord.ui.Button:
        async def _callback(interaction: discord.Interaction):
            await interaction.response.edit_message(view=None)
            await self._on_pick(value)
        btn = discord.ui.Button(label=str(value), style=discord.ButtonStyle.primary)
        btn.callback = _callback
        return btn

    def _build_custom_button(self) -> discord.ui.Button:
        async def _callback(interaction: discord.Interaction):
            await interaction.response.edit_message(view=None)
            await self._on_custom()
        btn = discord.ui.Button(label="Custom", style=discord.ButtonStyle.secondary)
        btn.callback = _callback
        return btn

    def _build_cancel_button(self) -> discord.ui.Button:
        async def _callback(interaction: discord.Interaction):
            await interaction.response.edit_message(view=None)
            await self._on_cancel()
        btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger)
        btn.callback = _callback
        return btn


class IntentChoiceView(CandidateSelectView):
    pass
