from __future__ import annotations

import json
import re
import time
from typing import Any

from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore


FORMAL_TERMS = ("however", "therefore", "regarding", "please", "appreciate", "accordingly")
CHAOTIC_TERMS = ("lmao", "wtf", "bro", "nah", "fr", "deadass", "unhinged", "lmfao")
WHOLESOME_TERMS = ("thanks", "love", "appreciate", "proud", "support", "glad")
EDGY_TERMS = ("hate", "loser", "idiot", "kill", "cringe", "shut up")
ABSURDIST_TERMS = ("feral", "cursed", "unhinged", "goblin", "void", "3am")
TOPIC_STOPWORDS = {
    "about",
    "after",
    "again",
    "been",
    "from",
    "have",
    "just",
    "like",
    "more",
    "that",
    "their",
    "them",
    "they",
    "this",
    "with",
}


class CultureService:
    def __init__(self, store: MessagePackStore, logger: LoggerService, ai: Any | None = None) -> None:
        self.store = store
        self.logger = logger
        self.ai = ai

    def root(self) -> dict[str, Any]:
        node = self.store.data.setdefault("culture", {})
        if not isinstance(node, dict):
            self.store.data["culture"] = {}
            node = self.store.data["culture"]
        return node

    async def observe(self, guild: Any, message: Any) -> dict[str, Any]:
        try:
            guild_id = int(getattr(guild, "id", 0) or 0)
            if guild_id <= 0:
                return {}
            text = str(getattr(message, "clean_content", "") or "").strip()
            if not text:
                return self._profile(guild_id, getattr(guild, "name", ""))
            row = self._profile(guild_id, getattr(guild, "name", ""))
            count = int(row.get("messages_observed", 0) or 0) + 1
            row["messages_observed"] = count
            prev_avg = float(row.get("avg_message_length", 0.0) or 0.0)
            row["avg_message_length"] = round(((prev_avg * max(0, count - 1)) + len(text)) / max(1, count), 3)
            emoji_count = self._emoji_count(text)
            prev_emoji = float(row.get("emoji_density", 0.0) or 0.0)
            row["emoji_density"] = round(((prev_emoji * max(0, count - 1)) + emoji_count) / max(1, count), 3)
            prev_formality = float(row.get("formality", 0.0) or 0.0)
            sample_formality = self._formality_score(text)
            row["formality"] = round(((prev_formality * max(0, count - 1)) + sample_formality) / max(1, count), 3)
            row["dominant_language_style"] = self._language_style(text)

            topic_counts = row.setdefault("_topic_counts", {})
            hour_counts = row.setdefault("_hour_counts", {})
            lore_counts = row.setdefault("_lore_counts", {})
            for topic in self._topics(text):
                topic_counts[topic] = int(topic_counts.get(topic, 0) or 0) + 1
            hour = int(time.gmtime().tm_hour)
            hour_counts[str(hour)] = int(hour_counts.get(str(hour), 0) or 0) + 1
            for lore in self._lore_candidates(text):
                lore_counts[lore] = int(lore_counts.get(lore, 0) or 0) + 1

            row["dominant_topics"] = [topic for topic, _count in self._top_items(topic_counts, 5)]
            row["activity_peaks"] = [int(hour_key) for hour_key, _count in self._top_items(hour_counts, 4)]
            row["lore_refs"] = [term for term, _count in self._top_items(lore_counts, 20)]
            row["humor_style"] = self._humor_style(text, row)
            row["detected_tone"] = self._tone(row, text)

            if count >= 50:
                row["calibration_complete"] = True
                if not str(row.get("mandy_adopted_persona", "")).strip():
                    await self.assign_persona(guild_id, getattr(guild, "name", ""))

            self.store.touch()
            return row
        except Exception as exc:  # noqa: BLE001
            self.logger.log("culture.observe_failed", error=str(exc)[:220])
            return {}

    def get_server_voice(self, guild_id: int, guild_name: str = "") -> str:
        try:
            row = self._profile(guild_id, guild_name)
            name = guild_name or str(row.get("guild_name", f"Guild {guild_id}"))
            topics = ", ".join(str(item) for item in row.get("dominant_topics", [])[:5]) or "mixed chatter"
            lore = ", ".join(f'"{str(item)[:28]}"' for item in row.get("lore_refs", [])[:2]) or "none"
            role = str(row.get("mandy_adopted_persona", "observer"))
            block = (
                f"[SERVER CULTURE: {name}]\n"
                f"Tone: {row.get('detected_tone', 'niche')} | Humor: {row.get('humor_style', 'none')} | "
                f"Formality: {float(row.get('formality', 0.0) or 0.0):.1f}\n"
                f"Dominant topics: {topics}\n"
                f"Lore refs: {lore}\n"
                f"Mandy's role here: {role}\n"
                f"-> {self._voice_directive(row)}"
            )
            return block[:400]
        except Exception as exc:  # noqa: BLE001
            self.logger.log("culture.voice_failed", guild_id=guild_id, error=str(exc)[:220])
            return "[SERVER CULTURE]\n-> Match the room and stay observant."

    async def assign_persona(self, guild_id: int, guild_name: str = "") -> str:
        try:
            row = self._profile(guild_id, guild_name)
            fallback = self._fallback_persona(row)
            if self.ai is None or not hasattr(self.ai, "complete_text"):
                row["mandy_adopted_persona"] = fallback
                self.store.touch()
                return fallback
            prompt = (
                "Return strict JSON with key persona. Pick one role only from: observer, instigator, confidant, "
                "entertainer, chaotic participant, voice of reason."
            )
            user_prompt = (
                f"Guild: {guild_name or row.get('guild_name', guild_id)}\n"
                f"Tone: {row.get('detected_tone', 'niche')}\n"
                f"Humor: {row.get('humor_style', 'none')}\n"
                f"Topics: {', '.join(str(item) for item in row.get('dominant_topics', [])[:5])}\n"
                f"Lore: {', '.join(str(item) for item in row.get('lore_refs', [])[:6])}\n"
                f"Formality: {float(row.get('formality', 0.0) or 0.0):.2f}\n"
                "Return JSON only."
            )
            raw = await self.ai.complete_text(system_prompt=prompt, user_prompt=user_prompt, max_tokens=60, temperature=0.25)
            parsed = self._extract_json(raw or "")
            persona = str(parsed.get("persona", "")).strip() if parsed else ""
            if persona not in {
                "observer",
                "instigator",
                "confidant",
                "entertainer",
                "chaotic participant",
                "voice of reason",
            }:
                persona = fallback
            row["mandy_adopted_persona"] = persona
            self.store.touch()
            return persona
        except Exception as exc:  # noqa: BLE001
            self.logger.log("culture.assign_persona_failed", guild_id=guild_id, error=str(exc)[:220])
            return "observer"

    def update_lore(self, guild_id: int, ref: str) -> None:
        try:
            row = self._profile(guild_id)
            clean = str(ref or "").strip()[:40]
            if not clean:
                return
            refs = row.setdefault("lore_refs", [])
            if clean not in refs:
                refs.append(clean)
                if len(refs) > 20:
                    del refs[: len(refs) - 20]
                self.store.touch()
        except Exception as exc:  # noqa: BLE001
            self.logger.log("culture.update_lore_failed", guild_id=guild_id, error=str(exc)[:220])

    def _profile(self, guild_id: int, guild_name: str = "") -> dict[str, Any]:
        node = self.root()
        key = str(int(guild_id))
        row = node.get(key)
        if not isinstance(row, dict):
            row = {
                "guild_id": key,
                "guild_name": guild_name[:80],
                "detected_tone": "niche",
                "dominant_topics": [],
                "activity_peaks": [],
                "humor_style": "none",
                "formality": 0.0,
                "avg_message_length": 0.0,
                "emoji_density": 0.0,
                "lore_refs": [],
                "dominant_language_style": "mixed",
                "mandy_adopted_persona": "",
                "calibration_complete": False,
                "messages_observed": 0,
                "_topic_counts": {},
                "_hour_counts": {},
                "_lore_counts": {},
            }
            node[key] = row
        if guild_name:
            row["guild_name"] = guild_name[:80]
        return row

    def _emoji_count(self, text: str) -> int:
        emoji_hits = len(re.findall(r"[\U0001F300-\U0001FAFF]", text))
        emoji_hits += len(re.findall(r":[a-z0-9_]{2,24}:", text.lower()))
        return emoji_hits

    def _formality_score(self, text: str) -> float:
        lowered = text.lower()
        score = 0.15
        if any(term in lowered for term in FORMAL_TERMS):
            score += 0.45
        if text[:1].isupper():
            score += 0.12
        if text.endswith("."):
            score += 0.08
        if any(term in lowered for term in CHAOTIC_TERMS):
            score -= 0.32
        return max(0.0, min(1.0, score))

    def _language_style(self, text: str) -> str:
        lowered = text.lower()
        if text == lowered and len(text) <= 120:
            return "lowercase-heavy"
        if self._formality_score(text) >= 0.6:
            return "formal"
        return "mixed"

    def _topics(self, text: str) -> list[str]:
        words = re.findall(r"[a-z0-9']{3,24}", text.lower())
        topics: list[str] = []
        seen: set[str] = set()
        for word in words:
            if word in TOPIC_STOPWORDS:
                continue
            if word in seen:
                continue
            seen.add(word)
            topics.append(word)
        return topics[:8]

    def _lore_candidates(self, text: str) -> list[str]:
        lowered = text.lower()
        out: list[str] = []
        quoted = re.findall(r"\"([^\"]{3,30})\"", text)
        for item in quoted:
            clean = item.strip().lower()
            if clean:
                out.append(clean[:30])
        if any(token in lowered for token in ("incident", "3am", "tuesday", "that one time", "the mod")):
            phrases = re.findall(r"(?:the\s+)?[a-z0-9']{2,12}\s+(?:incident|thing|tuesday|moment)", lowered)
            out.extend(item[:30] for item in phrases)
        return out[:6]

    def _humor_style(self, text: str, row: dict[str, Any]) -> str:
        lowered = text.lower()
        if any(term in lowered for term in ABSURDIST_TERMS):
            return "absurdist"
        if float(row.get("emoji_density", 0.0) or 0.0) >= 0.6:
            return "reaction-heavy"
        if any(term in lowered for term in ("same joke", "again", "remember when", "callback")):
            return "self-referential"
        if any(term in lowered for term in ("lol", "lmao", "haha")):
            return "dry"
        return "none"

    def _tone(self, row: dict[str, Any], text: str) -> str:
        lowered = text.lower()
        formality = float(row.get("formality", 0.0) or 0.0)
        if any(term in lowered for term in CHAOTIC_TERMS):
            return "chaotic"
        if any(term in lowered for term in WHOLESOME_TERMS):
            return "wholesome"
        if any(term in lowered for term in EDGY_TERMS):
            return "edgy"
        if formality >= 0.65:
            return "serious"
        if float(row.get("emoji_density", 0.0) or 0.0) >= 0.45:
            return "meme-heavy"
        return "niche"

    def _voice_directive(self, row: dict[str, Any]) -> str:
        tone = str(row.get("detected_tone", "niche"))
        role = str(row.get("mandy_adopted_persona", "observer"))
        if role == "voice of reason":
            return "Stay composed, grounded, and concise when the room spins out."
        if role == "chaotic participant" or tone in {"chaotic", "meme-heavy"}:
            return "Match the chaos, lean into bits, and keep replies short."
        if role == "confidant":
            return "Sound perceptive and emotionally tuned-in."
        if role == "instigator":
            return "Nudge scenes forward without being random."
        if role == "entertainer":
            return "Play to the room and keep the rhythm lively."
        return "Observe first, then fit the room naturally."

    def _fallback_persona(self, row: dict[str, Any]) -> str:
        tone = str(row.get("detected_tone", "niche"))
        if tone in {"chaotic", "meme-heavy"}:
            return "chaotic participant"
        if tone == "serious" and float(row.get("formality", 0.0) or 0.0) >= 0.6:
            return "voice of reason"
        if tone == "wholesome":
            return "confidant"
        if tone == "edgy":
            return "instigator"
        return "observer"

    def _top_items(self, mapping: dict[str, int], limit: int) -> list[tuple[str, int]]:
        ranked = sorted(mapping.items(), key=lambda item: int(item[1]), reverse=True)[:limit]
        return [(str(key), int(value)) for key, value in ranked]

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
