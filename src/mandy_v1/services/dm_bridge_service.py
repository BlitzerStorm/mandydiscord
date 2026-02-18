from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord

from mandy_v1.config import Settings
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore


class DMBridgeService:
    def __init__(self, settings: Settings, store: MessagePackStore, logger: LoggerService) -> None:
        self.settings = settings
        self.store = store
        self.logger = logger

    def root(self) -> dict[str, dict[str, Any]]:
        node = self.store.data.setdefault("dm_bridges", {})
        if not isinstance(node, dict):
            self.store.data["dm_bridges"] = {}
            self.store.touch()
            node = self.store.data["dm_bridges"]
        return node

    def list_user_ids(self) -> list[int]:
        out: list[int] = []
        for key in self.root().keys():
            try:
                user_id = int(key)
            except (TypeError, ValueError):
                continue
            if user_id > 0:
                out.append(user_id)
        return sorted(set(out))

    def bridge_row(self, user_id: int, *, create: bool = True) -> dict[str, Any] | None:
        key = str(int(user_id))
        node = self.root()
        row = node.get(key)
        changed = False
        if not isinstance(row, dict):
            if not create:
                return None
            row = {}
            node[key] = row
            changed = True
        changed = self._normalize_row(row) or changed
        if changed:
            self.store.touch()
        return row

    def _normalize_row(self, row: dict[str, Any]) -> bool:
        changed = False
        channel_id = self._to_positive_int(row.get("channel_id", 0))
        if self._to_positive_int(row.get("channel_id", 0)) != channel_id:
            row["channel_id"] = channel_id
            changed = True
        if "active" not in row:
            row["active"] = True
            changed = True
        else:
            active = bool(row.get("active", True))
            if row.get("active") is not active:
                row["active"] = active
                changed = True
        if "ai_enabled" not in row:
            row["ai_enabled"] = True
            changed = True
        else:
            ai_enabled = bool(row.get("ai_enabled", True))
            if row.get("ai_enabled") is not ai_enabled:
                row["ai_enabled"] = ai_enabled
                changed = True
        control_message_id = self._to_positive_int(row.get("control_message_id", 0))
        if self._to_positive_int(row.get("control_message_id", 0)) != control_message_id:
            row["control_message_id"] = control_message_id
            changed = True
        history_ids_raw = row.get("history_message_ids", [])
        history_ids: list[int] = []
        if "history_message_ids" not in row:
            row["history_message_ids"] = []
            changed = True
        if isinstance(history_ids_raw, list):
            for value in history_ids_raw:
                clean = self._to_positive_int(value)
                if clean > 0:
                    history_ids.append(clean)
        if history_ids_raw != history_ids:
            row["history_message_ids"] = history_ids
            changed = True
        history_count = max(0, self._to_int(row.get("history_count", 0), default=0))
        if "history_count" not in row:
            row["history_count"] = history_count
            changed = True
        elif self._to_int(row.get("history_count", 0), default=0) != history_count:
            row["history_count"] = history_count
            changed = True
        try:
            last_refresh_ts = float(row.get("last_refresh_ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            last_refresh_ts = 0.0
        if "last_refresh_ts" not in row:
            row["last_refresh_ts"] = last_refresh_ts
            changed = True
        elif row.get("last_refresh_ts") != last_refresh_ts:
            row["last_refresh_ts"] = last_refresh_ts
            changed = True
        if "last_refresh_reason" not in row:
            row["last_refresh_reason"] = ""
            changed = True
        return changed

    def _to_positive_int(self, value: Any) -> int:
        try:
            out = int(value)
        except (TypeError, ValueError):
            return 0
        return out if out > 0 else 0

    def _to_int(self, value: Any, *, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def parse_user_id_from_channel_name(self, channel_name: str | None) -> int | None:
        text = str(channel_name or "").strip()
        if not text.startswith("dm-"):
            return None
        tail = text.split("-", 1)[1].strip()
        if not tail.isdigit():
            return None
        user_id = int(tail)
        return user_id if user_id > 0 else None

    async def resolve_user(self, bot: discord.Client, user_id: int) -> discord.abc.User | None:
        uid = int(user_id)
        if uid <= 0:
            return None
        user = bot.get_user(uid)
        if user is not None:
            return user
        try:
            user = await bot.fetch_user(uid)
        except discord.HTTPException:
            return None
        return user

    async def ensure_channel(self, bot: discord.Client, user: discord.abc.User) -> discord.TextChannel | None:
        admin_guild = bot.get_guild(self.settings.admin_guild_id)
        if not admin_guild:
            return None
        category = discord.utils.get(admin_guild.categories, name="ENGINEERING")
        if category is None:
            category = await admin_guild.create_category("ENGINEERING", reason="Mandy v1 DM bridge setup")
        channel_name = f"dm-{user.id}"
        channel = discord.utils.get(admin_guild.text_channels, name=channel_name)
        if channel is None:
            channel = await category.create_text_channel(channel_name, reason="Mandy v1 DM bridge opened")
            await channel.send(f"DM bridge opened for <@{user.id}> (`{user.id}`)")
        row = self.bridge_row(int(user.id), create=True)
        if row is not None:
            if self._to_positive_int(row.get("channel_id", 0)) != int(channel.id):
                row["channel_id"] = int(channel.id)
                self.store.touch()
        return channel

    async def resolve_channel(self, bot: discord.Client, user_id: int) -> discord.TextChannel | None:
        admin_guild = bot.get_guild(self.settings.admin_guild_id)
        if not admin_guild:
            return None
        row = self.bridge_row(user_id, create=False)
        channel_id = 0
        if isinstance(row, dict):
            channel_id = self._to_positive_int(row.get("channel_id", 0))
        if channel_id > 0:
            channel = bot.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                channel = admin_guild.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                return channel
        channel = discord.utils.get(admin_guild.text_channels, name=f"dm-{int(user_id)}")
        if isinstance(channel, discord.TextChannel):
            row = self.bridge_row(user_id, create=True)
            if row is not None and self._to_positive_int(row.get("channel_id", 0)) != int(channel.id):
                row["channel_id"] = int(channel.id)
                self.store.touch()
            return channel
        user = await self.resolve_user(bot, user_id)
        if user is None:
            return None
        return await self.ensure_channel(bot, user)

    def is_active(self, user_id: int) -> bool:
        row = self.bridge_row(user_id, create=True)
        if not isinstance(row, dict):
            return True
        return bool(row.get("active", True))

    def set_active(self, user_id: int, active: bool) -> bool:
        row = self.bridge_row(user_id, create=True)
        if not isinstance(row, dict):
            return False
        current = bool(row.get("active", True))
        desired = bool(active)
        if current == desired:
            return current
        row["active"] = desired
        self.store.touch()
        return desired

    def is_ai_enabled(self, user_id: int) -> bool:
        row = self.bridge_row(user_id, create=True)
        if not isinstance(row, dict):
            return True
        return bool(row.get("ai_enabled", True))

    def set_ai_enabled(self, user_id: int, enabled: bool) -> bool:
        row = self.bridge_row(user_id, create=True)
        if not isinstance(row, dict):
            return False
        desired = bool(enabled)
        if bool(row.get("ai_enabled", True)) == desired:
            return desired
        row["ai_enabled"] = desired
        self.store.touch()
        return desired

    def toggle_ai_enabled(self, user_id: int) -> bool:
        current = self.is_ai_enabled(user_id)
        return self.set_ai_enabled(user_id, not current)

    def control_message_id(self, user_id: int) -> int:
        row = self.bridge_row(user_id, create=True)
        if not isinstance(row, dict):
            return 0
        return self._to_positive_int(row.get("control_message_id", 0))

    def set_control_message_id(self, user_id: int, message_id: int) -> int:
        row = self.bridge_row(user_id, create=True)
        if not isinstance(row, dict):
            return 0
        clean = self._to_positive_int(message_id)
        if self._to_positive_int(row.get("control_message_id", 0)) != clean:
            row["control_message_id"] = clean
            self.store.touch()
        return clean

    def history_message_ids(self, user_id: int) -> list[int]:
        row = self.bridge_row(user_id, create=True)
        if not isinstance(row, dict):
            return []
        raw = row.get("history_message_ids", [])
        if not isinstance(raw, list):
            row["history_message_ids"] = []
            self.store.touch()
            return []
        out: list[int] = []
        for value in raw:
            clean = self._to_positive_int(value)
            if clean > 0:
                out.append(clean)
        if out != raw:
            row["history_message_ids"] = out
            self.store.touch()
        return out

    def set_history_snapshot(
        self,
        user_id: int,
        *,
        message_ids: list[int],
        history_count: int,
        reason: str,
    ) -> None:
        row = self.bridge_row(user_id, create=True)
        if not isinstance(row, dict):
            return
        clean_ids = [mid for mid in (self._to_positive_int(v) for v in message_ids) if mid > 0]
        row["history_message_ids"] = clean_ids
        row["history_count"] = max(0, int(history_count))
        row["last_refresh_ts"] = float(datetime.now(tz=timezone.utc).timestamp())
        row["last_refresh_reason"] = str(reason or "")[:120]
        self.store.touch()

    def build_control_embed(self, user: discord.abc.User, row: dict[str, Any] | None = None) -> discord.Embed:
        state = row if isinstance(row, dict) else self.bridge_row(int(user.id), create=True) or {}
        active = bool(state.get("active", True))
        ai_enabled = bool(state.get("ai_enabled", True))
        history_count = max(0, self._to_int(state.get("history_count", 0), default=0))
        last_refresh_ts = float(state.get("last_refresh_ts", 0.0) or 0.0)
        if last_refresh_ts > 0:
            try:
                refresh_text = datetime.fromtimestamp(last_refresh_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            except (OverflowError, OSError, ValueError):
                refresh_text = "unknown"
        else:
            refresh_text = "never"
        reason = str(state.get("last_refresh_reason", "") or "").strip()
        embed = discord.Embed(
            title=f"DM Bridge Controls: {user}",
            description="Use this bar to open/close relay, toggle DM AI response, and refresh full DM history.",
            color=0x5865F2 if active else 0xED4245,
        )
        embed.add_field(
            name="Bridge State",
            value=(
                f"user_id=`{int(user.id)}`\n"
                f"active=`{active}`\n"
                f"ai_response=`{ai_enabled}`"
            ),
            inline=True,
        )
        embed.add_field(
            name="History",
            value=(
                f"messages=`{history_count}`\n"
                f"last_refresh=`{refresh_text}`\n"
                f"reason=`{reason or 'n/a'}`"
            ),
            inline=True,
        )
        embed.set_footer(text="History refresh always pulls the full DM transcript from Discord.")
        return embed

    async def relay_inbound(self, bot: discord.Client, message: discord.Message) -> bool:
        channel = await self.ensure_channel(bot, message.author)
        if not channel:
            return False
        if not self.is_active(int(message.author.id)):
            self.set_active(int(message.author.id), True)
            self.logger.log("dm_bridge.reactivated", user_id=message.author.id, channel_id=channel.id, reason="inbound")
        self.logger.log("dm_bridge.inbound", user_id=message.author.id, channel_id=channel.id)
        return True

    async def relay_outbound(self, bot: discord.Client, message: discord.Message) -> bool:
        if not isinstance(message.channel, discord.TextChannel):
            return False
        user_id = self.parse_user_id_from_channel_name(message.channel.name)
        if user_id is None:
            return False
        if not self.is_active(user_id):
            self.set_active(user_id, True)
            self.logger.log("dm_bridge.reactivated", user_id=user_id, source_channel_id=message.channel.id, reason="outbound")
        text = self._render_message_text(message.content, message.attachments, message.stickers)
        if not text:
            self.logger.log(
                "dm_bridge.outbound_skipped",
                user_id=user_id,
                source_channel_id=message.channel.id,
                reason="empty_payload",
            )
            return False
        user = await self.resolve_user(bot, user_id)
        if not user:
            self.logger.log("dm_bridge.outbound_skipped", user_id=user_id, source_channel_id=message.channel.id, reason="user_missing")
            return False
        try:
            await user.send(text)
        except discord.Forbidden as exc:
            self.logger.log(
                "dm_bridge.outbound_failed",
                user_id=user_id,
                source_channel_id=message.channel.id,
                reason="forbidden",
                error=str(exc)[:200],
            )
            return False
        except discord.HTTPException as exc:
            self.logger.log(
                "dm_bridge.outbound_failed",
                user_id=user_id,
                source_channel_id=message.channel.id,
                reason="http_error",
                error=str(exc)[:200],
            )
            return False
        self.logger.log("dm_bridge.outbound", user_id=user_id, source_channel_id=message.channel.id, chars=len(text))
        return True

    async def pull_full_history(
        self,
        bot: discord.Client,
        *,
        user_id: int,
    ) -> tuple[discord.abc.User, list[dict[str, Any]]]:
        user = await self.resolve_user(bot, user_id)
        if user is None:
            raise RuntimeError("User not found.")
        dm_channel = user.dm_channel
        if dm_channel is None:
            dm_channel = await user.create_dm()
        bot_user_id = int(bot.user.id) if bot.user else 0
        rows: list[dict[str, Any]] = []
        async for row in dm_channel.history(limit=None, oldest_first=True):
            rows.append(self._build_history_row(row, bot_user_id=bot_user_id))
        return user, rows

    def render_history_text(
        self,
        *,
        user: discord.abc.User,
        rows: list[dict[str, Any]],
    ) -> tuple[str, str]:
        header = [
            f"DM transcript for {user} ({int(user.id)})",
            f"Generated at {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"Total messages: {len(rows)}",
            "",
        ]
        body: list[str] = []
        for row in rows:
            body.append(
                f"[{row.get('created_at', '')}] "
                f"{row.get('direction', '')} "
                f"{row.get('author_name', '')} "
                f"({row.get('author_id', 0)})"
            )
            text = str(row.get("text", "") or "").strip() or "(no text)"
            for line in text.splitlines():
                body.append(f"  {line}")
            body.append("")
        if not body:
            body.append("(no messages found)")
        transcript = "\n".join(header + body).strip()

        preview_rows = rows[-12:]
        preview_lines: list[str] = []
        for row in preview_rows:
            text = str(row.get("text", "") or "").replace("\n", " | ").strip() or "(no text)"
            if len(text) > 140:
                text = f"{text[:137]}..."
            preview_lines.append(
                f"[{row.get('created_at', '')}] {row.get('direction', '')}: {text}"
            )
        preview = "\n".join(preview_lines) if preview_lines else "(no messages yet)"
        return transcript, preview

    def _build_history_row(self, message: discord.Message, *, bot_user_id: int) -> dict[str, Any]:
        created_at = message.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        author_id = int(message.author.id)
        author_name = str(getattr(message.author, "display_name", "") or getattr(message.author, "name", "") or author_id)
        direction = "OUTBOUND" if bot_user_id > 0 and author_id == bot_user_id else "INBOUND"
        text = self._render_message_text(message.content, message.attachments, message.stickers)
        if not text:
            text = "(no text)"
        return {
            "message_id": int(message.id),
            "created_at": created_at,
            "direction": direction,
            "author_id": author_id,
            "author_name": author_name,
            "text": text,
        }

    def _render_message_text(
        self,
        content: str | None,
        attachments: list[discord.Attachment],
        stickers: list[discord.StickerItem],
    ) -> str:
        out: list[str] = []
        text = str(content or "").strip()
        if text:
            out.append(text)
        urls = [str(item.url) for item in attachments if getattr(item, "url", None)]
        if urls:
            out.append("Attachments:")
            out.extend(urls)
        sticker_names = [str(item.name or "").strip() for item in stickers if str(item.name or "").strip()]
        if sticker_names:
            out.append("Stickers:")
            out.extend(sticker_names)
        return "\n".join(out).strip()
