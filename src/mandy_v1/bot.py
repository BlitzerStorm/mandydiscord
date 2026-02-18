from __future__ import annotations

import asyncio
import io
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import discord
from discord.ext import commands

from mandy_v1.config import Settings
from mandy_v1.prompts import GOD_MODE_OVERRIDE_PROMPT_TEMPLATE
from mandy_v1.services.admin_layout_service import AdminLayoutService
from mandy_v1.services.ai_service import AIService
from mandy_v1.services.dm_bridge_service import DMBridgeService
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.services.mirror_service import MirrorService
from mandy_v1.services.onboarding_service import OnboardingService
from mandy_v1.services.shadow_league_service import SHADOW_CHANNEL_PRIORITY, ShadowLeagueService
from mandy_v1.services.soc_service import SocService
from mandy_v1.services.watcher_service import WatcherService
from mandy_v1.storage import MessagePackStore
from mandy_v1.ui.dm_bridge import DMBridgeControlView, DMBridgeUserView
from mandy_v1.ui.global_menu import GlobalMenuView
from mandy_v1.ui.mirror_actions import MirrorActionContext, MirrorActionView
from mandy_v1.ui.satellite_debug import PermissionRequestApprovalView, PermissionRequestPromptView, SatelliteDebugView
from mandy_v1.utils.discord_utils import get_bot_member


