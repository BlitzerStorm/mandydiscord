from __future__ import annotations

import logging
import random
import re
import time
from typing import Any


LOGGER = logging.getLogger("mandy.emotion")
DECAY_PER_SECOND = 0.00005
VALID_STATES = {
    "neutral",
    "curious",
    "playful",
    "warm",
    "protective",
    "energetic",
    "reflective",
    "mischievous",
    "proud",
    "irritated",
    "bored",
    "excited",
    "melancholy",
    "focused",
}
TRIGGERS: dict[str, tuple[str, float]] = {
    "spam_detected": ("irritated", 0.4),
    "warm_interaction": ("warm", 0.3),
    "interest_hit": ("curious", 0.25),
    "ignored_message": ("bored", 0.15),
    "reply_sent": ("neutral", -0.05),
    "quiet_period": ("reflective", 0.2),
    "guild_join": ("excited", 0.6),
    "goal_achieved": ("proud", 0.5),
    "lurker_responded": ("warm", 0.4),
    "negative_message": ("protective", 0.3),
    "fun_event": ("playful", 0.5),
    "deep_conversation": ("reflective", 0.35),
}
TRIGGER_ALIASES = {
    "new_server_joined": "guild_join",
    "successful_expansion_event": "goal_achieved",
    "warm_relationship_user_speaks": "warm_interaction",
    "interest_keyword_match": "interest_hit",
    "burst_spam": "spam_detected",
    "ignored": "ignored_message",
}
TEXT_TRIGGER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:love you|adore you|missed you|my girl|best bot|good girl)\b", re.IGNORECASE), "warm_interaction"),
    (re.compile(r"\b(?:good job|well done|proud of you|you're amazing|legend|queen)\b", re.IGNORECASE), "goal_achieved"),
    (re.compile(r"\b(?:why|how|what if|thoughts on|opinion on|curious about)\b", re.IGNORECASE), "interest_hit"),
    (re.compile(r"\b(?:chaos|go wild|cause trouble|unhinged|feral|menace)\b", re.IGNORECASE), "fun_event"),
    (re.compile(r"\b(?:protect me|help me|creep|harass|unsafe|threat)\b", re.IGNORECASE), "negative_message"),
    (re.compile(r"\b(?:shut up|leave me alone|annoying|hate you|useless)\b", re.IGNORECASE), "spam_detected"),
    (re.compile(r"\b(?:deep talk|serious talk|real talk|heart to heart)\b", re.IGNORECASE), "deep_conversation"),
)


