from __future__ import annotations

import time
from typing import Any

from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore


NEUTRAL_INTENSITY = 0.5
DECAY_PER_MINUTE = 0.05
STATE_BY_TRIGGER = {
    "burst_spam": "irritated",
    "quiet": "bored",
    "warm_relationship_user_speaks": "warm",
    "interest_keyword_match": "curious",
    "successful_expansion_event": "mischievous",
    "reply_sent": "neutral",
    "ignored": "bored",
    "new_server_joined": "curious",
}


class EmotionService:
    def __init__(self, store: MessagePackStore, logger: LoggerService) -> None:
        self.store = store
        self.logger = logger

    def root(self) -> dict[str, Any]:
        node = self.store.data.setdefault("emotion", {})
        node.setdefault("state", "neutral")
        node.setdefault("intensity", NEUTRAL_INTENSITY)
        node.setdefault("last_updated", 0)
        node.setdefault("event_log", [])
        node.setdefault("last_activity", 0)
        node.setdefault("last_quiet_triggered", 0)
        return node

    def note_activity(self, *, ts: int | None = None) -> None:
        try:
            row = self.root()
            row["last_activity"] = int(ts or time.time())
            self.store.touch()
        except Exception as exc:  # noqa: BLE001
            self.logger.log("emotion.note_activity_failed", error=str(exc)[:220])

    def get_mood(self) -> dict[str, Any]:
        try:
            row = self.root()
            now = int(time.time())
            changed = self._apply_decay(row, now)
            last_activity = int(row.get("last_activity", 0) or 0)
            last_quiet = int(row.get("last_quiet_triggered", 0) or 0)
            if last_activity > 0 and (now - last_activity) >= (30 * 60) and (now - last_quiet) >= (30 * 60):
                self._shift_unlocked(row, "quiet", 0.2, now=now)
                row["last_quiet_triggered"] = now
                changed = True
            if changed:
                self.store.touch()
            return {
                "state": str(row.get("state", "neutral")),
                "intensity": round(float(row.get("intensity", NEUTRAL_INTENSITY) or NEUTRAL_INTENSITY), 3),
                "last_updated": int(row.get("last_updated", 0) or 0),
                "event_log": list(row.get("event_log", []))[-20:],
            }
        except Exception as exc:  # noqa: BLE001
            self.logger.log("emotion.get_mood_failed", error=str(exc)[:220])
            return {
                "state": "neutral",
                "intensity": NEUTRAL_INTENSITY,
                "last_updated": 0,
                "event_log": [],
            }

    def shift(self, trigger: str, delta: float) -> dict[str, Any]:
        try:
            row = self.root()
            now = int(time.time())
            self._apply_decay(row, now)
            self._shift_unlocked(row, trigger, delta, now=now)
            row["last_activity"] = now
            self.store.touch()
            return {
                "state": str(row.get("state", "neutral")),
                "intensity": round(float(row.get("intensity", NEUTRAL_INTENSITY) or NEUTRAL_INTENSITY), 3),
                "last_updated": int(row.get("last_updated", 0) or 0),
                "event_log": list(row.get("event_log", []))[-20:],
            }
        except Exception as exc:  # noqa: BLE001
            self.logger.log("emotion.shift_failed", trigger=str(trigger)[:60], error=str(exc)[:220])
            return self.get_mood()

    def mood_tag(self) -> str:
        try:
            mood = self.get_mood()
            return f"[MOOD: {mood['state']}/{float(mood['intensity']):.1f}]"
        except Exception as exc:  # noqa: BLE001
            self.logger.log("emotion.mood_tag_failed", error=str(exc)[:220])
            return "[MOOD: neutral/0.5]"

    def _apply_decay(self, row: dict[str, Any], now: int) -> bool:
        try:
            last_updated = int(row.get("last_updated", 0) or 0)
            if last_updated <= 0 or now <= last_updated:
                row["last_updated"] = now
                return False
            elapsed_minutes = (now - last_updated) / 60.0
            decay = max(0.0, elapsed_minutes * DECAY_PER_MINUTE)
            current = float(row.get("intensity", NEUTRAL_INTENSITY) or NEUTRAL_INTENSITY)
            distance = current - NEUTRAL_INTENSITY
            if abs(distance) <= decay:
                next_intensity = NEUTRAL_INTENSITY
            elif distance > 0:
                next_intensity = current - decay
            else:
                next_intensity = current + decay
            changed = abs(next_intensity - current) >= 0.001
            row["intensity"] = round(max(0.0, min(1.0, next_intensity)), 3)
            if abs(float(row["intensity"]) - NEUTRAL_INTENSITY) <= 0.03:
                row["state"] = "neutral"
            row["last_updated"] = now
            return changed
        except Exception:
            row["last_updated"] = now
            return False

    def _shift_unlocked(self, row: dict[str, Any], trigger: str, delta: float, *, now: int) -> None:
        current = float(row.get("intensity", NEUTRAL_INTENSITY) or NEUTRAL_INTENSITY)
        next_intensity = max(0.0, min(1.0, current + float(delta)))
        state = str(row.get("state", "neutral") or "neutral")
        target_state = STATE_BY_TRIGGER.get(trigger, state)

        if trigger == "new_server_joined":
            state = "playful" if next_intensity >= 0.8 else "curious"
        elif trigger == "reply_sent" and next_intensity <= 0.55:
            state = "neutral"
        elif trigger == "ignored" and next_intensity >= 0.55:
            state = "bored"
        elif delta > 0 and next_intensity >= 0.55:
            state = target_state
        elif abs(next_intensity - NEUTRAL_INTENSITY) <= 0.05:
            state = "neutral"

        if state == "curious" and next_intensity >= 0.85:
            state = "playful"

        row["state"] = state
        row["intensity"] = round(next_intensity, 3)
        row["last_updated"] = now
        events = row.setdefault("event_log", [])
        if not isinstance(events, list):
            events = []
            row["event_log"] = events
        events.append({"ts": now, "trigger": str(trigger)[:80], "delta": round(float(delta), 3)})
        if len(events) > 20:
            del events[: len(events) - 20]
