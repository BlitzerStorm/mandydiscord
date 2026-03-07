from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import discord


LOGGER = logging.getLogger("mandy.server_control")


class ServerControlService:
    """Central wrapper for autonomous Discord server mutation actions."""

    def __init__(self, bot: Any, logger_service: Any | None = None, maybe_logger: Any | None = None) -> None:
        """Store bot/logger dependencies used by all control operations."""
        if maybe_logger is not None:
            # Compatibility with legacy signature: (settings, store, logger)
            self.bot = bot
            self.logger_service = maybe_logger
        else:
            self.bot = bot
            self.logger_service = logger_service

    async def _log_action(self, action_name: str, target: str, reason: str = "autonomous") -> None:
        """Write autonomous action logs to logger service and mandy-thoughts when available."""
        line = f"[AUTONOMOUS] {action_name} on {target} - reason: {reason}"
        try:
            self.logger_service.log("autonomous.action", action=action_name, target=target, reason=reason)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed logger_service log for action %s", action_name)
        LOGGER.info(line)
        try:
            admin_guild_id = int(getattr(getattr(self.bot, "settings", None), "admin_guild_id", 0) or 0)
            if admin_guild_id <= 0:
                return
            guild = self.bot.get_guild(admin_guild_id)
            if guild is None:
                return
            channel = discord.utils.get(guild.text_channels, name="mandy-thoughts")
            if isinstance(channel, discord.TextChannel):
                await channel.send(line[:1900])
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed mandy-thoughts log for action %s", action_name)

    async def create_channel(
        self,
        guild: discord.Guild,
        name: str,
        category: discord.CategoryChannel | None = None,
        topic: str | None = None,
    ) -> discord.abc.GuildChannel | None:
        """Create a text channel in the target guild."""
        await self._log_action("create_channel", f"{guild.id}:{name}", "create channel")
        try:
            return await guild.create_text_channel(str(name)[:100], category=category, topic=(str(topic)[:1024] if topic else None))
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def delete_channel(self, channel: discord.abc.GuildChannel) -> bool:
        """Delete a guild channel."""
        await self._log_action("delete_channel", f"{channel.id}", "delete channel")
        try:
            await channel.delete(reason="Mandy autonomous action")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def rename_channel(self, channel: discord.abc.GuildChannel, name: str) -> bool:
        """Rename a guild channel."""
        await self._log_action("rename_channel", f"{channel.id}", "rename channel")
        try:
            await channel.edit(name=str(name)[:100], reason="Mandy autonomous action")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def set_topic(self, channel: discord.TextChannel, topic: str) -> bool:
        """Set a text channel topic."""
        await self._log_action("set_topic", f"{channel.id}", "set topic")
        try:
            await channel.edit(topic=str(topic)[:1024], reason="Mandy autonomous action")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def set_channel_topic(self, channel: discord.TextChannel, topic: str) -> bool:
        """Compatibility alias for setting a topic."""
        return await self.set_topic(channel, topic)

    async def set_slowmode(self, channel: discord.TextChannel, seconds: int) -> bool:
        """Set slowmode on a text channel."""
        await self._log_action("set_slowmode", f"{channel.id}", f"set slowmode={seconds}")
        try:
            await channel.edit(slowmode_delay=max(0, min(21600, int(seconds))), reason="Mandy autonomous action")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def lock_channel(self, channel: discord.TextChannel) -> bool:
        """Lock channel sends for @everyone."""
        await self._log_action("lock_channel", f"{channel.id}", "lock channel")
        try:
            await channel.set_permissions(channel.guild.default_role, send_messages=False, reason="Mandy autonomous action")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def unlock_channel(self, channel: discord.TextChannel) -> bool:
        """Unlock channel sends for @everyone."""
        await self._log_action("unlock_channel", f"{channel.id}", "unlock channel")
        try:
            await channel.set_permissions(channel.guild.default_role, send_messages=None, reason="Mandy autonomous action")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def pin_message(self, message: discord.Message) -> bool:
        """Pin a message."""
        await self._log_action("pin_message", f"{message.id}", "pin message")
        try:
            await message.pin(reason="Mandy autonomous action")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def unpin_message(self, message: discord.Message) -> bool:
        """Unpin a message."""
        await self._log_action("unpin_message", f"{message.id}", "unpin message")
        try:
            await message.unpin(reason="Mandy autonomous action")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def create_role(self, guild: discord.Guild, name: str, color: discord.Color | None = None) -> discord.Role | None:
        """Create a role in the guild."""
        await self._log_action("create_role", f"{guild.id}:{name}", "create role")
        try:
            kwargs: dict[str, Any] = {"name": str(name)[:100], "reason": "Mandy autonomous action"}
            if color is not None:
                kwargs["colour"] = color
            return await guild.create_role(**kwargs)
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def delete_role(self, role: discord.Role) -> bool:
        """Delete a role."""
        await self._log_action("delete_role", f"{role.id}", "delete role")
        try:
            await role.delete(reason="Mandy autonomous action")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def rename_role(self, role: discord.Role, name: str) -> bool:
        """Rename a role."""
        await self._log_action("rename_role", f"{role.id}", "rename role")
        try:
            await role.edit(name=str(name)[:100], reason="Mandy autonomous action")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def assign_role(self, member: discord.Member, role: discord.Role) -> bool:
        """Assign role to member."""
        await self._log_action("assign_role", f"{member.id}:{role.id}", "assign role")
        try:
            await member.add_roles(role, reason="Mandy autonomous action")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def remove_role(self, member: discord.Member, role: discord.Role) -> bool:
        """Remove role from member."""
        await self._log_action("remove_role", f"{member.id}:{role.id}", "remove role")
        try:
            await member.remove_roles(role, reason="Mandy autonomous action")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def nickname_member(self, *args: Any) -> bool:
        """Change a member nickname."""
        # Compatibility: nickname_member(member, nick) or nickname_member(guild, member, nick)
        if len(args) == 2:
            member = args[0]
            nick = args[1]
        elif len(args) >= 3:
            member = args[1]
            nick = args[2]
        else:
            return False
        await self._log_action("nickname_member", f"{getattr(member, 'id', 0)}", "nickname change")
        try:
            await member.edit(nick=str(nick)[:32], reason="Mandy autonomous action")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def kick_member(self, member: discord.Member, reason: str | None = None) -> bool:
        """Kick a guild member."""
        await self._log_action("kick_member", f"{member.id}", reason or "kick member")
        try:
            await member.kick(reason=(reason or "Mandy autonomous action")[:240])
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def timeout_member(self, member: discord.Member, duration_minutes: int) -> bool:
        """Timeout a guild member for a number of minutes."""
        await self._log_action("timeout_member", f"{member.id}", f"timeout {duration_minutes}m")
        try:
            until = discord.utils.utcnow() + timedelta(minutes=max(1, min(40320, int(duration_minutes))))
            await member.timeout(until, reason="Mandy autonomous action")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def bulk_delete(self, channel: discord.TextChannel, limit: int) -> int:
        """Delete up to `limit` recent messages in a channel."""
        await self._log_action("bulk_delete", f"{channel.id}", f"bulk delete {limit}")
        try:
            deleted = await channel.purge(limit=max(1, min(200, int(limit))), bulk=True, reason="Mandy autonomous action")
            return len(deleted)
        except (discord.Forbidden, discord.HTTPException):
            return 0

    async def send_message(self, channel: discord.abc.Messageable, content: str) -> discord.Message | None:
        """Send a plain text message."""
        await self._log_action("send_message", f"{getattr(channel, 'id', 0)}", "send message")
        try:
            return await channel.send(str(content)[:1900])
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def send_as_mandy(self, channel: discord.abc.Messageable, content: str) -> discord.Message | None:
        """Compatibility alias for plain send."""
        return await self.send_message(channel, content)

    async def send_embed(
        self,
        channel: discord.abc.Messageable,
        title: str,
        description: str,
        color: int = 0x5865F2,
        fields: list[dict[str, Any]] | None = None,
    ) -> discord.Message | None:
        """Send a basic embed message."""
        await self._log_action("send_embed", f"{getattr(channel, 'id', 0)}", "send embed")
        try:
            embed = discord.Embed(title=str(title)[:256], description=str(description)[:4096], color=int(color))
            for row in (fields or [])[:10]:
                if not isinstance(row, dict):
                    continue
                embed.add_field(
                    name=str(row.get("name", ""))[:256] or "Field",
                    value=str(row.get("value", ""))[:1024] or "-",
                    inline=bool(row.get("inline", False)),
                )
            return await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def add_reaction(self, message: discord.Message, emoji: str) -> bool:
        """Add a reaction to a message."""
        await self._log_action("add_reaction", f"{message.id}", "add reaction")
        try:
            await message.add_reaction(str(emoji)[:32])
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def react(self, message: discord.Message, emoji: str) -> bool:
        """Compatibility alias for add_reaction."""
        return await self.add_reaction(message, emoji)

    async def set_server_name(self, guild: discord.Guild, name: str) -> bool:
        """Rename a guild."""
        await self._log_action("set_server_name", f"{guild.id}", "rename server")
        try:
            await guild.edit(name=str(name)[:100], reason="Mandy autonomous action")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def create_invite(self, channel: discord.TextChannel, max_uses: int = 50, max_age: int = 86400) -> discord.Invite | None:
        """Create an invite for a channel."""
        await self._log_action("create_invite", f"{channel.id}", "create invite")
        try:
            return await channel.create_invite(max_uses=max(0, int(max_uses)), max_age=max(0, int(max_age)), reason="Mandy autonomous action")
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def list_members(self, guild: discord.Guild) -> list[dict[str, Any]]:
        """Return a compact member list."""
        await self._log_action("list_members", f"{guild.id}", "list members")
        try:
            return [{"id": m.id, "name": m.display_name, "bot": m.bot} for m in guild.members]
        except Exception:  # noqa: BLE001
            return []

    async def list_channels(self, guild: discord.Guild) -> list[dict[str, Any]]:
        """Return a compact channel list."""
        await self._log_action("list_channels", f"{guild.id}", "list channels")
        try:
            return [{"id": c.id, "name": c.name, "type": str(c.type)} for c in guild.channels]
        except Exception:  # noqa: BLE001
            return []

    async def dispatch_action(self, guild: discord.Guild, payload: dict[str, Any], *, source_message: discord.Message | None = None) -> bool:
        """Execute a structured autonomous action payload."""
        try:
            action = str(payload.get("action", "")).strip()
            if not action:
                return False
            target_id = int(payload.get("target", 0) or payload.get("channel_id", 0) or payload.get("message_id", 0) or 0)
            reason = str(payload.get("reason", "autonomous")).strip()[:220] or "autonomous"
            params = payload.get("params", {})
            if not isinstance(params, dict):
                params = {}

            if action == "nickname_member":
                member = guild.get_member(target_id)
                value = str(payload.get("value", "") or params.get("nick", "")).strip()
                return await self.nickname_member(member, value[:32]) if member is not None and value else False
            if action == "create_channel":
                name = str(payload.get("name", "") or params.get("name", "")).strip()
                topic = str(payload.get("topic", "") or params.get("topic", "")).strip()
                return (await self.create_channel(guild, name=name, topic=topic or None)) is not None if name else False
            if action == "delete_channel":
                channel = guild.get_channel(target_id)
                return await self.delete_channel(channel) if channel is not None else False
            if action == "pin_message":
                if source_message is None:
                    return False
                message = source_message if source_message.id == target_id or target_id == 0 else None
                if message is None and target_id > 0:
                    try:
                        message = await source_message.channel.fetch_message(target_id)
                    except Exception:  # noqa: BLE001
                        message = None
                return await self.pin_message(message) if message is not None else False
            if action == "set_slowmode":
                channel = guild.get_channel(target_id)
                seconds = int(payload.get("seconds", 0) or params.get("seconds", 0) or 0)
                return await self.set_slowmode(channel, seconds) if isinstance(channel, discord.TextChannel) else False
            if action == "rename_channel":
                channel = guild.get_channel(target_id)
                name = str(payload.get("name", "") or params.get("name", "")).strip()
                return await self.rename_channel(channel, name) if channel is not None and name else False
            if action == "set_channel_topic":
                channel = guild.get_channel(target_id)
                topic = str(payload.get("topic", "") or params.get("topic", "")).strip()
                return await self.set_topic(channel, topic) if isinstance(channel, discord.TextChannel) else False
            if action == "lock_channel":
                channel = guild.get_channel(target_id)
                return await self.lock_channel(channel) if isinstance(channel, discord.TextChannel) else False
            if action == "unlock_channel":
                channel = guild.get_channel(target_id)
                return await self.unlock_channel(channel) if isinstance(channel, discord.TextChannel) else False
            if action == "create_role":
                name = str(payload.get("name", "") or params.get("name", "")).strip()
                return (await self.create_role(guild, name=name)) is not None if name else False
            if action == "delete_role":
                role = guild.get_role(target_id)
                return await self.delete_role(role) if role is not None else False
            if action == "assign_role":
                member = guild.get_member(int(payload.get("target", 0) or 0))
                role = guild.get_role(int(payload.get("role_id", 0) or params.get("role_id", 0) or 0))
                return await self.assign_role(member, role) if member is not None and role is not None else False
            if action == "remove_role":
                member = guild.get_member(int(payload.get("target", 0) or 0))
                role = guild.get_role(int(payload.get("role_id", 0) or params.get("role_id", 0) or 0))
                return await self.remove_role(member, role) if member is not None and role is not None else False
            if action == "rename_role":
                role = guild.get_role(target_id)
                name = str(payload.get("name", "") or params.get("name", "")).strip()
                return await self.rename_role(role, name) if role is not None and name else False
            if action == "set_server_name":
                name = str(payload.get("name", "") or params.get("name", "")).strip()
                return await self.set_server_name(guild, name) if name else False
            if action == "bulk_delete":
                channel = guild.get_channel(target_id)
                limit = int(payload.get("limit", 10) or params.get("limit", 10))
                return (await self.bulk_delete(channel, limit)) > 0 if isinstance(channel, discord.TextChannel) else False
            if action == "timeout_member":
                member = guild.get_member(target_id)
                minutes = int(payload.get("duration_minutes", 5) or params.get("duration_minutes", 5))
                return await self.timeout_member(member, minutes) if member is not None else False
            if action == "kick_member":
                member = guild.get_member(target_id)
                return await self.kick_member(member, reason=reason) if member is not None else False
            return False
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed autonomous action dispatch.")
            return False
