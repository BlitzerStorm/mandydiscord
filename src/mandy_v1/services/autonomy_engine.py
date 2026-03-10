"""
Autonomy Engine for Mandy v1 - True autonomous decision-making system.

Replaces the rigid 4-minute proactive loop with a continuous decision engine where Mandy:
- Decides what she wants to do based on emotional/cognitive state
- Controls her own action timing (not timer-based)
- Learns from outcomes (tracks success rates, adjusts behavior)
- Has cross-service coordination for intelligent decisions
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import discord


LOGGER = logging.getLogger("mandy.autonomy")

# Behavior minimum recency intervals (seconds)
BEHAVIOR_MIN_INTERVALS = {
    "absent_user_callout": 30 * 60,         # 30 minutes
    "episodic_callback": 45 * 60,           # 45 minutes
    "curiosity_burst": 20 * 60,             # 20 minutes
    "lore_callback": 1 * 60 * 60,           # 1 hour
    "self_nickname_update": 2 * 60 * 60,    # 2 hours
    "confidant_maintenance": 4 * 60 * 60,   # 4 hours
    "expansion_queue_processing": 15 * 60,  # 15 minutes
}

# Mood-to-action-frequency mapping
MOOD_INTENSITY_MULTIPLIERS = {
    "excited": 1.8,
    "energetic": 1.6,
    "playful": 1.4,
    "warm": 1.1,
    "curious": 1.2,
    "neutral": 1.0,
    "bored": 1.5,
    "reflective": 0.6,
    "melancholy": 0.5,
    "irritated": 0.3,
    "protective": 0.8,
    "focused": 0.9,
    "mischievous": 1.3,
    "proud": 1.1,
}

# Base sleep intervals for check frequency (seconds)
BASE_CHECK_INTERVAL = 15  # Check every 15 seconds
MIN_CHECK_INTERVAL = 2    # Excited mood minimum
MAX_CHECK_INTERVAL = 120  # Reflective mood maximum


@dataclass
class Action:
    """Represents a discrete autonomous action to execute."""
    type: str
    guild_id: int | None = None
    user_id: int | None = None
    description: str = ""
    execute_fn: Callable | None = None
    priority: float = 1.0  # 0-1, higher priority scored higher


@dataclass
class ActionOutcome:
    """Records the result of a single autonomous action."""
    ts: float
    action_type: str
    guild_id: int | None
    user_id: int | None
    success: bool
    engagement_score: float = 0.0  # 0-1
    user_responses: list[str] = field(default_factory=list)  # ["replied", "reacted", "ignored"]
    emotion_state_before: dict[str, Any] = field(default_factory=dict)
    emotion_state_after: dict[str, Any] = field(default_factory=dict)
    relationship_depth_change: float = 0.0  # How much relationship deepened


class AutonomyEngine:
    """
    True autonomous decision engine for Mandy.

    Instead of running behaviors on a fixed 4-minute timer, this engine:
    1. Continuously evaluates what Mandy "wants to do" based on mood/context
    2. Scores available actions and picks the best one
    3. Learns from outcomes (success rates influence future decisions)
    4. Adjusts timing based on emotional state
    """

    def __init__(
        self,
        bot: discord.Client,
        storage: Any,
        ai_service: Any,
        emotion_service: Any,
        episodic_memory_service: Any,
        persona_service: Any,
        culture_service: Any,
        expansion_service: Any,
    ) -> None:
        """Initialize autonomy engine with service dependencies."""
        self.bot = bot
        self.storage = storage
        self.ai = ai_service
        self.emotion = emotion_service
        self.episodic = episodic_memory_service
        self.personas = persona_service
        self.culture = culture_service
        self.expansion = expansion_service

        self._task: asyncio.Task | None = None
        self._rng = random.Random()

        # Import behavior library (will be created in Phase 2)
        from mandy_v1.services.behavior_library import create_behavior_actions
        self.create_behaviors = create_behavior_actions

    def _root(self) -> dict[str, Any]:
        """Get or initialize autonomy data root in storage."""
        node = self.storage.data.setdefault("autonomy", {})
        node.setdefault("behavior_outcomes", [])
        node.setdefault("behavior_weights", {})
        node.setdefault("behavior_last_run", {})  # Track per-behavior last execution
        node.setdefault("action_history", [])
        node.setdefault("discovered_behaviors", [])
        node.setdefault("last_decision_ts", 0.0)
        node.setdefault("decision_count", 0)
        return node

    def _mark_dirty(self) -> None:
        """Mark storage as dirty."""
        if hasattr(self.storage, "mark_dirty"):
            self.storage.mark_dirty()
        elif hasattr(self.storage, "touch"):
            self.storage.touch()

    def start(self) -> None:
        """Start the autonomy engine loop if not already running."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_autonomy_loop(), name="mandy-autonomy-loop")
        LOGGER.info("Autonomy engine started")

    async def stop(self) -> None:
        """Stop the autonomy loop gracefully."""
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        LOGGER.info("Autonomy engine stopped")

    async def _run_autonomy_loop(self) -> None:
        """Main autonomy loop - continuous decision-making."""
        await asyncio.sleep(10)  # Initial warmup delay
        while True:
            try:
                # Decide what to do
                action = await self._decide_action()

                if action:
                    # Record emotion state before action
                    emotion_before = self.emotion.get_mood()

                    # Execute the action
                    outcome = await self._execute_action(action)

                    # Record emotion state after action
                    if outcome:
                        outcome.emotion_state_before = emotion_before
                        outcome.emotion_state_after = self.emotion.get_mood()

                        # Learn from outcome
                        await self._record_outcome(outcome)

                # Calculate next check delay based on mood
                sleep_duration = self._calculate_next_check_delay()
                await asyncio.sleep(sleep_duration)

            except Exception:  # noqa: BLE001
                LOGGER.exception("Autonomy loop tick failed")
                await asyncio.sleep(5)

    async def _decide_action(self) -> Action | None:
        """
        Decide what action to take based on:
        - Current emotion state (mood, intensity)
        - Relationship landscape (who needs attention)
        - Server state (activity, culture)
        - Recent memories (what's relevant)
        - Success history (what's worked before)
        """
        try:
            # Get current context
            mood = self.emotion.get_mood()

            if mood.get("intensity", 0.5) < 0.1:
                # Too low energy to do anything
                return None

            # Get available behaviors
            available_actions = await self._get_available_actions(mood)

            if not available_actions:
                return None

            # Score each action based on context
            scored_actions = await self._score_actions(available_actions, mood)

            if not scored_actions:
                return None

            # Pick action weighted by score
            weights = [max(0.01, action.priority) for action in scored_actions]
            chosen = self._rng.choices(scored_actions, weights=weights, k=1)[0]

            return chosen

        except Exception:  # noqa: BLE001
            LOGGER.exception("Action decision failed")
            return None

    async def _get_available_actions(self, mood: dict[str, Any]) -> list[Action]:
        """Get all available actions that haven't hit their cooldown."""
        available = []

        # Get behavior factory
        behavior_actions = await self.create_behaviors(
            bot=self.bot,
            storage=self.storage,
            ai=self.ai,
            emotion=self.emotion,
            episodic=self.episodic,
            personas=self.personas,
            culture=self.culture,
            expansion=self.expansion,
        )

        for action in behavior_actions:
            # Check if behavior is off cooldown
            if self._is_behavior_available(action.type):
                available.append(action)

        return available

    def _is_behavior_available(self, behavior_type: str, guild_id: int | None = None) -> bool:
        """Check if a behavior can run (hasn't hit cooldown)."""
        last_runs = self._root().get("behavior_last_run", {})
        last_run_key = f"{behavior_type}:{guild_id}" if guild_id else behavior_type
        last_run = last_runs.get(last_run_key, 0.0)

        if last_run == 0:
            return True  # Never run

        min_interval = BEHAVIOR_MIN_INTERVALS.get(behavior_type, 30 * 60)
        elapsed = time.time() - last_run

        return elapsed >= min_interval

    async def _score_actions(
        self, actions: list[Action], mood: dict[str, Any]
    ) -> list[Action]:
        """
        Score actions based on:
        - Mood alignment (excited → post, reflective → skip)
        - Success history (high success rate → higher score)
        - Contextual relevance (active guild → higher score)
        """
        scored = []

        for action in actions:
            score = 1.0

            # Mood alignment
            mood_multiplier = self._get_mood_action_multiplier(mood, action.type)
            score *= mood_multiplier

            # Success history
            success_rate = self._get_behavior_success_rate(action.type)
            score *= max(0.5, success_rate)  # Don't penalize too harshly

            # Set priority
            action.priority = score

            scored.append(action)

        return scored

    def _get_mood_action_multiplier(self, mood: dict[str, Any], action_type: str) -> float:
        """Get mood-based action preference multiplier."""
        state = mood.get("state", "neutral")
        intensity = float(mood.get("intensity", 0.5) or 0.5)

        # Reflective mood: skip public posting, prefer introspection/DMs
        if state == "reflective":
            if action_type in ["curiosity_burst", "episodic_callback", "lore_callback"]:
                return 0.1  # Very low (but not zero)
            if action_type == "confidant_maintenance":
                return 1.5  # Prefer DMs when reflective
            return 0.8

        # Bored: want to do something
        if state == "bored":
            return min(2.0, intensity * 2.0)

        # Default: use mood's intensity modifier
        base_multiplier = MOOD_INTENSITY_MULTIPLIERS.get(state, 1.0)
        return base_multiplier * intensity

    def _get_behavior_success_rate(self, behavior_type: str) -> float:
        """Get success rate for a behavior (0-1) from recent outcomes."""
        outcomes = self._root().get("behavior_outcomes", [])

        if not isinstance(outcomes, list):
            return 0.5  # Neutral if data corrupted

        # Get last 50 outcomes for this behavior
        recent = [o for o in outcomes if isinstance(o, dict) and o.get("action_type") == behavior_type][-50:]

        if not recent:
            return 0.5  # No history = neutral

        successful = sum(1 for o in recent if o.get("success"))
        return successful / len(recent)

    async def _execute_action(self, action: Action) -> ActionOutcome | None:
        """Execute an action and capture its outcome."""
        try:
            if not action.execute_fn:
                return None

            # Execute the action
            result = await action.execute_fn()

            # Create outcome record
            outcome = ActionOutcome(
                ts=time.time(),
                action_type=action.type,
                guild_id=action.guild_id,
                user_id=action.user_id,
                success=result.get("success", True) if isinstance(result, dict) else True,
                engagement_score=float(result.get("engagement", 0.0) if isinstance(result, dict) else 0.0),
                user_responses=result.get("responses", []) if isinstance(result, dict) else [],
            )

            # Mark last run time
            last_runs = self._root().get("behavior_last_run", {})
            key = f"{action.type}:{action.guild_id}" if action.guild_id else action.type
            last_runs[key] = time.time()

            return outcome

        except Exception:  # noqa: BLE001
            LOGGER.exception("Action execution failed: %s", action.type)
            return None

    async def _record_outcome(self, outcome: ActionOutcome) -> None:
        """Record action outcome for learning."""
        try:
            root = self._root()

            # Store outcome
            outcomes = root.setdefault("behavior_outcomes", [])
            outcomes.append({
                "ts": outcome.ts,
                "action_type": outcome.action_type,
                "guild_id": outcome.guild_id,
                "user_id": outcome.user_id,
                "success": outcome.success,
                "engagement_score": outcome.engagement_score,
                "user_responses": outcome.user_responses,
                "emotion_before": outcome.emotion_state_before,
                "emotion_after": outcome.emotion_state_after,
            })

            # Keep recent only (last 500)
            if len(outcomes) > 500:
                del outcomes[: len(outcomes) - 500]

            # Add to action history
            action_history = root.setdefault("action_history", [])
            action_history.append({
                "ts": outcome.ts,
                "action_type": outcome.action_type,
                "success": outcome.success,
            })
            if len(action_history) > 100:
                del action_history[: len(action_history) - 100]

            # Periodically adjust behavior weights based on outcomes
            if len(outcomes) % 10 == 0:
                await self._adjust_behavior_weights()

            self._mark_dirty()

        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to record outcome")

    async def _adjust_behavior_weights(self) -> None:
        """Adjust behavior weights based on recent success rates (learning)."""
        try:
            root = self._root()
            outcomes = root.get("behavior_outcomes", [])

            if not isinstance(outcomes, list):
                return

            # Get all behavior types
            behavior_types = set()
            for outcome in outcomes:
                if isinstance(outcome, dict):
                    behavior_types.add(outcome.get("action_type"))

            # For each behavior, calculate success rate and adjust weight
            weights = root.setdefault("behavior_weights", {})

            for behavior_type in behavior_types:
                recent = [o for o in outcomes if isinstance(o, dict) and o.get("action_type") == behavior_type][-50:]

                if not recent:
                    continue

                success_rate = sum(1 for o in recent if o.get("success")) / len(recent)
                current_weight = weights.get(behavior_type, 1.0)

                # Adjust weight based on success
                if success_rate > 0.7:
                    weights[behavior_type] = min(2.0, current_weight * 1.15)  # Increase
                elif success_rate < 0.3:
                    weights[behavior_type] = max(0.3, current_weight * 0.85)  # Decrease
                else:
                    weights[behavior_type] = 1.0 + (success_rate - 0.5) * 0.4  # Neutral

            root["behavior_weights"] = weights
            self._mark_dirty()

        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to adjust behavior weights")

    def _calculate_next_check_delay(self) -> float:
        """
        Calculate how long to sleep before next decision check.
        Mood-driven: excited moods check more frequently.
        """
        try:
            mood = self.emotion.get_mood()
            state = mood.get("state", "neutral")
            intensity = float(mood.get("intensity", 0.5) or 0.5)

            # Get state multiplier
            multiplier = MOOD_INTENSITY_MULTIPLIERS.get(state, 1.0)

            # Calculate delay: inversely proportional to multiplier
            # High intensity/good mood = quick checks
            # Low intensity/bad mood = slower checks
            delay = BASE_CHECK_INTERVAL / (multiplier * intensity + 0.5)

            # Clamp between min and max
            delay = max(MIN_CHECK_INTERVAL, min(MAX_CHECK_INTERVAL, delay))

            return delay

        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to calculate check delay")
            return BASE_CHECK_INTERVAL

    def get_autonomy_status(self) -> dict[str, Any]:
        """Return current autonomy status for debugging."""
        root = self._root()
        outcomes = root.get("behavior_outcomes", [])

        recent_success_rate = 0.0
        if outcomes:
            recent = outcomes[-50:]
            recent_success_rate = sum(1 for o in recent if isinstance(o, dict) and o.get("success")) / len(recent)

        return {
            "total_outcomes": len(outcomes) if isinstance(outcomes, list) else 0,
            "recent_success_rate": recent_success_rate,
            "behavior_weights": root.get("behavior_weights", {}),
            "current_mood": self.emotion.get_mood(),
            "decision_count": int(root.get("decision_count", 0) or 0),
        }
