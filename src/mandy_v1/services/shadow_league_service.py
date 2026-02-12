from __future__ import annotations

import time
from typing import Any

import discord

from mandy_v1.config import Settings
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore


SHADOW_ROLE_NAME = "SHADOW:Associate"
SHADOW_CATEGORY_NAME = "SHADOW LEAGUE"
SHADOW_CHANNEL_PRIORITY = ("shadow-council", "shadow-ops", "shadow-lounge")


class ShadowLeagueService:
    def __init__(self, settings: Settings, store: MessagePackStore, logger: LoggerService) -> None:
        self.settings = settings
        self.store = store
        self.logger = logger

    def root(self) -> dict[str, object]:
        node = self.store.data.setdefault("shadow_league", {})
        node.setdefault("pending_user_ids", [])
        node.setdefault("member_user_ids", [])
        node.setdefault("nickname_map", {})
        node.setdefault("ai_enabled", True)
        node.setdefault("loop_interval_sec", 150)
        node.setdefault("max_actions_per_cycle", 3)
        node.setdefault("last_cycle_ts", 0.0)
        node.setdefault("last_cycle_results", [])
        return node

    def ai_enabled(self) -> bool:
        return bool(self.root().get("ai_enabled", True))

    def loop_interval_sec(self) -> int:
        return max(45, min(1800, int(self.root().get("loop_interval_sec", 150) or 150)))

    def max_actions_per_cycle(self) -> int:
        return max(1, min(5, int(self.root().get("max_actions_per_cycle", 3) or 3)))

    def pending_ids(self) -> set[int]:
        out: set[int] = set()
        for raw in self.root().get("pending_user_ids", []):
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                out.add(value)
        return out

    def member_ids(self) -> set[int]:
        out: set[int] = set()
        for raw in self.root().get("member_user_ids", []):
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                out.add(value)
        return out

    async def ensure_structure(self, guild: discord.Guild) -> None:
        role = await self._ensure_role(guild)
        for category in guild.categories:
            if category.name == SHADOW_CATEGORY_NAME:
                await category.set_permissions(guild.default_role, view_channel=False)
                await category.set_permissions(role, view_channel=True, send_messages=True, read_message_history=True)
                admin = discord.utils.get(guild.roles, name="ACCESS:Admin")
                soc = discord.utils.get(guild.roles, name="ACCESS:SOC")
                if admin:
                    await category.set_permissions(admin, view_channel=True, send_messages=True, read_message_history=True)
                if soc:
                    await category.set_permissions(soc, view_channel=True, send_messages=True, read_message_history=True)
            else:
                # Shadow members can only see shadow channels.
                await category.set_permissions(role, view_channel=False)
        self.store.touch()

    async def send_invite(self, bot: discord.Client, target_user: discord.User | discord.Member) -> str:
        admin_guild = bot.get_guild(self.settings.admin_guild_id)
        if not admin_guild:
            raise RuntimeError("Admin hub not found.")
        if int(target_user.id) in self._protected_ids(admin_guild):
            raise RuntimeError("Target is protected.")
        await self.ensure_structure(admin_guild)
        invite_channel = self._pick_invite_channel(admin_guild)
        if invite_channel is None:
            raise RuntimeError("No shadow channel with invite permissions.")
        invite = await invite_channel.create_invite(max_age=86400, max_uses=1, reason="Shadow League invite")
        pending = set(self.root().get("pending_user_ids", []))
        pending.add(int(target_user.id))
        self.root()["pending_user_ids"] = sorted(pending)
        self.store.touch()
        await target_user.send(
            "You are invited to the Shadow League.\n"
            f"Use this single-use invite: {invite.url}"
        )
        self.logger.log("shadow.invite_sent", user_id=target_user.id, invite_url=invite.url)
        return invite.url

    async def send_council_message(self, guild: discord.Guild, content: str, *, reason: str = "Shadow cycle") -> bool:
        text = content.strip()
        if not text:
            return False
        channel = self._pick_primary_shadow_channel(guild)
        if not channel:
            return False
        await channel.send(text[:1900])
        self.logger.log("shadow.council_message", guild_id=guild.id, reason=reason, chars=len(text))
        return True

    async def activate_member(self, member: discord.Member, *, reason: str) -> bool:
        if member.guild.id != self.settings.admin_guild_id:
            return False
        uid = int(member.id)
        root = self.root()
        pending = set(root.get("pending_user_ids", []))
        members = set(root.get("member_user_ids", []))
        should_activate = uid in pending or uid in members
        if not should_activate:
            return False
        role = await self._ensure_role(member.guild)
        if role not in member.roles:
            await member.add_roles(role, reason=reason[:240] or "Shadow League activation")
        pending.discard(uid)
        members.add(uid)
        root["pending_user_ids"] = sorted(pending)
        root["member_user_ids"] = sorted(members)
        nick = str(root.get("nickname_map", {}).get(str(uid), "")).strip()
        if nick:
            try:
                await member.edit(nick=nick[:32], reason="Shadow League nickname sync")
            except discord.HTTPException:
                pass
        self.store.touch()
        self.logger.log("shadow.member_activated", user_id=uid, guild_id=member.guild.id)
        return True

    async def add_existing_member(self, member: discord.Member) -> None:
        root = self.root()
        members = set(root.get("member_user_ids", []))
        members.add(int(member.id))
        root["member_user_ids"] = sorted(members)
        self.store.touch()
        await self.activate_member(member, reason="Shadow League manual add")

    async def remove_member(self, member: discord.Member) -> bool:
        role = discord.utils.get(member.guild.roles, name=SHADOW_ROLE_NAME)
        removed = False
        if role and role in member.roles:
            await member.remove_roles(role, reason="Shadow League manual remove")
            removed = True
        uid = int(member.id)
        root = self.root()
        pending = set(root.get("pending_user_ids", []))
        members = set(root.get("member_user_ids", []))
        if uid in pending:
            pending.remove(uid)
            removed = True
        if uid in members:
            members.remove(uid)
            removed = True
        root["pending_user_ids"] = sorted(pending)
        root["member_user_ids"] = sorted(members)
        self.store.touch()
        if removed:
            self.logger.log("shadow.member_removed", user_id=uid, guild_id=member.guild.id)
        return removed

    async def set_nickname(self, member: discord.Member, nickname: str) -> None:
        nick = nickname.strip()[:32]
        root = self.root()
        nickname_map = root.setdefault("nickname_map", {})
        if nick:
            nickname_map[str(member.id)] = nick
        else:
            nickname_map.pop(str(member.id), None)
        self.store.touch()
        await member.edit(nick=nick or None, reason="Shadow League nickname update")
        self.logger.log("shadow.nickname_set", user_id=member.id, nickname=nick)

    def status_text(self, guild: discord.Guild) -> str:
        root = self.root()
        role = discord.utils.get(guild.roles, name=SHADOW_ROLE_NAME)
        category = discord.utils.get(guild.categories, name=SHADOW_CATEGORY_NAME)
        member_count = 0
        if role:
            member_count = sum(1 for member in guild.members if role in member.roles)
        pending = len(root.get("pending_user_ids", []))
        tracked = len(root.get("member_user_ids", []))
        ai_enabled = bool(root.get("ai_enabled", True))
        loop_interval = self.loop_interval_sec()
        return (
            f"Role: `{role.id if role else 0}` ({SHADOW_ROLE_NAME})\n"
            f"Category: `{category.id if category else 0}` ({SHADOW_CATEGORY_NAME})\n"
            f"Live members: `{member_count}`\n"
            f"Tracked members: `{tracked}`\n"
            f"Pending invites: `{pending}`\n"
            f"AI enabled: `{ai_enabled}` interval_sec=`{loop_interval}`"
        )

    def snapshot_for_ai(self, guild: discord.Guild) -> dict[str, Any]:
        members = sorted(self.member_ids())
        pending = sorted(self.pending_ids())
        excluded = sorted(set(members) | set(pending) | self._protected_ids(guild))
        return {
            "member_count": len(members),
            "pending_count": len(pending),
            "members_sample": members[:25],
            "excluded_user_ids": excluded[:120],
        }

    async def execute_ai_actions(
        self,
        bot: discord.Client,
        guild: discord.Guild,
        actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for action in actions[: self.max_actions_per_cycle()]:
            if not isinstance(action, dict):
                continue
            name = str(action.get("action", "")).strip().lower()
            ok = False
            detail = "ignored"
            try:
                if name == "invite_user":
                    uid = self._extract_user_id(action)
                    if uid <= 0:
                        detail = "invalid user_id"
                    elif uid in self.pending_ids() or uid in self.member_ids():
                        ok = True
                        detail = "already pending/member"
                    else:
                        user = bot.get_user(uid)
                        if user is None:
                            user = await bot.fetch_user(uid)
                        invite = await self.send_invite(bot, user)
                        ok = True
                        detail = f"invited {uid}: {invite}"
                elif name == "nickname_user":
                    uid = self._extract_user_id(action)
                    member = guild.get_member(uid) if uid > 0 else None
                    nickname = str(action.get("nickname", "")).strip()
                    if member is None:
                        detail = "member not in admin guild"
                    elif uid not in self.member_ids():
                        detail = "not a shadow member"
                    else:
                        await self.set_nickname(member, nickname)
                        ok = True
                        detail = f"nickname set for {uid}"
                elif name == "remove_user":
                    uid = self._extract_user_id(action)
                    member = guild.get_member(uid) if uid > 0 else None
                    if member is None:
                        detail = "member not in admin guild"
                    elif uid in self._protected_ids(guild):
                        detail = "protected member"
                    else:
                        removed = await self.remove_member(member)
                        ok = removed
                        detail = "removed" if removed else "no-op"
                elif name == "send_shadow_message":
                    content = str(action.get("content", "")).strip()
                    sent = await self.send_council_message(guild, content, reason="Shadow AI message")
                    ok = sent
                    detail = "message sent" if sent else "message skipped"
                else:
                    detail = f"unknown action `{name}`"
            except (discord.HTTPException, discord.Forbidden, RuntimeError) as exc:
                detail = str(exc)[:240]
            row = {"action": name, "ok": ok, "detail": detail}
            out.append(row)
            self.logger.log("shadow.ai_action", action=name, ok=ok, detail=detail[:160], guild_id=guild.id)
        root = self.root()
        root["last_cycle_ts"] = time.time()
        root["last_cycle_results"] = out[-20:]
        self.store.touch()
        return out

    async def _ensure_role(self, guild: discord.Guild) -> discord.Role:
        role = discord.utils.get(guild.roles, name=SHADOW_ROLE_NAME)
        if role is None:
            role = await guild.create_role(name=SHADOW_ROLE_NAME, mentionable=False, reason="Shadow League setup")
        return role

    def _pick_invite_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        me = guild.me
        if me is None:
            return None
        for name in SHADOW_CHANNEL_PRIORITY:
            channel = discord.utils.get(guild.text_channels, name=name)
            if isinstance(channel, discord.TextChannel) and channel.permissions_for(me).create_instant_invite:
                return channel
        category = discord.utils.get(guild.categories, name=SHADOW_CATEGORY_NAME)
        if category:
            for channel in guild.text_channels:
                if channel.category_id == category.id and channel.permissions_for(me).create_instant_invite:
                    return channel
        return None

    def _pick_primary_shadow_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        for name in SHADOW_CHANNEL_PRIORITY:
            channel = discord.utils.get(guild.text_channels, name=name)
            if isinstance(channel, discord.TextChannel):
                return channel
        category = discord.utils.get(guild.categories, name=SHADOW_CATEGORY_NAME)
        if category:
            for channel in guild.text_channels:
                if channel.category_id == category.id:
                    return channel
        return None

    def _protected_ids(self, guild: discord.Guild) -> set[int]:
        return {int(self.settings.god_user_id), int(guild.owner_id)}

    def _extract_user_id(self, action: dict[str, Any]) -> int:
        for key in ("user_id", "target_user_id", "member_id"):
            try:
                value = int(action.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return 0
