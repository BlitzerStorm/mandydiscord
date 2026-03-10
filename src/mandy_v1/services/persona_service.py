from __future__ import annotations

import logging
import re
import time
from typing import Any


LOGGER = logging.getLogger("mandy.persona")
COMMON_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "before",
    "being",
    "could",
    "dont",
    "from",
    "have",
    "just",
    "like",
    "make",
    "more",
    "only",
    "really",
    "that",
    "their",
    "them",
    "they",
    "this",
    "with",
    "would",
    "your",
}
TOPIC_KEYWORDS = {
    "gaming": {"game", "gaming", "ranked", "match", "steam"},
    "music": {"music", "song", "album", "playlist", "artist"},
    "tech": {"code", "coding", "python", "bot", "api", "tech"},
    "relationships": {"dating", "relationship", "crush", "breakup", "love"},
    "community": {"server", "mod", "mods", "community", "discord"},
}


class PersonaService:
    """Tracks per-user communication patterns and relationship state."""

    def __init__(self, storage: Any, ai_service: Any | None = None) -> None:
        """Store dependencies for persona management."""
        self.storage = storage
        self.ai_service = ai_service

    def _root(self) -> dict[str, Any]:
        """Return personas map node."""
        node = self.storage.data.setdefault("personas", {})
        if not isinstance(node, dict):
            self.storage.data["personas"] = {}
            node = self.storage.data["personas"]
        return node

    def _mark_dirty(self) -> None:
        """Mark storage dirty using compatible store API."""
        if hasattr(self.storage, "mark_dirty"):
            self.storage.mark_dirty()
        elif hasattr(self.storage, "touch"):
            self.storage.touch()

    def get_profile(self, user_id: int) -> dict[str, Any]:
        """Return a user profile, creating one if it does not exist."""
        uid = str(int(user_id))
        profiles = self._root()
        row = profiles.get(uid)
        if isinstance(row, dict):
            self._apply_silence_arc(row)
            return row
        row = {
            "aliases": [],
            "communication_style": "casual",
            "avg_message_length": 0.0,
            "vocab_complexity": "simple",
            "cared_about_topics": [],
            "topics_they_care_about": [],
            "emotional_register": "dry",
            "response_to_mandy": "engaged",
            "relationship_depth": 0,
            "inside_references": [],
            "total_interactions": 0,
            "notable_moments": [],
            "arc": "new",
            "absorbed_slang": {},
            "last_updated": 0.0,
        }
        profiles[uid] = row
        self._mark_dirty()
        return row

    def update_from_message(self, user_id: int, display_name: str, content: str) -> None:
        """Update profile statistics and derived fields from a user message."""
        try:
            text = str(content or "").strip()
            if not text:
                return
            row = self.get_profile(user_id)
            aliases = row.setdefault("aliases", [])
            name = str(display_name or "").strip()[:60]
            if name and name not in aliases:
                aliases.append(name)
                if len(aliases) > 20:
                    del aliases[: len(aliases) - 20]
            interactions = int(row.get("total_interactions", 0) or 0) + 1
            row["total_interactions"] = interactions
            prev_avg = float(row.get("avg_message_length", 0.0) or 0.0)
            row["avg_message_length"] = round(((prev_avg * (interactions - 1)) + len(text)) / interactions, 3)
            row["vocab_complexity"] = self._vocab_complexity(text)
            row["communication_style"] = self._communication_style(text)
            row["emotional_register"] = self._emotional_register(text)
            row["response_to_mandy"] = self._response_to_mandy(text)
            row["last_updated"] = float(time.time())
            self._update_topics(row, text)
            self._update_slang(row, text)
            self._update_relationship_depth(row)
            self._update_arc(row)
            self._mark_dirty()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to update persona from message.")

    def capture_inside_reference(self, user_id: int, reference: str) -> None:
        """Store a shared inside reference for a user."""
        row = self.get_profile(user_id)
        refs = row.setdefault("inside_references", [])
        value = str(reference or "").strip()[:80]
        if not value or value in refs:
            return
        refs.append(value)
        if len(refs) > 20:
            del refs[: len(refs) - 20]
        self._mark_dirty()

    def add_notable_moment(self, user_id: int, moment: str) -> None:
        """Store a notable interaction moment for a user."""
        row = self.get_profile(user_id)
        moments = row.setdefault("notable_moments", [])
        value = str(moment or "").strip()[:140]
        if not value:
            return
        moments.append(value)
        if len(moments) > 10:
            del moments[: len(moments) - 10]
        self._mark_dirty()

    def voice_block(self, user_id: int) -> str:
        """Return a capped prompt block describing how to address this user."""
        row = self.get_profile(user_id)
        refs = row.get("inside_references", [])
        topics = row.get("cared_about_topics", [])
        block = (
            f"User style: {row.get('communication_style','casual')}, "
            f"{row.get('emotional_register','dry')}, arc={row.get('arc','new')}, "
            f"depth={int(row.get('relationship_depth',0) or 0)}. "
            f"Topics: {', '.join(str(x) for x in topics[:4]) or 'unknown'}. "
            f"Shared refs: {', '.join(str(x) for x in refs[:3]) or 'none'}."
        )
        return block[:500]

    def relationship_summary(self) -> dict[str, Any]:
        """Return aggregate relationship metrics across all profiles."""
        profiles = self._root()
        counts = {"new": 0, "warming": 0, "close": 0, "drifting": 0}
        total = 0
        depth_sum = 0
        for row in profiles.values():
            if not isinstance(row, dict):
                continue
            total += 1
            arc = str(row.get("arc", "new"))
            if arc not in counts:
                counts[arc] = 0
            counts[arc] += 1
            depth_sum += int(row.get("relationship_depth", 0) or 0)
        avg_depth = (depth_sum / total) if total else 0.0
        return {"counts_by_arc": counts, "avg_relationship_depth": round(avg_depth, 3), "total_profiles": total}

    async def update_profile(self, user_id: int, message: Any) -> dict[str, Any]:
        """Compatibility async alias for legacy call paths."""
        content = str(getattr(message, "clean_content", "") or "")
        display_name = str(getattr(getattr(message, "author", None), "display_name", "") or f"user-{user_id}")
        self.update_from_message(user_id, display_name, content)
        return self.get_profile(user_id)

    async def maybe_capture_inside_reference(self, user_id: int, user_text: str, reply_text: str) -> None:
        """Compatibility method to infer shared phrases from exchange text."""
        for phrase in self._extract_candidate_phrases(user_text, reply_text):
            self.capture_inside_reference(user_id, phrase)
            break

    def get_mandy_voice_for(self, user_id: int, *, guild_id: int = 0, username: str = "") -> str:
        """Compatibility alias for previous voice block method names."""
        del guild_id, username
        return self.voice_block(user_id)

    def get_relationship_depth(self, user_id: int) -> float:
        """Compatibility accessor for relationship depth score."""
        return float(self.get_profile(user_id).get("relationship_depth", 0) or 0)

    def deepen_relationship(self, user_id: int, delta: float) -> float:
        """Compatibility mutator for depth updates from chat outcomes."""
        row = self.get_profile(user_id)
        amount = max(0, int(round(float(delta) * 2)))
        row["relationship_depth"] = min(5, int(row.get("relationship_depth", 0) or 0) + amount)
        self._update_arc(row)
        self._mark_dirty()
        return float(row.get("relationship_depth", 0) or 0)

    def root(self) -> dict[str, Any]:
        """Compatibility alias returning the persona map."""
        return self._root()

    def _profile(self, user_id: int | str) -> dict[str, Any]:
        """Compatibility alias expected by legacy tests."""
        return self.get_profile(int(user_id))

    def _extract_candidate_phrases(self, user_text: str, reply_text: str) -> list[str]:
        """Extract possible shared 2-4 token phrases from user/reply overlap."""
        user_tokens = re.findall(r"[a-z0-9']{3,18}", user_text.lower())
        reply_tokens = re.findall(r"[a-z0-9']{3,18}", reply_text.lower())
        shared = set(user_tokens).intersection(reply_tokens)
        if not shared:
            return []
        return [token for token in shared if token not in COMMON_WORDS][:4]

    def _vocab_complexity(self, text: str) -> str:
        """Classify lexical complexity from average token length."""
        tokens = re.findall(r"[a-zA-Z']+", text)
        if not tokens:
            return "simple"
        avg_len = sum(len(token) for token in tokens) / len(tokens)
        if avg_len >= 6.2:
            return "rich"
        if avg_len >= 4.7:
            return "moderate"
        return "simple"

    def _communication_style(self, text: str) -> str:
        """Infer communication style from length and punctuation intensity."""
        length = len(text)
        if length <= 24:
            return "clipped"
        if length >= 220:
            return "verbose"
        if text.count("!") >= 3 or text.isupper():
            return "intense"
        return "casual"

    def _emotional_register(self, text: str) -> str:
        """Infer emotional register from lexical cues."""
        lowered = text.lower()
        if any(term in lowered for term in ("lol", "lmao", "haha", "bro", "nah")):
            return "playful"
        if any(term in lowered for term in ("honestly", "hurt", "anxious", "worried", "scared")):
            return "expressive"
        if any(term in lowered for term in ("whatever", "fine", "k.", "ok.")):
            return "dry"
        if "?" in lowered and len(lowered) < 80:
            return "curious"
        return "balanced"

    def _response_to_mandy(self, text: str) -> str:
        """Classify the user's response mode toward Mandy."""
        lowered = text.lower()
        if any(term in lowered for term in ("thanks mandy", "thank you mandy", "appreciate you")):
            return "engaged"
        if any(term in lowered for term in ("lol mandy", "mandy pls", "mandy fr")):
            return "playful"
        if any(term in lowered for term in ("whatever mandy", "shut up mandy", "nah mandy")):
            return "distant"
        return "engaged"

    def _update_topics(self, row: dict[str, Any], text: str) -> None:
        """Update cared-about topics using keyword buckets."""
        lowered = text.lower()
        topics = row.setdefault("cared_about_topics", [])
        for topic, words in TOPIC_KEYWORDS.items():
            if any(word in lowered for word in words) and topic not in topics:
                topics.append(topic)
        if len(topics) > 20:
            del topics[: len(topics) - 20]
        row["topics_they_care_about"] = list(topics[:20])

    def _update_slang(self, row: dict[str, Any], text: str) -> None:
        """Absorb repeated non-common slang-like terms."""
        counts = row.setdefault("_slang_counts", {})
        slang = row.setdefault("absorbed_slang", {})
        if not isinstance(slang, dict):
            slang = {}
            row["absorbed_slang"] = slang
        for token in re.findall(r"[a-z0-9']{2,20}", text.lower()):
            if token in COMMON_WORDS or token.isdigit():
                continue
            if re.match(r"^[a-z]{2,}$", token) and token in COMMON_WORDS:
                continue
            counts[token] = int(counts.get(token, 0) or 0) + 1
            slang[token] = int(counts[token])
        if len(slang) > 50:
            ranked = sorted(slang.items(), key=lambda item: int(item[1]), reverse=True)[:50]
            row["absorbed_slang"] = {k: int(v) for k, v in ranked}

    def _update_relationship_depth(self, row: dict[str, Any]) -> None:
        """Increase relationship depth every 25 interactions up to depth 5."""
        interactions = int(row.get("total_interactions", 0) or 0)
        computed = min(5, interactions // 25)
        row["relationship_depth"] = max(int(row.get("relationship_depth", 0) or 0), computed)

    def _apply_silence_arc(self, row: dict[str, Any]) -> None:
        """Mark relationship drifting if user has been silent 14+ days."""
        last_updated = float(row.get("last_updated", 0.0) or 0.0)
        if last_updated <= 0:
            return
        if (time.time() - last_updated) >= (14 * 24 * 60 * 60):
            row["arc"] = "drifting"

    def _update_arc(self, row: dict[str, Any]) -> None:
        """Advance arc based on interaction count and inactivity."""
        self._apply_silence_arc(row)
        if row.get("arc") == "drifting":
            return
        interactions = int(row.get("total_interactions", 0) or 0)
        if interactions >= 50:
            row["arc"] = "close"
        elif interactions >= 5:
            row["arc"] = "warming"
        else:
            row["arc"] = "new"

    def get_relationships_summary(self) -> dict[str, Any]:
        """
        Get a summary of all relationships for autonomy decision-making.
        Returns: {user_id: {"depth": 0-5, "arc": str, "last_seen_ts": int}}
        """
        summary = {}
        for uid, profile in self._root().items():
            if not isinstance(profile, dict):
                continue
            try:
                user_id = int(uid)
                summary[str(user_id)] = {
                    "depth": int(profile.get("relationship_depth", 0) or 0),
                    "arc": str(profile.get("arc", "new")),
                    "last_seen_ts": int(profile.get("last_updated", 0) or 0),
                }
            except (ValueError, TypeError):
                continue
        return summary
