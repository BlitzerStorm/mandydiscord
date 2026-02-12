from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable

import discord
from discord.ext import commands

from mandy_v1.config import Settings
from mandy_v1.services.dm_bridge_service import DMBridgeService
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.services.mirror_service import MirrorService
from mandy_v1.services.onboarding_service import OnboardingService
from mandy_v1.services.soc_service import SocService
from mandy_v1.services.watcher_service import WatcherService
from mandy_v1.storage import MessagePackStore
from mandy_v1.ui.mirror_actions import MirrorActionContext, MirrorActionView


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
        self.soc = SocService(settings, self.store)
        self.watchers = WatcherService(self.store)
        self.mirrors = MirrorService(settings, self.store, self.logger)
        self.onboarding = OnboardingService(settings, self.store, self.logger)
        self.dm_bridges = DMBridgeService(settings, self.store, self.logger)
        self.started_at = datetime.now(tz=timezone.utc)
        self._autosave_task: asyncio.Task | None = None
        self._ready_once = False

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
                count = self.store.data["watcher_counts"].get(user_id, 0)
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
            await ctx.send("Access sync complete.")

        @self.command(name="setguestpass")
        @self._tier_check(90)
        async def setguestpass(ctx: commands.Context, *, password: str) -> None:
            self.store.data["guest_access"]["password"] = password.strip()
            self.store.touch()
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
            await ctx.send("Access granted.")

    def _collect_onboard_candidates(self) -> list[discord.User | discord.Member]:
        users: dict[int, discord.User | discord.Member] = {}
        for guild in self.guilds:
            for member in guild.members:
                if member.bot:
                    continue
                users.setdefault(member.id, member)
        return sorted(users.values(), key=lambda u: str(u))[:25]

    async def on_ready(self) -> None:
        if self._ready_once:
            return
        self._ready_once = True
        self.logger.log("bot.ready", user_id=self.user.id if self.user else None, guilds=len(self.guilds))
        for guild in self.guilds:
            if guild.id == self.settings.admin_guild_id:
                continue
            try:
                await self.mirrors.ensure_satellite(self, guild)
            except discord.HTTPException:
                self.logger.log("mirror.ensure_failed", guild_id=guild.id)
        print(f"Connected as {self.user} ({self.user.id if self.user else '?'})")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        self.logger.log("guild.joined", guild_id=guild.id, guild_name=guild.name)
        try:
            await self.mirrors.ensure_satellite(self, guild)
        except discord.HTTPException:
            self.logger.log("guild.join_setup_failed", guild_id=guild.id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self.logger.log("guild.removed", guild_id=guild.id, guild_name=guild.name)

    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != self.settings.admin_guild_id:
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
        await ctx.send(f"Command error: {exception}")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if isinstance(message.channel, discord.DMChannel):
            await self.dm_bridges.relay_inbound(self, message)
            await self.process_commands(message)
            return

        hit = self.watchers.on_message(message)
        if hit:
            await message.channel.send(hit.response)
            self.logger.log("watcher.hit", user_id=hit.user_id, threshold=hit.threshold, count=hit.count, guild_id=message.guild.id if message.guild else 0)

        await self.mirrors.mirror_message(self, message, self._build_mirror_view)

        if message.guild and message.guild.id == self.settings.admin_guild_id:
            if isinstance(message.channel, discord.TextChannel) and message.channel.name.startswith("dm-"):
                if self.soc.can_run(message.author, 50):
                    sent = await self.dm_bridges.relay_outbound(self, message)
                    if sent:
                        await message.add_reaction("✅")

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
        for role_name in ("ACCESS:Guest", "ACCESS:Member"):
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


def main() -> None:
    settings = Settings.load()
    bot = MandyBot(settings)
    bot.run(settings.discord_token)
