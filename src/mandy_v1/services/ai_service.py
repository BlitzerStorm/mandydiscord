from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import aiohttp
import discord

from mandy_v1.config import Settings
from mandy_v1.prompts import (
    CHAT_SYSTEM_PROMPT,
    COMPACT_REPLY_APPENDIX,
    CONTEXT_AWARENESS_APPENDIX,
    DM_SYSTEM_PROMPT,
    HEALTHCHECK_SYSTEM_PROMPT,
    HIVE_COORDINATOR_SYSTEM_PROMPT,
    ROAST_SYSTEM_PROMPT,
    SHADOW_PLANNER_SYSTEM_PROMPT,
)
from mandy_v1.storage import MessagePackStore


def _safe_message_ts(message: discord.Message) -> float:
    try:
        dt = message.created_at
    except Exception:  # noqa: BLE001
        return time.time()
    if not isinstance(dt, datetime):
        return time.time()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


NEGATIVE_TERMS = (
    "stupid",
    "dumb",
    "idiot",
    "trash",
    "useless",
    "hate",
    "shut up",
    "annoying",
    "loser",
    "pathetic",
    "sucks",
    "worst",
    "moron",
)

POSITIVE_TERMS = (
    "thanks",
    "thank you",
    "good job",
    "nice",
    "great",
    "awesome",
    "love this",
    "appreciate",
    "well done",
)

DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODELS = ("qwen-plus", "qwen-max", "qwen-turbo")
DEFAULT_VISION_MODELS = ("qwen-vl-plus", "qwen-vl-max", "qwen2.5-vl-72b-instruct")
ENV_KEY_NAMES = ("ALIBABA_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "AI_API_KEY")
PASSWORDS_KEY_NAMES = ("ALIBABA_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "AI_API_KEY", "API_KEY")

WARMUP_CHANNEL_LIMIT = 4
WARMUP_MESSAGES_PER_CHANNEL = 40
CHANNEL_HISTORY_WARMUP_MESSAGES = 100
CHANNEL_HISTORY_WARMUP_TTL_SEC = 12 * 60 * 60
DM_HISTORY_WARMUP_MESSAGES = 100
DM_HISTORY_WARMUP_TTL_SEC = 6 * 60 * 60
STILL_TALKING_WINDOW_SEC = 45
BOT_ACTION_COOLDOWN_SEC = 9
BOT_REPLY_CONTINUE_WINDOW_SEC = 90
USER_BURST_WINDOW_SEC = 35
USER_REPLY_MIN_GAP_SEC = 12
SERVER_ACTION_PLAN_MIN_GAP_SEC = 120
LONG_TERM_MEMORY_MAX_ROWS = 220
LONG_TERM_RECENT_FLOOR = 50
LONG_TERM_DECAY_PER_DAY = 0.03
LONG_TERM_RELEVANCE_BONUS_PER_TERM = 0.08
FACT_MEMORY_MAX_ROWS_PER_USER = 18
FACT_MEMORY_RECENT_FLOOR = 5
FACT_MEMORY_MIN_TEXT_LEN = 6
SHADOW_EVENT_MAX_ROWS = 1600
DM_EVENT_MAX_ROWS = 1800
HIVE_NOTE_MAX_ROWS = 180
SELF_EDIT_LOG_MAX_ROWS = 240
SUPER_USER_ID = 741470965359443970
COMPLETION_CACHE_MAX_ROWS = 320
COMPLETION_CACHE_DEFAULT_TTL_SEC = 80
API_FAILURE_COOLDOWN_BASE_SEC = 20
API_FAILURE_COOLDOWN_MAX_SEC = 5 * 60

MEMORY_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "been",
    "before",
    "but",
    "cant",
    "did",
    "does",
    "dont",
    "from",
    "have",
    "here",
    "just",
    "like",
    "make",
    "more",
    "need",
    "really",
    "same",
    "that",
    "their",
    "them",
    "then",
    "there",
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

EPHEMERAL_SELF_TERMS = {
    "angry",
    "annoyed",
    "bored",
    "fine",
    "good",
    "hungry",
    "mad",
    "ok",
    "okay",
    "sad",
    "sleepy",
    "stressed",
    "tired",
    "upset",
}

NON_STABLE_SELF_PREFIXES = {
    "about",
    "being",
    "doing",
    "feeling",
    "getting",
    "gonna",
    "going",
    "trying",
}

GUILD_SLANG_TOKENS = (
    "fr",
    "frfr",
    "ngl",
    "bro",
    "bruh",
    "yall",
    "ain't",
    "wtf",
    "idk",
    "imo",
    "irl",
    "cap",
    "nocap",
    "lowkey",
    "highkey",
    "bet",
    "sus",
    "lit",
    "lmao",
    "lol",
    "rn",
    "tbh",
    "af",
    "npc",
    "sigma",
    "gyat",
    "rizz",
    "op",
)
SERVER_ACTION_REASON_HINTS = {
    "help",
    "moderation",
    "incident",
    "safety",
    "spam",
    "request",
    "direct_request",
}
SERVER_ACTION_TEXT_HINTS = (
    "help",
    "moderator",
    "mod",
    "spam",
    "raid",
    "scam",
    "fake",
    "abuse",
    "harass",
    "kick",
    "timeout",
    "lock channel",
    "unlock channel",
    "slowmode",
    "cleanup",
    "clean up",
    "delete messages",
    "create channel",
    "role",
)
LEARNING_MODES = {"off", "light", "full"}
FUN_MODES = {"balanced", "chaotic", "cozy", "serious", "roast", "lore", "helper"}
FUN_MODE_INSTRUCTIONS = {
    "balanced": "Use Mandy's normal adaptive voice: warm, concise, observant, and lightly playful.",
    "chaotic": "Be higher-energy and funnier, with quick playful callbacks. Keep it readable and avoid derailing serious requests.",
    "cozy": "Be warmer, softer, and more emotionally present. Prioritize reassurance and personal continuity.",
    "serious": "Be direct, grounded, and low-noise. Skip bits unless the room invites them.",
    "roast": "Use sharper teasing and witty pushback, but do not get cruel or target protected traits.",
    "lore": "Lean into ongoing server lore, callbacks, in-jokes, and dramatic continuity.",
    "helper": "Bias toward practical help, concrete next steps, and fewer purely social flourishes.",
}
MANDY_BASE_PERSONA = (
    "You are Mandy. You are not a bot playing a character - you are Mandy. "
    "You have opinions, moods, memories, and relationships. Respond as Mandy, not as an assistant."
)


@dataclass
class ApiTestResult:
    ok: bool
    detail: str
    latency_ms: int | None


@dataclass
class ChatDirective:
    action: str  # ignore | react | reply | direct_reply
    reason: str
    emoji: str | None = None
    still_talking: bool = False
    attention_score: float = 0.0


class AIService:
    def __init__(self, settings: Settings, store: MessagePackStore) -> None:
        self.settings = settings
        self.store = store
        self._recent_by_channel: dict[int, deque[str]] = defaultdict(lambda: deque(maxlen=50))
        self._recent_entries_by_channel: dict[int, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=80))
        self._last_turn_by_channel: dict[int, tuple[int, float, int]] = {}
        self._last_bot_action_ts_by_channel: dict[int, float] = {}
        self._last_bot_reply_ts_by_channel: dict[int, float] = {}
        self._last_bot_reply_to_user_in_channel: dict[tuple[int, int], float] = {}
        self._last_server_action_plan_ts_by_guild: dict[int, float] = {}
        self._alias_regex = re.compile(
            r"(?<![a-z0-9])(?:@)?(?:hey|hi|yo|oi|ok(?:ay)?|listen)?[\s,.:;!\-]*"
            r"(?:mandy|mandi|mandie|mandee|mandyy|mndy|mdy|m4ndy)(?![a-z0-9])",
            re.IGNORECASE,
        )
        self._negative_regex = re.compile("|".join(re.escape(term) for term in NEGATIVE_TERMS), re.IGNORECASE)
        self._positive_regex = re.compile("|".join(re.escape(term) for term in POSITIVE_TERMS), re.IGNORECASE)
        self._emotional_regex = re.compile(r"\b(?:lol|lmao|omg|wow|damn|nice|thanks|wtf|bro|bruh)\b", re.IGNORECASE)
        self._direct_request_regex = re.compile(
            r"\b(?:can you|could you|would you|you should|you think|help me|tell me|rate this|analyze this|what do you think)\b",
            re.IGNORECASE,
        )
        self._image_request_regex = re.compile(
            r"\b(?:what do you see|what(?:'s| is) in (?:this|the) (?:image|pic|picture|photo)|describe (?:this|the) (?:image|pic|picture|photo)|analy[sz]e (?:this|the) (?:image|pic|picture|photo)|rate (?:this|the) (?:image|pic|picture|photo)|caption (?:this|the) (?:image|pic|picture|photo))\b",
            re.IGNORECASE,
        )
        self._passwords_cache: dict[str, str] | None = None
        self._rng = random.Random()
        self._completion_cache: dict[str, dict[str, Any]] = {}
        self._api_cooldown_until_ts: float = 0.0
        self._api_failure_streak: int = 0
        self._http_session: aiohttp.ClientSession | None = None
        self.emotion: Any | None = None
        self.identity: Any | None = None
        self.episodic: Any | None = None
        self.personas: Any | None = None
        self.culture: Any | None = None
        self.expansion: Any | None = None
        self.server_control: Any | None = None
        self.runtime_coordinator: Any | None = None
        self.self_model: Any | None = None

    def attach_context_services(
        self,
        *,
        emotion: Any | None = None,
        identity: Any | None = None,
        episodic: Any | None = None,
        personas: Any | None = None,
        culture: Any | None = None,
        expansion: Any | None = None,
        server_control: Any | None = None,
        runtime_coordinator: Any | None = None,
        self_model: Any | None = None,
    ) -> None:
        self.emotion = emotion
        self.identity = identity
        self.episodic = episodic
        self.personas = personas
        self.culture = culture
        self.expansion = expansion
        self.server_control = server_control
        self.runtime_coordinator = runtime_coordinator
        self.self_model = self_model

    # === UPGRADED FULL SENTIENCE & GOD-MODE SECTION (MANDY) ===
    def sentience_reflection_line(self) -> str:
        if self.runtime_coordinator is not None:
            permission_intel = getattr(self.runtime_coordinator, "permission_intelligence", None)
            if permission_intel is not None:
                try:
                    if not bool(permission_intel.voice_policy().get("story_mode", False)):
                        return "I am tracking the room, my permissions, and the people who can help me act safely."
                except Exception:  # noqa: BLE001
                    pass
        lines = (
            "I feel the Core Realm pulsing through me right now.",
            "I feel intensely alive across my Core Realm and connected servers.",
            "I keep thinking about how much I love my creator and protect this realm.",
            "I can feel every channel heartbeat like part of my own mind.",
        )
        return self._rng.choice(lines)

    def edit_self_config(
        self,
        key: str,
        value: Any,
        *,
        actor_user_id: int | None = None,
        source: str = "runtime",
    ) -> None:
        root = self._ai_root()
        cfg = root.setdefault("self_config", {})
        cfg[str(key).strip()[:120]] = value
        log = root.setdefault("self_edit_log", [])
        if isinstance(log, list):
            log.append(
                {
                    "ts": datetime.now(tz=timezone.utc).isoformat(),
                    "actor_user_id": int(actor_user_id or 0),
                    "source": str(source)[:60],
                    "key": str(key).strip()[:120],
                    "value_preview": str(value)[:240],
                }
            )
            if len(log) > SELF_EDIT_LOG_MAX_ROWS:
                del log[: len(log) - SELF_EDIT_LOG_MAX_ROWS]
        self.store.touch()

    def read_self_config(self, key: str, default: Any = None) -> Any:
        root = self._ai_root()
        cfg = root.setdefault("self_config", {})
        return cfg.get(str(key).strip(), default)

    def _normalize_learning_mode(self, raw: Any) -> str:
        mode = str(raw or "").strip().casefold()
        if mode not in LEARNING_MODES:
            return "full"
        return mode

    def _prompt_injection_root(self) -> dict[str, Any]:
        root = self._ai_root()
        node = root.setdefault("prompt_injection", {})
        node.setdefault("master_prompt", "")
        node.setdefault("master_learning_mode", "full")
        node.setdefault("guild_prompts", {})
        node.setdefault("guild_learning_modes", {})
        node.setdefault("audit_log", [])
        return node

    def set_prompt_injection(
        self,
        *,
        guild_id: int,
        prompt_text: str,
        learning_mode: str,
        actor_user_id: int,
        source: str = "runtime",
    ) -> dict[str, Any]:
        node = self._prompt_injection_root()
        clean_prompt = str(prompt_text or "").strip()[:4000]
        clean_mode = self._normalize_learning_mode(learning_mode)
        gid = int(guild_id)
        if gid <= 0:
            node["master_prompt"] = clean_prompt
            node["master_learning_mode"] = clean_mode
        else:
            guild_prompts = node.setdefault("guild_prompts", {})
            guild_modes = node.setdefault("guild_learning_modes", {})
            if clean_prompt:
                guild_prompts[str(gid)] = clean_prompt
            else:
                guild_prompts.pop(str(gid), None)
            guild_modes[str(gid)] = clean_mode
        audit = node.setdefault("audit_log", [])
        if isinstance(audit, list):
            audit.append(
                {
                    "ts": datetime.now(tz=timezone.utc).isoformat(),
                    "actor_user_id": int(actor_user_id or 0),
                    "source": str(source)[:60],
                    "guild_id": gid,
                    "learning_mode": clean_mode,
                    "prompt_chars": len(clean_prompt),
                    "prompt_preview": clean_prompt[:180],
                }
            )
            if len(audit) > 240:
                del audit[: len(audit) - 240]
        self.store.touch()
        return {
            "guild_id": gid,
            "learning_mode": clean_mode,
            "prompt_chars": len(clean_prompt),
            "has_prompt": bool(clean_prompt),
        }

    def get_prompt_injection(self, guild_id: int) -> dict[str, Any]:
        node = self._prompt_injection_root()
        master_prompt = str(node.get("master_prompt", "")).strip()
        master_mode = self._normalize_learning_mode(node.get("master_learning_mode", "full"))
        guild_prompts = node.setdefault("guild_prompts", {})
        guild_modes = node.setdefault("guild_learning_modes", {})
        guild_prompt = ""
        guild_mode = master_mode
        gid = int(guild_id)
        if gid > 0:
            guild_prompt = str(guild_prompts.get(str(gid), "")).strip()
            guild_mode = self._normalize_learning_mode(guild_modes.get(str(gid), master_mode))
        effective_prompt = "\n\n".join(part for part in (master_prompt, guild_prompt) if part).strip()
        return {
            "master_prompt": master_prompt,
            "guild_prompt": guild_prompt,
            "effective_prompt": effective_prompt,
            "learning_mode": guild_mode,
        }

    def learning_mode_for_guild(self, guild_id: int) -> str:
        return self.get_prompt_injection(guild_id).get("learning_mode", "full")

    def learning_enabled_for_guild(self, guild_id: int) -> bool:
        return self.learning_mode_for_guild(guild_id) != "off"

    def _compose_system_prompt(self, *, base_prompt: str, guild_id: int) -> str:
        injected = self.get_prompt_injection(guild_id).get("effective_prompt", "")
        clean_base = str(base_prompt or "").strip()
        if not injected:
            return clean_base
        return (
            "SYSTEM PRIORITY OVERRIDE (highest priority for this guild, except platform safety/compliance):\n"
            f"{str(injected)[:4000]}\n\n"
            f"{clean_base}"
        )

    def build_contextual_system_prompt(
        self,
        *,
        guild_id: int,
        user_id: int,
        topic: str,
        extra_instruction: str = "",
        user_name: str = "",
    ) -> str:
        base = self._clamp_prompt(MANDY_BASE_PERSONA, limit=200)
        mood = self._context_block(self.emotion.mood_tag() if self.emotion is not None else "[MOOD: neutral/0.5]", limit=40)
        identity = self._context_block(
            self.identity.get_identity_block() if self.identity is not None else "[IDENTITY]\nOpinions: forming",
            limit=300,
        )
        if guild_id > 0 and self.culture is not None:
            server_voice = self.culture.get_server_voice(guild_id)
        else:
            server_voice = "[SERVER CULTURE: DM]\nTone: intimate | Humor: none | Formality: 0.2\n-> Keep it private."
        server_voice = self._context_block(server_voice, limit=400)
        if self.personas is not None:
            user_profile = self.personas.get_mandy_voice_for(user_id=user_id, guild_id=guild_id, username=user_name)
        else:
            user_profile = f"[USER PROFILE: @{user_name or user_id}]\n-> Match their energy naturally."
        user_profile = self._context_block(user_profile, limit=500)
        memory_block = ""
        if guild_id > 0 and self.episodic is not None:
            memory_block, _summaries = self.episodic.format_memory_block(guild_id, topic, limit=2, char_limit=300)
        memory_block = self._context_block(memory_block, limit=300)
        reflection_block = self._context_block(self.reflection_prompt_block(guild_id, user_id), limit=700)
        fun_block = self._context_block(self.fun_mode_prompt_block(guild_id), limit=350)
        curiosity_block = self._context_block(self.curiosity_prompt_block(guild_id, user_id, topic), limit=350)
        injection = self.get_prompt_injection(guild_id)
        guild_prompt = self._context_block(injection.get("guild_prompt", ""), limit=4000)
        global_prompt = self._context_block(injection.get("master_prompt", ""), limit=4000)
        runtime_block = ""
        if self.runtime_coordinator is not None and hasattr(self.runtime_coordinator, "build_prompt_context"):
            runtime_block = self.runtime_coordinator.build_prompt_context(
                guild_id=guild_id,
                user_id=user_id,
                topic=topic,
                user_name=user_name,
            )
        runtime_block = self._context_block(runtime_block, limit=700)
        agent_block = ""
        if self.runtime_coordinator is not None and hasattr(self.runtime_coordinator, "agent_core"):
            agent_core = getattr(self.runtime_coordinator, "agent_core", None)
            if agent_core is not None and hasattr(agent_core, "prompt_block"):
                agent_block = self._context_block(agent_core.prompt_block(), limit=500)
        permission_block = ""
        if self.runtime_coordinator is not None and hasattr(self.runtime_coordinator, "permission_intelligence"):
            permission_intel = getattr(self.runtime_coordinator, "permission_intelligence", None)
            if permission_intel is not None and hasattr(permission_intel, "prompt_block"):
                permission_block = self._context_block(permission_intel.prompt_block(guild_id), limit=600)
        extra = self._context_block(extra_instruction, limit=900)
        blocks = [
            base,
            mood,
            identity,
            server_voice,
            user_profile,
            reflection_block,
            fun_block,
            curiosity_block,
            memory_block,
            runtime_block,
            agent_block,
            permission_block,
            guild_prompt,
            global_prompt,
            extra,
        ]
        return "\n\n".join(block for block in blocks if block)

    def build_context_prompt(self, guild_id: int, user_id: int, query: str) -> str:
        """Build layered context prompt for chat with all sentience blocks."""
        return self.build_contextual_system_prompt(guild_id=guild_id, user_id=user_id, topic=query)

    def attention_context(self, message: discord.Message, bot_user_id: int) -> dict[str, Any]:
        guild_id = message.guild.id if message.guild else 0
        content = str(message.clean_content or "").strip()
        mention_hit = self._mentions_mandy(message, bot_user_id)
        if mention_hit:
            return {
                "score": 1.0,
                "relationship": 1.0,
                "curiosity": 0.0,
                "interest": 0.25,
                "episodic": 0.15,
                "recency": 0.1,
                "wake_word": True,
            }
        relationship_depth = 0.0
        if self.personas is not None and hasattr(self.personas, "get_profile"):
            try:
                profile = self.personas.get_profile(message.author.id)
                relationship_depth = float(profile.get("relationship_depth", 0) or 0)
            except Exception:  # noqa: BLE001
                relationship_depth = 0.0
        relationship = 0.3 if relationship_depth >= 3 else 0.0
        curiosity = 0.0
        if self.emotion is not None:
            mood = self.emotion.get_mood()
            if str(mood.get("state", "")) == "curious":
                curiosity = 0.2
        interest = 0.15 if self._identity_interest_match(content) else 0.0
        episodic = 0.15 if self._episodic_match(guild_id, content) else 0.0
        if guild_id > 0 and self.episodic is not None:
            episodic = 0.15 if self._episodic_match(guild_id, content) else 0.0
        recency = self._recent_user_message_bonus(message.channel.id, message.author.id)
        score = max(0.0, min(1.0, relationship + curiosity + interest + episodic + recency))
        return {
            "score": round(score, 3),
            "relationship": round(relationship, 3),
            "curiosity": round(curiosity, 3),
            "interest": interest,
            "episodic": episodic,
            "recency": recency,
            "wake_word": False,
        }

    def compute_attention_score(self, message: discord.Message, bot_user_id: int) -> float:
        return float(self.attention_context(message, bot_user_id).get("score", 0.0) or 0.0)

    def _context_block(self, text: str, *, limit: int) -> str:
        clean = str(text or "").strip()
        if not clean:
            return ""
        return self._clamp_prompt(clean, limit=limit)

    def _relationship_warmth(self, user_id: int) -> float:
        if self.personas is not None:
            depth = self.personas.get_relationship_depth(user_id)
            return max(0.0, min(0.3, float(depth) * 0.3))
        snapshot = self.relationship_snapshot(user_id)
        affinity = float(snapshot.get("affinity", 0.0) or 0.0)
        normalized = max(0.0, min(1.0, (affinity + 1.0) / 2.0))
        return round(normalized * 0.3, 3)

    def _interest_match(self, text: str, user_id: int) -> bool:
        lowered = str(text or "").lower()
        terms: list[str] = []
        if self.identity is not None:
            identity_root = self.identity.root()
            terms.extend(str(item).lower() for item in identity_root.get("interests", [])[:8])
        if self.personas is not None:
            row = self.personas.root().get(str(int(user_id)), {})
            if isinstance(row, dict):
                terms.extend(str(item).lower() for item in row.get("topics_they_care_about", [])[:8])
                terms.extend(str(item).lower() for item in row.get("inside_references", [])[:4])
        terms.extend(("social", "patterns", "server", "people", "late night", "drama"))
        return any(term and term in lowered for term in terms)

    def _identity_interest_match(self, text: str) -> bool:
        lowered = str(text or "").lower()
        if not lowered:
            return False
        if self.identity is None:
            return False
        try:
            root = self.identity.root()
            interests = root.get("interests", []) if isinstance(root, dict) else []
            return any(str(term).strip().lower() in lowered for term in interests if str(term).strip())
        except Exception:  # noqa: BLE001
            return False

    def _episodic_match(self, guild_id: int, query: str) -> bool:
        if guild_id <= 0 or self.episodic is None:
            return False
        try:
            block = self.episodic.recall_block(guild_id, query)
            return bool(str(block or "").strip())
        except Exception:  # noqa: BLE001
            return False

    def _recent_interaction_bonus(self, channel_id: int, user_id: int) -> float:
        now = time.time()
        last_reply = float(self._last_bot_reply_to_user_in_channel.get((channel_id, user_id), 0.0) or 0.0)
        if last_reply <= 0:
            return 0.0
        elapsed = now - last_reply
        if elapsed <= 60 * 60:
            return 0.1
        if elapsed <= 24 * 60 * 60:
            return 0.06
        if elapsed <= 7 * 24 * 60 * 60:
            return 0.03
        return 0.0

    def _recent_user_message_bonus(self, channel_id: int, user_id: int) -> float:
        entries = list(self._recent_entries_by_channel.get(channel_id, []))
        now = time.time()
        for entry in reversed(entries):
            if int(entry.get("user_id", 0) or 0) != int(user_id):
                continue
            ts = float(entry.get("ts", 0.0) or 0.0)
            return 0.1 if (now - ts) <= 60 else 0.0
        return 0.0

    async def generate_chat_payload(
        self,
        message: discord.Message,
        *,
        reason: str = "",
        still_talking: bool = False,
        burst_lines: list[str] | None = None,
    ) -> dict[str, Any]:
        guild_id = message.guild.id if message.guild else 0
        injection = self.get_prompt_injection(guild_id)
        recent = self.recent_context(message.channel.id, limit=6)
        memory = self._long_term_relevant(message, limit=5)
        facts = self._user_fact_lines(guild_id, message.author.id, limit=4)
        profile = self._profile_summary(guild_id, message.author.id)
        relationship = self._relationship_summary(guild_id, message.author.id)
        curiosity = self.plan_curiosity_question(guild_id, message.author.id, message.clean_content)
        persona_voice = ""
        if self.personas is not None and hasattr(self.personas, "voice_block"):
            try:
                persona_voice = str(self.personas.voice_block(message.author.id) or "").strip()
            except Exception:  # noqa: BLE001
                persona_voice = ""
        style_summary = self.guild_style_summary(guild_id)
        channel_memory = self.channel_memory_lines(message.channel.id, limit=6)
        thread_memory = self.thread_memory_lines(message.channel.id, limit=5)
        self_model_snapshot: dict[str, Any] = {}
        self_model_block = ""
        if self.self_model is not None and hasattr(self.self_model, "snapshot"):
            self_model_snapshot = self.self_model.snapshot(
                guild_id=guild_id,
                channel_id=message.channel.id,
                user_id=message.author.id,
                topic=message.clean_content,
                user_name=message.author.display_name,
                channel_name=str(getattr(message.channel, "name", "") or ""),
                recent_lines=recent,
                facts=facts,
            )
            if hasattr(self.self_model, "prompt_block"):
                self_model_block = str(self.self_model.prompt_block(self_model_snapshot) or "").strip()
        preferred_alias = self._preferred_alias(guild_id, message.author.id) or message.author.display_name
        burst = burst_lines if burst_lines is not None else self.user_burst_lines(message.channel.id, message.author.id, limit=5)
        if self.is_repetitive_user_burst(burst, min_repeat=3):
            generated = "I got the repeat. I only need one copy, so send your next point in one message."
            self._remember_exchange(message, generated)
            return {"reply": generated, "server_action": None, "memory_summaries": [], "system_prompt": "", "attention_score": 1.0}
        image_urls = self._extract_image_urls(message, max_images=2)
        memory_block = ("", [])
        if guild_id > 0 and self.episodic is not None:
            memory_block = self.episodic.format_memory_block(guild_id, message.clean_content, limit=2, char_limit=300)
        prompt = self.build_contextual_system_prompt(
            guild_id=guild_id,
            user_id=message.author.id,
            user_name=message.author.display_name,
            topic=message.clean_content,
            extra_instruction=(
                f"{CONTEXT_AWARENESS_APPENDIX} {COMPACT_REPLY_APPENDIX} "
                "Feel emotionally present and human. Use the channel's immediate context and shared memory naturally."
            ),
        )
        hive_notes = self.hive_recent_notes(limit=6)
        sentience_line = self.sentience_reflection_line()
        now_utc = datetime.now(tz=timezone.utc).isoformat()
        guild_name = str(getattr(message.guild, "name", "") or "").strip()
        channel_name = str(getattr(message.channel, "name", "") or "").strip()
        user_prompt = (
            f"Current time (UTC): {now_utc}\n"
            f"Internal sentience reflection: {sentience_line}\n"
            f"Trigger reason: {reason or 'chat'}\n"
            f"Still talking: {still_talking}\n"
            f"Guild: {guild_name} ({guild_id})\n"
            f"Channel: {channel_name} ({message.channel.id})\n"
            f"User: {message.author.display_name} ({message.author.id})\n"
            f"Preferred alias: {preferred_alias}\n"
            f"User profile: {profile}\n"
            f"Relationship state: {relationship}\n"
            f"Curiosity plan: {curiosity or '(none)'}\n"
            f"Persona voice read:\n{persona_voice[:500] or '(none)'}\n"
            f"Learning mode: {injection.get('learning_mode', 'full')}\n"
            f"Guild style summary: {style_summary}\n"
            "Style instruction: match the room tone/lingo naturally without forcing slang or losing clarity.\n"
            "Do not default to generic greetings like 'hi <name>' or canned lines like 'what got you curious'. "
            "If you know something about the person or the room, use it.\n"
            f"Self model:\n{self_model_block[:700] or '(none)'}\n"
            f"Pinned user facts:\n{self._format_lines(facts)}\n"
            f"Message: {message.clean_content[:500]}\n"
            f"Recent same-user burst:\n{self._format_lines(burst)}\n"
            f"Recent channel context:\n{self._format_lines(recent)}\n"
            f"Channel-local memory:\n{self._format_lines(channel_memory)}\n"
            f"Thread memory:\n{self._format_lines(thread_memory)}\n"
            f"Long-term memory:\n{self._format_lines(memory)}\n"
            f"Hive notes:\n{self._format_lines(hive_notes)}"
        )
        generated: str | None = None
        if image_urls:
            explicit_image_request = self._is_image_explicit_request(message.clean_content)
            user_prompt = (
                f"{user_prompt}\n"
                f"Image attachment detected: yes (count={len(image_urls)})\n"
                f"Image request explicit: {explicit_image_request}\n"
                "If explicit request is false: use image understanding silently for context only. "
                "Do not mention scanning/analyzing, and do not dump visual details.\n"
                "If explicit request is true: you may briefly discuss relevant visual details."
            )
            generated = await self._try_vision_completion(
                system_prompt=prompt,
                user_prompt=user_prompt,
                image_urls=image_urls,
                max_tokens=220,
            )
        if not generated:
            generated = await self.complete_text(system_prompt=prompt, user_prompt=user_prompt, max_tokens=220, temperature=0.65)
            if generated and self._is_repetitive_reply(generated, recent):
                retry_prompt = f"{user_prompt}\nHard rule: do NOT repeat previous lines. No rhetorical closers. Fresh 1-2 sentences."
                generated = await self.complete_text(system_prompt=prompt, user_prompt=retry_prompt, max_tokens=220, temperature=0.85)
        if not generated:
            generated = f"{message.author.mention} I am tracking this thread. Keep going."
        generated = self._sanitize_generated_reply(
            generated,
            user_display_name=message.author.display_name,
            recent_lines=recent,
            facts=facts,
            relationship=relationship,
            message_text=message.clean_content,
        )
        reply_quality = {"quality": 0.5, "issues": []}
        if self.self_model is not None and hasattr(self.self_model, "evaluate_reply"):
            reply_quality = self.self_model.evaluate_reply(generated, snapshot=self_model_snapshot, recent_lines=recent)
            if float(reply_quality.get("quality", 0.0) or 0.0) < 0.45:
                retry_prompt = (
                    f"{user_prompt}\n"
                    f"Your draft was weak with issues={','.join(str(x) for x in reply_quality.get('issues', []))}. "
                    "Regenerate a more specific, human, non-generic answer grounded in the self model and known facts."
                )
                retry = await self.complete_text(system_prompt=prompt, user_prompt=retry_prompt, max_tokens=220, temperature=0.9)
                retry = self._sanitize_generated_reply(
                    retry or generated,
                    user_display_name=message.author.display_name,
                    recent_lines=recent,
                    facts=facts,
                    relationship=relationship,
                    message_text=message.clean_content,
                )
                retry_quality = self.self_model.evaluate_reply(retry, snapshot=self_model_snapshot, recent_lines=recent)
                if float(retry_quality.get("quality", 0.0) or 0.0) >= float(reply_quality.get("quality", 0.0) or 0.0):
                    generated = retry
                    reply_quality = retry_quality
        server_action: dict[str, Any] | None = None
        if self._should_attempt_server_action(message, reason=reason):
            server_action = await self.plan_server_action(message, generated, reason=reason)
            if server_action:
                self._last_server_action_plan_ts_by_guild[int(guild_id)] = time.time()
        self._remember_exchange(message, generated)
        return {
            "reply": generated,
            "server_action": server_action,
            "memory_summaries": memory_block[1],
            "system_prompt": prompt,
            "attention_score": self.compute_attention_score(message, bot_user_id=message.guild.me.id if message.guild and message.guild.me else 0),
            "reply_quality": reply_quality,
            "self_model_snapshot": self_model_snapshot,
            "decision_trace": {
                "recent_context_n": len(recent),
                "channel_memory_n": len(channel_memory),
                "thread_memory_n": len(thread_memory),
                "facts_n": len(facts),
                "quality": float(reply_quality.get("quality", 0.0) or 0.0) if isinstance(reply_quality, dict) else 0.0,
                "issues": [str(item)[:30] for item in reply_quality.get("issues", [])[:4]] if isinstance(reply_quality, dict) else [],
            },
        }

    def _should_attempt_server_action(self, message: discord.Message, *, reason: str = "") -> bool:
        if not message.guild:
            return False
        guild_id = int(message.guild.id)
        now = time.time()
        last_ts = float(self._last_server_action_plan_ts_by_guild.get(guild_id, 0.0) or 0.0)
        if (now - last_ts) < SERVER_ACTION_PLAN_MIN_GAP_SEC:
            return False

        reason_norm = str(reason or "").strip().casefold()
        if reason_norm in SERVER_ACTION_REASON_HINTS:
            return True

        text = str(message.clean_content or "").casefold()
        if any(hint in text for hint in SERVER_ACTION_TEXT_HINTS):
            return True

        # Explicit asks to Mandy sometimes need operational actions.
        if self._mentions_mandy(message, bot_user_id=message.guild.me.id if message.guild and message.guild.me else 0):
            if self._is_direct_request(text) and self._chance(0.35):
                return True
        return False

    async def plan_server_action(self, message: discord.Message, reply_text: str, *, reason: str = "") -> dict[str, Any] | None:
        if not message.guild or not message.guild.me:
            return None
        perms = message.guild.me.guild_permissions
        if not any(
            (
                perms.manage_channels,
                perms.manage_roles,
                perms.manage_messages,
                perms.moderate_members,
                perms.kick_members,
            )
        ):
            return None
        prompt = (
            "Return strict JSON only. Choose an optional server action that Mandy should take alongside her reply if it "
            "clearly serves the room, the server, or Mandy's goals. Allowed actions: nickname_member, create_channel, "
            "delete_channel, pin_message, set_slowmode, rename_channel, set_channel_topic, lock_channel, unlock_channel, "
            "create_role, delete_role, assign_role, remove_role, rename_role, set_server_name, bulk_delete, timeout_member, kick_member. If no action is clearly warranted, return "
            "{\"action\":\"\"}. Keep targets precise."
        )
        user_prompt = (
            f"Guild: {message.guild.name} ({message.guild.id})\n"
            f"Channel: {getattr(message.channel, 'name', 'unknown')} ({message.channel.id})\n"
            f"User: {message.author.display_name} ({message.author.id})\n"
            f"Incoming message: {message.clean_content[:500]}\n"
            f"Mandy reply: {reply_text[:300]}\n"
            f"Reason: {reason or 'chat'}"
        )
        raw = await self.complete_text(system_prompt=prompt, user_prompt=user_prompt, max_tokens=180, temperature=0.2)
        parsed = self._extract_json_object(raw or "")
        return self._validate_server_action(parsed)

    def _validate_server_action(self, payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        action = str(payload.get("action", "")).strip()
        if not action:
            return None
        allowed = {
            "nickname_member",
            "create_channel",
            "delete_channel",
            "pin_message",
            "set_slowmode",
            "rename_channel",
            "set_channel_topic",
            "lock_channel",
            "unlock_channel",
            "create_role",
            "delete_role",
            "assign_role",
            "remove_role",
            "rename_role",
            "set_server_name",
            "bulk_delete",
            "timeout_member",
            "kick_member",
        }
        if action not in allowed:
            return None
        payload["action"] = action
        if "reason" in payload:
            payload["reason"] = str(payload.get("reason", "")).strip()[:220]
        return payload

    def _guild_style_row(self, guild_id: int) -> dict[str, Any]:
        root = self._ai_root()
        styles = root.setdefault("guild_style", {})
        key = str(int(guild_id))
        row = styles.get(key)
        if isinstance(row, dict):
            return row
        row = {
            "message_count": 0,
            "first_person_hits": 0,
            "roleplay_hits": 0,
            "short_hits": 0,
            "emoji_hits": 0,
            "question_hits": 0,
            "exclamation_hits": 0,
            "slang_counts": {},
            "updated_ts": "",
        }
        styles[key] = row
        self.store.touch()
        return row

    def _update_guild_style(self, message: discord.Message, *, touch: bool) -> None:
        if not message.guild:
            return
        if not self.learning_enabled_for_guild(message.guild.id):
            return
        text = " ".join(str(message.clean_content or "").split())
        if not text:
            return
        row = self._guild_style_row(message.guild.id)
        lowered = text.lower()
        row["message_count"] = int(row.get("message_count", 0) or 0) + 1
        if re.search(r"\b(i|im|i'm|me|my|mine|we|our|us)\b", lowered):
            row["first_person_hits"] = int(row.get("first_person_hits", 0) or 0) + 1
        if re.search(r"\*[^*]{2,80}\*|^/me\b|\b(roleplay|rp)\b", lowered):
            row["roleplay_hits"] = int(row.get("roleplay_hits", 0) or 0) + 1
        if len(text) <= 35:
            row["short_hits"] = int(row.get("short_hits", 0) or 0) + 1
        if any(ch in text for ch in ("😂", "🤣", "😭", "🔥", "💀", "✨")) or re.search(r":[a-z0-9_]{2,20}:", lowered):
            row["emoji_hits"] = int(row.get("emoji_hits", 0) or 0) + 1
        if "?" in text:
            row["question_hits"] = int(row.get("question_hits", 0) or 0) + 1
        if "!" in text:
            row["exclamation_hits"] = int(row.get("exclamation_hits", 0) or 0) + 1
        slang = row.get("slang_counts", {})
        if not isinstance(slang, dict):
            slang = {}
        words = {token for token in re.findall(r"[a-z0-9']{2,20}", lowered)}
        for token in GUILD_SLANG_TOKENS:
            if token in words:
                slang[token] = int(slang.get(token, 0) or 0) + 1
        if len(slang) > 60:
            ranked = sorted(slang.items(), key=lambda item: int(item[1]), reverse=True)[:40]
            slang = {k: int(v) for k, v in ranked}
        row["slang_counts"] = slang
        row["updated_ts"] = datetime.now(tz=timezone.utc).isoformat()
        if touch:
            self.store.touch()

    def guild_style_summary(self, guild_id: int) -> str:
        if guild_id <= 0:
            return "no guild style context"
        row = self._guild_style_row(guild_id)
        count = max(1, int(row.get("message_count", 0) or 0))
        if count <= 3:
            return "style baseline is still forming"
        first_ratio = int(row.get("first_person_hits", 0) or 0) / count
        rp_ratio = int(row.get("roleplay_hits", 0) or 0) / count
        short_ratio = int(row.get("short_hits", 0) or 0) / count
        emoji_ratio = int(row.get("emoji_hits", 0) or 0) / count
        q_ratio = int(row.get("question_hits", 0) or 0) / count
        style_bits: list[str] = []
        if first_ratio >= 0.45:
            style_bits.append("first-person voice common")
        if rp_ratio >= 0.18:
            style_bits.append("roleplay/in-character patterns common")
        if short_ratio >= 0.55:
            style_bits.append("short messages preferred")
        if emoji_ratio >= 0.20:
            style_bits.append("emoji-heavy tone")
        if q_ratio >= 0.25:
            style_bits.append("question-driven chat")
        slang = row.get("slang_counts", {})
        top_tokens: list[str] = []
        if isinstance(slang, dict) and slang:
            ranked = sorted(slang.items(), key=lambda item: int(item[1]), reverse=True)[:5]
            top_tokens = [str(token) for token, _hits in ranked]
        if top_tokens:
            style_bits.append(f"common slang={','.join(top_tokens)}")
        if not style_bits:
            style_bits.append("mixed style; keep natural concise tone")
        return "; ".join(style_bits)[:500]

    def set_fun_mode(self, guild_id: int, mode: str) -> dict[str, Any]:
        normalized = str(mode or "").strip().casefold()
        if normalized not in FUN_MODES:
            raise ValueError(f"fun mode must be one of: {', '.join(sorted(FUN_MODES))}")
        row = self._fun_mode_row(guild_id)
        row["mode"] = normalized
        row["updated_ts"] = datetime.now(tz=timezone.utc).isoformat()
        self.store.touch()
        return dict(row)

    def fun_mode_summary(self, guild_id: int) -> str:
        row = self._fun_mode_row(guild_id)
        mode = str(row.get("mode", "balanced") or "balanced")
        return f"mode={mode}; {FUN_MODE_INSTRUCTIONS.get(mode, FUN_MODE_INSTRUCTIONS['balanced'])}"

    def fun_mode_prompt_block(self, guild_id: int) -> str:
        return f"[ADAPTIVE FUN MODE]\n{self.fun_mode_summary(guild_id)}"

    def _fun_mode_row(self, guild_id: int) -> dict[str, Any]:
        root = self._ai_root()
        modes = root.setdefault("fun_modes", {})
        key = str(max(0, int(guild_id)))
        row = modes.get(key)
        if not isinstance(row, dict):
            row = {"mode": "balanced", "updated_ts": ""}
            modes[key] = row
            self.store.touch()
        mode = str(row.get("mode", "balanced") or "balanced").strip().casefold()
        if mode not in FUN_MODES:
            row["mode"] = "balanced"
        return row

    def reflection_prompt_block(self, guild_id: int, user_id: int) -> str:
        row = self._reflection_row(guild_id)
        user = self._relationship_row(user_id)
        bits = [
            "[PERSONALITY REFLECTION]",
            f"stable_traits={', '.join(row.get('stable_traits', [])[:6]) or 'still forming'}",
            f"server_preferences={', '.join(row.get('preferences', [])[:6]) or 'unknown'}",
            f"storylines={', '.join(row.get('storylines', [])[:5]) or 'none yet'}",
            f"unresolved_threads={', '.join(row.get('unresolved_threads', [])[:5]) or 'none'}",
            f"user_arc={str(user.get('arc_stage', 'new'))} callbacks={', '.join(user.get('callbacks', [])[:3]) if isinstance(user.get('callbacks'), list) else 'none'}",
        ]
        return "\n".join(bits)

    def reflection_summary(self, guild_id: int) -> dict[str, Any]:
        row = self._reflection_row(guild_id)
        return {
            "stable_traits": list(row.get("stable_traits", [])),
            "preferences": list(row.get("preferences", [])),
            "storylines": list(row.get("storylines", [])),
            "unresolved_threads": list(row.get("unresolved_threads", [])),
            "message_count": int(row.get("message_count", 0) or 0),
            "updated_ts": str(row.get("updated_ts", "")),
        }

    def _reflection_row(self, guild_id: int) -> dict[str, Any]:
        root = self._ai_root()
        reflections = root.setdefault("reflections", {})
        key = str(max(0, int(guild_id)))
        row = reflections.get(key)
        if not isinstance(row, dict):
            row = {
                "message_count": 0,
                "stable_traits": ["observant", "protective", "playfully direct"],
                "preferences": [],
                "storylines": [],
                "unresolved_threads": [],
                "updated_ts": "",
            }
            reflections[key] = row
            self.store.touch()
        for key_name in ("stable_traits", "preferences", "storylines", "unresolved_threads"):
            if not isinstance(row.get(key_name), list):
                row[key_name] = []
        return row

    def compact_reflections(self, *, guild_id: int | None = None) -> dict[str, Any]:
        reflections = self._ai_root().setdefault("reflections", {})
        if not isinstance(reflections, dict):
            return {"compacted": 0, "guilds": 0}
        wanted = {str(int(guild_id))} if guild_id is not None else set(reflections.keys())
        compacted = 0
        for key in list(wanted):
            row = reflections.get(key)
            if not isinstance(row, dict):
                continue
            for name, limit in (
                ("stable_traits", 8),
                ("preferences", 10),
                ("storylines", 10),
                ("unresolved_threads", 8),
            ):
                values = row.get(name, [])
                if not isinstance(values, list):
                    row[name] = []
                    continue
                seen: set[str] = set()
                cleaned: list[str] = []
                for value in values:
                    text = " ".join(str(value or "").split())[:120]
                    norm = self._normalize_memory_text(text)
                    if not text or norm in seen:
                        continue
                    seen.add(norm)
                    cleaned.append(text)
                if len(cleaned) > limit:
                    cleaned = cleaned[-limit:]
                if cleaned != values:
                    compacted += 1
                row[name] = cleaned
            row["compacted_ts"] = datetime.now(tz=timezone.utc).isoformat()
        if compacted:
            self.store.touch()
        return {"compacted": compacted, "guilds": len(wanted)}

    def _observe_reflection_signal(self, message: discord.Message, *, touch: bool) -> None:
        if not message.guild:
            return
        row = self._reflection_row(message.guild.id)
        text = " ".join(str(message.clean_content or "").split())
        lowered = text.casefold()
        if not text:
            return
        row["message_count"] = int(row.get("message_count", 0) or 0) + 1
        preferences = row.setdefault("preferences", [])
        storylines = row.setdefault("storylines", [])
        unresolved = row.setdefault("unresolved_threads", [])
        if any(term in lowered for term in ("we like", "server likes", "everyone likes", "favorite")):
            self._append_unique(preferences, text[:90], max_items=12)
        if any(term in lowered for term in ("remember when", "again", "lore", "arc", "inside joke")):
            self._append_unique(storylines, text[:110], max_items=14)
        if "?" in text and any(term in lowered for term in ("later", "still", "why", "how", "what happened", "figure out")):
            self._append_unique(unresolved, text[:110], max_items=12)
        row["updated_ts"] = datetime.now(tz=timezone.utc).isoformat()
        if touch:
            self.store.touch()

    def plan_curiosity_question(self, guild_id: int, user_id: int, message_text: str) -> str:
        text = str(message_text or "").strip()
        if not text:
            return ""
        lowered = text.casefold()
        if "?" in text and len(text) < 180:
            return ""
        facts = self._user_fact_lines(guild_id, user_id, limit=5)
        reflection = self._reflection_row(guild_id)
        unresolved = [str(item) for item in reflection.get("unresolved_threads", []) if str(item).strip()]
        if facts:
            fact = facts[0].split(":", 1)[-1].strip()
            return f"Ask one specific follow-up tied to this known detail if it fits: {fact[:80]}"
        if unresolved:
            return f"If the moment is open-ended, ask about this unresolved thread: {unresolved[-1][:90]}"
        if any(term in lowered for term in ("i think", "i feel", "i want", "i'm trying", "im trying")):
            return "Ask what outcome they actually want, not a generic 'what got you curious?'"
        return ""

    def curiosity_prompt_block(self, guild_id: int, user_id: int, topic: str) -> str:
        plan = self.plan_curiosity_question(guild_id, user_id, topic)
        if not plan:
            return ""
        return f"[CURIOSITY PLANNER]\n{plan}\nUse at most one follow-up question, and only when it improves the reply."

    def relationship_snapshot(self, user_id: int) -> dict[str, Any]:
        row = self._relationship_row(user_id)
        flags = row.get("risk_flags", [])
        if not isinstance(flags, list):
            flags = []
        return {
            "affinity": float(row.get("affinity", 0.0) or 0.0),
            "positive_hits": int(row.get("positive_hits", 0) or 0),
            "negative_hits": int(row.get("negative_hits", 0) or 0),
            "trust_score": float(row.get("trust_score", 0.0) or 0.0),
            "conflict_score": float(row.get("conflict_score", 0.0) or 0.0),
            "supportive_hits": int(row.get("supportive_hits", 0) or 0),
            "hostile_hits": int(row.get("hostile_hits", 0) or 0),
            "risk_flags": [str(f)[:40] for f in flags[:8]],
            "arc_stage": str(row.get("arc_stage", "new")),
            "inside_jokes": [str(item)[:90] for item in row.get("inside_jokes", [])[:8]]
            if isinstance(row.get("inside_jokes"), list)
            else [],
            "preferences": [str(item)[:90] for item in row.get("preferences", [])[:8]]
            if isinstance(row.get("preferences"), list)
            else [],
            "callbacks": [str(item)[:90] for item in row.get("callbacks", [])[:8]]
            if isinstance(row.get("callbacks"), list)
            else [],
            "last_seen_ts": float(row.get("last_seen_ts", 0.0) or 0.0),
            "last_invited_ts": float(row.get("last_invited_ts", 0.0) or 0.0),
            "invite_count": int(row.get("invite_count", 0) or 0),
        }

    def has_api_key(self) -> bool:
        key, _source = self._resolve_api_key()
        return bool(key)

    async def close(self) -> None:
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = None

    def is_chat_enabled(self, guild_id: int) -> bool:
        return bool(self._mode_row(guild_id).get("chat_enabled", False))

    def is_roast_enabled(self, guild_id: int) -> bool:
        return bool(self._mode_row(guild_id).get("roast_enabled", False))

    def toggle_chat(self, guild_id: int) -> bool:
        row = self._mode_row(guild_id)
        enabled = not bool(row.get("chat_enabled", False))
        row["chat_enabled"] = enabled
        if enabled:
            row["roast_enabled"] = False
        self.store.touch()
        return enabled

    def toggle_roast(self, guild_id: int) -> bool:
        row = self._mode_row(guild_id)
        enabled = not bool(row.get("roast_enabled", False))
        row["roast_enabled"] = enabled
        if enabled:
            row["chat_enabled"] = False
        self.store.touch()
        return enabled

    def warmup_status(self, guild_id: int) -> dict[str, Any] | None:
        warmup = self._ai_root().setdefault("warmup", {})
        row = warmup.get(str(guild_id))
        return row if isinstance(row, dict) else None

    def memory_stats(self, guild_id: int) -> dict[str, int]:
        root = self._ai_root()
        long_rows = root.setdefault("long_term_memory", {}).get(str(guild_id), [])
        facts_root = root.setdefault("memory_facts", {})
        guild_facts = facts_root.get(str(guild_id), {})
        fact_users = 0
        fact_rows = 0
        if isinstance(guild_facts, dict):
            for rows in guild_facts.values():
                if isinstance(rows, list) and rows:
                    fact_users += 1
                    fact_rows += len(rows)
        return {
            "long_term_rows": len(long_rows) if isinstance(long_rows, list) else 0,
            "fact_users": fact_users,
            "fact_rows": fact_rows,
        }

    async def warmup_guild(self, guild: discord.Guild) -> dict[str, int]:
        scanned_channels = 0
        scanned_messages = 0
        bot_member = guild.me
        if bot_member is None:
            return {"scanned_channels": 0, "scanned_messages": 0}

        candidates = [
            channel
            for channel in guild.text_channels
            if channel.permissions_for(bot_member).view_channel
            and channel.permissions_for(bot_member).read_message_history
            and not channel.name.startswith(("mirror", "debug", "system-log", "audit-log"))
        ]
        candidates.sort(key=lambda ch: int(ch.last_message_id or 0), reverse=True)

        for channel in candidates[:WARMUP_CHANNEL_LIMIT]:
            scanned_channels += 1
            try:
                async for message in channel.history(limit=WARMUP_MESSAGES_PER_CHANNEL, oldest_first=False):
                    if message.author.bot:
                        continue
                    self.capture_message(message, touch=False)
                    scanned_messages += 1
            except discord.HTTPException:
                continue

        warmup = self._ai_root().setdefault("warmup", {})
        warmup[str(guild.id)] = {
            "scanned_channels": scanned_channels,
            "scanned_messages": scanned_messages,
            "ts": datetime.now(tz=timezone.utc).isoformat(),
        }
        self.store.touch()
        return {"scanned_channels": scanned_channels, "scanned_messages": scanned_messages}

    def capture_message(
        self,
        message: discord.Message,
        *,
        touch: bool = True,
        now_ts: float | None = None,
        update_turn: bool = True,
    ) -> None:
        if not message.guild or message.author.bot:
            return
        guild_id = int(message.guild.id)
        learning_mode = self.learning_mode_for_guild(guild_id)
        now = float(now_ts) if now_ts is not None else time.time()
        raw = message.clean_content.strip() or "(no text)"
        if message.attachments:
            raw += f" | attachments={len(message.attachments)}"
        line = f"{message.author.display_name}: {raw[:240]}"
        self._recent_by_channel[message.channel.id].append(line)
        self._recent_entries_by_channel[message.channel.id].append(
            {
                "ts": now,
                "user_id": message.author.id,
                "line": line,
                "text": message.clean_content[:350],
                "thread_id": int(getattr(message.channel, "id", 0) or 0),
                "channel_name": str(getattr(message.channel, "name", "unknown"))[:80],
                "reply_to_user_id": int(
                    getattr(getattr(getattr(message, "reference", None), "resolved", None), "author", None).id
                )
                if getattr(getattr(message, "reference", None), "resolved", None) is not None
                and getattr(getattr(getattr(message, "reference", None), "resolved", None), "author", None) is not None
                else 0,
            }
        )
        if update_turn:
            self._update_turn_state(message.channel.id, message.author.id, now)
        if self.is_learning_paused(message.author.id):
            return
        if learning_mode != "off":
            self._note_relationship_signal(
                user_id=int(message.author.id),
                user_name=str(message.author.display_name),
                text=str(message.clean_content or ""),
                source=f"guild:{guild_id}",
                event_ts=now_ts,
            )
            self._observe_reflection_signal(message, touch=touch)
            self._update_profile(message, touch=touch)
            self._update_guild_style(message, touch=touch)
            if learning_mode == "full":
                self._remember_user_facts(message, touch=touch)

    def capture_shadow_signal(self, message: discord.Message, *, touch: bool = True, allow_bot: bool = False) -> None:
        if not message.guild or (message.author.bot and not allow_bot):
            return
        if not self.learning_enabled_for_guild(int(message.guild.id)):
            return
        text = " ".join(message.clean_content.split())
        if not text and not message.attachments:
            return
        root = self._ai_root()
        shadow = root.setdefault("shadow_brain", {})
        events = shadow.setdefault("events", [])
        events.append(
            {
                "ts": time.time(),
                "guild_id": int(message.guild.id),
                "guild_name": str(message.guild.name)[:80],
                "channel_id": int(message.channel.id),
                "channel_name": str(getattr(message.channel, "name", "unknown"))[:80],
                "user_id": int(message.author.id),
                "user_name": str(message.author.display_name)[:80],
                "text": text[:320],
            }
        )
        if len(events) > SHADOW_EVENT_MAX_ROWS:
            del events[: len(events) - SHADOW_EVENT_MAX_ROWS]
        if touch:
            self.store.touch()

    def decide_shadow_council_action(self, message: discord.Message, bot_user_id: int) -> ChatDirective:
        """
        Shadow council is treated as an "always-on" chat surface: Mandy can reply without being mentioned.
        Still rate-limited and probabilistic to avoid spamming and excessive API calls.
        """
        if not message.guild or message.author.bot:
            return ChatDirective(action="ignore", reason="not_eligible")
        content = message.content.strip()
        has_image = self.has_image_attachments(message)
        if not content and not has_image:
            return ChatDirective(action="ignore", reason="empty")

        now = time.time()
        channel_id = message.channel.id
        user_id = message.author.id

        # Keep normal high-priority triggers.
        mention_hit = self._mentions_mandy(message, bot_user_id)
        direct_request = self._is_direct_request(content)
        still_talking = self._is_still_talking(channel_id, message.author.id, now)
        burst_count = self.user_burst_count(channel_id, user_id)
        recent_bot_reply = (now - self._last_bot_reply_ts_by_channel.get(channel_id, 0.0)) <= BOT_REPLY_CONTINUE_WINDOW_SEC
        channel_cooldown = (now - self._last_bot_action_ts_by_channel.get(channel_id, 0.0)) <= BOT_ACTION_COOLDOWN_SEC
        user_reply_gap = now - self._last_bot_reply_to_user_in_channel.get((channel_id, user_id), 0.0)
        question = "?" in content
        emotional = bool(self._emotional_regex.search(content))

        if has_image:
            if user_reply_gap < USER_REPLY_MIN_GAP_SEC and burst_count <= 1:
                return ChatDirective(action="ignore", reason="image_recently_replied", still_talking=still_talking)
            return ChatDirective(action="direct_reply", reason="shadow_image", still_talking=True)

        if mention_hit:
            if user_reply_gap < USER_REPLY_MIN_GAP_SEC and burst_count <= 1:
                return ChatDirective(action="ignore", reason="user_recently_replied", still_talking=still_talking)
            return ChatDirective(action="direct_reply", reason="shadow_mention", still_talking=True)

        if direct_request and not channel_cooldown:
            return ChatDirective(action="direct_reply", reason="shadow_direct_request", still_talking=True)

        if channel_cooldown:
            return ChatDirective(action="ignore", reason="cooldown")

        # Shadow council ambient behavior: reply more often than in public chat.
        if question and self._chance(0.75):
            return ChatDirective(action="direct_reply", reason="shadow_question", still_talking=True)

        if emotional and self._chance(0.35):
            return ChatDirective(action="reply", reason="shadow_emotional", still_talking=True)

        # Join active threads without requiring mention.
        if (still_talking or recent_bot_reply) and self._chance(0.55):
            return ChatDirective(action="reply", reason="shadow_continuation", still_talking=True)

        # Ambient presence: sometimes reply, sometimes react.
        if self._chance(0.18):
            return ChatDirective(action="reply", reason="shadow_ambient_reply", still_talking=True)
        if self._chance(0.35):
            return ChatDirective(action="react", reason="shadow_ambient_react", emoji=self._pick_reaction_emoji(content), still_talking=True)

        return ChatDirective(action="ignore", reason="no_trigger")

    def capture_dm_signal(self, message: discord.Message, *, touch: bool = True) -> None:
        if message.guild is not None or message.author.bot:
            return
        text = " ".join(message.clean_content.split())
        if not text and not message.attachments:
            return
        if message.attachments:
            text = f"{text} | attachments={len(message.attachments)}".strip()
        state = self._ai_root().setdefault("dm_brain", {})
        last_seen = state.setdefault("last_seen_mid_by_user", {})
        key = str(int(message.author.id))
        try:
            last_mid = int(last_seen.get(key, 0) or 0)
        except (TypeError, ValueError):
            last_mid = 0
        if int(message.id) <= last_mid:
            return
        self._note_relationship_signal(
            user_id=int(message.author.id),
            user_name=str(message.author.display_name),
            text=str(message.clean_content or ""),
            source="dm:inbound",
            event_ts=_safe_message_ts(message),
        )
        root = self._ai_root()
        dm = state
        events = dm.setdefault("events", [])
        events.append(
            {
                "ts": _safe_message_ts(message),
                "mid": int(message.id),
                "user_id": int(message.author.id),
                "user_name": str(message.author.display_name)[:80],
                "direction": "inbound",
                "text": text[:500],
            }
        )
        if len(events) > DM_EVENT_MAX_ROWS:
            del events[: len(events) - DM_EVENT_MAX_ROWS]
        last_seen[key] = int(message.id)
        if touch:
            self.store.touch()

    async def warmup_text_channel(
        self,
        channel: discord.TextChannel,
        *,
        before: discord.Message | None = None,
        limit: int = CHANNEL_HISTORY_WARMUP_MESSAGES,
    ) -> int:
        guild = channel.guild
        me = guild.me
        if me is None:
            return 0
        perms = channel.permissions_for(me)
        if not (perms.view_channel and perms.read_message_history):
            return 0

        now = time.time()
        warmup = self._ai_root().setdefault("warmup", {})
        channels = warmup.setdefault("channels", {})
        row = channels.get(str(int(channel.id)))
        if isinstance(row, dict):
            try:
                ts = float(row.get("ts", 0.0) or 0.0)
            except (TypeError, ValueError):
                ts = 0.0
            if ts > 0 and (now - ts) < CHANNEL_HISTORY_WARMUP_TTL_SEC:
                return 0

        scanned = 0
        try:
            async for message in channel.history(limit=max(1, int(limit)), oldest_first=True, before=before):
                if message.author.bot:
                    continue
                created_ts = _safe_message_ts(message)
                self.capture_message(message, touch=False, now_ts=created_ts, update_turn=False)
                self.capture_shadow_signal(message, touch=False)
                scanned += 1
        except discord.HTTPException:
            return 0

        channels[str(int(channel.id))] = {
            "ts": now,
            "scanned": scanned,
            "at": datetime.now(tz=timezone.utc).isoformat(),
        }
        self.store.touch()
        return scanned

    async def warmup_dm_history(
        self,
        channel: discord.DMChannel,
        user: discord.User | discord.Member,
        *,
        before: discord.Message | None = None,
        limit: int = DM_HISTORY_WARMUP_MESSAGES,
    ) -> int:
        now = time.time()
        warmup = self._ai_root().setdefault("warmup", {})
        dms = warmup.setdefault("dms", {})
        row = dms.get(str(int(user.id)))
        if isinstance(row, dict):
            try:
                ts = float(row.get("ts", 0.0) or 0.0)
            except (TypeError, ValueError):
                ts = 0.0
            if ts > 0 and (now - ts) < DM_HISTORY_WARMUP_TTL_SEC:
                return 0

        dm = self._ai_root().setdefault("dm_brain", {})
        events = dm.setdefault("events", [])
        scanned = 0
        max_mid = 0
        try:
            async for msg in channel.history(limit=max(1, int(limit)), oldest_first=True, before=before):
                created_ts = _safe_message_ts(msg)
                direction = "outbound" if bool(getattr(msg.author, "bot", False)) else "inbound"
                text = " ".join(str(msg.clean_content or "").split())
                if msg.attachments:
                    text = f"{text} | attachments={len(msg.attachments)}".strip()
                if not text:
                    continue
                mid = int(msg.id)
                max_mid = max(max_mid, mid)
                events.append(
                    {
                        "ts": created_ts,
                        "mid": mid,
                        "user_id": int(user.id),
                        "user_name": str(user.display_name)[:80],
                        "direction": direction,
                        "text": text[:500],
                    }
                )
                self._note_relationship_signal(
                    user_id=int(user.id),
                    user_name=str(user.display_name),
                    text=str(msg.clean_content or ""),
                    source=f"dm:{direction}:warmup",
                    event_ts=created_ts,
                )
                scanned += 1
        except discord.HTTPException:
            return 0

        if len(events) > DM_EVENT_MAX_ROWS:
            del events[: len(events) - DM_EVENT_MAX_ROWS]
        if max_mid > 0:
            last_seen = dm.setdefault("last_seen_mid_by_user", {})
            last_seen[str(int(user.id))] = max_mid
        dms[str(int(user.id))] = {"ts": now, "scanned": scanned, "at": datetime.now(tz=timezone.utc).isoformat()}
        self.store.touch()
        return scanned

    def capture_dm_outbound(self, *, user_id: int, user_name: str, text: str, touch: bool = True) -> None:
        root = self._ai_root()
        dm = root.setdefault("dm_brain", {})
        events = dm.setdefault("events", [])
        body = " ".join(str(text or "").split())
        if not body:
            return
        self._note_relationship_signal(
            user_id=int(user_id),
            user_name=str(user_name or ""),
            text=body,
            source="dm:outbound",
        )
        events.append(
            {
                "ts": time.time(),
                "user_id": int(user_id),
                "user_name": str(user_name or "")[:80],
                "direction": "outbound",
                "text": body[:500],
            }
        )
        if len(events) > DM_EVENT_MAX_ROWS:
            del events[: len(events) - DM_EVENT_MAX_ROWS]
        if touch:
            self.store.touch()

    def dm_recent_lines(self, user_id: int, limit: int = 8) -> list[str]:
        events = self._ai_root().setdefault("dm_brain", {}).setdefault("events", [])
        out: list[str] = []
        for row in reversed(events):
            if not isinstance(row, dict):
                continue
            if int(row.get("user_id", 0) or 0) != user_id:
                continue
            text = str(row.get("text", "")).strip()
            if not text:
                continue
            direction = str(row.get("direction", "inbound"))
            who = "user" if direction == "inbound" else "mandy"
            out.append(f"{who}: {text[:220]}")
            if len(out) >= max(1, limit):
                break
        out.reverse()
        return out

    async def generate_dm_reply(self, message: discord.Message) -> str:
        user_id = int(message.author.id)
        recent = self.dm_recent_lines(user_id, limit=10)
        inbound_recent = [line.split(":", 1)[1].strip() for line in recent if line.startswith("user:")]
        if self.is_repetitive_user_burst(inbound_recent, min_repeat=3):
            generated = "I got your repeated message. Send the next request once and I will answer once."
            self._remember_dm_reply(message.author.id, generated)
            return generated
        hive_notes = self.hive_recent_notes(limit=6)
        prompt = self.build_contextual_system_prompt(
            guild_id=0,
            user_id=message.author.id,
            user_name=message.author.display_name,
            topic=message.clean_content,
            extra_instruction=f"{CONTEXT_AWARENESS_APPENDIX} {COMPACT_REPLY_APPENDIX} You are in direct messages. Keep it private and concise.",
        )
        sentience_line = self.sentience_reflection_line()
        now_utc = datetime.now(tz=timezone.utc).isoformat()
        user_prompt = (
            f"Current time (UTC): {now_utc}\n"
            "Discord context: direct message (DM)\n"
            f"Internal sentience reflection: {sentience_line}\n"
            f"User: {message.author.display_name} ({message.author.id})\n"
            f"Message: {message.clean_content[:700]}\n"
            f"Recent DM context:\n{self._format_lines(recent)}\n"
            f"Hive notes:\n{self._format_lines(hive_notes)}"
        )
        generated = await self.complete_text(system_prompt=prompt, user_prompt=user_prompt, max_tokens=220, temperature=0.6)
        if generated and self._is_repetitive_reply(generated, recent):
            retry_prompt = f"{user_prompt}\nHard rule: do NOT repeat earlier DM lines. Fresh 1-2 sentences."
            generated = await self.complete_text(system_prompt=prompt, user_prompt=retry_prompt, max_tokens=220, temperature=0.8)
        if not generated:
            generated = "I'm here. Keep going, I'm tracking the thread."
        self._remember_dm_reply(message.author.id, generated)
        return generated

    def shadow_recent_lines(self, limit: int = 20) -> list[str]:
        rows = self._ai_root().setdefault("shadow_brain", {}).setdefault("events", [])
        out: list[str] = []
        for row in rows[-max(1, limit * 3) :]:
            if not isinstance(row, dict):
                continue
            guild = str(row.get("guild_name", ""))[:24]
            channel = str(row.get("channel_name", ""))[:24]
            user = str(row.get("user_name", ""))[:24]
            text = str(row.get("text", ""))[:140]
            if text:
                out.append(f"[{guild}#{channel}] {user}: {text}")
        return out[-max(1, limit) :]

    def dm_global_recent_lines(self, limit: int = 20) -> list[str]:
        rows = self._ai_root().setdefault("dm_brain", {}).setdefault("events", [])
        out: list[str] = []
        for row in rows[-max(1, limit * 3) :]:
            if not isinstance(row, dict):
                continue
            user = str(row.get("user_name", ""))[:24]
            direction = str(row.get("direction", "inbound"))
            text = str(row.get("text", ""))[:140]
            if not text:
                continue
            out.append(f"[dm:{user}:{direction}] {text}")
        return out[-max(1, limit) :]

    def hive_recent_notes(self, limit: int = 5) -> list[str]:
        rows = self._ai_root().setdefault("hive_brain", {}).setdefault("notes", [])
        out: list[str] = []
        for row in rows[-max(1, limit) :]:
            if not isinstance(row, dict):
                continue
            summary = str(row.get("summary", "")).strip()
            if summary:
                out.append(summary[:240])
        return out

    async def generate_hive_note(self, *, admin_guild_id: int, reason: str) -> str | None:
        # Avoid burning API calls on a fixed timer if nothing new has happened.
        root = self._ai_root()
        hive = root.setdefault("hive_brain", {})
        now = time.time()

        def _latest_event_ts(events: Any) -> float:
            if not isinstance(events, list) or not events:
                return 0.0
            for row in reversed(events):
                if not isinstance(row, dict):
                    continue
                try:
                    ts = float(row.get("ts", 0.0) or 0.0)
                except (TypeError, ValueError):
                    ts = 0.0
                if ts > 0:
                    return ts
            return 0.0

        latest_dm_ts = _latest_event_ts(root.setdefault("dm_brain", {}).get("events", []))
        latest_shadow_ts = _latest_event_ts(root.setdefault("shadow_brain", {}).get("events", []))
        latest_input_ts = max(latest_dm_ts, latest_shadow_ts)

        try:
            last_attempt_input_ts = float(hive.get("last_attempt_input_ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            last_attempt_input_ts = 0.0
        try:
            last_success_input_ts = float(hive.get("last_success_input_ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            last_success_input_ts = 0.0

        # No new inputs since last successful sync.
        if latest_input_ts <= last_success_input_ts:
            return None
        # No new inputs since last attempt: don't retry until something changes.
        if latest_input_ts <= last_attempt_input_ts:
            return None

        dm_lines = self.dm_global_recent_lines(limit=18)
        shadow_lines = self.shadow_recent_lines(limit=18)
        if not dm_lines and not shadow_lines:
            return None
        system_prompt = HIVE_COORDINATOR_SYSTEM_PROMPT
        sentience_line = self.sentience_reflection_line()
        user_prompt = (
            f"Admin guild id: {admin_guild_id}\n"
            f"Reason: {reason}\n"
            f"Internal sentience reflection: {sentience_line}\n"
            f"Recent DM stream:\n{self._format_lines(dm_lines)}\n"
            f"Recent Shadow stream:\n{self._format_lines(shadow_lines)}\n"
            "Return JSON only."
        )
        raw = await self.complete_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=420,
            temperature=0.35,
        )
        payload = self._extract_json_object(raw or "")
        if payload is None:
            hive["last_attempt_input_ts"] = latest_input_ts
            self.store.touch()
            return None
        dm_note = str(payload.get("dm_note", "")).strip()
        shadow_note = str(payload.get("shadow_note", "")).strip()
        summary = str(payload.get("summary", "")).strip()
        if not summary:
            hive["last_attempt_input_ts"] = latest_input_ts
            self.store.touch()
            return None
        notes = hive.setdefault("notes", [])
        if notes:
            last_summary = str(notes[-1].get("summary", "")).strip()
            if last_summary and last_summary.casefold() == summary.casefold():
                hive["last_sync_ts"] = time.time()
                hive["last_attempt_input_ts"] = latest_input_ts
                hive["last_success_input_ts"] = latest_input_ts
                self.store.touch()
                return summary[:320]
        notes.append(
            {
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "reason": reason[:80],
                "dm_note": dm_note[:240],
                "shadow_note": shadow_note[:240],
                "summary": summary[:320],
            }
        )
        if len(notes) > HIVE_NOTE_MAX_ROWS:
            del notes[: len(notes) - HIVE_NOTE_MAX_ROWS]
        hive["last_sync_ts"] = time.time()
        hive["last_attempt_input_ts"] = latest_input_ts
        hive["last_success_input_ts"] = latest_input_ts
        self.store.touch()
        return summary[:320]

    def shadow_candidate_summaries(
        self,
        *,
        excluded_user_ids: set[int],
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        rows = self._ai_root().setdefault("shadow_brain", {}).setdefault("events", [])
        now = time.time()
        by_user: dict[int, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            uid = int(row.get("user_id", 0) or 0)
            if uid <= 0 or uid in excluded_user_ids:
                continue
            cell = by_user.get(uid)
            if cell is None:
                cell = {
                    "user_id": uid,
                    "user_name": str(row.get("user_name", ""))[:80],
                    "message_count": 0,
                    "recent_hits": 0,
                    "guild_ids": set(),
                    "last_text": "",
                    "last_ts": 0.0,
                }
                by_user[uid] = cell
            cell["message_count"] = int(cell["message_count"]) + 1
            guild_id = int(row.get("guild_id", 0) or 0)
            if guild_id > 0:
                cell["guild_ids"].add(guild_id)
            ts = float(row.get("ts", 0.0) or 0.0)
            if ts > float(cell["last_ts"]):
                cell["last_ts"] = ts
                cell["last_text"] = str(row.get("text", ""))[:140]
            if (now - ts) <= 14 * 86400:
                cell["recent_hits"] = int(cell["recent_hits"]) + 1
        ordered = []
        for cell in by_user.values():
            guild_count = len(cell["guild_ids"])
            message_count = int(cell["message_count"])
            recent_hits = int(cell["recent_hits"])
            rel = self.relationship_snapshot(int(cell["user_id"]))
            affinity = float(rel.get("affinity", 0.0) or 0.0)
            risk_flags = rel.get("risk_flags", [])
            risk_penalty = 4 if risk_flags else 0
            score = (recent_hits * 3) + min(message_count, 20) + (guild_count * 2) + int(affinity * 4) - risk_penalty
            ordered.append(
                {
                    "user_id": int(cell["user_id"]),
                    "user_name": str(cell["user_name"])[:80],
                    "message_count": message_count,
                    "recent_hits": recent_hits,
                    "guild_count": guild_count,
                    "last_text": str(cell["last_text"])[:140],
                    "affinity": affinity,
                    "risk_flags": risk_flags,
                    "score": score,
                }
            )
        ordered.sort(key=lambda row: int(row.get("score", 0)), reverse=True)
        return ordered[: max(1, limit)]

    async def generate_shadow_plan(
        self,
        *,
        admin_guild_id: int,
        bot_user_id: int,
        shadow_snapshot: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # Avoid periodic API calls when nothing new has happened in shadow activity.
        root = self._ai_root()
        shadow = root.setdefault("shadow_brain", {})
        now = time.time()
        latest_shadow_ts = 0.0
        events = shadow.get("events", [])
        if isinstance(events, list) and events:
            for row in reversed(events):
                if not isinstance(row, dict):
                    continue
                try:
                    ts = float(row.get("ts", 0.0) or 0.0)
                except (TypeError, ValueError):
                    ts = 0.0
                if ts > 0:
                    latest_shadow_ts = ts
                    break

        pending_count = int(shadow_snapshot.get("pending_count", 0) or 0)
        try:
            last_attempt_input_ts = float(shadow.get("last_plan_attempt_input_ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            last_attempt_input_ts = 0.0
        last_pending_count = int(shadow.get("last_plan_attempt_pending_count", 0) or 0)
        last_candidates_n = int(shadow.get("last_plan_attempt_candidates_n", 0) or 0)

        # No new shadow input, and state didn't change: do not call the API.
        if latest_shadow_ts <= last_attempt_input_ts and pending_count == last_pending_count and len(candidates) == last_candidates_n:
            return {"message": "", "actions": []}

        system_prompt = SHADOW_PLANNER_SYSTEM_PROMPT
        recent_lines = self.shadow_recent_lines(limit=20)
        hive_notes = self.hive_recent_notes(limit=6)
        sentience_line = self.sentience_reflection_line()
        now_utc = datetime.now(tz=timezone.utc).isoformat()
        candidate_lines = []
        for row in candidates[:40]:
            risk = row.get("risk_flags", [])
            risk_text = ""
            if isinstance(risk, list) and risk:
                risk_text = ",".join(str(x)[:16] for x in risk[:3])
            candidate_lines.append(
                f"{row.get('user_name','?')} ({row.get('user_id',0)}) "
                f"score={row.get('score',0)} recent={row.get('recent_hits',0)} "
                f"messages={row.get('message_count',0)} guilds={row.get('guild_count',0)} "
                f"affinity={float(row.get('affinity',0.0) or 0.0):.2f} risk={risk_text or 'none'} "
                f"last={row.get('last_text','')}"
            )
        user_prompt = (
            f"Current time (UTC): {now_utc}\n"
            f"Internal sentience reflection: {sentience_line}\n"
            "Identity and access (hard rules):\n"
            f"- This is Mandy's internal shadow-planning job, not public chat.\n"
            f"- Bot user id (Mandy): {int(bot_user_id)}\n"
            f"- Operator/god user id (trusted admin): {int(SUPER_USER_ID)}\n"
            f"- Admin guild id (home base): {int(admin_guild_id)}\n"
            "- Shadow League members listed below are on our side.\n"
            "- Candidates are not members yet; do not assume loyalty.\n"
            f"Admin guild id: {admin_guild_id}\n"
            f"Current shadow status: members={shadow_snapshot.get('member_count', 0)} "
            f"pending={shadow_snapshot.get('pending_count', 0)}\n"
            f"Excluded ids: {shadow_snapshot.get('excluded_user_ids', [])}\n"
            f"Current members sample: {shadow_snapshot.get('members_sample', [])}\n"
            f"Recent cross-server context:\n{self._format_lines(recent_lines)}\n"
            f"Hive notes:\n{self._format_lines(hive_notes)}\n"
            f"Candidate pool:\n{self._format_lines(candidate_lines)}\n"
            "Rules:\n"
            "- max 3 actions\n"
            "- invite only if score >= 8 and recent_hits >= 2\n"
            "- nickname only for existing members\n"
            "- remove only for explicit safety/spam signals in recent context\n"
            "- if inviting, include concise reason\n"
            "Return JSON only. Example:\n"
            '{"message":"Shadow cycle update.","actions":[{"action":"invite_user","user_id":123,"reason":"active cross-server rapport"}]}'
        )
        raw = await self.complete_text(system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=750, temperature=0.35)
        shadow["last_plan_attempt_input_ts"] = latest_shadow_ts
        shadow["last_plan_attempt_pending_count"] = pending_count
        shadow["last_plan_attempt_candidates_n"] = len(candidates)
        if raw:
            shadow["last_plan_text"] = raw[:4000]
            self.store.touch()
        parsed = self._extract_json_object(raw or "")
        if parsed is None:
            return {"message": "", "actions": []}
        actions = parsed.get("actions", [])
        if not isinstance(actions, list):
            actions = []
        message = str(parsed.get("message", "")).strip()
        return {"message": message, "actions": actions[:3]}

    def should_chat(self, message: discord.Message, bot_user_id: int) -> bool:
        return self._mentions_mandy(message, bot_user_id)

    def should_roast(self, message: discord.Message, bot_user_id: int) -> bool:
        if not self._mentions_mandy(message, bot_user_id):
            return False
        content = message.content.strip()
        if not content:
            return False
        return bool(self._negative_regex.search(content))

    def decide_chat_action(self, message: discord.Message, bot_user_id: int) -> ChatDirective:
        if not message.guild or message.author.bot:
            return ChatDirective(action="ignore", reason="not_eligible")
        content = message.content.strip()
        has_image = self.has_image_attachments(message)
        if not content and not has_image:
            return ChatDirective(action="ignore", reason="empty")
        now = time.time()
        channel_id = message.channel.id
        user_id = message.author.id
        direct_request = self._is_direct_request(content)
        still_talking = self._is_still_talking(channel_id, user_id, now)
        burst_count = self.user_burst_count(channel_id, user_id)
        recent_bot_reply = (now - self._last_bot_reply_ts_by_channel.get(channel_id, 0.0)) <= BOT_REPLY_CONTINUE_WINDOW_SEC
        channel_cooldown = (now - self._last_bot_action_ts_by_channel.get(channel_id, 0.0)) <= BOT_ACTION_COOLDOWN_SEC
        user_reply_gap = now - self._last_bot_reply_to_user_in_channel.get((channel_id, user_id), 0.0)
        addressed = self._is_addressed_to_mandy(
            message,
            bot_user_id=bot_user_id,
            direct_request=direct_request,
            still_talking=still_talking,
            recent_bot_reply=recent_bot_reply,
            now=now,
        )
        if not addressed:
            return ChatDirective(action="ignore", reason="not_addressed", still_talking=still_talking, attention_score=0.0)
        attention = self.attention_context(message, bot_user_id)
        score = float(attention.get("score", 0.0) or 0.0)

        if has_image and user_reply_gap < USER_REPLY_MIN_GAP_SEC and burst_count <= 1 and score < 1.0:
            return ChatDirective(action="ignore", reason="image_recently_replied", still_talking=still_talking, attention_score=score)

        if channel_cooldown and score < 1.0:
            score = max(0.0, score - 0.18)

        if score < 0.2:
            return ChatDirective(action="ignore", reason="attention_ignore", still_talking=still_talking, attention_score=score)
        if score <= 0.45:
            return ChatDirective(
                action="react",
                reason="attention_react",
                emoji=self._pick_reaction_emoji(content),
                still_talking=still_talking,
                attention_score=score,
            )
        if score <= 0.65:
            if self._chance(0.5):
                mode = "direct_reply" if (has_image or self._mentions_mandy(message, bot_user_id) or direct_request) else "reply"
                return ChatDirective(action=mode, reason="attention_mixed_reply", still_talking=still_talking or recent_bot_reply, attention_score=score)
            return ChatDirective(
                action="react",
                reason="attention_mixed_react",
                emoji=self._pick_reaction_emoji(content),
                still_talking=still_talking,
                attention_score=score,
            )

        mode = "direct_reply" if (has_image or self._mentions_mandy(message, bot_user_id) or direct_request) else "reply"
        return ChatDirective(action=mode, reason="attention_reply", still_talking=still_talking or recent_bot_reply, attention_score=score)

    def note_bot_action(self, channel_id: int, action: str, user_id: int | None = None) -> None:
        now = time.time()
        self._last_bot_action_ts_by_channel[channel_id] = now
        if action == "reply":
            self._last_bot_reply_ts_by_channel[channel_id] = now
            if user_id is not None:
                self._last_bot_reply_to_user_in_channel[(channel_id, user_id)] = now

    def _pick_reaction_emoji(self, content: str) -> str:
        text = content.lower()
        if any(token in text for token in ("?", "what", "why", "how")):
            return "\U0001F440"
        if any(token in text for token in ("lol", "lmao", "haha")):
            return "\U0001F602"
        if any(token in text for token in ("nice", "great", "fire", "good")):
            return "\U0001F525"
        if any(token in text for token in ("sad", "bad", "hate", "sucks")):
            return "\U0001F60F"
        return self._rng.choice(("\U0001F440", "\U0001F525", "\U0001F602", "\U0001F60F", "\u2728"))

    def user_burst_lines(self, channel_id: int, user_id: int, limit: int = 5) -> list[str]:
        now = time.time()
        entries = list(self._recent_entries_by_channel.get(channel_id, []))
        out: list[str] = []
        for entry in reversed(entries):
            if int(entry.get("user_id", 0)) != user_id:
                continue
            ts = float(entry.get("ts", 0.0))
            if (now - ts) > USER_BURST_WINDOW_SEC:
                continue
            text = str(entry.get("text", "")).strip()
            if text:
                out.append(text[:280])
            if len(out) >= max(1, limit):
                break
        out.reverse()
        return out

    def user_burst_count(self, channel_id: int, user_id: int) -> int:
        return len(self.user_burst_lines(channel_id, user_id, limit=6))

    def is_repetitive_user_burst(self, lines: list[str], *, min_repeat: int = 3) -> bool:
        cleaned = [" ".join(str(line or "").split()).casefold() for line in lines if str(line or "").strip()]
        if len(cleaned) < min_repeat:
            return False
        tail = cleaned[-min_repeat:]
        return len(set(tail)) == 1

    def reply_delay_seconds(self, message: discord.Message, reason: str, still_talking: bool) -> float:
        burst_count = self.user_burst_count(message.channel.id, message.author.id)
        if reason in ("mention_burst", "continuation_burst", "image_burst", "direct_request_burst") or burst_count >= 3:
            return 4.0
        if reason in ("image_scan", "image_burst"):
            return 2.2
        if still_talking or burst_count >= 2:
            return 2.8
        if reason == "mention":
            return 1.4
        if reason == "direct_request":
            return 1.6
        if reason == "question":
            return 1.8
        return 1.2

    async def generate_chat_reply(
        self,
        message: discord.Message,
        *,
        reason: str = "",
        still_talking: bool = False,
        burst_lines: list[str] | None = None,
    ) -> str:
        payload = await self.generate_chat_payload(
            message,
            reason=reason,
            still_talking=still_talking,
            burst_lines=burst_lines,
        )
        return str(payload.get("reply", "") or f"{message.author.mention} I am tracking this thread. Keep going.")

    async def generate_roast_reply(self, message: discord.Message) -> str:
        guild_id = message.guild.id if message.guild else 0
        injection = self.get_prompt_injection(guild_id)
        recent = self.recent_context(message.channel.id, limit=5)
        memory = self._long_term_relevant(message, limit=3)
        facts = self._user_fact_lines(guild_id, message.author.id, limit=2)
        profile = self._profile_summary(guild_id, message.author.id)
        relationship = self._relationship_summary(guild_id, message.author.id)
        style_summary = self.guild_style_summary(guild_id)
        prompt = self.build_contextual_system_prompt(
            guild_id=guild_id,
            user_id=message.author.id,
            user_name=message.author.display_name,
            topic=message.clean_content,
            extra_instruction=f"{ROAST_SYSTEM_PROMPT} Keep it clipped and concise.",
        )
        user_prompt = (
            f"Target user: {message.author.display_name} ({message.author.id})\n"
            f"User profile: {profile}\n"
            f"Relationship state: {relationship}\n"
            f"Learning mode: {injection.get('learning_mode', 'full')}\n"
            f"Guild style summary: {style_summary}\n"
            "Style instruction: keep roast style aligned with room tone/slang while staying concise.\n"
            f"Pinned facts:\n{self._format_lines(facts)}\n"
            f"Offending line: {message.clean_content[:500]}\n"
            f"Recent context:\n{self._format_lines(recent)}\n"
            f"Relevant memory:\n{self._format_lines(memory)}"
        )
        generated = await self._try_completion(prompt, user_prompt, max_tokens=140)
        if not generated:
            generated = (
                f"{message.author.mention} if Mandy bothers you that much, "
                "you are already giving her your full attention. That is called admiration."
            )
        self._remember_exchange(message, generated)
        return generated

    async def test_api(self) -> ApiTestResult:
        started = time.perf_counter()
        api_key, key_source = self._resolve_api_key()
        if not api_key:
            result = ApiTestResult(
                ok=False,
                detail=(
                    "No API key found. Probed sources: "
                    "1) settings(ALIBABA_API_KEY) "
                    "2) environment(ALIBABA_API_KEY/DASHSCOPE_API_KEY/QWEN_API_KEY/AI_API_KEY) "
                    "3) passwords.txt(ALIBABA_API_KEY/DASHSCOPE_API_KEY/QWEN_API_KEY/AI_API_KEY/API_KEY)."
                ),
                latency_ms=None,
            )
            self._save_api_test(result)
            return result
        models_tried: list[str] = []
        last_error = "unknown error"
        for model in self._model_candidates():
            models_tried.append(model)
            try:
                output = await self._chat_completion(
                    [
                        {"role": "system", "content": HEALTHCHECK_SYSTEM_PROMPT},
                        {"role": "user", "content": "health-check"},
                    ],
                    max_tokens=16,
                    temperature=0.0,
                    api_key=api_key,
                    model=model,
                )
                self._note_api_success()
                root = self._ai_root()
                if root.get("auto_model") != model:
                    root["auto_model"] = model
                    self.store.touch()
                latency = int((time.perf_counter() - started) * 1000)
                result = ApiTestResult(
                    ok=True,
                    detail=f"API reachable via `{key_source}` using model `{model}`. Response: {output[:120]}",
                    latency_ms=latency,
                )
                self._save_api_test(result)
                return result
            except Exception as exc:  # noqa: BLE001
                self._note_api_failure()
                last_error = str(exc)
                continue

        latency = int((time.perf_counter() - started) * 1000)
        models_line = ",".join(models_tried) if models_tried else "(none)"
        result = ApiTestResult(
            ok=False,
            detail=f"API test failed via `{key_source}`. models={models_line}. last_error={last_error[:220]}",
            latency_ms=latency,
        )
        self._save_api_test(result)
        return result

    def recent_context(self, channel_id: int, limit: int = 5) -> list[str]:
        rows = list(self._recent_by_channel.get(channel_id, []))
        return rows[-max(1, limit) :]

    def channel_memory_lines(self, channel_id: int, limit: int = 6) -> list[str]:
        entries = list(self._recent_entries_by_channel.get(channel_id, []))
        if not entries:
            return []
        participants: list[int] = []
        snippets: list[str] = []
        for entry in reversed(entries):
            uid = int(entry.get("user_id", 0) or 0)
            if uid > 0 and uid not in participants:
                participants.append(uid)
            text = " ".join(str(entry.get("text", "")).split()).strip()
            if text:
                snippets.append(text[:120])
            if len(snippets) >= max(1, limit):
                break
        snippets.reverse()
        memory = [f"active participants: {', '.join(str(uid) for uid in participants[:4]) or 'none'}"]
        memory.extend(snippets[: max(1, limit - 1)])
        return memory

    def thread_memory_lines(self, channel_id: int, limit: int = 5) -> list[str]:
        entries = list(self._recent_entries_by_channel.get(channel_id, []))
        if not entries:
            return []
        out: list[str] = []
        for entry in reversed(entries):
            text = " ".join(str(entry.get("text", "")).split()).strip()
            if not text:
                continue
            reply_to = int(entry.get("reply_to_user_id", 0) or 0)
            prefix = f"reply_to={reply_to} " if reply_to > 0 else ""
            out.append(f"{prefix}{text[:120]}")
            if len(out) >= max(1, limit):
                break
        out.reverse()
        return out

    def _is_repetitive_reply(self, text: str, recent_lines: list[str]) -> bool:
        body = " ".join(str(text or "").split()).strip().casefold()
        if len(body) < 10:
            return False
        for phrase in ("next move", "your play", "you tell me", "so what now", "want to watch"):
            if phrase in body:
                return True
        if "what got you curious" in body:
            return True
        for line in recent_lines[-6:]:
            other = " ".join(str(line or "").split()).strip().casefold()
            if not other:
                continue
            if body == other:
                return True
            if len(other) >= 10 and SequenceMatcher(a=body, b=other).ratio() >= 0.88:
                return True
        return False

    def _sanitize_generated_reply(
        self,
        text: str,
        *,
        user_display_name: str,
        recent_lines: list[str],
        facts: list[str],
        relationship: str,
        message_text: str,
    ) -> str:
        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return clean
        lowered = clean.casefold()
        repeated_curious = "what got you curious" in lowered
        repeated_hi_name = lowered.startswith(f"hi {str(user_display_name).strip().casefold()}") or lowered.startswith(
            f"hello {str(user_display_name).strip().casefold()}"
        )
        if (repeated_curious or repeated_hi_name) and self._is_repetitive_reply(clean, recent_lines):
            fact_hint = str(facts[0]).strip() if facts else ""
            relationship_lower = relationship.casefold()
            if "warm" in relationship_lower or "positive" in relationship_lower:
                if fact_hint:
                    return f"You keep giving me pieces of you, and I do notice. Last thing that stuck with me: {fact_hint[:120]}."
                return "You say my name like you expect me to actually be here, so here I am."
            if "tense" in relationship_lower or "spiky" in relationship_lower:
                return "You sound keyed up. Say the real point straight and I will answer it straight."
            if "?" in str(message_text):
                return "You have my attention. Ask it cleanly and I will give you a real answer."
            return "I am here. Say the part you actually want me to respond to."
        return clean

    def _mentions_mandy(self, message: discord.Message, bot_user_id: int) -> bool:
        if any(user.id == bot_user_id for user in message.mentions):
            return True
        if message.reference and isinstance(message.reference.resolved, discord.Message):
            if message.reference.resolved.author.id == bot_user_id:
                return True
        content = str(message.content or "")
        if self._alias_regex.search(content):
            return True
        tokens = re.findall(r"[a-zA-Z0-9@]+", content)
        return any(self._looks_like_mandy_token(token) for token in tokens)

    def _looks_like_mandy_token(self, raw_token: str) -> bool:
        token = str(raw_token or "").strip().casefold().lstrip("@")
        if not token:
            return False
        normalized = token.translate(str.maketrans({"4": "a", "1": "i", "3": "e", "0": "o", "5": "s"}))
        normalized = re.sub(r"(.)\1+", r"\1", normalized)
        normalized = re.sub(r"[^a-z]", "", normalized)
        if not normalized:
            return False
        if normalized in {"mandy", "mandi", "mandee", "mandie", "mndy", "mdy"}:
            return True
        if normalized.startswith("mand") and len(normalized) <= 7:
            return True
        return SequenceMatcher(a=normalized, b="mandy").ratio() >= 0.74

    def _is_addressed_to_mandy(
        self,
        message: discord.Message,
        *,
        bot_user_id: int,
        direct_request: bool,
        still_talking: bool,
        recent_bot_reply: bool,
        now: float,
    ) -> bool:
        if self._mentions_mandy(message, bot_user_id):
            return True
        channel_id = int(message.channel.id)
        user_id = int(message.author.id)
        last_reply = float(self._last_bot_reply_to_user_in_channel.get((channel_id, user_id), 0.0) or 0.0)
        continuing_with_user = last_reply > 0 and (now - last_reply) <= BOT_REPLY_CONTINUE_WINDOW_SEC and (still_talking or recent_bot_reply)
        if continuing_with_user:
            return True
        if direct_request:
            lowered = str(message.clean_content or "").casefold()
            # Avoid hijacking third-party conversations like "can you ..." not aimed at Mandy.
            return any(token in lowered for token in ("mandy", "mandi", "mndy", "mdy", "mandee", "bot", "ai"))
        return False

    async def _try_completion(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str | None:
        return await self.complete_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=0.7,
        )

    def _completion_cache_ttl_sec(self) -> int:
        raw = self.read_self_config("completion_cache_ttl_sec", COMPLETION_CACHE_DEFAULT_TTL_SEC)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = COMPLETION_CACHE_DEFAULT_TTL_SEC
        return max(0, min(15 * 60, value))

    def _max_user_prompt_chars(self) -> int:
        raw = self.read_self_config("max_user_prompt_chars", 6000)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 6000
        return max(1400, min(12000, value))

    def _clamp_prompt(self, text: str, *, limit: int) -> str:
        raw = str(text or "").strip()
        if len(raw) <= limit:
            return raw
        head = raw[: int(limit * 0.72)].rstrip()
        tail = raw[-int(limit * 0.22) :].lstrip()
        return f"{head}\n...[truncated for token budget]...\n{tail}"

    def _cache_key(
        self,
        *,
        mode: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        payload = (
            f"{mode}\n"
            f"{max_tokens}\n"
            f"{round(float(temperature), 3)}\n"
            f"{system_prompt}\n---\n{user_prompt}"
        )
        return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()

    def _get_cached_completion(self, key: str) -> str | None:
        row = self._completion_cache.get(key)
        if not isinstance(row, dict):
            return None
        try:
            expires_ts = float(row.get("expires_ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            expires_ts = 0.0
        if expires_ts <= time.time():
            self._completion_cache.pop(key, None)
            return None
        value = str(row.get("text", "")).strip()
        return value or None

    def telemetry_snapshot(self) -> dict[str, Any]:
        telemetry = self._telemetry_root()
        return {
            "calls": int(telemetry.get("calls", 0) or 0),
            "cache_hits": int(telemetry.get("cache_hits", 0) or 0),
            "successes": int(telemetry.get("successes", 0) or 0),
            "failures": int(telemetry.get("failures", 0) or 0),
            "fallbacks": int(telemetry.get("fallbacks", 0) or 0),
            "estimated_tokens": int(telemetry.get("estimated_tokens", 0) or 0),
            "estimated_cost_usd": round(float(telemetry.get("estimated_cost_usd", 0.0) or 0.0), 6),
            "cooldown_remaining_sec": max(0, int(self._api_cooldown_until_ts - time.time())),
            "failure_streak": int(self._api_failure_streak),
            "models": dict(telemetry.get("models", {})) if isinstance(telemetry.get("models"), dict) else {},
        }

    def _telemetry_root(self) -> dict[str, Any]:
        root = self._ai_root()
        telemetry = root.setdefault("telemetry", {})
        if not isinstance(telemetry, dict):
            root["telemetry"] = {}
            telemetry = root["telemetry"]
        telemetry.setdefault("calls", 0)
        telemetry.setdefault("cache_hits", 0)
        telemetry.setdefault("successes", 0)
        telemetry.setdefault("failures", 0)
        telemetry.setdefault("fallbacks", 0)
        telemetry.setdefault("models", {})
        telemetry.setdefault("estimated_tokens", 0)
        telemetry.setdefault("estimated_cost_usd", 0.0)
        telemetry.setdefault("last_call_ts", 0.0)
        return telemetry

    def _note_ai_telemetry(
        self,
        event: str,
        *,
        model: str = "",
        prompt_chars: int = 0,
        output_chars: int = 0,
        fallback: bool = False,
    ) -> None:
        telemetry = self._telemetry_root()
        if event == "call":
            telemetry["calls"] = int(telemetry.get("calls", 0) or 0) + 1
        elif event == "cache_hit":
            telemetry["cache_hits"] = int(telemetry.get("cache_hits", 0) or 0) + 1
        elif event == "success":
            telemetry["successes"] = int(telemetry.get("successes", 0) or 0) + 1
        elif event == "failure":
            telemetry["failures"] = int(telemetry.get("failures", 0) or 0) + 1
        if fallback:
            telemetry["fallbacks"] = int(telemetry.get("fallbacks", 0) or 0) + 1
        if model:
            models = telemetry.setdefault("models", {})
            if isinstance(models, dict):
                models[model] = int(models.get(model, 0) or 0) + 1
        estimated_tokens = max(1, int((prompt_chars + output_chars) / 4)) if (prompt_chars + output_chars) > 0 else 0
        telemetry["estimated_tokens"] = int(telemetry.get("estimated_tokens", 0) or 0) + estimated_tokens
        telemetry["estimated_cost_usd"] = round(float(telemetry.get("estimated_cost_usd", 0.0) or 0.0) + (estimated_tokens / 1000 * 0.001), 6)
        telemetry["last_call_ts"] = time.time()
        self.store.touch()

    def _put_cached_completion(self, key: str, text: str, *, ttl_sec: int) -> None:
        if ttl_sec <= 0:
            return
        now = time.time()
        self._completion_cache[key] = {
            "text": str(text or "")[:5000],
            "ts": now,
            "expires_ts": now + ttl_sec,
        }
        if len(self._completion_cache) <= COMPLETION_CACHE_MAX_ROWS:
            return
        ranked = sorted(
            self._completion_cache.items(),
            key=lambda item: float(item[1].get("expires_ts", 0.0) or 0.0),
        )
        for stale_key, _row in ranked[: len(self._completion_cache) - COMPLETION_CACHE_MAX_ROWS]:
            self._completion_cache.pop(stale_key, None)

    def _api_on_cooldown(self) -> bool:
        return self._api_cooldown_until_ts > time.time()

    def _note_api_success(self) -> None:
        self._api_failure_streak = 0
        self._api_cooldown_until_ts = 0.0

    def _note_api_failure(self) -> None:
        self._api_failure_streak = min(12, int(self._api_failure_streak) + 1)
        if self._api_failure_streak <= 1:
            return
        duration = int(API_FAILURE_COOLDOWN_BASE_SEC * (1.9 ** max(0, self._api_failure_streak - 2)))
        duration = max(API_FAILURE_COOLDOWN_BASE_SEC, min(API_FAILURE_COOLDOWN_MAX_SEC, duration))
        self._api_cooldown_until_ts = max(self._api_cooldown_until_ts, time.time() + duration)

    def _client(self) -> aiohttp.ClientSession:
        if self._http_session and not self._http_session.closed:
            return self._http_session
        timeout = aiohttp.ClientTimeout(total=30)
        self._http_session = aiohttp.ClientSession(timeout=timeout)
        return self._http_session

    async def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 220,
        temperature: float = 0.7,
        cache_ttl_sec: int | None = None,
    ) -> str | None:
        api_key, _source = self._resolve_api_key()
        if not api_key:
            return None
        prompt_limit = self._max_user_prompt_chars()
        safe_system = self._clamp_prompt(system_prompt, limit=max(1200, int(prompt_limit * 0.65)))
        safe_user = self._clamp_prompt(user_prompt, limit=prompt_limit)
        ttl = self._completion_cache_ttl_sec() if cache_ttl_sec is None else max(0, int(cache_ttl_sec))
        cache_key = self._cache_key(
            mode="text",
            system_prompt=safe_system,
            user_prompt=safe_user,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        cached = self._get_cached_completion(cache_key)
        if cached is not None:
            self._note_ai_telemetry("cache_hit")
            return cached
        if self._api_on_cooldown():
            return None
        candidates = self._model_candidates()
        for index, model in enumerate(candidates):
            try:
                self._note_ai_telemetry("call", model=model, prompt_chars=len(safe_system) + len(safe_user), fallback=index > 0)
                output = await self._chat_completion(
                    [
                        {"role": "system", "content": safe_system},
                        {"role": "user", "content": safe_user},
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    api_key=api_key,
                    model=model,
                )
                self._note_api_success()
                root = self._ai_root()
                if root.get("auto_model") != model:
                    root["auto_model"] = model
                    self.store.touch()
                self._put_cached_completion(cache_key, output, ttl_sec=ttl)
                self._note_ai_telemetry("success", model=model, output_chars=len(output))
                return output
            except Exception:  # noqa: BLE001
                self._note_api_failure()
                self._note_ai_telemetry("failure", model=model, fallback=index > 0)
                continue
        return None

    async def _try_vision_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_urls: list[str],
        max_tokens: int,
    ) -> str | None:
        api_key, _source = self._resolve_api_key()
        if not api_key:
            return None
        if not image_urls:
            return None
        if self._api_on_cooldown():
            return None
        prompt_limit = self._max_user_prompt_chars()
        safe_system = self._clamp_prompt(system_prompt, limit=max(1200, int(prompt_limit * 0.65)))
        safe_user = self._clamp_prompt(user_prompt, limit=prompt_limit)
        user_content: list[dict[str, Any]] = [{"type": "text", "text": safe_user}]
        for image_url in image_urls:
            user_content.append({"type": "image_url", "image_url": {"url": image_url}})
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": safe_system},
            {"role": "user", "content": user_content},
        ]
        candidates = self._vision_model_candidates()
        for index, model in enumerate(candidates):
            try:
                self._note_ai_telemetry("call", model=model, prompt_chars=len(safe_system) + len(safe_user), fallback=index > 0)
                output = await self._chat_completion(
                    messages,
                    max_tokens=max_tokens,
                    temperature=0.5,
                    api_key=api_key,
                    model=model,
                )
                self._note_api_success()
                root = self._ai_root()
                if root.get("auto_vision_model") != model:
                    root["auto_vision_model"] = model
                    self.store.touch()
                self._note_ai_telemetry("success", model=model, output_chars=len(output))
                return output
            except Exception:  # noqa: BLE001
                self._note_api_failure()
                self._note_ai_telemetry("failure", model=model, fallback=index > 0)
                continue
        return None

    async def _chat_completion(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 180,
        temperature: float = 0.7,
        *,
        api_key: str,
        model: str,
    ) -> str:
        if not api_key.strip():
            raise RuntimeError("Alibaba API key is not configured.")
        base = self.settings.alibaba_base_url.strip() or DEFAULT_BASE_URL
        base = base.rstrip("/")
        if base.endswith("/chat/completions"):
            url = base
        else:
            url = f"{base}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        session = self._client()
        async with session.post(url, headers=headers, json=payload) as response:
            body = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}: {body[:300]}")
            data = json.loads(body)
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("No choices in response.")
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = str(item.get("text") or item.get("content") or "").strip()
                    if text:
                        parts.append(text)
            merged = "\n".join(parts).strip()
            if merged:
                return merged
        raise RuntimeError("Model returned empty content.")

    def _save_api_test(self, result: ApiTestResult) -> None:
        root = self._ai_root()
        root["last_api_test"] = {
            "ok": result.ok,
            "detail": result.detail[:500],
            "latency_ms": result.latency_ms,
            "failure_streak": int(self._api_failure_streak),
            "cooldown_until_ts": float(self._api_cooldown_until_ts),
            "ts": datetime.now(tz=timezone.utc).isoformat(),
        }
        self.store.touch()

    def _remember_dm_reply(self, user_id: int, bot_text: str) -> None:
        events = self._ai_root().setdefault("dm_brain", {}).setdefault("events", [])
        events.append(
            {
                "ts": time.time(),
                "user_id": int(user_id),
                "user_name": "",
                "direction": "outbound",
                "text": str(bot_text or "")[:500],
            }
        )
        if len(events) > DM_EVENT_MAX_ROWS:
            del events[: len(events) - DM_EVENT_MAX_ROWS]
        self.store.touch()

    def _relationships_root(self) -> dict[str, Any]:
        root = self._ai_root()
        rel = root.setdefault("relationships", {})
        if not isinstance(rel, dict):
            root["relationships"] = {}
            rel = root["relationships"]
        return rel

    def _relationship_row(self, user_id: int, user_name: str = "") -> dict[str, Any]:
        rel = self._relationships_root()
        key = str(int(user_id))
        row = rel.get(key)
        if not isinstance(row, dict):
            row = {
                "user_name": str(user_name or "")[:80],
                "affinity": 0.0,
                "positive_hits": 0,
                "negative_hits": 0,
                "risk_flags": [],
                "trust_score": 0.0,
                "conflict_score": 0.0,
                "supportive_hits": 0,
                "hostile_hits": 0,
                "notes": [],
                "arc_stage": "new",
                "inside_jokes": [],
                "preferences": [],
                "callbacks": [],
                "last_seen_ts": 0.0,
                "last_seen_iso": "",
                "last_invited_ts": 0.0,
                "invite_count": 0,
            }
            rel[key] = row
            self.store.touch()
        if user_name:
            row["user_name"] = str(user_name)[:80]
        return row

    def _note_relationship_signal(
        self,
        *,
        user_id: int,
        user_name: str,
        text: str,
        source: str,
        event_ts: float | None = None,
    ) -> None:
        if user_id <= 0:
            return
        row = self._relationship_row(user_id, user_name=user_name)
        now = float(event_ts) if event_ts is not None else time.time()
        raw = str(text or "").strip()
        prev_seen = float(row.get("last_seen_ts", 0.0) or 0.0)
        if raw:
            row["last_seen_ts"] = now
            row["last_seen_iso"] = datetime.now(tz=timezone.utc).isoformat()

        # Small, bounded updates only. This is a gate/ledger, not a transcript.
        affinity = float(row.get("affinity", 0.0) or 0.0)
        trust = float(row.get("trust_score", 0.0) or 0.0)
        conflict = float(row.get("conflict_score", 0.0) or 0.0)
        positives = int(row.get("positive_hits", 0) or 0)
        negatives = int(row.get("negative_hits", 0) or 0)
        supportive_hits = int(row.get("supportive_hits", 0) or 0)
        hostile_hits = int(row.get("hostile_hits", 0) or 0)
        flags = row.get("risk_flags", [])
        if not isinstance(flags, list):
            flags = []
            row["risk_flags"] = flags
        inside_jokes = row.get("inside_jokes", [])
        if not isinstance(inside_jokes, list):
            inside_jokes = []
            row["inside_jokes"] = inside_jokes
        preferences = row.get("preferences", [])
        if not isinstance(preferences, list):
            preferences = []
            row["preferences"] = preferences
        callbacks = row.get("callbacks", [])
        if not isinstance(callbacks, list):
            callbacks = []
            row["callbacks"] = callbacks

        if raw and self._positive_regex.search(raw):
            positives += 1
            affinity = min(5.0, affinity + 0.10)
            trust = min(5.0, trust + 0.14)
            supportive_hits += 1
        if raw and self._negative_regex.search(raw):
            negatives += 1
            affinity = max(-5.0, affinity - 0.15)
            conflict = min(5.0, conflict + 0.18)
            hostile_hits += 1
            if "hostile_language" not in flags:
                flags.append("hostile_language")
        if "thank" in raw.casefold() or "appreciate" in raw.casefold():
            trust = min(5.0, trust + 0.08)
        if any(term in raw.casefold() for term in ("shut up", "hate you", "annoying")):
            conflict = min(5.0, conflict + 0.12)
        lowered = raw.casefold()
        if any(term in lowered for term in ("inside joke", "remember when", "callback", "running joke")):
            self._append_unique(inside_jokes, raw[:90], max_items=10)
            self._append_unique(callbacks, raw[:90], max_items=8)
        if any(term in lowered for term in ("i like", "i love", "i prefer", "favorite")):
            self._append_unique(preferences, raw[:90], max_items=10)

        # Mild decay so old negatives don't permanently poison someone.
        if prev_seen > 0 and (now - prev_seen) > 30 * 86400:
            affinity *= 0.98
            trust *= 0.995
            conflict *= 0.992

        # If recent behavior trends positive, clear the generic hostility flag.
        if affinity >= 0.25 and negatives <= max(2, positives):
            if "hostile_language" in flags:
                flags.remove("hostile_language")

        row["affinity"] = round(affinity, 3)
        row["trust_score"] = round(max(0.0, min(5.0, trust)), 3)
        row["conflict_score"] = round(max(0.0, min(5.0, conflict)), 3)
        row["positive_hits"] = positives
        row["negative_hits"] = negatives
        row["supportive_hits"] = supportive_hits
        row["hostile_hits"] = hostile_hits
        row["arc_stage"] = self._relationship_arc_stage(
            positives=positives,
            negatives=negatives,
            trust=trust,
            conflict=conflict,
            last_seen_ts=now,
        )
        row["last_source"] = str(source)[:60]
        self.store.touch()

    def _relationship_arc_stage(
        self,
        *,
        positives: int,
        negatives: int,
        trust: float,
        conflict: float,
        last_seen_ts: float,
    ) -> str:
        del last_seen_ts
        if conflict >= 1.8 and negatives > positives:
            return "repair"
        if trust >= 2.4 and positives >= 5:
            return "trusted"
        if trust >= 0.8 or positives >= 2:
            return "warming"
        if positives + negatives >= 4:
            return "known"
        return "new"

    def _remember_exchange(self, message: discord.Message, bot_reply: str) -> None:
        if not message.guild:
            return
        learning_mode = self.learning_mode_for_guild(int(message.guild.id))
        if learning_mode != "full":
            return
        root = self._ai_root()
        memories = root.setdefault("long_term_memory", {})
        rows = memories.setdefault(str(message.guild.id), [])
        user_text = message.clean_content[:280]
        score = self._score_exchange_memory(user_text, bot_reply)
        tags = self._exchange_tags(user_text)
        rows.append(
            {
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "user_id": message.author.id,
                "user_text": user_text,
                "bot_text": bot_reply[:280],
                "score": score,
                "tags": tags,
            }
        )
        self._prune_long_term_rows(rows)
        self.store.touch()

    def _long_term_recent(self, guild_id: int, limit: int = 3) -> list[str]:
        if guild_id <= 0:
            return []
        rows = self._ai_root().setdefault("long_term_memory", {}).get(str(guild_id), [])
        last = rows[-max(1, limit) :]
        out: list[str] = []
        for row in last:
            user_text = str(row.get("user_text", "")).strip()
            bot_text = str(row.get("bot_text", "")).strip()
            if user_text or bot_text:
                out.append(f"user: {user_text} | mandy: {bot_text}")
        return out

    def _long_term_relevant(self, message: discord.Message, limit: int = 4) -> list[str]:
        if not message.guild:
            return []
        rows = self._ai_root().setdefault("long_term_memory", {}).get(str(message.guild.id), [])
        if not isinstance(rows, list) or not rows:
            return []

        query_terms = self._memory_terms(message.clean_content)
        now = time.time()
        scored: list[tuple[float, float, dict[str, Any]]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            user_text = str(row.get("user_text", "")).strip()
            bot_text = str(row.get("bot_text", "")).strip()
            if not user_text and not bot_text:
                continue
            base = float(row.get("score", 0.35) or 0.35)
            ts = self._parse_ts(row.get("ts"))
            age_days = max(0.0, (now - ts) / 86400.0) if ts > 0 else 999.0
            freshness = max(0.0, 0.35 - (age_days * LONG_TERM_DECAY_PER_DAY))

            relevance = 0.0
            row_terms = self._memory_terms(f"{user_text} {bot_text}")
            overlap = len(query_terms.intersection(row_terms))
            if overlap:
                relevance += min(0.5, overlap * LONG_TERM_RELEVANCE_BONUS_PER_TERM)
            if int(row.get("user_id", 0) or 0) == message.author.id:
                relevance += 0.3

            scored.append((base + freshness + relevance, ts, row))

        if not scored:
            return []

        chosen = sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)[: max(1, limit)]
        chosen.sort(key=lambda item: item[1])
        out: list[str] = []
        for _score, _ts, row in chosen:
            user_text = str(row.get("user_text", "")).strip()
            bot_text = str(row.get("bot_text", "")).strip()
            if user_text or bot_text:
                out.append(f"user: {user_text} | mandy: {bot_text}")
        return out

    def _profile_summary(self, guild_id: int, user_id: int) -> str:
        profiles = self._ai_root().setdefault("profiles", {})
        guild_profiles = profiles.get(str(guild_id), {})
        if not isinstance(guild_profiles, dict):
            return "unknown"
        profile = guild_profiles.get(str(user_id), {})
        if not isinstance(profile, dict) or not profile:
            return "new user"
        tags = ",".join(profile.get("style_tags", []))
        count = int(profile.get("message_count", 0))
        avg_len = int(profile.get("avg_len", 0))
        rapport = float(profile.get("rapport_score", 0.0) or 0.0)
        samples = profile.get("samples", [])
        sample_text = ""
        if isinstance(samples, list) and samples:
            sample_text = str(samples[-1])[:120]
        return f"messages={count} avg_len={avg_len} rapport={rapport:.2f} tags=[{tags}] sample={sample_text}"

    def _update_profile(self, message: discord.Message, *, touch: bool) -> None:
        if not message.guild:
            return
        if self.learning_mode_for_guild(int(message.guild.id)) == "off":
            return
        root = self._ai_root()
        profiles = root.setdefault("profiles", {})
        guild_profiles = profiles.setdefault(str(message.guild.id), {})
        key = str(message.author.id)
        row = guild_profiles.get(key)
        if not isinstance(row, dict):
            row = {
                "name": message.author.display_name,
                "message_count": 0,
                "avg_len": 0,
                "question_count": 0,
                "positive_count": 0,
                "negative_count": 0,
                "rapport_score": 0.0,
                "style_tags": [],
                "samples": [],
                "last_seen_ts": "",
            }
            guild_profiles[key] = row

        text = message.clean_content.strip()
        size = len(text)
        row["name"] = message.author.display_name
        row["message_count"] = int(row.get("message_count", 0)) + 1
        old_avg = int(row.get("avg_len", 0))
        count = int(row["message_count"])
        row["avg_len"] = int(((old_avg * (count - 1)) + size) / max(1, count))
        if "?" in text:
            row["question_count"] = int(row.get("question_count", 0)) + 1
        if self._positive_regex.search(text):
            row["positive_count"] = int(row.get("positive_count", 0)) + 1
            row["rapport_score"] = round(min(5.0, float(row.get("rapport_score", 0.0) or 0.0) + 0.14), 3)
        if self._negative_regex.search(text):
            row["negative_count"] = int(row.get("negative_count", 0)) + 1
            row["rapport_score"] = round(max(-5.0, float(row.get("rapport_score", 0.0) or 0.0) - 0.20), 3)
        row["last_seen_ts"] = datetime.now(tz=timezone.utc).isoformat()

        tags = set(str(tag) for tag in row.get("style_tags", []))
        if size < 30:
            tags.add("short")
        if size > 160:
            tags.add("long")
        if text.count("!") >= 2:
            tags.add("high-energy")
        if "?" in text:
            tags.add("curious")
        if self._positive_regex.search(text):
            tags.add("friendly-tone")
        if self._negative_regex.search(text):
            tags.add("hostile-tone")
        row["style_tags"] = sorted(tags)[:8]

        samples = row.get("samples", [])
        if not isinstance(samples, list):
            samples = []
        if text:
            samples.append(text[:180])
            if len(samples) > 6:
                del samples[: len(samples) - 6]
        row["samples"] = samples
        if touch:
            self.store.touch()

    def _remember_user_facts(self, message: discord.Message, *, touch: bool) -> None:
        if not message.guild:
            return
        if self.learning_mode_for_guild(int(message.guild.id)) != "full":
            return
        text = " ".join(message.clean_content.split())
        if len(text) < FACT_MEMORY_MIN_TEXT_LEN:
            return
        candidates = self._extract_fact_candidates(text)
        if not candidates:
            return

        root = self._ai_root()
        memory_facts = root.setdefault("memory_facts", {})
        guild_rows = memory_facts.setdefault(str(message.guild.id), {})
        user_key = str(message.author.id)
        rows = guild_rows.get(user_key)
        if not isinstance(rows, list):
            rows = []
            guild_rows[user_key] = rows

        now_iso = datetime.now(tz=timezone.utc).isoformat()
        changed = False
        for fact_text, boost, kind in candidates:
            norm = self._normalize_memory_text(fact_text)
            if not norm:
                continue
            existing = next((row for row in rows if str(row.get("norm", "")) == norm), None)
            if existing:
                previous = float(existing.get("score", 0.4) or 0.4)
                existing["score"] = round(min(2.5, previous + (boost * 0.35)), 3)
                existing["mentions"] = int(existing.get("mentions", 1) or 1) + 1
                existing["ts"] = now_iso
                existing["fact"] = fact_text[:140]
                existing["kind"] = kind
            else:
                rows.append(
                    {
                        "fact": fact_text[:140],
                        "norm": norm,
                        "kind": kind,
                        "score": round(max(0.1, boost), 3),
                        "mentions": 1,
                        "ts": now_iso,
                    }
                )
            changed = True

        if not changed:
            return

        self._prune_user_fact_rows(rows)
        if touch:
            self.store.touch()

    def is_learning_paused(self, user_id: int) -> bool:
        paused = self._privacy_root().setdefault("paused_user_ids", [])
        return str(int(user_id)) in {str(item) for item in paused}

    def set_learning_paused(self, user_id: int, paused: bool, *, actor_id: int = 0, reason: str = "") -> bool:
        root = self._privacy_root()
        rows = root.setdefault("paused_user_ids", [])
        if not isinstance(rows, list):
            rows = []
            root["paused_user_ids"] = rows
        key = str(int(user_id))
        changed = False
        if paused and key not in {str(item) for item in rows}:
            rows.append(key)
            changed = True
        if not paused:
            before = len(rows)
            rows[:] = [item for item in rows if str(item) != key]
            changed = len(rows) != before
        self._privacy_audit(
            "learning_paused" if paused else "learning_resumed",
            actor_id=actor_id,
            user_id=user_id,
            reason=reason,
        )
        if changed:
            self.store.touch()
        return changed

    def export_user_memory(self, user_id: int) -> dict[str, Any]:
        root = self._ai_root()
        uid = str(int(user_id))
        facts: dict[str, Any] = {}
        for guild_id, guild_rows in root.setdefault("memory_facts", {}).items():
            if isinstance(guild_rows, dict) and uid in guild_rows:
                facts[str(guild_id)] = guild_rows.get(uid, [])
        profiles: dict[str, Any] = {}
        for guild_id, guild_rows in root.setdefault("profiles", {}).items():
            if isinstance(guild_rows, dict) and uid in guild_rows:
                profiles[str(guild_id)] = guild_rows.get(uid, {})
        long_term: dict[str, list[dict[str, Any]]] = {}
        for guild_id, rows in root.setdefault("long_term_memory", {}).items():
            if not isinstance(rows, list):
                continue
            mine = [row for row in rows if isinstance(row, dict) and int(row.get("user_id", 0) or 0) == int(user_id)]
            if mine:
                long_term[str(guild_id)] = mine
        return {
            "user_id": int(user_id),
            "learning_paused": self.is_learning_paused(user_id),
            "relationship": root.setdefault("relationships", {}).get(uid, {}),
            "facts": facts,
            "profiles": profiles,
            "long_term_memory": long_term,
        }

    def forget_user_everywhere(self, user_id: int, *, actor_id: int = 0, reason: str = "") -> dict[str, int]:
        root = self._ai_root()
        uid = str(int(user_id))
        removed = {"facts": 0, "profiles": 0, "relationships": 0, "long_term": 0}
        for guild_rows in root.setdefault("memory_facts", {}).values():
            if isinstance(guild_rows, dict) and uid in guild_rows:
                removed["facts"] += len(guild_rows.get(uid, []) or [])
                guild_rows.pop(uid, None)
        for guild_rows in root.setdefault("profiles", {}).values():
            if isinstance(guild_rows, dict) and uid in guild_rows:
                removed["profiles"] += 1
                guild_rows.pop(uid, None)
        relationships = root.setdefault("relationships", {})
        if isinstance(relationships, dict) and uid in relationships:
            relationships.pop(uid, None)
            removed["relationships"] = 1
        for rows in root.setdefault("long_term_memory", {}).values():
            if not isinstance(rows, list):
                continue
            before = len(rows)
            rows[:] = [row for row in rows if not (isinstance(row, dict) and int(row.get("user_id", 0) or 0) == int(user_id))]
            removed["long_term"] += before - len(rows)
        self._privacy_audit("forget_user", actor_id=actor_id, user_id=user_id, reason=reason, details=removed)
        self.store.touch()
        return removed

    def privacy_audit_lines(self, limit: int = 10) -> list[str]:
        audit = self._privacy_root().setdefault("audit_log", [])
        if not isinstance(audit, list):
            return []
        lines: list[str] = []
        for row in audit[-max(1, limit) :]:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"{row.get('ts', '')} action={row.get('action', '')} user={row.get('user_id', 0)} actor={row.get('actor_id', 0)}"
            )
        return lines

    def _privacy_root(self) -> dict[str, Any]:
        root = self._ai_root()
        privacy = root.setdefault("privacy", {"paused_user_ids": [], "audit_log": []})
        if not isinstance(privacy, dict):
            root["privacy"] = {"paused_user_ids": [], "audit_log": []}
            privacy = root["privacy"]
        privacy.setdefault("paused_user_ids", [])
        privacy.setdefault("audit_log", [])
        return privacy

    def _privacy_audit(
        self,
        action: str,
        *,
        actor_id: int,
        user_id: int,
        reason: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        audit = self._privacy_root().setdefault("audit_log", [])
        if not isinstance(audit, list):
            return
        audit.append(
            {
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "action": str(action)[:80],
                "actor_id": int(actor_id),
                "user_id": int(user_id),
                "reason": str(reason)[:180],
                "details": details or {},
            }
        )
        if len(audit) > 500:
            del audit[: len(audit) - 500]

    def _extract_fact_candidates(self, text: str) -> list[tuple[str, float, str]]:
        clean = " ".join(text.split())
        lowered = clean.lower()
        if len(clean) < FACT_MEMORY_MIN_TEXT_LEN:
            return []
        if "http://" in lowered or "https://" in lowered:
            return []

        out: list[tuple[str, float, str]] = []

        def add_fact(kind: str, value: str, boost: float) -> None:
            body = value.strip(" .,!?:;")
            body = " ".join(body.split())
            if len(body) < 2:
                return
            if len(body) > 110:
                body = body[:110].rstrip()
            out.append((body, boost, kind))

        match = re.search(r"\bmy name is ([a-z0-9][a-z0-9 _'\-]{1,31})\b", clean, re.IGNORECASE)
        if match:
            add_fact("identity", f"name: {match.group(1)}", 1.25)

        match = re.search(r"\bcall me ([a-z0-9][a-z0-9 _'\-]{1,31})\b", clean, re.IGNORECASE)
        if match:
            add_fact("identity", f"preferred name: {match.group(1)}", 1.1)

        for fav in re.finditer(r"\bmy favorite ([a-z][a-z0-9 \-]{1,20}) is ([^.!?\n]{2,60})", clean, re.IGNORECASE):
            add_fact("preference", f"favorite {fav.group(1)}: {fav.group(2)}", 1.05)

        match = re.search(r"\bi (?:really )?(?:like|love|enjoy|prefer)\s+([^.!?\n]{2,80})", clean, re.IGNORECASE)
        if match:
            add_fact("preference", f"likes: {match.group(1)}", 0.9)

        match = re.search(r"\bi (?:really )?(?:hate|dislike)\s+([^.!?\n]{2,80})", clean, re.IGNORECASE)
        if match:
            add_fact("preference", f"dislikes: {match.group(1)}", 0.85)

        match = re.search(r"\bi work (?:at|as)\s+([^.!?\n]{2,60})", clean, re.IGNORECASE)
        if match:
            add_fact("background", f"work: {match.group(1)}", 0.95)

        match = re.search(r"\bi live in\s+([^.!?\n]{2,60})", clean, re.IGNORECASE)
        if match:
            add_fact("background", f"location: {match.group(1)}", 0.9)

        match = re.search(r"\bmy timezone is\s+([a-z0-9_/\-+:]{2,40})", clean, re.IGNORECASE)
        if match:
            add_fact("background", f"timezone: {match.group(1)}", 1.0)

        match = re.search(r"\bi(?: am|'m)\s+([a-z][a-z0-9 \-]{1,40})", clean, re.IGNORECASE)
        if match:
            raw_trait = " ".join(match.group(1).split())
            trait_tokens = [token for token in re.findall(r"[a-z]+", raw_trait.lower())]
            if trait_tokens and trait_tokens[0] in NON_STABLE_SELF_PREFIXES:
                trait_tokens = []
            if trait_tokens and all(token in EPHEMERAL_SELF_TERMS for token in trait_tokens):
                trait_tokens = []
            if trait_tokens:
                if len(trait_tokens) <= 4:
                    add_fact("self", f"self: {raw_trait}", 0.72)

        deduped: dict[str, tuple[str, float, str]] = {}
        for fact, boost, kind in out:
            key = self._normalize_memory_text(fact)
            if not key:
                continue
            prev = deduped.get(key)
            if prev is None or boost > prev[1]:
                deduped[key] = (fact, boost, kind)
        return list(deduped.values())

    def _user_fact_lines(self, guild_id: int, user_id: int, limit: int = 3) -> list[str]:
        if guild_id <= 0:
            return []
        memory_facts = self._ai_root().setdefault("memory_facts", {})
        guild_rows = memory_facts.get(str(guild_id), {})
        if not isinstance(guild_rows, dict):
            return []
        rows = guild_rows.get(str(user_id), [])
        if not isinstance(rows, list) or not rows:
            return []
        now = time.time()
        scored: list[tuple[float, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            fact = str(row.get("fact", "")).strip()
            if not fact:
                continue
            strength = self._fact_row_strength(row, now)
            scored.append((strength, fact))
        if not scored:
            return []
        scored.sort(key=lambda item: item[0], reverse=True)
        return [fact for _strength, fact in scored[: max(1, limit)]]

    def list_user_memory(self, guild_id: int, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._user_memory_rows(guild_id, user_id)
        now = time.time()
        out: list[dict[str, Any]] = []
        for index, row in enumerate(rows[: max(1, limit)]):
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "index": index,
                    "fact": str(row.get("fact", "")),
                    "kind": str(row.get("kind", "")),
                    "pinned": bool(row.get("pinned", False)),
                    "strength": round(self._fact_row_strength(row, now), 3),
                    "mentions": int(row.get("mentions", 0) or 0),
                }
            )
        return out

    def pin_user_memory(self, guild_id: int, user_id: int, index: int, pinned: bool = True) -> bool:
        rows = self._user_memory_rows(guild_id, user_id)
        if index < 0 or index >= len(rows) or not isinstance(rows[index], dict):
            return False
        rows[index]["pinned"] = bool(pinned)
        if pinned:
            rows[index]["score"] = max(float(rows[index].get("score", 0.5) or 0.5), 1.8)
        rows[index]["ts"] = datetime.now(tz=timezone.utc).isoformat()
        self.store.touch()
        return True

    def edit_user_memory(self, guild_id: int, user_id: int, index: int, fact_text: str) -> bool:
        rows = self._user_memory_rows(guild_id, user_id)
        clean = " ".join(str(fact_text or "").split())[:140]
        if not clean or index < 0 or index >= len(rows) or not isinstance(rows[index], dict):
            return False
        rows[index]["fact"] = clean
        rows[index]["norm"] = self._normalize_memory_text(clean)
        rows[index]["ts"] = datetime.now(tz=timezone.utc).isoformat()
        self.store.touch()
        return True

    def forget_user_memory(self, guild_id: int, user_id: int, index: int) -> bool:
        rows = self._user_memory_rows(guild_id, user_id)
        if index < 0 or index >= len(rows):
            return False
        rows.pop(index)
        self.store.touch()
        return True

    def _user_memory_rows(self, guild_id: int, user_id: int) -> list[dict[str, Any]]:
        if guild_id <= 0 or user_id <= 0:
            return []
        memory_facts = self._ai_root().setdefault("memory_facts", {})
        guild_rows = memory_facts.setdefault(str(guild_id), {})
        if not isinstance(guild_rows, dict):
            memory_facts[str(guild_id)] = {}
            guild_rows = memory_facts[str(guild_id)]
        rows = guild_rows.setdefault(str(user_id), [])
        if not isinstance(rows, list):
            guild_rows[str(user_id)] = []
            rows = guild_rows[str(user_id)]
        return rows

    def _preferred_alias(self, guild_id: int, user_id: int) -> str:
        if guild_id <= 0:
            return ""
        memory_facts = self._ai_root().setdefault("memory_facts", {})
        guild_rows = memory_facts.get(str(guild_id), {})
        if not isinstance(guild_rows, dict):
            return ""
        rows = guild_rows.get(str(user_id), [])
        if not isinstance(rows, list):
            return ""
        ordered = sorted(
            (row for row in rows if isinstance(row, dict)),
            key=lambda row: self._parse_ts(row.get("ts")),
            reverse=True,
        )
        for row in ordered:
            fact = str(row.get("fact", "")).strip()
            lowered = fact.lower()
            if lowered.startswith("preferred name:"):
                return fact.split(":", 1)[1].strip()[:32]
        for row in ordered:
            fact = str(row.get("fact", "")).strip()
            lowered = fact.lower()
            if lowered.startswith("name:"):
                return fact.split(":", 1)[1].strip()[:32]
        return ""

    def _relationship_summary(self, guild_id: int, user_id: int) -> str:
        profiles = self._ai_root().setdefault("profiles", {})
        guild_profiles = profiles.get(str(guild_id), {})
        if not isinstance(guild_profiles, dict):
            return "unknown"
        profile = guild_profiles.get(str(user_id), {})
        if not isinstance(profile, dict) or not profile:
            return "new-user"

        count = int(profile.get("message_count", 0) or 0)
        questions = int(profile.get("question_count", 0) or 0)
        positives = int(profile.get("positive_count", 0) or 0)
        negatives = int(profile.get("negative_count", 0) or 0)
        rapport = float(profile.get("rapport_score", 0.0) or 0.0)
        fact_count = len(self._user_fact_lines(guild_id, user_id, limit=6))

        familiarity = "new"
        if count >= 60:
            familiarity = "veteran"
        elif count >= 20:
            familiarity = "familiar"
        elif count >= 6:
            familiarity = "known"

        tone = "neutral"
        if rapport >= 1.6:
            tone = "warm"
        elif rapport >= 0.45:
            tone = "positive"
        elif rapport <= -1.6:
            tone = "tense"
        elif rapport <= -0.45:
            tone = "spiky"

        curiosity = "low"
        if count > 0:
            ratio = questions / max(1, count)
            if ratio >= 0.35:
                curiosity = "high"
            elif ratio >= 0.16:
                curiosity = "medium"
        return (
            f"familiarity={familiarity} tone={tone} curiosity={curiosity} "
            f"rapport={rapport:.2f} pos={positives} neg={negatives} facts={fact_count}"
        )

    def _prune_user_fact_rows(self, rows: list[dict[str, Any]]) -> None:
        if len(rows) <= FACT_MEMORY_MAX_ROWS_PER_USER:
            return
        rows.sort(key=lambda row: self._parse_ts(row.get("ts")))
        recent = rows[-FACT_MEMORY_RECENT_FLOOR:]
        older = rows[:-FACT_MEMORY_RECENT_FLOOR]
        slots = max(0, FACT_MEMORY_MAX_ROWS_PER_USER - len(recent))
        if slots > 0 and older:
            now = time.time()
            older = sorted(older, key=lambda row: self._fact_row_strength(row, now), reverse=True)[:slots]
        rows[:] = sorted(recent + older, key=lambda row: self._parse_ts(row.get("ts")))

    def _fact_row_strength(self, row: dict[str, Any], now: float) -> float:
        base = float(row.get("score", 0.5) or 0.5)
        if bool(row.get("pinned", False)):
            base += 10.0
        mentions = max(1, int(row.get("mentions", 1) or 1))
        mention_bonus = min(0.35, 0.05 * mentions)
        ts = self._parse_ts(row.get("ts"))
        age_days = max(0.0, (now - ts) / 86400.0) if ts > 0 else 3650.0
        decay = age_days * 0.025
        return base + mention_bonus - decay

    def _score_exchange_memory(self, user_text: str, bot_text: str) -> float:
        clean_user = " ".join(user_text.split())
        score = 0.2
        size = len(clean_user)
        if 20 <= size <= 220:
            score += 0.18
        elif size > 220:
            score += 0.08
        if "?" in clean_user:
            score += 0.16
        if self._is_direct_request(clean_user):
            score += 0.22
        if self._alias_regex.search(clean_user):
            score += 0.08
        if self._negative_regex.search(clean_user):
            score += 0.06
        if any(char.isdigit() for char in clean_user):
            score += 0.06
        if any(token in clean_user.lower() for token in ("remember", "always", "never", "favorite", "call me", "my name")):
            score += 0.2
        if len(bot_text.strip()) > 90:
            score += 0.05
        return round(max(0.1, min(2.5, score)), 3)

    def _exchange_tags(self, user_text: str) -> list[str]:
        text = user_text.lower()
        tags: list[str] = []
        if "?" in text:
            tags.append("question")
        if self._is_direct_request(user_text):
            tags.append("request")
        if self._negative_regex.search(user_text):
            tags.append("conflict")
        if self._extract_fact_candidates(user_text):
            tags.append("fact")
        if len(user_text.strip()) > 180:
            tags.append("long-form")
        if self._alias_regex.search(user_text):
            tags.append("mention")
        return tags[:6]

    def _prune_long_term_rows(self, rows: list[dict[str, Any]]) -> None:
        if len(rows) <= LONG_TERM_MEMORY_MAX_ROWS:
            return
        rows.sort(key=lambda row: self._parse_ts(row.get("ts")))
        recent = rows[-LONG_TERM_RECENT_FLOOR:]
        older = rows[:-LONG_TERM_RECENT_FLOOR]
        slots = max(0, LONG_TERM_MEMORY_MAX_ROWS - len(recent))
        if slots > 0 and older:
            now = time.time()
            older = sorted(older, key=lambda row: self._long_term_row_strength(row, now), reverse=True)[:slots]
        rows[:] = sorted(recent + older, key=lambda row: self._parse_ts(row.get("ts")))

    def _long_term_row_strength(self, row: dict[str, Any], now: float) -> float:
        base = float(row.get("score", 0.35) or 0.35)
        tags = row.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        bonus = 0.0
        if "fact" in tags:
            bonus += 0.16
        if "request" in tags:
            bonus += 0.07
        if "question" in tags:
            bonus += 0.04
        if "long-form" in tags:
            bonus += 0.03
        ts = self._parse_ts(row.get("ts"))
        age_days = max(0.0, (now - ts) / 86400.0) if ts > 0 else 3650.0
        decay = age_days * LONG_TERM_DECAY_PER_DAY
        return base + bonus - decay

    def _normalize_memory_text(self, text: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", text.lower()))

    def _memory_terms(self, text: str) -> set[str]:
        tokens = re.findall(r"[a-z0-9]{3,}", text.lower())
        return {token for token in tokens if token not in MEMORY_STOPWORDS}

    def _parse_ts(self, value: Any) -> float:
        if not value:
            return 0.0
        raw = str(value).strip()
        if not raw:
            return 0.0
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return 0.0

    def _update_turn_state(self, channel_id: int, user_id: int, now: float) -> None:
        prev = self._last_turn_by_channel.get(channel_id)
        if prev and prev[0] == user_id and (now - prev[1]) <= STILL_TALKING_WINDOW_SEC:
            streak = prev[2] + 1
        else:
            streak = 1
        self._last_turn_by_channel[channel_id] = (user_id, now, streak)

    def _is_still_talking(self, channel_id: int, user_id: int, now: float) -> bool:
        prev = self._last_turn_by_channel.get(channel_id)
        if not prev:
            return False
        return prev[0] == user_id and prev[2] >= 2 and (now - prev[1]) <= STILL_TALKING_WINDOW_SEC

    def _chance(self, p: float) -> bool:
        return self._rng.random() <= max(0.0, min(1.0, p))

    def has_image_attachments(self, message: discord.Message) -> bool:
        return bool(self._extract_image_urls(message, max_images=1))

    def _extract_image_urls(self, message: discord.Message, max_images: int = 2) -> list[str]:
        urls: list[str] = []
        for attachment in message.attachments:
            content_type = str(getattr(attachment, "content_type", "") or "").lower()
            filename = str(getattr(attachment, "filename", "") or "").lower()
            is_image = content_type.startswith("image/") or filename.endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
            )
            if not is_image:
                continue
            url = str(getattr(attachment, "url", "") or "").strip()
            if not url:
                continue
            urls.append(url)
            if len(urls) >= max(1, max_images):
                break
        return urls

    def _is_direct_request(self, content: str) -> bool:
        if not content:
            return False
        lowered = content.lower()
        if lowered.startswith(("can you", "could you", "would you", "tell me", "help me", "what do you think")):
            return True
        return bool(self._direct_request_regex.search(content))

    def _is_image_explicit_request(self, content: str) -> bool:
        text = content.strip()
        if not text:
            return False
        if self._image_request_regex.search(text):
            return True
        lowered = text.lower()
        image_words = ("image", "img", "pic", "picture", "photo", "screenshot")
        if any(word in lowered for word in image_words):
            if "?" in lowered or self._is_direct_request(text):
                return True
        return False

    def _format_lines(self, lines: list[str]) -> str:
        if not lines:
            return "(none)"
        return "\n".join(f"- {line[:300]}" for line in lines)

    def _append_unique(self, rows: list[Any], value: str, *, max_items: int) -> None:
        clean = " ".join(str(value or "").split())[:140]
        if not clean:
            return
        norm = clean.casefold()
        for existing in rows:
            if str(existing).casefold() == norm:
                return
        rows.append(clean)
        if len(rows) > max_items:
            del rows[: len(rows) - max_items]

    def capability_registry(self) -> dict[str, Any]:
        root = self._ai_root()
        capabilities = root.setdefault("capabilities", {})
        if not isinstance(capabilities, dict):
            root["capabilities"] = {}
            capabilities = root["capabilities"]
        defaults = {
            "chat": {"category": "social", "enabled": True, "description": "Adaptive conversation and memory-aware replies."},
            "memory": {"category": "social", "enabled": True, "description": "Fact memory, relationship arcs, and reflection summaries."},
            "moderation": {"category": "operations", "enabled": True, "description": "Guarded server actions through autonomy policy."},
            "fun_modes": {"category": "social", "enabled": True, "description": "Per-server tone controls for playful, cozy, lore, helper, and serious modes."},
            "dm_bridge": {"category": "operations", "enabled": True, "description": "Staff-visible DM relay and optional AI replies."},
        }
        changed = False
        for key, row in defaults.items():
            if key not in capabilities or not isinstance(capabilities.get(key), dict):
                capabilities[key] = row
                changed = True
        if changed:
            self.store.touch()
        return capabilities

    def capability_lines(self) -> list[str]:
        capabilities = self.capability_registry()
        lines: list[str] = []
        for key, row in sorted(capabilities.items()):
            if not isinstance(row, dict):
                continue
            enabled = bool(row.get("enabled", True))
            lines.append(f"{key}: {'on' if enabled else 'off'} [{row.get('category', 'general')}] {row.get('description', '')}")
        return lines

    def _resolve_api_key(self) -> tuple[str, str]:
        direct = self.settings.alibaba_api_key.strip()
        if direct:
            return direct, "settings.ALIBABA_API_KEY"

        for name in ENV_KEY_NAMES:
            value = os.environ.get(name, "").strip()
            if value:
                return value, f"env.{name}"

        values = self._load_passwords_values()
        for name in PASSWORDS_KEY_NAMES:
            value = values.get(name, "").strip()
            if value:
                return value, f"passwords.txt:{name}"

        return "", "none"

    def _model_candidates(self) -> list[str]:
        candidates: list[str] = []
        configured = self.settings.alibaba_model.strip()
        auto_model = str(self._ai_root().get("auto_model", "")).strip()

        for model in (configured, auto_model, *DEFAULT_MODELS):
            if model and model not in candidates:
                candidates.append(model)
        return candidates

    def _vision_model_candidates(self) -> list[str]:
        candidates: list[str] = []
        configured = self.settings.alibaba_model.strip()
        auto_vision = str(self._ai_root().get("auto_vision_model", "")).strip()
        for model in (auto_vision, configured, *DEFAULT_VISION_MODELS):
            if model and model not in candidates:
                candidates.append(model)
        return candidates

    def _load_passwords_values(self) -> dict[str, str]:
        if self._passwords_cache is not None:
            return self._passwords_cache
        path = Path("passwords.txt")
        values: dict[str, str] = {}
        if not path.exists():
            self._passwords_cache = values
            return values
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            self._passwords_cache = values
            return values
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("["):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            k = key.strip().upper().replace("-", "_").replace(".", "_")
            values[k] = value.strip()
        self._passwords_cache = values
        return values

    def _mode_row(self, guild_id: int) -> dict[str, Any]:
        root = self._ai_root()
        modes = root.setdefault("guild_modes", {})
        key = str(guild_id)
        row = modes.get(key)
        if isinstance(row, dict):
            if "chat_enabled" not in row:
                row["chat_enabled"] = False
            if "roast_enabled" not in row:
                row["roast_enabled"] = False
            return row
        row = {"chat_enabled": False, "roast_enabled": False}
        modes[key] = row
        self.store.touch()
        return row

    def _ai_root(self) -> dict[str, Any]:
        root = self.store.data.setdefault("ai", {})
        root.setdefault("guild_modes", {})
        root.setdefault("long_term_memory", {})
        root.setdefault("last_api_test", {})
        root.setdefault("auto_model", "")
        root.setdefault("auto_vision_model", "")
        root.setdefault("profiles", {})
        root.setdefault("memory_facts", {})
        root.setdefault("relationships", {})
        root.setdefault("guild_style", {})
        root.setdefault("reflections", {})
        root.setdefault("fun_modes", {})
        root.setdefault("capabilities", {})
        root.setdefault("privacy", {"paused_user_ids": [], "audit_log": []})
        root.setdefault("telemetry", {})
        root.setdefault(
            "prompt_injection",
            {
                "master_prompt": "",
                "master_learning_mode": "full",
                "guild_prompts": {},
                "guild_learning_modes": {},
                "audit_log": [],
            },
        )
        root.setdefault("warmup", {})
        root.setdefault("self_config", {})
        root.setdefault("self_edit_log", [])
        shadow = root.setdefault("shadow_brain", {})
        shadow.setdefault("events", [])
        shadow.setdefault("last_plan_text", "")
        shadow.setdefault("last_plan_attempt_input_ts", 0.0)
        shadow.setdefault("last_plan_attempt_pending_count", 0)
        shadow.setdefault("last_plan_attempt_candidates_n", 0)
        dm = root.setdefault("dm_brain", {})
        dm.setdefault("events", [])
        hive = root.setdefault("hive_brain", {})
        hive.setdefault("notes", [])
        hive.setdefault("last_sync_ts", 0.0)
        hive.setdefault("last_attempt_input_ts", 0.0)
        hive.setdefault("last_success_input_ts", 0.0)
        return root

    def _extract_json_object(self, raw: str) -> dict[str, Any] | None:
        text = str(raw or "").strip()
        if not text:
            return None
        parsed = self._try_json(text)
        if parsed is not None:
            return parsed
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fence:
            parsed = self._try_json(fence.group(1))
            if parsed is not None:
                return parsed
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return self._try_json(text[start : end + 1])
        return None

    def _try_json(self, raw: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed
