from __future__ import annotations

import json
import os
import random
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import discord

from mandy_v1.config import Settings
from mandy_v1.storage import MessagePackStore


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

DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODELS = ("qwen-plus", "qwen-max", "qwen-turbo")
DEFAULT_VISION_MODELS = ("qwen-vl-plus", "qwen-vl-max", "qwen2.5-vl-72b-instruct")
ENV_KEY_NAMES = ("ALIBABA_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "AI_API_KEY")
PASSWORDS_KEY_NAMES = ("ALIBABA_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "AI_API_KEY", "API_KEY")

WARMUP_CHANNEL_LIMIT = 4
WARMUP_MESSAGES_PER_CHANNEL = 40
STILL_TALKING_WINDOW_SEC = 45
BOT_ACTION_COOLDOWN_SEC = 9
BOT_REPLY_CONTINUE_WINDOW_SEC = 90
USER_BURST_WINDOW_SEC = 35
USER_REPLY_MIN_GAP_SEC = 12


@dataclass
class ApiTestResult:
    ok: bool
    detail: str
    latency_ms: int | None


@dataclass
class ChatDirective:
    action: str  # ignore | react | reply
    reason: str
    emoji: str | None = None
    still_talking: bool = False


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
        self._alias_regex = re.compile(r"\b(?:mandy|mandi|mndy|mdy|mandee)\b", re.IGNORECASE)
        self._negative_regex = re.compile("|".join(re.escape(term) for term in NEGATIVE_TERMS), re.IGNORECASE)
        self._emotional_regex = re.compile(r"\b(?:lol|lmao|omg|wow|damn|nice|thanks|wtf|bro|bruh)\b", re.IGNORECASE)
        self._direct_request_regex = re.compile(
            r"\b(?:can you|could you|would you|you should|you think|help me|tell me|rate this|analyze this|what do you think)\b",
            re.IGNORECASE,
        )
        self._passwords_cache: dict[str, str] | None = None
        self._rng = random.Random()

    def has_api_key(self) -> bool:
        key, _source = self._resolve_api_key()
        return bool(key)

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

    def capture_message(self, message: discord.Message, *, touch: bool = True) -> None:
        if not message.guild or message.author.bot:
            return
        now = time.time()
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
            }
        )
        self._update_turn_state(message.channel.id, message.author.id, now)
        self._update_profile(message, touch=touch)

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
            if burst_count >= 2:
                return ChatDirective(action="reply", reason="image_burst", still_talking=True)
            return ChatDirective(action="reply", reason="image_scan", still_talking=still_talking)

        if mention_hit:
            if user_reply_gap < USER_REPLY_MIN_GAP_SEC and burst_count <= 1:
                return ChatDirective(action="ignore", reason="user_recently_replied", still_talking=still_talking)
            if burst_count >= 2:
                return ChatDirective(action="reply", reason="mention_burst", still_talking=True)
            return ChatDirective(action="reply", reason="mention", still_talking=still_talking)

        if direct_request:
            if channel_cooldown and burst_count <= 1:
                return ChatDirective(action="ignore", reason="cooldown_direct_request")
            if burst_count >= 2:
                return ChatDirective(action="reply", reason="direct_request_burst", still_talking=True)
            return ChatDirective(action="reply", reason="direct_request", still_talking=still_talking)

        if channel_cooldown:
            return ChatDirective(action="ignore", reason="cooldown")

        if still_talking and recent_bot_reply and self._chance(0.40):
            if burst_count >= 2:
                return ChatDirective(action="reply", reason="continuation_burst", still_talking=True)
            return ChatDirective(action="react", reason="continuation_react", emoji=self._pick_reaction_emoji(content), still_talking=True)

        if question and self._chance(0.25):
            return ChatDirective(action="reply", reason="question", still_talking=still_talking)

        if emotional and self._chance(0.20):
            return ChatDirective(action="react", reason="emotional_reaction", emoji=self._pick_reaction_emoji(content))

        if self._chance(0.06):
            return ChatDirective(action="react", reason="ambient_presence", emoji=self._pick_reaction_emoji(content))

        return ChatDirective(action="ignore", reason="no_trigger")

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
        recent = self.recent_context(message.channel.id, limit=6)
        memory = self._long_term_recent(message.guild.id if message.guild else 0, limit=4)
        profile = self._profile_summary(message.guild.id if message.guild else 0, message.author.id)
        burst = burst_lines if burst_lines is not None else self.user_burst_lines(message.channel.id, message.author.id, limit=5)
        image_urls = self._extract_image_urls(message, max_images=2)
        prompt = (
            "You are Mandy: a sharp, adaptive female teen with a mafia-boss vibe. "
            "Confident, witty, in control. Respond naturally, concise by default, and context-aware."
        )
        user_prompt = (
            f"Trigger reason: {reason or 'chat'}\n"
            f"Still talking: {still_talking}\n"
            f"User: {message.author.display_name} ({message.author.id})\n"
            f"User profile: {profile}\n"
            f"Message: {message.clean_content[:500]}\n"
            f"Recent same-user burst:\n{self._format_lines(burst)}\n"
            f"Recent channel context:\n{self._format_lines(recent)}\n"
            f"Long-term memory:\n{self._format_lines(memory)}"
        )
        generated: str | None = None
        if image_urls:
            user_prompt = (
                f"{user_prompt}\n"
                "The user sent image(s). Give a quick visual analysis first, then your short reply."
            )
            generated = await self._try_vision_completion(
                system_prompt=prompt,
                user_prompt=user_prompt,
                image_urls=image_urls,
                max_tokens=220,
            )
        if not generated:
            generated = await self._try_completion(prompt, user_prompt, max_tokens=220)
        if not generated:
            generated = f"{message.author.mention} I am tracking this thread. Keep going."
        self._remember_exchange(message, generated)
        return generated

    async def generate_roast_reply(self, message: discord.Message) -> str:
        recent = self.recent_context(message.channel.id, limit=5)
        profile = self._profile_summary(message.guild.id if message.guild else 0, message.author.id)
        prompt = (
            "You are Mandy. Reply with a short reverse-psychology roast. "
            "Keep it non-hateful, no slurs, no threats, and no protected-class attacks."
        )
        user_prompt = (
            f"Target user: {message.author.display_name} ({message.author.id})\n"
            f"User profile: {profile}\n"
            f"Offending line: {message.clean_content[:500]}\n"
            f"Recent context:\n{self._format_lines(recent)}"
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
                        {"role": "system", "content": "You are a health check endpoint. Reply with exactly: OK"},
                        {"role": "user", "content": "health-check"},
                    ],
                    max_tokens=16,
                    temperature=0.0,
                    api_key=api_key,
                    model=model,
                )
                self._ai_root()["auto_model"] = model
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

    def _mentions_mandy(self, message: discord.Message, bot_user_id: int) -> bool:
        if any(user.id == bot_user_id for user in message.mentions):
            return True
        if message.reference and isinstance(message.reference.resolved, discord.Message):
            if message.reference.resolved.author.id == bot_user_id:
                return True
        return bool(self._alias_regex.search(message.content))

    async def _try_completion(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str | None:
        api_key, _source = self._resolve_api_key()
        if not api_key:
            return None
        for model in self._model_candidates():
            try:
                output = await self._chat_completion(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=0.7,
                    api_key=api_key,
                    model=model,
                )
                self._ai_root()["auto_model"] = model
                self.store.touch()
                return output
            except Exception:  # noqa: BLE001
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
        user_content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for image_url in image_urls:
            user_content.append({"type": "image_url", "image_url": {"url": image_url}})
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        for model in self._vision_model_candidates():
            try:
                output = await self._chat_completion(
                    messages,
                    max_tokens=max_tokens,
                    temperature=0.5,
                    api_key=api_key,
                    model=model,
                )
                self._ai_root()["auto_vision_model"] = model
                self.store.touch()
                return output
            except Exception:  # noqa: BLE001
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
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
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
            "ts": datetime.now(tz=timezone.utc).isoformat(),
        }
        self.store.touch()

    def _remember_exchange(self, message: discord.Message, bot_reply: str) -> None:
        if not message.guild:
            return
        root = self._ai_root()
        memories = root.setdefault("long_term_memory", {})
        rows = memories.setdefault(str(message.guild.id), [])
        rows.append(
            {
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "user_id": message.author.id,
                "user_text": message.clean_content[:280],
                "bot_text": bot_reply[:280],
            }
        )
        if len(rows) > 200:
            del rows[: len(rows) - 200]
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
        samples = profile.get("samples", [])
        sample_text = ""
        if isinstance(samples, list) and samples:
            sample_text = str(samples[-1])[:120]
        return f"messages={count} avg_len={avg_len} tags=[{tags}] sample={sample_text}"

    def _update_profile(self, message: discord.Message, *, touch: bool) -> None:
        if not message.guild:
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

    def _format_lines(self, lines: list[str]) -> str:
        if not lines:
            return "(none)"
        return "\n".join(f"- {line[:300]}" for line in lines)

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
        root.setdefault("warmup", {})
        return root
