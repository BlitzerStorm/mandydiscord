from __future__ import annotations

import time
from typing import Any

import discord

from mandy_v1.config import Settings
from mandy_v1.utils.discord_utils import get_bot_member
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore


class OnboardingService:
    BOT_OAUTH_INVITE_URL = (
        "https://discord.com/oauth2/authorize?client_id=1451713809281712128"
        "&scope=bot%20applications.commands&permissions=8"
    )
    ACCESS_RECHECK_INTERVAL_SEC = 10 * 60

    def __init__(self, settings: Settings, store: MessagePackStore, logger: LoggerService) -> None:
        self.settings = settings
        self.store = store
        self.logger = logger

    def root(self) -> dict[str, Any]:
        node = self.store.data.setdefault("onboarding", {})
        if not isinstance(node.get("bypass_user_ids"), list):
            node["bypass_user_ids"] = []
        pending = node.setdefault("pending_access_rechecks", {})
        if not isinstance(pending, dict):
            node["pending_access_rechecks"] = {}
        return node

    def bypass_set(self) -> set[int]:
        return set(self.root().get("bypass_user_ids", []))

    def pending_rechecks(self) -> dict[str, dict[str, float]]:
        root = self.root()
        pending = root.setdefault("pending_access_rechecks", {})
        out: dict[str, dict[str, float]] = {}
        for key, value in pending.items():
            if isinstance(key, str) and isinstance(value, dict):
                out[key] = value
        return out

    def mark_bypass(self, user_id: int) -> None:
        ids = self.bypass_set()
        ids.add(user_id)
        self.root()["bypass_user_ids"] = sorted(ids)
        self.store.touch()

    def queue_access_recheck(self, user_id: int, *, next_check_ts: float | None = None) -> None:
        if next_check_ts is None:
            next_check_ts = time.time() + self.ACCESS_RECHECK_INTERVAL_SEC
        pending = self.pending_rechecks()
        pending[str(int(user_id))] = {
            "next_check_ts": float(next_check_ts),
            "last_notice_ts": float(pending.get(str(int(user_id)), {}).get("last_notice_ts", 0.0) or 0.0),
            "created_ts": float(pending.get(str(int(user_id)), {}).get("created_ts", time.time()) or time.time()),
        }
        self.root()["pending_access_rechecks"] = pending
        self.store.touch()

    def clear_access_recheck(self, user_id: int) -> bool:
        pending = self.pending_rechecks()
        existed = pending.pop(str(int(user_id)), None) is not None
        if existed:
            self.root()["pending_access_rechecks"] = pending
            self.store.touch()
        return existed

    def has_shared_satellite(self, bot: discord.Client, user_id: int) -> bool:
        for guild in bot.guilds:
            if guild.id == self.settings.admin_guild_id:
                continue
            if guild.get_member(user_id) is not None:
                return True
        return False

    async def _notify_no_server_access(self, bot: discord.Client, user_id: int) -> bool:
        user = bot.get_user(user_id)
        if user is None:
            try:
                user = await bot.fetch_user(user_id)
            except discord.HTTPException:
                return False
        try:
            await user.send("No server access noticed waiting recheck in 10m")
        except discord.HTTPException:
            return False
        return True

    async def process_pending_access_rechecks(self, bot: discord.Client) -> None:
        admin_guild = bot.get_guild(self.settings.admin_guild_id)
        if admin_guild is None:
            return
        pending = self.pending_rechecks()
        if not pending:
            return
        now = time.time()
        changed = False
        for user_id_text in list(pending.keys()):
            row = pending.get(user_id_text) or {}
            try:
                user_id = int(user_id_text)
            except ValueError:
                pending.pop(user_id_text, None)
                changed = True
                continue
            member = admin_guild.get_member(user_id)
            if member is None:
                continue
            next_check_ts = float(row.get("next_check_ts", 0.0) or 0.0)
            if next_check_ts > now:
                continue
            if self.has_shared_satellite(bot, user_id):
                try:
                    await bot.mirrors.sync_admin_member_access(bot, member, self.bypass_set())
                except Exception as exc:  # noqa: BLE001
                    self.logger.log("onboarding.access_sync_failed", user_id=user_id, error=str(exc)[:240])
                else:
                    pending.pop(user_id_text, None)
                    changed = True
                    self.logger.log("onboarding.access_synced_after_recheck", user_id=user_id)
                continue
            dm_ok = await self._notify_no_server_access(bot, user_id)
            row["last_notice_ts"] = now
            row["next_check_ts"] = now + self.ACCESS_RECHECK_INTERVAL_SEC
            pending[user_id_text] = row
            changed = True
            self.logger.log("onboarding.access_recheck_notice", user_id=user_id, dm_ok=dm_ok)
        if changed:
            self.root()["pending_access_rechecks"] = pending
            self.store.touch()

    async def handle_admin_member_join(self, bot: discord.Client, member: discord.Member) -> None:
        if member.guild.id != self.settings.admin_guild_id or member.bot:
            return
        if str(member.id) not in self.pending_rechecks():
            return
        if self.has_shared_satellite(bot, member.id):
            await bot.mirrors.sync_admin_member_access(bot, member, self.bypass_set())
            self.clear_access_recheck(member.id)
            self.logger.log("onboarding.access_synced_on_join", user_id=member.id)
            return
        self.queue_access_recheck(member.id)
        self.logger.log("onboarding.access_recheck_queued", user_id=member.id)

    async def send_invite(self, bot: discord.Client, target_user: discord.User | discord.Member) -> str:
        self.root()
        admin_guild = bot.get_guild(self.settings.admin_guild_id)
        if not admin_guild:
            raise RuntimeError("Admin hub not found.")
        me = await get_bot_member(bot, admin_guild)
        if me is None:
            raise RuntimeError("Bot member unavailable in admin hub (cache/intents issue).")

        invite_channel = admin_guild.system_channel
        if invite_channel is not None:
            perms = invite_channel.permissions_for(me)
            if not (perms.view_channel and perms.create_instant_invite):
                invite_channel = None
        if invite_channel is None:
            invite_channel = next(
                (
                    c
                    for c in admin_guild.text_channels
                    if c.permissions_for(me).view_channel and c.permissions_for(me).create_instant_invite
                ),
                None,
            )
        if invite_channel is None:
            raise RuntimeError("No admin hub channel with invite permissions.")
        invite = await invite_channel.create_invite(max_age=86400, max_uses=1, reason="Mandy v1 onboarding")
        self.mark_bypass(target_user.id)
        self.queue_access_recheck(target_user.id)
        self.store.touch()
        try:
            await target_user.send(
                "You were onboarded into Mandy SOC.\n"
                f"Add the bot to your server: {self.BOT_OAUTH_INVITE_URL}\n"
                f"Join the Admin Hub with this one-time invite: {invite.url}"
            )
        except discord.Forbidden as exc:
            self.logger.log("onboarding.invite_dm_failed", user_id=target_user.id, invite_url=invite.url, error=str(exc)[:240])
            raise RuntimeError(f"Invite created but could not DM user (DMs disabled?). Invite: {invite.url}") from exc
        except discord.HTTPException as exc:
            self.logger.log("onboarding.invite_dm_failed", user_id=target_user.id, invite_url=invite.url, error=str(exc)[:240])
            raise RuntimeError(f"Invite created but DM failed. Invite: {invite.url}") from exc
        self.logger.log(
            "onboarding.invite_sent",
            user_id=target_user.id,
            invite_url=invite.url,
            bot_invite_url=self.BOT_OAUTH_INVITE_URL,
        )
        return invite.url
