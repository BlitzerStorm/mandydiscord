from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone
from typing import Any, Callable

import discord
from discord.ext import commands

from mandy_v1.config import Settings
from mandy_v1.services.admin_layout_service import AdminLayoutService
from mandy_v1.services.ai_service import AIService
from mandy_v1.services.dm_bridge_service import DMBridgeService
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.services.mirror_service import MirrorService
from mandy_v1.services.onboarding_service import OnboardingService
from mandy_v1.services.soc_service import SocService
from mandy_v1.services.watcher_service import WatcherService
from mandy_v1.storage import MessagePackStore
from mandy_v1.ui.global_menu import GlobalMenuView
from mandy_v1.ui.mirror_actions import MirrorActionContext, MirrorActionView
from mandy_v1.ui.satellite_debug import PermissionRequestApprovalView, PermissionRequestPromptView, SatelliteDebugView


class OnboardingSelect(discord.ui.Select):
    def __init__(self, bot: "MandyBot", users: list[discord.User | discord.Member]):
        options = [discord.SelectOption(label=f"{u} ({u.id})"[:100], value=str(u.id)) for u in users[:25]]
        super().__init__(placeholder="Select user to onboard", min_values=1, max_values=1, options=options)
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.bot.soc.can_run(interaction.user, 70):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        user_id = int(self.values[0])
        user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        if not user:
            await interaction.response.send_message("User not found.", ephemeral=True)
            return
        try:
            invite = await self.bot.onboarding.send_invite(self.bot, user)
            self.bot.logger.log("onboarding.invite_sent_manual", actor_id=interaction.user.id, user_id=user_id)
            await interaction.response.send_message(f"Invite sent to `{user_id}`: {invite}", ephemeral=True)
        except Exception as exc:  # noqa: BLE001
            await interaction.response.send_message(f"Onboarding failed: {exc}", ephemeral=True)


class OnboardingView(discord.ui.View):
    def __init__(self, bot: "MandyBot", users: list[discord.User | discord.Member]):
        super().__init__(timeout=120)
        self.add_item(OnboardingSelect(bot, users))


class MandyBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True
        intents.reactions = True
        intents.dm_messages = True
        super().__init__(command_prefix=settings.command_prefix, intents=intents, help_command=None)
        self.settings = settings
        self.store = MessagePackStore(settings.store_path)
        self.logger = LoggerService(self.store)
        self.layout = AdminLayoutService(self.store, self.logger)
        self.soc = SocService(settings, self.store)
        self.watchers = WatcherService(self.store)
        self.mirrors = MirrorService(settings, self.store, self.logger)
        self.onboarding = OnboardingService(settings, self.store, self.logger)
        self.dm_bridges = DMBridgeService(settings, self.store, self.logger)
        self.ai = AIService(settings, self.store)
        self.started_at = datetime.now(tz=timezone.utc)
        self._autosave_task: asyncio.Task | None = None
        self._ai_warmup_task: asyncio.Task | None = None
        self._ai_pending_reply_tasks: dict[tuple[int, int], asyncio.Task] = {}
        self._typing_rng = random.Random()
        self._ready_once = False
        self.logger.subscribe(self._on_log_row)

    async def setup_hook(self) -> None:
        await self.store.load()
        self._autosave_task = asyncio.create_task(self.store.autosave_loop(), name="msgpack-autosave")
        self._register_commands()

    def _tier_check(self, min_tier: int) -> Callable[[commands.Context], bool]:
        async def predicate(ctx: commands.Context) -> bool:
            user = ctx.author
            return self.soc.can_run(user, min_tier)

        return commands.check(predicate)

    def _register_commands(self) -> None:
        @self.command(name="health")
        @self._tier_check(50)
        async def health(ctx: commands.Context) -> None:
            uptime = datetime.now(tz=timezone.utc) - self.started_at
            payload = (
                f"Uptime: `{uptime}`\n"
                f"Guilds: `{len(self.guilds)}`\n"
                f"Watchers: `{len(self.store.data['watchers'])}`\n"
                f"Mirror servers: `{len(self.store.data['mirrors']['servers'])}`\n"
                f"DM bridges: `{len(self.store.data['dm_bridges'])}`"
            )
            await ctx.send(payload)

        @self.group(name="watchers", invoke_without_command=True)
        @self._tier_check(50)
        async def watchers_group(ctx: commands.Context) -> None:
            rows = self.watchers.list_all()
            if not rows:
                await ctx.send("No watchers configured.")
                return
            lines = ["Active watchers:"]
            for user_id, cfg in rows.items():
                count = self.store.data["watcher_counts"].get(str(user_id), 0)
                lines.append(f"- `{user_id}` threshold={cfg['threshold']} count={count} response={cfg['response_text']}")
            await ctx.send("\n".join(lines)[:1900])

        @watchers_group.command(name="add")
        @self._tier_check(70)
        async def watchers_add(ctx: commands.Context, user_id: int, threshold: int, *, response_text: str) -> None:
            self.watchers.add_or_update(user_id=user_id, threshold=threshold, response_text=response_text)
            self.logger.log("watcher.add", actor_id=ctx.author.id, user_id=user_id, threshold=threshold)
            await ctx.send(f"Watcher set for `{user_id}` with threshold `{threshold}`.")

        @watchers_group.command(name="remove")
        @self._tier_check(70)
        async def watchers_remove(ctx: commands.Context, user_id: int) -> None:
            existed = self.watchers.remove(user_id)
            self.logger.log("watcher.remove", actor_id=ctx.author.id, user_id=user_id, existed=existed)
            await ctx.send(f"Watcher removed for `{user_id}`: `{existed}`.")

        @watchers_group.command(name="reset")
        @self._tier_check(70)
        async def watchers_reset(ctx: commands.Context, user_id: int) -> None:
            self.watchers.reset_count(user_id)
            self.logger.log("watcher.reset", actor_id=ctx.author.id, user_id=user_id)
            await ctx.send(f"Watcher count reset for `{user_id}`.")
        @self.command(name="socset")
        @self._tier_check(90)
        async def socset(ctx: commands.Context, user_id: int, tier: int) -> None:
            self.store.data["soc"]["user_tiers"][str(user_id)] = int(tier)
            self.store.touch()
            self.logger.log("soc.tier_set", actor_id=ctx.author.id, user_id=user_id, tier=tier)
            await ctx.send(f"SOC tier set: `{user_id}` -> `{tier}`")

        @self.command(name="onboarding")
        @self._tier_check(70)
        async def onboarding_cmd(ctx: commands.Context, user_id: int | None = None) -> None:
            if user_id:
                user = self.get_user(user_id) or await self.fetch_user(user_id)
                invite = await self.onboarding.send_invite(self, user)
                await ctx.send(f"Invite sent to `{user_id}`: {invite}")
                return
            users = self._collect_onboard_candidates()
            if not users:
                await ctx.send("No candidate users found.")
                return
            await ctx.send("Select a user to onboard:", view=OnboardingView(self, users))

        @self.command(name="syncaccess")
        @self._tier_check(90)
        async def syncaccess(ctx: commands.Context) -> None:
            if not isinstance(ctx.guild, discord.Guild) or ctx.guild.id != self.settings.admin_guild_id:
                await ctx.send("Run this in the Admin Hub.")
                return
            bypass = self.onboarding.bypass_set()
            for member in ctx.guild.members:
                await self.mirrors.sync_admin_member_access(self, member, bypass)
            self.logger.log("soc.sync_access_run", actor_id=ctx.author.id, guild_id=ctx.guild.id)
            await ctx.send("Access sync complete.")

        @self.command(name="setup")
        @self._tier_check(90)
        async def setup_cmd(ctx: commands.Context) -> None:
            if not isinstance(ctx.guild, discord.Guild) or ctx.guild.id != self.settings.admin_guild_id:
                await ctx.send("Run this in the Admin Hub.")
                return
            summary = await self.layout.ensure(ctx.guild)
            await self._ensure_base_access_roles(ctx.guild)
            await self._ensure_global_menu_panel()
            self.logger.log("admin.setup_command", actor_id=ctx.author.id, guild_id=ctx.guild.id)
            await ctx.send(
                "Setup complete. "
                f"created_categories={summary['created_categories']} "
                f"created_channels={summary['created_channels']}"
            )

        @self.command(name="menupanel")
        @self._tier_check(50)
        async def menupanel(ctx: commands.Context) -> None:
            if not isinstance(ctx.guild, discord.Guild) or ctx.guild.id != self.settings.admin_guild_id:
                await ctx.send("Run this in the Admin Hub.")
                return
            await self._ensure_global_menu_panel(force_refresh=True)
            await ctx.send("Global menu panel refreshed.")

        @self.command(name="debugpanel")
        @self._tier_check(50)
        async def debugpanel(ctx: commands.Context) -> None:
            if not isinstance(ctx.guild, discord.Guild) or ctx.guild.id == self.settings.admin_guild_id:
                await ctx.send("Run this in a satellite server.")
                return
            server_cfg = self.store.data["mirrors"]["servers"].get(str(ctx.guild.id))
            if not server_cfg:
                await self.mirrors.ensure_satellite(self, ctx.guild)
            await self._ensure_satellite_debug_panel(ctx.guild, force_invite_refresh=True)
            await ctx.send("Satellite debug panel refreshed.")

        @self.command(name="setguestpass")
        @self._tier_check(90)
        async def setguestpass(ctx: commands.Context, *, password: str) -> None:
            self.store.data["guest_access"]["password"] = password.strip()
            self.store.touch()
            self.logger.log("guestpass.updated", actor_id=ctx.author.id)
            await ctx.send("Guest password updated.")

        @self.command(name="guestpass")
        async def guestpass(ctx: commands.Context, *, password: str) -> None:
            if not ctx.guild or ctx.guild.id != self.settings.admin_guild_id:
                await ctx.send("Run this in the Admin Hub.")
                return
            expected = str(self.store.data["guest_access"].get("password", ""))
            bypass = self.onboarding.bypass_set()
            if ctx.author.id in bypass or ctx.author.id == self.settings.god_user_id:
                await self._promote_member(ctx.author)
                await ctx.send("Bypass verified. Access granted.")
                return
            if not expected:
                await ctx.send("Guest password is not configured.")
                return
            if password.strip() != expected:
                await ctx.send("Invalid password.")
                return
            verified = set(self.store.data["guest_access"].get("verified_user_ids", []))
            verified.add(ctx.author.id)
            self.store.data["guest_access"]["verified_user_ids"] = sorted(verified)
            self.store.touch()
            await self._promote_member(ctx.author)
            self.logger.log("guestpass.verified", user_id=ctx.author.id)
            await ctx.send("Access granted.")

    def _collect_onboard_candidates(self) -> list[discord.User | discord.Member]:
        users: dict[int, discord.User | discord.Member] = {}
        for guild in self.guilds:
            for member in guild.members:
                if member.bot:
                    continue
                users.setdefault(member.id, member)
        return sorted(users.values(), key=lambda u: str(u))[:25]

    def _ui_state(self) -> dict[str, Any]:
        root = self.store.data.setdefault("ui", {})
        root.setdefault("global_menu_message_id", 0)
        return root

    def _resolve_global_menu_channel(self) -> discord.TextChannel | None:
        admin_guild = self.get_guild(self.settings.admin_guild_id)
        if not admin_guild:
            return None
        for name in ("menu", "requests"):
            channel = discord.utils.get(admin_guild.text_channels, name=name)
            if isinstance(channel, discord.TextChannel):
                return channel
        return None

    def _build_global_menu_embed(self, channel: discord.TextChannel) -> discord.Embed:
        total_satellites = len(self.store.data.get("mirrors", {}).get("servers", {}))
        embed = discord.Embed(
            title="Mandy Global Menu",
            description="Unified control panel for satellite controls, health, and approval workflows.",
            color=0x5865F2,
            timestamp=datetime.now(tz=timezone.utc),
        )
        embed.add_field(
            name="Panel Actions",
            value=(
                "Open Satellite Controls: choose a server ID and open its full control menu.\n"
                "List Satellites: see all onboarded satellite IDs.\n"
                "Health Snapshot: quick runtime and load stats.\n"
                "Refresh Menu Panel: rebuild this panel."
            )[:1024],
            inline=False,
        )
        embed.add_field(
            name="Core Commands",
            value=(
                "`!health` `!setup` `!menupanel` `!debugpanel`\n"
                "`!watchers` `!watchers add/remove/reset`\n"
                "`!onboarding` `!syncaccess` `!socset`\n"
                "`!setguestpass` `!guestpass`"
            )[:1024],
            inline=False,
        )
        embed.add_field(
            name="Environment",
            value=(
                f"Admin Hub: `{channel.guild.name}` (`{channel.guild.id}`)\n"
                f"Satellites onboarded: `{total_satellites}`\n"
                f"Prefix: `{self.settings.command_prefix}`"
            ),
            inline=False,
        )
        embed.set_footer(text="Use the button menu below to access full satellite controls.")
        return embed

    async def _ensure_global_menu_panel(self, force_refresh: bool = False) -> None:
        channel = self._resolve_global_menu_channel()
        if not channel:
            return
        state = self._ui_state()
        view = GlobalMenuView(self)
        embed = self._build_global_menu_embed(channel)
        message_id = int(state.get("global_menu_message_id", 0) or 0)
        existing: discord.Message | None = None
        if message_id > 0:
            try:
                existing = await channel.fetch_message(message_id)
            except discord.HTTPException:
                existing = None
        if existing and not force_refresh:
            await existing.edit(embed=embed, view=view)
            return
        if existing and force_refresh:
            await existing.edit(embed=embed, view=view)
            return
        posted = await channel.send(embed=embed, view=view)
        state["global_menu_message_id"] = posted.id
        self.store.touch()

    async def global_menu_list_satellites(self) -> str:
        rows: list[str] = []
        for guild_id in sorted(self.store.data["mirrors"]["servers"].keys(), key=lambda x: int(x)):
            guild = self.get_guild(int(guild_id))
            if guild:
                rows.append(f"- `{guild.id}` {guild.name}")
            else:
                rows.append(f"- `{guild_id}` (bot not currently in cache)")
        if not rows:
            return "No satellites are onboarded yet."
        text = "Satellites:\n" + "\n".join(rows)
        return text[:1900]

    async def global_menu_health_snapshot(self) -> str:
        uptime = datetime.now(tz=timezone.utc) - self.started_at
        last_api = self.store.data.get("ai", {}).get("last_api_test", {})
        api_status = "none"
        if isinstance(last_api, dict) and last_api:
            api_status = "ok" if bool(last_api.get("ok")) else "fail"
        payload = (
            f"Uptime: `{uptime}`\n"
            f"Guilds: `{len(self.guilds)}`\n"
            f"Satellites: `{len(self.store.data['mirrors']['servers'])}`\n"
            f"Watchers: `{len(self.store.data['watchers'])}`\n"
            f"Logs buffered: `{len(self.store.data['logs'])}`\n"
            f"AI last API test: `{api_status}`"
        )
        return payload

    async def refresh_global_menu_panel(self, interaction: discord.Interaction) -> None:
        if not self.soc.can_run(interaction.user, 70):
            await self._send_interaction_message(interaction, "Not authorized.", ephemeral=True)
            return
        await self._ensure_global_menu_panel(force_refresh=True)
        await self._send_interaction_message(interaction, "Global menu panel refreshed.", ephemeral=True)

    async def open_global_satellite_menu(self, interaction: discord.Interaction, satellite_guild_id: int) -> None:
        if not self.soc.can_run(interaction.user, 50):
            await self._send_interaction_message(interaction, "Not authorized.", ephemeral=True)
            return
        if satellite_guild_id == self.settings.admin_guild_id:
            await self._send_interaction_message(interaction, "That is the Admin Hub ID, not a satellite.", ephemeral=True)
            return
        guild = self.get_guild(satellite_guild_id)
        if guild is None:
            await self._send_interaction_message(interaction, "Satellite not found in current bot cache.", ephemeral=True)
            return
        server_cfg = self.store.data["mirrors"]["servers"].get(str(satellite_guild_id))
        if not server_cfg:
            server_cfg = await self.mirrors.ensure_satellite(self, guild)
            if not server_cfg:
                await self._send_interaction_message(interaction, "Failed to provision satellite mirror/debug channels.", ephemeral=True)
                return
        embed = await self._build_satellite_debug_embed(guild, server_cfg, force_invite_refresh=False)
        await self._send_interaction_message(
            interaction,
            f"Satellite controls loaded for `{guild.name}`.",
            ephemeral=True,
            view=SatelliteDebugView(self, satellite_guild_id),
            embed=embed,
        )

    async def _run_ai_startup_scan(self) -> None:
        self.logger.log("ai.warmup_started", guilds=max(0, len(self.guilds) - 1))
        for guild in self.guilds:
            if guild.id == self.settings.admin_guild_id:
                continue
            await self._warmup_ai_for_guild(guild)
        self.logger.log("ai.warmup_finished")

    async def _warmup_ai_for_guild(self, guild: discord.Guild) -> None:
        try:
            summary = await self.ai.warmup_guild(guild)
            self.logger.log(
                "ai.warmup_guild",
                guild_id=guild.id,
                scanned_channels=summary.get("scanned_channels", 0),
                scanned_messages=summary.get("scanned_messages", 0),
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.log("ai.warmup_failed", guild_id=guild.id, error=str(exc)[:300])

    async def on_ready(self) -> None:
        if self._ready_once:
            return
        self._ready_once = True
        self.logger.log("bot.ready", user_id=self.user.id if self.user else None, guilds=len(self.guilds))
        admin_guild = self.get_guild(self.settings.admin_guild_id)
        if admin_guild:
            try:
                await self.layout.ensure(admin_guild)
                await self._ensure_base_access_roles(admin_guild)
                await self._ensure_global_menu_panel()
            except discord.HTTPException:
                self.logger.log("admin.layout_setup_failed", guild_id=admin_guild.id)
        for guild in self.guilds:
            if guild.id == self.settings.admin_guild_id:
                continue
            try:
                await self.mirrors.ensure_satellite(self, guild)
                await self._ensure_satellite_debug_panel(guild)
            except discord.HTTPException:
                self.logger.log("mirror.ensure_failed", guild_id=guild.id)
        if self._ai_warmup_task is None or self._ai_warmup_task.done():
            self._ai_warmup_task = asyncio.create_task(self._run_ai_startup_scan(), name="ai-startup-scan")
        print(f"Connected as {self.user} ({self.user.id if self.user else '?'})")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        self.logger.log("guild.joined", guild_id=guild.id, guild_name=guild.name)
        if guild.id == self.settings.admin_guild_id:
            try:
                await self.layout.ensure(guild)
                await self._ensure_base_access_roles(guild)
                await self._ensure_global_menu_panel()
            except discord.HTTPException:
                self.logger.log("admin.layout_setup_failed", guild_id=guild.id)
            return
        try:
            await self.mirrors.ensure_satellite(self, guild)
            await self._ensure_satellite_debug_panel(guild, force_invite_refresh=True)
            asyncio.create_task(self._warmup_ai_for_guild(guild), name=f"ai-warmup-{guild.id}")
        except discord.HTTPException:
            self.logger.log("guild.join_setup_failed", guild_id=guild.id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self.logger.log("guild.removed", guild_id=guild.id, guild_name=guild.name)

    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != self.settings.admin_guild_id:
            self.logger.log("satellite.member_join", guild_id=member.guild.id, user_id=member.id)
            return
        await self._ensure_base_access_roles(member.guild)
        bypass = self.onboarding.bypass_set()
        verified = set(self.store.data["guest_access"].get("verified_user_ids", []))
        if member.id in bypass or member.id in verified or member.id == self.settings.god_user_id:
            await self._promote_member(member)
        else:
            guest_role = discord.utils.get(member.guild.roles, name="ACCESS:Guest")
            if guest_role and guest_role not in member.roles:
                await member.add_roles(guest_role, reason="Mandy v1 guest default")
        bypass = self.onboarding.bypass_set()
        await self.mirrors.sync_admin_member_access(self, member, bypass)
        self.logger.log("admin.member_join", user_id=member.id)

    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.abc.User) -> None:
        await self.mirrors.forward_reaction(self, reaction, user)

    async def on_command_error(self, ctx: commands.Context, exception: Exception) -> None:
        if isinstance(exception, commands.CheckFailure):
            await ctx.send("Not authorized.")
            return
        self.logger.log("command.error", error=str(exception), command=ctx.command.name if ctx.command else "unknown")
        await ctx.send(f"Command error: {exception}")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if isinstance(message.channel, discord.DMChannel):
            await self.dm_bridges.relay_inbound(self, message)
            await self.process_commands(message)
            return

        self.ai.capture_message(message)

        # Mirror first; watcher and AI consume the same live event to avoid extra fetches.
        await self.mirrors.mirror_message(self, message, self._build_mirror_view)

        hit = self.watchers.on_message(message)
        if hit:
            typing_delay = await self._simulate_typing_delay(message.channel)
            parts = await self._send_split_channel_message(message.channel, hit.response)
            self.logger.log(
                "watcher.hit",
                user_id=hit.user_id,
                threshold=hit.threshold,
                count=hit.count,
                guild_id=message.guild.id if message.guild else 0,
                typing_delay_sec=typing_delay,
                parts=parts,
            )

        if message.guild and not message.content.startswith(self.settings.command_prefix):
            await self._maybe_handle_ai_message(message)

        if message.guild and message.guild.id == self.settings.admin_guild_id:
            if isinstance(message.channel, discord.TextChannel) and message.channel.name.startswith("dm-"):
                if self.soc.can_run(message.author, 50):
                    sent = await self.dm_bridges.relay_outbound(self, message)
                    if sent:
                        await message.add_reaction("\u2705")

        await self.process_commands(message)

    def _build_mirror_view(self, source_message: discord.Message) -> discord.ui.View:
        ctx = MirrorActionContext(
            source_guild_id=source_message.guild.id if source_message.guild else 0,
            source_channel_id=source_message.channel.id,
            source_message_id=source_message.id,
            source_author_id=source_message.author.id,
        )
        return MirrorActionView(
            bot=self,
            ctx=ctx,
            mirror_service=self.mirrors,
            watcher_service=self.watchers,
            soc_service=self.soc,
            logger=self.logger,
        )

    async def _ensure_base_access_roles(self, guild: discord.Guild) -> None:
        for role_name in ("ACCESS:Guest", "ACCESS:Member", "ACCESS:Engineer", "ACCESS:Admin", "ACCESS:SOC"):
            if discord.utils.get(guild.roles, name=role_name) is None:
                await guild.create_role(name=role_name, reason="Mandy v1 access role setup")

    async def _promote_member(self, member: discord.Member | discord.User) -> None:
        if not isinstance(member, discord.Member):
            return
        await self._ensure_base_access_roles(member.guild)
        guest = discord.utils.get(member.guild.roles, name="ACCESS:Guest")
        member_role = discord.utils.get(member.guild.roles, name="ACCESS:Member")
        if guest and guest in member.roles:
            await member.remove_roles(guest, reason="Mandy v1 guest verification")
        if member_role and member_role not in member.roles:
            await member.add_roles(member_role, reason="Mandy v1 guest verification")

    async def _maybe_handle_ai_message(self, message: discord.Message) -> None:
        if not message.guild or not self.user:
            return
        guild_id = message.guild.id
        if self.ai.is_roast_enabled(guild_id):
            if self.ai.should_roast(message, self.user.id):
                reply = await self.ai.generate_roast_reply(message)
                typing_delay = await self._simulate_typing_delay(message.channel)
                parts = await self._send_split_reply(message, reply, mention_author=False)
                self.ai.note_bot_action(message.channel.id, "reply", user_id=message.author.id)
                self.logger.log(
                    "ai.roast_reply",
                    guild_id=guild_id,
                    user_id=message.author.id,
                    typing_delay_sec=typing_delay,
                    parts=parts,
                )
            return
        if self.ai.is_chat_enabled(guild_id):
            directive = self.ai.decide_chat_action(message, self.user.id)
            if directive.action == "ignore":
                return
            if directive.action == "react":
                emoji = directive.emoji or "\U0001F440"
                try:
                    await message.add_reaction(emoji)
                    self.ai.note_bot_action(message.channel.id, "react")
                    self.logger.log(
                        "ai.chat_react",
                        guild_id=guild_id,
                        user_id=message.author.id,
                        emoji=emoji,
                        reason=directive.reason,
                    )
                except discord.HTTPException:
                    self.logger.log("ai.chat_react_failed", guild_id=guild_id, user_id=message.author.id, emoji=emoji)
                return
            if directive.action == "reply":
                delay = self.ai.reply_delay_seconds(message, reason=directive.reason, still_talking=directive.still_talking)
                self._schedule_ai_reply(
                    message,
                    reason=directive.reason,
                    still_talking=directive.still_talking,
                    delay_sec=delay,
                )

    def _schedule_ai_reply(
        self,
        message: discord.Message,
        *,
        reason: str,
        still_talking: bool,
        delay_sec: float,
    ) -> None:
        key = (message.channel.id, message.author.id)
        existing = self._ai_pending_reply_tasks.get(key)
        if existing and not existing.done():
            existing.cancel()

        async def worker() -> None:
            try:
                await asyncio.sleep(max(0.4, delay_sec))
            except asyncio.CancelledError:
                return

            try:
                burst = self.ai.user_burst_lines(message.channel.id, message.author.id, limit=6)
                reply = await self.ai.generate_chat_reply(
                    message,
                    reason=reason,
                    still_talking=still_talking,
                    burst_lines=burst,
                )
                typing_delay = await self._simulate_typing_delay(message.channel)
                parts = await self._send_split_reply(message, reply, mention_author=False)
                self.ai.note_bot_action(message.channel.id, "reply", user_id=message.author.id)
                self.logger.log(
                    "ai.chat_reply",
                    guild_id=message.guild.id if message.guild else 0,
                    user_id=message.author.id,
                    reason=reason,
                    still_talking=still_talking,
                    delay_sec=round(delay_sec, 2),
                    burst_count=len(burst),
                    typing_delay_sec=typing_delay,
                    parts=parts,
                )
            except asyncio.CancelledError:
                return
            except discord.HTTPException as exc:
                self.logger.log(
                    "ai.chat_reply_failed",
                    guild_id=message.guild.id if message.guild else 0,
                    user_id=message.author.id,
                    error=str(exc)[:300],
                )
            finally:
                current = self._ai_pending_reply_tasks.get(key)
                if current is asyncio.current_task():
                    self._ai_pending_reply_tasks.pop(key, None)

        task = asyncio.create_task(worker(), name=f"ai-reply-{message.channel.id}-{message.author.id}")
        self._ai_pending_reply_tasks[key] = task
        self.logger.log(
            "ai.chat_reply_scheduled",
            guild_id=message.guild.id if message.guild else 0,
            user_id=message.author.id,
            reason=reason,
            delay_sec=round(delay_sec, 2),
        )

    async def _simulate_typing_delay(self, channel: discord.abc.Messageable) -> float:
        delay = round(self._typing_rng.uniform(2.0, 10.0), 2)
        try:
            async with channel.typing():
                await asyncio.sleep(delay)
        except (AttributeError, discord.HTTPException):
            await asyncio.sleep(delay)
        return delay

    def _split_text_for_discord(self, text: str, limit: int = 1900) -> list[str]:
        normalized = str(text or "").replace("\r\n", "\n").strip()
        if not normalized:
            return ["(no response)"]

        chunks: list[str] = []
        remaining = normalized
        while len(remaining) > limit:
            cut = remaining.rfind("\n\n", 0, limit + 1)
            if cut < max(1, int(limit * 0.5)):
                cut = remaining.rfind("\n", 0, limit + 1)
            if cut < max(1, int(limit * 0.5)):
                cut = remaining.rfind(" ", 0, limit + 1)
            if cut <= 0:
                cut = limit
            chunk = remaining[:cut].strip()
            if not chunk:
                chunk = remaining[:limit]
                cut = len(chunk)
            chunks.append(chunk[:limit])
            remaining = remaining[cut:].strip()
        if remaining:
            chunks.append(remaining[:limit])
        return chunks

    async def _send_split_channel_message(self, channel: discord.abc.Messageable, text: str) -> int:
        chunks = self._split_text_for_discord(text)
        for chunk in chunks:
            await channel.send(chunk)
        return len(chunks)

    async def _send_split_reply(
        self,
        source_message: discord.Message,
        text: str,
        *,
        mention_author: bool = False,
    ) -> int:
        chunks = self._split_text_for_discord(text)
        first, *rest = chunks
        await source_message.reply(first, mention_author=mention_author)
        for chunk in rest:
            await source_message.channel.send(chunk)
        return len(chunks)

    async def _ensure_satellite_debug_panel(self, satellite_guild: discord.Guild, force_invite_refresh: bool = False) -> None:
        if satellite_guild.id == self.settings.admin_guild_id:
            return
        server_cfg = self.store.data["mirrors"]["servers"].get(str(satellite_guild.id))
        if not isinstance(server_cfg, dict):
            return
        debug_channel = self.get_channel(int(server_cfg.get("debug_channel_id", 0) or 0))
        if not isinstance(debug_channel, discord.TextChannel):
            return
        embed = await self._build_satellite_debug_embed(satellite_guild, server_cfg, force_invite_refresh=force_invite_refresh)
        view = SatelliteDebugView(self, satellite_guild.id)
        message_id = int(server_cfg.get("debug_dashboard_message_id", 0) or 0)
        existing: discord.Message | None = None
        if message_id > 0:
            try:
                existing = await debug_channel.fetch_message(message_id)
            except discord.HTTPException:
                existing = None
        if existing:
            await existing.edit(embed=embed, view=view)
        else:
            posted = await debug_channel.send(embed=embed, view=view)
            server_cfg["debug_dashboard_message_id"] = posted.id
            self.store.touch()

    async def _build_satellite_debug_embed(
        self,
        satellite_guild: discord.Guild,
        server_cfg: dict[str, Any],
        force_invite_refresh: bool = False,
    ) -> discord.Embed:
        bot_member = satellite_guild.me
        mirror_feed_id = int(server_cfg.get("mirror_feed_id", 0) or 0)
        mirror_feed = self.get_channel(mirror_feed_id)
        mirror_active = isinstance(mirror_feed, discord.TextChannel)

        member_count = satellite_guild.member_count or len(satellite_guild.members)
        owner_id = satellite_guild.owner_id
        owner_text = f"<@{owner_id}> (`{owner_id}`)" if owner_id else "Unknown"
        invite_url = await self._get_or_create_satellite_invite(satellite_guild, server_cfg, force_refresh=force_invite_refresh)

        perm_rows: list[str] = []
        if bot_member:
            perms = bot_member.guild_permissions
            for perm_key, label in (
                ("view_channel", "View Channels"),
                ("send_messages", "Send Messages"),
                ("read_message_history", "Read History"),
                ("manage_channels", "Manage Channels"),
                ("manage_roles", "Manage Roles"),
                ("create_instant_invite", "Create Invite"),
                ("add_reactions", "Add Reactions"),
                ("manage_messages", "Manage Messages"),
            ):
                perm_rows.append(f"{label}: {'YES' if getattr(perms, perm_key, False) else 'NO'}")
        else:
            perm_rows.append("Bot member state unavailable.")

        chat_enabled = self.ai.is_chat_enabled(satellite_guild.id)
        roast_enabled = self.ai.is_roast_enabled(satellite_guild.id)
        warmup = self.ai.warmup_status(satellite_guild.id) or {}
        warmup_line = "not run"
        if warmup:
            warmup_line = (
                f"channels={int(warmup.get('scanned_channels', 0))} "
                f"messages={int(warmup.get('scanned_messages', 0))} "
                f"at {str(warmup.get('ts', ''))[:19]}"
            )
        last_test = self.store.data.get("ai", {}).get("last_api_test", {})
        last_test_line = "No API test yet."
        if isinstance(last_test, dict) and last_test:
            outcome = "OK" if last_test.get("ok") else "FAIL"
            latency = last_test.get("latency_ms")
            last_test_line = f"{outcome} ({latency} ms): {str(last_test.get('detail', ''))[:120]}"

        embed = discord.Embed(
            title=f"Satellite Debug Dashboard: {satellite_guild.name}",
            color=0x2B2D31,
            timestamp=datetime.now(tz=timezone.utc),
        )
        embed.add_field(
            name="Server Snapshot",
            value=(
                f"Mirror active: `{mirror_active}`\n"
                f"Members: `{member_count}`\n"
                f"Channels: `{len(satellite_guild.channels)}` "
                f"(text={len(satellite_guild.text_channels)}, voice={len(satellite_guild.voice_channels)})\n"
                f"Owner: {owner_text}\n"
                f"Invite: {invite_url if invite_url else 'Unavailable'}"
            )[:1024],
            inline=False,
        )
        embed.add_field(name="Bot Permissions", value="\n".join(perm_rows)[:1024], inline=False)
        embed.add_field(
            name="AI Controls",
            value=(
                f"AI chat mode: `{chat_enabled}`\n"
                f"AI roast mode: `{roast_enabled}`\n"
                f"Alibaba key configured: `{self.ai.has_api_key()}`\n"
                f"Startup memory scan: {warmup_line}\n"
                f"Last API test: {last_test_line}"
            )[:1024],
            inline=False,
        )
        embed.set_footer(
            text=f"Satellite ID: {satellite_guild.id} | Mirror Feed ID: {mirror_feed_id} | Debug Channel ID: {server_cfg.get('debug_channel_id')}"
        )
        return embed

    async def _get_or_create_satellite_invite(
        self,
        satellite_guild: discord.Guild,
        server_cfg: dict[str, Any],
        force_refresh: bool = False,
    ) -> str:
        cached = str(server_cfg.get("satellite_invite_url", "")).strip()
        if cached and not force_refresh:
            return cached
        bot_member = satellite_guild.me
        if not bot_member:
            return cached
        if not bot_member.guild_permissions.create_instant_invite:
            return cached
        channel = next(
            (
                ch
                for ch in satellite_guild.text_channels
                if ch.permissions_for(bot_member).create_instant_invite and ch.permissions_for(bot_member).view_channel
            ),
            None,
        )
        if channel is None:
            return cached
        try:
            invite = await channel.create_invite(max_age=0, max_uses=0, unique=False, reason="Mandy satellite dashboard")
        except discord.HTTPException:
            return cached
        server_cfg["satellite_invite_url"] = invite.url
        self.store.touch()
        return invite.url

    async def handle_satellite_debug_action(
        self,
        interaction: discord.Interaction,
        satellite_guild_id: int,
        action: str,
    ) -> None:
        action_tiers = {
            "refresh_dashboard": 50,
            "toggle_ai_mode": 70,
            "toggle_ai_roast": 70,
            "test_ai_api": 70,
        }
        if action not in action_tiers:
            await self._send_interaction_message(interaction, "Unknown action.", ephemeral=True)
            return
        required_tier = action_tiers[action]
        if not self._can_run_menu_action(interaction.user, satellite_guild_id, action, required_tier):
            self.logger.log(
                "access.menu_denied",
                user_id=interaction.user.id,
                satellite_guild_id=satellite_guild_id,
                action=action,
                required_tier=required_tier,
            )
            await self._send_interaction_message(
                interaction,
                "Sorry, your permissions are not allowed for this action. Would you like to make a request?",
                ephemeral=True,
                view=PermissionRequestPromptView(self, satellite_guild_id, action),
            )
            return
        result_text = await self._perform_satellite_action(satellite_guild_id, action, actor_id=interaction.user.id, via_request=False)
        await self._send_interaction_message(interaction, result_text, ephemeral=True)

    async def submit_permission_request(
        self,
        interaction: discord.Interaction,
        satellite_guild_id: int,
        action: str,
        reason: str,
    ) -> int:
        root = self._feature_request_root()
        request_id = int(root.get("next_id", 1))
        root["next_id"] = request_id + 1
        request_row: dict[str, Any] = {
            "status": "pending",
            "created_ts": datetime.now(tz=timezone.utc).isoformat(),
            "requester_id": interaction.user.id,
            "requester_name": str(interaction.user),
            "satellite_guild_id": int(satellite_guild_id),
            "action": action,
            "reason": reason[:1000],
            "admin_channel_id": 0,
            "admin_message_id": 0,
        }
        root["requests"][str(request_id)] = request_row
        self.store.touch()
        self.logger.log(
            "access.request_submitted",
            request_id=request_id,
            user_id=interaction.user.id,
            satellite_guild_id=satellite_guild_id,
            action=action,
        )

        channel = self._resolve_god_admin_channel()
        if channel:
            try:
                msg = await channel.send(
                    embed=self._build_permission_request_embed(request_id, request_row),
                    view=PermissionRequestApprovalView(self, request_id),
                )
                request_row["admin_channel_id"] = channel.id
                request_row["admin_message_id"] = msg.id
                self.store.touch()
            except discord.HTTPException:
                self.logger.log("access.request_notify_failed", request_id=request_id, channel_id=channel.id)
        return request_id

    async def resolve_permission_request(
        self,
        interaction: discord.Interaction,
        request_id: int,
        resolution: str,
    ) -> tuple[bool, str, bool]:
        if not self.soc.can_run(interaction.user, 90):
            return False, "Not authorized.", False
        root = self._feature_request_root()
        requests = root["requests"]
        row = requests.get(str(request_id))
        if not isinstance(row, dict):
            return False, "Request not found.", False
        status = str(row.get("status", "pending"))
        if status != "pending":
            return False, f"Request already resolved as `{status}`.", True
        if resolution not in {"approve_once", "approve_permanent", "deny"}:
            return False, "Invalid resolution.", False

        requester_id = int(row.get("requester_id", 0))
        satellite_guild_id = int(row.get("satellite_guild_id", 0))
        action = str(row.get("action", ""))
        result_note = ""

        if resolution == "approve_once":
            key = self._request_grant_key(satellite_guild_id, requester_id, action)
            once = root["grants"]["once"]
            once[key] = int(once.get(key, 0)) + 1
            row["status"] = "approved_once"
            result_note = await self._perform_satellite_action(satellite_guild_id, action, actor_id=requester_id, via_request=True)
        elif resolution == "approve_permanent":
            key = self._request_grant_key(satellite_guild_id, requester_id, action)
            root["grants"]["permanent"][key] = True
            row["status"] = "approved_permanent"
            result_note = await self._perform_satellite_action(satellite_guild_id, action, actor_id=requester_id, via_request=True)
        else:
            row["status"] = "denied"

        row["resolved_ts"] = datetime.now(tz=timezone.utc).isoformat()
        row["resolver_id"] = interaction.user.id
        row["resolution"] = resolution
        self.store.touch()

        if interaction.message:
            try:
                await interaction.message.edit(embed=self._build_permission_request_embed(request_id, row))
            except discord.HTTPException:
                pass

        await self._notify_requester_resolution(requester_id, request_id, row, result_note)

        self.logger.log(
            "access.request_resolved",
            request_id=request_id,
            resolution=resolution,
            resolver_id=interaction.user.id,
            requester_id=requester_id,
            satellite_guild_id=satellite_guild_id,
            action=action,
        )
        feedback = f"Request `#{request_id}` resolved as `{row['status']}`."
        if result_note:
            feedback = f"{feedback} Action result: {result_note}"
        return True, feedback[:1900], True

    async def _perform_satellite_action(self, satellite_guild_id: int, action: str, actor_id: int, via_request: bool) -> str:
        guild = self.get_guild(satellite_guild_id)
        if not guild:
            return "Satellite is unavailable."

        if action == "refresh_dashboard":
            await self._ensure_satellite_debug_panel(guild, force_invite_refresh=True)
            self.logger.log(
                "debug.dashboard_refreshed",
                actor_id=actor_id,
                satellite_guild_id=satellite_guild_id,
                via_request=via_request,
            )
            return f"Dashboard refreshed for `{guild.name}`."

        if action == "toggle_ai_mode":
            enabled = self.ai.toggle_chat(satellite_guild_id)
            if enabled:
                await self._warmup_ai_for_guild(guild)
            await self._ensure_satellite_debug_panel(guild)
            self.logger.log(
                "ai.mode_toggled",
                actor_id=actor_id,
                satellite_guild_id=satellite_guild_id,
                mode="chat",
                enabled=enabled,
                via_request=via_request,
            )
            return f"AI chat mode is now `{enabled}` for `{guild.name}`. AI roast auto-disabled when chat is enabled."

        if action == "toggle_ai_roast":
            enabled = self.ai.toggle_roast(satellite_guild_id)
            await self._ensure_satellite_debug_panel(guild)
            self.logger.log(
                "ai.mode_toggled",
                actor_id=actor_id,
                satellite_guild_id=satellite_guild_id,
                mode="roast",
                enabled=enabled,
                via_request=via_request,
            )
            return f"AI roast mode is now `{enabled}` for `{guild.name}`. AI chat auto-disabled when roast is enabled."

        if action == "test_ai_api":
            result = await self.ai.test_api()
            await self._ensure_satellite_debug_panel(guild)
            self.logger.log(
                "ai.api_test",
                actor_id=actor_id,
                satellite_guild_id=satellite_guild_id,
                ok=result.ok,
                latency_ms=result.latency_ms,
            )
            return f"API test ok=`{result.ok}` latency_ms=`{result.latency_ms}` detail=`{result.detail[:220]}`"

        return "Unknown action."

    def _can_run_menu_action(
        self,
        user: discord.abc.User | discord.Member,
        satellite_guild_id: int,
        action: str,
        required_tier: int,
    ) -> bool:
        if self.soc.can_run(user, required_tier):
            return True
        return self._consume_one_time_or_permanent_grant(satellite_guild_id, user.id, action)

    def _consume_one_time_or_permanent_grant(self, satellite_guild_id: int, user_id: int, action: str) -> bool:
        root = self._feature_request_root()
        key = self._request_grant_key(satellite_guild_id, user_id, action)
        permanent = root["grants"]["permanent"]
        if permanent.get(key):
            return True
        once = root["grants"]["once"]
        count = int(once.get(key, 0))
        if count <= 0:
            return False
        if count == 1:
            once.pop(key, None)
        else:
            once[key] = count - 1
        self.store.touch()
        return True

    def _feature_request_root(self) -> dict[str, Any]:
        root = self.store.data.setdefault("feature_requests", {})
        root.setdefault("next_id", 1)
        root.setdefault("requests", {})
        grants = root.setdefault("grants", {})
        grants.setdefault("once", {})
        grants.setdefault("permanent", {})
        return root

    def _request_grant_key(self, satellite_guild_id: int, user_id: int, action: str) -> str:
        return f"{satellite_guild_id}:{user_id}:{action}"

    def _build_permission_request_embed(self, request_id: int, row: dict[str, Any]) -> discord.Embed:
        status = str(row.get("status", "pending"))
        color = 0xFEE75C
        if status.startswith("approved"):
            color = 0x57F287
        if status == "denied":
            color = 0xED4245
        satellite_guild_id = int(row.get("satellite_guild_id", 0))
        satellite = self.get_guild(satellite_guild_id)
        satellite_text = satellite.name if satellite else f"ID {satellite_guild_id}"
        embed = discord.Embed(title=f"Permission Request #{request_id}", color=color)
        embed.add_field(name="Requester", value=f"<@{row.get('requester_id', 0)}> (`{row.get('requester_id', 0)}`)", inline=False)
        embed.add_field(name="Satellite", value=satellite_text, inline=False)
        embed.add_field(name="Action", value=self._action_label(str(row.get("action", ""))), inline=False)
        embed.add_field(name="Reason", value=str(row.get("reason", ""))[:1000] or "(none)", inline=False)
        embed.add_field(name="Status", value=f"`{status}`", inline=False)
        resolver_id = int(row.get("resolver_id", 0) or 0)
        if resolver_id:
            embed.add_field(name="Resolved By", value=f"<@{resolver_id}> (`{resolver_id}`)", inline=False)
        ts = str(row.get("created_ts", ""))
        if ts:
            embed.set_footer(text=f"Created: {ts}")
        return embed

    async def _notify_requester_resolution(
        self,
        requester_id: int,
        request_id: int,
        row: dict[str, Any],
        result_note: str,
    ) -> None:
        if requester_id <= 0:
            return
        user = self.get_user(requester_id)
        if user is None:
            try:
                user = await self.fetch_user(requester_id)
            except discord.HTTPException:
                return
        status = str(row.get("status", "resolved"))
        text = f"Your Mandy request `#{request_id}` was resolved as `{status}`."
        if result_note:
            text = f"{text}\nAction result: {result_note}"
        try:
            await user.send(text[:1900])
        except discord.HTTPException:
            return

    async def _send_interaction_message(
        self,
        interaction: discord.Interaction,
        content: str | None,
        *,
        ephemeral: bool = True,
        view: discord.ui.View | None = None,
        embed: discord.Embed | None = None,
    ) -> None:
        payload: dict[str, object] = {"ephemeral": ephemeral}
        if content is not None:
            payload["content"] = content
        if embed is not None:
            payload["embed"] = embed
        if view is not None:
            payload["view"] = view
        if interaction.response.is_done():
            await interaction.followup.send(**payload)
            return
        await interaction.response.send_message(**payload)

    def _action_label(self, action: str) -> str:
        labels = {
            "refresh_dashboard": "Refresh Dashboard",
            "toggle_ai_mode": "Toggle AI Mode",
            "toggle_ai_roast": "Toggle AI Roast",
            "test_ai_api": "Test AI API",
        }
        return labels.get(action, action)

    def _resolve_admin_debug_channel(self) -> discord.TextChannel | None:
        admin_guild = self.get_guild(self.settings.admin_guild_id)
        if not admin_guild:
            return None
        for name in ("debug-log", "diagnostics"):
            channel = discord.utils.get(admin_guild.text_channels, name=name)
            if isinstance(channel, discord.TextChannel):
                return channel
        return None

    def _resolve_god_admin_channel(self) -> discord.TextChannel | None:
        admin_guild = self.get_guild(self.settings.admin_guild_id)
        if not admin_guild:
            return None
        for name in ("server-management", "admin-chat", "requests"):
            channel = discord.utils.get(admin_guild.text_channels, name=name)
            if isinstance(channel, discord.TextChannel):
                return channel
        return None

    def _on_log_row(self, row: dict[str, object]) -> None:
        event = str(row.get("event", ""))
        if event.startswith("mirror."):
            return
        if not self._ready_once:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._dispatch_debug_log(row))

    async def _dispatch_debug_log(self, row: dict[str, object]) -> None:
        payload = self._format_log_payload(row)
        admin_channel = self._resolve_admin_debug_channel()
        if admin_channel:
            try:
                await admin_channel.send(payload)
            except discord.HTTPException:
                pass

        satellite_guild_id = self._extract_satellite_guild_from_log(row)
        if not satellite_guild_id:
            return
        server_cfg = self.store.data["mirrors"]["servers"].get(str(satellite_guild_id), {})
        debug_channel = self.get_channel(int(server_cfg.get("debug_channel_id", 0) or 0))
        if isinstance(debug_channel, discord.TextChannel):
            try:
                await debug_channel.send(payload)
            except discord.HTTPException:
                pass

    def _extract_satellite_guild_from_log(self, row: dict[str, object]) -> int:
        data = row.get("data", {})
        if not isinstance(data, dict):
            return 0
        for key in ("satellite_guild_id", "guild_id"):
            value = data.get(key)
            try:
                guild_id = int(value)
            except (TypeError, ValueError):
                continue
            if guild_id > 0 and guild_id != self.settings.admin_guild_id:
                return guild_id
        return 0

    def _format_log_payload(self, row: dict[str, object]) -> str:
        ts = str(row.get("ts", ""))
        event = str(row.get("event", "unknown"))
        data = row.get("data", {})
        if isinstance(data, dict):
            compact = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
        else:
            compact = str(data)
        message = f"[{ts}] {event} {compact}"
        if len(message) > 1900:
            message = message[:1900]
        return message


def main() -> None:
    settings = Settings.load()
    bot = MandyBot(settings)
    bot.run(settings.discord_token)


