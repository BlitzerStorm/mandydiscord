from __future__ import annotations

import time
from typing import Any

import discord

from mandy_v1.config import Settings
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore

SUPER_USER_ID = 741470965359443970
from mandy_v1.utils.discord_utils import get_bot_member


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
        node.setdefault("blocked_user_ids", [])
        node.setdefault("invite_min_affinity", 0.15)
        node.setdefault("invite_cooldown_sec", 7 * 24 * 60 * 60)
        node.setdefault("ai_enabled", True)
        node.setdefault("loop_interval_sec", 150)
        node.setdefault("max_actions_per_cycle", 3)
        node.setdefault("last_cycle_ts", 0.0)
        node.setdefault("last_cycle_results", [])
        node.setdefault("last_structure_sync_ts", 0.0)
        node.setdefault("structure_sync_min_interval_sec", 20 * 60)
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

    def blocked_ids(self) -> set[int]:
        out: set[int] = set()
        for raw in self.root().get("blocked_user_ids", []):
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                out.add(value)
        return out

    def invite_min_affinity(self) -> float:
        try:
            value = float(self.root().get("invite_min_affinity", 0.15) or 0.15)
        except (TypeError, ValueError):
            value = 0.15
        return max(-5.0, min(5.0, value))

    def invite_cooldown_sec(self) -> int:
        try:
            value = int(self.root().get("invite_cooldown_sec", 7 * 24 * 60 * 60) or (7 * 24 * 60 * 60))
        except (TypeError, ValueError):
            value = 7 * 24 * 60 * 60
        return max(0, min(60 * 60 * 24 * 60, value))

    def _relationship_row(self, user_id: int) -> dict[str, Any]:
        ai = self.store.data.setdefault("ai", {})
        rel = ai.setdefault("relationships", {})
        if not isinstance(rel, dict):
            ai["relationships"] = {}
            rel = ai["relationships"]
        key = str(int(user_id))
        row = rel.get(key)
        if not isinstance(row, dict):
            row = {"affinity": 0.0, "risk_flags": [], "last_invited_ts": 0.0, "invite_count": 0}
            rel[key] = row
            self.store.touch()
        return row

    def can_invite_user(self, user_id: int, *, guild: discord.Guild) -> tuple[bool, str]:
        uid = int(user_id)
        if uid <= 0:
            return False, "invalid user_id"
        if uid in self._protected_ids(guild):
            return False, "protected user"
        if uid in self.blocked_ids():
            return False, "blocked user"
        row = self._relationship_row(uid)
        try:
            affinity = float(row.get("affinity", 0.0) or 0.0)
        except (TypeError, ValueError):
            affinity = 0.0
        flags = row.get("risk_flags", [])
        if not isinstance(flags, list):
            flags = []
        if flags:
            return False, f"risk_flags={','.join(str(f)[:20] for f in flags[:3])}"
        if affinity < self.invite_min_affinity():
            return False, f"affinity={affinity:.2f} below_min={self.invite_min_affinity():.2f}"
        try:
            last_invited = float(row.get("last_invited_ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            last_invited = 0.0
        cooldown = self.invite_cooldown_sec()
        if cooldown > 0 and last_invited > 0 and (time.time() - last_invited) < cooldown:
            return False, "invite cooldown active"
        return True, "ok"

    async def ensure_structure(self, guild: discord.Guild, *, force: bool = False) -> None:
        root = self.root()
        now = time.time()
        try:
            min_interval = int(root.get("structure_sync_min_interval_sec", 20 * 60) or (20 * 60))
        except (TypeError, ValueError):
            min_interval = 20 * 60
        min_interval = max(60, min(6 * 60 * 60, min_interval))
        try:
            last_sync_ts = float(root.get("last_structure_sync_ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            last_sync_ts = 0.0
        if not force and last_sync_ts > 0 and (now - last_sync_ts) < min_interval:
            return

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
        root["last_structure_sync_ts"] = now
        self.store.touch()

    async def send_invite(self, bot: discord.Client, target_user: discord.User | discord.Member) -> str:
        admin_guild = bot.get_guild(self.settings.admin_guild_id)
        if not admin_guild:
            raise RuntimeError("Admin hub not found.")
        if int(target_user.id) in self._protected_ids(admin_guild):
            raise RuntimeError("Target is protected.")
        await self.ensure_structure(admin_guild)
        me = await get_bot_member(bot, admin_guild)
        if me is None:
            raise RuntimeError("Bot member unavailable in admin hub (cache/intents issue).")
        invite_channel = self._pick_invite_channel(admin_guild, me)
        if invite_channel is None:
            raise RuntimeError("No shadow channel with invite permissions.")
        invite = await invite_channel.create_invite(max_age=86400, max_uses=1, reason="Shadow League invite")
        pending = set(self.root().get("pending_user_ids", []))
        pending.add(int(target_user.id))
        self.root()["pending_user_ids"] = sorted(pending)
        self.store.touch()
        try:
            await target_user.send(
                "You are invited to the Shadow League.\n"
                f"Use this single-use invite: {invite.url}"
            )
        except discord.Forbidden as exc:
            self.logger.log("shadow.invite_dm_failed", user_id=target_user.id, invite_url=invite.url, error=str(exc)[:240])
            raise RuntimeError(f"Invite created but could not DM user (DMs disabled?). Invite: {invite.url}") from exc
        except discord.HTTPException as exc:
            self.logger.log("shadow.invite_dm_failed", user_id=target_user.id, invite_url=invite.url, error=str(exc)[:240])
            raise RuntimeError(f"Invite created but DM failed. Invite: {invite.url}") from exc
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
        excluded = sorted(set(members) | set(pending) | self._protected_ids(guild) | self.blocked_ids())
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
                        allowed, why = self.can_invite_user(uid, guild=guild)
                        if not allowed:
                            ok = False
                            detail = f"invite gated: {why}"
                        else:
                            user = bot.get_user(uid)
                            if user is None:
                                user = await bot.fetch_user(uid)
                            invite = await self.send_invite(bot, user)
                            rel = self._relationship_row(uid)
                            rel["last_invited_ts"] = time.time()
                            rel["invite_count"] = int(rel.get("invite_count", 0) or 0) + 1
                            self.store.touch()
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

    def _pick_invite_channel(self, guild: discord.Guild, me: discord.Member) -> discord.TextChannel | None:
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
        return {int(SUPER_USER_ID), int(guild.owner_id)}

    def _extract_user_id(self, action: dict[str, Any]) -> int:
        for key in ("user_id", "target_user_id", "member_id"):
            try:
                value = int(action.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return 0