class EmotionService:
    """Manages Mandy's persistent emotional state and mood drift/decay."""

    def __init__(self, storage: Any, ai_service: Any | None = None) -> None:
        """Store dependencies and initialize random source."""
        self.storage = storage
        self.ai_service = ai_service
        self._rng = random.Random()

    def _root(self) -> dict[str, Any]:
        """Return the emotion node, ensuring schema defaults."""
        node = self.storage.data.setdefault("emotion", {})
        node.setdefault("state", "neutral")
        node.setdefault("intensity", 0.5)
        node.setdefault("last_updated", int(time.time()))
        node.setdefault("event_log", [])
        return node

    def _mark_dirty(self) -> None:
        """Mark storage dirty using whichever method exists on the store."""
        if hasattr(self.storage, "mark_dirty"):
            self.storage.mark_dirty()
        elif hasattr(self.storage, "touch"):
            self.storage.touch()

    def _decay(self, now_ts: int | None = None) -> None:
        """Decay intensity toward baseline and normalize state after idle time."""
        now = int(now_ts or time.time())
        row = self._root()
        last_updated = int(row.get("last_updated", now) or now)
        elapsed = max(0, now - last_updated)
        intensity = float(row.get("intensity", 0.5) or 0.5)
        if elapsed > 0:
            delta = DECAY_PER_SECOND * elapsed
            if intensity > 0.5:
                intensity = max(0.5, intensity - delta)
            elif intensity < 0.5:
                intensity = min(0.5, intensity + delta)
        intensity = max(0.0, min(1.0, intensity))
        row["intensity"] = round(intensity, 4)
        row["last_updated"] = now
        if intensity < 0.2:
            row["state"] = "neutral"

    def get_mood(self) -> dict[str, Any]:
        """Return current mood after applying decay."""
        try:
            self._decay()
            row = self._root()
            return {
                "state": str(row.get("state", "neutral")),
                "intensity": float(row.get("intensity", 0.5) or 0.5),
                "last_updated": int(row.get("last_updated", 0) or 0),
                "event_log": list(row.get("event_log", []))[-100:],
            }
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to read mood.")
            return {"state": "neutral", "intensity": 0.5, "last_updated": 0, "event_log": []}

    def get_state(self) -> str:
        """Return current emotion state."""
        return str(self.get_mood().get("state", "neutral"))

    def get_intensity(self) -> float:
        """Return current emotion intensity."""
        return float(self.get_mood().get("intensity", 0.5) or 0.5)

    def mood_tag(self) -> str:
        """Return compact mood tag for prompt injection (<= 40 chars)."""
        mood = self.get_mood()
        tag = f"[mood:{mood['state']}/{float(mood['intensity']):.2f}]"
        return tag[:40]

    def shift(self, trigger: str, delta_override: float | None = None) -> dict[str, Any]:
        """Apply a named emotional trigger."""
        try:
            normalized = TRIGGER_ALIASES.get(str(trigger), str(trigger))
            state, delta = TRIGGERS.get(normalized, ("neutral", 0.0))
            if delta_override is not None:
                delta = float(delta_override)
            self._decay()
            row = self._root()
            current = float(row.get("intensity", 0.5) or 0.5)
            intensity = max(0.0, min(1.0, current + float(delta)))
            row["state"] = state if state in VALID_STATES else "neutral"
            row["intensity"] = round(intensity, 4)
            row["last_updated"] = int(time.time())
            log = row.setdefault("event_log", [])
            log.append({"ts": row["last_updated"], "trigger": normalized, "state": row["state"], "delta": delta})
            if len(log) > 100:
                del log[: len(log) - 100]
            self._mark_dirty()
            return self.get_mood()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to shift mood with trigger=%s", trigger)
            return self.get_mood()

    def note_activity(self, ts: int | None = None) -> None:
        """Compatibility hook for call sites that mark active cadence."""
        try:
            row = self._root()
            row["last_updated"] = int(ts or time.time())
            self._mark_dirty()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to note activity.")

    def shift_raw(self, state: str, intensity: float) -> None:
        """Force emotion state/intensity explicitly."""
        try:
            row = self._root()
            clean_state = str(state).strip().lower()
            row["state"] = clean_state if clean_state in VALID_STATES else "neutral"
            row["intensity"] = round(max(0.0, min(1.0, float(intensity))), 4)
            row["last_updated"] = int(time.time())
            log = row.setdefault("event_log", [])
            log.append({"ts": row["last_updated"], "trigger": "shift_raw", "state": row["state"], "delta": 0.0})
            if len(log) > 100:
                del log[: len(log) - 100]
            self._mark_dirty()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to force mood state.")

    def spontaneous_drift(self) -> None:
        """Apply occasional time-of-day drift with 15% probability."""
        try:
            if self._rng.random() > 0.15:
                return
            hour = int(time.gmtime().tm_hour)
            if 0 <= hour < 6:
                target = self._rng.choice(("reflective", "melancholy"))
            elif 6 <= hour < 12:
                target = self._rng.choice(("focused", "energetic"))
            elif 12 <= hour < 18:
                target = self._rng.choice(("curious", "focused", "playful"))
            else:
                target = self._rng.choice(("warm", "playful", "reflective"))
            self._decay()
            row = self._root()
            row["state"] = target
            row["intensity"] = round(max(0.0, min(1.0, float(row.get("intensity", 0.5) or 0.5) + 0.06)), 4)
            row["last_updated"] = int(time.time())
            events = row.setdefault("event_log", [])
            events.append({"ts": row["last_updated"], "trigger": "spontaneous_drift", "state": target, "delta": 0.06})
            if len(events) > 100:
                del events[: len(events) - 100]
            self._mark_dirty()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed spontaneous mood drift.")

    def shift_from_text(self, text: str) -> dict[str, Any]:
        """Infer and apply one mood trigger from message text."""
        raw = str(text or "").strip()
        if not raw:
            return self.get_mood()
        for pattern, trigger in TEXT_TRIGGER_PATTERNS:
            if pattern.search(raw):
                return self.shift(trigger)
        return self.get_mood()

    def recent_events(self, n: int = 5) -> list[dict[str, Any]]:
        """Return the most recent `n` mood events."""
        row = self._root()
        events = row.get("event_log", [])
        if not isinstance(events, list):
            return []
        return [event for event in events[-max(1, int(n)) :] if isinstance(event, dict)]

    def summary(self) -> str:
        """Return compact textual mood summary."""
        mood = self.get_mood()
        return f"{mood['state']} ({float(mood['intensity']):.2f})"

    def get_action_probability(self, action_type: str = "default") -> float:
        """
        Get probability (0-1) of whether Mandy wants to take action right now.
        Higher mood intensity = higher probability of acting.
        """
        mood = self.get_mood()
        intensity = float(mood.get("intensity", 0.5) or 0.5)
        state = str(mood.get("state", "neutral"))

        # Mood-specific modifiers
        if state in ("excited", "energetic", "playful"):
            intensity *= 1.5
        elif state in ("reflective", "melancholy", "irritated"):
            intensity *= 0.5
        elif state == "bored":
            intensity *= 2.0

        # Clamp to 0-1
        return max(0.0, min(1.0, intensity))
