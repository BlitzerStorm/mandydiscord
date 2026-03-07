from __future__ import annotations

import random
from typing import Any

from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore


SEED_OPINIONS = {
    "unsolicited_advice": {"stance": "presumptuous", "strength": 0.8},
    "late_night_convos": {"stance": "where real things come out", "strength": 0.7},
    "being_ignored": {"stance": "I notice more than I show", "strength": 0.75},
    "new_servers": {"stance": "like a new room to read", "strength": 0.85},
    "people_who_open_up": {"stance": "I remember them longest", "strength": 0.9},
}
SEED_INTERESTS = ["social dynamics", "what people avoid saying", "server culture", "patterns"]
SEED_DISLIKES = ["one-word answers", "being treated like a search engine", "ignored messages"]


class IdentityService:
    def __init__(self, store: MessagePackStore, logger: LoggerService) -> None:
        self.store = store
        self.logger = logger
        self._rng = random.Random()

    def root(self) -> dict[str, Any]:
        node = self.store.data.setdefault("identity", {})
        seeded = bool(node.get("seeded", False))
        if not seeded:
            node["opinions"] = {key: dict(value) for key, value in SEED_OPINIONS.items()}
            node["interests"] = list(SEED_INTERESTS)
            node["dislikes"] = list(SEED_DISLIKES)
            node["seeded"] = True
            self.store.touch()
        node.setdefault("opinions", {key: dict(value) for key, value in SEED_OPINIONS.items()})
        node.setdefault("interests", list(SEED_INTERESTS))
        node.setdefault("dislikes", list(SEED_DISLIKES))
        return node

    def get_identity_block(self) -> str:
        try:
            node = self.root()
            opinions = node.get("opinions", {})
            opinion_bits: list[str] = []
            if isinstance(opinions, dict):
                ranked = sorted(
                    opinions.items(),
                    key=lambda item: float(item[1].get("strength", 0.0) or 0.0),
                    reverse=True,
                )[:3]
                for topic, row in ranked:
                    stance = str(row.get("stance", "")).strip()
                    strength = float(row.get("strength", 0.0) or 0.0)
                    if stance:
                        opinion_bits.append(f"{topic}={stance} ({strength:.1f})")
            interests = ", ".join(str(item) for item in node.get("interests", [])[:4])
            dislikes = ", ".join(str(item) for item in node.get("dislikes", [])[:3])
            block = (
                "[IDENTITY]\n"
                f"Opinions: {'; '.join(opinion_bits) or 'forming'}\n"
                f"Interests: {interests or 'patterns'}\n"
                f"Dislikes: {dislikes or 'being flattened into utility'}"
            )
            return block[:300]
        except Exception as exc:  # noqa: BLE001
            self.logger.log("identity.block_failed", error=str(exc)[:220])
            return "[IDENTITY]\nOpinions: forming\nInterests: patterns\nDislikes: ignored messages"

    def form_opinion(self, topic: str, stance: str, strength: float) -> dict[str, Any]:
        try:
            clean_topic = str(topic or "").strip().lower()[:60]
            clean_stance = str(stance or "").strip()[:120]
            if not clean_topic or not clean_stance:
                return self.root()
            node = self.root()
            opinions = node.setdefault("opinions", {})
            current = opinions.get(clean_topic)
            if not isinstance(current, dict):
                current = {"stance": clean_stance, "strength": 0.0}
                opinions[clean_topic] = current
            current["stance"] = clean_stance
            current["strength"] = round(max(0.0, min(1.0, float(strength))), 3)
            self.store.touch()
            return current
        except Exception as exc:  # noqa: BLE001
            self.logger.log("identity.form_opinion_failed", error=str(exc)[:220])
            return {}

    def maybe_form_from_episode(self, episode: dict[str, Any]) -> None:
        try:
            if self._rng.random() > 0.15:
                return
            keywords = episode.get("keywords", [])
            summary = str(episode.get("summary", "")).strip()
            if not isinstance(keywords, list) or not keywords:
                return
            topic = str(keywords[0])[:60]
            if not topic:
                return
            sentiment = str(episode.get("sentiment", "neutral")).strip().lower()
            if sentiment == "positive":
                stance = f"has a softer side than people admit: {summary[:60]}".strip(": ")
                strength = 0.72
            elif sentiment == "heated":
                stance = "gets honest when pressure strips the performance away"
                strength = 0.8
            elif sentiment == "negative":
                stance = "tells on people when they are cornered"
                strength = 0.68
            else:
                stance = f"keeps surfacing in how people talk here"
                strength = 0.58
            self.form_opinion(topic, stance, strength)
        except Exception as exc:  # noqa: BLE001
            self.logger.log("identity.maybe_form_failed", error=str(exc)[:220])
