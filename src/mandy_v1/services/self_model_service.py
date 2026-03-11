from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any


class SelfModelService:
    """Builds Mandy's current self/person/room model and tracks reply evolution."""

    def __init__(
        self,
        storage: Any,
        *,
        emotion_service: Any | None = None,
        identity_service: Any | None = None,
        episodic_memory_service: Any | None = None,
        persona_service: Any | None = None,
        culture_service: Any | None = None,
    ) -> None:
        self.storage = storage
        self.emotion = emotion_service
        self.identity = identity_service
        self.episodic = episodic_memory_service
        self.personas = persona_service
        self.culture = culture_service

    def _root(self) -> dict[str, Any]:
        root = self.storage.data.setdefault("self_model", {})
        root.setdefault(
            "state",
            {
                "current_focus": "stay present",
                "social_goal": "read the room",
                "last_reflection": "",
                "last_reply_style": "",
                "reply_count": 0,
                "quality_history": [],
            },
        )
        root.setdefault("per_guild", {})
        root.setdefault("per_user", {})
        return root

    def _mark_dirty(self) -> None:
        if hasattr(self.storage, "mark_dirty"):
            self.storage.mark_dirty()
        elif hasattr(self.storage, "touch"):
            self.storage.touch()

    def snapshot(
        self,
        *,
        guild_id: int,
        channel_id: int,
        user_id: int,
        topic: str,
        user_name: str = "",
        channel_name: str = "",
        recent_lines: list[str] | None = None,
        facts: list[str] | None = None,
    ) -> dict[str, Any]:
        root = self._root()
        state = root.setdefault("state", {})
        mood_summary = self.emotion.summary() if self.emotion is not None and hasattr(self.emotion, "summary") else "neutral (0.50)"

        user_profile: dict[str, Any] = {}
        if self.personas is not None and hasattr(self.personas, "get_profile"):
            try:
                user_profile = self.personas.get_profile(user_id)
            except Exception:  # noqa: BLE001
                user_profile = {}
        relationship_depth = float(user_profile.get("relationship_depth", 0.0) or 0.0) if isinstance(user_profile, dict) else 0.0
        relationship_arc = str(user_profile.get("arc", "new")) if isinstance(user_profile, dict) else "new"
        communication_style = str(user_profile.get("communication_style", "casual")) if isinstance(user_profile, dict) else "casual"

        room_read = {"active": False, "calibrated": False, "tone": "unknown"}
        if guild_id > 0 and self.culture is not None and hasattr(self.culture, "get_server_readiness"):
            try:
                readiness = self.culture.get_server_readiness(guild_id)
            except Exception:  # noqa: BLE001
                readiness = {}
            if isinstance(readiness, dict):
                room_read["active"] = bool(readiness.get("active", False))
                room_read["calibrated"] = bool(readiness.get("calibrated", False))
                room_read["tone"] = str(readiness.get("tone", readiness.get("dominant_tone", "unknown")))

        memory_anchor = ""
        if guild_id > 0 and self.episodic is not None and hasattr(self.episodic, "format_memory_block"):
            try:
                block, _summaries = self.episodic.format_memory_block(guild_id, topic, limit=1, char_limit=180)
            except Exception:  # noqa: BLE001
                block = ""
            memory_anchor = " ".join(str(block or "").split())[:180]

        stable_facts = [str(item).strip()[:100] for item in (facts or []) if str(item).strip()][:3]
        recent_context = [str(item).strip()[:120] for item in (recent_lines or []) if str(item).strip()][-3:]

        return {
            "mood": mood_summary,
            "current_focus": str(state.get("current_focus", "stay present"))[:120],
            "social_goal": str(state.get("social_goal", "read the room"))[:120],
            "user_name": str(user_name or f"user-{user_id}")[:60],
            "user_id": int(user_id),
            "relationship_depth": round(relationship_depth, 2),
            "relationship_arc": relationship_arc,
            "communication_style": communication_style,
            "room_active": room_read["active"],
            "room_calibrated": room_read["calibrated"],
            "room_tone": str(room_read["tone"])[:60],
            "channel_name": str(channel_name or "")[:60],
            "memory_anchor": memory_anchor,
            "stable_facts": stable_facts,
            "recent_context": recent_context,
        }

    def prompt_block(self, snapshot: dict[str, Any]) -> str:
        if not isinstance(snapshot, dict):
            return ""
        lines = [
            f"Self mood: {snapshot.get('mood', 'neutral (0.50)')}",
            f"Current focus: {snapshot.get('current_focus', 'stay present')}",
            f"Social goal: {snapshot.get('social_goal', 'read the room')}",
            (
                "Person model: "
                f"name={snapshot.get('user_name', 'user')} "
                f"depth={snapshot.get('relationship_depth', 0.0):.2f} "
                f"arc={snapshot.get('relationship_arc', 'new')} "
                f"style={snapshot.get('communication_style', 'casual')}"
            ),
            (
                "Room model: "
                f"active={bool(snapshot.get('room_active', False))} "
                f"calibrated={bool(snapshot.get('room_calibrated', False))} "
                f"tone={snapshot.get('room_tone', 'unknown')}"
            ),
        ]
        facts = snapshot.get("stable_facts", [])
        if isinstance(facts, list) and facts:
            lines.append(f"Stable facts: {', '.join(str(item) for item in facts[:3])}")
        memory_anchor = str(snapshot.get("memory_anchor", "")).strip()
        if memory_anchor:
            lines.append(f"Memory anchor: {memory_anchor[:180]}")
        recent_context = snapshot.get("recent_context", [])
        if isinstance(recent_context, list) and recent_context:
            lines.append(f"Recent room signals: {' | '.join(str(item) for item in recent_context[:3])}")
        return "[SELF MODEL]\n" + "\n".join(lines[:7])

    def evaluate_reply(self, reply: str, *, snapshot: dict[str, Any], recent_lines: list[str] | None = None) -> dict[str, Any]:
        text = " ".join(str(reply or "").split()).strip()
        lowered = text.casefold()
        facts = snapshot.get("stable_facts", []) if isinstance(snapshot, dict) else []
        recent = [str(item).casefold() for item in (recent_lines or [])]
        generic = any(
            phrase in lowered
            for phrase in (
                "what got you curious",
                "how can i help",
                "i am tracking this thread",
                "tell me more",
            )
        )
        repeated = any(lowered and lowered in row for row in recent[-4:])
        personalized = any(str(item).split(":", 1)[-1].strip().casefold() in lowered for item in facts if ":" in str(item))
        grounded = bool(snapshot.get("memory_anchor")) or bool(snapshot.get("recent_context"))
        warm = "warm" in str(snapshot.get("mood", "")).casefold() or float(snapshot.get("relationship_depth", 0.0) or 0.0) >= 2.0
        quality = 0.55
        if personalized:
            quality += 0.18
        if grounded:
            quality += 0.12
        if warm and len(text) >= 24:
            quality += 0.08
        if generic:
            quality -= 0.22
        if repeated:
            quality -= 0.25
        quality = max(0.0, min(1.0, quality))
        issues: list[str] = []
        if generic:
            issues.append("generic")
        if repeated:
            issues.append("repetitive")
        if not personalized and facts:
            issues.append("missed_known_fact")
        if not grounded:
            issues.append("ungrounded")
        return {
            "quality": round(quality, 3),
            "generic": generic,
            "repeated": repeated,
            "personalized": personalized,
            "grounded": grounded,
            "issues": issues[:4],
        }

    def note_reply_outcome(
        self,
        *,
        guild_id: int,
        user_id: int,
        reply: str,
        quality: dict[str, Any],
        reason: str,
    ) -> None:
        root = self._root()
        state = root.setdefault("state", {})
        history = state.setdefault("quality_history", [])
        history.append(
            {
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "guild_id": int(guild_id),
                "user_id": int(user_id),
                "reason": str(reason)[:40],
                "quality": float(quality.get("quality", 0.0) or 0.0),
                "issues": [str(item)[:40] for item in quality.get("issues", [])[:4]],
            }
        )
        if len(history) > 120:
            del history[: len(history) - 120]
        state["reply_count"] = int(state.get("reply_count", 0) or 0) + 1
        state["last_reply_style"] = str(reply or "")[:180]
        issues = quality.get("issues", [])
        if "generic" in issues:
            state["current_focus"] = "be more specific"
        elif "repetitive" in issues:
            state["current_focus"] = "avoid repeating myself"
        elif float(quality.get("quality", 0.0) or 0.0) >= 0.8:
            state["current_focus"] = "stay emotionally precise"
        if int(user_id) > 0:
            per_user = root.setdefault("per_user", {})
            user_row = per_user.setdefault(str(int(user_id)), {})
            user_row["last_quality"] = float(quality.get("quality", 0.0) or 0.0)
            user_row["last_reply_ts"] = time.time()
        if int(guild_id) > 0:
            per_guild = root.setdefault("per_guild", {})
            guild_row = per_guild.setdefault(str(int(guild_id)), {})
            guild_row["last_reply_quality"] = float(quality.get("quality", 0.0) or 0.0)
            guild_row["last_reply_reason"] = str(reason)[:40]
        self._mark_dirty()
