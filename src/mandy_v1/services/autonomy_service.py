from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord

from mandy_v1.config import Settings
from mandy_v1.services.ai_service import AIService
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore


CONTROL_CHANNELS = {"admin-chat", "server-management", "incident-room", "layout-control"}


class AutonomyService:
    def __init__(self, settings: Settings, store: MessagePackStore, logger: LoggerService, ai: AIService) -> None:
        self.settings = settings
        self.store = store
        self.logger = logger
        self.ai = ai
        self.workspace_root = Path.cwd().resolve()
        self._alias_regex = re.compile(r"\b(?:mandy|mandi|mndy|mdy|mandee)\b", re.IGNORECASE)
        self._intent_regex = re.compile(
            r"\b(?:create|delete|rename|invite|kick|ban|role|channel|server|clean|purge|organize|file|code|read|write|patch|fix|implement)\b",
            re.IGNORECASE,
        )

    def root(self) -> dict[str, Any]:
        node = self.store.data.setdefault("autonomy", {})
        node.setdefault("enabled", True)
        node.setdefault("write_guild_id", int(self.settings.admin_guild_id))
        node.setdefault("observe_other_guilds", True)
        node.setdefault("allow_file_tools", True)
        node.setdefault("max_actions_per_cycle", 6)
        node.setdefault("last_run_ts", 0.0)
        node.setdefault("protected_user_ids", [int(self.settings.god_user_id)])
        node.setdefault("protected_role_names", ["ACCESS:SOC"])
        node.setdefault("observations", [])
        node.setdefault("journal", [])
        if int(node.get("write_guild_id", 0) or 0) <= 0:
            node["write_guild_id"] = int(self.settings.admin_guild_id)
        return node

    def is_enabled(self) -> bool:
        return bool(self.root().get("enabled", True))

    def set_enabled(self, enabled: bool) -> None:
        self.root()["enabled"] = bool(enabled)
        self.store.touch()

    def status_snapshot(self, bot: discord.Client) -> str:
        node = self.root()
        write_guild_id = int(node.get("write_guild_id", self.settings.admin_guild_id) or self.settings.admin_guild_id)
        write_guild = bot.get_guild(write_guild_id)
        last = float(node.get("last_run_ts", 0.0) or 0.0)
        last_text = "never" if last <= 0 else datetime.fromtimestamp(last, tz=timezone.utc).isoformat()[:19]
        return (
            f"enabled=`{bool(node.get('enabled', True))}` "
            f"write_guild=`{write_guild_id}` ({write_guild.name if write_guild else 'unavailable'}) "
            f"observe_other_guilds=`{bool(node.get('observe_other_guilds', True))}` "
            f"file_tools=`{bool(node.get('allow_file_tools', True))}` "
            f"observations=`{len(node.get('observations', []))}` "
            f"journal=`{len(node.get('journal', []))}` "
            f"last_run_utc=`{last_text}`"
        )

    def observe_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        node = self.root()
        if message.guild.id != self.settings.admin_guild_id and not bool(node.get("observe_other_guilds", True)):
            return
        text = " ".join(message.clean_content.split())[:500]
        if not text and not message.attachments:
            return
        rows = node["observations"]
        rows.append(
            {
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "guild_id": int(message.guild.id),
                "guild_name": str(message.guild.name)[:80],
                "channel_id": int(message.channel.id),
                "channel_name": str(getattr(message.channel, "name", "unknown"))[:80],
                "author_id": int(message.author.id),
                "author_name": str(message.author.display_name)[:80],
                "text": text,
            }
        )
        if len(rows) > 700:
            del rows[: len(rows) - 700]
        self.store.touch()

    async def maybe_run_from_message(self, bot: discord.Client, message: discord.Message) -> bool:
        if not self.is_enabled() or not message.guild:
            return False
        if message.guild.id != self.settings.admin_guild_id:
            return False
        if not self._should_consider_message(bot, message):
            return False
        return await self.run_with_text(
            bot=bot,
            guild=message.guild,
            channel=message.channel,
            actor=message.author,
            prompt_text=message.clean_content,
        )

    async def run_with_text(
        self,
        *,
        bot: discord.Client,
        guild: discord.Guild,
        channel: discord.abc.MessageableChannel,
        actor: discord.abc.User,
        prompt_text: str,
    ) -> bool:
        if guild.id != self.settings.admin_guild_id:
            return False
        node = self.root()
        now = time.time()
        if (now - float(node.get("last_run_ts", 0.0) or 0.0)) < 4.0:
            return False
        plan = self._extract_plan_json(prompt_text)
        if plan is None:
            planned = await self._plan_from_ai(bot=bot, guild=guild, actor=actor, prompt_text=prompt_text)
            plan = self._extract_plan_json(planned or "")
        if plan is None:
            return False
        actions = plan.get("actions", [])
        if not isinstance(actions, list):
            return False
        reply = str(plan.get("reply", "")).strip()
        if reply:
            try:
                await channel.send(reply[:1800])
            except discord.HTTPException:
                pass
        max_actions = max(1, min(6, int(node.get("max_actions_per_cycle", 6) or 6)))
        results: list[tuple[str, bool, str]] = []
        for action in actions[:max_actions]:
            if not isinstance(action, dict):
                continue
            ok, detail = await self._execute_action(guild=guild, action=action)
            name = str(action.get("action", "unknown"))
            results.append((name, ok, detail))
        if results:
            self.logger.log(
                "autonomy.run",
                guild_id=guild.id,
                actor_id=actor.id,
                actions=len(results),
                ok=sum(1 for _name, ok, _d in results if ok),
            )
        for name, ok, detail in results:
            self._append_journal(
                {
                    "ts": datetime.now(tz=timezone.utc).isoformat(),
                    "action": name,
                    "ok": ok,
                    "detail": detail[:260],
                    "actor_id": int(actor.id),
                }
            )
        node["last_run_ts"] = now
        self.store.touch()
        return bool(reply or results)

    def _should_consider_message(self, bot: discord.Client, message: discord.Message) -> bool:
        text = message.clean_content.strip()
        if not text or text.startswith("!"):
            return False
        mention_hit = bool(bot.user and any(m.id == bot.user.id for m in message.mentions))
        alias_hit = bool(self._alias_regex.search(text))
        intent_hit = bool(self._intent_regex.search(text))
        ch_name = str(getattr(message.channel, "name", "")).lower()
        control = ch_name in CONTROL_CHANNELS
        return (mention_hit or alias_hit or control) and (intent_hit or "autonomy" in text.lower())

    async def _plan_from_ai(self, *, bot: discord.Client, guild: discord.Guild, actor: discord.abc.User, prompt_text: str) -> str | None:
        other = self._recent_observations(include_admin=False, limit=8)
        admin = self._recent_observations(include_admin=True, limit=10)
        system_prompt = (
            "You are Mandy Autonomy Planner. Return strict JSON only: "
            '{"reply":"string","actions":[{"action":"..."}]}. '
            "Never modify other guilds. Never target protected users."
        )
        user_prompt = (
            f"Admin guild id: {self.settings.admin_guild_id}\n"
            f"Protected users: [{self.settings.god_user_id}, {guild.owner_id}]\n"
            f"Actor: {actor} ({actor.id})\n"
            f"Request: {prompt_text[:900]}\n"
            f"Recent admin observations:\n{self._format_lines(admin)}\n"
            f"Recent other-server observations:\n{self._format_lines(other)}\n"
            "Allowed actions: send_message, create_category, create_text_channel, rename_channel, rename_role, "
            "set_server_name, create_invite, kick_member, ban_member, unban_member, add_role, remove_role, "
            "purge_channel, list_files, read_file, write_file.\n"
            "Respond with JSON only."
        )
        return await self.ai.complete_text(system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=650, temperature=0.25)

    async def _execute_action(self, *, guild: discord.Guild, action: dict[str, Any]) -> tuple[bool, str]:
        if guild.id != int(self.root().get("write_guild_id", self.settings.admin_guild_id)):
            return False, "blocked: write guild mismatch"
        name = str(action.get("action", "")).strip().lower()
        try:
            if name == "send_message":
                ch = self._resolve_text_channel(guild, action)
                if not ch:
                    return False, "channel not found"
                content = str(action.get("content", "")).strip()
                if not content:
                    return False, "empty content"
                await ch.send(content[:1900])
                return True, f"sent message in #{ch.name}"
            if name == "create_category":
                cat_name = self._norm_name(str(action.get("name", "")))
                if not cat_name:
                    return False, "empty category name"
                if not discord.utils.get(guild.categories, name=cat_name):
                    await guild.create_category(cat_name, reason="Mandy autonomy")
                return True, f"category ready: {cat_name}"
            if name == "create_text_channel":
                ch_name = self._norm_name(str(action.get("name", "")))
                if not ch_name:
                    return False, "empty channel name"
                if not discord.utils.get(guild.text_channels, name=ch_name):
                    cat = None
                    cat_name = self._norm_name(str(action.get("category", "")))
                    if cat_name:
                        cat = discord.utils.get(guild.categories, name=cat_name)
                    topic = str(action.get("topic", "")).strip()[:1024] or None
                    await guild.create_text_channel(ch_name, category=cat, topic=topic, reason="Mandy autonomy")
                return True, f"text channel ready: {ch_name}"
            if name == "rename_channel":
                ch = self._resolve_text_channel(guild, action)
                new_name = self._norm_name(str(action.get("new_name", "")))
                if not ch or not new_name:
                    return False, "channel/new_name missing"
                old = ch.name
                await ch.edit(name=new_name, reason="Mandy autonomy")
                return True, f"renamed #{old} -> #{new_name}"
            if name == "rename_role":
                role = self._resolve_role(guild, action)
                new_name = str(action.get("new_name", "")).strip()[:100]
                if not role or not new_name:
                    return False, "role/new_name missing"
                old = role.name
                await role.edit(name=new_name, reason="Mandy autonomy")
                return True, f"renamed role {old} -> {new_name}"
            if name == "set_server_name":
                new_name = str(action.get("name", "")).strip()[:100]
                if not new_name:
                    return False, "empty server name"
                old = guild.name
                await guild.edit(name=new_name, reason="Mandy autonomy")
                return True, f"server renamed {old} -> {new_name}"
            if name == "create_invite":
                ch = self._resolve_text_channel(guild, action)
                if not ch:
                    return False, "channel not found"
                invite = await ch.create_invite(
                    max_age=max(0, int(action.get("max_age", 0) or 0)),
                    max_uses=max(0, int(action.get("max_uses", 0) or 0)),
                    temporary=bool(action.get("temporary", False)),
                    unique=bool(action.get("unique", True)),
                    reason="Mandy autonomy",
                )
                return True, f"invite: {invite.url}"
            if name in {"kick_member", "ban_member", "add_role", "remove_role"}:
                member = await self._resolve_member(guild, action)
                if not member:
                    return False, "member not found"
                if self._protected(member, guild):
                    return False, "blocked: protected member"
                if name == "kick_member":
                    await member.kick(reason=str(action.get("reason", "Mandy autonomy"))[:300])
                    return True, f"kicked {member.id}"
                if name == "ban_member":
                    await guild.ban(member, reason=str(action.get("reason", "Mandy autonomy"))[:300], delete_message_days=0)
                    return True, f"banned {member.id}"
                role = self._resolve_role(guild, action)
                if not role:
                    return False, "role not found"
                if name == "add_role":
                    if role not in member.roles:
                        await member.add_roles(role, reason="Mandy autonomy")
                    return True, f"added role {role.name} to {member.id}"
                if role in member.roles:
                    await member.remove_roles(role, reason="Mandy autonomy")
                return True, f"removed role {role.name} from {member.id}"
            if name == "unban_member":
                uid = self._extract_user_id(action)
                if uid <= 0:
                    return False, "invalid user_id"
                await guild.unban(discord.Object(id=uid), reason=str(action.get("reason", "Mandy autonomy"))[:300])
                return True, f"unbanned {uid}"
            if name == "purge_channel":
                ch = self._resolve_text_channel(guild, action)
                if not ch:
                    return False, "channel not found"
                limit = max(1, min(400, int(action.get("limit", 120) or 120)))
                deleted = 0
                async for msg in ch.history(limit=limit, oldest_first=False):
                    if msg.pinned or msg.author.id in self._protected_ids(guild):
                        continue
                    try:
                        await msg.delete()
                        deleted += 1
                    except discord.HTTPException:
                        continue
                return True, f"purged {deleted} in #{ch.name}"
            if name == "list_files":
                return self._list_files(action)
            if name == "read_file":
                return self._read_file(action)
            if name == "write_file":
                return self._write_file(action)
            return False, f"unknown action `{name}`"
        except (discord.Forbidden, discord.HTTPException) as exc:
            return False, str(exc)[:180]
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)[:180]

    async def _resolve_member(self, guild: discord.Guild, action: dict[str, Any]) -> discord.Member | None:
        uid = self._extract_user_id(action)
        if uid <= 0:
            return None
        member = guild.get_member(uid)
        if member:
            return member
        try:
            return await guild.fetch_member(uid)
        except discord.HTTPException:
            return None

    def _resolve_text_channel(self, guild: discord.Guild, action: dict[str, Any]) -> discord.TextChannel | None:
        cid = int(action.get("channel_id", 0) or 0)
        if cid > 0:
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.TextChannel):
                return ch
        raw = str(action.get("channel", "")).strip()
        if raw.isdigit():
            ch = guild.get_channel(int(raw))
            if isinstance(ch, discord.TextChannel):
                return ch
        return discord.utils.get(guild.text_channels, name=self._norm_name(raw))

    def _resolve_role(self, guild: discord.Guild, action: dict[str, Any]) -> discord.Role | None:
        rid = int(action.get("role_id", 0) or 0)
        if rid > 0:
            role = guild.get_role(rid)
            if role:
                return role
        raw = str(action.get("role", "")).strip()
        if raw.isdigit():
            role = guild.get_role(int(raw))
            if role:
                return role
        return discord.utils.get(guild.roles, name=raw[:100])

    def _extract_user_id(self, action: dict[str, Any]) -> int:
        for key in ("user_id", "member_id", "target_user_id"):
            try:
                value = int(action.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return 0

    def _protected_ids(self, guild: discord.Guild) -> set[int]:
        out = {int(self.settings.god_user_id), int(guild.owner_id)}
        for raw in self.root().get("protected_user_ids", []):
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                out.add(parsed)
        return out

    def _protected(self, member: discord.Member, guild: discord.Guild) -> bool:
        if member.id in self._protected_ids(guild):
            return True
        protected_roles = {str(v) for v in self.root().get("protected_role_names", []) if str(v)}
        return bool(protected_roles and any(role.name in protected_roles for role in member.roles))

    def _list_files(self, action: dict[str, Any]) -> tuple[bool, str]:
        if not bool(self.root().get("allow_file_tools", True)):
            return False, "file tools disabled"
        path = self._workspace_path(str(action.get("path", ".")))
        if not path or not path.exists():
            return False, "path not found"
        if path.is_file():
            return True, f"file: {path.relative_to(self.workspace_root)}"
        max_entries = max(1, min(120, int(action.get("max_entries", 40) or 40)))
        rows = [f"{'D' if p.is_dir() else 'F'} {p.relative_to(self.workspace_root)}" for p in sorted(path.iterdir())[:max_entries]]
        return True, " | ".join(rows)[:900]

    def _read_file(self, action: dict[str, Any]) -> tuple[bool, str]:
        if not bool(self.root().get("allow_file_tools", True)):
            return False, "file tools disabled"
        path = self._workspace_path(str(action.get("path", "")))
        if not path or not path.exists() or not path.is_file():
            return False, "file not found"
        max_chars = max(120, min(8000, int(action.get("max_chars", 1400) or 1400)))
        text = path.read_text(encoding="utf-8", errors="replace")
        return True, f"{path.relative_to(self.workspace_root)} :: {text[:max_chars]}"

    def _write_file(self, action: dict[str, Any]) -> tuple[bool, str]:
        if not bool(self.root().get("allow_file_tools", True)):
            return False, "file tools disabled"
        path = self._workspace_path(str(action.get("path", "")))
        if not path:
            return False, "invalid path"
        content = str(action.get("content", ""))
        mode = str(action.get("mode", "overwrite")).strip().lower()
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(content)
        else:
            path.write_text(content, encoding="utf-8", newline="")
        return True, f"wrote {len(content)} chars to {path.relative_to(self.workspace_root)}"

    def _workspace_path(self, raw: str) -> Path | None:
        candidate = Path(raw.strip() or ".")
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if resolved != self.workspace_root and self.workspace_root not in resolved.parents:
            return None
        return resolved

    def _extract_plan_json(self, raw: str) -> dict[str, Any] | None:
        text = str(raw or "").strip()
        if not text:
            return None
        obj = self._try_json(text)
        if obj is not None:
            return obj
        block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if block:
            obj = self._try_json(block.group(1))
            if obj is not None:
                return obj
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return self._try_json(text[start : end + 1])
        return None

    def _try_json(self, raw: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _recent_observations(self, *, include_admin: bool, limit: int) -> list[str]:
        admin_id = self.settings.admin_guild_id
        rows = self.root().get("observations", [])
        if not isinstance(rows, list):
            return []
        out: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            guild_id = int(row.get("guild_id", 0) or 0)
            if include_admin and guild_id != admin_id:
                continue
            if not include_admin and guild_id == admin_id:
                continue
            out.append(
                f"[{str(row.get('guild_name', ''))[:24]}#{str(row.get('channel_name', ''))[:24]}] "
                f"{str(row.get('author_name', ''))[:24]}: {str(row.get('text', ''))[:140]}"
            )
        return out[-max(1, limit) :]

    def _append_journal(self, row: dict[str, Any]) -> None:
        journal = self.root()["journal"]
        journal.append(row)
        if len(journal) > 700:
            del journal[: len(journal) - 700]
        self.store.touch()

    def _norm_name(self, raw: str) -> str:
        cleaned = raw.strip().lower().replace(" ", "-")
        cleaned = re.sub(r"[^a-z0-9\-_]", "", cleaned)
        return cleaned[:90]

    def _format_lines(self, rows: list[str]) -> str:
        if not rows:
            return "- (none)"
        return "\n".join(f"- {row[:220]}" for row in rows[:20])
