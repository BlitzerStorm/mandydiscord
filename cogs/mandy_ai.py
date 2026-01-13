
import asyncio
import datetime
import json
import os
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord.ext import commands

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:
    genai = None
    genai_types = None

from capability_registry import CapabilityRegistry
from resolver import GuildIndexCache, pick_best, rank_members
from extensions.validator import validate_extension_path, validate_extension_source

try:
    from intelligent_command_processor import IntelligentCommandProcessor
    INTELLIGENT_PROCESSOR_AVAILABLE = True
except ImportError:
    INTELLIGENT_PROCESSOR_AVAILABLE = False
from intelligence_layer import UniversalIntelligenceLayer

MAX_MESSAGE_LEN = 1900
MANDY_MIN_LEVEL = 90
AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac")
BACKOFF_STEPS = [10, 30, 60, 120, 240, 480, 600]
FAST_STATUS_STATES = {"online", "idle", "dnd", "invisible"}
WINDOW_ALIASES = {
    "today": "daily",
    "day": "daily",
    "daily": "daily",
    "week": "weekly",
    "weekly": "weekly",
    "month": "monthly",
    "monthly": "monthly",
    "year": "yearly",
    "yearly": "yearly",
    "rolling24": "rolling24",
    "rolling_24h": "rolling24",
    "rolling-24h": "rolling24",
    "rolling24h": "rolling24",
}

ALLOWED_INTENTS = {"TALK", "ACTION", "NEEDS_CONFIRMATION", "DESIGN_TOOL", "BUILD_TOOL"}
SCHEMA_KEYS = {"intent", "response", "actions", "tool_design", "build", "confirm"}

class GeminiRateLimitError(Exception):
    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after

class LocalRateLimitError(Exception):
    def __init__(self, wait_seconds: float):
        super().__init__(f"local rate limit: wait {wait_seconds}s")
        self.wait_seconds = float(wait_seconds)

def _now_ts() -> int:
    return int(time.time())

def _format_wait(seconds: float) -> str:
    secs = max(0, int(seconds))
    mins, sec = divmod(secs, 60)
    hrs, mins = divmod(mins, 60)
    if hrs:
        return f"{hrs}h {mins}m {sec}s"
    if mins:
        return f"{mins}m {sec}s"
    return f"{sec}s"

def _chunk_text(text: str, limit: int = MAX_MESSAGE_LEN) -> List[str]:
    if not text:
        return [""]
    chunks: List[str] = []
    cur = ""
    for line in text.splitlines():
        line = line.rstrip()
        if len(cur) + len(line) + 1 > limit:
            if cur:
                chunks.append(cur)
                cur = ""
        if len(line) > limit:
            for i in range(0, len(line), limit):
                chunks.append(line[i : i + limit])
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        chunks.append(cur)
    return chunks

class GeminiClient:
    def __init__(self, api_key: Optional[str]):
        self.api_key = api_key or ""
        self.available = bool(self.api_key and genai is not None)
        self._client = genai.Client(api_key=self.api_key) if self.available else None

    def _build_contents(
        self,
        system_prompt: str,
        user_prompt: str,
        audio_bytes: Optional[bytes] = None,
        audio_mime: Optional[str] = None,
    ):
        prompt = (system_prompt or "").strip() + "\n\nUSER:\n" + (user_prompt or "").strip()
        if audio_bytes and genai_types:
            part = genai_types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime or "audio/wav")
            return [part, prompt]
        return prompt

    def _extract_text(self, resp: Any) -> str:
        if resp is None:
            return ""
        text = getattr(resp, "text", None)
        if isinstance(text, str) and text.strip():
            return text
        try:
            cands = resp.candidates or []
            if not cands:
                return ""
            parts = cands[0].content.parts or []
            out = []
            for part in parts:
                t = getattr(part, "text", None)
                if isinstance(t, str):
                    out.append(t)
            return "".join(out).strip()
        except Exception:
            return ""

    def _is_rate_limit_error(self, exc: Exception) -> Tuple[bool, Optional[float]]:
        msg = str(exc).lower()
        is_rate = "429" in msg or "rate limit" in msg or "quota" in msg or "resource exhausted" in msg
        retry_after = self._extract_retry_after(exc, msg)
        return is_rate, retry_after

    def _extract_retry_after(self, exc: Exception, msg: str) -> Optional[float]:
        retry_after = getattr(exc, "retry_after", None)
        if isinstance(retry_after, (int, float)) and retry_after > 0:
            return float(retry_after)
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) if response else None
        if headers:
            raw = headers.get("Retry-After") or headers.get("retry-after")
            try:
                return float(raw)
            except Exception:
                pass
        match = re.search(r"retry-?after[:=]\s*(\d+)", msg)
        if match:
            try:
                return float(match.group(1))
            except Exception:
                return None
        return None

    def _generate_sync(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        response_format: Optional[str],
        audio_bytes: Optional[bytes],
        audio_mime: Optional[str],
    ):
        contents = self._build_contents(system_prompt, user_prompt, audio_bytes, audio_mime)
        if genai_types:
            mime = "application/json" if response_format == "json" else "text/plain"
            config = genai_types.GenerateContentConfig(response_mime_type=mime, temperature=0.2)
            return self._client.models.generate_content(model=model, contents=contents, config=config)
        return self._client.models.generate_content(model=model, contents=contents)

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        response_format: Optional[str] = None,
        audio_bytes: Optional[bytes] = None,
        audio_mime: Optional[str] = None,
        timeout: float = 60.0,
        retries: int = 2,
    ) -> str:
        if not self.available:
            raise RuntimeError("Gemini SDK not available or API key missing")
        loop = asyncio.get_running_loop()
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                resp = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        self._generate_sync,
                        system_prompt,
                        user_prompt,
                        model,
                        response_format,
                        audio_bytes,
                        audio_mime,
                    ),
                    timeout=timeout,
                )
                text = self._extract_text(resp)
                if not text:
                    raise RuntimeError("Empty response from Gemini")
                return text
            except Exception as exc:
                is_rate, retry_after = self._is_rate_limit_error(exc)
                if is_rate:
                    raise GeminiRateLimitError(str(exc), retry_after=retry_after)
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(0.4 * (attempt + 1))
        raise last_exc or RuntimeError("Gemini request failed")

