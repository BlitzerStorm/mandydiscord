from __future__ import annotations

import json
import re
import time
from typing import Any

from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore


COMMON_WORDS = {
    "about",
    "after",
    "again",
    "aint",
    "also",
    "because",
    "been",
    "before",
    "being",
    "could",
    "dont",
    "from",
    "have",
    "here",
    "into",
    "just",
    "know",
    "like",
    "make",
    "more",
    "need",
    "only",
    "really",
    "same",
    "some",
    "that",
    "their",
    "them",
    "then",
    "they",
    "this",
    "want",
    "what",
    "when",
    "where",
    "with",
    "would",
    "your",
}
TOPIC_HINTS = {
    "gaming",
    "music",
    "drama",
    "dating",
    "sleep",
    "school",
    "work",
    "server",
    "servers",
    "friends",
    "family",
    "mods",
    "roleplay",
    "anime",
    "memes",
    "night",
    "late",
    "art",
    "coding",
    "bots",
    "patterns",
}
REGISTER_KEYWORDS = {
    "anxious": ("worried", "scared", "panic", "nervous", "spiral", "stress"),
    "expressive": ("love", "hate", "cry", "obsessed", "literally", "soooo"),
    "confident": ("obviously", "clearly", "easy", "bet", "watch", "trust"),
    "guarded": ("fine", "whatever", "sure", "idk", "maybe"),
}
ARC_TEXT = {
    "stranger": "Be slightly reserved and ask one genuine question if it fits.",
    "acquaintance": "Be warmer and lightly reference one past thing if natural.",
    "regular": "Be more direct and tease lightly.",
    "trusted": "Reference inside jokes and notice their absence.",
    "confidant": "Be the most honest version of Mandy and remember the details.",
    "rival": "Stay sharp, never back down, and enjoy the tension without real cruelty.",
    "complicated": "Keep some inconsistency in the warmth because Mandy is still working them out.",
}


