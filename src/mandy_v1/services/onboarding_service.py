from __future__ import annotations

import discord

from mandy_v1.config import Settings
from mandy_v1.utils.discord_utils import get_bot_member
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore


class OnboardingService:
    def __init__(self, settings: Settings, store: MessagePackStore, logger: LoggerService) -> None:
        self.settings = settings
        self.store = store
        self.logger = logger

    def bypass_set(self) -> set[int]:
        return set(self.store.data["onboarding"].get("bypass_user_ids", []))

    def mark_bypass(self, user_id: int) -> None:
        ids = self.bypass_set()
        ids.add(user_id)
        self.store.data["onboarding"]["bypass_user_ids"] = sorted(ids)
        self.store.touch()

    async def send_invite(self, bot: discord.Client, target_user: discord.User | discord.Member) -> str:
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
        try:
            await target_user.send(
                "You were onboarded into Mandy SOC.\n"
                f"Join the Admin Hub with this one-time invite: {invite.url}"
            )
        except discord.Forbidden as exc:
            self.logger.log("onboarding.invite_dm_failed", user_id=target_user.id, invite_url=invite.url, error=str(exc)[:240])
            raise RuntimeError(f"Invite created but could not DM user (DMs disabled?). Invite: {invite.url}") from exc
        except discord.HTTPException as exc:
            self.logger.log("onboarding.invite_dm_failed", user_id=target_user.id, invite_url=invite.url, error=str(exc)[:240])
            raise RuntimeError(f"Invite created but DM failed. Invite: {invite.url}") from exc
        self.logger.log("onboarding.invite_sent", user_id=target_user.id, invite_url=invite.url)
        return invite.url