class RateLimitView(discord.ui.View):
    def __init__(self, cog, job_id: str, user_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.job_id = job_id
        self.user_id = user_id

    @discord.ui.button(label="WAIT", style=discord.ButtonStyle.primary)
    async def wait_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not authorized.", ephemeral=True)
        await self.cog.accept_job(self.job_id)
        await interaction.response.edit_message(content="Queued. Will retry automatically.", view=None)

    @discord.ui.button(label="CANCEL", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not authorized.", ephemeral=True)
        await self.cog.cancel_job(self.job_id)
        await interaction.response.edit_message(content="Cancelled.", view=None)

class ConfirmView(discord.ui.View):
    def __init__(self, cog, user_id: int, channel_id: int, query: str):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.channel_id = channel_id
        self.query = query

    @discord.ui.button(label="CONFIRM", style=discord.ButtonStyle.success)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not authorized.", ephemeral=True)
        await interaction.response.edit_message(content="Confirmed. Processing...", view=None)
        await self.cog.confirm_request(self.user_id, self.channel_id, self.query)

    @discord.ui.button(label="CANCEL", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not authorized.", ephemeral=True)
        await interaction.response.edit_message(content="Cancelled.", view=None)

    @discord.ui.button(label="WAIT", style=discord.ButtonStyle.secondary)
    async def wait_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not authorized.", ephemeral=True)
        await interaction.response.send_message("Waiting. Use CONFIRM when ready.", ephemeral=True)

class UserPickView(discord.ui.View):
    def __init__(self, cog, requester_id: int, action: Dict[str, Any], candidates: List[Tuple[int, str]]):
        super().__init__(timeout=120)
        self.cog = cog
        self.requester_id = requester_id
        self.action = action
        for idx, (uid, label) in enumerate(candidates[:5], start=1):
            btn = discord.ui.Button(label=f"{idx}. {label}", style=discord.ButtonStyle.secondary)
            async def callback(interaction: discord.Interaction, picked_id: int = uid, picked_label: str = label):
                if interaction.user.id != self.requester_id:
                    return await interaction.response.send_message("Not authorized.", ephemeral=True)
                await interaction.response.edit_message(
                    content=f"Selected {picked_label}. Processing...",
                    view=None,
                )
                await self.cog.handle_user_pick(self.action, picked_id, interaction.channel, interaction.guild, interaction.user)
            btn.callback = callback
            self.add_item(btn)
        cancel = discord.ui.Button(label="CANCEL", style=discord.ButtonStyle.danger)
        async def cancel_callback(interaction: discord.Interaction):
            if interaction.user.id != self.requester_id:
                return await interaction.response.send_message("Not authorized.", ephemeral=True)
            await interaction.response.edit_message(content="Cancelled.", view=None)
        cancel.callback = cancel_callback
        self.add_item(cancel)

class _ManydAICommandExecutor:
    """Executor for intelligent command processor - bridges to MandyAI tools."""
    
    def __init__(self, mandy_cog, user, guild, channel):
        self.mandy = mandy_cog
        self.user = user
        self.guild = guild
        self.channel = channel
    
    async def send_dm(self, targets: List[int], text: str) -> bool:
        """Send DM to multiple users."""
        try:
            actions = [{"tool": "send_dm", "args": {"user_id": uid, "text": text}} for uid in targets]
            results = await self.mandy._execute_actions(
                self.user.id,
                actions,
                guild=self.guild,
                channel=self.channel
            )
            ok_count = sum(1 for r in results if "OK" in r)
            err_lines = [r for r in results if "ERROR" in r]
            summary = f"✅ DMs sent: {ok_count}/{len(results)}"
            if err_lines:
                summary += "\n❌ Errors:\n" + "\n".join(err_lines[:5])
            await self.mandy._send_chunks(self.channel, summary)
            return True
        except Exception as e:
            await self.mandy._send_chunks(self.channel, f"❌ Error sending DMs: {e}")
            return False
    
    async def add_watcher(self, target_user_id: int, count: int, text: str) -> bool:
        """Add a watcher for a user."""
        try:
            actions = [{"tool": "add_watcher", "args": {"target_user_id": target_user_id, "count": count, "text": text}}]
            results = await self.mandy._execute_actions(
                self.user.id,
                actions,
                guild=self.guild,
                channel=self.channel
            )
            summary = "\n".join(results)
            await self.mandy._send_chunks(self.channel, summary)
            return True
        except Exception as e:
            await self.mandy._send_chunks(self.channel, f"❌ Error adding watcher: {e}")
            return False
    
    async def remove_watcher(self, target_user_id: int) -> bool:
        """Remove a watcher."""
        try:
            actions = [{"tool": "remove_watcher", "args": {"target_user_id": target_user_id}}]
            results = await self.mandy._execute_actions(
                self.user.id,
                actions,
                guild=self.guild,
                channel=self.channel
            )
            summary = "\n".join(results)
            await self.mandy._send_chunks(self.channel, summary)
            return True
        except Exception as e:
            await self.mandy._send_chunks(self.channel, f"❌ Error removing watcher: {e}")
            return False
    
    async def list_watchers(self) -> bool:
        """List all watchers."""
        try:
            actions = [{"tool": "list_watchers", "args": {}}]
            results = await self.mandy._execute_actions(
                self.user.id,
                actions,
                guild=self.guild,
                channel=self.channel
            )
            summary = "\n".join(results)
            await self.mandy._send_chunks(self.channel, summary)
            return True
        except Exception as e:
            await self.mandy._send_chunks(self.channel, f"❌ Error listing watchers: {e}")
            return False
    
    async def create_mirror(self, source: str, dest: str) -> bool:
        """Create a mirror between channels."""
        try:
            source_id = self._parse_channel(source)
            dest_id = self._parse_channel(dest)
            
            if not source_id or not dest_id:
                await self.mandy._send_chunks(self.channel, "❌ Could not parse channel references")
                return False
            
            actions = [{
                "tool": "create_mirror",
                "args": {
                    "source_channel_id": source_id,
                    "dest_channel_id": dest_id
                }
            }]
            
            results = await self.mandy._execute_actions(
                self.user.id,
                actions,
                guild=self.guild,
                channel=self.channel
            )
            summary = "\n".join(results)
            await self.mandy._send_chunks(self.channel, summary)
            return True
        except Exception as e:
            await self.mandy._send_chunks(self.channel, f"❌ Error creating mirror: {e}")
            return False
    
    async def show_stats(self, scope: str = "daily", user_id: int = None) -> bool:
        """Get statistics."""
        try:
            args = {"scope": scope}
            if user_id:
                args["user_id"] = user_id
            
            actions = [{"tool": "show_stats", "args": args}]
            results = await self.mandy._execute_actions(
                self.user.id,
                actions,
                guild=self.guild,
                channel=self.channel
            )
            summary = "\n".join(results)
            await self.mandy._send_chunks(self.channel, summary)
            return True
        except Exception as e:
            await self.mandy._send_chunks(self.channel, f"❌ Error showing stats: {e}")
            return False
    
    async def show_health(self) -> bool:
        """Get bot health status."""
        try:
            report = self.mandy._health_report()
            await self.mandy._send_chunks(self.channel, report)
            return True
        except Exception as e:
            await self.mandy._send_chunks(self.channel, f"❌ Error getting health: {e}")
            return False
    
    async def show_queue(self) -> bool:
        """Get queue status."""
        try:
            report = self.mandy._queue_report()
            await self.mandy._send_chunks(self.channel, report)
            return True
        except Exception as e:
            await self.mandy._send_chunks(self.channel, f"❌ Error getting queue: {e}")
            return False
    
    def _parse_channel(self, channel_ref: str) -> Optional[int]:
        """Parse channel reference to ID."""
        mention_match = re.match(r"<#(\d+)>", channel_ref)
        if mention_match:
            return int(mention_match.group(1))
        
        if channel_ref.isdigit():
            return int(channel_ref)
        
        name = channel_ref.lstrip("#")
        if self.guild:
            for chan in self.guild.channels:
                if chan.name == name:
                    return chan.id
        
        return None

class MandyAI(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tools = getattr(bot, "mandy_tools", None)
        self.registry = getattr(bot, "mandy_registry", None)
        self.plugin_manager = getattr(bot, "mandy_plugin_manager", None)
        self._cfg_fn = getattr(bot, "mandy_get_ai_config", None)
        self._cfg_root_fn = getattr(bot, "mandy_cfg", None)
        self.store = getattr(bot, "mandy_store", None)
        self.log_to = getattr(bot, "mandy_log_to", None)
        self.audit = getattr(bot, "mandy_audit", None)
        self._require_level_ctx = getattr(bot, "mandy_require_level_ctx", None)
        self._effective_level = getattr(bot, "mandy_effective_level", None)
        self.client = GeminiClient(getattr(bot, "mandy_api_key", None))
        self._cooldowns: Dict[int, float] = {}
        self._usage: Dict[str, Dict[str, Dict[str, float]]] = {"rolling": {}, "daily": {}}
        self._queue_tasks: Dict[str, asyncio.Task] = {}
        self._queue_lock = asyncio.Lock()
        self._pending_designs: Dict[int, Dict[str, Any]] = {}
        self._runtime = getattr(bot, "mandy_runtime", None)
        if self._runtime is None:
            self._runtime = {"counters": {}, "last_actions": [], "last_rate_limit": None}
            bot.mandy_runtime = self._runtime
        if not self.registry and self.tools:
            self.registry = CapabilityRegistry(self.tools)
        
        # Initialize intelligent command processor
        self._intelligent_processor = None
        self._intelligence = None
        self._resolver_cache = GuildIndexCache(ttl_seconds=120)
        if INTELLIGENT_PROCESSOR_AVAILABLE:
            try:
                self._intelligent_processor = IntelligentCommandProcessor(bot)
            except Exception as e:
                print(f"Warning: Could not initialize intelligent processor: {e}")
        try:
            self._intelligence = UniversalIntelligenceLayer(
                bot=self.bot,
                tools=self.tools,
                registry=self.registry,
                execute_actions=self._execute_actions,
                send_func=self._send_chunks,
                is_god=self._is_god_user,
                local_actions={
                    "health": self._health_report,
                    "queue": self._queue_report,
                },
            )
        except Exception as e:
            print(f"Warning: Could not initialize intelligence layer: {e}")

    def _cfg(self) -> Dict[str, Any]:
        if callable(self._cfg_fn):
            return self._cfg_fn()
        return {}

    def _cfg_root(self) -> Dict[str, Any]:
        if callable(self._cfg_root_fn):
            return self._cfg_root_fn()
        return {}

    async def _mark_dirty(self):
        if self.store:
            await self.store.mark_dirty()

    def _router_model(self) -> str:
        ai = self._cfg()
        return str(ai.get("router_model") or ai.get("default_model") or "gemini-2.5-flash-lite")

    def _default_model(self) -> str:
        ai = self._cfg()
        return str(ai.get("default_model") or "gemini-2.5-flash-lite")

    def _cooldown_seconds(self) -> int:
        ai = self._cfg()
        try:
            return max(1, int(ai.get("cooldown_seconds", 5)))
        except Exception:
            return 5

    def _limits_for(self, model: str) -> Dict[str, Any]:
        ai = self._cfg()
        return ai.get("limits", {}).get(model, {})

    def _queue(self) -> Dict[str, Any]:
        ai = self._cfg()
        return ai.setdefault("queue", {})

    def _queue_counts(self) -> Dict[str, int]:
        counts = {"pending": 0, "waiting": 0, "running": 0}
        for job in self._queue().values():
            status = str(job.get("status", "pending"))
            if status in counts:
                counts[status] += 1
        return counts

    def _counter_inc(self, key: str, value: int = 1) -> None:
        counters = self._runtime.setdefault("counters", {})
        counters[key] = int(counters.get(key, 0)) + int(value)

    def _record_action(self, entry: Dict[str, Any]) -> None:
        actions = self._runtime.setdefault("last_actions", [])
        actions.append(entry)
        while len(actions) > 5:
            actions.pop(0)

    def _record_rate_limit(self, wait_seconds: float, source: str) -> None:
        self._runtime["last_rate_limit"] = {
            "at": _now_ts(),
            "wait_seconds": int(wait_seconds),
            "source": source,
        }
        self._counter_inc("rate_limits", 1)

    def _capabilities_snapshot(self, guild: Optional[discord.Guild] = None, channel: Optional[discord.abc.Messageable] = None) -> Dict[str, Any]:
        registry = self.registry or (CapabilityRegistry(self.tools) if self.tools else None)
        ai = self._cfg()
        installed = list(ai.get("installed_extensions", []) or [])
        for mod in self.bot.extensions.keys():
            if mod not in installed:
                installed.append(mod)
        dynamic_tools = []
        if self.tools:
            dynamic_tools = self.tools.list_dynamic_tools()
        snapshot = {
            "tools": registry.snapshot() if registry else [],
            "dynamic_tools": dynamic_tools,
            "installed_extensions": installed,
            "models": {
                "default": ai.get("default_model"),
                "router": ai.get("router_model"),
                "tts": ai.get("tts_model") or "",
            },
            "queue": {
                "length": len(self._queue()),
                "counts": self._queue_counts(),
            },
            "counters": dict(self._runtime.get("counters", {})),
            "scope": {
                "guild_id": getattr(guild, "id", 0) if guild else 0,
                "channel_id": getattr(channel, "id", 0) if channel else 0,
            },
        }
        return snapshot

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 1
        return max(1, int(len(text) / 4))

    def _check_local_limits(self, model: str, tokens: int) -> float:
        limits = self._limits_for(model)
        rpm = int(limits.get("rpm", 0) or 0)
        tpm = int(limits.get("tpm", 0) or 0)
        rpd = int(limits.get("rpd", 0) or 0)

        now = time.time()
        rolling = self._usage["rolling"].setdefault(model, {"start": now, "count": 0, "tokens": 0})
        if now - float(rolling.get("start", now)) >= 60:
            rolling["start"] = now
            rolling["count"] = 0
            rolling["tokens"] = 0

        wait = 0.0
        if rpm and int(rolling.get("count", 0)) >= rpm:
            wait = max(wait, 60 - (now - float(rolling.get("start", now))))
        if tpm and int(rolling.get("tokens", 0)) + tokens > tpm:
            wait = max(wait, 60 - (now - float(rolling.get("start", now))))

        day_key = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        daily = self._usage["daily"].setdefault(model, {"date": day_key, "count": 0, "tokens": 0})
        if daily.get("date") != day_key:
            daily["date"] = day_key
            daily["count"] = 0
            daily["tokens"] = 0
        if rpd and int(daily.get("count", 0)) >= rpd:
            tomorrow = (datetime.datetime.utcnow() + datetime.timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            wait = max(wait, (tomorrow - datetime.datetime.utcnow()).total_seconds())

        return max(0.0, wait)

    def _record_usage(self, model: str, tokens_in: int, tokens_out: int):
        now = time.time()
        rolling = self._usage["rolling"].setdefault(model, {"start": now, "count": 0, "tokens": 0})
        if now - float(rolling.get("start", now)) >= 60:
            rolling["start"] = now
            rolling["count"] = 0
            rolling["tokens"] = 0
        rolling["count"] = int(rolling.get("count", 0)) + 1
        rolling["tokens"] = int(rolling.get("tokens", 0)) + int(tokens_in) + int(tokens_out)

        day_key = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        daily = self._usage["daily"].setdefault(model, {"date": day_key, "count": 0, "tokens": 0})
        if daily.get("date") != day_key:
            daily["date"] = day_key
            daily["count"] = 0
            daily["tokens"] = 0
        daily["count"] = int(daily.get("count", 0)) + 1
        daily["tokens"] = int(daily.get("tokens", 0)) + int(tokens_in) + int(tokens_out)

    def _backoff_seconds(self, attempts: int) -> int:
        idx = min(max(0, attempts), len(BACKOFF_STEPS) - 1)
        return BACKOFF_STEPS[idx]

    def _has_audit_channel(self) -> bool:
        cfg = self._cfg_root()
        return bool(cfg.get("logs", {}).get("audit"))

    async def _log(self, text: str):
        if callable(self.log_to):
            await self.log_to("audit", text[:MAX_MESSAGE_LEN])
        if not self._has_audit_channel():
            print(text)

    async def _send_chunks(self, channel: discord.abc.Messageable, text: str):
        for chunk in _chunk_text(text, MAX_MESSAGE_LEN):
            if chunk:
                await channel.send(chunk)

    async def _notify_user(self, user_id: int, channel_id: int, text: str):
        user = self.bot.get_user(user_id)
        if not user:
            try:
                user = await self.bot.fetch_user(user_id)
            except Exception:
                user = None
        if user:
            try:
                await self._send_chunks(user, text)
                return
            except Exception:
                pass
        ch = self.bot.get_channel(channel_id)
        if not ch:
            try:
                ch = await self.bot.fetch_channel(channel_id)
            except Exception:
                return
        await self._send_chunks(ch, text)

    async def _is_god_user(self, user: discord.User) -> bool:
        if callable(self._effective_level):
            try:
                lvl = await self._effective_level(user)
                return lvl >= MANDY_MIN_LEVEL
            except Exception:
                return False
        return False

    async def _require_god(self, ctx: commands.Context) -> bool:
        if callable(self._require_level_ctx):
            return await self._require_level_ctx(ctx, MANDY_MIN_LEVEL)
        if callable(self._effective_level):
            lvl = await self._effective_level(ctx.author)
            return lvl >= MANDY_MIN_LEVEL
        return False

    def _get_audio_attachment(self, message: discord.Message) -> Optional[discord.Attachment]:
        for att in message.attachments:
            name = (att.filename or "").lower()
            ctype = (att.content_type or "").lower()
            if ctype.startswith("audio/"):
                return att
            if any(name.endswith(ext) for ext in AUDIO_EXTS):
                return att
        return None

    async def _transcribe_audio(self, message: discord.Message) -> Optional[str]:
        att = self._get_audio_attachment(message)
        if not att:
            return None
        if not self.client.available or not genai_types:
            return "AUDIO_NOT_SUPPORTED"
        try:
            audio_bytes = await att.read()
        except Exception:
            return "AUDIO_NOT_SUPPORTED"

        model = self._default_model()
        sys_prompt = "You are a transcription engine. Return only the transcript text."
        user_prompt = "Transcribe the attached audio to plain text."
        tokens_in = self._estimate_tokens(sys_prompt + user_prompt)
        wait = self._check_local_limits(model, tokens_in)
        if wait > 0:
            raise LocalRateLimitError(wait)
        text = await self.client.generate(
            sys_prompt,
            user_prompt,
            model=model,
            response_format=None,
            audio_bytes=audio_bytes,
            audio_mime=att.content_type or "audio/wav",
            timeout=60.0,
        )
        tokens_out = self._estimate_tokens(text)
        self._record_usage(model, tokens_in, tokens_out)
        return text.strip()

    def _normalize_query(self, query: str) -> str:
        return " ".join((query or "").strip().split())

    def _extract_mention_user_id(self, text: str, message: Optional[discord.Message]) -> Optional[int]:
        if message and getattr(message, "mentions", None):
            if message.mentions:
                return int(message.mentions[0].id)
        match = re.search(r"<@!?(\d+)>", text or "")
        if match:
            try:
                return int(match.group(1))
            except Exception:
                return None
        return None

    def _normalize_name(self, text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (text or "").strip().lower())

    async def _resolve_user(
        self,
        guild: Optional[discord.Guild],
        text: str,
        message: Optional[discord.Message],
        author: Optional[discord.User] = None,
    ) -> Tuple[Optional[int], List[Tuple[int, str]]]:
        mention_id = self._extract_mention_user_id(text, message)
        if mention_id:
            return mention_id, []
        token = (text or "").strip().lstrip("@")
        token_norm = self._normalize_name(token)
        if token_norm in ("me", "myself", "self", "i") and author:
            return int(author.id), []
        if token.isdigit():
            return int(token), []
        if not guild or not token_norm:
            return None, []

        index = self._resolver_cache.get(guild)
        candidates = rank_members(guild, token, index=index)
        picked = pick_best(candidates)
        if picked:
            return int(picked), []
        if candidates:
            resolved = [(cand.entity_id, cand.label) for cand in candidates[:5]]
            return None, resolved

        fallback: List[Tuple[int, str]] = []
        try:
            if hasattr(guild, "query_members"):
                results = await guild.query_members(query=token, limit=5)
                for member in results or []:
                    label = member.display_name
                    if member.name and member.name != member.display_name:
                        label = f"{label} ({member.name})"
                    fallback.append((member.id, label))
        except Exception:
            pass
        if len(fallback) == 1:
            return int(fallback[0][0]), []
        return None, fallback

    def _infer_window(self, text: str) -> Optional[str]:
        lower = (text or "").lower()
        if re.search(r"(last|past)\s+24\s*(hours|hrs|h)", lower):
            return "rolling24"
        for key, value in WINDOW_ALIASES.items():
            if re.search(rf"\b{re.escape(key)}\b", lower):
                return value
        return None

    def _parse_watcher_add(self, text: str) -> Optional[Tuple[str, int, str]]:
        match = re.match(
            r"^(add\s+watcher|watcher\s+add)\s+(.+?)\s+after\s+(\d+)\s+say\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        user_text = match.group(2).strip()
        count = int(match.group(3))
        message = match.group(4).strip().strip("\"'")
        return user_text, count, message

    def _parse_watcher_remove(self, text: str) -> Optional[str]:
        match = re.match(r"^(remove\s+watcher|watcher\s+remove)\s+(.+)$", text, re.IGNORECASE)
        if not match:
            return None
        return match.group(2).strip()

    def _parse_status_set(self, text: str) -> Optional[Tuple[str, str]]:
        match = re.match(r"^(set\s+status|status\s+set)\s+(\w+)(?:\s+(.+))?$", text, re.IGNORECASE)
        if not match:
            return None
        state = match.group(2).strip().lower()
        if state not in FAST_STATUS_STATES:
            return None
        status_text = (match.group(3) or "").strip()
        return state, status_text

    def _parse_stats_query(self, text: str) -> Optional[Tuple[str, Optional[str]]]:
        window = self._infer_window(text) or "daily"
        match = re.search(r"messages\s+did\s+(.+?)\s+send", text, re.IGNORECASE)
        if match:
            return window, match.group(1).strip()
        if re.match(r"^(stats|show\s+stats)\b", text, re.IGNORECASE):
            return window, None
        return None

    def _parse_dm_request(self, text: str) -> Optional[Tuple[List[str], str]]:
        match = re.match(
            r"^(dm|message|privately\s+message)\s+(.+?)\s+\"(.+)\"$",
            text,
            re.IGNORECASE,
        )
        if match:
            targets = self._split_targets(match.group(2).strip())
            return targets, match.group(3).strip()
        match = re.match(
            r"^(dm|message|privately\s+message)\s+\"(.+)\"\s+to\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if match:
            targets = self._split_targets(match.group(3).strip())
            return targets, match.group(2).strip()
        match = re.match(
            r"^(dm|message|privately\s+message)\s+(.+?)\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if match:
            targets = self._split_targets(match.group(2).strip())
            return targets, match.group(3).strip()
        return None

    def _split_targets(self, text: str) -> List[str]:
        cleaned = re.sub(r"\s+and\s+", ",", text, flags=re.IGNORECASE)
        parts = [p.strip() for p in cleaned.split(",") if p.strip()]
        if len(parts) <= 1:
            return parts
        return parts

    def _parse_transcript_summarize(self, text: str) -> Optional[int]:
        match = re.match(r"^(summarize|summary)\s+(transcript|recent)\s*(\d+)?", text, re.IGNORECASE)
        if not match:
            return None
        if match.group(3):
            try:
                return max(5, min(80, int(match.group(3))))
            except Exception:
                return 50
        return 50

    async def _handle_fast_path(
        self,
        user: discord.User,
        channel: discord.abc.Messageable,
        guild: Optional[discord.Guild],
        message: Optional[discord.Message],
        query: str,
    ) -> bool:
        text = self._normalize_query(query)
        if not text:
            return False
        
        # Universal intelligence layer - handles natural language commands
        if self._intelligence:
            try:
                if await self._intelligence.process(user, channel, guild, message, text):
                    return True
            except Exception as e:
                print(f"Intelligence layer error (falling back): {e}")
        
        lower = text.lower()

        if lower in ("tools", "tool", "capabilities"):
            snapshot = self._capabilities_snapshot(guild, channel)
            await self._send_chunks(channel, json.dumps(snapshot, indent=2))
            return True
        if lower == "health":
            await self._send_chunks(channel, self._health_report())
            return True
        if lower == "queue":
            await self._send_chunks(channel, self._queue_report())
            return True
        if lower.startswith("cancel "):
            job_id = text.split(" ", 1)[1].strip()
            if not job_id:
                await self._send_chunks(channel, "Usage: !mandy cancel <job_id>")
                return True
            if job_id not in self._queue():
                await self._send_chunks(channel, "Job not found.")
                return True
            await self.cancel_job(job_id)
            return True
        if lower == "extensions":
            await self._send_chunks(channel, self._extensions_report())
            return True
        if lower == "selftest":
            await self._send_chunks(channel, await self._run_selftest(user, guild, channel))
            return True

        dm_match = self._parse_dm_request(text)
        if dm_match:
            targets, dm_text = dm_match
            if not targets:
                await self._send_chunks(channel, "Please name at least one target.")
                return True
            # UNRESTRICTED: Allow mass operations without limit
            # if len(targets) > 50:
            #     await self._send_chunks(channel, "Too many targets. Max 50 per command.")
            #     return True
            # UNRESTRICTED: Allow @everyone and mass broadcasts
            blocked_terms = set()  # Disabled - now allows all targets
            for target_text in targets:
                if any(term in target_text.lower() for term in blocked_terms):
                    await self._send_chunks(
                        channel,
                        "I can�t DM all users. Please list explicit users (mentions or nicknames).",
                    )
                    return True

            resolved_ids: List[int] = []
            unresolved: List[Tuple[str, List[Tuple[int, str]]]] = []
            for target_text in targets:
                uid, candidates = await self._resolve_user(guild, target_text, message, author=user)
                if uid:
                    if uid not in resolved_ids:
                        resolved_ids.append(uid)
                else:
                    unresolved.append((target_text, candidates))

            if unresolved:
                lines = ["Some targets are ambiguous or not found:"]
                for name, candidates in unresolved[:10]:
                    if candidates:
                        opts = ", ".join(f"{label}" for _, label in candidates)
                        lines.append(f"- {name}: {opts}")
                    else:
                        lines.append(f"- {name}: not found")
                lines.append("Please re-run with @mentions for those names.")
                await self._send_chunks(channel, "\n".join(lines))
                return True

            actions = [{"tool": "send_dm", "args": {"user_id": uid, "text": dm_text}} for uid in resolved_ids]
            results = await self._execute_actions(user.id, actions, guild=guild, channel=channel)
            ok_count = sum(1 for r in results if "OK" in r)
            err_lines = [r for r in results if "ERROR" in r]
            summary = f"DMs sent: {ok_count}/{len(results)}."
            if err_lines:
                summary += "\nErrors:\n" + "\n".join(err_lines[:5])
            await self._send_chunks(channel, summary)
            return True

        summary_limit = self._parse_transcript_summarize(text)
        if summary_limit:
            await self._summarize_transcript(user, channel, guild, summary_limit, query_text=text)
            return True

        add_match = self._parse_watcher_add(text)
        if add_match:
            user_text, count, msg = add_match
            uid, candidates = await self._resolve_user(guild, user_text, message, author=user)
            if uid:
                actions = [{"tool": "add_watcher", "args": {"target_user_id": uid, "count": count, "text": msg}}]
                results = await self._execute_actions(user.id, actions, guild=guild, channel=channel)
                await self._send_chunks(channel, "\n".join(results))
                return True
            if candidates:
                await self._prompt_user_pick(channel, user.id, guild, {"tool": "add_watcher", "args": {"count": count, "text": msg}, "user_arg": "target_user_id"}, candidates)
                return True
            await self._send_chunks(channel, "User not found. Try @mention, exact nickname, or 'me'.")
            return True

        remove_match = self._parse_watcher_remove(text)
        if remove_match:
            uid, candidates = await self._resolve_user(guild, remove_match, message, author=user)
            if uid:
                actions = [{"tool": "remove_watcher", "args": {"target_user_id": uid}}]
                results = await self._execute_actions(user.id, actions, guild=guild, channel=channel)
                await self._send_chunks(channel, "\n".join(results))
                return True
            if candidates:
                await self._prompt_user_pick(channel, user.id, guild, {"tool": "remove_watcher", "args": {}, "user_arg": "target_user_id"}, candidates)
                return True
            await self._send_chunks(channel, "User not found. Try @mention, exact nickname, or 'me'.")
            return True

        if re.match(r"^(list|show)\s+watchers?\b", text, re.IGNORECASE):
            if self.tools:
                actions = [{"tool": "list_watchers", "args": {}}]
                results = await self._execute_actions(user.id, actions, guild=guild, channel=channel)
                await self._send_chunks(channel, "\n".join(results))
                return True

        status_match = self._parse_status_set(text)
        if status_match:
            state, status_text = status_match
            actions = [{"tool": "set_bot_status", "args": {"state": state, "text": status_text}}]
            results = await self._execute_actions(user.id, actions, guild=guild, channel=channel)
            await self._send_chunks(channel, "\n".join(results))
            return True

        stats_match = self._parse_stats_query(text)
        if stats_match:
            window, user_text = stats_match
            if user_text:
                uid, candidates = await self._resolve_user(guild, user_text, message, author=user)
                if uid:
                    actions = [{"tool": "show_stats", "args": {"scope": window, "user_id": uid}}]
                    results = await self._execute_actions(user.id, actions, guild=guild, channel=channel)
                    await self._send_chunks(channel, "\n".join(results))
                    return True
                if candidates:
                    await self._prompt_user_pick(channel, user.id, guild, {"tool": "show_stats", "args": {"scope": window}, "user_arg": "user_id"}, candidates)
                    return True
                await self._send_chunks(channel, "User not found. Try @mention, exact nickname, or 'me'.")
                return True
            actions = [{"tool": "show_stats", "args": {"scope": window}}]
            results = await self._execute_actions(user.id, actions, guild=guild, channel=channel)
            await self._send_chunks(channel, "\n".join(results))
            return True

        return False

    async def handle_mention(self, message: discord.Message, text: str) -> bool:
        if not await self._is_god_user(message.author):
            return False
        return await self._handle_fast_path(
            message.author,
            message.channel,
            message.guild,
            message,
            text,
        )

    async def _prompt_user_pick(
        self,
        channel: discord.abc.Messageable,
        requester_id: int,
        guild: Optional[discord.Guild],
        action: Dict[str, Any],
        candidates: List[Tuple[int, str]],
    ):
        if not candidates:
            await self._send_chunks(channel, "No matching users found.")
            return
        names = [f"- {label} (<@{uid}>)" for uid, label in candidates[:5]]
        question = "Which user did you mean?\n" + "\n".join(names)
        view = UserPickView(self, requester_id, action, candidates)
        await channel.send(question, view=view)

    async def handle_user_pick(
        self,
        action: Dict[str, Any],
        picked_id: int,
        channel: discord.abc.Messageable,
        guild: Optional[discord.Guild],
        user: discord.User,
    ):
        if not action or not isinstance(action, dict):
            return
        tool = action.get("tool")
        args = dict(action.get("args", {}) or {})
        user_arg = action.get("user_arg")
        if not tool or not user_arg:
            return
        args[user_arg] = int(picked_id)
        actions = [{"tool": tool, "args": args}]
        results = await self._execute_actions(user.id, actions, guild=guild, channel=channel)
        await self._send_chunks(channel, "\n".join(results))

    def _format_tool_design(self, design: Dict[str, Any]) -> str:
        name = design.get("name")
        description = design.get("description")
        args_schema = design.get("args_schema", {})
        side_effect = design.get("side_effect")
        cost = design.get("cost")
        needs_confirmation = design.get("needs_confirmation")
        example_calls = design.get("example_calls", [])
        lines = [
            "Tool proposal:",
            f"- name: {name}",
            f"- description: {description}",
            f"- side_effect: {side_effect}",
            f"- cost: {cost}",
            f"- needs_confirmation: {needs_confirmation}",
            f"- args_schema: {json.dumps(args_schema, ensure_ascii=True)}",
        ]
        if example_calls:
            lines.append("Example calls:")
            for call in example_calls[:3]:
                lines.append(f"- {call}")
        lines.append("Build this tool?")
        return "\n".join(lines)

    async def _summarize_transcript(
        self,
        user: discord.User,
        channel: discord.abc.Messageable,
        guild: Optional[discord.Guild],
        limit: int,
        query_text: str = "summarize transcript",
    ):
        if not self.client.available:
            await self._send_chunks(channel, "Gemini API key missing or SDK unavailable.")
            return
        if not self.tools or not isinstance(channel, discord.TextChannel):
            await self._send_chunks(channel, "Transcript summarize requires a text channel.")
            return
        try:
            transcript = await self.tools.get_recent_transcript(channel.id, limit=limit)
        except Exception:
            transcript = []
        if not transcript:
            await self._send_chunks(channel, "No recent messages to summarize.")
            return
        sys_prompt = "Summarize the chat transcript clearly and concisely in 5-8 bullet points."
        user_prompt = json.dumps(transcript, ensure_ascii=True)
        tokens_in = self._estimate_tokens(sys_prompt + user_prompt)
        wait = self._check_local_limits(self._default_model(), tokens_in)
        if wait > 0:
            await self._enqueue_rate_limit(channel, user.id, 0, query_text, wait, 0)
            self._record_rate_limit(wait, "local")
            return
        try:
            text = await self.client.generate(
                sys_prompt,
                user_prompt,
                model=self._default_model(),
                response_format=None,
                timeout=60.0,
            )
            tokens_out = self._estimate_tokens(text)
            self._record_usage(self._default_model(), tokens_in, tokens_out)
        except GeminiRateLimitError as exc:
            wait = exc.retry_after if exc.retry_after else self._backoff_seconds(0)
            await self._enqueue_rate_limit(channel, user.id, 0, query_text, wait, 0)
            self._record_rate_limit(wait, "gemini")
            return
        except Exception as exc:
            await self._send_chunks(channel, f"Summary failed: {exc}")
            return
        await self._send_chunks(channel, text)

    def _health_report(self) -> str:
        queue_counts = self._queue_counts()
        last_actions = self._runtime.get("last_actions", [])
        last_rate = self._runtime.get("last_rate_limit")
        counters = self._runtime.get("counters", {})
        lines = []
        lines.append(f"Gemini: {'available' if self.client.available else 'unavailable'}")
        lines.append(
            "Queue: "
            f"total={len(self._queue())} pending={queue_counts['pending']} "
            f"waiting={queue_counts['waiting']} running={queue_counts['running']}"
        )
        if last_rate:
            lines.append(
                "Last rate limit: "
                f"{_format_wait(last_rate.get('wait_seconds', 0))} "
                f"source={last_rate.get('source', 'unknown')} "
                f"at={last_rate.get('at', 0)}"
            )
        if counters:
            counter_text = " ".join(f"{k}={v}" for k, v in sorted(counters.items()))
            lines.append(f"Counters: {counter_text}")
        if last_actions:
            lines.append("Last actions:")
            for entry in last_actions[-5:]:
                if "error" in entry:
                    lines.append(f"- {entry.get('tool')}: ERROR {entry.get('error')}")
                else:
                    lines.append(f"- {entry.get('tool')}: OK {entry.get('result')}")
        return "\n".join(lines)

    def _queue_report(self) -> str:
        queue = self._queue()
        if not queue:
            return "Queue is empty."
        lines = []
        for job_id, job in list(queue.items())[:15]:
            status = job.get("status", "pending")
            next_retry = int(job.get("next_retry_at", 0))
            wait = max(0, next_retry - _now_ts())
            q = str(job.get("query") or "")
            if len(q) > 80:
                q = q[:77] + "..."
            lines.append(f"{job_id} | {status} | retry in {_format_wait(wait)} | {q}")
        return "Queued jobs:\n" + "\n".join(lines)

    def _extensions_report(self) -> str:
        ai = self._cfg()
        installed = list(ai.get("installed_extensions", []) or [])
        loaded = sorted(self.bot.extensions.keys())
        for mod in loaded:
            if mod not in installed:
                installed.append(mod)
        lines = []
        lines.append(f"Installed extensions ({len(installed)}): {', '.join(installed) if installed else 'none'}")
        lines.append("Reload: bot.reload_extension('<module>')")
        lines.append("Unload: bot.unload_extension('<module>')")
        return "\n".join(lines)

    async def _run_selftest(
        self,
        user: discord.User,
        guild: Optional[discord.Guild],
        channel: Optional[discord.abc.Messageable],
    ) -> str:
        results = []
        registry_ok = bool(self.registry and self.registry.tool_names())
        results.append(f"registry_non_empty: {'PASS' if registry_ok else 'FAIL'}")

        allowlist_ok = False
        if self.registry and self.tools:
            allowlist_ok, errors = self.registry.verify_tool_registry()
            results.append(f"allowlist_consistent: {'PASS' if allowlist_ok else 'FAIL'}")
            if errors:
                results.append("allowlist_errors: " + "; ".join(errors))
        else:
            results.append("allowlist_consistent: FAIL")

        if self.registry:
            ok, _ = self.registry.validate_tool_call("unknown_tool", {})
            results.append(f"schema_rejects_unknown_tool: {'PASS' if not ok else 'FAIL'}")
        else:
            results.append("schema_rejects_unknown_tool: FAIL")

        send_dm_ok = self.registry and bool(self.registry.get("send_dm"))
        results.append(f"send_dm_exists: {'PASS' if send_dm_ok else 'FAIL'}")

        bad_source = (
            "import os\n"
            "from discord.ext import commands\n"
            "async def setup(bot):\n"
            "    return\n"
            "@commands.command(name='bad')\n"
            "async def bad(ctx):\n"
            "    return\n"
            "@commands.command(name='bad_test')\n"
            "async def bad_test(ctx):\n"
            "    return 'OK'\n"
        )
        valid, errors = validate_extension_source("bad", bad_source)
        blocked = any("import 'os'" in err for err in errors)
        results.append(f"validator_rejects_dangerous_import: {'PASS' if (not valid and blocked) else 'FAIL'}")

        bad_calls = (
            "from discord.ext import commands\n"
            "async def setup(bot):\n"
            "    return\n"
            "def nope():\n"
            "    open('x','w')\n"
        )
        valid, errors = validate_extension_source("", bad_calls)
        blocked = any("call to 'open'" in err for err in errors)
        results.append(f"validator_rejects_open_exec: {'PASS' if (not valid and blocked) else 'FAIL'}")

        queue_before = len(self._queue())
        job_id = None
        if channel:
            job_id = await self._enqueue_rate_limit(channel, user.id, 0, "selftest", 10, 0)
        queue_after = len(self._queue())
        queue_ok = job_id is not None and queue_after == queue_before + 1
        results.append(f"queue_backoff_creates_job: {'PASS' if queue_ok else 'FAIL'}")
        if job_id:
            await self.cancel_job(job_id, silent=True)

        plugin_ok = False
        if self.plugin_manager:
            try:
                await self.plugin_manager.load_plugin("extensions/tool_ping.py")
                plugin_ok = "tool_ping" in (self.tools.list_dynamic_tools() if self.tools else [])
                if plugin_ok:
                    actions = [{"tool": "tool_ping", "args": {}}]
                    res = await self._execute_actions(user.id, actions, guild=guild, channel=channel)
                    plugin_ok = any("OK" in str(r) for r in res)
            except Exception:
                plugin_ok = False
        results.append(f"plugin_registration_and_call: {'PASS' if plugin_ok else 'FAIL'}")

        rolling_ok = False
        if self.tools and guild:
            try:
                text = await self.tools.show_stats("rolling24", user_id=user.id, guild_id=guild.id)
                rolling_ok = "rolling24" in str(text)
            except Exception:
                rolling_ok = False
        results.append(f"rolling24_supported: {'PASS' if rolling_ok else 'FAIL'}")

        return "\n".join(results)

    def _build_router_prompts(
        self,
        query: str,
        transcript: List[Dict[str, Any]],
        context: Dict[str, Any],
        confirmed: bool,
        tts_model: str,
        guild: Optional[discord.Guild],
        channel: Optional[discord.abc.Messageable],
    ) -> Tuple[str, str]:
        registry = self.registry or (CapabilityRegistry(self.tools) if self.tools else None)
        tool_help = registry.format_tools_summary(include_args=True) if registry else "No tools available."
        snapshot = self._capabilities_snapshot(guild, channel)
        snapshot_text = json.dumps(snapshot, ensure_ascii=True)
        system_prompt = (
            "You are Mandy AI, a strict intent router for a Discord bot. "
            "Return ONLY valid JSON with this schema:\n"
            "{"
            "\"intent\":\"TALK|ACTION|NEEDS_CONFIRMATION|DESIGN_TOOL|BUILD_TOOL\","
            "\"response\":\"string\","
            "\"actions\":[{\"tool\":\"name\",\"args\":{...}}],"
            "\"tool_design\":{"
            "\"name\":\"snake_case\",\"description\":\"string\",\"args_schema\":{...},"
            "\"side_effect\":\"read|write\",\"cost\":\"cheap|normal|expensive\","
            "\"needs_confirmation\":true|false,\"example_calls\":[\"...\"]},"
            "\"build\":{\"slug\":\"tool_name\",\"files\":[{\"path\":\"extensions/<slug>.py\",\"content\":\"python\"}]},"
            "\"confirm\":{\"question\":\"string\",\"options\":[\"CONFIRM\",\"CANCEL\",\"WAIT\"]}"
            "}\n"
            "Keys allowed: intent, response, actions, build, confirm. Unknown keys are forbidden.\n"
            "Actions only if intent=ACTION. Tool design only if intent=DESIGN_TOOL. "
            "Build only if intent=BUILD_TOOL. Confirm only if intent=NEEDS_CONFIRMATION.\n"
            "Tool allowlist and schemas:\n"
            f"{tool_help}\n"
            "No other tools are allowed. Actions must include only tool and args.\n"
            "Routing policy: if user asks to do something with bot features, prefer ACTION. "
            "If user wants chat, explanation, brainstorming, prefer TALK. "
            "If user requests a new command/feature, prefer DESIGN_TOOL. "
            "If action is risky/irreversible, return NEEDS_CONFIRMATION.\n"
            "BUILD-IF-MISSING: If the request cannot be satisfied using allowed tools, return DESIGN_TOOL. "
            "Do not fabricate answers or claim to have executed actions without ACTION.\n"
            "If the user request is ambiguous, return NEEDS_CONFIRMATION with a clarification question.\n"
            "If the user asks to DM/privately message someone, you MUST use send_dm.\n"
            "For BUILD_TOOL, include actions to run the new tool if it solves the request.\n"
            f"Capabilities snapshot (JSON): {snapshot_text}\n"
        )
        if confirmed:
            system_prompt += "User already confirmed. Do NOT return NEEDS_CONFIRMATION.\n"
        system_prompt += (
            "For BUILD: files must live under extensions/ and end with .py, <=200KB. "
            "Allowed imports: discord, discord.ext.commands, typing, datetime, json, re, asyncio, math, random. "
            "Deny imports: os, sys, subprocess, socket, requests, aiohttp, httpx, pathlib, shutil, aiomysql, sqlite3, pickle. "
            "No eval/exec/open. Must define setup(bot). "
            "Extension must include TOOL_EXPORTS = {\"tool_name\": {\"description\":...,\"args_schema\":...,\"side_effect\":\"read|write\","
            "\"cost\":\"cheap|normal|expensive\",\"handler\": async callable}}.\n"
            "TOOL_EXPORTS must be a dict literal and tools must be safe.\n"
            "If user asks for audio output and no TTS model is configured, respond with 'not supported' in response.\n"
        )
        user_prompt = (
            f"Context: {json.dumps(context, ensure_ascii=True)}\n"
            f"Recent messages: {json.dumps(transcript, ensure_ascii=True)}\n"
            f"User query: {query}\n"
            f"TTS model configured: {bool(tts_model)}\n"
        )
        return system_prompt, user_prompt

    def _extract_json(self, text: str) -> Optional[dict]:
        if not text:
            return None
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(stripped[start : end + 1])
        except Exception:
            return None

    def _validate_build_spec(self, build: Any) -> Tuple[bool, str]:
        if not isinstance(build, dict):
            return False, "build not a dict"
        unknown = set(build.keys()) - {"slug", "files"}
        if unknown:
            return False, f"build unknown keys: {sorted(unknown)}"
        slug = build.get("slug")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9_]{3,32}", slug):
            return False, "invalid slug"
        files = build.get("files")
        if not isinstance(files, list) or not files:
            return False, "files required"
        for entry in files:
            if not isinstance(entry, dict):
                return False, "file entry must be dict"
            unknown_keys = set(entry.keys()) - {"path", "content"}
            if unknown_keys:
                return False, f"file unknown keys: {sorted(unknown_keys)}"
            path = entry.get("path")
            content = entry.get("content")
            if not isinstance(path, str) or not isinstance(content, str):
                return False, "file path/content must be str"
            ok, err = validate_extension_path(path)
            if not ok:
                return False, err
            if len(content.encode("utf-8")) > 200 * 1024:
                return False, "file too large"
        return True, ""

    def _validate_tool_design_spec(self, design: Any) -> Tuple[bool, str]:
        if not isinstance(design, dict):
            return False, "tool_design not a dict"
        required = {"name", "description", "args_schema", "side_effect", "cost", "needs_confirmation", "example_calls"}
        unknown = set(design.keys()) - required
        if unknown:
            return False, f"tool_design unknown keys: {sorted(unknown)}"
        name = design.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", name):
            return False, "invalid tool name"
        desc = design.get("description")
        if not isinstance(desc, str) or not desc.strip():
            return False, "description required"
        args_schema = design.get("args_schema")
        if not isinstance(args_schema, dict):
            return False, "args_schema must be dict"
        side_effect = design.get("side_effect")
        if side_effect not in ("read", "write"):
            return False, "side_effect must be read or write"
        cost = design.get("cost")
        if cost not in ("cheap", "normal", "expensive"):
            return False, "cost must be cheap|normal|expensive"
        needs_confirmation = design.get("needs_confirmation")
        if not isinstance(needs_confirmation, bool):
            return False, "needs_confirmation must be bool"
        example_calls = design.get("example_calls")
        if not isinstance(example_calls, list) or not all(isinstance(x, str) for x in example_calls):
            return False, "example_calls must be list[str]"
        for arg, meta in args_schema.items():
            if not isinstance(meta, dict):
                return False, f"args_schema {arg} must be dict"
            arg_type = meta.get("type")
            if arg_type not in ("int", "float", "bool", "str", "enum", "optional"):
                return False, f"args_schema {arg} invalid type"
            if arg_type == "enum":
                if not isinstance(meta.get("enum"), list) or not meta.get("enum"):
                    return False, f"args_schema {arg} enum choices required"
            if arg_type in ("int", "float"):
                if "min" in meta and not isinstance(meta["min"], (int, float)):
                    return False, f"args_schema {arg} min must be number"
                if "max" in meta and not isinstance(meta["max"], (int, float)):
                    return False, f"args_schema {arg} max must be number"
            if arg_type == "str":
                if "max_len" in meta and not isinstance(meta["max_len"], int):
                    return False, f"args_schema {arg} max_len must be int"
        return True, ""

    def _validate_confirm_spec(self, confirm: Any) -> Tuple[bool, str]:
        if not isinstance(confirm, dict):
            return False, "confirm not a dict"
        unknown = set(confirm.keys()) - {"question", "options"}
        if unknown:
            return False, f"confirm unknown keys: {sorted(unknown)}"
        question = confirm.get("question")
        options = confirm.get("options")
        if not isinstance(question, str) or not question.strip():
            return False, "confirm question required"
        if not isinstance(options, list) or not options:
            return False, "confirm options required"
        allowed = {"CONFIRM", "CANCEL", "WAIT"}
        for opt in options:
            if opt not in allowed:
                return False, f"invalid confirm option: {opt}"
        if "CONFIRM" not in options or "CANCEL" not in options:
            return False, "confirm must include CONFIRM and CANCEL"
        return True, ""

    def _validate_router_payload(self, payload: dict) -> Tuple[bool, str]:
        if not isinstance(payload, dict):
            return False, "payload not a dict"
        unknown = set(payload.keys()) - SCHEMA_KEYS
        if unknown:
            return False, f"unknown keys: {sorted(unknown)}"
        intent = payload.get("intent")
        if intent not in ALLOWED_INTENTS:
            return False, "invalid intent"
        response = payload.get("response")
        if not isinstance(response, str):
            return False, "response must be string"
        if intent == "ACTION":
            actions = payload.get("actions")
            if not isinstance(actions, list) or not actions:
                return False, "actions required for ACTION"
            for action in actions:
                ok, err = self._validate_action(action)
                if not ok:
                    return False, err
        elif "actions" in payload and intent != "BUILD_TOOL":
            return False, "actions not allowed for this intent"
        if intent == "BUILD_TOOL":
            build = payload.get("build")
            ok, err = self._validate_build_spec(build)
            if not ok:
                return False, err
            actions = payload.get("actions")
            if actions is not None:
                if not isinstance(actions, list):
                    return False, "actions must be list"
                for action in actions:
                    ok, err = self._validate_action(action)
                    if not ok:
                        return False, err
        elif "build" in payload:
            return False, "build not allowed for this intent"
        if intent == "DESIGN_TOOL":
            design = payload.get("tool_design")
            ok, err = self._validate_tool_design_spec(design)
            if not ok:
                return False, err
        elif "tool_design" in payload:
            return False, "tool_design not allowed for this intent"
        if intent == "NEEDS_CONFIRMATION":
            confirm = payload.get("confirm")
            ok, err = self._validate_confirm_spec(confirm)
            if not ok:
                return False, err
        elif "confirm" in payload:
            return False, "confirm not allowed for this intent"
        return True, ""

    def _validate_action(self, action: Any) -> Tuple[bool, str]:
        if not isinstance(action, dict):
            return False, "action not a dict"
        if set(action.keys()) != {"tool", "args"}:
            return False, "action must include only tool and args"
        tool = action.get("tool")
        if not self.registry and self.tools:
            self.registry = CapabilityRegistry(self.tools)
        if not self.registry:
            return False, "capability registry missing"
        args = action.get("args", {})
        return self.registry.validate_tool_call(tool, args)

    async def _execute_actions(
        self,
        user_id: int,
        actions: List[Dict[str, Any]],
        guild: Optional[discord.Guild] = None,
        channel: Optional[discord.abc.Messageable] = None,
        message_id: int = 0,
    ) -> List[str]:
        results: List[str] = []
        if not self.tools:
            return ["tool registry missing"]
        if not self.registry:
            self.registry = CapabilityRegistry(self.tools)
        for action in actions:
            tool = action.get("tool")
            args = dict(action.get("args", {}) or {})
            if tool == "show_stats" and guild and "guild_id" not in args:
                args["guild_id"] = guild.id
            ok, err = self._validate_action({"tool": tool, "args": args})
            if not ok:
                results.append(f"{action}: ERROR {err}")
                continue
            try:
                if guild and "target_user_id" in args:
                    member = guild.get_member(int(args["target_user_id"]))
                    if not member:
                        results.append(f"{tool}: ERROR user not found in this guild")
                        continue
                if guild and "user_id" in args:
                    member = guild.get_member(int(args["user_id"]))
                    if not member:
                        results.append(f"{tool}: ERROR user not found in this guild")
                        continue
                dynamic = self.tools.get_dynamic_tool(tool)
                if dynamic:
                    handler = dynamic.get("handler")
                    if not handler:
                        results.append(f"{tool}: ERROR handler missing")
                        continue
                    if self.plugin_manager:
                        author = self.bot.get_user(user_id)
                        if not author and guild:
                            author = guild.get_member(user_id)
                        ctx = self.plugin_manager.build_context(guild, channel, author, message_id)
                    else:
                        ctx = None
                    res = await handler(ctx, **args)
                else:
                    func = getattr(self.tools, tool, None)
                    if not func:
                        results.append(f"{tool}: ERROR tool missing")
                        continue
                    if self.registry.requires_actor(tool):
                        res = await func(**args, actor_id=user_id)
                    else:
                        res = await func(**args)
                results.append(f"{tool}: OK {res}")
                await self._log(f"[Mandy ACTION] user={user_id} tool={tool} args={self._summarize_args(args)} result={res}")
                self._counter_inc("actions", 1)
                self._record_action({
                    "at": _now_ts(),
                    "tool": tool,
                    "args": self._summarize_args(args),
                    "result": str(res),
                })
            except Exception as exc:
                results.append(f"{tool}: ERROR {exc}")
                await self._log(f"[Mandy ACTION] user={user_id} tool={tool} args={self._summarize_args(args)} error={exc}")
                self._record_action({
                    "at": _now_ts(),
                    "tool": tool,
                    "args": self._summarize_args(args),
                    "error": str(exc),
                })
        return results

    def _summarize_args(self, args: Dict[str, Any]) -> str:
        parts = []
        for k, v in args.items():
            s = str(v)
            if len(s) > 120:
                s = s[:117] + "..."
            parts.append(f"{k}={s}")
        return ", ".join(parts)

    async def _handle_build_tool(self, user_id: int, channel: discord.abc.Messageable, build: Dict[str, Any]) -> Tuple[bool, str]:
        if not self.plugin_manager:
            await self._send_chunks(channel, "Build failed: plugin manager unavailable.")
            return False, "plugin manager missing"
        slug = str(build.get("slug") or "").strip().lower()
        files = build.get("files") or []

        if not re.fullmatch(r"[a-z0-9_]{3,32}", slug):
            await self._send_chunks(channel, "Build failed: invalid slug.")
            return False, "invalid slug"
        if not isinstance(files, list) or not files:
            await self._send_chunks(channel, "Build failed: missing files.")
            return False, "missing files"

        written_paths = []
        for f in files:
            if not isinstance(f, dict):
                await self._send_chunks(channel, "Build failed: bad file entry.")
                return False, "bad file entry"
            path = str(f.get("path") or "")
            content = str(f.get("content") or "")
            ok, err = validate_extension_path(path)
            if not ok:
                await self._send_chunks(channel, f"Build failed: {err}")
                return False, err
            if os.path.exists(path):
                await self._send_chunks(channel, "Build failed: file already exists.")
                return False, "file exists"
            if len(content.encode("utf-8")) > 200 * 1024:
                await self._send_chunks(channel, "Build failed: file too large.")
                return False, "file too large"
            if "TOOL_EXPORTS" not in content:
                await self._send_chunks(channel, "Build failed: TOOL_EXPORTS required.")
                return False, "missing TOOL_EXPORTS"
            valid, errors = validate_extension_source("", content)
            if not valid:
                await self._send_chunks(channel, "Build failed:\n" + "\n".join(errors[:8]))
                return False, "validation failed"

            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fhandle:
                fhandle.write(content)
            written_paths.append(path)

        try:
            if self.plugin_manager:
                for path in written_paths:
                    await self.plugin_manager.load_plugin(path)
        except Exception as exc:
            await self._send_chunks(channel, f"Build failed: plugin load error: {exc}")
            return False, "plugin load error"

        ai = self._cfg()
        installed = ai.setdefault("installed_extensions", [])
        module_name = os.path.splitext(written_paths[0].replace("\\", "/"))[0].replace("/", ".")
        if module_name not in installed:
            installed.append(module_name)
            await self._mark_dirty()

        await self._log(
            f"[Mandy BUILD] user={user_id} slug={slug} module={module_name} files={written_paths}"
        )

        await self._send_chunks(channel, f"Build complete: `{module_name}` loaded.")

        dm_text = (
            f"Mandy AI build complete.\n"
            f"Module: {module_name}\n"
            f"Path: {written_paths[0]}\n"
            f"Tool slug: {slug}\n"
            f"Reload: bot.reload_extension('{module_name}')\n"
            f"Unload: bot.unload_extension('{module_name}')\n"
        )
        await self._notify_user(user_id, getattr(channel, "id", 0), dm_text)
        return True, "ok"

    async def _call_router(
        self,
        query: str,
        transcript: List[Dict[str, Any]],
        context: Dict[str, Any],
        confirmed: bool,
        guild: Optional[discord.Guild],
        channel: Optional[discord.abc.Messageable],
    ) -> dict:
        tts_model = str(self._cfg().get("tts_model") or "")
        sys_prompt, user_prompt = self._build_router_prompts(query, transcript, context, confirmed, tts_model, guild, channel)
        tokens_in = self._estimate_tokens(sys_prompt + user_prompt)
        wait = self._check_local_limits(self._router_model(), tokens_in)
        if wait > 0:
            raise LocalRateLimitError(wait)
        text = await self.client.generate(
            sys_prompt,
            user_prompt,
            model=self._router_model(),
            response_format="json",
            timeout=60.0,
        )
        self._counter_inc("router_calls", 1)
        tokens_out = self._estimate_tokens(text)
        self._record_usage(self._router_model(), tokens_in, tokens_out)
        payload = self._extract_json(text)
        if payload is None:
            raise RuntimeError("Malformed JSON from router")
        ok, err = self._validate_router_payload(payload)
        if not ok:
            raise RuntimeError(f"Router validation failed: {err}")
        return payload

    async def _process_request(
        self,
        user: discord.User,
        channel: discord.abc.Messageable,
        guild: Optional[discord.Guild],
        message_id: int,
        query: str,
        confirmed: bool = False,
        from_queue: bool = False,
        extra_context: Optional[Dict[str, Any]] = None,
    ):
        now = time.time()
        if not from_queue:
            last = self._cooldowns.get(user.id, 0.0)
            cooldown = self._cooldown_seconds()
            if now - last < cooldown:
                wait = cooldown - (now - last)
                await self._send_chunks(channel, f"Cooldown active. Try again in {_format_wait(wait)}.")
                return
            self._cooldowns[user.id] = now

        text_query = query.strip()

        msg = None
        if message_id and hasattr(channel, "fetch_message"):
            try:
                msg = await channel.fetch_message(message_id)
            except Exception:
                msg = None

        if text_query:
            handled = await self._handle_fast_path(user, channel, guild, msg, text_query)
            if handled:
                return

        if msg:
            try:
                audio_text = await self._transcribe_audio(msg)
                if audio_text == "AUDIO_NOT_SUPPORTED":
                    await self._send_chunks(channel, "Audio input not supported for transcription.")
                elif audio_text:
                    text_query = (text_query + "\n" + audio_text).strip() if text_query else audio_text
            except LocalRateLimitError as exc:
                if from_queue:
                    raise
                await self._enqueue_rate_limit(channel, user.id, message_id, text_query, exc.wait_seconds, 0)
                self._record_rate_limit(exc.wait_seconds, "local")
                return
            except GeminiRateLimitError as exc:
                if from_queue:
                    raise
                local_wait = self._check_local_limits(self._default_model(), 1)
                wait = exc.retry_after if exc.retry_after else max(self._backoff_seconds(0), local_wait)
                await self._enqueue_rate_limit(channel, user.id, message_id, text_query, wait, 0)
                self._record_rate_limit(wait, "gemini")
                return
            except Exception:
                await self._send_chunks(channel, "Audio transcription failed.")

        if not text_query:
            if isinstance(channel, discord.TextChannel):
                await self._send_chunks(channel, "Usage: !mandy <query>")
            else:
                await self._send_chunks(channel, "No query text provided.")
            return

        if not self.tools:
            await self._send_chunks(channel, "Tool registry not available.")
            return
        if not self.client.available:
            await self._send_chunks(channel, "Gemini API key missing or SDK unavailable.")
            return

        transcript: List[Dict[str, Any]] = []
        if isinstance(channel, discord.TextChannel):
            try:
                transcript = await self.tools.get_recent_transcript(channel.id, limit=50)
            except Exception:
                transcript = []

        context = {
            "author_id": user.id,
            "guild_id": guild.id if guild else 0,
            "channel_id": getattr(channel, "id", 0),
            "message_id": message_id,
            "confirmed": confirmed,
        }
        if extra_context:
            context.update(extra_context)

        try:
            payload = await self._call_router(text_query, transcript, context, confirmed, guild, channel)
        except LocalRateLimitError as exc:
            if from_queue:
                raise
            await self._enqueue_rate_limit(channel, user.id, message_id, text_query, exc.wait_seconds, 0)
            self._record_rate_limit(exc.wait_seconds, "local")
            return
        except GeminiRateLimitError as exc:
            if from_queue:
                raise
            local_wait = self._check_local_limits(self._router_model(), self._estimate_tokens(text_query))
            wait = exc.retry_after if exc.retry_after else max(self._backoff_seconds(0), local_wait)
            await self._enqueue_rate_limit(channel, user.id, message_id, text_query, wait, 0)
            self._record_rate_limit(wait, "gemini")
            return
        except Exception as exc:
            await self._send_chunks(channel, f"Router error: {exc}")
            return

        intent = payload.get("intent")
        response = payload.get("response") or ""
        if intent == "TALK":
            await self._send_chunks(channel, response or "No response.")
            self._counter_inc("talks", 1)
            return
        if intent == "ACTION":
            actions = payload.get("actions") or []
            results = await self._execute_actions(user.id, actions, guild=guild, channel=channel, message_id=message_id)
            summary = response.strip()
            if summary:
                summary += "\n"
            summary += "\n".join(results)
            await self._send_chunks(channel, summary)
            self._counter_inc("action_requests", 1)
            return
        if intent == "DESIGN_TOOL":
            design = payload.get("tool_design") or {}
            proposal = self._format_tool_design(design)
            if response:
                proposal = response.strip() + "\n" + proposal
            self._pending_designs[user.id] = {"query": text_query, "design": design}
            await channel.send(proposal, view=ConfirmView(self, user.id, getattr(channel, "id", 0), text_query))
            self._counter_inc("confirmations", 1)
            return
        if intent == "BUILD_TOOL":
            build = payload.get("build") or {}
            if not confirmed:
                slug = str(build.get("slug") or "")
                question = f"Proposed BUILD: {slug}. Proceed?"
                view = ConfirmView(self, user.id, getattr(channel, "id", 0), text_query)
                await channel.send(question, view=view)
                self._counter_inc("confirmations", 1)
                return
            ok, msg = await self._handle_build_tool(user.id, channel, build)
            if response:
                await self._send_chunks(channel, response)
            if not ok:
                return
            actions = payload.get("actions") or []
            if actions:
                results = await self._execute_actions(user.id, actions, guild=guild, channel=channel, message_id=message_id)
                await self._send_chunks(channel, "\n".join(results))
            self._counter_inc("builds", 1)
            return
        if intent == "NEEDS_CONFIRMATION":
            confirm = payload.get("confirm") or {}
            question = str(confirm.get("question") or "Confirm?")
            view = ConfirmView(self, user.id, getattr(channel, "id", 0), text_query)
            await channel.send(question, view=view)
            self._counter_inc("confirmations", 1)
            return
        await self._send_chunks(channel, "Unhandled intent.")

    async def _enqueue_rate_limit(
        self,
        channel: discord.abc.Messageable,
        user_id: int,
        message_id: int,
        query: str,
        wait_seconds: float,
        attempts: int,
    ) -> Optional[str]:
        job_id = f"job_{_now_ts()}_{random.randint(1000, 9999)}"
        wait_seconds = max(0, float(wait_seconds))
        job = {
            "job_id": job_id,
            "user_id": user_id,
            "channel_id": getattr(channel, "id", 0),
            "message_id": message_id,
            "query": query,
            "status": "pending",
            "attempts": attempts,
            "next_retry_at": _now_ts() + int(wait_seconds),
            "created_at": _now_ts(),
        }
        async with self._queue_lock:
            self._queue()[job_id] = job
            await self._mark_dirty()
        self._counter_inc("queued", 1)

        wait_text = _format_wait(wait_seconds)
        msg = f"Rate-limited. Next attempt in ~{wait_text}. Choose: [WAIT] [CANCEL]. Job: {job_id}"
        await channel.send(msg, view=RateLimitView(self, job_id, user_id))
        return job_id

    async def _run_job(self, job_id: str):
        async with self._queue_lock:
            job = self._queue().get(job_id)
        if not job:
            return
        sleep_for = max(0, int(job.get("next_retry_at", _now_ts())) - _now_ts())
        if sleep_for:
            await asyncio.sleep(sleep_for)
        async with self._queue_lock:
            job = self._queue().get(job_id)
        if not job or job.get("status") == "cancelled":
            return
        await self._process_job(job_id)

    async def _process_job(self, job_id: str):
        async with self._queue_lock:
            job = self._queue().get(job_id)
            if not job:
                return
            job["status"] = "running"
            await self._mark_dirty()

        user_id = int(job.get("user_id", 0))
        channel_id = int(job.get("channel_id", 0))
        message_id = int(job.get("message_id", 0))
        query = str(job.get("query") or "")
        attempts = int(job.get("attempts", 0))

        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                channel = None

        user = self.bot.get_user(user_id)
        if not user:
            try:
                user = await self.bot.fetch_user(user_id)
            except Exception:
                user = None

        if not channel or not user:
            await self.cancel_job(job_id)
            return

        try:
            await self._process_request(user, channel, getattr(channel, "guild", None), message_id, query, from_queue=True)
        except LocalRateLimitError as exc:
            await self._reschedule_job(job_id, attempts, exc.wait_seconds)
            self._record_rate_limit(exc.wait_seconds, "local")
            return
        except GeminiRateLimitError as exc:
            backoff = self._backoff_seconds(attempts + 1)
            local_wait = self._check_local_limits(self._router_model(), self._estimate_tokens(query))
            wait = exc.retry_after if exc.retry_after else max(backoff, local_wait)
            await self._reschedule_job(job_id, attempts + 1, wait)
            self._record_rate_limit(wait, "gemini")
            return
        except Exception as exc:
            await self._notify_user(user_id, channel_id, f"Queued job failed: {exc}")
            await self.cancel_job(job_id)
            return

        await self._notify_user(user_id, channel_id, f"Queued job {job_id} completed.")
        await self.cancel_job(job_id, silent=True)

    async def _reschedule_job(self, job_id: str, attempts: int, wait_seconds: float):
        async with self._queue_lock:
            job = self._queue().get(job_id)
            if not job:
                return
            job["status"] = "waiting"
            job["attempts"] = attempts
            job["next_retry_at"] = _now_ts() + int(wait_seconds)
            await self._mark_dirty()
        if job_id not in self._queue_tasks or self._queue_tasks[job_id].done():
            self._queue_tasks[job_id] = asyncio.create_task(self._run_job(job_id))

    async def accept_job(self, job_id: str):
        async with self._queue_lock:
            job = self._queue().get(job_id)
            if not job:
                return
            job["status"] = "waiting"
            await self._mark_dirty()
        if job_id not in self._queue_tasks or self._queue_tasks[job_id].done():
            self._queue_tasks[job_id] = asyncio.create_task(self._run_job(job_id))

    async def cancel_job(self, job_id: str, silent: bool = False):
        async with self._queue_lock:
            job = self._queue().pop(job_id, None)
            await self._mark_dirty()
        task = self._queue_tasks.pop(job_id, None)
        if task:
            task.cancel()
        if not silent and job:
            await self._notify_user(int(job.get("user_id", 0)), int(job.get("channel_id", 0)), f"Job {job_id} cancelled.")

    async def confirm_request(self, user_id: int, channel_id: int, query: str):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return
        user = self.bot.get_user(user_id)
        if not user:
            try:
                user = await self.bot.fetch_user(user_id)
            except Exception:
                return
        extra = None
        pending = self._pending_designs.pop(user_id, None)
        if pending and pending.get("query") == query:
            extra = {"tool_design": pending.get("design", {}), "design_confirmed": True}
        await self._process_request(
            user,
            channel,
            getattr(channel, "guild", None),
            0,
            query,
            confirmed=True,
            extra_context=extra,
        )

    @commands.command(name="mandy")
    async def mandy_cmd(self, ctx: commands.Context, *, query: str = ""):
        if not await self._require_god(ctx):
            return
        await self._process_request(ctx.author, ctx.channel, ctx.guild, ctx.message.id, query)

    @commands.command(name="mandy_model")
    async def mandy_model(self, ctx: commands.Context, *, name: str):
        if not await self._require_god(ctx):
            return
        model = name.strip().lower()
        mapping = {
            "flash-lite": "gemini-2.5-flash-lite",
            "flash": "gemini-2.5-flash",
            "pro": "gemini-2.5-pro",
        }
        model = mapping.get(model, model)
        ai = self._cfg()
        ai["default_model"] = model
        await self._mark_dirty()
        await self._send_chunks(ctx.channel, f"Default model set to {model}.")

    @commands.command(name="mandy_queue")
    async def mandy_queue(self, ctx: commands.Context):
        if not await self._require_god(ctx):
            return
        queue = self._queue()
        if not queue:
            return await self._send_chunks(ctx.channel, "Queue is empty.")
        lines = []
        for job_id, job in list(queue.items())[:15]:
            status = job.get("status", "pending")
            next_retry = int(job.get("next_retry_at", 0))
            wait = max(0, next_retry - _now_ts())
            q = str(job.get("query") or "")
            if len(q) > 80:
                q = q[:77] + "..."
            lines.append(f"{job_id} | {status} | retry in {_format_wait(wait)} | {q}")
        await self._send_chunks(ctx.channel, "Queued jobs:\n" + "\n".join(lines))

    @commands.command(name="mandy_cancel")
    async def mandy_cancel(self, ctx: commands.Context, job_id: str):
        if not await self._require_god(ctx):
            return
        if job_id not in self._queue():
            return await self._send_chunks(ctx.channel, "Job not found.")
        await self.cancel_job(job_id)

    @commands.command(name="mandy_limits")
    async def mandy_limits(self, ctx: commands.Context):
        if not await self._require_god(ctx):
            return
        ai = self._cfg()
        lines = []
        lines.append(f"Cooldown: {self._cooldown_seconds()}s")
        lines.append(f"Default model: {self._default_model()} | Router: {self._router_model()}")
        lines.append(f"Queue size: {len(self._queue())}")
        lines.append("Limits (AI Studio is source of truth):")
        for model, lim in ai.get("limits", {}).items():
            rpm = lim.get("rpm", "n/a")
            tpm = lim.get("tpm", "n/a")
            rpd = lim.get("rpd", "n/a")
            lines.append(f"- {model}: rpm={rpm} tpm={tpm} rpd={rpd}")
        lines.append("Rolling (last 60s):")
        for model, entry in self._usage["rolling"].items():
            lines.append(f"- {model}: count={entry.get('count', 0)} tokens={entry.get('tokens', 0)}")
        lines.append("Daily:")
        for model, entry in self._usage["daily"].items():
            lines.append(
                f"- {model} ({entry.get('date')}): count={entry.get('count', 0)} tokens={entry.get('tokens', 0)}"
            )
        await self._send_chunks(ctx.channel, "\n".join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(MandyAI(bot))