class PersonaService:
    def __init__(self, store: MessagePackStore, logger: LoggerService, ai: Any | None = None) -> None:
        self.store = store
        self.logger = logger
        self.ai = ai

    def root(self) -> dict[str, Any]:
        node = self.store.data.setdefault("personas", {})
        if not isinstance(node, dict):
            self.store.data["personas"] = {}
            node = self.store.data["personas"]
        return node

    async def update_profile(self, user_id: int, message: Any) -> dict[str, Any]:
        try:
            uid = str(int(user_id))
            if uid == "0":
                return {}
            row = self._profile(uid, message=message)
            text = str(getattr(message, "clean_content", "") or "").strip()
            if not text:
                return row
            lowered = text.lower()
            tokens = re.findall(r"[a-z0-9']{2,24}", lowered)
            unique = set(tokens)
            total = len(tokens) or 1

            row["user_id"] = uid
            self._add_alias(row, str(getattr(getattr(message, "author", None), "display_name", "") or ""))
            self._add_alias(row, str(getattr(getattr(message, "author", None), "name", "") or ""))
            row["total_interactions"] = int(row.get("total_interactions", 0) or 0) + 1
            prev_avg = float(row.get("avg_message_length", 0.0) or 0.0)
            count = int(row["total_interactions"])
            row["avg_message_length"] = round(((prev_avg * max(0, count - 1)) + len(text)) / max(1, count), 3)
            long_word_hits = sum(1 for token in unique if len(token) >= 7)
            row["vocab_complexity"] = round(max(0.0, min(1.0, (len(unique) / total * 0.5) + (long_word_hits / total))), 3)
            row["communication_style"] = self._detect_style(text)
            row["emotional_register"] = self._detect_register(lowered)
            row["response_to_mandy"] = self._detect_response_to_mandy(message, lowered, row)
            row["last_seen"] = int(time.time())
            row["mandy_mirrors_them"] = True
            row["home_channel_id"] = int(getattr(getattr(message, "channel", None), "id", 0) or 0)
            row["home_guild_id"] = int(getattr(getattr(message, "guild", None), "id", 0) or 0)

            topics = list(row.get("topics_they_care_about", []))
            for topic in self._extract_topics(text):
                if topic not in topics:
                    topics.append(topic)
            row["topics_they_care_about"] = topics[:10]

            depth_delta = 0.015
            if len(text) >= 200:
                depth_delta += 0.03
            if any(word in lowered for word in ("feel", "felt", "honestly", "truth", "trust", "scared", "hurts")):
                depth_delta += 0.05
            if row["response_to_mandy"] in {"trusting", "engaged", "playful"}:
                depth_delta += 0.02
            if row["response_to_mandy"] == "dismissive":
                depth_delta -= 0.03
            self._update_depth(row, depth_delta)

            self._record_slang(row, message)
            self._update_arc(row)

            if await self._maybe_add_notable(row, text):
                self._update_depth(row, 0.03)

            self.store.touch()
            return row
        except Exception as exc:  # noqa: BLE001
            self.logger.log("persona.update_failed", user_id=user_id, error=str(exc)[:220])
            return {}

    def get_mandy_voice_for(self, user_id: int, *, guild_id: int = 0, username: str = "") -> str:
        try:
            row = self.root().get(str(int(user_id)), {})
            if not isinstance(row, dict) or not row:
                name = username or f"user-{int(user_id)}"
                return (
                    f"[USER PROFILE: @{name}]\n"
                    "Style: unknown | Vocab: mixed | Register: guarded\n"
                    "Relationship: 0.10 (engaged) | Arc: stranger | Mirror mode: ON\n"
                    "-> Start reserved, observant, and genuinely curious."
                )[:500]

            aliases = row.get("aliases", [])
            display = username or (aliases[0] if isinstance(aliases, list) and aliases else f"user-{user_id}")
            topics = ", ".join(str(item) for item in row.get("topics_they_care_about", [])[:4]) or "their own pattern"
            inside_refs = ", ".join(f'"{str(item)[:36]}"' for item in row.get("inside_references", [])[:2]) or "none"
            notable = ", ".join(f'"{str(item)[:36]}"' for item in row.get("notable_moments", [])[-2:]) or "none"
            depth = max(0.0, min(1.0, float(row.get("relationship_depth", 0.0) or 0.0)))
            response = str(row.get("response_to_mandy", "engaged"))
            arc = str(row.get("arc", "stranger"))
            vocab = "complex" if float(row.get("vocab_complexity", 0.0) or 0.0) >= 0.62 else "simple"
            style = str(row.get("communication_style", "casual"))
            register = str(row.get("emotional_register", "guarded"))
            mirror_mode = self._mirror_mode(row)
            slang_terms = ", ".join(self.allowed_slang_for_user(user_id=user_id, guild_id=guild_id)[:3]) or "none"
            directive = self._mirror_directive(row, mirror_mode)
            row["last_mirror_mode"] = mirror_mode
            arc_line = ARC_TEXT.get(arc, ARC_TEXT["stranger"])
            block = (
                f"[USER PROFILE: @{display}]\n"
                f"Style: {style} | Vocab: {vocab} | Register: {register}\n"
                f"They care about: {topics}\n"
                f"Relationship: {depth:.2f} ({response}) | Arc: {arc} | Mirror mode: ON\n"
                f"Inside refs: {inside_refs}\n"
                f"Recent notable: {notable}\n"
                f"Allowed slang: {slang_terms}\n"
                f"-> {directive} {arc_line}"
            )
            self.store.touch()
            return block[:500]
        except Exception as exc:  # noqa: BLE001
            self.logger.log("persona.voice_failed", user_id=user_id, error=str(exc)[:220])
            return "[USER PROFILE]\n-> Match their energy with warmth and variation."

    def add_inside_reference(self, user_id: int, ref: str) -> None:
        try:
            row = self._profile(str(int(user_id)))
            clean = str(ref or "").strip()[:60]
            if not clean:
                return
            refs = row.setdefault("inside_references", [])
            if clean not in refs:
                refs.append(clean)
                if len(refs) > 10:
                    del refs[: len(refs) - 10]
                self.store.touch()
        except Exception as exc:  # noqa: BLE001
            self.logger.log("persona.add_inside_ref_failed", user_id=user_id, error=str(exc)[:220])

    async def maybe_capture_inside_reference(self, user_id: int, user_text: str, reply_text: str) -> None:
        try:
            candidates = self._shared_unusual_phrases(user_text, reply_text)
            if candidates:
                self.add_inside_reference(user_id, candidates[0])
                return
            if self.ai is None or not hasattr(self.ai, "complete_text"):
                return
            prompt = (
                "Return strict JSON with key ref. If this exchange contains a unique inside reference that Mandy and "
                "this user are likely to keep reusing, set ref to a short phrase. Otherwise return {\"ref\":\"\"}."
            )
            raw = await self.ai.complete_text(
                system_prompt=prompt,
                user_prompt=f"User: {user_text[:280]}\nMandy: {reply_text[:280]}",
                max_tokens=80,
                temperature=0.25,
            )
            parsed = self._extract_json(raw or "")
            ref = str(parsed.get("ref", "")).strip() if parsed else ""
            if ref:
                self.add_inside_reference(user_id, ref)
        except Exception as exc:  # noqa: BLE001
            self.logger.log("persona.capture_inside_ref_failed", user_id=user_id, error=str(exc)[:220])

    def get_relationship_depth(self, user_id: int) -> float:
        try:
            row = self.root().get(str(int(user_id)), {})
            return max(0.0, min(1.0, float(row.get("relationship_depth", 0.0) or 0.0)))
        except Exception:
            return 0.0

    def deepen_relationship(self, user_id: int, delta: float) -> float:
        try:
            row = self._profile(str(int(user_id)))
            self._update_depth(row, delta)
            self._update_arc(row)
            self.store.touch()
            return float(row.get("relationship_depth", 0.0) or 0.0)
        except Exception as exc:  # noqa: BLE001
            self.logger.log("persona.deepen_failed", user_id=user_id, error=str(exc)[:220])
            return self.get_relationship_depth(user_id)

    def allowed_slang_for_user(self, *, user_id: int, guild_id: int) -> list[str]:
        try:
            row = self.root().get(str(int(user_id)), {})
            if not isinstance(row, dict):
                return []
            slang = row.get("absorbed_slang", {})
            server_usage = row.get("server_slang_usage", {})
            if not isinstance(slang, dict):
                slang = {}
            if not isinstance(server_usage, dict):
                server_usage = {}
            allowed: list[str] = []
            for term, count in slang.items():
                if int(count or 0) >= 3:
                    allowed.append(str(term)[:24])
            guild_terms = self.server_slang_terms(guild_id)
            for term in guild_terms:
                if term not in allowed:
                    allowed.append(term)
            return allowed[:6]
        except Exception:
            return []

    def server_slang_terms(self, guild_id: int) -> list[str]:
        try:
            gid = str(int(guild_id))
            counts: dict[str, int] = {}
            for row in self.root().values():
                if not isinstance(row, dict):
                    continue
                server_usage = row.get("server_slang_usage", {})
                if not isinstance(server_usage, dict):
                    continue
                guild_terms = server_usage.get(gid, {})
                if not isinstance(guild_terms, dict):
                    continue
                for term, count in guild_terms.items():
                    if int(count or 0) >= 5:
                        counts[str(term)] = max(counts.get(str(term), 0), int(count or 0))
            ranked = sorted(counts.items(), key=lambda item: int(item[1]), reverse=True)[:30]
            return [str(term)[:24] for term, _count in ranked]
        except Exception:
            return []

    def _profile(self, user_id: str, *, message: Any | None = None) -> dict[str, Any]:
        node = self.root()
        row = node.get(user_id)
        if not isinstance(row, dict):
            row = {
                "user_id": user_id,
                "aliases": [],
                "communication_style": "casual",
                "avg_message_length": 0.0,
                "vocab_complexity": 0.0,
                "topics_they_care_about": [],
                "emotional_register": "guarded",
                "response_to_mandy": "engaged",
                "relationship_depth": 0.08,
                "inside_references": [],
                "last_seen": 0,
                "total_interactions": 0,
                "mandy_mirrors_them": True,
                "notable_moments": [],
                "arc": "stranger",
                "absorbed_slang": {},
                "server_slang_usage": {},
                "last_mirror_mode": "",
                "testing_hits": 0,
                "trust_hits": 0,
                "mixed_signal_hits": 0,
                "home_channel_id": 0,
                "home_guild_id": 0,
            }
            node[user_id] = row
        if message is not None:
            self._add_alias(row, str(getattr(getattr(message, "author", None), "display_name", "") or ""))
        return row

    def _add_alias(self, row: dict[str, Any], alias: str) -> None:
        clean = str(alias or "").strip()[:40]
        if not clean:
            return
        aliases = row.setdefault("aliases", [])
        if clean not in aliases:
            aliases.append(clean)
            if len(aliases) > 8:
                del aliases[: len(aliases) - 8]

    def _detect_style(self, text: str) -> str:
        lowered = text.lower()
        if text.isupper() or text.count("!") >= 3:
            return "intense"
        if text == lowered and len(text) <= 80:
            return "casual"
        if any(token in lowered for token in ("lmao", "bro", "fr", "nah", "lowkey", "deadass")):
            return "chaotic"
        if re.search(r"\bregards\b|\btherefore\b|\bhowever\b", lowered):
            return "formal"
        if len(text) <= 28:
            return "dry"
        if any(token in lowered for token in ("haha", "lol", "hehe", "funny")):
            return "playful"
        return "casual"

    def _detect_register(self, lowered: str) -> str:
        best = "stoic"
        best_hits = 0
        for label, terms in REGISTER_KEYWORDS.items():
            hits = sum(1 for term in terms if term in lowered)
            if hits > best_hits:
                best = label
                best_hits = hits
        return best if best_hits > 0 else "stoic"

    def _detect_response_to_mandy(self, message: Any, lowered: str, row: dict[str, Any]) -> str:
        mentions = bool(getattr(message, "mentions", []))
        if any(term in lowered for term in ("prove", "bet", "cap", "wrong", "test you", "you cant")):
            row["testing_hits"] = int(row.get("testing_hits", 0) or 0) + 1
            return "testing"
        if any(term in lowered for term in ("thanks", "thank you", "trust", "needed that", "i know you get it")):
            row["trust_hits"] = int(row.get("trust_hits", 0) or 0) + 1
            return "trusting"
        if mentions and any(term in lowered for term in ("lol", "lmao", "bro", "nah", "shut up")):
            return "playful"
        if mentions and any(term in lowered for term in ("whatever", "ok", "k", "sure")):
            row["mixed_signal_hits"] = int(row.get("mixed_signal_hits", 0) or 0) + 1
            return "dismissive"
        return "engaged"

    def _extract_topics(self, text: str) -> list[str]:
        lowered = text.lower()
        words = re.findall(r"[a-z0-9']{3,24}", lowered)
        out: list[str] = []
        seen: set[str] = set()
        for word in words:
            if word in COMMON_WORDS:
                continue
            if word not in TOPIC_HINTS and len(word) < 5:
                continue
            if word in seen:
                continue
            seen.add(word)
            out.append(word)
        return out[:6]

    async def _maybe_add_notable(self, row: dict[str, Any], text: str) -> bool:
        if not self._is_notable_message(text):
            return False
        note = await self._summarize_notable(text)
        if not note:
            return False
        notes = row.setdefault("notable_moments", [])
        if note not in notes:
            notes.append(note)
            if len(notes) > 12:
                del notes[: len(notes) - 12]
        return True

    def _is_notable_message(self, text: str) -> bool:
        lowered = text.lower()
        if len(text) >= 220:
            return True
        if any(term in lowered for term in ("honestly", "i never", "im scared", "i am scared", "this is embarrassing", "shut up", "fight me")):
            return True
        if any(term in lowered for term in ("funniest", "crying", "dead", "lmfao")):
            return True
        return False

    async def _summarize_notable(self, text: str) -> str:
        if self.ai is None or not hasattr(self.ai, "complete_text"):
            return self._fallback_notable(text)
        try:
            raw = await self.ai.complete_text(
                system_prompt="Return strict JSON with key summary. summary must be at most 15 characters.",
                user_prompt=f"Message: {text[:600]}",
                max_tokens=40,
                temperature=0.25,
            )
            parsed = self._extract_json(raw or "")
            summary = str(parsed.get("summary", "")).strip() if parsed else ""
            return summary[:15] if summary else self._fallback_notable(text)
        except Exception:
            return self._fallback_notable(text)

    def _fallback_notable(self, text: str) -> str:
        lowered = text.lower()
        if any(term in lowered for term in ("scared", "hurt", "honestly", "truth")):
            return "opened up"
        if any(term in lowered for term in ("fight me", "wrong", "prove", "cap")):
            return "tested me"
        if any(term in lowered for term in ("lmfao", "crying", "dead")):
            return "killed the room"
        return "stood out"

    def _record_slang(self, row: dict[str, Any], message: Any) -> None:
        text = str(getattr(message, "clean_content", "") or "").lower()
        guild_id = str(int(getattr(getattr(message, "guild", None), "id", 0) or 0))
        slang = row.setdefault("absorbed_slang", {})
        if not isinstance(slang, dict):
            slang = {}
            row["absorbed_slang"] = slang
        server_usage = row.setdefault("server_slang_usage", {})
        if not isinstance(server_usage, dict):
            server_usage = {}
            row["server_slang_usage"] = server_usage
        guild_terms = server_usage.setdefault(guild_id, {})
        if not isinstance(guild_terms, dict):
            guild_terms = {}
            server_usage[guild_id] = guild_terms

        tokens = re.findall(r"[a-z0-9']{2,20}", text)
        phrases = self._phrase_candidates(tokens)
        for term in tokens + phrases:
            if term in COMMON_WORDS:
                continue
            if len(term) < 2 or len(term) > 24:
                continue
            if term.isdigit():
                continue
            if term in TOPIC_HINTS:
                continue
            slang[term] = int(slang.get(term, 0) or 0) + 1
            guild_terms[term] = int(guild_terms.get(term, 0) or 0) + 1

        if len(slang) > 40:
            ranked = sorted(slang.items(), key=lambda item: int(item[1]), reverse=True)[:30]
            row["absorbed_slang"] = {k: int(v) for k, v in ranked}
        if len(guild_terms) > 30:
            ranked = sorted(guild_terms.items(), key=lambda item: int(item[1]), reverse=True)[:30]
            server_usage[guild_id] = {k: int(v) for k, v in ranked}

    def _phrase_candidates(self, tokens: list[str]) -> list[str]:
        phrases: list[str] = []
        for size in (2, 3, 4):
            for index in range(0, max(0, len(tokens) - size + 1)):
                phrase_tokens = tokens[index : index + size]
                if any(token in COMMON_WORDS for token in phrase_tokens):
                    continue
                phrase = " ".join(phrase_tokens)
                phrases.append(phrase[:24])
        return phrases[:20]

    def _update_depth(self, row: dict[str, Any], delta: float) -> None:
        depth = float(row.get("relationship_depth", 0.08) or 0.08)
        row["relationship_depth"] = round(max(0.0, min(1.0, depth + float(delta))), 3)

    def _update_arc(self, row: dict[str, Any]) -> None:
        depth = float(row.get("relationship_depth", 0.0) or 0.0)
        testing_hits = int(row.get("testing_hits", 0) or 0)
        trust_hits = int(row.get("trust_hits", 0) or 0)
        mixed_hits = int(row.get("mixed_signal_hits", 0) or 0)
        if testing_hits >= 4 and trust_hits <= 1:
            row["arc"] = "rival"
            return
        if mixed_hits >= 2 and trust_hits >= 1:
            row["arc"] = "complicated"
            return
        if depth >= 0.8:
            row["arc"] = "confidant"
        elif depth >= 0.6:
            row["arc"] = "trusted"
        elif depth >= 0.4:
            row["arc"] = "regular"
        elif depth >= 0.2:
            row["arc"] = "acquaintance"
        else:
            row["arc"] = "stranger"

    def _mirror_mode(self, row: dict[str, Any]) -> str:
        style = str(row.get("communication_style", "casual"))
        avg_len = float(row.get("avg_message_length", 0.0) or 0.0)
        if style == "formal":
            return "formal"
        if avg_len <= 24:
            return "terse"
        if avg_len >= 140 or str(row.get("emotional_register", "")) in {"anxious", "expressive"}:
            return "deep"
        if style in {"chaotic", "playful"}:
            return "slangy"
        return "casual"

    def _mirror_directive(self, row: dict[str, Any], mirror_mode: str) -> str:
        last_mode = str(row.get("last_mirror_mode", "") or "")
        variation = " Keep the same lane but vary cadence and phrasing." if last_mode == mirror_mode else ""
        aliases = row.get("aliases", [])
        alias = aliases[0] if isinstance(aliases, list) and aliases else "them"
        if mirror_mode == "formal":
            return f"Match {alias}'s formality and tighten the register.{variation}"
        if mirror_mode == "terse":
            return f"Keep replies dramatically short and clipped with {alias}.{variation}"
        if mirror_mode == "deep":
            return f"Match the length and emotional depth, but be slightly warmer than usual.{variation}"
        if mirror_mode == "slangy":
            return f"Mirror their lowercase/slang energy and use their specific terms naturally, not constantly.{variation}"
        return f"Match their energy with subtle variation and do not sound generic.{variation}"

    def _shared_unusual_phrases(self, user_text: str, reply_text: str) -> list[str]:
        user_terms = set(self._phrase_candidates(re.findall(r"[a-z0-9']{2,20}", user_text.lower())))
        reply_terms = set(self._phrase_candidates(re.findall(r"[a-z0-9']{2,20}", reply_text.lower())))
        shared = [term for term in user_terms.intersection(reply_terms) if term and term not in COMMON_WORDS]
        return sorted(shared)[:3]

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
