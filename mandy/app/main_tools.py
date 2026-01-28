from __future__ import annotations

from typing import Any, Dict, List, Optional

import discord
from discord.ext import commands

from mandy.capability_registry import CapabilityRegistry
from mandy.tool_plugin_manager import ToolPluginManager

from . import state
from .config import GEMINI_API_KEY, AGENT_ROUTER_TOKEN, AGENT_ROUTER_BASE_URL
from .core import request_elevation
from .logging import audit, debug, log_to
from .store import STORE, ai_cfg, cfg


class ToolRegistry:
    def __init__(self, bot_ref: commands.Bot):
        self.bot = bot_ref
        self.dynamic_tools: Dict[str, Dict[str, Any]] = {}

    def register_dynamic_tool(self, name: str, meta: Dict[str, Any]) -> None:
        self.dynamic_tools[name] = meta

    def unregister_dynamic_tool(self, name: str) -> None:
        self.dynamic_tools.pop(name, None)

    def get_dynamic_tool(self, name: str) -> Optional[Dict[str, Any]]:
        return self.dynamic_tools.get(name)

    def list_dynamic_tools(self) -> List[str]:
        return sorted(self.dynamic_tools.keys())

    def _as_int(self, value: Any, name: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be int")
        try:
            return int(value)
        except Exception as exc:
            raise ValueError(f"{name} must be int") from exc

    def _as_text(self, value: Any, name: str, max_len: int) -> str:
        if value is None:
            raise ValueError(f"{name} must be str")
        text = str(value)
        if len(text) > max_len:
            text = text[:max_len]
        return text

    async def send_message(self, channel_id: int, text: str):
        ch_id = self._as_int(channel_id, "channel_id")
        content = self._as_text(text, "text", 1900).strip()
        if not content:
            raise ValueError("text cannot be empty")
        ch = self.bot.get_channel(ch_id)
        if not ch:
            try:
                ch = await self.bot.fetch_channel(ch_id)
            except Exception as exc:
                raise ValueError("channel not found") from exc
        if isinstance(ch, discord.TextChannel):
            perms = ch.permissions_for(ch.guild.me)
            if not perms.send_messages:
                raise ValueError("missing send_messages permission")
        try:
            msg = await ch.send(content)
            return {"message_id": msg.id}
        except Exception as exc:
            if "Forbidden" in str(exc) or "permission" in str(exc).lower():
                await request_elevation("send_message", f"missing permission for channel {ch_id}", {"channel_id": ch_id})
            raise ValueError(f"send failed: {exc}") from exc

    async def reply_to_message(self, channel_id: int, message_id: int, text: str):
        ch_id = self._as_int(channel_id, "channel_id")
        msg_id = self._as_int(message_id, "message_id")
        content = self._as_text(text, "text", 1900).strip()
        if not content:
            raise ValueError("text cannot be empty")
        ch = self.bot.get_channel(ch_id)
        if not ch:
            try:
                ch = await self.bot.fetch_channel(ch_id)
            except Exception as exc:
                raise ValueError("channel not found") from exc
        try:
            msg = await ch.fetch_message(msg_id)
        except Exception as exc:
            raise ValueError("message not found") from exc
        try:
            sent = await msg.reply(content)
            return {"message_id": sent.id}
        except Exception as exc:
            raise ValueError(f"reply failed: {exc}") from exc

    async def send_dm(self, user_id: int, text: str):
        uid = self._as_int(user_id, "user_id")
        content = self._as_text(text, "text", 1900).strip()
        if not content:
            raise ValueError("text cannot be empty")
        user = self.bot.get_user(uid)
        if not user:
            try:
                user = await self.bot.fetch_user(uid)
            except Exception as exc:
                raise ValueError("user not found") from exc
        try:
            msg = await user.send(content)
            return {"message_id": msg.id}
        except discord.Forbidden as exc:
            raise ValueError("user blocked DMs") from exc
        except Exception as exc:
            raise ValueError(f"dm failed: {exc}") from exc

    async def broadcast_message(self, channel: str, text: str, actor_id: int = 0):
        channel_token = self._as_text(channel, "channel", 100).strip()
        content = self._as_text(text, "text", 1900).strip()
        if not channel_token:
            raise ValueError("channel required")
        if not content:
            raise ValueError("text cannot be empty")

        channel_id = parse_channel_id(channel_token)
        sent = 0
        failed = 0
        targets = 0

        if channel_id:
            ch = self.bot.get_channel(channel_id)
            if not ch:
                try:
                    ch = await self.bot.fetch_channel(channel_id)
                except Exception as exc:
                    raise ValueError("channel not found") from exc
            if not isinstance(ch, discord.TextChannel):
                raise ValueError("channel must be a text channel")
            targets = 1
            perms = ch.permissions_for(ch.guild.me)
            if not perms.send_messages:
                raise ValueError("missing send_messages permission")
            try:
                await ch.send(content)
                sent = 1
            except Exception as exc:
                raise ValueError(f"send failed: {exc}") from exc
            if actor_id:
                await audit(actor_id, "Broadcast message", {"channel_id": channel_id, "sent": sent})
            return {"sent": sent, "targets": targets, "failed": failed}

        name = channel_token.lstrip("#").strip().lower()
        if not name:
            raise ValueError("channel required")
        for guild in self.bot.guilds:
            ch = None
            for cand in guild.text_channels:
                if cand.name.lower() == name:
                    ch = cand
                    break
            if not ch:
                continue
            targets += 1
            perms = ch.permissions_for(ch.guild.me)
            if not perms.send_messages:
                failed += 1
                continue
            try:
                await ch.send(content)
                sent += 1
            except Exception:
                failed += 1
        if actor_id:
            await audit(
                actor_id,
                "Broadcast message",
                {"channel": name, "sent": sent, "targets": targets, "failed": failed},
            )
        return {"sent": sent, "targets": targets, "failed": failed}

    async def broadcast_dm(self, text: str, guild: str = "", limit: int = 0, actor_id: int = 0):
        content = self._as_text(text, "text", 1900).strip()
        if not content:
            raise ValueError("text cannot be empty")
        guild_token = self._as_text(guild or "", "guild", 100).strip()
        max_users = self._as_int(limit or 0, "limit") if limit else 0

        def _norm(s: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", (s or "").strip().lower())

        guilds = list(self.bot.guilds)
        if guild_token:
            gid = None
            if guild_token.isdigit():
                gid = int(guild_token)
            if gid:
                guilds = [g for g in guilds if g.id == gid]
            else:
                token_norm = _norm(guild_token)
                exact = [g for g in guilds if _norm(g.name) == token_norm]
                if exact:
                    guilds = exact
                else:
                    contains = [g for g in guilds if token_norm and token_norm in _norm(g.name)]
                    if len(contains) == 1:
                        guilds = contains
                    else:
                        raise ValueError("guild not found (use exact name or guild id)")
        if not guilds:
            raise ValueError("guild not found")

        sent = 0
        failed = 0
        targets = 0
        seen: Set[int] = set()
        for g in guilds:
            for member in g.members:
                if member.bot:
                    continue
                if member.id in seen:
                    continue
                seen.add(member.id)
                targets += 1
                try:
                    await member.send(content)
                    sent += 1
                except Exception:
                    failed += 1
                if max_users and targets >= max_users:
                    break
            if max_users and targets >= max_users:
                break
        if actor_id:
            await audit(
                actor_id,
                "Broadcast DM",
                {
                    "guild": guild_token or "all",
                    "sent": sent,
                    "targets": targets,
                    "failed": failed,
                    "limit": max_users,
                },
            )
        return {"sent": sent, "targets": targets, "failed": failed}

    async def close_dm_bridge(self, user_id: int, actor_id: int = 0):
        uid = self._as_int(user_id, "user_id")
        info = await dm_bridge_get(uid)
        if not info:
            alt_uid = await dm_bridge_user_for_channel(uid)
            if alt_uid:
                uid = int(alt_uid)
                info = await dm_bridge_get(uid)
        if not info:
            raise ValueError("dm bridge not found")
        await dm_bridge_close(uid)
        if actor_id:
            await audit(actor_id, "DM bridge close", {"user_id": uid})
        return {"user_id": uid, "status": "closed"}

    async def set_bot_status(self, state: str, text: str):
        st = self._as_text(state, "state", 16)
        msg = self._as_text(text, "text", 120)
        await set_bot_status(st, msg)
        return {"status": st, "text": msg}

    async def get_recent_transcript(self, channel_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        ch_id = self._as_int(channel_id, "channel_id")
        lim = max(1, min(80, self._as_int(limit, "limit")))
        ch = self.bot.get_channel(ch_id)
        if not ch:
            try:
                ch = await self.bot.fetch_channel(ch_id)
            except Exception as exc:
                raise ValueError("channel not found") from exc
        if isinstance(ch, discord.TextChannel):
            perms = ch.permissions_for(ch.guild.me)
            if not perms.view_channel or not perms.read_message_history:
                raise ValueError("missing read_message_history permission")
        messages: List[Dict[str, Any]] = []
        async for m in ch.history(limit=lim, oldest_first=False):
            if not m:
                continue
            content = (m.content or "").strip()
            messages.append({
                "id": m.id,
                "author_id": m.author.id,
                "author": str(m.author),
                "content": content,
                "created_at": m.created_at.isoformat() if m.created_at else ""
            })
        messages.reverse()
        return messages

    async def add_watcher(self, target_user_id: int, count: int, text: str, actor_id: int = 0):
        uid = self._as_int(target_user_id, "target_user_id")
        threshold = self._as_int(count, "count")
        msg = self._as_text(text or "", "text", 500)
        if threshold < 1:
            raise ValueError("count must be >= 1")
        cfg().setdefault("targets", {})[str(uid)] = {"count": threshold, "current": 0, "text": msg}
        await STORE.mark_dirty()
        if actor_id:
            await audit(actor_id, "Watcher set (json)", {"user_id": uid, "count": threshold})
        return "JSON watcher saved."

    async def remove_watcher(self, target_user_id: int, actor_id: int = 0):
        uid = self._as_int(target_user_id, "target_user_id")
        return await remove_watcher("json", uid, actor_id or 0)

    async def list_watchers(self) -> str:
        def fmt(uid, count, current, text):
            return f"{uid} (<@{uid}>) | count={count} current={current} text={truncate(text, 120)}"

        lines: List[str] = []
        targets = cfg().get("targets", {})
        for uid, data in targets.items():
            lines.append(fmt(uid, data.get("count", 0), data.get("current", 0), data.get("text", "")))

        if not lines:
            return "No JSON watchers found."

        header = f"JSON watchers ({len(lines)}):"
        return header + "\n" + "\n".join(lines[:50])
    async def list_mirror_rules(self) -> str:
        rules = list(mirror_rules_dict().values())
        if not rules:
            return "No mirror rules."
        lines: List[str] = []
        for r in rules[:50]:
            lines.append(f"{rule_summary(r)} ({'on' if r.get('enabled', True) else 'off'})")
        header = f"Mirror rules ({len(rules)}):"
        return header + "\n" + "\n".join(lines)

    async def create_mirror(self, source_channel_id: int, target_channel_id: int, actor_id: int = 0):
        src_id = self._as_int(source_channel_id, "source_channel_id")
        dst_id = self._as_int(target_channel_id, "target_channel_id")
        try:
            src_ch = self.bot.get_channel(src_id) or await self.bot.fetch_channel(src_id)
        except Exception as exc:
            raise ValueError("source channel not found") from exc
        if not isinstance(src_ch, discord.TextChannel):
            raise ValueError("source channel must be a text channel")
        try:
            dst_ch = self.bot.get_channel(dst_id) or await self.bot.fetch_channel(dst_id)
        except Exception as exc:
            raise ValueError("target channel not found") from exc
        if not isinstance(dst_ch, discord.TextChannel):
            raise ValueError("target channel must be a text channel")

        rule_id = make_rule_id("channel", src_id, dst_id)
        rule = {
            "rule_id": rule_id,
            "scope": "channel",
            "source_guild": src_ch.guild.id,
            "source_id": src_id,
            "target_channel": dst_id,
            "enabled": True,
            "fail_count": 0
        }
        await mirror_rule_save(rule)
        if actor_id:
            await audit(actor_id, "Mirror rule add (tool)", rule)
        return "Mirror rule added."

    async def disable_mirror_rule(self, rule_id: str, actor_id: int = 0):
        rid = self._as_text(rule_id, "rule_id", 96).strip()
        if not rid:
            raise ValueError("rule_id required")
        rule = mirror_rules_dict().get(rid)
        if not rule:
            raise ValueError("rule not found")
        await mirror_rule_disable(rule, "disabled via mandy")
        if actor_id:
            await audit(actor_id, "Mirror rule disabled", {"rule_id": rid})
        return "Mirror rule disabled."

    async def show_stats(self, scope: str, user_id: Optional[int] = None, guild_id: Optional[int] = None) -> str:
        window = normalize_stats_window(scope, "daily")
        if window not in ("daily", "weekly", "monthly", "yearly", "rolling24"):
            window = "daily"
        now_dt = datetime.datetime.now(datetime.timezone.utc)

        if user_id:
            uid = self._as_int(user_id, "user_id")
            if guild_id:
                gid = self._as_int(guild_id, "guild_id")
                guild = self.bot.get_guild(gid)
                if not guild:
                    raise ValueError("guild not found")
                entry, changed = chat_stats_get_user_entry(guild, window, uid, now_dt)
                if changed:
                    await STORE.mark_dirty()
                return (
                    f"User stats ({window}) for {uid} in {guild.name}: "
                    f"messages={int(entry.get('messages', 0))} words={int(entry.get('words', 0))} "
                    f"sentences={int(entry.get('sentences', 0))} top_words={format_top_words(entry)}"
                )

            total = default_user_stats(int(now_dt.timestamp()))
            for g in self.bot.guilds:
                entry, changed = chat_stats_get_user_entry(g, window, uid, now_dt)
                total["messages"] += int(entry.get("messages", 0))
                total["words"] += int(entry.get("words", 0))
                total["sentences"] += int(entry.get("sentences", 0))
                for w, c in (entry.get("word_freq", {}) or {}).items():
                    total["word_freq"][w] = int(total["word_freq"].get(w, 0)) + int(c)
                if changed:
                    await STORE.mark_dirty()
            return (
                f"User stats ({window}) for {uid}: "
                f"messages={total['messages']} words={total['words']} sentences={total['sentences']} "
                f"top_words={format_top_words(total)}"
            )

        totals, users, changed = aggregate_global_stats(window)
        if changed:
            await STORE.mark_dirty()
        top_users = sorted(
            ((int(uid), int(entry.get("messages", 0))) for uid, entry in users.items()),
            key=lambda row: row[1],
            reverse=True
        )[:5]
        top_lines = [f"{global_user_label(uid)} ({count})" for uid, count in top_users if count > 0]
        top_text = ", ".join(top_lines) if top_lines else "None"
        return (
            f"Global stats ({window}): messages={totals.get('messages', 0)} "
            f"words={totals.get('words', 0)} sentences={totals.get('sentences', 0)} "
            f"active_users={totals.get('active_users', 0)} top_users={top_text}"
        )

    async def list_capabilities(self) -> str:
        registry = getattr(self.bot, "mandy_registry", None) or CapabilityRegistry(self)
        ai = ai_cfg()
        installed = list(ai.get("installed_extensions", []) or [])
        loaded = sorted(self.bot.extensions.keys())
        for mod in loaded:
            if mod not in installed:
                installed.append(mod)
        queue = ai.get("queue", {}) or {}
        queue_counts = {"pending": 0, "waiting": 0, "running": 0}
        for job in queue.values():
            status = str(job.get("status", "pending"))
            if status in queue_counts:
                queue_counts[status] += 1
        runtime = getattr(self.bot, "mandy_runtime", {}) or {}
        counters = runtime.get("counters", {}) or {}

        lines: List[str] = []
        lines.append("Tools:")
        lines.append(registry.format_tools_summary(include_args=False))
        dynamic = registry._tool_registry.list_dynamic_tools() if registry else []
        lines.append(f"Plugin tools: {', '.join(dynamic) if dynamic else 'none'}")
        lines.append(f"Extensions: {', '.join(installed) if installed else 'none'}")
        lines.append(
            "Models: "
            f"default={ai.get('default_model')} "
            f"router={ai.get('router_model')} "
            f"tts={ai.get('tts_model') or 'none'}"
        )
        lines.append(
            "Queue: "
            f"total={len(queue)} pending={queue_counts['pending']} "
            f"waiting={queue_counts['waiting']} running={queue_counts['running']}"
        )
        if counters:
            counter_text = " ".join(f"{k}={v}" for k, v in sorted(counters.items()))
            lines.append(f"Counters: {counter_text}")
        return "\n".join(lines)

def attach_mandy_context(
    bot: commands.Bot,
    *,
    dm_ai_is_enabled,
    dm_bridge_user_for_channel,
    mandy_power_mode_enabled,
    effective_level,
    require_level_ctx,
):
    bot.mandy_tools = ToolRegistry(bot)
    bot.mandy_registry = CapabilityRegistry(bot.mandy_tools)
    bot.mandy_runtime = {"counters": {}, "last_actions": [], "last_rate_limit": None}
    bot.mandy_plugin_manager = ToolPluginManager(bot, bot.mandy_tools, log_to)
    bot.mandy_cfg = cfg
    bot.mandy_get_ai_config = ai_cfg
    bot.mandy_api_key = GEMINI_API_KEY
    bot.mandy_agent_router_token = AGENT_ROUTER_TOKEN
    bot.mandy_agent_router_base_url = AGENT_ROUTER_BASE_URL
    bot.mandy_store = STORE
    bot.mandy_audit = audit
    bot.mandy_log_to = log_to
    bot.mandy_dm_ai_is_enabled = dm_ai_is_enabled
    bot.mandy_dm_bridge_user_for_channel = dm_bridge_user_for_channel
    bot.mandy_power_mode_enabled = mandy_power_mode_enabled
    bot.mandy_effective_level = effective_level
    bot.mandy_require_level_ctx = require_level_ctx

async def maybe_load_mandy_extension(bot: commands.Bot):
    if state.MANDY_LOADED:
        return
    try:
        await bot.load_extension(state.MANDY_EXTENSION)
        state.MANDY_LOADED = True
    except Exception as e:
        await debug(f"Mandy AI extension failed to load: {e}")
