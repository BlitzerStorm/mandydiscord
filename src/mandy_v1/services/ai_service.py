from __future__ import annotations

import json
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
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


@dataclass
class ApiTestResult:
    ok: bool
    detail: str
    latency_ms: int | None


class AIService:
    def __init__(self, settings: Settings, store: MessagePackStore) -> None:
        self.settings = settings
        self.store = store
        self._recent_by_channel: dict[int, deque[str]] = defaultdict(lambda: deque(maxlen=20))
        self._alias_regex = re.compile(r"\b(?:mandy|mandi|mndy|mdy|mandee)\b", re.IGNORECASE)
        self._negative_regex = re.compile("|".join(re.escape(term) for term in NEGATIVE_TERMS), re.IGNORECASE)

    def has_api_key(self) -> bool:
        return bool(self.settings.alibaba_api_key.strip())

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

    def capture_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        raw = message.clean_content.strip() or "(no text)"
        if message.attachments:
            raw += f" | attachments={len(message.attachments)}"
        line = f"{message.author.display_name}: {raw[:240]}"
        self._recent_by_channel[message.channel.id].append(line)

    def should_chat(self, message: discord.Message, bot_user_id: int) -> bool:
        return self._mentions_mandy(message, bot_user_id)

    def should_roast(self, message: discord.Message, bot_user_id: int) -> bool:
        if not self._mentions_mandy(message, bot_user_id):
            return False
        content = message.content.strip()
        if not content:
            return False
        return bool(self._negative_regex.search(content))

    async def generate_chat_reply(self, message: discord.Message) -> str:
        recent = self.recent_context(message.channel.id, limit=5)
        memory = self._long_term_recent(message.guild.id if message.guild else 0, limit=3)
        prompt = (
            "You are Mandy, a concise Discord assistant. "
            "Reply naturally, keep it short, and avoid roleplay fluff."
        )
        user_prompt = (
            f"User: {message.author.display_name} ({message.author.id})\n"
            f"Message: {message.clean_content[:500]}\n"
            f"Recent channel context:\n{self._format_lines(recent)}\n"
            f"Long-term memory:\n{self._format_lines(memory)}"
        )
        generated = await self._try_completion(prompt, user_prompt, max_tokens=180)
        if not generated:
            generated = f"{message.author.mention} I am online and watching this channel."
        self._remember_exchange(message, generated)
        return generated[:1800]

    async def generate_roast_reply(self, message: discord.Message) -> str:
        recent = self.recent_context(message.channel.id, limit=5)
        prompt = (
            "You are Mandy. Reply with a short reverse-psychology roast. "
            "Keep it non-hateful, no slurs, no threats, and no protected-class attacks."
        )
        user_prompt = (
            f"Target user: {message.author.display_name} ({message.author.id})\n"
            f"Offending line: {message.clean_content[:500]}\n"
            f"Recent context:\n{self._format_lines(recent)}"
        )
        generated = await self._try_completion(prompt, user_prompt, max_tokens=120)
        if not generated:
            generated = (
                f"{message.author.mention} if Mandy bothers you that much, "
                "you are already giving her your full attention. That is called admiration."
            )
        self._remember_exchange(message, generated)
        return generated[:1500]

    async def test_api(self) -> ApiTestResult:
        started = time.perf_counter()
        if not self.has_api_key():
            result = ApiTestResult(ok=False, detail="ALIBABA_API_KEY is missing in passwords.txt.", latency_ms=None)
            self._save_api_test(result)
            return result
        try:
            output = await self._chat_completion(
                [
                    {"role": "system", "content": "You are a health check endpoint. Reply with exactly: OK"},
                    {"role": "user", "content": "health-check"},
                ],
                max_tokens=16,
                temperature=0.0,
            )
            latency = int((time.perf_counter() - started) * 1000)
            result = ApiTestResult(ok=True, detail=f"API reachable. Model response: {output[:120]}", latency_ms=latency)
            self._save_api_test(result)
            return result
        except Exception as exc:  # noqa: BLE001
            latency = int((time.perf_counter() - started) * 1000)
            result = ApiTestResult(ok=False, detail=f"API test failed: {exc}", latency_ms=latency)
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
        if not self.has_api_key():
            return None
        try:
            return await self._chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.7,
            )
        except Exception:  # noqa: BLE001
            return None

    async def _chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 180,
        temperature: float = 0.7,
    ) -> str:
        if not self.has_api_key():
            raise RuntimeError("Alibaba API key is not configured.")
        base = self.settings.alibaba_base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            url = base
        else:
            url = f"{base}/chat/completions"
        payload = {
            "model": self.settings.alibaba_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.alibaba_api_key}",
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
                    text = str(item.get("text", "")).strip()
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

    def _format_lines(self, lines: list[str]) -> str:
        if not lines:
            return "(none)"
        return "\n".join(f"- {line[:300]}" for line in lines)

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
        return root
