from __future__ import annotations

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
                f"AI telemetry calls=`{row['calls']}` cache_hits=`{row['cache_hits']}` successes=`{row['successes']}` "
                f"failures=`{row['failures']}` fallbacks=`{row['fallbacks']}`\n"
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


async def setup_intelligence_controls(bot: Any) -> None:
    existing = bot.get_cog("IntelligenceControlsCog")
    if existing is None:
        await bot.add_cog(IntelligenceControlsCog(bot))
