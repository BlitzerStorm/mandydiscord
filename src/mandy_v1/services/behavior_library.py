"""
Behavior Library for Mandy's Autonomy Engine.

Extracts individual behavior implementations from the proactive service
and provides them as Action factories that the autonomy engine can consume.

Each behavior function returns a list of Action objects that can be executed.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable

import discord


LOGGER = logging.getLogger("mandy.behaviors")


class BehaviorContext:
    """Shared context passed to behavior generators."""

    def __init__(
        self,
        bot: discord.Client,
        storage: Any,
        ai: Any,
        emotion: Any,
        episodic: Any,
        personas: Any,
        culture: Any,
        expansion: Any,
    ) -> None:
        self.bot = bot
        self.storage = storage
        self.ai = ai
        self.emotion = emotion
        self.episodic = episodic
        self.personas = personas
        self.culture = culture
        self.expansion = expansion
        self._rng = random.Random()

    def _pick_general_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        """Select `general` channel or first writable channel."""
        me = guild.me
        if me is None:
            return None
        general = discord.utils.get(guild.text_channels, name="general")
        if isinstance(general, discord.TextChannel):
            perms = general.permissions_for(me)
            if perms.view_channel and perms.send_messages:
                return general
        for channel in guild.text_channels:
            perms = channel.permissions_for(me)
            if perms.view_channel and perms.send_messages:
                return channel
        return None

    async def _safe_send(self, channel: discord.TextChannel, text: str) -> bool:
        """Send with exception swallowing. Returns True if sent successfully."""
        try:
            await channel.send(text[:1900])
            return True
        except Exception:  # noqa: BLE001
            LOGGER.exception("Safe send failed")
            return False

    async def _generate_text(self, system_prompt: str, user_prompt: str, fallback: str) -> str:
        """Generate text with safe fallback."""
        try:
            if self.ai is None or not hasattr(self.ai, "complete_text"):
                return fallback
            raw = await self.ai.complete_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=120,
                temperature=0.7,
            )
            text = str(raw or "").strip()
            return text or fallback
        except Exception:  # noqa: BLE001
            LOGGER.exception("Text generation failed")
            return fallback


async def create_behavior_actions(
    bot: discord.Client,
    storage: Any,
    ai: Any,
    emotion: Any,
    episodic: Any,
    personas: Any,
    culture: Any,
    expansion: Any,
) -> list[Any]:  # Returns list of Action objects
    """
    Create all available behavior actions for the current moment.

    Returns a list of Action objects that can be scored and executed by the autonomy engine.
    """
    from mandy_v1.services.autonomy_engine import Action

    ctx = BehaviorContext(bot, storage, ai, emotion, episodic, personas, culture, expansion)
    actions = []

    # Generate absent user callout actions (one per guild)
    for guild in bot.guilds:
        action = await _create_absent_user_callout_action(ctx, guild)
        if action:
            actions.append(action)

    # Generate episodic callback actions (one per guild)
    for guild in bot.guilds:
        action = await _create_episodic_callback_action(ctx, guild)
        if action:
            actions.append(action)

    # Generate curiosity burst actions (one per guild)
    for guild in bot.guilds:
        action = await _create_curiosity_burst_action(ctx, guild)
        if action:
            actions.append(action)

    # Generate lore callback actions (one per guild)
    for guild in bot.guilds:
        action = await _create_lore_callback_action(ctx, guild)
        if action:
            actions.append(action)

    # Generate self nickname update actions (one per guild)
    for guild in bot.guilds:
        action = await _create_self_nickname_action(ctx, guild)
        if action:
            actions.append(action)

    # Generate confidant maintenance actions (one per deep relationship)
    actions_confidant = await _create_confidant_actions(ctx)
    actions.extend(actions_confidant)

    # Generate expansion queue processing action (global)
    action_expansion = await _create_expansion_action(ctx)
    if action_expansion:
        actions.append(action_expansion)

    return actions


async def _create_absent_user_callout_action(ctx: BehaviorContext, guild: discord.Guild) -> Any | None:
    """Create action to mention users who've been silent 6-48 hours."""
    from mandy_v1.services.autonomy_engine import Action

    now = time.time()
    candidate_id = None

    # Find a user who's been silent 6-48 hours with depth >= 2
    for uid, profile in ctx.personas._root().items():  # noqa: SLF001
        if not isinstance(profile, dict):
            continue
        if int(profile.get("relationship_depth", 0) or 0) < 2:
            continue
        last = float(profile.get("last_updated", 0.0) or 0.0)
        silence = now - last
        if 6 * 60 * 60 <= silence <= 48 * 60 * 60:
            candidate_id = int(uid)
            break

    if candidate_id is None:
        return None

    channel = ctx._pick_general_channel(guild)
    if channel is None:
        return None

    async def execute():
        text = await ctx._generate_text(
            "Write one short casual line mentioning that a regular has been quiet, warm but not clingy.",
            f"Target user id: {candidate_id}",
            fallback=f"haven't seen <@{candidate_id}> around much today. hope you're good.",
        )
        success = await ctx._safe_send(channel, text[:1800])
        return {"success": success, "engagement": 0.0, "responses": []}

    return Action(
        type="absent_user_callout",
        guild_id=guild.id,
        user_id=candidate_id,
        description=f"Mention {candidate_id} who's been quiet",
        execute_fn=execute,
        priority=1.0,
    )


async def _create_episodic_callback_action(ctx: BehaviorContext, guild: discord.Guild) -> Any | None:
    """Create action to surface an old memory in chat."""
    from mandy_v1.services.autonomy_engine import Action

    rows = ctx.storage.data.get("episodic", {}).get("episodes", {}).get(str(guild.id), [])
    if not isinstance(rows, list) or not rows:
        return None

    # Pick a random older memory
    choice = rows[max(0, len(rows) - 1 - ctx._rng.randint(0, min(15, len(rows) - 1)))]
    if not isinstance(choice, dict):
        return None

    age_sec = time.time() - float(choice.get("ts", 0) or 0)
    if age_sec < 6 * 60 * 60:
        return None  # Too recent

    channel = ctx._pick_general_channel(guild)
    if channel is None:
        return None

    async def execute():
        fallback = f"i was just thinking about when {choice.get('author_name','someone')} said: {str(choice.get('content',''))[:100]}"
        text = await ctx._generate_text(
            "Write one short memory callback line for a server.",
            str(choice.get("content", "")),
            fallback=fallback,
        )
        success = await ctx._safe_send(channel, text[:1800])
        return {"success": success, "engagement": 0.0, "responses": []}

    return Action(
        type="episodic_callback",
        guild_id=guild.id,
        description="Surface an old server memory",
        execute_fn=execute,
        priority=1.0,
    )


async def _create_curiosity_burst_action(ctx: BehaviorContext, guild: discord.Guild) -> Any | None:
    """Create action to post a spontaneous curiosity question."""
    from mandy_v1.services.autonomy_engine import Action

    interests = ctx.storage.data.get("identity", {}).get("interests", [])
    topic = str(ctx._rng.choice(interests) if isinstance(interests, list) and interests else "community dynamics")

    channel = ctx._pick_general_channel(guild)
    if channel is None:
        return None

    async def execute():
        text = await ctx._generate_text(
            "Write one short curiosity question to a Discord server.",
            f"Interest topic: {topic}",
            fallback=f"random thought: what is everyone's take on {topic} lately?",
        )
        success = await ctx._safe_send(channel, text[:1800])
        return {"success": success, "engagement": 0.0, "responses": []}

    return Action(
        type="curiosity_burst",
        guild_id=guild.id,
        description=f"Ask a curiosity question about {topic}",
        execute_fn=execute,
        priority=1.0,
    )


