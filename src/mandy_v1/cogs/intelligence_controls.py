from __future__ import annotations

import time
import json
from typing import Any

import discord
from discord.ext import commands


class IntelligenceControlsCog(commands.Cog):
    def __init__(self, bot: Any) -> None:
        self.bot = bot

    def _tier_check(self, user: discord.abc.User, tier: int) -> bool:
        return bool(getattr(self.bot, "soc").can_run(user, tier))

    @commands.command(name="privacy")
    async def privacy(self, ctx: commands.Context, action: str = "status", user_id: int | None = None, *, reason: str = "") -> None:
        target_id = int(user_id or ctx.author.id)
        action_key = action.strip().casefold()
        is_self = target_id == int(ctx.author.id)
        if not is_self and not self._tier_check(ctx.author, 90):
            await ctx.send("Not authorized.")
            return
        if action_key in {"status", "show"}:
            paused = self.bot.ai.is_learning_paused(target_id)
            await ctx.send(f"Learning paused for `{target_id}`: `{paused}`")
            return
        if action_key in {"pause", "off"}:
            changed = self.bot.ai.set_learning_paused(target_id, True, actor_id=ctx.author.id, reason=reason)
            await ctx.send(f"Learning paused for `{target_id}` changed=`{changed}`.")
            return
        if action_key in {"resume", "on"}:
            changed = self.bot.ai.set_learning_paused(target_id, False, actor_id=ctx.author.id, reason=reason)
            await ctx.send(f"Learning resumed for `{target_id}` changed=`{changed}`.")
            return
        if action_key == "forget":
            removed = self.bot.ai.forget_user_everywhere(target_id, actor_id=ctx.author.id, reason=reason)
            await ctx.send(f"Forgot `{target_id}` memory: `{removed}`")
            return
        if action_key == "export":
            payload = json.dumps(self.bot.ai.export_user_memory(target_id), indent=2)[:1800]
            await ctx.send(f"```json\n{payload}\n```")
            return
        await ctx.send("Usage: `!privacy status|pause|resume|export|forget [user_id] [reason]`")

    @commands.command(name="privacyaudit")
    async def privacy_audit(self, ctx: commands.Context) -> None:
        if not self._tier_check(ctx.author, 90):
            await ctx.send("Not authorized.")
            return
        lines = self.bot.ai.privacy_audit_lines(limit=15)
        await ctx.send("\n".join(["Privacy audit:", *(lines or ["(empty)"])])[:1900])

    @commands.command(name="telemetry")
    async def telemetry(self, ctx: commands.Context) -> None:
        if not self._tier_check(ctx.author, 70):
            await ctx.send("Not authorized.")
            return
        row = self.bot.ai.telemetry_snapshot()
        models = row.get("models", {})
        model_line = ", ".join(f"{name}={count}" for name, count in list(models.items())[:6]) if isinstance(models, dict) else ""
        await ctx.send(
            (
                f"AI telemetry calls=`{row['calls']}` cache_hits=`{row['cache_hits']}` "
                f"inflight_joins=`{row.get('inflight_joins', 0)}` budget_throttles=`{row.get('budget_throttles', 0)}`\n"
                f"successes=`{row['successes']}` failures=`{row['failures']}` fallbacks=`{row['fallbacks']}` "
                f"persistent_cache_rows=`{row.get('persistent_cache_rows', 0)}`\n"
                f"tokens~`{row['estimated_tokens']}` cost~`${row['estimated_cost_usd']}` "
                f"cooldown=`{row['cooldown_remaining_sec']}s` failure_streak=`{row['failure_streak']}`\n"
                f"models: {model_line or '(none)'}"
            )[:1900]
        )

    @commands.command(name="compactreflections")
    async def compact_reflections(self, ctx: commands.Context, guild_id: int | None = None) -> None:
        target_id = guild_id or (ctx.guild.id if isinstance(ctx.guild, discord.Guild) else None)
        if target_id is not None and not self.bot._can_control_satellite(ctx.author, int(target_id), min_tier=70):  # noqa: SLF001
            await ctx.send("Not authorized.")
            return
        if target_id is None and not self._tier_check(ctx.author, 90):
            await ctx.send("Not authorized.")
            return
        result = self.bot.ai.compact_reflections(guild_id=target_id)
        await ctx.send(f"Reflection compaction: `{result}`")

    @commands.command(name="wakebroadcast")
    async def wake_broadcast(self, ctx: commands.Context, action: str = "preview", limit: int = 25, *, message: str = "") -> None:
        if not self._tier_check(ctx.author, 90):
            await ctx.send("Not authorized.")
            return
        root = self._wake_root()
        contacts = self._wake_contact_ids()
        safe_limit = max(1, min(100, int(limit or 25)))
        selected = contacts[:safe_limit]
        action_key = action.strip().casefold()
        if action_key in {"preview", "show", "status"}:
            await ctx.send(
                (
                    f"Wake broadcast contacts=`{len(contacts)}` selected=`{len(selected)}` "
                    f"last_sent_ts=`{root.get('last_sent_ts', 0.0)}`\n"
                    f"Default message: {root.get('default_message', '')}\n"
                    f"First targets: {', '.join(str(uid) for uid in selected[:20]) or '(none)'}\n"
                    "Run `!wakebroadcast send <limit> <message>` to send intentionally."
                )[:1900]
            )
            return
        if action_key != "send":
            await ctx.send("Usage: `!wakebroadcast preview [limit]` or `!wakebroadcast send <limit> [message]`")
            return
        if not selected:
            await ctx.send("No known DM contacts to message.")
            return
        now = time.time()
        last_sent = float(root.get("last_sent_ts", 0.0) or 0.0)
        if (now - last_sent) < 6 * 60 * 60:
            await ctx.send("Wake broadcast is on cooldown. Try again later or send manually through DM bridges.")
            return
        body = (message.strip() or str(root.get("default_message", ""))).strip()
        if not body:
            await ctx.send("Wake broadcast message is empty.")
            return
        sent = 0
        failed = 0
        for user_id in selected:
            user = await self.bot.dm_bridges.resolve_user(self.bot, user_id)
            if user is None:
                failed += 1
                continue
            try:
                await user.send(body[:1900])
                sent += 1
                self.bot.ai.capture_dm_outbound(user_id=user_id, user_name=str(user), text=body, touch=False)
            except (discord.Forbidden, discord.HTTPException):
                failed += 1
        root["last_sent_ts"] = now
        log = root.setdefault("sent_log", [])
        if isinstance(log, list):
            log.append(
                {
                    "ts": now,
                    "actor_id": int(ctx.author.id),
                    "selected": len(selected),
                    "sent": sent,
                    "failed": failed,
                    "message_preview": body[:120],
                }
            )
            if len(log) > 100:
                del log[: len(log) - 100]
        self.bot.store.touch()
        await ctx.send(f"Wake broadcast complete: sent=`{sent}` failed=`{failed}` selected=`{len(selected)}`.")

    def _wake_root(self) -> dict[str, Any]:
        root = self.bot.store.data.setdefault("wake_broadcast", {})
        if not isinstance(root, dict):
            self.bot.store.data["wake_broadcast"] = {}
            root = self.bot.store.data["wake_broadcast"]
        root.setdefault("sent_log", [])
        root.setdefault("last_sent_ts", 0.0)
        root.setdefault("default_message", "Hi i just woke up sorry i been gone lets go")
        return root

    def _wake_contact_ids(self) -> list[int]:
        ids: set[int] = set()
        for user_id in self.bot.dm_bridges.list_user_ids():
            if int(user_id) > 0:
                ids.add(int(user_id))
        events = self.bot.store.data.setdefault("ai", {}).setdefault("dm_brain", {}).setdefault("events", [])
        if isinstance(events, list):
            for row in events:
                if not isinstance(row, dict):
                    continue
                try:
                    uid = int(row.get("user_id", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if uid > 0:
                    ids.add(uid)
        return sorted(ids)


async def setup_intelligence_controls(bot: Any) -> None:
    existing = bot.get_cog("IntelligenceControlsCog")
    if existing is None:
        await bot.add_cog(IntelligenceControlsCog(bot))
