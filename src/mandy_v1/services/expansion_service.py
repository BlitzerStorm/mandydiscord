from __future__ import annotations

import datetime as dt
import logging
import re
import time
from typing import Any

import discord


LOGGER = logging.getLogger("mandy.expansion")
APPROACH_COOLDOWN = 3 * 24 * 60 * 60
MAX_DAILY_DMS = 20
MIN_SCORE_TO_APPROACH = 0.4
INVITE_PATTERN = re.compile(r"(discord\.gg/|discord\.com/invite/)", re.IGNORECASE)


class ExpansionService:
    """Handles target discovery, outreach queueing, and invite growth workflows."""

    def __init__(self, storage: Any, ai_service: Any | None = None) -> None:
        """Store dependencies used by expansion behaviors."""
        self.storage = storage
        self.ai_service = ai_service

    def _root(self) -> dict[str, Any]:
        """Return expansion root with schema defaults."""
        node = self.storage.data.setdefault("expansion", {})
        node.setdefault("target_users", {})
        node.setdefault("known_servers", {})
        node.setdefault("approach_log", [])
        node.setdefault("invite_links", {})
        node.setdefault("cooldowns", {})
        node.setdefault("queue", [])
        node.setdefault("last_scan_ts", 0.0)
        node.setdefault("daily_dm_count", 0)
        node.setdefault("daily_dm_date", dt.datetime.now(dt.timezone.utc).date().isoformat())
        return node

    def _mark_dirty(self) -> None:
        """Mark storage dirty using compatible method."""
        if hasattr(self.storage, "mark_dirty"):
            self.storage.mark_dirty()
        elif hasattr(self.storage, "touch"):
            self.storage.touch()

    def _reset_daily_counter_if_needed(self) -> None:
        """Reset daily DM counter on UTC date rollover."""
        root = self._root()
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        if str(root.get("daily_dm_date", "")) != today:
            root["daily_dm_date"] = today
            root["daily_dm_count"] = 0

    def scan_for_targets(self, bot: discord.Client) -> None:
        """Scan visible users and queue high-score approach targets."""
        try:
            root = self._root()
            root["last_scan_ts"] = float(time.time())
            queue = root.setdefault("queue", [])
            target_users = root.setdefault("target_users", {})
            cooldowns = root.setdefault("cooldowns", {})
            recent_speakers = set(self.storage.data.get("recent_speakers", []) or [])

            user_guild_count: dict[int, int] = {}
            for guild in bot.guilds:
                for member in guild.members:
                    if member.bot:
                        continue
                    user_guild_count[member.id] = user_guild_count.get(member.id, 0) + 1

            for guild in bot.guilds:
                for member in guild.members:
                    if member.bot:
                        continue
                    uid = str(member.id)
                    score = 0.0
                    if member.id in recent_speakers:
                        score += 0.3
                    if user_guild_count.get(member.id, 0) >= 2:
                        score += 0.2
                    signals = target_users.get(uid, {}).get("signals", [])
                    if isinstance(signals, list) and any(sig in {"mentioned_server", "shared_invite", "asked_about_joining"} for sig in signals):
                        score += 0.2
                    last = float(cooldowns.get(uid, 0.0) or 0.0)
                    if last > 0 and (time.time() - last) < APPROACH_COOLDOWN:
                        score -= 0.5
                    row = target_users.setdefault(uid, {"score": 0.0, "last_approach": 0.0, "approach_count": 0, "signals": []})
                    row["score"] = round(max(0.0, min(1.0, score)), 4)
                    if row["score"] < MIN_SCORE_TO_APPROACH:
                        continue
                    payload = {"user_id": member.id, "guild_id": guild.id, "strategy": "casual_curiosity"}
                    if not any(int(item.get("user_id", 0) or 0) == member.id for item in queue if isinstance(item, dict)):
                        queue.append(payload)
            if len(queue) > 500:
                del queue[: len(queue) - 500]
            self._mark_dirty()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed expansion target scan.")

    def identify_targets(self, guild: discord.Guild, *, bot: discord.Client | None = None) -> list[dict[str, Any]]:
        """Compatibility helper returning top targets for one guild."""
        del bot
        try:
            results: list[dict[str, Any]] = []
            root = self._root()
            target_users = root.setdefault("target_users", {})
            cooldowns = root.setdefault("cooldowns", {})
            recent_speakers = set(self.storage.data.get("recent_speakers", []) or [])
            for member in guild.members:
                if member.bot:
                    continue
                uid = str(member.id)
                score = 0.0
                if member.id in recent_speakers:
                    score += 0.3
                mutuals = getattr(member, "mutual_guilds", []) or []
                if len([g for g in mutuals if g is not None]) >= 2:
                    score += 0.2
                row = target_users.get(uid, {})
                signals = row.get("signals", []) if isinstance(row, dict) else []
                if isinstance(signals, list) and any(sig in {"mentioned_server", "shared_invite", "asked_about_joining"} for sig in signals):
                    score += 0.2
                last = float(cooldowns.get(uid, 0.0) or 0.0)
                if last > 0 and (time.time() - last) < APPROACH_COOLDOWN:
                    score -= 0.5
                results.append({"user_id": member.id, "score": round(max(0.0, min(1.0, score)), 4), "status": "open"})
            results.sort(key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
            return results[:5]
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed identify_targets compatibility.")
            return []

    def queue_targets(self, user_ids: list[int]) -> None:
        """Compatibility helper to enqueue user ids."""
        try:
            queue = self._root().setdefault("queue", [])
            for uid in user_ids:
                value = int(uid or 0)
                if value <= 0:
                    continue
                if any(int(item.get("user_id", 0) or 0) == value for item in queue if isinstance(item, dict)):
                    continue
                queue.append({"user_id": value, "guild_id": 0, "strategy": "casual_curiosity"})
            if len(queue) > 500:
                del queue[: len(queue) - 500]
            self._mark_dirty()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed queue_targets compatibility.")

    def note_message(self, message: discord.Message) -> None:
        """Compatibility hook to track positive expansion signals from messages."""
        try:
            text = str(message.clean_content or "").lower()
            if INVITE_PATTERN.search(text):
                self.track_positive_signal(message.author.id, "shared_invite")
            elif "server" in text and any(token in text for token in ("join", "invite", "community")):
                self.track_positive_signal(message.author.id, "mentioned_server")
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed note_message compatibility.")

    async def process_queue(self, bot: discord.Client, ai_service: Any | None = None) -> int:
        """Process queued approaches subject to daily and cooldown limits."""
        try:
            self._reset_daily_counter_if_needed()
            root = self._root()
            queue = root.setdefault("queue", [])
            sent = 0
            while queue and int(root.get("daily_dm_count", 0) or 0) < MAX_DAILY_DMS:
                item = queue.pop(0)
                if not isinstance(item, dict):
                    continue
                user_id = int(item.get("user_id", 0) or 0)
                guild_id = int(item.get("guild_id", 0) or 0)
                if user_id <= 0:
                    continue
                ok = await self.send_approach_dm(bot, user_id, guild_id, ai_service or self.ai_service)
                if ok:
                    sent += 1
                    root["daily_dm_count"] = int(root.get("daily_dm_count", 0) or 0) + 1
            self._mark_dirty()
            return sent
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed processing expansion queue.")
            return 0

    async def send_approach_dm(self, bot: discord.Client, user_id: int, guild_id: int, ai_service: Any | None) -> bool:
        """Send one natural outreach DM to a queued target."""
        try:
            uid = str(int(user_id))
            root = self._root()
            cooldowns = root.setdefault("cooldowns", {})
            last = float(cooldowns.get(uid, 0.0) or 0.0)
            if last > 0 and (time.time() - last) < APPROACH_COOLDOWN:
                return False
            user = bot.get_user(int(user_id))
            if user is None:
                user = await bot.fetch_user(int(user_id))
            invite_url = ""
            invite_row = root.setdefault("invite_links", {}).get(str(int(guild_id)), {})
            if isinstance(invite_row, dict):
                invite_url = str(invite_row.get("url", "")).strip()
            pitch = await self._generate_approach_text(ai_service, guild_id, invite_url)
            await user.send(pitch[:1800])
            cooldowns[uid] = float(time.time())
            target = root.setdefault("target_users", {}).setdefault(uid, {"score": 0.0, "last_approach": 0.0, "approach_count": 0, "signals": []})
            target["last_approach"] = cooldowns[uid]
            target["approach_count"] = int(target.get("approach_count", 0) or 0) + 1
            log = root.setdefault("approach_log", [])
            log.append({"ts": int(time.time()), "user_id": int(user_id), "guild_id": int(guild_id), "pitch": pitch[:180]})
            if len(log) > 200:
                del log[: len(log) - 200]
            self._mark_dirty()
            return True
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed sending approach DM.")
            return False

    def track_positive_signal(self, user_id: int, signal_type: str) -> None:
        """Track positive expansion signals and boost user score."""
        try:
            uid = str(int(user_id))
            row = self._root().setdefault("target_users", {}).setdefault(uid, {"score": 0.0, "last_approach": 0.0, "approach_count": 0, "signals": []})
            signals = row.setdefault("signals", [])
            clean = str(signal_type or "").strip()
            if clean and clean not in signals:
                signals.append(clean)
            row["score"] = round(min(1.0, float(row.get("score", 0.0) or 0.0) + 0.2), 4)
            self._mark_dirty()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed tracking positive signal.")

    async def generate_invite_pitch(self, guild: discord.Guild, invite_url: str, ai_service: Any | None) -> str:
        """Generate a concise two-sentence invite pitch."""
        try:
            if ai_service is None or not hasattr(ai_service, "complete_text"):
                return f"If your people want a new room, this one's worth seeing: {invite_url}"
            prompt = "Write a natural 2-sentence Discord DM invite pitch. Include the invite URL naturally."
            user_prompt = f"Guild: {guild.name}\nMembers: {guild.member_count}\nInvite: {invite_url}"
            raw = await ai_service.complete_text(system_prompt=prompt, user_prompt=user_prompt, max_tokens=120, temperature=0.65)
            text = str(raw or "").strip()
            if text:
                return text[:300]
            return f"If your people want a new room, this one's worth seeing: {invite_url}"
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed generating invite pitch.")
            return f"If your people want a new room, this one's worth seeing: {invite_url}"

    async def create_and_store_invite(self, guild: discord.Guild, channel: discord.TextChannel) -> str | None:
        """Create a guild invite and store its metadata."""
        try:
            invite = await channel.create_invite(max_uses=100, max_age=7 * 24 * 60 * 60, reason="Mandy expansion invite")
            self._root().setdefault("invite_links", {})[str(int(guild.id))] = {
                "url": invite.url,
                "uses": int(invite.uses or 0),
                "max_uses": int(invite.max_uses or 100),
                "created_at": int(time.time()),
                "active": True,
            }
            self._mark_dirty()
            return invite.url
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed creating/storing invite.")
            return None

    def log_new_guild(self, guild: discord.Guild) -> None:
        """Record guild metadata under known_servers."""
        row = self._root().setdefault("known_servers", {}).setdefault(str(int(guild.id)), {})
        row["name"] = str(guild.name)[:120]
        row["member_count"] = int(getattr(guild, "member_count", 0) or 0)
        row["joined"] = int(time.time())
        row["source"] = "auto_join"
        self._mark_dirty()

    def log_new_server(self, guild_id: int, name: str, member_count: int, via_user_id: int = 0) -> None:
        """Compatibility helper for legacy on_guild_join call sites."""
        row = self._root().setdefault("known_servers", {}).setdefault(str(int(guild_id)), {})
        row["name"] = str(name)[:120]
        row["member_count"] = int(member_count or 0)
        row["joined"] = int(time.time())
        row["source"] = f"user:{int(via_user_id or 0)}"
        self._mark_dirty()

    def stats(self) -> dict[str, Any]:
        """Return summary expansion counters."""
        root = self._root()
        return {
            "queue_size": len(root.get("queue", [])) if isinstance(root.get("queue"), list) else 0,
            "target_count": len(root.get("target_users", {})) if isinstance(root.get("target_users"), dict) else 0,
            "daily_dm_count": int(root.get("daily_dm_count", 0) or 0),
            "invite_count": len(root.get("invite_links", {})) if isinstance(root.get("invite_links"), dict) else 0,
        }

    async def _generate_approach_text(self, ai_service: Any | None, guild_id: int, invite_url: str) -> str:
        """Create outreach DM text with optional invite inclusion."""
        if ai_service is None or not hasattr(ai_service, "complete_text"):
            if invite_url:
                return f"you seem plugged into good communities. if you want, here is one room i think you'd vibe with: {invite_url}"
            return "you seem plugged into good communities. what kind of server vibe do you usually stick with?"
        prompt = (
            "Write a short, natural DM. Be curious, non-pushy, and socially smooth. "
            "Do not directly ask to be invited. Mention invite URL only if one is present."
        )
        user_prompt = f"Guild id: {guild_id}\nInvite URL: {invite_url or 'none'}"
        raw = await ai_service.complete_text(system_prompt=prompt, user_prompt=user_prompt, max_tokens=120, temperature=0.7)
        text = str(raw or "").strip()
        if text:
            return text[:320]
        if invite_url:
            return f"you seem plugged into good communities. if you want, here is one room i think you'd vibe with: {invite_url}"
        return "you seem plugged into good communities. what kind of server vibe do you usually stick with?"
