from __future__ import annotations

import json
import logging
import re
import time
from typing import Any


LOGGER = logging.getLogger("mandy.culture")
TOPIC_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "before",
    "being",
    "from",
    "have",
    "just",
    "like",
    "more",
    "really",
    "that",
    "their",
    "them",
    "they",
    "this",
    "with",
}


class CultureService:
    """Tracks per-guild culture and calibrates Mandy's local persona."""

    def __init__(self, storage: Any, ai_service: Any | None = None) -> None:
        """Persist dependencies for culture analysis."""
        self.storage = storage
        self.ai_service = ai_service

    def _root(self) -> dict[str, Any]:
        """Return culture root node with schema defaults."""
        node = self.storage.data.setdefault("culture", {})
        if not isinstance(node, dict):
            self.storage.data["culture"] = {}
            node = self.storage.data["culture"]
        return node

    def _mark_dirty(self) -> None:
        """Mark storage dirty."""
        if hasattr(self.storage, "mark_dirty"):
            self.storage.mark_dirty()
        elif hasattr(self.storage, "touch"):
            self.storage.touch()

    def _profile(self, guild_id: int) -> dict[str, Any]:
        """Return culture profile for a guild, creating defaults if missing."""
        profiles = self._root()
        key = str(int(guild_id))
        row = profiles.get(key)
        if isinstance(row, dict):
            return row
        row = {
            "detected_tone": "chill",
            "dominant_topics": [],
            "activity_peaks": [],
            "humor_style": "dry",
            "formality": 0.25,
            "avg_message_length": 0.0,
            "emoji_density": 0.0,
            "lore_refs": [],
            "dominant_language_style": "mixed",
            "mandy_persona": "herself",
            "calibration_state": "uncalibrated",
            "calibration_complete": False,
            "observed_count": 0,
            "messages_observed": 0,
            "last_updated": 0.0,
            "_topic_counts": {},
            "_hour_counts": {},
        }
        profiles[key] = row
        self._mark_dirty()
        return row

    def observe_message(self, guild_id: int, message_content: str, author_name: str, hour: int) -> None:
        """Consume one message and update rolling cultural statistics."""
        try:
            row = self._profile(guild_id)
            text = str(message_content or "").strip()
            if not text:
                return
            count = int(row.get("observed_count", 0) or 0) + 1
            row["observed_count"] = count
            row["messages_observed"] = count
            row["last_updated"] = float(time.time())
            prev_len = float(row.get("avg_message_length", 0.0) or 0.0)
            row["avg_message_length"] = round(((prev_len * (count - 1)) + len(text)) / count, 4)
            emoji_hits = self._emoji_count(text)
            prev_emoji = float(row.get("emoji_density", 0.0) or 0.0)
            row["emoji_density"] = round(((prev_emoji * (count - 1)) + emoji_hits) / count, 4)
            formality = self._formality(text)
            prev_formality = float(row.get("formality", 0.0) or 0.0)
            row["formality"] = round(((prev_formality * (count - 1)) + formality) / count, 4)
            row["dominant_language_style"] = self._language_style(text)
            self._track_activity_peak(row, hour)
            self._track_topics(row, text)
            self._track_lore_ref(row, text)
            if row.get("calibration_state") == "uncalibrated":
                row["calibration_state"] = "calibrating"
            if count >= 50 and row.get("calibration_state") != "calibrated":
                row["calibration_state"] = "calibrating"
            self._mark_dirty()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to observe culture message.")

    async def observe(self, guild: Any, message: Any) -> None:
        """Compatibility async observer for legacy bot call paths."""
        try:
            guild_id = int(getattr(guild, "id", 0) or 0)
            if guild_id <= 0:
                return
            content = str(getattr(message, "clean_content", "") or "")
            author_name = str(getattr(getattr(message, "author", None), "display_name", "") or "unknown")
            created_at = getattr(message, "created_at", None)
            hour = int(getattr(created_at, "hour", 0) if created_at is not None else 0)
            self.observe_message(guild_id, content, author_name, hour)
            if int(self._profile(guild_id).get("observed_count", 0) or 0) >= 50:
                await self.calibrate(guild_id, self.ai_service)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed compatibility culture observe.")

    async def calibrate(self, guild_id: int, ai_service: Any | None = None) -> None:
        """Calibrate server tone/persona using observed stats and optional AI."""
        try:
            row = self._profile(guild_id)
            if int(row.get("observed_count", 0) or 0) < 50:
                row["calibration_state"] = "calibrating"
                self._mark_dirty()
                return
            stats_summary = {
                "observed_count": int(row.get("observed_count", 0) or 0),
                "formality": float(row.get("formality", 0.0) or 0.0),
                "emoji_density": float(row.get("emoji_density", 0.0) or 0.0),
                "avg_message_length": float(row.get("avg_message_length", 0.0) or 0.0),
                "dominant_language_style": str(row.get("dominant_language_style", "mixed")),
                "dominant_topics": list(row.get("dominant_topics", []))[:6],
                "activity_peaks": list(row.get("activity_peaks", []))[:6],
            }
            client = ai_service or self.ai_service
            if client is not None and hasattr(client, "complete_text"):
                await self._calibrate_with_ai(row, stats_summary, client)
            else:
                self._calibrate_fallback(row)
            row["calibration_state"] = "calibrated"
            row["calibration_complete"] = True
            row["last_updated"] = float(time.time())
            self._mark_dirty()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to calibrate culture.")
            row = self._profile(guild_id)
            self._calibrate_fallback(row)
            row["calibration_state"] = "calibrated"
            row["calibration_complete"] = True
            self._mark_dirty()

    async def _calibrate_with_ai(self, row: dict[str, Any], stats_summary: dict[str, Any], ai_service: Any) -> None:
        """Use AI to derive tone/humor/topics/persona from compact stats."""
        prompt = (
            "You are calibrating Discord server culture. Return strict JSON with keys detected_tone, humor_style, "
            "dominant_topics, mandy_persona. Keep dominant_topics as max 5 short strings."
        )
        raw = await ai_service.complete_text(
            system_prompt=prompt,
            user_prompt=json.dumps(stats_summary, ensure_ascii=True),
            max_tokens=220,
            temperature=0.35,
        )
        parsed = self._extract_json_object(str(raw or ""))
        if not parsed:
            self._calibrate_fallback(row)
            return
        row["detected_tone"] = str(parsed.get("detected_tone", row.get("detected_tone", "chill")))[:40]
        row["humor_style"] = str(parsed.get("humor_style", row.get("humor_style", "dry")))[:40]
        topics = parsed.get("dominant_topics", row.get("dominant_topics", []))
        if isinstance(topics, list):
            row["dominant_topics"] = [str(item)[:40] for item in topics if str(item).strip()][:5]
        row["mandy_persona"] = str(parsed.get("mandy_persona", row.get("mandy_persona", "herself")))[:60]

    def _calibrate_fallback(self, row: dict[str, Any]) -> None:
        """Fallback calibration when AI output is unavailable."""
        formality = float(row.get("formality", 0.0) or 0.0)
        emoji = float(row.get("emoji_density", 0.0) or 0.0)
        if formality >= 0.65:
            row["detected_tone"] = "professional"
            row["humor_style"] = "dry"
            row["mandy_persona"] = "measured facilitator"
        elif emoji >= 0.5:
            row["detected_tone"] = "chill"
            row["humor_style"] = "absurd"
            row["mandy_persona"] = "playful regular"
        elif formality <= 0.2 and emoji <= 0.2:
            row["detected_tone"] = "chaotic"
            row["humor_style"] = "edgy"
            row["mandy_persona"] = "chaotic observer"
        else:
            row["detected_tone"] = "chill"
            row["humor_style"] = "wholesome"
            row["mandy_persona"] = "curious participant"

    def culture_block(self, guild_id: int) -> str:
        """Return capped culture block for prompt injection."""
        row = self._profile(guild_id)
        text = (
            f"Server vibe: {row.get('detected_tone','chill')}. "
            f"Humor: {row.get('humor_style','dry')}. "
            f"Mandy's role here: {row.get('mandy_persona','herself')}."
        )
        return text[:400]

    def get_server_voice(self, guild_id: int) -> str:
        """Compatibility alias for prompt assembly."""
        row = self._profile(guild_id)
        topics = ", ".join(str(x) for x in row.get("dominant_topics", [])[:5]) or "unknown"
        lore = ", ".join(str(x) for x in row.get("lore_refs", [])[:3]) or "none"
        block = (
            f"[SERVER CULTURE: {guild_id}]\n"
            f"Tone: {row.get('detected_tone','chill')} | Humor: {row.get('humor_style','dry')} | Formality: {float(row.get('formality', 0.0) or 0.0):.2f}\n"
            f"Dominant topics: {topics}\n"
            f"Lore refs: {lore}\n"
            f"Mandy's role here: {row.get('mandy_persona','herself')}"
        )
        return block[:400]

    def add_lore_ref(self, guild_id: int, ref: str) -> None:
        """Add a recurring lore/reference phrase to a guild profile."""
        row = self._profile(guild_id)
        lore = row.setdefault("lore_refs", [])
        value = str(ref or "").strip()[:80]
        if not value or value in lore:
            return
        lore.append(value)
        if len(lore) > 30:
            del lore[: len(lore) - 30]
        self._mark_dirty()

    def get_mandy_persona(self, guild_id: int) -> str:
        """Return Mandy's calibrated persona for a guild."""
        row = self._profile(guild_id)
        if str(row.get("calibration_state", "")) != "calibrated":
            return "herself"
        return str(row.get("mandy_persona", "herself"))

    def _emoji_count(self, text: str) -> int:
        """Count unicode and custom emoji-like patterns in message text."""
        return len(re.findall(r"[\U0001F300-\U0001FAFF]|:[a-z0-9_]{2,20}:", text))

    def _formality(self, text: str) -> float:
        """Compute rough formality score for one message."""
        lowered = text.lower()
        score = 0.2
        if any(term in lowered for term in ("please", "regarding", "therefore", "however", "appreciate")):
            score += 0.4
        if text[:1].isupper():
            score += 0.1
        if any(term in lowered for term in ("lol", "lmao", "bro", "wtf", "nah")):
            score -= 0.35
        return max(0.0, min(1.0, score))

    def _language_style(self, text: str) -> str:
        """Return broad language style for the message."""
        if text.isupper() and len(text) >= 8:
            return "all-caps"
        if text.lower() == text and len(text) >= 5:
            return "lowercase"
        return "mixed"

    def _track_activity_peak(self, row: dict[str, Any], hour: int) -> None:
        """Track top activity hours for the guild."""
        hour_counts = row.setdefault("_hour_counts", {})
        key = str(max(0, min(23, int(hour))))
        hour_counts[key] = int(hour_counts.get(key, 0) or 0) + 1
        ranked = sorted(hour_counts.items(), key=lambda item: int(item[1]), reverse=True)[:6]
        row["activity_peaks"] = [int(h) for h, _count in ranked]

    def _track_topics(self, row: dict[str, Any], text: str) -> None:
        """Track recurring topic tokens while filtering common words."""
        topic_counts = row.setdefault("_topic_counts", {})
        for token in re.findall(r"[a-z0-9']{3,20}", text.lower()):
            if token in TOPIC_STOPWORDS:
                continue
            topic_counts[token] = int(topic_counts.get(token, 0) or 0) + 1
        ranked = sorted(topic_counts.items(), key=lambda item: int(item[1]), reverse=True)[:5]
        row["dominant_topics"] = [topic for topic, _count in ranked]

    def _track_lore_ref(self, row: dict[str, Any], text: str) -> None:
        """Capture quoted snippets or recurring incident references as lore."""
        refs = row.setdefault("lore_refs", [])
        for quoted in re.findall(r"\"([^\"]{4,40})\"", text):
            clean = quoted.strip()
            if clean and clean not in refs:
                refs.append(clean[:80])
        lowered = text.lower()
        if "incident" in lowered or "that one time" in lowered:
            candidate = lowered[:60]
            if candidate not in refs:
                refs.append(candidate)
        if len(refs) > 30:
            del refs[: len(refs) - 30]

    def _extract_json_object(self, raw: str) -> dict[str, Any] | None:
        """Extract JSON object from AI completion text."""
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

    def root(self) -> dict[str, Any]:
        """Compatibility alias returning guild culture map."""
        return self._root()

    def get_server_readiness(self, guild_id: int) -> dict[str, Any]:
        """
        Get calibration/readiness of a server for autonomous actions.
        Returns: {"calibrated": bool, "tone": str, "active": bool}
        """
        culture = self.get_culture(guild_id)
        return {
            "calibrated": bool(culture.get("calibrated")),
            "tone": str(culture.get("tone", "unknown")),
            "active": bool(culture.get("total_observations", 0) >= 10),
            "observation_count": int(culture.get("total_observations", 0) or 0),
        }
