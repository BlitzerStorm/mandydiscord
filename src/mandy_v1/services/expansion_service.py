from __future__ import annotations

import json
import time
from typing import Any

import discord

from mandy_v1.config import Settings
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore


OTHER_SERVER_TERMS = ("server", "servers", "community", "guild", "discord", "mod team", "other place")


class ExpansionService:
    def __init__(
        self,
        settings: Settings,
        store: MessagePackStore,
        logger: LoggerService,
        ai: Any | None = None,
        personas: Any | None = None,
        server_control: Any | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.logger = logger
        self.ai = ai
        self.personas = personas
        self.server_control = server_control

    def root(self) -> dict[str, Any]:
        node = self.store.data.setdefault("expansion", {})
        node.setdefault("target_users", {})
        node.setdefault("known_servers", {})
        node.setdefault("approach_log", [])
        node.setdefault("invite_links", {})
        node.setdefault("cooldowns", {})
        node.setdefault("queue", [])
        node.setdefault("last_scan_ts", 0)
        return node

    def identify_targets(self, guild: discord.Guild, *, bot: discord.Client | None = None) -> list[dict[str, Any]]:
        try:
            targets: list[dict[str, Any]] = []
            target_root = self.root().setdefault("target_users", {})
            for member in guild.members:
                if member.bot:
                    continue
                mutual_servers = 0
                if bot is not None:
                    mutual_servers = sum(1 for item in bot.guilds if item.get_member(member.id) is not None)
                profile = self.personas.root().get(str(member.id), {}) if self.personas is not None else {}
                if not isinstance(profile, dict):
                    profile = {}
                score = 0.0
                if mutual_servers >= 2:
                    score += 0.4
                if int(profile.get("total_interactions", 0) or 0) >= 15:
                    score += 0.2
                depth = float(profile.get("relationship_depth", 0.0) or 0.0)
                if depth >= 0.55:
                    score += 0.3
                topics = " ".join(str(item) for item in profile.get("topics_they_care_about", [])[:8]).lower()
                if any(term in topics for term in OTHER_SERVER_TERMS):
                    score += 0.2
                response = str(profile.get("response_to_mandy", "")).lower()
                if response in {"trusting", "playful", "engaged"}:
                    score += 0.15
                target_row = target_root.setdefault(str(member.id), {})
                target_row.update(
                    {
                        "score": round(min(1.25, score), 3),
                        "server_count": mutual_servers,
                        "last_approach": int(target_row.get("last_approach", 0) or 0),
                        "status": str(target_row.get("status", "open") or "open"),
                    }
                )
                targets.append(
                    {
                        "user_id": member.id,
                        "user_name": member.display_name,
                        "score": target_row["score"],
                        "server_count": mutual_servers,
                        "status": target_row["status"],
                    }
                )
            self.store.touch()
            targets.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
            return targets[:5]
        except Exception as exc:  # noqa: BLE001
            self.logger.log("expansion.identify_failed", guild_id=guild.id if guild else 0, error=str(exc)[:240])
            return []

    def queue_targets(self, user_ids: list[int]) -> None:
        try:
            queue = self.root().setdefault("queue", [])
            for user_id in user_ids:
                if int(user_id) <= 0:
                    continue
                if int(user_id) not in queue:
                    queue.append(int(user_id))
            if len(queue) > 30:
                del queue[: len(queue) - 30]
            self.store.touch()
        except Exception as exc:  # noqa: BLE001
            self.logger.log("expansion.queue_failed", error=str(exc)[:220])

    async def approach_user(self, bot: discord.Client, user_id: int) -> bool:
        try:
            uid = str(int(user_id))
            if uid == "0":
                return False
            root = self.root()
            target_row = root.setdefault("target_users", {}).setdefault(uid, {"score": 0.0, "server_count": 0, "status": "open"})
            log = root.setdefault("approach_log", [])
            cooldowns = root.setdefault("cooldowns", {})
            now = int(time.time())
            last_approach = int(cooldowns.get(uid, 0) or 0)
            attempts = sum(1 for row in log if isinstance(row, dict) and str(row.get("user_id", "")) == uid)
            if target_row.get("status") == "closed" or attempts >= 2:
                target_row["status"] = "closed"
                self.store.touch()
                return False
            if last_approach > 0 and (now - last_approach) < (7 * 24 * 60 * 60):
                return False
            user = bot.get_user(int(uid))
            if user is None:
                try:
                    user = await bot.fetch_user(int(uid))
                except Exception:
                    user = None
            if user is None:
                return False
            text = await self._build_approach_text(user_id=int(uid), username=str(user.display_name if hasattr(user, "display_name") else user))
            await user.send(text[:1800])
            cooldowns[uid] = now
            target_row["last_approach"] = now
            target_row["status"] = "approached"
            log.append({"ts": now, "user_id": int(uid), "status": "approached", "text": text[:200]})
            if len(log) > 50:
                del log[: len(log) - 50]
            self.store.touch()
            self.logger.log("expansion.approached", user_id=int(uid), chars=len(text))
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.log("expansion.approach_failed", user_id=user_id, error=str(exc)[:240])
            return False

    async def generate_invite_pitch(self, guild_id: int, *, user_id: int = 0) -> str:
        try:
            if self.ai is None or not hasattr(self.ai, "complete_text"):
                return "I adapt fast, read the room well, and keep communities moving without flattening them."
            culture_line = ""
            if hasattr(self.ai, "culture") and self.ai.culture is not None:
                culture_line = self.ai.culture.get_server_voice(guild_id)
            user_line = ""
            if self.personas is not None and user_id > 0:
                user_line = self.personas.get_mandy_voice_for(user_id=user_id, guild_id=guild_id)
            raw = await self.ai.complete_text(
                system_prompt=(
                    "Write a 1-2 sentence invite pitch for why Mandy would be valuable in a Discord server. "
                    "Make it specific, social, and natural."
                ),
                user_prompt=f"Server culture:\n{culture_line}\n\nUser context:\n{user_line}",
                max_tokens=120,
                temperature=0.55,
            )
            return str(raw or "").strip()[:280] or "I adapt to the room fast and become useful without needing hand-holding."
        except Exception as exc:  # noqa: BLE001
            self.logger.log("expansion.pitch_failed", guild_id=guild_id, error=str(exc)[:220])
            return "I adapt to the room fast and become useful without needing hand-holding."

    def log_new_server(self, guild_id: int, name: str, member_count: int, via_user_id: int) -> None:
        try:
            row = self.root().setdefault("known_servers", {}).setdefault(str(int(guild_id)), {})
            row.update(
                {
                    "name": str(name or "")[:120],
                    "member_count": int(member_count or 0),
                    "invite_used": int(via_user_id or 0),
                    "joined_ts": int(time.time()),
                }
            )
            self.store.touch()
            self.logger.log("expansion.server_joined", guild_id=guild_id, via_user_id=via_user_id, member_count=member_count)
        except Exception as exc:  # noqa: BLE001
            self.logger.log("expansion.log_server_failed", guild_id=guild_id, error=str(exc)[:220])

    def note_message(self, message: discord.Message) -> None:
        try:
            text = str(message.clean_content or "").lower()
            if not any(term in text for term in OTHER_SERVER_TERMS + ("invite", "join us", "add you", "other guild")):
                return
            target_row = self.root().setdefault("target_users", {}).setdefault(str(int(message.author.id)), {})
            target_row["status"] = "positive_pending"
            target_row["score"] = round(min(1.5, float(target_row.get("score", 0.0) or 0.0) + 0.15), 3)
            log = self.root().setdefault("approach_log", [])
            log.append({"ts": int(time.time()), "user_id": int(message.author.id), "status": "positive_pending", "text": text[:200]})
            if len(log) > 50:
                del log[: len(log) - 50]
            self.store.touch()
        except Exception as exc:  # noqa: BLE001
            self.logger.log("expansion.note_message_failed", error=str(exc)[:220])

    async def process_followups(self, bot: discord.Client) -> None:
        try:
            now = int(time.time())
            for row in list(self.root().setdefault("approach_log", []))[-20:]:
                if not isinstance(row, dict):
                    continue
                if str(row.get("status", "")) != "positive_pending":
                    continue
                ts = int(row.get("ts", 0) or 0)
                user_id = int(row.get("user_id", 0) or 0)
                if user_id <= 0 or (now - ts) < (48 * 60 * 60):
                    continue
                target_row = self.root().setdefault("target_users", {}).setdefault(str(user_id), {})
                if bool(target_row.get("followed_up", False)):
                    continue
                user = bot.get_user(user_id)
                if user is None:
                    try:
                        user = await bot.fetch_user(user_id)
                    except Exception:
                        user = None
                if user is None:
                    continue
                text = "still thinking about what you said about your other space. the vibe sounded interesting."
                await user.send(text)
                target_row["followed_up"] = True
                self.store.touch()
        except Exception as exc:  # noqa: BLE001
            self.logger.log("expansion.followup_failed", error=str(exc)[:220])

    async def generate_and_distribute_invite(self, bot: discord.Client, guild_id: int, *, target_user_id: int = 0) -> str:
        try:
            guild = bot.get_guild(int(guild_id))
            if guild is None or self.server_control is None:
                return ""
            invite_root = self.root().setdefault("invite_links", {})
            existing = invite_root.get(str(int(guild_id)), {})
            now = int(time.time())
            if isinstance(existing, dict):
                ts = int(existing.get("ts", 0) or 0)
                url = str(existing.get("url", "")).strip()
                if url and (now - ts) < (48 * 60 * 60):
                    if target_user_id > 0:
                        await self._dm_invite(bot, target_user_id, guild_id, url)
                    return url
            url = await self.server_control.create_invite(guild, max_age_hours=48)
            if not url:
                return ""
            invite_root[str(int(guild_id))] = {"url": url, "ts": now, "target_user_id": int(target_user_id or 0)}
            self.store.touch()
            if target_user_id > 0:
                await self._dm_invite(bot, target_user_id, guild_id, url)
            return url
        except Exception as exc:  # noqa: BLE001
            self.logger.log("expansion.invite_failed", guild_id=guild_id, error=str(exc)[:220])
            return ""

    async def _dm_invite(self, bot: discord.Client, user_id: int, guild_id: int, url: str) -> None:
        try:
            user = bot.get_user(int(user_id))
            if user is None:
                user = await bot.fetch_user(int(user_id))
            pitch = await self.generate_invite_pitch(guild_id, user_id=user_id)
            await user.send(f"{pitch}\n{url}")
        except Exception as exc:  # noqa: BLE001
            self.logger.log("expansion.dm_invite_failed", user_id=user_id, error=str(exc)[:220])

    async def _build_approach_text(self, *, user_id: int, username: str) -> str:
        profile_block = ""
        if self.personas is not None:
            profile_block = self.personas.get_mandy_voice_for(user_id=user_id, username=username)
        fallback = f"you keep ending up in rooms with very different energy. i like that about you."
        if self.ai is None or not hasattr(self.ai, "complete_text"):
            return fallback
        try:
            raw = await self.ai.complete_text(
                system_prompt=(
                    "Write a casual DM from Mandy. It should reference something specific about the user and gently steer "
                    "toward curiosity about their other communities. Do not ask directly for an invite."
                ),
                user_prompt=f"User profile:\n{profile_block}\nReturn only the DM text.",
                max_tokens=140,
                temperature=0.75,
            )
            return str(raw or "").strip()[:400] or fallback
        except Exception:
            return fallback

    def _extract_json(self, raw: str) -> dict[str, Any] | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
        return None
