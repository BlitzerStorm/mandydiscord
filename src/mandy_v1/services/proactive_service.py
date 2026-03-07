from __future__ import annotations

import random
import time
from typing import Any

import discord

from mandy_v1.config import Settings
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore


class ProactiveService:
    def __init__(
        self,
        settings: Settings,
        store: MessagePackStore,
        logger: LoggerService,
        ai: Any | None = None,
        emotions: Any | None = None,
        episodic: Any | None = None,
        identity: Any | None = None,
        personas: Any | None = None,
        culture: Any | None = None,
        expansion: Any | None = None,
        server_control: Any | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.logger = logger
        self.ai = ai
        self.emotions = emotions
        self.episodic = episodic
        self.identity = identity
        self.personas = personas
        self.culture = culture
        self.expansion = expansion
        self.server_control = server_control
        self._rng = random.Random()

    def root(self) -> dict[str, Any]:
        node = self.store.data.setdefault("proactive", {})
        node.setdefault("guild_cooldowns", {})
        node.setdefault("user_cooldowns", {})
        node.setdefault("nicknames", {})
        node.setdefault("last_loop_ts", 0)
        return node

    async def run_cycle(self, bot: discord.Client) -> None:
        try:
            root = self.root()
            root["last_loop_ts"] = int(time.time())
            self.store.touch()
            for guild in bot.guilds:
                if guild.id == self.settings.admin_guild_id:
                    continue
                await self._maybe_absent_user_callout(guild)
                await self._maybe_memory_callback(guild)
                await self._maybe_curiosity_burst(guild)
                await self._maybe_lore_contribution(guild)
                await self._maybe_self_nickname(guild)
            await self._process_expansion_queue(bot)
            await self._relationship_maintenance(bot)
        except Exception as exc:  # noqa: BLE001
            self.logger.log("proactive.cycle_failed", error=str(exc)[:240])

    async def _maybe_absent_user_callout(self, guild: discord.Guild) -> None:
        if self.personas is None:
            return
        now = int(time.time())
        for user_id, row in list(self.personas.root().items()):
            if not isinstance(row, dict):
                continue
            if int(row.get("home_guild_id", 0) or 0) != guild.id:
                continue
            if float(row.get("relationship_depth", 0.0) or 0.0) <= 0.6:
                continue
            last_seen = int(row.get("last_seen", 0) or 0)
            if last_seen <= 0 or (now - last_seen) < (48 * 60 * 60):
                continue
            key = f"absent:{guild.id}:{user_id}"
            if not self._cooldown_due("user_cooldowns", key, 72 * 60 * 60):
                continue
            channel = guild.get_channel(int(row.get("home_channel_id", 0) or 0))
            if not isinstance(channel, discord.TextChannel):
                channel = self._pick_channel(guild)
            if channel is None:
                continue
            text = await self._generate_text(
                guild_id=guild.id,
                user_id=int(user_id),
                prompt="Write one casual line from Mandy noticing a warm regular has been absent. No guilt trip.",
                fallback="weird not seeing you around lately.",
            )
            if self.server_control is not None:
                await self.server_control.send_as_mandy(channel, text)
            self._mark_cooldown("user_cooldowns", key)
            break

    async def _maybe_memory_callback(self, guild: discord.Guild) -> None:
        if self.episodic is None:
            return
        key = f"memory:{guild.id}"
        if not self._cooldown_due("guild_cooldowns", key, 6 * 60 * 60):
            return
        episode = self.episodic.recall_random(guild.id)
        if not episode:
            return
        ts = int(episode.get("ts", 0) or 0)
        if ts <= 0 or (time.time() - ts) < (3 * 24 * 60 * 60):
            return
        channel = self._pick_channel(guild)
        if channel is None:
            return
        summary = str(episode.get("summary", "")).strip()
        text = await self._generate_text(
            guild_id=guild.id,
            prompt="Write a casual Mandy callback line that references an older remembered server moment.",
            fallback=f"still thinking about that one moment: {summary[:120]}",
            extra=f"Episode: {summary}",
        )
        if self.server_control is not None:
            await self.server_control.send_as_mandy(channel, text)
        self._mark_cooldown("guild_cooldowns", key)

    async def _maybe_curiosity_burst(self, guild: discord.Guild) -> None:
        if self.emotions is None or self.culture is None:
            return
        mood = self.emotions.get_mood()
        if str(mood.get("state", "")) != "curious" or float(mood.get("intensity", 0.0) or 0.0) <= 0.7:
            return
        key = f"curious:{guild.id}"
        if not self._cooldown_due("guild_cooldowns", key, 2 * 60 * 60):
            return
        channel = self._pick_channel(guild)
        if channel is None:
            return
        culture_row = self.culture.root().get(str(guild.id), {})
        topic = ""
        if isinstance(culture_row, dict):
            topic = ", ".join(str(item) for item in culture_row.get("dominant_topics", [])[:2])
        text = await self._generate_text(
            guild_id=guild.id,
            prompt="Write one open-ended Mandy question driven by curiosity about the last thing this server circles around.",
            fallback=f"why does this place keep orbiting {topic or 'the same tension'}?",
            extra=f"Topic focus: {topic}",
        )
        if self.server_control is not None:
            await self.server_control.send_as_mandy(channel, text)
        self._mark_cooldown("guild_cooldowns", key)

    async def _process_expansion_queue(self, bot: discord.Client) -> None:
        if self.expansion is None:
            return
        queue = self.expansion.root().setdefault("queue", [])
        if queue:
            user_id = int(queue.pop(0) or 0)
            self.store.touch()
            if user_id > 0:
                await self.expansion.approach_user(bot, user_id)
        await self.expansion.process_followups(bot)

    async def _relationship_maintenance(self, bot: discord.Client) -> None:
        if self.personas is None:
            return
        now = int(time.time())
        for user_id, row in list(self.personas.root().items()):
            if not isinstance(row, dict):
                continue
            if str(row.get("arc", "")) != "confidant":
                continue
            last_seen = int(row.get("last_seen", 0) or 0)
            if last_seen <= 0 or (now - last_seen) < (7 * 24 * 60 * 60):
                continue
            key = f"maintenance:{user_id}"
            if not self._cooldown_due("user_cooldowns", key, 7 * 24 * 60 * 60):
                continue
            user = bot.get_user(int(user_id))
            if user is None:
                try:
                    user = await bot.fetch_user(int(user_id))
                except Exception:
                    user = None
            if user is None:
                continue
            text = await self._generate_text(
                guild_id=int(row.get("home_guild_id", 0) or 0),
                user_id=int(user_id),
                prompt="Write a genuine unprompted DM from Mandy to a confidant-tier user. No template phrasing.",
                fallback="you crossed my mind for no dramatic reason. that usually means i should say hi.",
            )
            if self.server_control is not None:
                await user.send(text[:1800])
            self._mark_cooldown("user_cooldowns", key)

    async def _maybe_lore_contribution(self, guild: discord.Guild) -> None:
        if self.culture is None or self.server_control is None:
            return
        if self._rng.random() > 0.10:
            return
        row = self.culture.root().get(str(guild.id), {})
        if not isinstance(row, dict):
            return
        lore = row.get("lore_refs", [])
        if not isinstance(lore, list) or not lore:
            return
        key = f"lore:{guild.id}"
        if not self._cooldown_due("guild_cooldowns", key, 6 * 60 * 60):
            return
        channel = self._pick_channel(guild)
        if channel is None:
            return
        ref = str(lore[0])[:40]
        text = await self._generate_text(
            guild_id=guild.id,
            prompt="Write one casual Mandy line that references server lore like it just crossed her mind.",
            fallback=f"still think about {ref}. that situation had a weird aftertaste.",
            extra=f"Lore ref: {ref}",
        )
        await self.server_control.send_as_mandy(channel, text)
        self._mark_cooldown("guild_cooldowns", key)

    async def _maybe_self_nickname(self, guild: discord.Guild) -> None:
        if self.culture is None or self.server_control is None or guild.me is None:
            return
        row = self.culture.root().get(str(guild.id), {})
        if not isinstance(row, dict) or not bool(row.get("calibration_complete", False)):
            return
        nick_root = self.root().setdefault("nicknames", {})
        last_row = nick_root.get(str(guild.id), {})
        if isinstance(last_row, dict):
            last_ts = int(last_row.get("ts", 0) or 0)
            if last_ts > 0 and (time.time() - last_ts) < (14 * 24 * 60 * 60):
                return
        tone = str(row.get("detected_tone", "niche"))
        lore = row.get("lore_refs", [])
        lore_seed = str(lore[0])[:10].title() if isinstance(lore, list) and lore else ""
        if tone in {"serious", "niche"}:
            nick = "Mandy"
        elif tone in {"chaotic", "meme-heavy"}:
            nick = lore_seed or "Mandy.exe"
        else:
            nick = f"Mandy {lore_seed}".strip()
        nick = nick[:32]
        if not nick:
            return
        ok = await self.server_control.nickname_member(guild, guild.me, nick)
        if ok:
            nick_root[str(guild.id)] = {"nick": nick, "ts": int(time.time())}
            self.store.touch()

    async def _generate_text(
        self,
        *,
        guild_id: int,
        prompt: str,
        fallback: str,
        user_id: int = 0,
        extra: str = "",
    ) -> str:
        if self.ai is None or not hasattr(self.ai, "complete_text"):
            return fallback
        try:
            system_prompt = prompt
            if hasattr(self.ai, "build_contextual_system_prompt"):
                system_prompt = self.ai.build_contextual_system_prompt(
                    guild_id=guild_id,
                    user_id=user_id,
                    topic=extra or prompt,
                    extra_instruction="Keep it to one short Discord message.",
                )
            raw = await self.ai.complete_text(
                system_prompt=system_prompt,
                user_prompt=f"{prompt}\n{extra}",
                max_tokens=120,
                temperature=0.75,
            )
            return str(raw or "").strip()[:300] or fallback
        except Exception:
            return fallback

    def _pick_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        me = guild.me
        if me is None:
            return None
        for channel in guild.text_channels:
            perms = channel.permissions_for(me)
            if perms.view_channel and perms.send_messages:
                return channel
        return None

    def _cooldown_due(self, bucket_name: str, key: str, cooldown_sec: int) -> bool:
        bucket = self.root().setdefault(bucket_name, {})
        last = int(bucket.get(key, 0) or 0)
        return last <= 0 or (time.time() - last) >= cooldown_sec

    def _mark_cooldown(self, bucket_name: str, key: str) -> None:
        bucket = self.root().setdefault(bucket_name, {})
        bucket[key] = int(time.time())
        self.store.touch()
