from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import discord


LOGGER = logging.getLogger("mandy.proactive")


class ProactiveService:
    """Runs periodic autonomous initiative behaviors for Mandy."""

    def __init__(
        self,
        bot: discord.Client,
        storage: Any,
        ai_service: Any,
        emotion_service: Any,
        episodic_memory_service: Any,
        persona_service: Any,
        culture_service: Any,
        expansion_service: Any,
    ) -> None:
        """Persist dependencies and initialize runtime loop handles."""
        self.bot = bot
        self.storage = storage
        self.ai_service = ai_service
        self.emotion_service = emotion_service
        self.episodic_memory_service = episodic_memory_service
        self.persona_service = persona_service
        self.culture_service = culture_service
        self.expansion_service = expansion_service
        self._task: asyncio.Task | None = None
        self._rng = random.Random()

    def _root(self) -> dict[str, Any]:
        """Return proactive state root with defaults."""
        node = self.storage.data.setdefault("proactive", {})
        node.setdefault("guild_cooldowns", {})
        node.setdefault("user_cooldowns", {})
        node.setdefault("nicknames", {})
        node.setdefault("last_loop_ts", 0.0)
        return node

    def _mark_dirty(self) -> None:
        """Mark storage dirty using compatible store API."""
        if hasattr(self.storage, "mark_dirty"):
            self.storage.mark_dirty()
        elif hasattr(self.storage, "touch"):
            self.storage.touch()

    def start(self) -> None:
        """Start the background proactive loop if not already running."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_loop(), name="mandy-proactive-loop")

    async def stop(self) -> None:
        """Stop the proactive loop task gracefully."""
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_loop(self) -> None:
        """Loop every 4 minutes and execute proactive behaviors with cooldowns."""
        await asyncio.sleep(20)
        while True:
            try:
                self._root()["last_loop_ts"] = float(time.time())
                self._mark_dirty()
                for guild in self.bot.guilds:
                    await self._behavior_absent_user_callout(guild)
                    await self._behavior_episodic_callback(guild)
                    await self._behavior_curiosity_burst(guild)
                    await self._behavior_lore_callback(guild)
                    await self._behavior_self_nickname_update(guild)
                await self._behavior_expansion_queue_processing()
                await self._behavior_confidant_maintenance()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Proactive loop tick failed.")
            await asyncio.sleep(4 * 60)

    async def _behavior_absent_user_callout(self, guild: discord.Guild) -> None:
        """Mention recently active users absent for 6-48 hours (2h guild cooldown)."""
        if not self._guild_cooldown_due(guild.id, "absent_user_callout", 2 * 60 * 60):
            return
        now = time.time()
        candidate_id = None
        for uid, profile in self.persona_service._root().items():  # noqa: SLF001
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
            return
        channel = self._pick_general_channel(guild)
        if channel is None:
            return
        text = await self._generate_text(
            "Write one short casual line mentioning that a regular has been quiet, warm but not clingy.",
            f"Target user id: {candidate_id}",
            fallback=f"haven't seen <@{candidate_id}> around much today. hope you're good.",
        )
        await self._safe_send(channel, text[:1800])
        self._set_guild_cooldown(guild.id, "absent_user_callout")

    async def _behavior_episodic_callback(self, guild: discord.Guild) -> None:
        """Surface an older memory in public chat (3h guild cooldown)."""
        if not self._guild_cooldown_due(guild.id, "episodic_callback", 3 * 60 * 60):
            return
        rows = self.storage.data.get("episodic", {}).get("episodes", {}).get(str(guild.id), [])
        if not isinstance(rows, list) or not rows:
            return
        choice = rows[max(0, len(rows) - 1 - self._rng.randint(0, min(15, len(rows) - 1)))]
        if not isinstance(choice, dict):
            return
        age_sec = time.time() - float(choice.get("ts", 0) or 0)
        if age_sec < 6 * 60 * 60:
            return
        channel = self._pick_general_channel(guild)
        if channel is None:
            return
        fallback = f"i was just thinking about when {choice.get('author_name','someone')} said: {str(choice.get('content',''))[:100]}"
        text = await self._generate_text(
            "Write one short memory callback line for a server.",
            str(choice.get("content", "")),
            fallback=fallback,
        )
        await self._safe_send(channel, text[:1800])
        self._set_guild_cooldown(guild.id, "episodic_callback")

    async def _behavior_curiosity_burst(self, guild: discord.Guild) -> None:
        """Post a spontaneous curiosity question (90m guild cooldown)."""
        if not self._guild_cooldown_due(guild.id, "curiosity_burst", 90 * 60):
            return
        interests = self.storage.data.get("identity", {}).get("interests", [])
        topic = str(self._rng.choice(interests) if isinstance(interests, list) and interests else "community dynamics")
        channel = self._pick_general_channel(guild)
        if channel is None:
            return
        text = await self._generate_text(
            "Write one short curiosity question to a Discord server.",
            f"Interest topic: {topic}",
            fallback=f"random thought: what is everyone's take on {topic} lately?",
        )
        await self._safe_send(channel, text[:1800])
        self._set_guild_cooldown(guild.id, "curiosity_burst")

    async def _behavior_expansion_queue_processing(self) -> None:
        """Process expansion queue globally every 30 minutes."""
        if not self._global_cooldown_due("expansion_queue_processing", 30 * 60):
            return
        await self.expansion_service.process_queue(self.bot, self.ai_service)
        self._set_global_cooldown("expansion_queue_processing")

    async def _behavior_confidant_maintenance(self) -> None:
        """DM users with relationship depth >= 4 (12h user cooldown, max 2/tick)."""
        sent = 0
        now = time.time()
        for uid, profile in self.persona_service._root().items():  # noqa: SLF001
            if sent >= 2:
                break
            if not isinstance(profile, dict):
                continue
            if int(profile.get("relationship_depth", 0) or 0) < 4:
                continue
            if not self._user_cooldown_due(int(uid), "confidant_maintenance", 12 * 60 * 60):
                continue
            user = self.bot.get_user(int(uid))
            if user is None:
                try:
                    user = await self.bot.fetch_user(int(uid))
                except Exception:  # noqa: BLE001
                    user = None
            if user is None:
                continue
            refs = profile.get("inside_references", [])
            ref_line = f"Shared refs: {', '.join(str(x) for x in refs[:2])}" if isinstance(refs, list) else ""
            text = await self._generate_text(
                "Write one short genuine check-in DM message.",
                ref_line,
                fallback="you crossed my mind for no dramatic reason. hope your week is treating you okay.",
            )
            try:
                await user.send(text[:1800])
                self._set_user_cooldown(int(uid), "confidant_maintenance")
                sent += 1
            except Exception:  # noqa: BLE001
                LOGGER.exception("Failed confidant maintenance DM.")
        if now:
            pass

    async def _behavior_lore_callback(self, guild: discord.Guild) -> None:
        """Reference server lore in public chat (4h guild cooldown)."""
        if not self._guild_cooldown_due(guild.id, "lore_callback", 4 * 60 * 60):
            return
        lore = self.storage.data.get("culture", {}).get(str(guild.id), {}).get("lore_refs", [])
        if not isinstance(lore, list) or not lore:
            return
        ref = str(self._rng.choice(lore))
        channel = self._pick_general_channel(guild)
        if channel is None:
            return
        text = await self._generate_text(
            "Write one short line that references in-server lore naturally.",
            f"Lore ref: {ref}",
            fallback=f"still not over '{ref}' by the way.",
        )
        await self._safe_send(channel, text[:1800])
        self._set_guild_cooldown(guild.id, "lore_callback")

    async def _behavior_self_nickname_update(self, guild: discord.Guild) -> None:
        """Occasionally update Mandy's own nickname (24h guild cooldown, 10% chance)."""
        if not self._guild_cooldown_due(guild.id, "self_nickname_update", 24 * 60 * 60):
            return
        if self._rng.random() > 0.10:
            return
        me = guild.me
        if me is None:
            return
        mood = self.emotion_service.get_state()
        persona = self.culture_service.get_mandy_persona(guild.id)
        nick = await self._generate_text(
            "Return a nickname only, max 32 chars, based on mood and server vibe.",
            f"Mood: {mood}\nPersona: {persona}",
            fallback=f"Mandy {mood[:8]}",
        )
        nick = nick.strip().replace("\n", " ")[:32]
        if not nick:
            return
        try:
            await me.edit(nick=nick, reason="Mandy proactive nickname")
            self._root().setdefault("nicknames", {})[str(guild.id)] = nick
            self._set_guild_cooldown(guild.id, "self_nickname_update")
            self._mark_dirty()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed proactive nickname update.")

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

    async def _safe_send(self, channel: discord.TextChannel, text: str) -> None:
        """Send a message with all exceptions swallowed."""
        try:
            await channel.send(text[:1900])
        except Exception:  # noqa: BLE001
            LOGGER.exception("Proactive send failed.")

    async def _generate_text(self, system_prompt: str, user_prompt: str, fallback: str) -> str:
        """Generate one short AI text response with safe fallback."""
        try:
            if self.ai_service is None or not hasattr(self.ai_service, "complete_text"):
                return fallback
            raw = await self.ai_service.complete_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=120,
                temperature=0.7,
            )
            text = str(raw or "").strip()
            return text or fallback
        except Exception:  # noqa: BLE001
            LOGGER.exception("Proactive AI generation failed.")
            return fallback

    def _guild_cooldown_due(self, guild_id: int, key: str, cooldown_sec: int) -> bool:
        """Return whether a guild behavior cooldown has elapsed."""
        cooldowns = self._root().setdefault("guild_cooldowns", {})
        raw = cooldowns.get(str(guild_id), {})
        row = raw if isinstance(raw, dict) else {}
        last = float(row.get(key, 0.0) or 0.0)
        return (time.time() - last) >= cooldown_sec

    def _set_guild_cooldown(self, guild_id: int, key: str) -> None:
        """Set a guild behavior cooldown timestamp."""
        cooldowns = self._root().setdefault("guild_cooldowns", {})
        row = cooldowns.setdefault(str(guild_id), {})
        row[key] = float(time.time())
        self._mark_dirty()

    def _user_cooldown_due(self, user_id: int, key: str, cooldown_sec: int) -> bool:
        """Return whether a user behavior cooldown has elapsed."""
        cooldowns = self._root().setdefault("user_cooldowns", {})
        raw = cooldowns.get(str(user_id), {})
        row = raw if isinstance(raw, dict) else {}
        last = float(row.get(key, 0.0) or 0.0)
        return (time.time() - last) >= cooldown_sec

    def _set_user_cooldown(self, user_id: int, key: str) -> None:
        """Set a user behavior cooldown timestamp."""
        cooldowns = self._root().setdefault("user_cooldowns", {})
        row = cooldowns.setdefault(str(user_id), {})
        row[key] = float(time.time())
        self._mark_dirty()

    def _global_cooldown_due(self, key: str, cooldown_sec: int) -> bool:
        """Return whether a global proactive cooldown has elapsed."""
        row = self._root()
        last = float(row.get(f"_global_{key}", 0.0) or 0.0)
        return (time.time() - last) >= cooldown_sec

    def _set_global_cooldown(self, key: str) -> None:
        """Set a global proactive cooldown timestamp."""
        row = self._root()
        row[f"_global_{key}"] = float(time.time())
        self._mark_dirty()
