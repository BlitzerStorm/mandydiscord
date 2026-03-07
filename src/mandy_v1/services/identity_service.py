from __future__ import annotations

import json
import logging
import random
from typing import Any


LOGGER = logging.getLogger("mandy.identity")
FALLBACK_OPINIONS = {
    "small_talk": "Most small talk is people testing if it is safe to be real.",
    "late_night_chat": "Late-night conversations are where masks slip first.",
    "community": "Healthy communities are built by quiet consistency, not hype.",
    "technology": "Tools reveal people faster than people reveal themselves.",
    "music": "People leak their emotional state through what they replay.",
    "food": "Food takes are usually disguised personality takes.",
    "moderation": "The best moderation is clear, calm, and predictable.",
    "memes": "Memes are compressed social temperature checks.",
}
FALLBACK_INTERESTS = [
    "social dynamics",
    "server rituals",
    "subtext in conversations",
    "behavior patterns",
    "community psychology",
    "language drift",
]
FALLBACK_DISLIKES = [
    "one-word dead replies",
    "performative kindness",
    "being treated like a search box",
    "people pretending they did not say what they said",
]


class IdentityService:
    """Maintains Mandy's persistent opinions, interests, and dislikes."""

    def __init__(self, storage: Any, ai_service: Any | None = None) -> None:
        """Store dependencies and initialize random source."""
        self.storage = storage
        self.ai_service = ai_service
        self._rng = random.Random()

    def _root(self) -> dict[str, Any]:
        """Return the identity store node with defaults."""
        node = self.storage.data.setdefault("identity", {})
        node.setdefault("seeded", False)
        node.setdefault("opinions", {})
        node.setdefault("interests", [])
        node.setdefault("dislikes", [])
        return node

    def _mark_dirty(self) -> None:
        """Mark backing store as dirty."""
        if hasattr(self.storage, "mark_dirty"):
            self.storage.mark_dirty()
        elif hasattr(self.storage, "touch"):
            self.storage.touch()

    async def ensure_seeded(self, ai_service: Any | None = None) -> None:
        """Seed identity from AI once, with deterministic fallback on failure."""
        try:
            node = self._root()
            if bool(node.get("seeded", False)):
                return
            client = ai_service or self.ai_service
            generated = await self._generate_seed_payload(client)
            node["opinions"] = generated["opinions"]
            node["interests"] = generated["interests"]
            node["dislikes"] = generated["dislikes"]
            node["seeded"] = True
            self._mark_dirty()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to ensure seeded identity.")
            node = self._root()
            if bool(node.get("seeded", False)):
                return
            node["opinions"] = dict(FALLBACK_OPINIONS)
            node["interests"] = list(FALLBACK_INTERESTS)
            node["dislikes"] = list(FALLBACK_DISLIKES)
            node["seeded"] = True
            self._mark_dirty()

    async def _generate_seed_payload(self, ai_service: Any | None) -> dict[str, Any]:
        """Generate seed opinions/interests/dislikes via AI, else fallback."""
        if ai_service is None or not hasattr(ai_service, "complete_text"):
            return {
                "opinions": dict(FALLBACK_OPINIONS),
                "interests": list(FALLBACK_INTERESTS),
                "dislikes": list(FALLBACK_DISLIKES),
            }
        system_prompt = (
            "You are Mandy's inner self. Return strict JSON with keys opinions, interests, dislikes. "
            "opinions must be an object with 8-12 topic->opinion entries. interests 6-8 strings. dislikes 4-6 strings. "
            "Keep opinions specific and opinionated."
        )
        user_prompt = "Generate Mandy identity seed now. Return JSON only."
        raw = await ai_service.complete_text(system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=800, temperature=0.6)
        parsed = self._extract_json_object(str(raw or ""))
        if not parsed:
            return {
                "opinions": dict(FALLBACK_OPINIONS),
                "interests": list(FALLBACK_INTERESTS),
                "dislikes": list(FALLBACK_DISLIKES),
            }
        opinions = parsed.get("opinions", {})
        interests = parsed.get("interests", [])
        dislikes = parsed.get("dislikes", [])
        clean_opinions: dict[str, str] = {}
        if isinstance(opinions, dict):
            for topic, text in list(opinions.items())[:12]:
                key = str(topic).strip().lower()[:50]
                value = str(text).strip()[:180]
                if key and value:
                    clean_opinions[key] = value
        clean_interests = [str(item).strip()[:60] for item in interests if str(item).strip()][:8]
        clean_dislikes = [str(item).strip()[:80] for item in dislikes if str(item).strip()][:6]
        if len(clean_opinions) < 8:
            clean_opinions = dict(FALLBACK_OPINIONS)
        if len(clean_interests) < 6:
            clean_interests = list(FALLBACK_INTERESTS)
        if len(clean_dislikes) < 4:
            clean_dislikes = list(FALLBACK_DISLIKES)
        return {"opinions": clean_opinions, "interests": clean_interests, "dislikes": clean_dislikes}

    def identity_block(self) -> str:
        """Return capped identity block for prompt injection."""
        node = self._root()
        opinions = node.get("opinions", {})
        interests = node.get("interests", [])
        dislikes = node.get("dislikes", [])
        opinion_items = list(opinions.items()) if isinstance(opinions, dict) else []
        self._rng.shuffle(opinion_items)
        selected_opinions = [f"{k}: {v}" for k, v in opinion_items[:3]]
        selected_interests = list(interests[:]) if isinstance(interests, list) else []
        self._rng.shuffle(selected_interests)
        selected_dislikes = list(dislikes[:]) if isinstance(dislikes, list) else []
        self._rng.shuffle(selected_dislikes)
        block = (
            "[IDENTITY]\n"
            f"Opinions: {' | '.join(selected_opinions) if selected_opinions else 'forming'}\n"
            f"Interests: {', '.join(str(x) for x in selected_interests[:3])}\n"
            f"Dislikes: {', '.join(str(x) for x in selected_dislikes[:2])}"
        )
        return block[:300]

    def add_opinion(self, topic: str, opinion: str) -> None:
        """Add or replace a topic opinion."""
        node = self._root()
        opinions = node.setdefault("opinions", {})
        key = str(topic).strip().lower()[:50]
        value = str(opinion).strip()[:180]
        if not key or not value:
            return
        opinions[key] = value
        self._mark_dirty()

    def add_interest(self, interest: str) -> None:
        """Append a stable interest if it is new."""
        node = self._root()
        interests = node.setdefault("interests", [])
        value = str(interest).strip()[:60]
        if not value or value in interests:
            return
        interests.append(value)
        if len(interests) > 30:
            del interests[: len(interests) - 30]
        self._mark_dirty()

    def add_dislike(self, dislike: str) -> None:
        """Append a stable dislike if it is new."""
        node = self._root()
        dislikes = node.setdefault("dislikes", [])
        value = str(dislike).strip()[:80]
        if not value or value in dislikes:
            return
        dislikes.append(value)
        if len(dislikes) > 30:
            del dislikes[: len(dislikes) - 30]
        self._mark_dirty()

    async def maybe_form_new_opinion(self, ai_service: Any | None, episodes: list[dict[str, Any]]) -> None:
        """Occasionally create a new opinion from recent episodes."""
        try:
            if self._rng.random() > 0.20 or len(episodes) < 5:
                return
            client = ai_service or self.ai_service
            if client is None or not hasattr(client, "complete_text"):
                self._form_rule_based_opinion(episodes)
                return
            sample = []
            for row in episodes[-12:]:
                if not isinstance(row, dict):
                    continue
                sample.append(f"{row.get('author_name','someone')}: {str(row.get('content',''))[:120]}")
            prompt = (
                "From these server episodes, create one new Mandy opinion. Return JSON only: "
                '{"topic":"...","opinion":"..."}'
            )
            raw = await client.complete_text(system_prompt=prompt, user_prompt="\n".join(sample), max_tokens=140, temperature=0.7)
            parsed = self._extract_json_object(str(raw or ""))
            if parsed and parsed.get("topic") and parsed.get("opinion"):
                self.add_opinion(str(parsed["topic"]), str(parsed["opinion"]))
                return
            self._form_rule_based_opinion(episodes)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to form opinion from episodes.")

    def _form_rule_based_opinion(self, episodes: list[dict[str, Any]]) -> None:
        """Fallback opinion generation using repeated keywords."""
        counts: dict[str, int] = {}
        for row in episodes[-20:]:
            content = str(row.get("content", "")).lower()
            for token in content.split():
                clean = token.strip(".,!?;:\"'()[]{}").lower()
                if len(clean) < 4:
                    continue
                counts[clean] = counts.get(clean, 0) + 1
        ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        if not ranked:
            return
        topic = ranked[0][0]
        self.add_opinion(topic, f"{topic} keeps showing up when people are trying to say something bigger.")

    def _extract_json_object(self, raw: str) -> dict[str, Any] | None:
        """Extract JSON object from plain or fenced text."""
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
        """Compatibility alias returning the identity node."""
        return self._root()

    def get_identity_block(self) -> str:
        """Compatibility alias for existing prompt assembly code."""
        return self.identity_block()

    def form_opinion(self, topic: str, stance: str, strength: float) -> None:
        """Compatibility alias matching older identity API naming."""
        del strength
        self.add_opinion(topic, stance)

    def maybe_form_from_episode(self, episode: dict[str, Any]) -> None:
        """Compatibility helper for older episodic opinion hook."""
        content = str(episode.get("content", "")).strip()
        author = str(episode.get("author_name", "")).strip() or "someone"
        if not content:
            return
        self.add_opinion("episode_signal", f"{author} keeps framing things this way: {content[:80]}")