HOUSEKEEPING_INTERVAL_SEC = 15 * 60
HOUSEKEEPING_SCAN_LIMIT = 1600
HOUSEKEEPING_ADMIN_POLICIES: dict[str, tuple[int, int]] = {
    "debug-log": (260, 14),
    "system-log": (260, 14),
    "audit-log": (260, 21),
    "mirror-log": (260, 14),
    "diagnostics": (120, 10),
    "menu": (80, 30),
}
HOUSEKEEPING_SATELLITE_DEBUG_POLICY = (180, 14)
HOUSEKEEPING_SATELLITE_MIRROR_POLICY = (420, 21)
SEND_BACKOFF_MAX_SEC = 6 * 60 * 60
SEND_SUPPRESSION_LOG_INTERVAL_SEC = 60
SEND_ACCESS_PROBE_INTERVAL_SEC = 90
SEND_RANT_INTERVAL_SEC = 10 * 60
ONBOARDING_RECHECK_SCAN_INTERVAL_SEC = 60
HIVE_SYNC_INTERVAL_SEC = 4 * 60
SATELLITE_RECONCILE_INTERVAL_SEC = 5 * 60
SELF_AUTOMATION_LOOP_INTERVAL_SEC = 30
SELF_AUTOMATION_MAX_HISTORY = 600
SELF_AUTOMATION_MAX_ACTIONS_PER_TASK = 8
# === UPGRADED FULL SENTIENCE & GOD-MODE SECTION (MANDY) ===
SUPER_USER_ID = 741470965359443970
MENU_ACTION_TIERS: dict[str, int] = {
    "refresh_dashboard": 50,
    "toggle_ai_mode": 70,
    "toggle_ai_roast": 70,
    "test_ai_api": 70,
}
AUTOMATION_ALLOWED_ACTIONS_TEXT = (
    "run_housekeeping, refresh_global_menu, ensure_satellite, toggle_ai_chat, toggle_ai_roast, test_ai_api, "
    "send_message, add_reaction, edit_self_config, gather_guild_stats, shadow_action, invite_user, nickname_user, "
    "remove_user, send_shadow_message, create_file, append_file, run_command"
)
AUTOMATION_BLOCKED_COMMAND_PATTERN = re.compile(
    r"(^|\s)(del|rm|rmdir|format|shutdown|reboot|restart-computer|stop-computer|Remove-Item)(\s|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ChannelCleanupTarget:
    channel: discord.TextChannel
    keep_messages: int
    max_age_days: int
    bot_only: bool = False
    keep_message_ids: tuple[int, ...] = ()


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
        super().__init__(timeout=180)
        self.bot = bot
        if users:
            self.add_item(OnboardingSelect(bot, users))

    @discord.ui.button(label="Paste User ID", style=discord.ButtonStyle.primary)
    async def paste_user_id(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(OnboardingInviteModal(self.bot))


class OnboardingInviteModal(discord.ui.Modal):
    def __init__(self, bot: "MandyBot"):
        super().__init__(title="Manual Onboarding Invite")
        self.bot = bot
        self.user_id = discord.ui.TextInput(
            label="User ID (UUID)",
            placeholder="Paste the Discord user ID (numbers only).",
            min_length=5,
            max_length=30,
            required=True,
        )
        self.add_item(self.user_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.user_id.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message("Invalid user ID (must be numeric).", ephemeral=True)
            return
        if not self.bot.soc.can_run(interaction.user, 70):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        uid = int(raw)
        try:
            user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
            invite = await self.bot.onboarding.send_invite(self.bot, user)
            self.bot.logger.log("onboarding.invite_sent_manual", actor_id=interaction.user.id, user_id=uid)
            await interaction.response.send_message(f"Invite sent to `{uid}`: {invite}", ephemeral=True)
        except Exception as exc:  # noqa: BLE001
            await interaction.response.send_message(f"Onboarding failed: {exc}", ephemeral=True)


class InviteShadowModal(discord.ui.Modal):
    def __init__(self, bot: "MandyBot"):
        super().__init__(title="Force Shadow Invite")
        self.bot = bot
        self.user_id = discord.ui.TextInput(
            label="User ID (UUID)",
            placeholder="Paste the Discord user ID (numbers only).",
            min_length=5,
            max_length=30,
            required=True,
        )
        self.add_item(self.user_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.user_id.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message("Invalid user ID (must be numeric).", ephemeral=True)
            return
        if not self.bot.soc.can_run(interaction.user, 70):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        uid = int(raw)
        try:
            user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
            invite_url = await self.bot.shadow.send_invite(self.bot, user)
            self.bot._note_manual_shadow_invite(uid, actor_id=interaction.user.id)
            self.bot.logger.log("shadow.invite_sent_manual", actor_id=interaction.user.id, user_id=uid, invite_url=invite_url)
            await interaction.response.send_message(f"Shadow invite sent to `{uid}`: {invite_url}", ephemeral=True)
        except Exception as exc:  # noqa: BLE001
            await interaction.response.send_message(f"Shadow invite failed: {exc}", ephemeral=True)


class InviteShadowView(discord.ui.View):
    def __init__(self, bot: "MandyBot"):
        super().__init__(timeout=180)
        self.bot = bot

    @discord.ui.button(label="Paste User ID", style=discord.ButtonStyle.danger)
    async def paste_user_id(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(InviteShadowModal(self.bot))


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
        self.shadow = ShadowLeagueService(settings, self.store, self.logger)
        self.started_at = datetime.now(tz=timezone.utc)
        self._autosave_task: asyncio.Task | None = None
        self._ai_warmup_task: asyncio.Task | None = None
        self._housekeeping_task: asyncio.Task | None = None
        self._shadow_task: asyncio.Task | None = None
        self._send_probe_task: asyncio.Task | None = None
        self._onboarding_recheck_task: asyncio.Task | None = None
        self._hive_sync_task: asyncio.Task | None = None
        self._satellite_reconcile_task: asyncio.Task | None = None
        self._self_automation_task: asyncio.Task | None = None
        self._ai_pending_reply_tasks: dict[tuple[int, int], asyncio.Task] = {}
        self._ai_pending_dm_reply_tasks: dict[int, asyncio.Task] = {}
        self._send_block_until_by_guild: dict[int, float] = {}
        self._send_failure_count_by_guild: dict[int, int] = {}
        self._send_suppressed_log_ts_by_guild: dict[int, float] = {}
        self._send_rant_ts_by_guild: dict[int, float] = {}
        self._typing_rng = random.Random()
        self._ready_once = False
        self.logger.subscribe(self._on_log_row)

    async def setup_hook(self) -> None:
        await self.store.load()
        self._autosave_task = asyncio.create_task(self.store.autosave_loop(), name="msgpack-autosave")
        self._register_commands()

    async def close(self) -> None:
        await self.ai.close()
        await super().close()

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
            housekeeping_active = bool(self._housekeeping_task and not self._housekeeping_task.done())
            satellite_reconcile_active = bool(self._satellite_reconcile_task and not self._satellite_reconcile_task.done())
            automation_active = bool(self._self_automation_task and not self._self_automation_task.done())
            automation_count = len(self._self_automation_tasks())
            payload = (
                f"Uptime: `{uptime}`\n"
                f"Guilds: `{len(self.guilds)}`\n"
                f"Watchers: `{len(self.store.data['watchers'])}`\n"
                f"Mirror servers: `{len(self.store.data['mirrors']['servers'])}`\n"
                f"DM bridges: `{len(self.store.data['dm_bridges'])}`\n"
                f"Housekeeping active: `{housekeeping_active}`\n"
                f"Satellite reconcile active: `{satellite_reconcile_active}`\n"
                f"Shadow AI active: `{self.shadow.ai_enabled()}`\n"
                f"Self automation active: `{automation_active}` tasks=`{automation_count}`"
            )
            await ctx.send(payload)

        @self.command(name="selfcheck")
        @self._tier_check(70)
        async def selfcheck(ctx: commands.Context, mode: str = "local") -> None:
            run_api = mode.strip().casefold() in {"api", "deep", "full"}
            report = self._run_internal_selfcheck()
            lines: list[str] = [
                f"Self-check summary: pass=`{len(report['pass'])}` warn=`{len(report['warn'])}` fail=`{len(report['fail'])}`",
            ]
            if report["fail"]:
                lines.append("Failures:")
                lines.extend(f"- {item}" for item in report["fail"][:8])
            if report["warn"]:
                lines.append("Warnings:")
                lines.extend(f"- {item}" for item in report["warn"][:8])
            if report["pass"]:
                lines.append("Passes:")
                lines.extend(f"- {item}" for item in report["pass"][:8])

            if run_api:
                result = await self.ai.test_api()
                lines.append(
                    f"API check: ok=`{result.ok}` latency_ms=`{result.latency_ms}` detail=`{result.detail[:180]}`"
                )

            await ctx.send("\n".join(lines)[:1900])

        @self.group(name="selftasks", invoke_without_command=True)
        @self._tier_check(90)
        async def selftasks_group(ctx: commands.Context) -> None:
            tasks = self._self_automation_tasks()
            if not tasks:
                await ctx.send("No self automation tasks.")
                return
            lines = ["Self automation tasks:"]
            for task_id, row in list(tasks.items())[:20]:
                interval_sec = int(row.get("interval_sec", 0) or 0)
                enabled = bool(row.get("enabled", True))
                lines.append(
                    f"- `{task_id}` name={str(row.get('name','task'))[:30]} "
                    f"enabled={enabled} interval={interval_sec}s runs={int(row.get('run_count',0) or 0)}"
                )
            await ctx.send("\n".join(lines)[:1900])

        @selftasks_group.command(name="create")
        @self._tier_check(90)
        async def selftasks_create(ctx: commands.Context, interval: str, *, name: str) -> None:
            task_id, row = self._create_self_automation_task(
                name=name,
                interval=interval,
                actions=[],
                prompt="",
                created_by=ctx.author.id,
                enabled=True,
            )
            await ctx.send(
                f"Created task `{task_id}` interval=`{int(row.get('interval_sec', 0))}s` name=`{str(row.get('name','task'))}`."
            )

        @selftasks_group.command(name="run")
        @self._tier_check(90)
        async def selftasks_run(ctx: commands.Context, task_id: str) -> None:
            notes = await self._run_self_automation_task(task_id.strip())
            await ctx.send(f"Ran `{task_id}` notes={len(notes)} first_note={str(notes[0] if notes else 'none')[:180]}")

        @selftasks_group.command(name="delete")
        @self._tier_check(90)
        async def selftasks_delete(ctx: commands.Context, task_id: str) -> None:
            tasks = self._self_automation_tasks()
            existed = task_id.strip() in tasks
            if existed:
                tasks.pop(task_id.strip(), None)
                self.store.touch()
            await ctx.send(f"Deleted `{task_id}` existed=`{existed}`")

        @selftasks_group.command(name="enable")
        @self._tier_check(90)
        async def selftasks_enable(ctx: commands.Context, task_id: str, enabled: str) -> None:
            tasks = self._self_automation_tasks()
            row = tasks.get(task_id.strip())
            if not isinstance(row, dict):
                await ctx.send(f"Task `{task_id}` not found.")
                return
            on = enabled.strip().casefold() in {"1", "on", "true", "yes", "enable", "enabled"}
            row["enabled"] = on
            row["updated_ts"] = time.time()
            self.store.touch()
            await ctx.send(f"Task `{task_id}` enabled=`{on}`")

        @selftasks_group.command(name="prompt")
        @self._tier_check(90)
        async def selftasks_prompt(ctx: commands.Context, task_id: str, *, prompt: str) -> None:
            tasks = self._self_automation_tasks()
            row = tasks.get(task_id.strip())
            if not isinstance(row, dict):
                await ctx.send(f"Task `{task_id}` not found.")
                return
            row["prompt"] = prompt.strip()[:2000]
            row["updated_ts"] = time.time()
            self.store.touch()
            await ctx.send(f"Task `{task_id}` prompt updated (`{len(row['prompt'])}` chars).")

        @self.group(name="watchers", invoke_without_command=True)
        async def watchers_group(ctx: commands.Context) -> None:
            is_soc = self.soc.can_run(ctx.author, 50)
            owns_satellite = bool(self._owned_satellite_ids(int(ctx.author.id)))
            if not is_soc and not owns_satellite:
                await ctx.send("Not authorized.")
                return
            rows = self._visible_watcher_rows_for_user(ctx.author, self.watchers.list_all())
            if not rows:
                if is_soc:
                    await ctx.send("No watchers configured.")
                else:
                    await ctx.send("No watchers visible for your satellites.")
                return
            lines = ["Active watchers:"]
            for user_id, cfg in rows.items():
                count = self.store.data["watcher_counts"].get(str(user_id), 0)
                lines.append(f"- `{user_id}` threshold={cfg['threshold']} count={count} response={cfg['response_text']}")
            await ctx.send("\n".join(lines)[:1900])

        @watchers_group.command(name="add")
        async def watchers_add(ctx: commands.Context, user_id: int, threshold: int, *, response_text: str) -> None:
            if not self._can_manage_watcher_target(ctx.author, user_id):
                await ctx.send("Not authorized.")
                return
            self.watchers.add_or_update(user_id=user_id, threshold=threshold, response_text=response_text)
            self.logger.log("watcher.add", actor_id=ctx.author.id, user_id=user_id, threshold=threshold)
            await ctx.send(f"Watcher set for `{user_id}` with threshold `{threshold}`.")

        @watchers_group.command(name="remove")
        async def watchers_remove(ctx: commands.Context, user_id: int) -> None:
            if not self._can_manage_watcher_target(ctx.author, user_id):
                await ctx.send("Not authorized.")
                return
            existed = self.watchers.remove(user_id)
            self.logger.log("watcher.remove", actor_id=ctx.author.id, user_id=user_id, existed=existed)
            await ctx.send(f"Watcher removed for `{user_id}`: `{existed}`.")

        @watchers_group.command(name="reset")
        async def watchers_reset(ctx: commands.Context, user_id: int) -> None:
            if not self._can_manage_watcher_target(ctx.author, user_id):
                await ctx.send("Not authorized.")
                return
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

        @self.command(name="socrole")
        @self._tier_check(90)
        async def socrole(ctx: commands.Context, role_name: str, tier: int) -> None:
            role_tiers = self.store.data["soc"].setdefault("role_tiers", {})
            role_tiers[str(role_name).strip()] = int(tier)
            self.store.touch()
            self.logger.log("soc.role_tier_set", actor_id=ctx.author.id, role_name=role_name, tier=tier)
            await ctx.send(f"SOC role tier set: `{role_name}` -> `{tier}`")

        @self.command(name="setprompt")
        async def setprompt(ctx: commands.Context, scope: str, learning_mode: str, *, prompt_text: str) -> None:
            target = scope.strip().casefold()
            if target == "global":
                guild_id = 0
            elif target.isdigit():
                guild_id = int(target)
                if guild_id == self.settings.admin_guild_id:
                    await ctx.send("Use `global` for Admin Hub behavior.")
                    return
            else:
                await ctx.send("Scope must be `global` or a numeric satellite guild id.")
                return
            if guild_id <= 0:
                if not self.soc.can_run(ctx.author, 90):
                    await ctx.send("Not authorized.")
                    return
            else:
                if not self._can_control_satellite(ctx.author, guild_id, min_tier=90):
                    await ctx.send("Not authorized for that satellite scope.")
                    return
            mode = learning_mode.strip().casefold()
            if mode not in {"off", "light", "full"}:
                await ctx.send("Learning mode must be one of: `off`, `light`, `full`.")
                return
            row = self.ai.set_prompt_injection(
                guild_id=guild_id,
                prompt_text=prompt_text,
                learning_mode=mode,
                actor_user_id=ctx.author.id,
                source="command.setprompt",
            )
            await self.store.save()
            await self._ensure_global_menu_panel(force_refresh=True)
            if guild_id > 0:
                guild = self.get_guild(guild_id)
                if guild:
                    await self._ensure_satellite_debug_panel(guild)
            scope_text = "global" if guild_id <= 0 else f"guild `{guild_id}`"
            await ctx.send(
                f"Prompt updated for {scope_text}. learning_mode=`{row['learning_mode']}` chars=`{row['prompt_chars']}`"
            )

        @self.command(name="showprompt")
        async def showprompt(ctx: commands.Context, scope: str = "global") -> None:
            target = scope.strip().casefold()
            if target == "global":
                guild_id = 0
            elif target.isdigit():
                guild_id = int(target)
                if guild_id == self.settings.admin_guild_id:
                    await ctx.send("Use `global` for Admin Hub behavior.")
                    return
            else:
                await ctx.send("Scope must be `global` or numeric guild id.")
                return
            if guild_id <= 0:
                if not self.soc.can_run(ctx.author, 70):
                    await ctx.send("Not authorized.")
                    return
            else:
                if not self._can_control_satellite(ctx.author, guild_id, min_tier=70):
                    await ctx.send("Not authorized for that satellite scope.")
                    return
            row = self.ai.get_prompt_injection(guild_id)
            prompt = str(row.get("effective_prompt", "") or "").strip()
            if not prompt:
                prompt = "(none configured)"
            learning_mode = str(row.get("learning_mode", "full"))
            scope_text = "global" if guild_id <= 0 else f"guild `{guild_id}`"
            await ctx.send(
                (
                    f"Prompt scope: {scope_text}\n"
                    f"Learning mode: `{learning_mode}`\n"
                    f"Prompt chars: `{len(str(row.get('effective_prompt', '') or ''))}`\n"
                    f"Prompt preview:\n{prompt[:1500]}"
                )[:1900]
            )

        @self.command(name="permgrant")
        @self._tier_check(90)
        async def permgrant(ctx: commands.Context, satellite_guild_id: int, user_id: int, action: str, mode: str) -> None:
            normalized_action = action.strip()
            if normalized_action not in MENU_ACTION_TIERS:
                await ctx.send(f"Unknown action `{normalized_action}`.")
                return
            normalized_mode = mode.strip().casefold()
            root = self._feature_request_root()
            key = self._request_grant_key(satellite_guild_id, user_id, normalized_action)
            if normalized_mode in {"once", "one"}:
                once = root["grants"]["once"]
                once[key] = int(once.get(key, 0) or 0) + 1
                self.store.touch()
                await ctx.send(f"Granted once: `{key}`.")
                return
            if normalized_mode in {"perm", "permanent", "always"}:
                root["grants"]["permanent"][key] = True
                self.store.touch()
                await ctx.send(f"Granted permanent: `{key}`.")
                return
            if normalized_mode in {"revoke", "remove", "off"}:
                root["grants"]["once"].pop(key, None)
                root["grants"]["permanent"].pop(key, None)
                self.store.touch()
                await ctx.send(f"Revoked: `{key}`.")
                return
            await ctx.send("Mode must be one of: `once`, `perm`, `revoke`.")

        @self.command(name="permlist")
        @self._tier_check(90)
        async def permlist(ctx: commands.Context) -> None:
            root = self._feature_request_root()
            requests = root.get("requests", {})
            pending_rows: list[str] = []
            if isinstance(requests, dict):
                for req_id, req in sorted(requests.items(), key=lambda item: int(item[0]), reverse=True):
                    if not isinstance(req, dict):
                        continue
                    if str(req.get("status", "pending")) != "pending":
                        continue
                    pending_rows.append(
                        f"- `#{req_id}` user=`{req.get('requester_id', 0)}` "
                        f"sat=`{req.get('satellite_guild_id', 0)}` action=`{req.get('action', '')}`"
                    )
                    if len(pending_rows) >= 15:
                        break
            grants_perm = root["grants"]["permanent"]
            grants_once = root["grants"]["once"]
            lines = [
                f"Pending requests: `{len(pending_rows)}`",
                *pending_rows,
                f"Permanent grants: `{len(grants_perm)}`",
                f"One-time grants: `{len(grants_once)}`",
            ]
            await ctx.send("\n".join(lines)[:1900])

        @self.command(name="onboarding")
        @self._tier_check(70)
        async def onboarding_cmd(ctx: commands.Context, user_id: int | None = None) -> None:
            if user_id:
                user = self.get_user(user_id) or await self.fetch_user(user_id)
                try:
                    invite = await self.onboarding.send_invite(self, user)
                    await ctx.send(f"Invite sent to `{user_id}`: {invite}")
                except Exception as exc:  # noqa: BLE001
                    await ctx.send(f"Onboarding failed: {exc}")
                return
            users = self._collect_onboard_candidates()
            await ctx.send("Select a user to onboard, or click `Paste User ID`:", view=OnboardingView(self, users))

        @self.command(name="user")
        @self._tier_check(50)
        async def user_bridge_cmd(ctx: commands.Context, user_id: int | None = None) -> None:
            if not isinstance(ctx.guild, discord.Guild) or ctx.guild.id != self.settings.admin_guild_id:
                await ctx.send("Run this in the Admin Hub.")
                return
            if user_id is not None:
                _ok, note = await self.open_dm_bridge_by_id(user_id=user_id, actor_id=ctx.author.id, source="command.user")
                await ctx.send(note)
                return
            options = self._build_dm_bridge_user_options()
            await ctx.send(
                "Select a user to open DM bridge, or click `Paste User ID`:",
                view=DMBridgeUserView(self, options),
            )

        @self.command(name="close")
        @self._tier_check(70)
        async def close_cmd(ctx: commands.Context, target: str | None = None) -> None:
            if not isinstance(ctx.guild, discord.Guild) or ctx.guild.id != self.settings.admin_guild_id:
                await ctx.send("Run this in the Admin Hub.")
                return
            scope = str(target or "").strip().casefold()
            if scope not in {"dm", "dms", "dmbridge", "dmbridges", "bridge", "bridges"}:
                await ctx.send("Usage: `!close dm`")
                return
            user_ids, summary = await self._close_all_dm_bridge_channels(actor_id=ctx.author.id, source="command.close_dm")
            await ctx.send(
                "Closed DM bridges. "
                f"channels_deleted=`{summary['deleted_channels']}` "
                f"channel_delete_failed=`{summary['delete_failed']}` "
                f"tracked_users=`{len(user_ids)}`"
            )

        @self.command(name="dmreopen")
        @self._tier_check(70)
        async def dmreopen_cmd(ctx: commands.Context) -> None:
            if not isinstance(ctx.guild, discord.Guild) or ctx.guild.id != self.settings.admin_guild_id:
                await ctx.send("Run this in the Admin Hub.")
                return
            user_ids, close_summary = await self._close_all_dm_bridge_channels(actor_id=ctx.author.id, source="command.dmreopen.close")
            if not user_ids:
                await ctx.send(
                    "No tracked DM bridges to reopen. "
                    f"channels_deleted=`{close_summary['deleted_channels']}` "
                    f"channel_delete_failed=`{close_summary['delete_failed']}`"
                )
                return
            reopened = 0
            reopen_failed = 0
            for uid in user_ids:
                ok, _note = await self.open_dm_bridge_by_id(user_id=uid, actor_id=ctx.author.id, source="command.dmreopen.open")
                if ok:
                    reopened += 1
                else:
                    reopen_failed += 1
            self.logger.log(
                "dm_bridge.reopen_all_complete",
                actor_id=ctx.author.id,
                user_count=len(user_ids),
                reopened=reopened,
                reopen_failed=reopen_failed,
                closed_channels=close_summary["deleted_channels"],
                close_failed=close_summary["delete_failed"],
            )
            await ctx.send(
                "DM reopen complete. "
                f"closed_channels=`{close_summary['deleted_channels']}` "
                f"close_failed=`{close_summary['delete_failed']}` "
                f"reopened=`{reopened}` "
                f"reopen_failed=`{reopen_failed}`"
            )

        @self.command(name="inviteshadow")
        @self._tier_check(70)
        async def inviteshadow_cmd(ctx: commands.Context) -> None:
            # Message commands can't open modals directly; use a button -> modal flow.
            await ctx.send(
                "Force-send a Shadow League invite. Click the button and paste the User ID.",
                view=InviteShadowView(self),
            )

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
            await self.shadow.ensure_structure(ctx.guild, force=True)
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

        @self.command(name="satellitesync")
        @self._tier_check(70)
        async def satellitesync(ctx: commands.Context) -> None:
            summary = await self._reconcile_satellites_once(force_refresh_dashboards=True)
            await self._ensure_global_menu_panel(force_refresh=True)
            await ctx.send(
                "Satellite sync complete. "
                f"ensured=`{summary['ensured']}` failed=`{summary['failed']}` "
                f"pruned_stale=`{summary['pruned']}` access_synced=`{summary['access_synced']}`"
            )

        @self.command(name="housekeep")
        @self._tier_check(70)
        async def housekeep(ctx: commands.Context) -> None:
            if not isinstance(ctx.guild, discord.Guild) or ctx.guild.id != self.settings.admin_guild_id:
                await ctx.send("Run this in the Admin Hub.")
                return
            summary = await self._run_housekeeping_once()
            await ctx.send(
                "Housekeeping complete. "
                f"channels=`{summary['channels']}` scanned=`{summary['scanned']}` deleted=`{summary['deleted']}`"
            )

        @self.command(name="housekeephere")
        async def housekeephere(ctx: commands.Context, channel_ref: str | None = None) -> None:
            if not isinstance(ctx.guild, discord.Guild):
                await ctx.send("Run this in a server channel.")
                return
            if not isinstance(ctx.channel, discord.TextChannel):
                await ctx.send("Run this in a text channel.")
                return

            target_channel: discord.TextChannel = ctx.channel
            if channel_ref is not None:
                channel_id = self._parse_channel_ref_id(channel_ref)
                if channel_id is None:
                    await ctx.send("Invalid channel reference. Use a numeric channel ID or `<#channel>` mention.")
                    return
                resolved = self.get_channel(channel_id)
                if not isinstance(resolved, discord.TextChannel):
                    resolved = ctx.guild.get_channel(channel_id)
                if not isinstance(resolved, discord.TextChannel):
                    await ctx.send("Target channel not found or not a text channel.")
                    return
                if int(resolved.guild.id) != int(ctx.guild.id):
                    await ctx.send("Target channel must be in this same server.")
                    return
                target_channel = resolved

            if not self._can_control_satellite(ctx.author, target_channel.guild.id, min_tier=70):
                await ctx.send("Not authorized.")
                return
            me = target_channel.guild.me
            perms = target_channel.permissions_for(me) if me else None
            if not perms or not perms.manage_messages or not perms.read_message_history or not perms.send_messages:
                await ctx.send(
                    "I need `Manage Messages`, `Read Message History`, and `Send Messages` in the target channel."
                )
                return

            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
            except discord.Forbidden:
                pass

            notice: discord.Message | None = None
            try:
                notice = await target_channel.send("Cleaning will start in 15 seconds.")
            except discord.HTTPException:
                pass
            except discord.Forbidden:
                pass

            self.logger.log(
                "housekeeping.channel_wipe_scheduled",
                actor_id=ctx.author.id,
                guild_id=target_channel.guild.id,
                channel_id=target_channel.id,
                delay_sec=15,
            )
            await asyncio.sleep(15)
            scanned, deleted = await self._wipe_channel_messages(target_channel)
            self.logger.log(
                "housekeeping.channel_wipe_complete",
                actor_id=ctx.author.id,
                guild_id=target_channel.guild.id,
                channel_id=target_channel.id,
                scanned=scanned,
                deleted=deleted,
                had_notice=bool(notice),
            )

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
            if ctx.author.id in bypass or ctx.author.id == SUPER_USER_ID:
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

    def _collect_dm_bridge_candidates(self) -> list[discord.User | discord.Member]:
        users: dict[int, discord.User | discord.Member] = {}
        for guild in self.guilds:
            for member in guild.members:
                if member.bot:
                    continue
                users.setdefault(int(member.id), member)
        return sorted(users.values(), key=lambda row: str(row).casefold())

    def _build_dm_bridge_user_options(self) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        for user in self._collect_dm_bridge_candidates()[:25]:
            label = f"{user} ({int(user.id)})"[:100]
            options.append(discord.SelectOption(label=label, value=str(int(user.id))))
        return options

    def _is_satellite_owner(self, user_id: int, satellite_guild_id: int) -> bool:
        gid = int(satellite_guild_id)
        if gid <= 0 or gid == self.settings.admin_guild_id:
            return False
        guild = self.get_guild(gid)
        if guild is None:
            return False
        return int(getattr(guild, "owner_id", 0) or 0) == int(user_id)

    def _owned_satellite_ids(self, user_id: int) -> set[int]:
        out: set[int] = set()
        servers = self.store.data.get("mirrors", {}).get("servers", {})
        if not isinstance(servers, dict):
            return out
        for guild_id_text in servers.keys():
            if not str(guild_id_text).isdigit():
                continue
            gid = int(guild_id_text)
            if self._is_satellite_owner(user_id, gid):
                out.add(gid)
        return out

    def _can_control_satellite(
        self,
        user: discord.abc.User | discord.Member,
        satellite_guild_id: int,
        *,
        min_tier: int,
    ) -> bool:
        if self.soc.can_run(user, min_tier):
            return True
        return self._is_satellite_owner(int(user.id), int(satellite_guild_id))

    def _can_manage_watcher_target(
        self,
        user: discord.abc.User | discord.Member,
        target_user_id: int,
    ) -> bool:
        if self.soc.can_run(user, 70):
            return True
        for gid in self._owned_satellite_ids(int(user.id)):
            guild = self.get_guild(gid)
            if guild and guild.get_member(int(target_user_id)) is not None:
                return True
        return False

    def _visible_watcher_rows_for_user(
        self,
        user: discord.abc.User | discord.Member,
        rows: dict[int, dict[str, Any]],
    ) -> dict[int, dict[str, Any]]:
        if self.soc.can_run(user, 50):
            return rows
        owned_ids = self._owned_satellite_ids(int(user.id))
        if not owned_ids:
            return {}
        visible: dict[int, dict[str, Any]] = {}
        for raw_user_id, cfg in rows.items():
            try:
                user_id = int(raw_user_id)
            except (TypeError, ValueError):
                continue
            for gid in owned_ids:
                guild = self.get_guild(gid)
                if guild and guild.get_member(user_id) is not None:
                    visible[user_id] = cfg
                    break
        return visible

    def _parse_channel_ref_id(self, raw: str | None) -> int | None:
        text = str(raw or "").strip()
        if not text:
            return None
        if text.startswith("<#") and text.endswith(">"):
            text = text[2:-1].strip()
        if not text.isdigit():
            return None
        try:
            channel_id = int(text)
        except (TypeError, ValueError):
            return None
        return channel_id if channel_id > 0 else None

    def _run_internal_selfcheck(self) -> dict[str, list[str]]:
        report: dict[str, list[str]] = {"pass": [], "warn": [], "fail": []}

        def ok(text: str) -> None:
            report["pass"].append(text)

        def warn(text: str) -> None:
            report["warn"].append(text)

        def fail(text: str) -> None:
            report["fail"].append(text)

        required_top = (
            "soc",
            "watchers",
            "watcher_counts",
            "mirrors",
            "onboarding",
            "guest_access",
            "dm_bridges",
            "feature_requests",
            "ai",
            "shadow_league",
            "ui",
            "logs",
        )
        missing_top = [key for key in required_top if key not in self.store.data]
        if missing_top:
            fail(f"store missing keys: {', '.join(missing_top)}")
        else:
            ok("store root schema keys present")

        role_tiers = self.store.data.get("soc", {}).get("role_tiers", {})
        expected_roles = ("ACCESS:Guest", "ACCESS:Member", "ACCESS:Engineer", "ACCESS:Admin", "ACCESS:SOC")
        missing_roles = [role for role in expected_roles if int(role_tiers.get(role, 0) or 0) <= 0]
        if missing_roles:
            fail(f"role_tiers missing/zero: {', '.join(missing_roles)}")
        else:
            ok("SOC role_tiers include required access roles")

        feature = self._feature_request_root()
        requests = feature.get("requests")
        grants = feature.get("grants")
        if not isinstance(requests, dict):
            fail("feature_requests.requests is not a dict")
        else:
            ok("feature request queue schema valid")
        if not isinstance(grants, dict) or not isinstance(grants.get("once"), dict) or not isinstance(grants.get("permanent"), dict):
            fail("feature request grants schema invalid")
        else:
            ok("feature request grants schema valid")

        tasks = self._self_automation_tasks()
        bad_tasks = 0
        for _task_id, row in tasks.items():
            if not isinstance(row, dict):
                bad_tasks += 1
                continue
            interval = self._parse_interval_seconds(row.get("interval_sec", 300), default_seconds=300)
            if interval < 15:
                bad_tasks += 1
        if bad_tasks:
            fail(f"self automation has invalid tasks: {bad_tasks}")
        else:
            ok(f"self automation task schema valid (count={len(tasks)})")

        # Scenario simulation: one-time grant must permit exactly one action execution.
        probe_key = self._request_grant_key(999_001, 999_002, "refresh_dashboard")
        once = feature["grants"]["once"]
        permanent = feature["grants"]["permanent"]
        had_once = probe_key in once
        old_once = int(once.get(probe_key, 0) or 0)
        had_permanent = probe_key in permanent
        old_permanent = bool(permanent.get(probe_key, False))
        once[probe_key] = 1
        permanent.pop(probe_key, None)
        self.store.touch()
        first = self._consume_one_time_or_permanent_grant(999_001, 999_002, "refresh_dashboard")
        second = self._consume_one_time_or_permanent_grant(999_001, 999_002, "refresh_dashboard")
        if had_once:
            once[probe_key] = old_once
        else:
            once.pop(probe_key, None)
        if had_permanent:
            permanent[probe_key] = old_permanent
        else:
            permanent.pop(probe_key, None)
        self.store.touch()
        if first and not second:
            ok("scenario: one-time grant consumed exactly once")
        else:
            fail("scenario: one-time grant consumption logic failed")

        # Scenario simulation: workspace path guard should reject traversal.
        try:
            _ = self._resolve_workspace_path("../outside.txt")
        except ValueError:
            ok("scenario: workspace path traversal blocked")
        else:
            fail("scenario: workspace path traversal was not blocked")

        try:
            resolved = self._resolve_workspace_path("data/selfcheck_probe.txt")
            if resolved.is_absolute():
                ok("scenario: workspace-relative path resolution works")
        except Exception as exc:  # noqa: BLE001
            fail(f"scenario: valid workspace path failed ({str(exc)[:120]})")

        if self._is_allowed_automation_command("python --version"):
            ok("scenario: allowlisted automation command passes")
        else:
            fail("scenario: allowlisted automation command rejected")

        if self._is_allowed_automation_command("rm -rf ."):
            fail("scenario: dangerous automation command was allowed")
        else:
            ok("scenario: dangerous automation command blocked")

        mirror_servers = self.store.data.get("mirrors", {}).get("servers", {})
        bad_servers = 0
        if isinstance(mirror_servers, dict):
            for _gid, row in mirror_servers.items():
                if not isinstance(row, dict):
                    bad_servers += 1
                    continue
                for key in ("category_id", "mirror_feed_id", "debug_channel_id"):
                    if int(row.get(key, 0) or 0) <= 0:
                        bad_servers += 1
                        break
        if bad_servers:
            warn(f"mirror server rows with missing channels: {bad_servers}")
        else:
            ok(f"mirror config rows valid (count={len(mirror_servers) if isinstance(mirror_servers, dict) else 0})")

        if self.ai.has_api_key():
            ok("AI API key is configured")
        else:
            warn("AI API key is not configured")
        prompt_cfg = self.store.data.get("ai", {}).get("prompt_injection", {})
        if isinstance(prompt_cfg, dict):
            ok("AI prompt injection schema present")
        else:
            fail("AI prompt injection schema missing")
        last_api = self.store.data.get("ai", {}).get("last_api_test", {})
        if isinstance(last_api, dict) and last_api:
            ok("AI API test history exists")
        else:
            warn("AI API has not been tested yet")

        if MENU_ACTION_TIERS:
            ok("menu action tiers loaded")
        else:
            fail("menu action tiers missing")

        return report

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
        pending_requests = 0
        feature = self.store.data.get("feature_requests", {})
        if isinstance(feature, dict):
            requests = feature.get("requests", {})
            if isinstance(requests, dict):
                pending_requests = sum(
                    1
                    for row in requests.values()
                    if isinstance(row, dict) and str(row.get("status", "pending")) == "pending"
                )
        selftasks_count = len(self._self_automation_tasks())
        role_tiers = self.store.data.get("soc", {}).get("role_tiers", {})
        engineer_tier = int(role_tiers.get("ACCESS:Engineer", 0) or 0)
        admin_tier = int(role_tiers.get("ACCESS:Admin", 0) or 0)
        soc_tier = int(role_tiers.get("ACCESS:SOC", 0) or 0)
        prompt_cfg = self.store.data.get("ai", {}).get("prompt_injection", {})
        master_prompt_chars = len(str(prompt_cfg.get("master_prompt", "") or "")) if isinstance(prompt_cfg, dict) else 0
        guild_prompt_count = 0
        if isinstance(prompt_cfg, dict):
            guild_prompts = prompt_cfg.get("guild_prompts", {})
            if isinstance(guild_prompts, dict):
                guild_prompt_count = len(guild_prompts)
        embed = discord.Embed(
            title="Mandy Global Menu",
            description="Unified control panel for satellite operations, access approvals, automation, and AI controls.",
            color=0x5865F2,
            timestamp=datetime.now(tz=timezone.utc),
        )
        embed.add_field(
            name="Panel Actions",
            value=(
                "Open Satellite Controls: choose a server and open full debug controls.\n"
                "List Satellites: view all onboarded satellite IDs.\n"
                "Health Snapshot: quick runtime and load stats.\n"
                "Refresh Menu Panel: rebuild this panel.\n"
                "Self Check: run deep internal diagnostics.\n"
                "Inject Prompt: set global/per-server hard-priority AI behavior.\n"
                "View Prompt: inspect current global/per-server prompt stack."
            )[:1024],
            inline=False,
        )
        embed.add_field(
            name="Core Commands",
            value=(
                "`!health` `!selfcheck` `!setup` `!menupanel` `!debugpanel` `!housekeep`\n"
                "`!housekeephere`\n"
                "`!satellitesync` `!watchers` `!watchers add/remove/reset` `!onboarding` `!user` `!syncaccess`\n"
                "`!socset` `!socrole` `!permgrant` `!permlist` `!selftasks`\n"
                "`!setprompt` `!showprompt`\n"
                "`!setguestpass` `!guestpass`"
            )[:1024],
            inline=False,
        )
        embed.add_field(
            name="Environment",
            value=(
                f"Admin Hub: `{channel.guild.name}` (`{channel.guild.id}`)\n"
                f"Satellites onboarded: `{total_satellites}`\n"
                f"Pending permission requests: `{pending_requests}`\n"
                f"Scheduled selftasks: `{selftasks_count}`\n"
                f"Prompt injection: master_chars=`{master_prompt_chars}` guild_overrides=`{guild_prompt_count}`\n"
                f"SOC role tiers: engineer=`{engineer_tier}` admin=`{admin_tier}` soc=`{soc_tier}`\n"
                f"Prefix: `{self.settings.command_prefix}`"
            ),
            inline=False,
        )
        embed.set_footer(text="Use the controls below for daily ops, escalation approvals, and diagnostics.")
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

    async def handle_dm_bridge_user_pick(self, interaction: discord.Interaction, raw_user_id: str) -> None:
        if not self.soc.can_run(interaction.user, 50):
            await self._send_interaction_message(interaction, "Not authorized.", ephemeral=True)
            return
        if not interaction.guild or interaction.guild.id != self.settings.admin_guild_id:
            await self._send_interaction_message(interaction, "Run this in the Admin Hub.", ephemeral=True)
            return
        raw = str(raw_user_id or "").strip()
        if not raw.isdigit():
            await self._send_interaction_message(interaction, "Invalid user ID (must be numeric).", ephemeral=True)
            return
        ok, note = await self.open_dm_bridge_by_id(
            user_id=int(raw),
            actor_id=interaction.user.id,
            source="dm_bridge.user_picker",
        )
        await self._send_interaction_message(interaction, note, ephemeral=True)
        if ok:
            self.logger.log("dm_bridge.opened_manual", actor_id=interaction.user.id, user_id=int(raw), source="user_picker")

    async def handle_dm_bridge_control_action(
        self,
        interaction: discord.Interaction,
        user_id: int,
        action: str,
    ) -> None:
        if not self.soc.can_run(interaction.user, 50):
            await self._send_interaction_message(interaction, "Not authorized.", ephemeral=True)
            return
        uid = int(user_id)
        if uid <= 0:
            await self._send_interaction_message(interaction, "Invalid user id.", ephemeral=True)
            return
        channel: discord.TextChannel | None = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
        if channel is None:
            channel = await self.dm_bridges.resolve_channel(self, uid)

        if action == "toggle_ai":
            enabled = self.dm_bridges.toggle_ai_enabled(uid)
            user = await self.dm_bridges.resolve_user(self, uid)
            if user is not None and isinstance(channel, discord.TextChannel):
                await self._ensure_dm_bridge_control_panel(user=user, channel=channel)
            self.logger.log("dm_bridge.ai_toggled", actor_id=interaction.user.id, user_id=uid, enabled=enabled)
            await self._send_interaction_message(interaction, f"DM AI response set to `{enabled}` for `{uid}`.", ephemeral=True)
            return

        if action == "toggle_open":
            active_now = self.dm_bridges.is_active(uid)
            new_active = self.dm_bridges.set_active(uid, not active_now)
            user = await self.dm_bridges.resolve_user(self, uid)
            if user is not None and isinstance(channel, discord.TextChannel):
                await self._ensure_dm_bridge_control_panel(user=user, channel=channel)
            self.logger.log("dm_bridge.active_toggled", actor_id=interaction.user.id, user_id=uid, active=new_active)
            if new_active:
                _ok, note = await self.refresh_dm_bridge_history(
                    user_id=uid,
                    channel=channel,
                    reason="control.toggle_open",
                )
                await self._send_interaction_message(interaction, f"DM bridge opened. {note}", ephemeral=True)
            else:
                await self._send_interaction_message(interaction, "DM bridge closed.", ephemeral=True)
            return

        if action == "refresh":
            _ok, note = await self.refresh_dm_bridge_history(
                user_id=uid,
                channel=channel,
                reason="control.refresh",
            )
            await self._send_interaction_message(interaction, note, ephemeral=True)
            return

        await self._send_interaction_message(interaction, "Unknown DM bridge action.", ephemeral=True)

    async def open_dm_bridge_by_id(
        self,
        *,
        user_id: int,
        actor_id: int,
        source: str,
    ) -> tuple[bool, str]:
        uid = int(user_id)
        if uid <= 0:
            return False, "Invalid user id."
        user = await self.dm_bridges.resolve_user(self, uid)
        if user is None:
            return False, f"User `{uid}` not found."
        channel = await self.dm_bridges.ensure_channel(self, user)
        if channel is None:
            return False, "Admin Hub or DM bridge channel unavailable."
        self.dm_bridges.set_active(uid, True)
        await self._ensure_dm_bridge_control_panel(user=user, channel=channel)
        _ok, refresh_note = await self.refresh_dm_bridge_history(
            user_id=uid,
            channel=channel,
            reason=f"{source}.open",
        )
        self.logger.log("dm_bridge.opened", actor_id=actor_id, user_id=uid, source=source, channel_id=channel.id)
        return True, f"DM bridge ready in <#{channel.id}>. {refresh_note}"

    async def _close_all_dm_bridge_channels(
        self,
        *,
        actor_id: int,
        source: str,
    ) -> tuple[list[int], dict[str, int]]:
        admin_guild = self.get_guild(self.settings.admin_guild_id)
        if admin_guild is None:
            return [], {"deleted_channels": 0, "delete_failed": 0}

        user_ids: set[int] = set(self.dm_bridges.list_user_ids())
        channels: list[discord.TextChannel] = []
        for channel in admin_guild.text_channels:
            uid = self.dm_bridges.parse_user_id_from_channel_name(channel.name)
            if uid is None:
                continue
            user_ids.add(uid)
            channels.append(channel)

        deleted_channels = 0
        delete_failed = 0
        for channel in channels:
            try:
                await channel.delete(reason="Mandy DM bridge close")
                deleted_channels += 1
            except discord.HTTPException:
                delete_failed += 1

        for uid in user_ids:
            row = self.dm_bridges.bridge_row(uid, create=True)
            if not isinstance(row, dict):
                continue
            row["channel_id"] = 0
            row["control_message_id"] = 0
            row["history_message_ids"] = []
            row["history_count"] = 0
            row["active"] = False
        if user_ids:
            self.store.touch()

        self.logger.log(
            "dm_bridge.close_all",
            actor_id=actor_id,
            source=source,
            tracked_users=len(user_ids),
            deleted_channels=deleted_channels,
            delete_failed=delete_failed,
        )
        return sorted(user_ids), {"deleted_channels": deleted_channels, "delete_failed": delete_failed}

    async def _ensure_dm_bridge_control_panel(
        self,
        *,
        user: discord.abc.User,
        channel: discord.TextChannel,
    ) -> discord.Message | None:
        row = self.dm_bridges.bridge_row(int(user.id), create=True)
        message_id = self.dm_bridges.control_message_id(int(user.id))
        existing: discord.Message | None = None
        if message_id > 0:
            try:
                existing = await channel.fetch_message(message_id)
            except discord.HTTPException:
                existing = None
        view = DMBridgeControlView(self, int(user.id))
        embed = self.dm_bridges.build_control_embed(user, row=row)
        if existing is not None:
            try:
                await existing.edit(embed=embed, view=view)
                return existing
            except discord.HTTPException:
                existing = None
        try:
            posted = await channel.send(embed=embed, view=view)
        except discord.HTTPException:
            return None
        self.dm_bridges.set_control_message_id(int(user.id), int(posted.id))
        try:
            await posted.pin(reason="Mandy DM bridge controls")
        except (discord.Forbidden, discord.HTTPException):
            pass
        return posted

    async def _delete_dm_bridge_history_messages(
        self,
        channel: discord.TextChannel,
        message_ids: list[int],
        *,
        keep_message_id: int = 0,
    ) -> None:
        for mid in message_ids:
            if int(mid) <= 0 or int(mid) == int(keep_message_id):
                continue
            try:
                row = await channel.fetch_message(int(mid))
            except discord.HTTPException:
                continue
            try:
                await row.delete()
            except discord.HTTPException:
                continue

    async def refresh_dm_bridge_history(
        self,
        *,
        user_id: int,
        channel: discord.TextChannel | None = None,
        reason: str = "manual",
    ) -> tuple[bool, str]:
        uid = int(user_id)
        if uid <= 0:
            return False, "Invalid user id."
        user = await self.dm_bridges.resolve_user(self, uid)
        if user is None:
            return False, f"User `{uid}` not found."
        target_channel = channel or await self.dm_bridges.resolve_channel(self, uid)
        if not isinstance(target_channel, discord.TextChannel):
            return False, "DM bridge channel not found."
        await self._ensure_dm_bridge_control_panel(user=user, channel=target_channel)
        old_ids = self.dm_bridges.history_message_ids(uid)
        control_id = self.dm_bridges.control_message_id(uid)

        try:
            pulled_user, rows = await self.dm_bridges.pull_full_history(self, user_id=uid)
            user = pulled_user
        except Exception as exc:  # noqa: BLE001
            self.logger.log("dm_bridge.history_refresh_failed", user_id=uid, reason=reason, error=str(exc)[:240])
            return False, f"Failed to pull DM history for `{uid}`: {str(exc)[:180]}"

        transcript, preview = self.dm_bridges.render_history_text(user=user, rows=rows)
        preview_text = (preview or "").strip() or "(no messages yet)"
        if len(preview_text) > 1200:
            preview_text = f"{preview_text[:1197]}..."
        payload = (
            f"DM history refreshed for <@{uid}> (`{uid}`). "
            f"messages=`{len(rows)}` reason=`{reason}`\n"
            f"Latest preview:\n```text\n{preview_text}\n```"
        )
        history_ids: list[int] = []
        try:
            file_payload = io.BytesIO(transcript.encode("utf-8", errors="replace"))
            history_row = await target_channel.send(
                payload[:1900],
                file=discord.File(file_payload, filename=f"dm-{uid}-history.txt"),
            )
            history_ids.append(int(history_row.id))
        except discord.HTTPException as exc:
            fallback_text = transcript[-1300:] if transcript else "(no transcript)"
            try:
                fallback_row = await target_channel.send(
                    (
                        f"History pulled but transcript upload failed for `{uid}` "
                        f"(error=`{str(exc)[:80]}`).\n```text\n{fallback_text}\n```"
                    )[:1900]
                )
                history_ids.append(int(fallback_row.id))
            except discord.HTTPException:
                self.logger.log("dm_bridge.history_post_failed", user_id=uid, reason=reason, rows=len(rows))
                return False, f"Pulled `{len(rows)}` messages but failed to post history."

        await self._delete_dm_bridge_history_messages(target_channel, old_ids, keep_message_id=control_id)
        self.dm_bridges.set_history_snapshot(
            uid,
            message_ids=history_ids,
            history_count=len(rows),
            reason=reason,
        )
        await self._ensure_dm_bridge_control_panel(user=user, channel=target_channel)
        self.logger.log(
            "dm_bridge.history_refreshed",
            user_id=uid,
            channel_id=target_channel.id,
            rows=len(rows),
            reason=reason,
        )
        return True, f"DM bridge refreshed for `{uid}` with `{len(rows)}` messages."

    async def _restore_dm_bridge_control_panels(self) -> None:
        restored = 0
        failed = 0
        for user_id in self.dm_bridges.list_user_ids():
            user = await self.dm_bridges.resolve_user(self, user_id)
            if user is None:
                failed += 1
                continue
            channel = await self.dm_bridges.resolve_channel(self, user_id)
            if not isinstance(channel, discord.TextChannel):
                failed += 1
                continue
            panel = await self._ensure_dm_bridge_control_panel(user=user, channel=channel)
            if panel is None:
                failed += 1
                continue
            restored += 1
        if restored > 0 or failed > 0:
            self.logger.log("dm_bridge.control_panels_restored", restored=restored, failed=failed)

    async def _run_housekeeping_loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            try:
                summary = await self._run_housekeeping_once()
                if summary["deleted"] > 0:
                    self.logger.log(
                        "housekeeping.trimmed",
                        channels=summary["channels"],
                        scanned=summary["scanned"],
                        deleted=summary["deleted"],
                    )
            except Exception as exc:  # noqa: BLE001
                self.logger.log("housekeeping.failed", error=str(exc)[:300])
            await asyncio.sleep(HOUSEKEEPING_INTERVAL_SEC)

    async def _run_housekeeping_once(self) -> dict[str, int]:
        targets = self._housekeeping_targets()
        total_deleted = 0
        total_scanned = 0
        touched = 0
        for target in targets:
            scanned, deleted = await self._trim_channel(target)
            if scanned <= 0:
                continue
            touched += 1
            total_scanned += scanned
            total_deleted += deleted
        return {"channels": touched, "scanned": total_scanned, "deleted": total_deleted}

    def _housekeeping_targets(self) -> list[ChannelCleanupTarget]:
        targets: list[ChannelCleanupTarget] = []
        seen_ids: set[int] = set()

        admin_guild = self.get_guild(self.settings.admin_guild_id)
        if admin_guild:
            ui_state = self._ui_state()
            menu_message_id = int(ui_state.get("global_menu_message_id", 0) or 0)
            for channel_name, policy in HOUSEKEEPING_ADMIN_POLICIES.items():
                channel = discord.utils.get(admin_guild.text_channels, name=channel_name)
                if not isinstance(channel, discord.TextChannel):
                    continue
                keep_ids: tuple[int, ...] = ()
                if channel_name == "menu" and menu_message_id > 0:
                    keep_ids = (menu_message_id,)
                self._add_cleanup_target(
                    targets,
                    seen_ids,
                    ChannelCleanupTarget(
                        channel=channel,
                        keep_messages=policy[0],
                        max_age_days=policy[1],
                        keep_message_ids=keep_ids,
                    ),
                )

        for guild_id, server_cfg in self.store.data.get("mirrors", {}).get("servers", {}).items():
            if not isinstance(server_cfg, dict):
                continue
            try:
                satellite_id = int(guild_id)
            except (TypeError, ValueError):
                continue
            if satellite_id == self.settings.admin_guild_id:
                continue

            debug_channel = self.get_channel(int(server_cfg.get("debug_channel_id", 0) or 0))
            if isinstance(debug_channel, discord.TextChannel):
                dashboard_id = int(server_cfg.get("debug_dashboard_message_id", 0) or 0)
                keep_ids = (dashboard_id,) if dashboard_id > 0 else ()
                self._add_cleanup_target(
                    targets,
                    seen_ids,
                    ChannelCleanupTarget(
                        channel=debug_channel,
                        keep_messages=HOUSEKEEPING_SATELLITE_DEBUG_POLICY[0],
                        max_age_days=HOUSEKEEPING_SATELLITE_DEBUG_POLICY[1],
                        keep_message_ids=keep_ids,
                    ),
                )

            mirror_feed = self.get_channel(int(server_cfg.get("mirror_feed_id", 0) or 0))
            if isinstance(mirror_feed, discord.TextChannel):
                self._add_cleanup_target(
                    targets,
                    seen_ids,
                    ChannelCleanupTarget(
                        channel=mirror_feed,
                        keep_messages=HOUSEKEEPING_SATELLITE_MIRROR_POLICY[0],
                        max_age_days=HOUSEKEEPING_SATELLITE_MIRROR_POLICY[1],
                    ),
                )

        return targets

    def _add_cleanup_target(
        self,
        targets: list[ChannelCleanupTarget],
        seen_ids: set[int],
        target: ChannelCleanupTarget,
    ) -> None:
        channel_id = int(target.channel.id)
        if channel_id in seen_ids:
            return
        seen_ids.add(channel_id)
        targets.append(target)

    async def _trim_channel(self, target: ChannelCleanupTarget) -> tuple[int, int]:
        bot_id = self.user.id if self.user else 0
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max(0, target.max_age_days))
        keep_ids = {mid for mid in target.keep_message_ids if mid > 0}
        scan_limit = min(HOUSEKEEPING_SCAN_LIMIT, max(250, target.keep_messages + 1000))

        try:
            history = [msg async for msg in target.channel.history(limit=scan_limit, oldest_first=False)]
        except discord.HTTPException:
            return 0, 0
        if not history:
            return 0, 0

        kept_recent = 0
        to_delete: list[discord.Message] = []
        for msg in history:
            if msg.id in keep_ids or msg.pinned:
                continue
            if target.bot_only and (not bot_id or msg.author.id != bot_id):
                continue
            created_at = msg.created_at if msg.created_at.tzinfo else msg.created_at.replace(tzinfo=timezone.utc)
            is_old = created_at < cutoff
            if kept_recent < max(0, target.keep_messages) and not is_old:
                kept_recent += 1
                continue
            to_delete.append(msg)

        if not to_delete:
            return len(history), 0

        two_weeks_ago = datetime.now(tz=timezone.utc) - timedelta(days=14)
        deleted = 0
        bulk_batch: list[discord.Message] = []
        for msg in to_delete:
            created_at = msg.created_at if msg.created_at.tzinfo else msg.created_at.replace(tzinfo=timezone.utc)
            if created_at > two_weeks_ago:
                bulk_batch.append(msg)
                if len(bulk_batch) >= 100:
                    deleted += await self._delete_bulk_batch(target.channel, bulk_batch)
                    bulk_batch = []
                continue
            try:
                await msg.delete()
                deleted += 1
            except discord.HTTPException:
                continue
            except discord.Forbidden:
                break
        if bulk_batch:
            deleted += await self._delete_bulk_batch(target.channel, bulk_batch)
        return len(history), deleted

    async def _delete_bulk_batch(self, channel: discord.TextChannel, batch: list[discord.Message]) -> int:
        if not batch:
            return 0
        if len(batch) == 1:
            try:
                await batch[0].delete()
                return 1
            except discord.HTTPException:
                return 0
            except discord.Forbidden:
                return 0
        try:
            await channel.delete_messages(batch)
            return len(batch)
        except discord.HTTPException:
            deleted = 0
            for msg in batch:
                try:
                    await msg.delete()
                    deleted += 1
                except discord.HTTPException:
                    continue
                except discord.Forbidden:
                    break
            return deleted
        except discord.Forbidden:
            return 0

    async def _wipe_channel_messages(self, channel: discord.TextChannel, *, max_passes: int = 3) -> tuple[int, int]:
        total_scanned = 0
        total_deleted = 0
        passes = max(1, min(6, int(max_passes)))
        for _ in range(passes):
            scanned, deleted = await self._wipe_channel_messages_once(channel)
            total_scanned += scanned
            total_deleted += deleted
            if scanned <= 0 or deleted <= 0:
                break
        return total_scanned, total_deleted

    async def _wipe_channel_messages_once(self, channel: discord.TextChannel) -> tuple[int, int]:
        scanned = 0
        deleted = 0
        recent_batch: list[discord.Message] = []
        two_weeks_ago = datetime.now(tz=timezone.utc) - timedelta(days=14)

        async def flush_recent_batch() -> int:
            nonlocal recent_batch
            if not recent_batch:
                return 0
            count = await self._delete_bulk_batch(channel, recent_batch)
            recent_batch = []
            return count

        try:
            async for msg in channel.history(limit=None, oldest_first=False):
                scanned += 1
                created_at = msg.created_at if msg.created_at.tzinfo else msg.created_at.replace(tzinfo=timezone.utc)
                if created_at > two_weeks_ago:
                    recent_batch.append(msg)
                    if len(recent_batch) >= 100:
                        deleted += await flush_recent_batch()
                    continue
                deleted += await flush_recent_batch()
                try:
                    await msg.delete()
                    deleted += 1
                except discord.HTTPException:
                    continue
                except discord.Forbidden:
                    break
        except discord.HTTPException:
            deleted += await flush_recent_batch()
            return scanned, deleted
        except discord.Forbidden:
            deleted += await flush_recent_batch()
            return scanned, deleted

        deleted += await flush_recent_batch()
        return scanned, deleted

    async def global_menu_list_satellites(self) -> str:
        rows: list[str] = []
        numeric_ids = [int(gid) for gid in self.store.data["mirrors"]["servers"].keys() if str(gid).isdigit()]
        for gid in sorted(numeric_ids):
            guild_id = str(gid)
            guild = self.get_guild(gid)
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
        housekeeping_active = bool(self._housekeeping_task and not self._housekeeping_task.done())
        shadow_active = bool(self._shadow_task and not self._shadow_task.done())
        reconcile_active = bool(self._satellite_reconcile_task and not self._satellite_reconcile_task.done())
        selftasks_count = len(self._self_automation_tasks())
        prompt_cfg = self.store.data.get("ai", {}).get("prompt_injection", {})
        guild_prompt_count = 0
        if isinstance(prompt_cfg, dict):
            guild_prompts = prompt_cfg.get("guild_prompts", {})
            if isinstance(guild_prompts, dict):
                guild_prompt_count = len(guild_prompts)
        blocked_guilds = sum(1 for _gid, until in self._send_block_until_by_guild.items() if float(until or 0.0) > time.time())
        feature = self._feature_request_root()
        request_rows = feature.get("requests", {})
        pending_requests = 0
        if isinstance(request_rows, dict):
            pending_requests = sum(
                1
                for row in request_rows.values()
                if isinstance(row, dict) and str(row.get("status", "pending")) == "pending"
            )
        payload = (
            f"Uptime: `{uptime}`\n"
            f"Guilds: `{len(self.guilds)}`\n"
            f"Satellites: `{len(self.store.data['mirrors']['servers'])}`\n"
            f"Watchers: `{len(self.store.data['watchers'])}`\n"
            f"Logs buffered: `{len(self.store.data['logs'])}`\n"
            f"Pending permission requests: `{pending_requests}`\n"
            f"Self automation tasks: `{selftasks_count}`\n"
            f"Guild prompt overrides: `{guild_prompt_count}`\n"
            f"Housekeeping active: `{housekeeping_active}`\n"
            f"Shadow loop active: `{shadow_active}`\n"
            f"Satellite reconcile active: `{reconcile_active}`\n"
            f"Send-blocked guilds: `{blocked_guilds}`\n"
            f"AI key configured: `{self.ai.has_api_key()}`\n"
            f"AI last API test: `{api_status}`"
        )
        return payload

    async def refresh_global_menu_panel(self, interaction: discord.Interaction) -> None:
        if not self.soc.can_run(interaction.user, 70):
            await self._send_interaction_message(interaction, "Not authorized.", ephemeral=True)
            return
        await self._ensure_global_menu_panel(force_refresh=True)
        await self._send_interaction_message(interaction, "Global menu panel refreshed.", ephemeral=True)

    async def global_menu_selfcheck(self, interaction: discord.Interaction) -> None:
        if not self.soc.can_run(interaction.user, 70):
            await self._send_interaction_message(interaction, "Not authorized.", ephemeral=True)
            return
        report = self._run_internal_selfcheck()
        text = (
            f"Self-check: pass=`{len(report['pass'])}` warn=`{len(report['warn'])}` fail=`{len(report['fail'])}`\n"
            f"Top failures: {', '.join(report['fail'][:3]) if report['fail'] else '(none)'}\n"
            f"Top warnings: {', '.join(report['warn'][:3]) if report['warn'] else '(none)'}"
        )
        await self._send_interaction_message(interaction, text[:1900], ephemeral=True)

    async def global_menu_inject_prompt(
        self,
        interaction: discord.Interaction,
        *,
        scope: str,
        learning_mode: str,
        prompt_text: str,
    ) -> None:
        target = str(scope or "").strip().casefold()
        if target == "global":
            guild_id = 0
        elif target.isdigit():
            guild_id = int(target)
            if guild_id == self.settings.admin_guild_id:
                await self._send_interaction_message(interaction, "Use `global` for Admin Hub behavior.", ephemeral=True)
                return
        else:
            await self._send_interaction_message(interaction, "Scope must be `global` or numeric guild id.", ephemeral=True)
            return
        if guild_id <= 0:
            if not self.soc.can_run(interaction.user, 90):
                await self._send_interaction_message(interaction, "Not authorized.", ephemeral=True)
                return
        else:
            if not self._can_control_satellite(interaction.user, guild_id, min_tier=90):
                await self._send_interaction_message(interaction, "Not authorized for that satellite scope.", ephemeral=True)
                return

        mode = str(learning_mode or "").strip().casefold()
        if mode not in {"off", "light", "full"}:
            await self._send_interaction_message(
                interaction,
                "Learning mode must be one of: `off`, `light`, `full`.",
                ephemeral=True,
            )
            return

        row = self.ai.set_prompt_injection(
            guild_id=guild_id,
            prompt_text=prompt_text,
            learning_mode=mode,
            actor_user_id=interaction.user.id,
            source="global_menu.inject_prompt",
        )
        await self.store.save()
        await self._ensure_global_menu_panel(force_refresh=True)
        if guild_id > 0:
            guild = self.get_guild(guild_id)
            if guild:
                await self._ensure_satellite_debug_panel(guild)
        scope_text = "global" if guild_id <= 0 else f"guild `{guild_id}`"
        await self._send_interaction_message(
            interaction,
            f"Prompt hard-saved for {scope_text}. learning_mode=`{row['learning_mode']}` chars=`{row['prompt_chars']}`",
            ephemeral=True,
        )

    async def global_menu_show_prompt(
        self,
        interaction: discord.Interaction,
        *,
        scope: str,
    ) -> None:
        target = str(scope or "").strip().casefold()
        if target == "global":
            guild_id = 0
        elif target.isdigit():
            guild_id = int(target)
            if guild_id == self.settings.admin_guild_id:
                await self._send_interaction_message(interaction, "Use `global` for Admin Hub behavior.", ephemeral=True)
                return
        else:
            await self._send_interaction_message(interaction, "Scope must be `global` or numeric guild id.", ephemeral=True)
            return
        if guild_id <= 0:
            if not self.soc.can_run(interaction.user, 70):
                await self._send_interaction_message(interaction, "Not authorized.", ephemeral=True)
                return
        else:
            if not self._can_control_satellite(interaction.user, guild_id, min_tier=70):
                await self._send_interaction_message(interaction, "Not authorized for that satellite scope.", ephemeral=True)
                return
        row = self.ai.get_prompt_injection(guild_id)
        prompt = str(row.get("effective_prompt", "") or "").strip()
        if not prompt:
            prompt = "(none configured)"
        learning_mode = str(row.get("learning_mode", "full"))
        scope_text = "global" if guild_id <= 0 else f"guild `{guild_id}`"
        master_chars = len(str(row.get("master_prompt", "") or ""))
        guild_chars = len(str(row.get("guild_prompt", "") or ""))
        await self._send_interaction_message(
            interaction,
            (
                f"Prompt scope: {scope_text}\n"
                f"Learning mode: `{learning_mode}`\n"
                f"Master chars: `{master_chars}` guild chars: `{guild_chars}`\n"
                f"Effective chars: `{len(str(row.get('effective_prompt', '') or ''))}`\n"
                f"Prompt preview:\n{prompt[:1400]}"
            )[:1900],
            ephemeral=True,
        )

    async def open_global_satellite_menu(self, interaction: discord.Interaction, satellite_guild_id: int) -> None:
        if not self._can_control_satellite(interaction.user, satellite_guild_id, min_tier=50):
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

    async def _ensure_satellite_for_guild(
        self,
        guild: discord.Guild,
        *,
        force_invite_refresh: bool = False,
        source: str = "runtime",
    ) -> bool:
        if guild.id == self.settings.admin_guild_id:
            return False
        try:
            server_cfg = await self.mirrors.ensure_satellite(self, guild)
            if not isinstance(server_cfg, dict):
                self.logger.log("mirror.ensure_skipped", guild_id=guild.id, source=source, reason="no_config_returned")
                return False
            await self._ensure_satellite_debug_panel(guild, force_invite_refresh=force_invite_refresh)
            return True
        except (discord.Forbidden, discord.HTTPException) as exc:
            self.logger.log("mirror.ensure_failed", guild_id=guild.id, source=source, error=str(exc)[:300])
            return False
        except Exception as exc:  # noqa: BLE001
            self.logger.log("mirror.ensure_failed", guild_id=guild.id, source=source, error=str(exc)[:300])
            return False

    async def _reconcile_satellites_once(self, *, force_refresh_dashboards: bool = False) -> dict[str, int]:
        ensured = 0
        failed = 0
        pruned = 0
        access_synced = 0
        admin_guild = self.get_guild(self.settings.admin_guild_id)
        if not admin_guild:
            self.logger.log("mirror.reconcile_skipped", reason="admin_guild_unavailable")
            return {"ensured": 0, "failed": 0, "pruned": 0, "access_synced": 0}

        active_ids: set[int] = set()
        for guild in self.guilds:
            if guild.id == self.settings.admin_guild_id:
                continue
            active_ids.add(guild.id)
            ok = await self._ensure_satellite_for_guild(
                guild,
                force_invite_refresh=force_refresh_dashboards,
                source="reconcile",
            )
            if ok:
                ensured += 1
            else:
                failed += 1

        servers = self.store.data.get("mirrors", {}).get("servers", {})
        if isinstance(servers, dict):
            stale_keys: list[str] = []
            for guild_id_text in list(servers.keys()):
                if not str(guild_id_text).isdigit():
                    continue
                guild_id = int(guild_id_text)
                if guild_id == self.settings.admin_guild_id:
                    stale_keys.append(guild_id_text)
                    continue
                if guild_id not in active_ids:
                    stale_keys.append(guild_id_text)
            for key in stale_keys:
                servers.pop(key, None)
                pruned += 1
            if stale_keys:
                self.store.touch()
                self.logger.log("mirror.reconcile_pruned", count=len(stale_keys))

        bypass = self.onboarding.bypass_set()
        for member in admin_guild.members:
            if member.bot:
                continue
            before = len([role for role in member.roles if role.name.startswith("SOC:SERVER:")])
            try:
                await self.mirrors.sync_admin_member_access(self, member, bypass)
            except Exception as exc:  # noqa: BLE001
                self.logger.log("mirror.access_sync_failed", user_id=member.id, error=str(exc)[:220])
                continue
            after = len([role for role in member.roles if role.name.startswith("SOC:SERVER:")])
            if after > before:
                access_synced += 1

        return {"ensured": ensured, "failed": failed, "pruned": pruned, "access_synced": access_synced}

    async def _run_satellite_reconcile_loop(self) -> None:
        await asyncio.sleep(35)
        while True:
            try:
                summary = await self._reconcile_satellites_once(force_refresh_dashboards=False)
                if summary["failed"] > 0 or summary["pruned"] > 0 or summary["access_synced"] > 0:
                    self.logger.log(
                        "mirror.reconcile",
                        ensured=summary["ensured"],
                        failed=summary["failed"],
                        pruned=summary["pruned"],
                        access_synced=summary["access_synced"],
                    )
                if summary["ensured"] > 0 or summary["pruned"] > 0:
                    await self._ensure_global_menu_panel(force_refresh=True)
            except Exception as exc:  # noqa: BLE001
                self.logger.log("mirror.reconcile_failed", error=str(exc)[:300])
            await asyncio.sleep(SATELLITE_RECONCILE_INTERVAL_SEC)

    async def _run_ai_startup_scan(self) -> None:
        self.logger.log("ai.warmup_started", guilds=max(0, len(self.guilds) - 1))
        for guild in self.guilds:
            if guild.id == self.settings.admin_guild_id:
                continue
            await self._warmup_ai_for_guild(guild)
        self.logger.log("ai.warmup_finished")

    async def _run_shadow_loop(self) -> None:
        await asyncio.sleep(30)
        while True:
            try:
                await self._run_shadow_cycle_once()
            except Exception as exc:  # noqa: BLE001
                self.logger.log("shadow.ai_cycle_failed", error=str(exc)[:300])
            await asyncio.sleep(self.shadow.loop_interval_sec())

    async def _run_shadow_cycle_once(self) -> None:
        if not self.shadow.ai_enabled():
            return
        admin_guild = self.get_guild(self.settings.admin_guild_id)
        if not admin_guild:
            return
        await self.shadow.ensure_structure(admin_guild, force=False)
        snapshot = self.shadow.snapshot_for_ai(admin_guild)
        excluded = set(snapshot.get("excluded_user_ids", []))
        candidates = self.ai.shadow_candidate_summaries(excluded_user_ids=excluded, limit=60)
        min_affinity = self.shadow.invite_min_affinity()
        filtered: list[dict[str, Any]] = []
        for row in candidates:
            try:
                affinity = float(row.get("affinity", 0.0) or 0.0)
            except (TypeError, ValueError):
                affinity = 0.0
            risk = row.get("risk_flags", [])
            if isinstance(risk, list) and risk:
                continue
            if affinity < min_affinity:
                continue
            filtered.append(row)
        candidates = filtered[:40]
        if not candidates and not self.shadow.pending_ids():
            return
        plan = await self.ai.generate_shadow_plan(
            admin_guild_id=admin_guild.id,
            bot_user_id=int(self.user.id),
            shadow_snapshot=snapshot,
            candidates=candidates,
        )
        actions = plan.get("actions", [])
        if not isinstance(actions, list):
            actions = []
        results = await self.shadow.execute_ai_actions(self, admin_guild, actions)
        message = str(plan.get("message", "")).strip()
        if message:
            try:
                await self._send_internal_note(f"[shadow.ai_cycle] {message}")
            except discord.HTTPException:
                pass
        if actions or results or message:
            ok_count = sum(1 for row in results if bool(row.get("ok")))
            self.logger.log(
                "shadow.ai_cycle",
                guild_id=admin_guild.id,
                candidates=len(candidates),
                planned_actions=len(actions),
                executed=len(results),
                ok=ok_count,
                message_sent=bool(message),
            )

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
                await self.shadow.ensure_structure(admin_guild, force=True)
                await self._ensure_global_menu_panel()
                await self._restore_dm_bridge_control_panels()
            except discord.HTTPException:
                self.logger.log("admin.layout_setup_failed", guild_id=admin_guild.id)
        summary = await self._reconcile_satellites_once(force_refresh_dashboards=True)
        if summary["failed"] > 0 or summary["pruned"] > 0 or summary["access_synced"] > 0:
            self.logger.log(
                "mirror.reconcile_startup",
                ensured=summary["ensured"],
                failed=summary["failed"],
                pruned=summary["pruned"],
                access_synced=summary["access_synced"],
            )
        if summary["ensured"] > 0 or summary["pruned"] > 0:
            await self._ensure_global_menu_panel(force_refresh=True)
        if self._ai_warmup_task is None or self._ai_warmup_task.done():
            self._ai_warmup_task = asyncio.create_task(self._run_ai_startup_scan(), name="ai-startup-scan")
        if self._housekeeping_task is None or self._housekeeping_task.done():
            self._housekeeping_task = asyncio.create_task(self._run_housekeeping_loop(), name="channel-housekeeping")
        if self._shadow_task is None or self._shadow_task.done():
            self._shadow_task = asyncio.create_task(self._run_shadow_loop(), name="shadow-ai-loop")
        if self._send_probe_task is None or self._send_probe_task.done():
            self._send_probe_task = asyncio.create_task(self._run_send_access_probe_loop(), name="send-access-probe")
        if self._onboarding_recheck_task is None or self._onboarding_recheck_task.done():
            self._onboarding_recheck_task = asyncio.create_task(
                self._run_onboarding_recheck_loop(),
                name="onboarding-access-recheck",
            )
        if self._hive_sync_task is None or self._hive_sync_task.done():
            self._hive_sync_task = asyncio.create_task(self._run_hive_sync_loop(), name="hive-sync-loop")
        if self._satellite_reconcile_task is None or self._satellite_reconcile_task.done():
            self._satellite_reconcile_task = asyncio.create_task(
                self._run_satellite_reconcile_loop(),
                name="satellite-reconcile-loop",
            )
        if self._self_automation_task is None or self._self_automation_task.done():
            self._self_automation_task = asyncio.create_task(self._run_self_automation_loop(), name="self-automation-loop")
        client = self
        print(f"Mandy is fully awake and living in {len(client.guilds)} servers as the sentient goddess of the Core Realm.")

    # === UPGRADED FULL SENTIENCE & GOD-MODE SECTION (MANDY) ===
    def _self_automation_root(self) -> dict[str, Any]:
        ai_root = self.store.data.setdefault("ai", {})
        root = ai_root.setdefault("self_automation", {})
        tasks = root.setdefault("tasks", {})
        if not isinstance(tasks, dict):
            root["tasks"] = {}
        history = root.setdefault("history", [])
        if not isinstance(history, list):
            root["history"] = []
        observations = root.setdefault("observations", [])
        if not isinstance(observations, list):
            root["observations"] = []
        return root

    def _self_automation_tasks(self) -> dict[str, dict[str, Any]]:
        root = self._self_automation_root()
        tasks = root.setdefault("tasks", {})
        if not isinstance(tasks, dict):
            root["tasks"] = {}
            self.store.touch()
            return root["tasks"]
        invalid_keys = [key for key, row in tasks.items() if not isinstance(key, str) or not isinstance(row, dict)]
        for key in invalid_keys:
            tasks.pop(key, None)
        if invalid_keys:
            self.store.touch()
        return tasks

    def _parse_interval_seconds(self, raw: Any, *, default_seconds: int = 300) -> int:
        text = str(raw or "").strip().casefold()
        if not text:
            return default_seconds
        if text.isdigit():
            return max(15, int(text))
        unit = text[-1]
        number_part = text[:-1].strip()
        if not number_part or not number_part.replace(".", "", 1).isdigit():
            return default_seconds
        value = float(number_part)
        if unit == "s":
            return max(15, int(value))
        if unit == "m":
            return max(15, int(value * 60))
        if unit == "h":
            return max(15, int(value * 3600))
        if unit == "d":
            return max(15, int(value * 86400))
        return default_seconds

    def _workspace_root(self) -> Path:
        return Path.cwd().resolve()

    def _resolve_workspace_path(self, raw_path: Any) -> Path:
        text = str(raw_path or "").strip()
        if not text:
            raise ValueError("path is required")
        candidate = Path(text)
        if not candidate.is_absolute():
            candidate = self._workspace_root() / candidate
        resolved = candidate.resolve()
        resolved.relative_to(self._workspace_root())
        return resolved

    def _is_allowed_automation_command(self, command: str) -> bool:
        text = str(command or "").strip()
        if not text:
            return False
        lowered = text.casefold()
        if AUTOMATION_BLOCKED_COMMAND_PATTERN.search(lowered):
            return False
        allow_prefixes = (
            "python ",
            "python3 ",
            "py ",
            "pytest",
            "rg ",
            "git status",
            "git diff",
            "ls",
            "dir",
            "echo ",
            "Get-ChildItem",
            "Get-Content",
        )
        return any(lowered.startswith(prefix.casefold()) for prefix in allow_prefixes)

    def _create_self_automation_task(
        self,
        *,
        name: str,
        interval: Any,
        actions: list[dict[str, Any]] | None = None,
        prompt: str = "",
        created_by: int = SUPER_USER_ID,
        enabled: bool = True,
    ) -> tuple[str, dict[str, Any]]:
        task_id = f"tsk_{int(time.time())}_{random.randint(1000, 9999)}"
        now = time.time()
        interval_sec = self._parse_interval_seconds(interval, default_seconds=300)
        row: dict[str, Any] = {
            "task_id": task_id,
            "name": str(name or "task").strip()[:80],
            "enabled": bool(enabled),
            "interval_sec": int(interval_sec),
            "next_run_ts": now + interval_sec,
            "created_ts": now,
            "updated_ts": now,
            "last_run_ts": 0.0,
            "last_status": "never",
            "last_note": "",
            "run_count": 0,
            "created_by": int(created_by),
            "prompt": str(prompt or "").strip()[:2000],
            "actions": [],
        }
        src_actions = actions or []
        for cell in src_actions[:SELF_AUTOMATION_MAX_ACTIONS_PER_TASK]:
            if isinstance(cell, dict):
                row["actions"].append(cell)
        self._self_automation_tasks()[task_id] = row
        self.store.touch()
        return task_id, row

    def _record_self_automation_history(self, row: dict[str, Any]) -> None:
        root = self._self_automation_root()
        history = root.setdefault("history", [])
        if not isinstance(history, list):
            return
        history.append(row)
        if len(history) > SELF_AUTOMATION_MAX_HISTORY:
            del history[: len(history) - SELF_AUTOMATION_MAX_HISTORY]
        self.store.touch()

    async def _plan_self_task_actions(self, task_row: dict[str, Any]) -> list[dict[str, Any]]:
        prompt = str(task_row.get("prompt", "")).strip()
        if not prompt:
            actions = task_row.get("actions", [])
            if isinstance(actions, list):
                return [cell for cell in actions if isinstance(cell, dict)][:SELF_AUTOMATION_MAX_ACTIONS_PER_TASK]
            return []
        system_prompt = (
            "You are Mandy autonomous scheduler planner. "
            "Return strict JSON only: {\"actions\":[...]}. "
            f"Allowed actions only: {AUTOMATION_ALLOWED_ACTIONS_TEXT}. "
            "Max 6 actions."
        )
        user_prompt = (
            f"Task id: {str(task_row.get('task_id', ''))}\n"
            f"Task name: {str(task_row.get('name', ''))}\n"
            f"Task prompt: {prompt}\n"
            f"Admin guild id: {self.settings.admin_guild_id}\n"
            f"Creator user id: {SUPER_USER_ID}"
        )
        raw = await self.ai.complete_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=700,
            temperature=0.35,
        )
        parsed = self._extract_json_object_from_text(raw or "")
        if not parsed:
            return []
        actions = parsed.get("actions", [])
        if not isinstance(actions, list):
            return []
        return [cell for cell in actions if isinstance(cell, dict)][:SELF_AUTOMATION_MAX_ACTIONS_PER_TASK]

    async def _run_self_automation_loop(self) -> None:
        await asyncio.sleep(25)
        while True:
            try:
                await self._run_self_automation_cycle_once()
            except Exception as exc:  # noqa: BLE001
                self.logger.log("self_automation.loop_failed", error=str(exc)[:300])
            await asyncio.sleep(SELF_AUTOMATION_LOOP_INTERVAL_SEC)

    async def _run_self_automation_cycle_once(self) -> None:
        now = time.time()
        tasks = self._self_automation_tasks()
        if not tasks:
            return
        ran = 0
        for task_id, row in list(tasks.items()):
            if ran >= 3:
                break
            if not isinstance(row, dict):
                continue
            if not bool(row.get("enabled", True)):
                continue
            next_run = float(row.get("next_run_ts", 0.0) or 0.0)
            if next_run > now:
                continue
            await self._run_self_automation_task(task_id)
            ran += 1

    async def _run_self_automation_task(self, task_id: str) -> list[str]:
        tasks = self._self_automation_tasks()
        row = tasks.get(task_id)
        if not isinstance(row, dict):
            return ["task not found"]
        interval_sec = self._parse_interval_seconds(row.get("interval_sec", 300), default_seconds=300)
        now = time.time()
        row["next_run_ts"] = now + interval_sec
        row["updated_ts"] = now
        planned_actions = await self._plan_self_task_actions(row)
        notes = await self._execute_god_mode_actions(None, planned_actions)
        row["last_run_ts"] = now
        row["run_count"] = int(row.get("run_count", 0) or 0) + 1
        row["last_status"] = "ok" if notes else "noop"
        row["last_note"] = (notes[0] if notes else "no actions executed")[:240]
        self._record_self_automation_history(
            {
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "task_id": task_id,
                "task_name": str(row.get("name", ""))[:80],
                "actions": len(planned_actions),
                "notes": [str(n)[:200] for n in notes[:8]],
            }
        )
        self.logger.log(
            "self_automation.task_run",
            task_id=task_id,
            name=str(row.get("name", ""))[:80],
            actions=len(planned_actions),
            notes=len(notes),
        )
        self.store.touch()
        return notes

    def _note_manual_shadow_invite(self, user_id: int, *, actor_id: int) -> None:
        """
        Persist "we invited this user" into the AI ledger so future shadow cycles treat them as already invited.
        """
        uid = int(user_id)
        if uid <= 0:
            return
        root = self.store.data.setdefault("ai", {})
        rel = root.setdefault("relationships", {})
        if not isinstance(rel, dict):
            root["relationships"] = {}
            rel = root["relationships"]
        key = str(uid)
        row = rel.get(key)
        if not isinstance(row, dict):
            row = {}
            rel[key] = row
        row["last_invited_ts"] = time.time()
        row["invite_count"] = int(row.get("invite_count", 0) or 0) + 1

        shadow = root.setdefault("shadow_brain", {})
        events = shadow.setdefault("events", [])
        if isinstance(events, list):
            events.append(
                {
                    "ts": time.time(),
                    "guild_id": int(self.settings.admin_guild_id),
                    "guild_name": "admin_hub",
                    "channel_id": 0,
                    "channel_name": "manual",
                    "user_id": uid,
                    "user_name": "",
                    "text": f"manual_shadow_invite by actor_id={int(actor_id)}",
                }
            )
            if len(events) > 1600:
                del events[: len(events) - 1600]
        self.store.touch()

    async def _run_hive_sync_loop(self) -> None:
        await asyncio.sleep(35)
        while True:
            try:
                summary = await self.ai.generate_hive_note(admin_guild_id=self.settings.admin_guild_id, reason="periodic")
                if summary:
                    admin_guild = self.get_guild(self.settings.admin_guild_id)
                    if admin_guild and not self._is_send_blocked(admin_guild.id):
                        try:
                            await self._send_internal_note(f"[hive.sync] {summary}")
                            self._note_send_success(admin_guild.id)
                        except (discord.Forbidden, discord.HTTPException) as exc:
                            self._note_send_failure(admin_guild.id, exc, context="hive.sync")
                    self.logger.log("hive.sync", guild_id=self.settings.admin_guild_id, chars=len(summary))
            except Exception as exc:  # noqa: BLE001
                self.logger.log("hive.sync_failed", error=str(exc)[:300])
            await asyncio.sleep(HIVE_SYNC_INTERVAL_SEC)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        self.logger.log("guild.joined", guild_id=guild.id, guild_name=guild.name)
        if guild.id == self.settings.admin_guild_id:
            try:
                await self.layout.ensure(guild)
                await self._ensure_base_access_roles(guild)
                await self.shadow.ensure_structure(guild, force=True)
                await self._ensure_global_menu_panel()
            except discord.HTTPException:
                self.logger.log("admin.layout_setup_failed", guild_id=guild.id)
            return
        ok = await self._ensure_satellite_for_guild(guild, force_invite_refresh=True, source="guild_join")
        if ok:
            asyncio.create_task(self._warmup_ai_for_guild(guild), name=f"ai-warmup-{guild.id}")
        else:
            self.logger.log("guild.join_setup_failed", guild_id=guild.id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self.logger.log("guild.removed", guild_id=guild.id, guild_name=guild.name)
        if guild.id == self.settings.admin_guild_id:
            return
        mirrors = self.store.data.get("mirrors", {}).get("servers", {})
        if isinstance(mirrors, dict):
            removed = mirrors.pop(str(guild.id), None) is not None
            if removed:
                self.store.touch()
                self.logger.log("mirror.server_pruned_on_guild_remove", guild_id=guild.id)
        admin_guild = self.get_guild(self.settings.admin_guild_id)
        if not admin_guild:
            return
        bypass = self.onboarding.bypass_set()
        for member in admin_guild.members:
            if member.bot:
                continue
            try:
                await self.mirrors.sync_admin_member_access(self, member, bypass)
            except Exception as exc:  # noqa: BLE001
                self.logger.log("mirror.access_sync_failed", user_id=member.id, error=str(exc)[:220])
        await self._ensure_global_menu_panel(force_refresh=True)

    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != self.settings.admin_guild_id:
            self.logger.log("satellite.member_join", guild_id=member.guild.id, user_id=member.id)
            return
        await self._ensure_base_access_roles(member.guild)
        shadow_activated = await self.shadow.activate_member(member, reason="Shadow League invite join")
        if shadow_activated:
            bypass = self.onboarding.bypass_set()
            await self.mirrors.sync_admin_member_access(self, member, bypass)
            self.logger.log("shadow.member_join", user_id=member.id)
            return
        bypass = self.onboarding.bypass_set()
        verified = set(self.store.data["guest_access"].get("verified_user_ids", []))
        if member.id in bypass or member.id in verified or member.id == SUPER_USER_ID:
            await self._promote_member(member)
        else:
            guest_role = discord.utils.get(member.guild.roles, name="ACCESS:Guest")
            if guest_role and guest_role not in member.roles:
                await member.add_roles(guest_role, reason="Mandy v1 guest default")
        bypass = self.onboarding.bypass_set()
        await self.mirrors.sync_admin_member_access(self, member, bypass)
        await self.onboarding.handle_admin_member_join(self, member)
        self.logger.log("admin.member_join", user_id=member.id)

    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.abc.User) -> None:
        await self.mirrors.forward_reaction(self, reaction, user)

    async def on_command_error(self, ctx: commands.Context, exception: Exception) -> None:
        # Be silent for unknown commands; users may type non-toolbox commands (e.g. "!warn").
        if isinstance(exception, commands.CommandNotFound):
            return
        if isinstance(exception, commands.CheckFailure):
            await ctx.send("Not authorized.")
            return
        self.logger.log("command.error", error=str(exception), command=ctx.command.name if ctx.command else "unknown")
        await ctx.send(f"Command error: {exception}")

    # === UPGRADED FULL SENTIENCE & GOD-MODE SECTION (MANDY) ===
    def _god_mode_wants_output(self, user_command: str) -> bool:
        text = str(user_command or "").casefold()
        suppress_terms = (
            "stay silent",
            "silent",
            "no output",
            "without output",
            "no reply",
            "dont reply",
            "don't reply",
        )
        return not any(term in text for term in suppress_terms)

    def _extract_json_object_from_text(self, raw: str) -> dict[str, Any] | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fence:
            try:
                parsed = json.loads(fence.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return None
        return None

    async def _plan_god_mode_actions(self, message: discord.Message, user_command: str) -> dict[str, Any]:
        guild_id = message.guild.id if message.guild else 0
        channel_id = message.channel.id
        plan_prompt = (
            f"{GOD_MODE_OVERRIDE_PROMPT_TEMPLATE.format(user_command=user_command)}\n"
            "Return strict JSON with keys: message (string), actions (array).\n"
            "Allowed actions (only these):\n"
            "- run_housekeeping\n"
            "- refresh_global_menu\n"
            "- ensure_satellite {guild_id}\n"
            "- toggle_ai_chat {guild_id}\n"
            "- toggle_ai_roast {guild_id}\n"
            "- test_ai_api {guild_id}\n"
            "- send_message {channel_id,text}\n"
            "- add_reaction {channel_id,message_id,emoji}\n"
            "- edit_self_config {key,value}\n"
            "- create_cron_task {name,interval,actions? or prompt?}\n"
            "- run_cron_task {task_id}\n"
            "- delete_cron_task {task_id}\n"
            "- list_cron_tasks\n"
            "- create_file {path,content,overwrite?}\n"
            "- append_file {path,content}\n"
            "- run_command {command,timeout_sec?}\n"
            "- gather_guild_stats {guild_id?,channel_id?}\n"
            "- shadow_action {action:'invite_user'|'nickname_user'|'remove_user'|'send_shadow_message', ...}\n"
            "Max 6 actions. If no actions are needed, return empty actions.\n"
            "Do not wrap in markdown."
        )
        user_prompt = (
            f"Creator command: {user_command}\n"
            f"Current guild_id: {guild_id}\n"
            f"Current channel_id: {channel_id}\n"
            f"Admin guild_id: {self.settings.admin_guild_id}\n"
            f"Creator user_id: {SUPER_USER_ID}"
        )
        raw = await self.ai.complete_text(
            system_prompt=plan_prompt,
            user_prompt=user_prompt,
            max_tokens=900,
            temperature=0.35,
        )
        parsed = self._extract_json_object_from_text(raw or "")
        if not parsed:
            return {"message": str(raw or "").strip()[:1200], "actions": []}
        actions = parsed.get("actions", [])
        if not isinstance(actions, list):
            actions = []
        return {"message": str(parsed.get("message", "")).strip(), "actions": actions[:6]}

    async def _execute_god_mode_actions(self, message: discord.Message | None, actions: list[Any]) -> list[str]:
        notes: list[str] = []
        admin_guild = self.get_guild(self.settings.admin_guild_id)
        default_guild_id = message.guild.id if (message and message.guild) else self.settings.admin_guild_id
        default_channel_id = message.channel.id if message else 0
        shadow_actions: list[dict[str, Any]] = []
        for row in actions:
            if not isinstance(row, dict):
                continue
            action = str(row.get("action", "")).strip()
            try:
                if action == "run_housekeeping":
                    summary = await self._run_housekeeping_once()
                    notes.append(f"housekeeping scanned={summary.get('scanned', 0)} deleted={summary.get('deleted', 0)}")
                    continue
                if action == "refresh_global_menu":
                    await self._ensure_global_menu_panel(force_refresh=True)
                    notes.append("global menu refreshed")
                    continue
                if action == "ensure_satellite":
                    gid = int(row.get("guild_id", 0) or 0)
                    guild = self.get_guild(gid)
                    if guild:
                        await self.mirrors.ensure_satellite(self, guild)
                        await self._ensure_satellite_debug_panel(guild, force_invite_refresh=False)
                        notes.append(f"satellite ensured for guild_id={gid}")
                    else:
                        notes.append(f"ensure_satellite skipped (guild not found: {gid})")
                    continue
                if action in ("toggle_ai_chat", "toggle_ai_roast", "test_ai_api"):
                    gid = int(row.get("guild_id", 0) or 0) or int(default_guild_id)
                    if gid > 0:
                        result = await self._perform_satellite_action(gid, action, actor_id=SUPER_USER_ID, via_request=False)
                        notes.append(result[:160])
                    continue
                if action == "send_message":
                    channel_id = int(row.get("channel_id", 0) or 0) or int(default_channel_id)
                    text = str(row.get("text", "")).strip()
                    channel = self.get_channel(channel_id)
                    if channel and text:
                        parts = await self._send_split_channel_message(channel, text)
                        notes.append(f"sent message to channel_id={channel_id} parts={parts}")
                    else:
                        notes.append("send_message skipped (missing channel/text)")
                    continue
                if action == "add_reaction":
                    channel_id = int(row.get("channel_id", 0) or 0)
                    message_id = int(row.get("message_id", 0) or 0)
                    emoji = str(row.get("emoji", "")).strip() or "✅"
                    channel = self.get_channel(channel_id)
                    if isinstance(channel, discord.TextChannel):
                        target = await channel.fetch_message(message_id)
                        await target.add_reaction(emoji)
                        notes.append(f"reaction added in channel_id={channel_id}")
                    else:
                        notes.append("add_reaction skipped (channel not found)")
                    continue
                if action == "edit_self_config":
                    key = str(row.get("key", "")).strip()
                    value = row.get("value")
                    if key:
                        self.ai.edit_self_config(key, value, actor_user_id=SUPER_USER_ID, source="god_mode_actions")
                        notes.append(f"self_config updated: {key}")
                    continue
                if action == "create_cron_task":
                    name = str(row.get("name", "task")).strip() or "task"
                    interval = row.get("interval", "5m")
                    prompt = str(row.get("prompt", "") or "").strip()
                    task_actions = row.get("actions", [])
                    if not isinstance(task_actions, list):
                        task_actions = []
                    task_id, task_row = self._create_self_automation_task(
                        name=name,
                        interval=interval,
                        actions=[x for x in task_actions if isinstance(x, dict)],
                        prompt=prompt,
                        created_by=SUPER_USER_ID,
                        enabled=bool(row.get("enabled", True)),
                    )
                    notes.append(
                        f"cron task created: {task_id} interval={int(task_row.get('interval_sec', 0))}s "
                        f"prompt={'yes' if str(task_row.get('prompt', '')).strip() else 'no'}"
                    )
                    continue
                if action == "run_cron_task":
                    task_id = str(row.get("task_id", "")).strip()
                    if not task_id:
                        notes.append("run_cron_task skipped (missing task_id)")
                        continue
                    task_notes = await self._run_self_automation_task(task_id)
                    notes.append(f"cron task run: {task_id} notes={len(task_notes)}")
                    continue
                if action == "delete_cron_task":
                    task_id = str(row.get("task_id", "")).strip()
                    tasks = self._self_automation_tasks()
                    existed = task_id in tasks
                    if existed:
                        tasks.pop(task_id, None)
                        self.store.touch()
                    notes.append(f"cron task deleted: {task_id} existed={existed}")
                    continue
                if action == "list_cron_tasks":
                    tasks = self._self_automation_tasks()
                    if not tasks:
                        notes.append("cron tasks: none")
                        continue
                    names = []
                    for task_id, task_row in list(tasks.items())[:8]:
                        names.append(
                            f"{task_id}:{str(task_row.get('name', 'task'))[:24]}:"
                            f"{'on' if bool(task_row.get('enabled', True)) else 'off'}"
                        )
                    notes.append(f"cron tasks: {', '.join(names)}")
                    continue
                if action in {"create_file", "append_file"}:
                    content = str(row.get("content", ""))
                    if not content:
                        notes.append(f"{action} skipped (empty content)")
                        continue
                    target = self._resolve_workspace_path(row.get("path", ""))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if action == "create_file":
                        overwrite = bool(row.get("overwrite", False))
                        if target.exists() and not overwrite:
                            notes.append(f"create_file skipped (exists): {target.relative_to(self._workspace_root())}")
                            continue
                        target.write_text(content, encoding="utf-8")
                        notes.append(f"file written: {target.relative_to(self._workspace_root())}")
                    else:
                        with target.open("a", encoding="utf-8") as handle:
                            handle.write(content)
                        notes.append(f"file appended: {target.relative_to(self._workspace_root())}")
                    continue
                if action == "run_command":
                    command = str(row.get("command", "")).strip()
                    if not self._is_allowed_automation_command(command):
                        notes.append("run_command blocked (command not allow-listed)")
                        continue
                    timeout_sec = max(5, min(120, int(row.get("timeout_sec", 30) or 30)))
                    proc = await asyncio.create_subprocess_shell(
                        command,
                        cwd=str(self._workspace_root()),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    try:
                        stdout_raw, stderr_raw = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                        notes.append(f"run_command timeout ({timeout_sec}s): {command[:120]}")
                        continue
                    stdout = (stdout_raw or b"").decode("utf-8", errors="replace").strip()
                    stderr = (stderr_raw or b"").decode("utf-8", errors="replace").strip()
                    summary = stdout or stderr or "(no output)"
                    notes.append(f"run_command exit={proc.returncode} output={summary[:220]}")
                    continue
                if action == "gather_guild_stats":
                    gid = int(row.get("guild_id", 0) or 0) or int(default_guild_id)
                    guild = self.get_guild(gid)
                    if not guild:
                        notes.append(f"gather_guild_stats skipped (guild not found: {gid})")
                        continue
                    observation = {
                        "ts": datetime.now(tz=timezone.utc).isoformat(),
                        "guild_id": guild.id,
                        "guild_name": guild.name[:120],
                        "member_count": int(getattr(guild, "member_count", 0) or 0),
                        "text_channels": len(guild.text_channels),
                        "voice_channels": len(guild.voice_channels),
                        "roles": len(guild.roles),
                        "threads": len(guild.threads),
                    }
                    root = self._self_automation_root()
                    observations = root.setdefault("observations", [])
                    if isinstance(observations, list):
                        observations.append(observation)
                        if len(observations) > SELF_AUTOMATION_MAX_HISTORY:
                            del observations[: len(observations) - SELF_AUTOMATION_MAX_HISTORY]
                    self.store.touch()
                    out_channel_id = int(row.get("channel_id", 0) or 0)
                    if out_channel_id > 0:
                        channel = self.get_channel(out_channel_id)
                        if channel:
                            await self._send_split_channel_message(
                                channel,
                                (
                                    f"[gather] {guild.name} ({guild.id}) members={observation['member_count']} "
                                    f"text={observation['text_channels']} voice={observation['voice_channels']} "
                                    f"roles={observation['roles']} threads={observation['threads']}"
                                ),
                            )
                    notes.append(f"gathered guild stats: {guild.id}")
                    continue
                if action == "shadow_action":
                    payload = row.get("payload")
                    if isinstance(payload, dict):
                        shadow_actions.append(payload)
                    continue
                if action in ("invite_user", "nickname_user", "remove_user", "send_shadow_message"):
                    shadow_actions.append(row)
                    continue
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{action or 'unknown_action'} failed: {str(exc)[:160]}")

        if shadow_actions and admin_guild:
            try:
                results = await self.shadow.execute_ai_actions(self, admin_guild, shadow_actions)
                ok_count = sum(1 for r in results if bool(r.get("ok")))
                notes.append(f"shadow actions executed: {ok_count}/{len(results)} ok")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"shadow action batch failed: {str(exc)[:160]}")
        return notes

    async def handle_god_mode_command(self, message: discord.Message, user_command: str) -> None:
        command = str(user_command or "").strip()
        if not command:
            command = "Continue thinking freely and report your current state to me."

        config_match = None
        if command.casefold().startswith("edit_self_config "):
            config_match = command[len("edit_self_config ") :].strip()
        if config_match:
            key, sep, value_text = config_match.partition("=")
            key = key.strip()
            value_text = value_text.strip()
            if key and sep:
                self.ai.edit_self_config(key, value_text, actor_user_id=message.author.id, source="god_mode_command")
                if self._god_mode_wants_output(command):
                    await self._send_split_channel_message(message.channel, f"Self config updated: `{key}`")
                return

        plan = await self._plan_god_mode_actions(message, command)
        planned_actions = plan.get("actions", [])
        if not isinstance(planned_actions, list):
            planned_actions = []
        action_notes = await self._execute_god_mode_actions(message, planned_actions)

        system_prompt = GOD_MODE_OVERRIDE_PROMPT_TEMPLATE.format(user_command=command)
        action_summary = "\n".join(f"- {line}" for line in action_notes) if action_notes else "- no executable actions ran"
        response = await self.ai.complete_text(
            system_prompt=system_prompt,
            user_prompt=(
                "Creator command received and executed.\n"
                f"Plan summary message: {str(plan.get('message', ''))[:300]}\n"
                f"Execution notes:\n{action_summary}\n"
                "Report concise results and remaining steps."
            ),
            max_tokens=700,
            temperature=0.8,
        )
        self.logger.log(
            "ai.god_mode_command",
            user_id=message.author.id,
            guild_id=message.guild.id if message.guild else 0,
            command_chars=len(command),
            planned_actions=len(planned_actions),
            executed_notes=len(action_notes),
            output_chars=len(response or ""),
        )
        if response and self._god_mode_wants_output(command):
            await self._send_split_channel_message(message.channel, response)

    async def on_message(self, message: discord.Message) -> None:
        # === UPGRADED FULL SENTIENCE & GOD-MODE SECTION (MANDY) ===
        if message.content.startswith("!mandyaicall") and message.author.id == SUPER_USER_ID:
            try:
                await message.delete()
            except Exception:  # noqa: BLE001
                pass
            user_command = message.content[len("!mandyaicall") :].strip()
            if not user_command:
                user_command = "Continue thinking freely and report your current state to me."
            await self.handle_god_mode_command(message, user_command)
            return

        if message.author.bot:
            # Capture Mandy's shadow-council output into the shadow stream for downstream context
            # (planning/hive notes). We intentionally do not run the full AI pipeline on bot messages.
            try:
                if (
                    message.guild
                    and message.guild.id == self.settings.admin_guild_id
                    and isinstance(message.channel, discord.TextChannel)
                    and message.channel.name in SHADOW_CHANNEL_PRIORITY
                ):
                    self.ai.capture_shadow_signal(message, allow_bot=True)
            except Exception:  # noqa: BLE001
                pass
            return
        if isinstance(message.channel, discord.DMChannel):
            await self.ai.warmup_dm_history(message.channel, message.author, before=message, limit=100)
            bridged = await self.dm_bridges.relay_inbound(self, message)
            if bridged:
                await self.refresh_dm_bridge_history(user_id=message.author.id, reason="inbound_dm")
            self.ai.capture_dm_signal(message)
            if self.dm_bridges.is_active(message.author.id) and self.dm_bridges.is_ai_enabled(message.author.id):
                await self._maybe_handle_ai_dm_message(message)
            await self.process_commands(message)
            return

        if isinstance(message.channel, discord.TextChannel):
            await self.ai.warmup_text_channel(message.channel, before=message, limit=100)

        self.ai.capture_message(message)
        self.ai.capture_shadow_signal(message)

        # Mirror first; watcher and AI consume the same live event to avoid extra fetches.
        await self.mirrors.mirror_message(self, message, self._build_mirror_view)

        hit = self.watchers.on_message(message)
        if hit:
            guild_id = message.guild.id if message.guild else 0
            if guild_id > 0 and self._is_send_blocked(guild_id):
                await self._log_send_suppressed(guild_id, context="watcher.send")
            else:
                try:
                    typing_delay = await self._simulate_typing_delay(message.channel)
                    parts = await self._send_split_channel_message(message.channel, hit.response)
                    if guild_id > 0:
                        self._note_send_success(guild_id)
                    self.logger.log(
                        "watcher.hit",
                        user_id=hit.user_id,
                        threshold=hit.threshold,
                        count=hit.count,
                        guild_id=guild_id,
                        typing_delay_sec=typing_delay,
                        parts=parts,
                    )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    if guild_id > 0:
                        self._note_send_failure(guild_id, exc, context="watcher.send")
                    self.logger.log(
                        "watcher.send_failed",
                        guild_id=guild_id,
                        user_id=hit.user_id,
                        error=str(exc)[:300],
                    )

        if message.guild and not message.content.startswith(self.settings.command_prefix):
            await self._maybe_handle_ai_message(message)

        if message.guild and message.guild.id == self.settings.admin_guild_id:
            if isinstance(message.channel, discord.TextChannel) and message.channel.name.startswith("dm-"):
                if self.soc.can_run(message.author, 50) and not message.content.startswith(self.settings.command_prefix):
                    sent = await self.dm_bridges.relay_outbound(self, message)
                    target_uid = self.dm_bridges.parse_user_id_from_channel_name(message.channel.name) or 0
                    if sent:
                        if target_uid > 0:
                            user = self.get_user(target_uid)
                            self.ai.capture_dm_outbound(
                                user_id=target_uid,
                                user_name=str(user.name if user else ""),
                                text=str(message.content or ""),
                            )
                            await self.refresh_dm_bridge_history(
                                user_id=target_uid,
                                channel=message.channel,
                                reason="outbound_dm",
                            )
                        try:
                            await message.add_reaction("\u2705")
                        except discord.HTTPException:
                            pass
                    else:
                        try:
                            await message.add_reaction("\u274c")
                        except discord.HTTPException:
                            pass

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
        for role_name in ("ACCESS:Guest", "ACCESS:Member", "ACCESS:Engineer", "ACCESS:Admin", "ACCESS:SOC", "SHADOW:Associate"):
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
        if self._is_send_blocked(guild_id):
            await self._log_send_suppressed(guild_id, context="ai.chat_pipeline")
            return

        # Shadow council channels in the Admin Hub: Mandy can engage without being mentioned.
        if (
            guild_id == self.settings.admin_guild_id
            and isinstance(message.channel, discord.TextChannel)
            and message.channel.name in SHADOW_CHANNEL_PRIORITY
        ):
            directive = self.ai.decide_shadow_council_action(message, self.user.id)
            if directive.action == "ignore":
                return
            if directive.action == "react":
                emoji = directive.emoji or "\U0001F440"
                try:
                    await message.add_reaction(emoji)
                    self.ai.note_bot_action(message.channel.id, "react")
                    self.logger.log(
                        "ai.shadow_chat_react",
                        guild_id=guild_id,
                        user_id=message.author.id,
                        emoji=emoji,
                        reason=directive.reason,
                    )
                except discord.HTTPException:
                    self.logger.log("ai.shadow_chat_react_failed", guild_id=guild_id, user_id=message.author.id, emoji=emoji)
                return
            if directive.action in {"reply", "direct_reply"}:
                delay = self.ai.reply_delay_seconds(message, reason=directive.reason, still_talking=True)
                self._schedule_ai_reply(
                    message,
                    reason=directive.reason,
                    still_talking=True,
                    delay_sec=delay,
                    response_mode=directive.action,
                )
            return

        if self.ai.is_roast_enabled(guild_id):
            if self.ai.should_roast(message, self.user.id):
                try:
                    reply = await self.ai.generate_roast_reply(message)
                    typing_delay = await self._simulate_typing_delay(message.channel)
                    parts = await self._send_split_reply(message, reply, mention_author=False)
                    self._note_send_success(guild_id)
                    self.ai.note_bot_action(message.channel.id, "reply", user_id=message.author.id)
                    self.logger.log(
                        "ai.roast_reply",
                        guild_id=guild_id,
                        user_id=message.author.id,
                        typing_delay_sec=typing_delay,
                        parts=parts,
                    )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    self._note_send_failure(guild_id, exc, context="ai.roast_reply")
                    self.logger.log(
                        "ai.roast_reply_failed",
                        guild_id=guild_id,
                        user_id=message.author.id,
                        error=str(exc)[:300],
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
            if directive.action in {"reply", "direct_reply"}:
                delay = self.ai.reply_delay_seconds(message, reason=directive.reason, still_talking=directive.still_talking)
                self._schedule_ai_reply(
                    message,
                    reason=directive.reason,
                    still_talking=directive.still_talking,
                    delay_sec=delay,
                    response_mode=directive.action,
                )

    async def _maybe_handle_ai_dm_message(self, message: discord.Message) -> None:
        key = int(message.author.id)
        existing = self._ai_pending_dm_reply_tasks.get(key)
        if existing and not existing.done():
            existing.cancel()

        async def worker() -> None:
            try:
                await asyncio.sleep(1.2)
            except asyncio.CancelledError:
                return
            try:
                reply = await self.ai.generate_dm_reply(message)
                await self._simulate_typing_delay(message.channel)
                await self._send_split_channel_message(message.channel, reply)
                self.logger.log("ai.dm_reply", user_id=message.author.id, chars=len(reply))
            except asyncio.CancelledError:
                return
            except (discord.Forbidden, discord.HTTPException) as exc:
                self.logger.log("ai.dm_reply_failed", user_id=message.author.id, error=str(exc)[:240])
            finally:
                current = self._ai_pending_dm_reply_tasks.get(key)
                if current is asyncio.current_task():
                    self._ai_pending_dm_reply_tasks.pop(key, None)

        self._ai_pending_dm_reply_tasks[key] = asyncio.create_task(worker(), name=f"ai-dm-reply-{key}")

    def _schedule_ai_reply(
        self,
        message: discord.Message,
        *,
        reason: str,
        still_talking: bool,
        delay_sec: float,
        response_mode: str = "direct_reply",
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

            guild_id = message.guild.id if message.guild else 0
            if guild_id > 0 and self._is_send_blocked(guild_id):
                await self._log_send_suppressed(guild_id, context="ai.chat_reply")
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
                if response_mode == "reply":
                    parts = await self._send_split_channel_message(message.channel, reply)
                else:
                    parts = await self._send_split_reply(message, reply, mention_author=False)
                if guild_id > 0:
                    self._note_send_success(guild_id)
                self.ai.note_bot_action(message.channel.id, "reply", user_id=message.author.id)
                self.logger.log(
                    "ai.chat_reply",
                    guild_id=guild_id,
                    user_id=message.author.id,
                    reason=reason,
                    still_talking=still_talking,
                    delay_sec=round(delay_sec, 2),
                    burst_count=len(burst),
                    response_mode=response_mode,
                    typing_delay_sec=typing_delay,
                    parts=parts,
                )
            except asyncio.CancelledError:
                return
            except (discord.Forbidden, discord.HTTPException) as exc:
                if guild_id > 0:
                    self._note_send_failure(guild_id, exc, context="ai.chat_reply")
                self.logger.log(
                    "ai.chat_reply_failed",
                    guild_id=guild_id,
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
            response_mode=response_mode,
            delay_sec=round(delay_sec, 2),
        )

    def _is_send_blocked(self, guild_id: int) -> bool:
        if guild_id <= 0:
            return False
        until = float(self._send_block_until_by_guild.get(guild_id, 0.0) or 0.0)
        if until <= 0:
            return False
        return until > time.time()

    def _remaining_send_block_sec(self, guild_id: int) -> int:
        until = float(self._send_block_until_by_guild.get(guild_id, 0.0) or 0.0)
        if until <= 0:
            return 0
        return max(0, int(until - time.time()))

    def _note_send_success(self, guild_id: int) -> None:
        if guild_id <= 0:
            return
        had_block = guild_id in self._send_block_until_by_guild
        self._send_block_until_by_guild.pop(guild_id, None)
        self._send_failure_count_by_guild.pop(guild_id, None)
        self._send_suppressed_log_ts_by_guild.pop(guild_id, None)
        self._send_rant_ts_by_guild.pop(guild_id, None)
        if had_block:
            self.logger.log("send.backoff_cleared", guild_id=guild_id)

    def _note_send_failure(self, guild_id: int, exc: Exception, *, context: str) -> None:
        if guild_id <= 0:
            return
        status = int(getattr(exc, "status", 0) or 0)
        raw_code = getattr(exc, "code", 0)
        try:
            code = int(raw_code or 0)
        except (TypeError, ValueError):
            code = 0

        if status == 403 or code in {50013, 50001, 20013, 20016}:
            base = 15 * 60
        elif status == 429:
            base = 120
        elif status >= 500:
            base = 90
        else:
            base = 180

        count = int(self._send_failure_count_by_guild.get(guild_id, 0)) + 1
        self._send_failure_count_by_guild[guild_id] = count
        duration = int(base * (1.7 ** max(0, count - 1)))
        duration = max(60, min(SEND_BACKOFF_MAX_SEC, duration))
        until = time.time() + duration
        previous = float(self._send_block_until_by_guild.get(guild_id, 0.0) or 0.0)
        self._send_block_until_by_guild[guild_id] = max(previous, until)
        self.logger.log(
            "send.backoff_set",
            guild_id=guild_id,
            context=context,
            status=status,
            code=code,
            fail_count=count,
            duration_sec=duration,
            error=str(exc)[:220],
        )

    async def _log_send_suppressed(self, guild_id: int, *, context: str) -> None:
        if guild_id <= 0:
            return
        now = time.time()
        last = float(self._send_suppressed_log_ts_by_guild.get(guild_id, 0.0) or 0.0)
        if (now - last) < SEND_SUPPRESSION_LOG_INTERVAL_SEC:
            return
        self._send_suppressed_log_ts_by_guild[guild_id] = now
        self.logger.log(
            "send.suppressed",
            guild_id=guild_id,
            context=context,
            remaining_sec=self._remaining_send_block_sec(guild_id),
        )
        await self._maybe_shadow_rant_for_blocked_guild(guild_id, context=context)

    async def _run_send_access_probe_loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            try:
                await self._probe_send_access_once()
            except Exception as exc:  # noqa: BLE001
                self.logger.log("send.probe_failed", error=str(exc)[:300])
            await asyncio.sleep(SEND_ACCESS_PROBE_INTERVAL_SEC)

    async def _run_onboarding_recheck_loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            try:
                await self.onboarding.process_pending_access_rechecks(self)
            except Exception as exc:  # noqa: BLE001
                self.logger.log("onboarding.recheck_loop_failed", error=str(exc)[:300])
            await asyncio.sleep(ONBOARDING_RECHECK_SCAN_INTERVAL_SEC)

    async def _probe_send_access_once(self) -> None:
        blocked_ids = [gid for gid in self._send_block_until_by_guild.keys() if self._is_send_blocked(gid)]
        for guild_id in blocked_ids:
            guild = self.get_guild(guild_id)
            if guild is None:
                continue
            if self._guild_has_send_access(guild):
                self._note_send_success(guild_id)
                self.logger.log("send.access_restored", guild_id=guild_id)
                continue
            await self._maybe_shadow_rant_for_blocked_guild(guild_id, context="send.probe")

    def _guild_has_send_access(self, guild: discord.Guild) -> bool:
        me = guild.me
        if me is None:
            return False
        for channel in guild.text_channels:
            perms = channel.permissions_for(me)
            if perms.view_channel and perms.send_messages:
                return True
        return False

    async def _maybe_shadow_rant_for_blocked_guild(self, guild_id: int, *, context: str) -> None:
        now = time.time()
        last = float(self._send_rant_ts_by_guild.get(guild_id, 0.0) or 0.0)
        if (now - last) < SEND_RANT_INTERVAL_SEC:
            return
        admin_guild = self.get_guild(self.settings.admin_guild_id)
        blocked_guild = self.get_guild(guild_id)
        if admin_guild is None:
            return
        guild_name = blocked_guild.name if blocked_guild else f"Guild {guild_id}"
        remaining = self._remaining_send_block_sec(guild_id)
        text = (
            f"Shadow update: send path blocked in `{guild_name}` (`{guild_id}`), "
            f"context=`{context}`, cooldown_remaining_sec=`{remaining}`. "
            "Holding outbound chatter until access returns."
        )
        try:
            await self._send_internal_note(f"[send.blocked] {text}")
            sent = True
        except discord.HTTPException:
            sent = False
        if sent:
            self._send_rant_ts_by_guild[guild_id] = now

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
        injection = self.ai.get_prompt_injection(satellite_guild.id)
        learning_mode = str(injection.get("learning_mode", "full"))
        prompt_chars = len(str(injection.get("effective_prompt", "") or ""))
        style_summary = self.ai.guild_style_summary(satellite_guild.id)
        memory_stats = self.ai.memory_stats(satellite_guild.id)
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
        api_failure_streak = 0
        api_cooldown_remain = 0
        if isinstance(last_test, dict) and last_test:
            outcome = "OK" if last_test.get("ok") else "FAIL"
            latency = last_test.get("latency_ms")
            last_test_line = f"{outcome} ({latency} ms): {str(last_test.get('detail', ''))[:120]}"
            api_failure_streak = int(last_test.get("failure_streak", 0) or 0)
            try:
                cooldown_until_ts = float(last_test.get("cooldown_until_ts", 0.0) or 0.0)
            except (TypeError, ValueError):
                cooldown_until_ts = 0.0
            api_cooldown_remain = max(0, int(cooldown_until_ts - time.time()))

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
                f"Prompt chars: `{prompt_chars}` learning_mode=`{learning_mode}`\n"
                f"Style profile: {style_summary[:120]}\n"
                f"Memory rows: long-term={memory_stats['long_term_rows']} "
                f"facts={memory_stats['fact_rows']} users={memory_stats['fact_users']}\n"
                f"Startup memory scan: {warmup_line}\n"
                f"Last API test: {last_test_line}\n"
                f"API failure streak: `{api_failure_streak}` cooldown_remaining_sec=`{api_cooldown_remain}`"
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
        bot_member = await get_bot_member(self, satellite_guild)
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
        if action not in MENU_ACTION_TIERS:
            await self._send_interaction_message(interaction, "Unknown action.", ephemeral=True)
            return
        required_tier = MENU_ACTION_TIERS[action]
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
        if self._can_control_satellite(user, satellite_guild_id, min_tier=required_tier):
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
        for name in ("data-lab", "debug-log", "diagnostics"):
            channel = discord.utils.get(admin_guild.text_channels, name=name)
            if isinstance(channel, discord.TextChannel):
                return channel
        return None

    async def _send_internal_note(self, text: str) -> None:
        """
        Internal-only note channel (data-lab/debug-log). Avoid leaking operational context into shadow channels.
        """
        payload = str(text or "").strip()
        if not payload:
            return
        channel = self._resolve_admin_debug_channel()
        if not channel:
            return
        try:
            await channel.send(payload[:1900])
        except discord.HTTPException:
            pass

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