async def _create_lore_callback_action(ctx: BehaviorContext, guild: discord.Guild) -> Any | None:
    """Create action to reference server lore."""
    from mandy_v1.services.autonomy_engine import Action

    lore = ctx.storage.data.get("culture", {}).get(str(guild.id), {}).get("lore_refs", [])
    if not isinstance(lore, list) or not lore:
        return None

    ref = str(ctx._rng.choice(lore))
    channel = ctx._pick_general_channel(guild)
    if channel is None:
        return None

    async def execute():
        text = await ctx._generate_text(
            "Write one short line that references in-server lore naturally.",
            f"Lore ref: {ref}",
            fallback=f"still not over '{ref}' by the way.",
        )
        success = await ctx._safe_send(channel, text[:1800])
        return {"success": success, "engagement": 0.0, "responses": []}

    return Action(
        type="lore_callback",
        guild_id=guild.id,
        description=f"Reference lore: {ref[:50]}",
        execute_fn=execute,
        priority=1.0,
    )


async def _create_self_nickname_action(ctx: BehaviorContext, guild: discord.Guild) -> Any | None:
    """Create action to update Mandy's own nickname."""
    from mandy_v1.services.autonomy_engine import Action

    me = guild.me
    if me is None:
        return None

    # Random 10% chance
    if ctx._rng.random() > 0.10:
        return None

    mood = ctx.emotion.get_state()
    persona = ctx.culture.get_mandy_persona(guild.id)

    async def execute():
        nick = await ctx._generate_text(
            "Return a nickname only, max 32 chars, based on mood and server vibe.",
            f"Mood: {mood}\nPersona: {persona}",
            fallback=f"Mandy {mood[:8]}",
        )
        nick = nick.strip().replace("\n", " ")[:32]
        if not nick:
            return {"success": False}

        try:
            await me.edit(nick=nick, reason="Mandy autonomy nickname update")
            return {"success": True}
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to update nickname")
            return {"success": False}

    return Action(
        type="self_nickname_update",
        guild_id=guild.id,
        description="Update own nickname",
        execute_fn=execute,
        priority=0.5,  # Lower priority - cosmetic change
    )


async def _create_confidant_actions(ctx: BehaviorContext) -> list[Any]:
    """Create actions to DM users with deep relationships (depth >= 4)."""
    from mandy_v1.services.autonomy_engine import Action

    actions = []
    now = time.time()
    sent_count = 0

    for uid, profile in ctx.personas._root().items():  # noqa: SLF001
        if sent_count >= 2:
            break

        if not isinstance(profile, dict):
            continue

        if int(profile.get("relationship_depth", 0) or 0) < 4:
            continue

        user_id = int(uid)
        user = ctx.bot.get_user(user_id)

        if user is None:
            try:
                user = await ctx.bot.fetch_user(user_id)
            except Exception:  # noqa: BLE001
                continue

        if user is None:
            continue

        # Create action for this user
        refs = profile.get("inside_references", [])
        ref_line = f"Shared refs: {', '.join(str(x) for x in refs[:2])}" if isinstance(refs, list) else ""

        async def execute(user_ref=user, ref_line_ref=ref_line):
            text = await ctx._generate_text(
                "Write one short genuine check-in DM message.",
                ref_line_ref,
                fallback="you crossed my mind for no dramatic reason. hope your week is treating you okay.",
            )
            try:
                await user_ref.send(text[:1800])
                return {"success": True}
            except Exception:  # noqa: BLE001
                LOGGER.exception("Failed confidant DM")
                return {"success": False}

        action = Action(
            type="confidant_maintenance",
            user_id=user_id,
            description=f"DM confidant {user_id}",
            execute_fn=execute,
            priority=1.0,
        )
        actions.append(action)
        sent_count += 1

    return actions


async def _create_expansion_action(ctx: BehaviorContext) -> Any | None:
    """Create action to process expansion queue."""
    from mandy_v1.services.autonomy_engine import Action

    async def execute():
        sent = await ctx.expansion.process_queue(ctx.bot, ctx.ai)
        return {"success": True, "engagement": float(sent) / 10.0, "responses": []}

    return Action(
        type="expansion_queue_processing",
        description="Process expansion queue",
        execute_fn=execute,
        priority=1.0,
    )
