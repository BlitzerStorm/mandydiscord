# ============================================================
# MANDY OS ƒ?" Prefix Control Plane (Full Build)
# ============================================================
# ƒo. Prefix commands (!)
# ƒo. Clean command UX: delete command + temporary inputs
# ƒo. Watchers from database.json -> targets[user_id]: {count,current,text}
# ƒo. Mirrors with Reply/Post/DM buttons (persistent mapping stored in MySQL when enabled)
# ƒo. Reaction-based God Menu
# ƒo. MySQL optional (safe fallback to JSON-only)
# ƒo. Auto server population + pinned docs
# ƒo. Logging channels
# ƒo. DM bridge relay both ways + history dump
# ƒo. Join gate for ADMIN_GUILD_ID
# ============================================================

import discord
from discord.ext import commands, tasks
import aiofiles
import aiomysql
import asyncio
import json
import os
import random
import re
import time
import datetime
import hashlib
from typing import Optional, Dict, Any, List, Tuple, Set
import secrets
import urllib.parse
import yt_dlp
from capability_registry import CapabilityRegistry
from tool_plugin_manager import ToolPluginManager
from cooldown_store import CooldownStore
import ambient_engine
from resolver import GuildIndexCache, parse_user_id, rank_members_global, pick_best
from sentience_layer import sentience_cfg, presence_cfg, voice_line

# -----------------------------
# IDs / Constants
# -----------------------------
ADMIN_GUILD_ID = 1273147628942524416
SUPER_USER_ID = 741470965359443970
AUTO_GOD_ID = 677193230265090059
MANDY_GOD_LEVEL = 90
MENTION_DM_COOLDOWN_SECONDS = 600

DB_JSON_PATH = "database.json"
PASSWORDS_PATH = "passwords.txt"

SPECIAL_VOICE_USER_ID = 741470965359443970
SPECIAL_VOICE_URL = "https://youtu.be/UukrfHmWmuY"
VOICE_QUIT_DELAY_SECONDS = 27
VOICE_EXIT_PHRASES = [
    "Excellent conversation we had in the VC, do you agree?",
    "That got me thinking: best VC yet, right?",
    "What a call, definitely excellent, wouldn't you say?",
    "I'll always remember that VC; was it as great for you?",
    "Top-tier voice chat there, agreed?",
    "Can't stop smiling about that VC; you feel the same?"
]

SPECIAL_VOICE_LEAVE_TASKS: Dict[int, asyncio.Task] = {}
MOVIE_PROMPT_TIMEOUT_SECONDS = 60
MOVIE_STAY_DEFAULT_MINUTES = 15
MOVIE_STAY_MAX_MINUTES = 30
MOVIE_QUEUE_LIMIT = 25
MOVIE_ACTIVE_GUILDS: Set[int] = set()
MOVIE_STATES: Dict[int, Dict[str, Any]] = {}
MOVIE_STAY_TASKS: Dict[int, asyncio.Task] = {}

GUEST_ROLE_NAME = "Guest"
QUARANTINE_ROLE_NAME = "Quarantine"
STAFF_ROLE_NAME = "Staff"
ADMIN_ROLE_NAME = "Admin"
GOD_ROLE_NAME = "GOD"

ROLE_LEVEL_DEFAULTS = {
    GOD_ROLE_NAME: 90,
    ADMIN_ROLE_NAME: 70,
    STAFF_ROLE_NAME: 50,
    GUEST_ROLE_NAME: 1,
    QUARANTINE_ROLE_NAME: 1
}

MIRROR_FAIL_THRESHOLD = 3
MIRROR_CACHE_REFRESH = 10
SERVER_STATUS_REFRESH = 60
INTEGRITY_REFRESH = 60
CLEANUP_RESPONSE_TTL = 20

DEFAULT_AI_LIMITS = {
    "gemini-2.5-pro": {"rpm": 5, "tpm": 250000, "rpd": 100},
    "gemini-2.5-flash": {"rpm": 10, "tpm": 250000, "rpd": 250},
    "gemini-2.5-flash-lite": {"rpm": 15, "tpm": 250000, "rpd": 1000},
    "gemini-3-pro-preview": {"rpm": 2, "tpm": 250000, "rpd": 50},
    "imagen-3": {"rpm": 2, "rpd": 50},
}

# -----------------------------
# Secrets loader
# -----------------------------
def load_secrets(path: str = PASSWORDS_PATH) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not os.path.exists(path):
        return data
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            data[k.strip()] = v.strip()
    return data

SECRETS = load_secrets()

DISCORD_TOKEN = SECRETS.get("DISCORD_TOKEN") or os.getenv("DISCORD_TOKEN")
SERVER_PASSWORD = SECRETS.get("SERVER_PASSWORD") or os.getenv("SERVER_PASSWORD") or ""

MYSQL_HOST = SECRETS.get("MYSQL_HOST") or os.getenv("MYSQL_HOST")
MYSQL_DB   = SECRETS.get("MYSQL_DB") or os.getenv("MYSQL_DB")
MYSQL_USER = SECRETS.get("MYSQL_USER") or os.getenv("MYSQL_USER")
MYSQL_PASS = SECRETS.get("MYSQL_PASS") or os.getenv("MYSQL_PASS")
GEMINI_API_KEY = SECRETS.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")

MYSQL_ENABLED = bool(MYSQL_HOST and MYSQL_DB and MYSQL_USER is not None)

if not DISCORD_TOKEN:
    raise SystemExit("Missing DISCORD_TOKEN in passwords.txt or env")

# -----------------------------
# JSON Store (live-edit)
# -----------------------------
DEFAULT_JSON: Dict[str, Any] = {
    "targets": {},       # your watcher targets live here
    "mirrors": {         # legacy: "guild:src_channel" -> dst_channel
        "interactive_controls_enabled": True
    },
    "mirror_rules": {},  # new unified mirror rules
    "mirror_status": {}, # per-guild last mirror
    "admin_servers": {}, # admin server mirror/status channels
    "server_status_messages": {},
    "server_info_messages": {},
    "mirror_message_map": {},  # json mirror message mapping (rule_id -> list)
    "dm_bridges": {},    # fallback dm bridges: "user_id" -> admin_channel_id
    "bot_status": {      # presence state + text
        "state": "online",
        "text": ""
    },
    "presence": {
        "bio": "",
        "autopresence_enabled": False,
        "last_message_ts": 0,
        "last_super_interaction_ts": 0
    },
    "ambient_engine": {
        "enabled": True,
        "last_typing": 0,
        "last_presence": 0
    },
    "permissions": {},   # json perms: "user_id" -> level
    "gate": {},          # gate state: "user_id" -> {channel, tries}
    "mirror_fail_threshold": MIRROR_FAIL_THRESHOLD,
    "logs": {            # log channel ids
        "system": None,
        "audit": None,
        "debug": None,
        "mirror": None,
        "ai": None,
        "voice": None
    },
    "command_channels": {
        "user": "command-requests",
        "god": "admin-chat",
        "mode": "off"  # off=anywhere, hard=clean + forward, soft=remind only
    },
    "menu_messages": {},
    "rbac": {
        "role_levels": ROLE_LEVEL_DEFAULTS.copy()
    },
    "auto": {
        "setup": True,
        "backfill": True,
        "backfill_limit": 50,
        "backfill_per_channel": 20,
        "backfill_delay": 0.2
    },
    "tuning": {
        "setup_delay": 1.0
    },
    "ai": {
        "default_model": "gemini-2.5-flash-lite",
        "router_model": "gemini-2.5-flash-lite",
        "tts_model": "",
        "cooldown_seconds": 5,
        "limits": DEFAULT_AI_LIMITS.copy(),
        "queue": {},
        "rolling": {},
        "daily": {},
        "installed_extensions": []
    },
    "mandy": {
        "mention_dm_cooldowns": {}
    },
    "sentience": {
        "enabled": True,
        "dialect": "sentient_core",
        "channels": {},
        "thoughts_rate_limit_seconds": 30,
        "menu_style": "default",
        "daily_reflection": {
            "enabled": False,
            "last_run_utc": 0,
            "hour_utc": None,
            "max_messages": 120,
            "fallback_enabled": False
        },
        "internal_monologue": {
            "enabled": False,
            "last_run_utc": 0,
            "interval_minutes": 180,
            "max_lines": 4
        },
        "maintenance": {
            "enabled": True,
            "ai_queue_max_age_hours": 6
        }
    },
    "diagnostics": {
        "channel_id": 0,
        "message_id": 0,
        "last_update": 0
    },
    "manual": {
        "channel_id": 0,
        "last_hash": "",
        "last_message_id": 0,
        "last_upload": 0,
        "auto_upload_enabled": False
    },
    "memory": {
        "events": []  # long-term sentiment / notes
    },
    "ark_snapshots": {},
    "phoenix_keys": {},
    "onboarding": {
        "rules_channel_id": 0,
        "role_name": "Citizen",
        "phrases": ["i agree"]
    },
    "backfill_state": {
        "done": {}
    },
    "chat_stats": {},
    "chat_stats_backfill_done": {},
    "chat_stats_live_message": {},
    "chat_stats_global_live_message": {},
    "layout": {
        "categories": {
            "Welcome & Information": ["rules-and-guidelines", "announcements", "guest-briefing"],
            "Bot Control & Monitoring": ["bot-status", "command-requests", "error-reporting"],
            "Research & Development": ["algorithm-discussion", "data-analysis"],
            "Guest Access": ["guest-chat", "guest-feedback", "quarantine"],
            "Engineering Core": ["core-chat", "system-logs", "audit-logs", "debug-logs", "mirror-logs"],
            "Admin Backrooms": ["admin-chat", "server-management"],
            "DM Bridges": []
        }
    },
    "channel_topics": {
        "rules-and-guidelines": "Read these first. Required for all members.",
        "announcements": "Server announcements and updates.",
        "guest-briefing": "How to join and get approved.",
        "guest-chat": "Guest chat (limited).",
        "guest-feedback": "Feedback and questions from guests.",
        "quarantine": "Restricted holding channel.",
        "bot-status": "Bot status updates and presence controls.",
        "command-requests": "User command requests. Commands outside this channel are removed.",
        "error-reporting": "Report issues or errors with commands.",
        "core-chat": "Core engineering discussion.",
        "algorithm-discussion": "Research ideas, algorithms, and experiments.",
        "data-analysis": "Data analysis, metrics, and reports.",
        "system-logs": "System log stream (general).",
        "audit-logs": "Audit trail for privileged actions.",
        "debug-logs": "Debug output and diagnostics.",
        "mirror-logs": "Mirror pipeline events and failures.",
        "admin-chat": "GOD-only commands and admin coordination.",
        "server-management": "Server ops notes and maintenance."
    },
    "pinned_text": {
        "rules-and-guidelines": (
            "dY\"O **Rules & Guidelines**\n"
            "- Be respectful.\n"
            "- No spam.\n"
            "- Follow staff instructions.\n\n"
            "**Commands (prefix):**\n"
            "- `!menu`\n"
            "- `!godmenu`\n"
            "- `!setup fullsync`\n"
        ),
        "bot-status": (
            "dY\"O **Bot Status & Help**\n"
            "Menus auto-populate in command channels.\n"
        ),
        "system-logs": (
            "dY\"O **System Logs**\n"
            "General system log stream.\n"
        ),
        "command-requests": (
            "dY\"O **Command Requests**\n"
            "Use the menu panel below for user tools.\n"
        ),
        "error-reporting": (
            "dY\"O **Error Reporting**\n"
            "Post issues with timestamps and screenshots if possible.\n"
        ),
        "audit-logs": (
            "dY\"O **Audit Logs**\n"
            "Privileged actions and security events.\n"
        ),
        "debug-logs": (
            "dY\"O **Debug Logs**\n"
            "Diagnostic output and errors.\n"
        ),
        "mirror-logs": (
            "dY\"O **Mirror Logs**\n"
            "Mirror events, failures, and status.\n"
        ),
        "guest-briefing": (
            "dY\"O **Guest Briefing**\n"
            "This server uses a password gate. Ask staff if youƒ?Tre stuck.\n"
        ),
        "quarantine": (
            "dY\"O **Quarantine**\n"
            "Quarantined users wait here until staff releases them.\n"
        ),
        "admin-chat": (
            "dY\"O **Admin Chat**\n"
            "GOD-only command channel. Use the panel below.\n"
        )
    }
}



def chunk_lines(lines: list, header: str, limit: int = 1900) -> list:
    """Chunk text with a header repeated per chunk."""
    chunks = []
    cur = header
    for line in lines:
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = header
        cur += "\n" + line
    if cur:
        chunks.append(cur)
    return chunks


def memory_state() -> Dict[str, Any]:
    return cfg().setdefault("memory", {}).setdefault("events", [])


async def memory_add(kind: str, text: str, meta: Optional[Dict[str, Any]] = None):
    events = cfg().setdefault("memory", {}).setdefault("events", [])
    events.append({
        "ts": now_ts(),
        "kind": kind,
        "text": truncate(text, 500),
        "meta": meta or {},
    })
    # keep last 200 events to stay lean
    if len(events) > 200:
        del events[:-200]
    await STORE.mark_dirty()


def memory_recent(limit: int = 10) -> List[Dict[str, Any]]:
    events = list(cfg().setdefault("memory", {}).get("events", []))
    return list(events[-limit:])


def ark_snapshots() -> Dict[str, Any]:
    return cfg().setdefault("ark_snapshots", {})


def phoenix_keys() -> Dict[str, str]:
    return cfg().setdefault("phoenix_keys", {})


async def request_elevation(action: str, reason: str, meta: Optional[Dict[str, Any]] = None):
    """Ask SUPERUSER for help when Mandy lacks permissions."""
    await audit(SUPER_USER_ID, f"Assist requested: {action}", {"reason": reason, **(meta or {})})
    try:
        owner = await bot.fetch_user(SUPER_USER_ID)
        msg = f"Assist requested for `{action}`: {reason}"
        if meta:
            msg += f"\nmeta: {meta}"
        await owner.send(msg)
    except Exception:
        pass


def classify_mood(text: str) -> str:
    lower = (text or "").lower()
    negative = ("angry", "mad", "hate", "annoyed", "wtf", "stupid", "dumb", "trash")
    positive = ("love", "awesome", "great", "thanks", "thank you", "nice", "cool")
    if any(w in lower for w in positive):
        return "positive"
    if any(w in lower for w in negative):
        return "negative"
    return "neutral"


def bot_missing_permissions(guild: discord.Guild) -> List[str]:
    m = guild.me
    if not m:
        return ["unknown"]
    perms = m.guild_permissions
    missing = []
    for name in (
        "view_channel",
        "read_message_history",
        "send_messages",
        "manage_channels",
        "manage_roles",
        "manage_messages",
    ):
        if not getattr(perms, name, False):
            missing.append(name)
    return missing


async def send_owner_server_report(guild: discord.Guild, reason: str = ""):
    if not guild:
        return
    owner = guild.owner
    if not owner and guild.owner_id:
        try:
            owner = await bot.fetch_user(guild.owner_id)
        except Exception:
            owner = None
    if not owner:
        return

    members = list(guild.members or [])
    member_names = [m.display_name for m in members][:25]
    targets = cfg().get("targets", {})
    watcher_hits = [uid for uid in targets.keys() if guild.get_member(int(uid))]
    missing = bot_missing_permissions(guild)

    lines = [
        f"Server report ({reason}): {guild.name} ({guild.id})",
        f"Members: {len(members)}",
        "Sample members: " + (", ".join(member_names) if member_names else "none"),
        f"Watchers in this server: {len(watcher_hits)}",
    ]
    if watcher_hits:
        lines.append("Watcher IDs: " + ", ".join(watcher_hits[:15]))
    if missing and missing != ["unknown"]:
        lines.append("Missing permissions: " + ", ".join(missing))
    else:
        lines.append("Missing permissions: none")
    try:
        await owner.send("\n".join(lines))
    except Exception:
        pass

def guild_word_freq(guild_id: int, window: str = "rolling24") -> Dict[str, int]:
    gstate = chat_stats_guild_state(guild_id)
    window_state = gstate.get(window, {})
    freq: Dict[str, int] = {}
    for entry in window_state.values():
        for w, c in (entry.get("word_freq", {}) or {}).items():
            freq[w] = int(freq.get(w, 0)) + int(c)
    trim_word_freq(freq, 200)
    return freq


def build_dynamic_blueprint(guild: discord.Guild) -> Dict[str, Any]:
    freq = guild_word_freq(guild.id, "rolling24")
    top_words = [w for w, _ in sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:20]]
    categories = [
        {"name": "WELCOME", "channels": ["rules", "announcements"]},
        {"name": "GENERAL", "channels": ["general", "media", "off-topic"]},
    ]
    notes: List[str] = []

    dev_words = {"code", "coding", "python", "java", "debug", "dev", "programming"}
    game_words = {"minecraft", "roblox", "fortnite", "valorant", "gta", "osu"}

    if any(w in top_words for w in dev_words):
        categories.append({"name": "DEVELOPMENT", "channels": ["python", "java", "debugging"]})
        notes.append("Detected developer-heavy chat; adding DEVELOPMENT category.")
    if any(w in top_words for w in game_words):
        categories.append({"name": "GAMING", "channels": ["minecraft", "game-chat", "clips"]})
        notes.append("Detected gaming-heavy chat; adding GAMING category.")

    return {"categories": categories, "notes": notes, "top_words": top_words}


def serialize_overwrites(channel: discord.abc.GuildChannel) -> Dict[str, Any]:
    ow = {}
    try:
        for target, perms in channel.overwrites.items():
            key = f"role:{target.id}" if isinstance(target, discord.Role) else f"user:{target.id}"
            ow[key] = {
                "allow": perms.value,
                "deny": perms._from_pair()[1].value if hasattr(perms, "_from_pair") else perms.value ^ (~perms).value
            }
    except Exception:
        pass
    return ow


def deserialize_overwrites(guild: discord.Guild, data: Dict[str, Any]) -> Dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    result: Dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {}
    for key, perms in (data or {}).items():
        try:
            kind, sid = key.split(":", 1)
            sid = int(sid)
            target = guild.get_role(sid) if kind == "role" else guild.get_member(sid)
            if not target:
                continue
            allow_val = int(perms.get("allow", 0))
            deny_val = int(perms.get("deny", 0))
            overw = discord.Permissions(allow_val).pair(discord.Permissions(deny_val))
            result[target] = overw
        except Exception:
            continue
    return result

class JsonStore:
    def __init__(self, path: str):
        self.path = path
        self.lock = asyncio.Lock()
        self.data: Dict[str, Any] = {}
        self.dirty = False
        self.last_mtime = 0.0

    def _deep_merge(self, base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
        for k, v in overlay.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k] = self._deep_merge(base[k], v)
            else:
                base[k] = v
        return base

    async def load(self) -> None:
        async with self.lock:
            if not os.path.exists(self.path):
                self.data = json.loads(json.dumps(DEFAULT_JSON))
                self.dirty = True
                await self.flush_locked()
                return
            self.last_mtime = os.path.getmtime(self.path)
            async with aiofiles.open(self.path, "r", encoding="utf-8") as f:
                raw = await f.read()
            try:
                loaded = json.loads(raw)
            except Exception:
                loaded = {}
            self.data = self._deep_merge(json.loads(json.dumps(DEFAULT_JSON)), loaded)

    async def reload_if_changed(self) -> None:
        async with self.lock:
            if not os.path.exists(self.path):
                return
            mtime = os.path.getmtime(self.path)
            if mtime > self.last_mtime + 0.0001:
                async with aiofiles.open(self.path, "r", encoding="utf-8") as f:
                    raw = await f.read()
                try:
                    loaded = json.loads(raw)
                except Exception:
                    return
                self.data = self._deep_merge(json.loads(json.dumps(DEFAULT_JSON)), loaded)
                self.last_mtime = mtime

    async def mark_dirty(self) -> None:
        async with self.lock:
            self.dirty = True

    async def flush(self) -> None:
        async with self.lock:
            await self.flush_locked()

    async def flush_locked(self) -> None:
        if not self.dirty:
            return
        tmp = self.path + ".tmp"
        async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
            await f.write(json.dumps(self.data, indent=2, ensure_ascii=False))
        os.replace(tmp, self.path)
        self.dirty = False
        if os.path.exists(self.path):
            self.last_mtime = os.path.getmtime(self.path)

STORE = JsonStore(DB_JSON_PATH)
MENTION_COOLDOWN = CooldownStore(STORE)

def cfg() -> Dict[str, Any]:
    return STORE.data

def ai_cfg() -> Dict[str, Any]:
    ai = cfg().setdefault("ai", {})
    ai.setdefault("default_model", "gemini-2.5-flash-lite")
    ai.setdefault("router_model", ai.get("default_model") or "gemini-2.5-flash-lite")
    ai.setdefault("tts_model", "")
    ai.setdefault("cooldown_seconds", 5)
    ai.setdefault("limits", json.loads(json.dumps(DEFAULT_AI_LIMITS)))
    ai.setdefault("queue", {})
    ai.setdefault("rolling", {})
    ai.setdefault("daily", {})
    ai.setdefault("installed_extensions", [])
    return ai

def strip_bot_mentions(text: str, bot_id: int) -> str:
    if not text or not bot_id:
        return ""
    cleaned = re.sub(rf"<@!?{bot_id}>", "", text)
    return " ".join(cleaned.split())

def is_youtube_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "youtu.be":
        return True
    if host == "youtube.com" or host.endswith(".youtube.com"):
        return True
    return False

def normalize_youtube_url(url: str) -> str:
    if not url:
        return ""
    normalized = url.strip()
    if normalized and not re.match(r"^https?://", normalized, re.IGNORECASE):
        normalized = "https://" + normalized
    return normalized

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "ignoreerrors": True,
    "default_search": "auto",
}
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

class YTDLSource(discord.PCMVolumeTransformer):
    ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

    def __init__(self, source: discord.AudioSource, *, data: Dict[str, Any]):
        super().__init__(source)
        self.data = data
        self.url = data.get("url")

    @classmethod
    async def from_url(cls, url: str, *, loop: Optional[asyncio.AbstractEventLoop] = None, stream: bool = True):
        if loop is None:
            loop = asyncio.get_running_loop()

        data = await loop.run_in_executor(
            None, lambda: cls.ytdl.extract_info(url, download=not stream)
        )
        if not data:
            raise ValueError("Unable to retrieve voice media metadata.")
        if "entries" in data:
            data = data["entries"][0]
        if not data:
            raise ValueError("Unable to resolve a playable voice entry.")

        source = discord.FFmpegPCMAudio(data["url"], **FFMPEG_OPTIONS)
        return cls(source, data=data)

async def start_special_user_voice(channel: discord.VoiceChannel) -> None:
    try:
        guild = channel.guild
        voice_client = discord.utils.get(bot.voice_clients, guild=guild)
        if not voice_client:
            voice_client = await channel.connect()
        elif voice_client.channel != channel:
            await voice_client.move_to(channel)

        if voice_client.is_playing():
            voice_client.stop()

        loop = asyncio.get_running_loop()
        source = await YTDLSource.from_url(SPECIAL_VOICE_URL, loop=loop, stream=True)

        def _after_play(error: Optional[Exception] = None) -> None:
            if error:
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(
                        debug(f"special voice playback failed: {error}")
                    )
                )

        voice_client.play(source, after=_after_play)
    except Exception as exc:
        await debug(f"special voice setup failed: {exc}")

async def _special_voice_leave_flow(guild: discord.Guild) -> None:
    try:
        await asyncio.sleep(VOICE_QUIT_DELAY_SECONDS)
        voice_client = discord.utils.get(bot.voice_clients, guild=guild)
        if voice_client and voice_client.is_connected():
            if voice_client.is_playing():
                voice_client.stop()
            await voice_client.disconnect()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        await debug(f"special voice tear-down failed: {exc}")

def cancel_special_voice_leave_task(guild_id: int) -> None:
    task = SPECIAL_VOICE_LEAVE_TASKS.pop(guild_id, None)
    if task and not task.done():
        task.cancel()

def schedule_special_voice_leave(guild: discord.Guild) -> None:
    cancel_special_voice_leave_task(guild.id)
    task = asyncio.create_task(_special_voice_leave_flow(guild))
    SPECIAL_VOICE_LEAVE_TASKS[guild.id] = task
    task.add_done_callback(lambda _: SPECIAL_VOICE_LEAVE_TASKS.pop(guild.id, None))

def movie_state(guild_id: int) -> Dict[str, Any]:
    state = MOVIE_STATES.setdefault(guild_id, {})
    state.setdefault("queue", [])
    state.setdefault("volume", 1.0)
    state.setdefault("stay_until", 0)
    state.setdefault("channel_id", 0)
    state.setdefault("now_title", "")
    state.setdefault("now_url", "")
    return state

def cancel_movie_stay_task(guild_id: int) -> None:
    task = MOVIE_STAY_TASKS.pop(guild_id, None)
    if task and not task.done():
        task.cancel()

def schedule_movie_stay_task(guild_id: int) -> None:
    cancel_movie_stay_task(guild_id)
    state = MOVIE_STATES.get(guild_id)
    if not state:
        return
    stay_until = int(state.get("stay_until", 0) or 0)
    if stay_until <= now_ts():
        return

    async def _wait_then_cleanup() -> None:
        await asyncio.sleep(max(0, stay_until - now_ts()))
        await _movie_cleanup(guild_id)

    task = asyncio.create_task(_wait_then_cleanup())
    MOVIE_STAY_TASKS[guild_id] = task
    task.add_done_callback(lambda _: MOVIE_STAY_TASKS.pop(guild_id, None))

async def _movie_cleanup(guild_id: int) -> None:
    state = MOVIE_STATES.get(guild_id)
    guild = bot.get_guild(guild_id)
    voice_client = discord.utils.get(bot.voice_clients, guild=guild) if guild else None
    if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
        return
    if state and state.get("queue"):
        return
    stay_until = int(state.get("stay_until", 0) or 0) if state else 0
    if stay_until and now_ts() < stay_until:
        schedule_movie_stay_task(guild_id)
        return
    MOVIE_ACTIVE_GUILDS.discard(guild_id)
    if voice_client and voice_client.is_connected():
        try:
            await voice_client.disconnect()
        except Exception:
            pass
    if state:
        state["queue"] = []
        state["stay_until"] = 0
        state["channel_id"] = 0
        state["now_title"] = ""
        state["now_url"] = ""

async def movie_get_voice_client(guild: discord.Guild, channel: discord.VoiceChannel) -> Optional[discord.VoiceClient]:
    voice_client = discord.utils.get(bot.voice_clients, guild=guild)
    if not voice_client:
        voice_client = await channel.connect()
    elif voice_client.channel != channel:
        await voice_client.move_to(channel)
    return voice_client

async def movie_start_playback(
    guild: discord.Guild,
    channel: discord.VoiceChannel,
    url: str,
    clear_queue: bool = False,
) -> None:
    state = movie_state(guild.id)
    if clear_queue:
        state["queue"] = []
    cancel_special_voice_leave_task(guild.id)
    MOVIE_ACTIVE_GUILDS.add(guild.id)
    voice_client = await movie_get_voice_client(guild, channel)
    if not voice_client:
        raise RuntimeError("Unable to join voice channel.")
    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()

    loop = asyncio.get_running_loop()
    source = await YTDLSource.from_url(url, loop=loop, stream=True)
    source.volume = float(state.get("volume", 1.0))
    state["channel_id"] = channel.id
    state["now_title"] = str(source.data.get("title") or "")
    state["now_url"] = url

    def _after_play(error: Optional[Exception] = None) -> None:
        if error:
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(debug(f"movie playback failed: {error}"))
            )
        loop.call_soon_threadsafe(lambda: asyncio.create_task(movie_handle_track_end(guild.id)))

    voice_client.play(source, after=_after_play)

async def movie_handle_track_end(guild_id: int) -> None:
    state = MOVIE_STATES.get(guild_id)
    if not state:
        await _movie_cleanup(guild_id)
        return
    if state.get("queue"):
        next_url = state["queue"].pop(0)
        guild = bot.get_guild(guild_id)
        channel_id = int(state.get("channel_id", 0) or 0)
        channel = guild.get_channel(channel_id) if guild else None
        if isinstance(channel, discord.VoiceChannel):
            try:
                await movie_start_playback(guild, channel, next_url)
                return
            except Exception as exc:
                await debug(f"movie next track failed: {exc}")
    await _movie_cleanup(guild_id)

async def movie_queue_add(guild: discord.Guild, channel: discord.VoiceChannel, url: str) -> Tuple[bool, str]:
    state = movie_state(guild.id)
    voice_client = discord.utils.get(bot.voice_clients, guild=guild)
    playing = voice_client and (voice_client.is_playing() or voice_client.is_paused())
    if playing or state.get("queue"):
        if len(state["queue"]) >= MOVIE_QUEUE_LIMIT:
            return False, "Queue is full."
        state["queue"].append(url)
        MOVIE_ACTIVE_GUILDS.add(guild.id)
        return True, "Queued."
    await movie_start_playback(guild, channel, url)
    return True, "Playing now."

async def movie_stop(guild_id: int) -> None:
    cancel_movie_stay_task(guild_id)
    state = MOVIE_STATES.get(guild_id)
    if state:
        state["queue"] = []
        state["stay_until"] = 0
        state["channel_id"] = 0
        state["now_title"] = ""
        state["now_url"] = ""
    MOVIE_ACTIVE_GUILDS.discard(guild_id)
    guild = bot.get_guild(guild_id)
    voice_client = discord.utils.get(bot.voice_clients, guild=guild) if guild else None
    if voice_client and voice_client.is_connected():
        try:
            if voice_client.is_playing() or voice_client.is_paused():
                voice_client.stop()
            await voice_client.disconnect()
        except Exception:
            pass

async def movie_set_volume(guild_id: int, volume: float) -> None:
    state = movie_state(guild_id)
    state["volume"] = max(0.0, min(2.0, float(volume)))
    guild = bot.get_guild(guild_id)
    voice_client = discord.utils.get(bot.voice_clients, guild=guild) if guild else None
    if voice_client and isinstance(voice_client.source, discord.PCMVolumeTransformer):
        voice_client.source.volume = state["volume"]

async def movie_pause(guild_id: int) -> bool:
    guild = bot.get_guild(guild_id)
    voice_client = discord.utils.get(bot.voice_clients, guild=guild) if guild else None
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        return True
    return False

async def movie_resume(guild_id: int) -> bool:
    guild = bot.get_guild(guild_id)
    voice_client = discord.utils.get(bot.voice_clients, guild=guild) if guild else None
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        return True
    return False

async def movie_skip(guild_id: int) -> bool:
    guild = bot.get_guild(guild_id)
    voice_client = discord.utils.get(bot.voice_clients, guild=guild) if guild else None
    if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
        voice_client.stop()
        return True
    return False

async def movie_find_voice_targets(user_id: int) -> List[Tuple[discord.Guild, discord.VoiceChannel]]:
    targets: List[Tuple[discord.Guild, discord.VoiceChannel]] = []
    for guild in bot.guilds:
        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                member = None
        if member and member.voice and isinstance(member.voice.channel, discord.VoiceChannel):
            targets.append((guild, member.voice.channel))
    return targets

async def movie_resolve_target(ctx: commands.Context) -> Tuple[Optional[discord.Guild], Optional[discord.VoiceChannel], Optional[str]]:
    if ctx.guild and isinstance(ctx.author, discord.Member):
        if ctx.author.voice and ctx.author.voice.channel:
            return ctx.guild, ctx.author.voice.channel, None
    targets = await movie_find_voice_targets(ctx.author.id)
    if not targets:
        return None, None, "Join a voice channel first."
    if len(targets) > 1:
        return None, None, "Multiple voice channels detected. Use the `!movie` menu to pick a target."
    return targets[0][0], targets[0][1], None

async def send_movie_menu(ctx: commands.Context) -> None:
    targets = await movie_find_voice_targets(ctx.author.id)
    if not targets:
        await temp_reply(ctx, "Join a voice channel first.")
        return
    target_map: Dict[str, Tuple[int, int, str]] = {}
    for guild, channel in targets:
        key = f"{guild.id}:{channel.id}"
        label = f"{guild.name} / {channel.name}"
        target_map[key] = (guild.id, channel.id, label)
    view = MovieControlView(ctx.author.id, target_map)
    await ctx.send("Movie control panel:", view=view)

class MovieTargetSelect(discord.ui.Select):
    def __init__(self, options: List[discord.SelectOption]):
        super().__init__(placeholder="Select voice target", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if isinstance(view, MovieControlView):
            view.selected = self.values[0]
            label = view.targets.get(self.values[0], ("", "", "target"))[2]
            await interaction.response.send_message(f"Selected: {label}", ephemeral=True)

class MovieLinkModal(discord.ui.Modal):
    def __init__(self, guild_id: int, channel_id: int, mode: str):
        title = "Play YouTube Link" if mode == "play" else "Queue YouTube Link"
        super().__init__(title=title, timeout=300)
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.mode = mode
        self.url = discord.ui.TextInput(
            label="YouTube URL",
            style=discord.TextStyle.short,
            required=True,
            max_length=200,
            placeholder="https://youtu.be/..."
        )
        self.add_item(self.url)

    async def on_submit(self, interaction: discord.Interaction):
        url = normalize_youtube_url(str(self.url.value))
        if not is_youtube_url(url):
            return await interaction.response.send_message("Only YouTube links are allowed.", ephemeral=True)
        guild = bot.get_guild(self.guild_id)
        channel = guild.get_channel(self.channel_id) if guild else None
        if not guild or not isinstance(channel, discord.VoiceChannel):
            return await interaction.response.send_message("Voice channel not found.", ephemeral=True)
        try:
            if self.mode == "queue":
                ok, msg = await movie_queue_add(guild, channel, url)
                return await interaction.response.send_message(msg, ephemeral=True)
            await movie_start_playback(guild, channel, url, clear_queue=True)
            return await interaction.response.send_message("Playing now.", ephemeral=True)
        except Exception as exc:
            await debug(f"movie modal failed: {exc}")
            return await interaction.response.send_message("Failed to start playback.", ephemeral=True)

class MovieVolumeModal(discord.ui.Modal):
    def __init__(self, guild_id: int):
        super().__init__(title="Set Volume (0-100)", timeout=300)
        self.guild_id = guild_id
        self.volume = discord.ui.TextInput(
            label="Volume",
            style=discord.TextStyle.short,
            required=True,
            max_length=4,
            placeholder="50"
        )
        self.add_item(self.volume)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(str(self.volume.value).strip())
        except Exception:
            return await interaction.response.send_message("Volume must be a number.", ephemeral=True)
        value = max(0, min(100, value))
        await movie_set_volume(self.guild_id, value / 100.0)
        return await interaction.response.send_message(f"Volume set to {value}%.", ephemeral=True)

class MovieStayModal(discord.ui.Modal):
    def __init__(self, guild_id: int):
        super().__init__(title="Stay Time (minutes)", timeout=300)
        self.guild_id = guild_id
        self.minutes = discord.ui.TextInput(
            label="Minutes (max 30)",
            style=discord.TextStyle.short,
            required=False,
            max_length=4,
            placeholder=str(MOVIE_STAY_DEFAULT_MINUTES)
        )
        self.add_item(self.minutes)

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.minutes.value).strip()
        if raw:
            try:
                minutes = int(raw)
            except Exception:
                return await interaction.response.send_message("Minutes must be a number.", ephemeral=True)
        else:
            minutes = MOVIE_STAY_DEFAULT_MINUTES
        minutes = max(1, min(MOVIE_STAY_MAX_MINUTES, minutes))
        state = movie_state(self.guild_id)
        state["stay_until"] = now_ts() + minutes * 60
        schedule_movie_stay_task(self.guild_id)
        return await interaction.response.send_message(f"Stay set for {minutes} minutes.", ephemeral=True)

class MovieControlView(discord.ui.View):
    def __init__(self, user_id: int, targets: Dict[str, Tuple[int, int, str]]):
        super().__init__(timeout=600)
        self.user_id = user_id
        self.targets = targets
        self.selected = next(iter(targets.keys())) if targets else ""
        if len(targets) > 1:
            options = [
                discord.SelectOption(label=label, value=key)
                for key, (_, _, label) in targets.items()
            ]
            self.add_item(MovieTargetSelect(options))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return False
        return True

    def get_selected_target(self) -> Optional[Tuple[int, int, str]]:
        return self.targets.get(self.selected)

    @discord.ui.button(label="Play", style=discord.ButtonStyle.green, row=0)
    async def play_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, channel_id, _ = target
        await interaction.response.send_modal(MovieLinkModal(guild_id, channel_id, "play"))

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.blurple, row=0)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, channel_id, _ = target
        await interaction.response.send_modal(MovieLinkModal(guild_id, channel_id, "queue"))

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.gray, row=0)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, _, _ = target
        skipped = await movie_skip(guild_id)
        msg = "Skipped." if skipped else "Nothing to skip."
        await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="Volume", style=discord.ButtonStyle.blurple, row=1)
    async def volume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, _, _ = target
        await interaction.response.send_modal(MovieVolumeModal(guild_id))

    @discord.ui.button(label="Stay", style=discord.ButtonStyle.gray, row=1)
    async def stay_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, _, _ = target
        await interaction.response.send_modal(MovieStayModal(guild_id))

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.red, row=1)
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, _, _ = target
        await movie_stop(guild_id)
        await interaction.response.send_message("Disconnected.", ephemeral=True)

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.gray, row=2)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, _, _ = target
        paused = await movie_pause(guild_id)
        msg = "Paused." if paused else "Nothing playing."
        await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="Resume", style=discord.ButtonStyle.gray, row=2)
    async def resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = self.get_selected_target()
        if not target:
            return await interaction.response.send_message("Select a target first.", ephemeral=True)
        guild_id, _, _ = target
        resumed = await movie_resume(guild_id)
        msg = "Resumed." if resumed else "Nothing paused."
        await interaction.response.send_message(msg, ephemeral=True)

# -----------------------------
# Bot
# -----------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.dm_messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

INTEGRITY_CURSOR = 0
GLOBAL_USER_RESOLVER = GuildIndexCache(ttl_seconds=120)
AUTO_SETUP_LOCK = asyncio.Lock()
TYPING_RATE_SECONDS = 6.0
TYPING_INDICATORS: Dict[int, float] = {}
BRIDGE_TYPING_INDICATORS: Dict[int, float] = {}
LIVE_STATS_TASKS: Dict[int, asyncio.Task] = {}
MANDY_EXTENSION = "cogs.mandy_ai"
MANDY_LOADED = False

# -----------------------------
# Optional MySQL
# -----------------------------
POOL: Optional[aiomysql.Pool] = None

async def db_init():
    global POOL
    if not MYSQL_ENABLED:
        return
    POOL = await aiomysql.create_pool(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASS or "",
        db=MYSQL_DB,
        autocommit=True,
        minsize=1,
        maxsize=10,
        charset="utf8mb4",
    )

async def db_exec(sql: str, args: tuple = ()):
    if not POOL:
        return
    async with POOL.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)

async def db_one(sql: str, args: tuple = ()):
    if not POOL:
        return None
    async with POOL.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            return await cur.fetchone()

async def db_all(sql: str, args: tuple = ()):
    if not POOL:
        return []
    async with POOL.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            return await cur.fetchall()

async def ensure_table_columns(table: str, cols: Dict[str, str]):
    if not POOL:
        return
    for col, col_type in cols.items():
        try:
            if not await db_column_exists(table, col):
                await db_exec(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        except Exception:
            pass

async def db_column_exists(table: str, column: str) -> bool:
    if not POOL:
        return False
    row = await db_one(
        "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        (table, column),
    )
    return bool(row)

async def ensure_mirror_rules_columns():
    await ensure_table_columns("mirror_rules", {
        "enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
        "fail_count": "INT NOT NULL DEFAULT 0",
        "last_error": "TEXT",
        "last_mirror_ts": "BIGINT",
        "last_mirror_msg": "TEXT",
        "last_disabled_at": "BIGINT NOT NULL DEFAULT 0",
    })

async def ensure_watchers_columns():
    await ensure_table_columns("watchers", {
        "threshold": "INT NOT NULL DEFAULT 0",
        "current": "INT NOT NULL DEFAULT 0",
        "text": "TEXT",
        "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
    })

async def ensure_users_permissions_columns():
    await ensure_table_columns("users_permissions", {
        "note": "TEXT",
        "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
    })

async def ensure_mirrors_columns():
    await ensure_table_columns("mirrors", {
        "enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
        "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
    })

async def ensure_mirror_messages_columns():
    await ensure_table_columns("mirror_messages", {
        "mirror_id": "VARCHAR(96) NOT NULL",
        "src_guild": "BIGINT NOT NULL",
        "src_channel": "BIGINT NOT NULL",
        "src_msg": "BIGINT NOT NULL",
        "dst_msg": "BIGINT NOT NULL",
        "author_id": "BIGINT NOT NULL",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    })

async def ensure_dm_bridges_columns():
    await ensure_table_columns("dm_bridges", {
        "channel_id": "BIGINT NOT NULL",
        "active": "BOOLEAN NOT NULL DEFAULT TRUE",
        "last_activity": "BIGINT",
        "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
    })

async def ensure_audit_logs_columns():
    await ensure_table_columns("audit_logs", {
        "action": "TEXT",
        "meta": "JSON",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    })

async def db_calibrate():
    if not POOL:
        return
    await ensure_users_permissions_columns()
    await ensure_mirrors_columns()
    await ensure_mirror_rules_columns()
    await ensure_mirror_messages_columns()
    await ensure_watchers_columns()
    await ensure_dm_bridges_columns()
    await ensure_audit_logs_columns()

async def db_purge_all():
    if not POOL:
        return False
    tables = [
        "mirror_messages",
        "mirror_rules",
        "mirrors",
        "watchers",
        "dm_bridges",
        "audit_logs",
        "users_permissions",
    ]
    for table in tables:
        try:
            await db_exec(f"TRUNCATE TABLE {table}")
        except Exception:
            try:
                await db_exec(f"DELETE FROM {table}")
            except Exception as e:
                await setup_log(f"MySQL purge failed for {table}: {e}")
    await db_bootstrap()
    return True

async def db_bootstrap():
    if not POOL:
        return

    await db_exec("""
    CREATE TABLE IF NOT EXISTS users_permissions (
      user_id BIGINT PRIMARY KEY,
      level INT NOT NULL,
      note TEXT,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)

    await db_exec("""
    CREATE TABLE IF NOT EXISTS mirrors (
      mirror_id VARCHAR(64) PRIMARY KEY,
      source_guild BIGINT NOT NULL,
      source_channel BIGINT NOT NULL,
      target_channel BIGINT NOT NULL,
      enabled BOOLEAN NOT NULL DEFAULT TRUE,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)

    await db_exec("""
    CREATE TABLE IF NOT EXISTS mirror_rules (
      rule_id VARCHAR(96) PRIMARY KEY,
      scope VARCHAR(16) NOT NULL,
      source_guild BIGINT NOT NULL,
      source_id BIGINT NOT NULL,
      target_channel BIGINT NOT NULL,
      enabled BOOLEAN NOT NULL DEFAULT TRUE,
      fail_count INT NOT NULL DEFAULT 0,
      last_error TEXT,
      last_mirror_ts BIGINT,
      last_mirror_msg TEXT,
      last_disabled_at BIGINT NOT NULL DEFAULT 0,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)

    await db_exec("""
    CREATE TABLE IF NOT EXISTS mirror_messages (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      mirror_id VARCHAR(96) NOT NULL,
      src_guild BIGINT NOT NULL,
      src_channel BIGINT NOT NULL,
      src_msg BIGINT NOT NULL,
      dst_msg BIGINT NOT NULL,
      author_id BIGINT NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      INDEX (mirror_id),
      INDEX (dst_msg),
      INDEX (src_msg)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)

    await db_exec("""
    CREATE TABLE IF NOT EXISTS watchers (
      user_id BIGINT PRIMARY KEY,
      threshold INT NOT NULL,
      current INT NOT NULL DEFAULT 0,
      text TEXT NOT NULL,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)

    await db_exec("""
    CREATE TABLE IF NOT EXISTS dm_bridges (
      user_id BIGINT PRIMARY KEY,
      channel_id BIGINT NOT NULL,
      active BOOLEAN NOT NULL DEFAULT TRUE,
      last_activity BIGINT,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)

    await db_exec("""
    CREATE TABLE IF NOT EXISTS audit_logs (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      actor_id BIGINT NOT NULL,
      action TEXT NOT NULL,
      meta JSON,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)
    await db_calibrate()

    # seed SUPERUSER + AUTO_GOD safely
    await db_exec("""
    INSERT INTO users_permissions (user_id, level, note)
    VALUES (%s, %s, %s)
    ON DUPLICATE KEY UPDATE level=GREATEST(level, VALUES(level));
    """, (SUPER_USER_ID, 100, "Immutable SUPERUSER"))

    row = await db_one("SELECT user_id FROM users_permissions WHERE user_id=%s", (AUTO_GOD_ID,))
    if not row:
        await db_exec("""
        INSERT INTO users_permissions (user_id, level, note)
        VALUES (%s,%s,%s)
        """, (AUTO_GOD_ID, 90, "Auto-added GOD"))

# -----------------------------
# Logging
# -----------------------------
LOG_SUBSYSTEMS = {
    "system": "SYNAPTIC",
    "audit": "IMMUNE",
    "mirror": "SENSORY",
    "ai": "AI",
    "voice": "VOICE",
    "debug": "SYNAPTIC",
}
LOG_SEVERITY_DEFAULTS = {
    "system": "INFO",
    "audit": "INFO",
    "mirror": "INFO",
    "ai": "INFO",
    "voice": "INFO",
    "debug": "DEBUG",
}
LOG_DEDUP_WINDOW = 60
_LOG_DEDUP: Dict[str, float] = {}


def _log_now() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _clean_log_message(text: str) -> str:
    cleaned = " ".join(str(text or "").replace("**", "").replace("`", "").split())
    return cleaned


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _format_log_line(
    which: str,
    text: str,
    subsystem: Optional[str] = None,
    severity: Optional[str] = None,
    details: Optional[dict] = None,
) -> str:
    msg = _clean_log_message(text)
    sub = subsystem or LOG_SUBSYSTEMS.get(which, "SYSTEM")
    sev = severity or LOG_SEVERITY_DEFAULTS.get(which, "INFO")
    detail_text = ""
    if details:
        try:
            detail_text = " | " + _truncate_text(json.dumps(details, ensure_ascii=True), 240)
        except Exception:
            detail_text = ""
    return f"{_log_now()} | {sub} | {sev} | {_truncate_text(msg, 460)}{detail_text}"


def _should_emit_log(key: str, now_ts: int) -> bool:
    last = _LOG_DEDUP.get(key)
    if last and now_ts - last < LOG_DEDUP_WINDOW:
        return False
    _LOG_DEDUP[key] = now_ts
    return True


async def _resolve_log_channel(which: str) -> Optional[discord.TextChannel]:
    logs = cfg().get("logs", {})
    ch_id = logs.get(which)
    fallback_id = logs.get("system") or logs.get("debug")
    try_ids = [ch_id, fallback_id] if ch_id else [fallback_id]
    for target_id in try_ids:
        if not target_id:
            continue
        ch = bot.get_channel(int(target_id))
        if ch:
            return ch
        try:
            ch = await bot.fetch_channel(int(target_id))
            if ch:
                return ch
        except Exception:
            continue
    return None


async def log_to(
    which: str,
    text: str,
    subsystem: Optional[str] = None,
    severity: Optional[str] = None,
    details: Optional[dict] = None,
):
    line = _format_log_line(which, text, subsystem=subsystem, severity=severity, details=details)
    key_parts = [
        which,
        subsystem or LOG_SUBSYSTEMS.get(which, "SYSTEM"),
        severity or LOG_SEVERITY_DEFAULTS.get(which, "INFO"),
        _clean_log_message(text),
        json.dumps(details, ensure_ascii=True) if details else "",
    ]
    dedup_key = "|".join(key_parts)
    if not _should_emit_log(dedup_key, now_ts()):
        return
    ch = await _resolve_log_channel(which)
    if not ch:
        print(line)
        return
    try:
        await ch.send(line[:1900])
    except Exception:
        print(line)


async def audit(actor_id: int, action: str, meta: Optional[dict] = None):
    if POOL:
        try:
            await db_exec(
                "INSERT INTO audit_logs (actor_id, action, meta) VALUES (%s,%s,%s)",
                (actor_id, action, json.dumps(meta or {}, ensure_ascii=False))
            )
        except Exception:
            pass
    await log_to("audit", action, subsystem="IMMUNE", severity="INFO", details=meta)


async def debug(text: str):
    await log_to("debug", text, subsystem="SYNAPTIC", severity="DEBUG")


async def ensure_debug_channel() -> Optional[discord.TextChannel]:
    admin = bot.get_guild(ADMIN_GUILD_ID)
    if not admin:
        return None
    ch = discord.utils.get(admin.text_channels, name="debug-logs")
    if ch:
        return ch
    ch = discord.utils.get(admin.text_channels, name="debug")
    if ch:
        return ch
    try:
        cat = discord.utils.get(admin.categories, name="Engineering Core")
        if not cat:
            cat = await admin.create_category("Engineering Core")
            await setup_pause()
        ch = await admin.create_text_channel("debug-logs", category=cat)
        await setup_pause()
        return ch
    except Exception:
        try:
            ch = await admin.create_text_channel("debug-logs")
            await setup_pause()
            return ch
        except Exception:
            return None

async def setup_log(text: str):
    await log_to("system", text, subsystem="SYNAPTIC", severity="INFO")
    await log_to("debug", text, subsystem="SYNAPTIC", severity="DEBUG")
    ch = await ensure_debug_channel()
    if ch:
        try:
            await ch.send(text[:1900])
        except Exception:
            pass

# -----------------------------
# RBAC
# -----------------------------
def is_super(uid: int) -> bool:
    return uid == SUPER_USER_ID

async def get_user_level(uid: int) -> int:
    if uid == SUPER_USER_ID:
        return 100
    if uid == AUTO_GOD_ID:
        return 90

    # Prefer MySQL if enabled
    if POOL:
        row = await db_one("SELECT level FROM users_permissions WHERE user_id=%s", (uid,))
        if row:
            return int(row["level"])

    # fallback json
    return int(cfg().get("permissions", {}).get(str(uid), 0))

def role_level_map() -> Dict[str, int]:
    return cfg().get("rbac", {}).get("role_levels", {}) or {}

async def effective_level(member: discord.abc.User) -> int:
    lvl = await get_user_level(member.id)
    if isinstance(member, discord.Member):
        mp = role_level_map()
        max_role = 0
        for r in member.roles:
            max_role = max(max_role, int(mp.get(r.name, 0)))
        lvl = max(lvl, max_role)
    return lvl

async def require_level_ctx(ctx: commands.Context, min_level: int) -> bool:
    lvl = await effective_level(ctx.author)
    if lvl >= min_level:
        return True
    try:
        await ctx.message.delete()
    except Exception:
        pass
    return False

# -----------------------------
# Clean UX helpers
# -----------------------------
async def safe_delete(msg: discord.Message):
    try:
        await msg.delete()
    except Exception:
        pass

async def say_clean(ctx: commands.Context, content: str):
    # delete command message then post
    await safe_delete(ctx.message)
    return await ctx.send(content)

async def safe_ctx_send(ctx: commands.Context, content: str, delete_after: Optional[float] = None):
    try:
        return await ctx.send(content, delete_after=delete_after)
    except discord.NotFound:
        try:
            return await ctx.author.send(content)
        except Exception:
            return None
    except Exception:
        return None


async def temp_reply(ctx: commands.Context, content: str, ttl: Optional[int] = CLEANUP_RESPONSE_TTL):
    """Send a short-lived reply to keep channels cleaner."""
    return await safe_ctx_send(ctx, content, delete_after=ttl)

# -----------------------------
# Mandy AI tools
# -----------------------------
class ToolRegistry:
    def __init__(self, bot_ref: commands.Bot):
        self.bot = bot_ref
        self.dynamic_tools: Dict[str, Dict[str, Any]] = {}

    def register_dynamic_tool(self, name: str, meta: Dict[str, Any]) -> None:
        self.dynamic_tools[name] = meta

    def unregister_dynamic_tool(self, name: str) -> None:
        self.dynamic_tools.pop(name, None)

    def get_dynamic_tool(self, name: str) -> Optional[Dict[str, Any]]:
        return self.dynamic_tools.get(name)

    def list_dynamic_tools(self) -> List[str]:
        return sorted(self.dynamic_tools.keys())

    def _as_int(self, value: Any, name: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be int")
        try:
            return int(value)
        except Exception as exc:
            raise ValueError(f"{name} must be int") from exc

    def _as_text(self, value: Any, name: str, max_len: int) -> str:
        if value is None:
            raise ValueError(f"{name} must be str")
        text = str(value)
        if len(text) > max_len:
            text = text[:max_len]
        return text

    async def send_message(self, channel_id: int, text: str):
        ch_id = self._as_int(channel_id, "channel_id")
        content = self._as_text(text, "text", 1900).strip()
        if not content:
            raise ValueError("text cannot be empty")
        ch = self.bot.get_channel(ch_id)
        if not ch:
            try:
                ch = await self.bot.fetch_channel(ch_id)
            except Exception as exc:
                raise ValueError("channel not found") from exc
        if isinstance(ch, discord.TextChannel):
            perms = ch.permissions_for(ch.guild.me)
            if not perms.send_messages:
                raise ValueError("missing send_messages permission")
        try:
            msg = await ch.send(content)
            return {"message_id": msg.id}
        except Exception as exc:
            if "Forbidden" in str(exc) or "permission" in str(exc).lower():
                await request_elevation("send_message", f"missing permission for channel {ch_id}", {"channel_id": ch_id})
            raise ValueError(f"send failed: {exc}") from exc

    async def reply_to_message(self, channel_id: int, message_id: int, text: str):
        ch_id = self._as_int(channel_id, "channel_id")
        msg_id = self._as_int(message_id, "message_id")
        content = self._as_text(text, "text", 1900).strip()
        if not content:
            raise ValueError("text cannot be empty")
        ch = self.bot.get_channel(ch_id)
        if not ch:
            try:
                ch = await self.bot.fetch_channel(ch_id)
            except Exception as exc:
                raise ValueError("channel not found") from exc
        try:
            msg = await ch.fetch_message(msg_id)
        except Exception as exc:
            raise ValueError("message not found") from exc
        try:
            sent = await msg.reply(content)
            return {"message_id": sent.id}
        except Exception as exc:
            raise ValueError(f"reply failed: {exc}") from exc

    async def send_dm(self, user_id: int, text: str):
        uid = self._as_int(user_id, "user_id")
        content = self._as_text(text, "text", 1900).strip()
        if not content:
            raise ValueError("text cannot be empty")
        user = self.bot.get_user(uid)
        if not user:
            try:
                user = await self.bot.fetch_user(uid)
            except Exception as exc:
                raise ValueError("user not found") from exc
        try:
            msg = await user.send(content)
            return {"message_id": msg.id}
        except discord.Forbidden as exc:
            raise ValueError("user blocked DMs") from exc
        except Exception as exc:
            raise ValueError(f"dm failed: {exc}") from exc

    async def set_bot_status(self, state: str, text: str):
        st = self._as_text(state, "state", 16)
        msg = self._as_text(text, "text", 120)
        await set_bot_status(st, msg)
        return {"status": st, "text": msg}

    async def get_recent_transcript(self, channel_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        ch_id = self._as_int(channel_id, "channel_id")
        lim = max(1, min(80, self._as_int(limit, "limit")))
        ch = self.bot.get_channel(ch_id)
        if not ch:
            try:
                ch = await self.bot.fetch_channel(ch_id)
            except Exception as exc:
                raise ValueError("channel not found") from exc
        if isinstance(ch, discord.TextChannel):
            perms = ch.permissions_for(ch.guild.me)
            if not perms.view_channel or not perms.read_message_history:
                raise ValueError("missing read_message_history permission")
        messages: List[Dict[str, Any]] = []
        async for m in ch.history(limit=lim, oldest_first=False):
            if not m:
                continue
            content = (m.content or "").strip()
            messages.append({
                "id": m.id,
                "author_id": m.author.id,
                "author": str(m.author),
                "content": content,
                "created_at": m.created_at.isoformat() if m.created_at else ""
            })
        messages.reverse()
        return messages

    async def add_watcher(self, target_user_id: int, count: int, text: str, actor_id: int = 0):
        uid = self._as_int(target_user_id, "target_user_id")
        threshold = self._as_int(count, "count")
        msg = self._as_text(text or "", "text", 500)
        if threshold < 1:
            raise ValueError("count must be >= 1")
        cfg().setdefault("targets", {})[str(uid)] = {"count": threshold, "current": 0, "text": msg}
        await STORE.mark_dirty()
        if actor_id:
            await audit(actor_id, "Watcher set (json)", {"user_id": uid, "count": threshold})
        return "JSON watcher saved."

    async def remove_watcher(self, target_user_id: int, actor_id: int = 0):
        uid = self._as_int(target_user_id, "target_user_id")
        return await remove_watcher("json", uid, actor_id or 0)

    async def list_watchers(self) -> str:
        def fmt(uid, count, current, text):
            return f"{uid} (<@{uid}>) | count={count} current={current} text={truncate(text, 120)}"

        lines: List[str] = []
        targets = cfg().get("targets", {})
        for uid, data in targets.items():
            lines.append(fmt(uid, data.get("count", 0), data.get("current", 0), data.get("text", "")))

        if not lines:
            return "No JSON watchers found."

        header = f"JSON watchers ({len(lines)}):"
        return header + "\n" + "\n".join(lines[:50])
    async def list_mirror_rules(self) -> str:
        rules = list(mirror_rules_dict().values())
        if not rules:
            return "No mirror rules."
        lines: List[str] = []
        for r in rules[:50]:
            lines.append(f"{rule_summary(r)} ({'on' if r.get('enabled', True) else 'off'})")
        header = f"Mirror rules ({len(rules)}):"
        return header + "\n" + "\n".join(lines)

    async def create_mirror(self, source_channel_id: int, target_channel_id: int, actor_id: int = 0):
        src_id = self._as_int(source_channel_id, "source_channel_id")
        dst_id = self._as_int(target_channel_id, "target_channel_id")
        try:
            src_ch = self.bot.get_channel(src_id) or await self.bot.fetch_channel(src_id)
        except Exception as exc:
            raise ValueError("source channel not found") from exc
        if not isinstance(src_ch, discord.TextChannel):
            raise ValueError("source channel must be a text channel")
        try:
            dst_ch = self.bot.get_channel(dst_id) or await self.bot.fetch_channel(dst_id)
        except Exception as exc:
            raise ValueError("target channel not found") from exc
        if not isinstance(dst_ch, discord.TextChannel):
            raise ValueError("target channel must be a text channel")

        rule_id = make_rule_id("channel", src_id, dst_id)
        rule = {
            "rule_id": rule_id,
            "scope": "channel",
            "source_guild": src_ch.guild.id,
            "source_id": src_id,
            "target_channel": dst_id,
            "enabled": True,
            "fail_count": 0
        }
        await mirror_rule_save(rule)
        if actor_id:
            await audit(actor_id, "Mirror rule add (tool)", rule)
        return "Mirror rule added."

    async def disable_mirror_rule(self, rule_id: str, actor_id: int = 0):
        rid = self._as_text(rule_id, "rule_id", 96).strip()
        if not rid:
            raise ValueError("rule_id required")
        rule = mirror_rules_dict().get(rid)
        if not rule:
            raise ValueError("rule not found")
        await mirror_rule_disable(rule, "disabled via mandy")
        if actor_id:
            await audit(actor_id, "Mirror rule disabled", {"rule_id": rid})
        return "Mirror rule disabled."

    async def show_stats(self, scope: str, user_id: Optional[int] = None, guild_id: Optional[int] = None) -> str:
        window = normalize_stats_window(scope, "daily")
        if window not in ("daily", "weekly", "monthly", "yearly", "rolling24"):
            window = "daily"
        now_dt = datetime.datetime.utcnow()

        if user_id:
            uid = self._as_int(user_id, "user_id")
            if guild_id:
                gid = self._as_int(guild_id, "guild_id")
                guild = self.bot.get_guild(gid)
                if not guild:
                    raise ValueError("guild not found")
                entry, changed = chat_stats_get_user_entry(guild, window, uid, now_dt)
                if changed:
                    await STORE.mark_dirty()
                return (
                    f"User stats ({window}) for {uid} in {guild.name}: "
                    f"messages={int(entry.get('messages', 0))} words={int(entry.get('words', 0))} "
                    f"sentences={int(entry.get('sentences', 0))} top_words={format_top_words(entry)}"
                )

            total = default_user_stats(int(now_dt.timestamp()))
            for g in self.bot.guilds:
                entry, changed = chat_stats_get_user_entry(g, window, uid, now_dt)
                total["messages"] += int(entry.get("messages", 0))
                total["words"] += int(entry.get("words", 0))
                total["sentences"] += int(entry.get("sentences", 0))
                for w, c in (entry.get("word_freq", {}) or {}).items():
                    total["word_freq"][w] = int(total["word_freq"].get(w, 0)) + int(c)
                if changed:
                    await STORE.mark_dirty()
            return (
                f"User stats ({window}) for {uid}: "
                f"messages={total['messages']} words={total['words']} sentences={total['sentences']} "
                f"top_words={format_top_words(total)}"
            )

        totals, users, changed = aggregate_global_stats(window)
        if changed:
            await STORE.mark_dirty()
        top_users = sorted(
            ((int(uid), int(entry.get("messages", 0))) for uid, entry in users.items()),
            key=lambda row: row[1],
            reverse=True
        )[:5]
        top_lines = [f"{global_user_label(uid)} ({count})" for uid, count in top_users if count > 0]
        top_text = ", ".join(top_lines) if top_lines else "None"
        return (
            f"Global stats ({window}): messages={totals.get('messages', 0)} "
            f"words={totals.get('words', 0)} sentences={totals.get('sentences', 0)} "
            f"active_users={totals.get('active_users', 0)} top_users={top_text}"
        )

    async def list_capabilities(self) -> str:
        registry = getattr(self.bot, "mandy_registry", None) or CapabilityRegistry(self)
        ai = ai_cfg()
        installed = list(ai.get("installed_extensions", []) or [])
        loaded = sorted(self.bot.extensions.keys())
        for mod in loaded:
            if mod not in installed:
                installed.append(mod)
        queue = ai.get("queue", {}) or {}
        queue_counts = {"pending": 0, "waiting": 0, "running": 0}
        for job in queue.values():
            status = str(job.get("status", "pending"))
            if status in queue_counts:
                queue_counts[status] += 1
        runtime = getattr(self.bot, "mandy_runtime", {}) or {}
        counters = runtime.get("counters", {}) or {}

        lines: List[str] = []
        lines.append("Tools:")
        lines.append(registry.format_tools_summary(include_args=False))
        dynamic = registry._tool_registry.list_dynamic_tools() if registry else []
        lines.append(f"Plugin tools: {', '.join(dynamic) if dynamic else 'none'}")
        lines.append(f"Extensions: {', '.join(installed) if installed else 'none'}")
        lines.append(
            "Models: "
            f"default={ai.get('default_model')} "
            f"router={ai.get('router_model')} "
            f"tts={ai.get('tts_model') or 'none'}"
        )
        lines.append(
            "Queue: "
            f"total={len(queue)} pending={queue_counts['pending']} "
            f"waiting={queue_counts['waiting']} running={queue_counts['running']}"
        )
        if counters:
            counter_text = " ".join(f"{k}={v}" for k, v in sorted(counters.items()))
            lines.append(f"Counters: {counter_text}")
        return "\n".join(lines)

def attach_mandy_context():
    bot.mandy_tools = ToolRegistry(bot)
    bot.mandy_registry = CapabilityRegistry(bot.mandy_tools)
    bot.mandy_runtime = {"counters": {}, "last_actions": [], "last_rate_limit": None}
    bot.mandy_plugin_manager = ToolPluginManager(bot, bot.mandy_tools, log_to)
    bot.mandy_cfg = cfg
    bot.mandy_get_ai_config = ai_cfg
    bot.mandy_api_key = GEMINI_API_KEY
    bot.mandy_store = STORE
    bot.mandy_audit = audit
    bot.mandy_log_to = log_to
    bot.mandy_effective_level = effective_level
    bot.mandy_require_level_ctx = require_level_ctx

async def maybe_load_mandy_extension():
    global MANDY_LOADED
    if MANDY_LOADED:
        return
    try:
        await bot.load_extension(MANDY_EXTENSION)
        MANDY_LOADED = True
    except Exception as e:
        await debug(f"Mandy AI extension failed to load: {e}")

# -----------------------------
# Helpers
# -----------------------------
def now_ts() -> int:
    return int(time.time())

def fmt_ts(ts: int) -> str:
    if not ts:
        return "never"
    return f"<t:{int(ts)}:R>"

def truncate(text: str, limit: int = 180) -> str:
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."

def get_role(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    return discord.utils.get(guild.roles, name=name)

def admin_category_name(guild: discord.Guild) -> str:
    return f"04-servers / {guild.name}"

def mirror_rules_dict() -> Dict[str, Any]:
    return cfg().setdefault("mirror_rules", {})

async def watchers_report(limit: int = 50) -> List[str]:
    def fmt(uid, count, current, text):
        return f"{uid} (<@{uid}>) | count={count} current={current} text={truncate(text, 120)}"

    lim = max(1, min(200, int(limit)))
    chunks: List[str] = []

    json_lines: List[str] = []
    targets = cfg().get("targets", {})
    for uid, data in targets.items():
        json_lines.append(fmt(uid, data.get("count", 0), data.get("current", 0), data.get("text", "")))
    header = f"JSON watchers ({len(json_lines)})"
    chunks.extend(chunk_lines((json_lines[:lim] if json_lines else ["None"]), header))

    if POOL:
        mysql_lines: List[str] = []
        try:
            rows = await db_all(
                "SELECT user_id, threshold, current, text FROM watchers ORDER BY updated_at DESC LIMIT %s",
                (lim,),
            )
            for row in rows:
                mysql_lines.append(fmt(row["user_id"], row["threshold"], row.get("current", 0), row.get("text", "")))
        except Exception:
            mysql_lines.append("(failed to read MySQL watchers)")
        header_mysql = f"MySQL watchers ({len(mysql_lines)})"
        chunks.extend(chunk_lines(mysql_lines or ["None"], header_mysql))
    return chunks


async def _resolve_user_reference(ctx: commands.Context, text: str) -> Tuple[Optional[int], List[Tuple[int, str]]]:
    uid = parse_user_id(text)
    if uid:
        return uid, []
    token = (text or "").strip()
    if not token:
        return None, []
    candidates = rank_members_global(
        bot,
        token,
        prefer_guild_id=getattr(ctx.guild, "id", None),
        cache=GLOBAL_USER_RESOLVER,
        limit=6,
    )
    if not candidates:
        return None, []
    picked = pick_best(candidates, min_score=0.78, gap=0.05)
    if picked:
        return picked, []
    return None, [(cand.entity_id, cand.label) for cand in candidates[:5]]

def normalize_presence_state(state: str) -> str:
    s = (state or "").strip().lower()
    if s in ("online", "idle", "dnd", "invisible"):
        return s
    return "online"

def presence_activity(text: str) -> Optional[discord.Activity]:
    txt = (text or "").strip()
    if not txt:
        return None
    return discord.Activity(type=discord.ActivityType.playing, name=txt[:120])

async def apply_bot_status():
    st = cfg().get("bot_status", {})
    state = normalize_presence_state(st.get("state", "online"))
    text = str(st.get("text") or "")
    status_map = {
        "online": discord.Status.online,
        "idle": discord.Status.idle,
        "dnd": discord.Status.dnd,
        "invisible": discord.Status.invisible
    }
    await bot.change_presence(status=status_map.get(state, discord.Status.online), activity=presence_activity(text))

async def set_bot_status(state: str, text: str = ""):
    cfg()["bot_status"] = {"state": normalize_presence_state(state), "text": str(text or "")}
    await STORE.mark_dirty()
    await apply_bot_status()

def sentience_enabled() -> bool:
    return bool(sentience_cfg(cfg()).get("enabled", True))

def sentience_dialect() -> str:
    return str(sentience_cfg(cfg()).get("dialect") or "sentient_core")

def mirror_controls_enabled() -> bool:
    mirrors = cfg().get("mirrors", {}) if isinstance(cfg().get("mirrors", {}), dict) else {}
    return bool(mirrors.get("interactive_controls_enabled", True))

def presence_config() -> Dict[str, Any]:
    return presence_cfg(cfg())

def presence_bio() -> str:
    return str(presence_config().get("bio") or "").strip()

def autopresence_enabled() -> bool:
    return bool(presence_config().get("autopresence_enabled", False))

def update_presence_activity_ts(message_ts: int) -> None:
    presence_config()["last_message_ts"] = message_ts

def update_super_interaction_ts(message_ts: int) -> None:
    presence_config()["last_super_interaction_ts"] = message_ts

def _any_member_online() -> bool:
    if not bot or not bot.intents.presences:
        return False
    try:
        for guild in bot.guilds:
            for member in guild.members:
                if member.bot:
                    continue
                status = getattr(member, "status", None)
                if status and status != discord.Status.offline:
                    return True
    except Exception:
        return False
    return False

def _presence_target_state(now: int) -> str:
    presence = presence_config()
    last_msg = int(presence.get("last_message_ts", 0) or 0)
    last_super = int(presence.get("last_super_interaction_ts", 0) or 0)
    if last_msg and now - last_msg <= 300:
        return "online"
    if _any_member_online():
        return "idle"
    if last_super and now - last_super <= 120:
        return "dnd"
    return "invisible"

def daily_reflection_cfg() -> Dict[str, Any]:
    return sentience_cfg(cfg()).get("daily_reflection", {})

def daily_reflection_enabled() -> bool:
    return bool(daily_reflection_cfg().get("enabled", False))

def _daily_reflection_due(now_dt: datetime.datetime) -> bool:
    daily = daily_reflection_cfg()
    last_run = int(daily.get("last_run_utc", 0) or 0)
    hour = daily.get("hour_utc", None)
    if hour is None or str(hour).strip() == "":
        return now_dt.timestamp() - last_run >= 86400
    try:
        hour_int = max(0, min(23, int(hour)))
    except Exception:
        return now_dt.timestamp() - last_run >= 86400
    scheduled = datetime.datetime(now_dt.year, now_dt.month, now_dt.day, hour_int)
    return now_dt >= scheduled and last_run < int(scheduled.timestamp())

def sentience_channels_cfg() -> Dict[str, Any]:
    return sentience_cfg(cfg()).setdefault("channels", {})

def diagnostics_cfg() -> Dict[str, Any]:
    return cfg().setdefault("diagnostics", {})

def manual_cfg() -> Dict[str, Any]:
    return cfg().setdefault("manual", {})

async def _resolve_thoughts_channel() -> Optional[discord.TextChannel]:
    channels = sentience_channels_cfg()
    ch_id = int(channels.get("thoughts", 0) or 0)
    if ch_id:
        ch = bot.get_channel(ch_id)
        if not ch:
            try:
                ch = await bot.fetch_channel(ch_id)
            except Exception:
                ch = None
        if isinstance(ch, discord.TextChannel):
            return ch
    admin = bot.get_guild(ADMIN_GUILD_ID)
    if not admin:
        return None
    ch = find_text_by_name(admin, "thoughts")
    if isinstance(ch, discord.TextChannel):
        channels["thoughts"] = ch.id
        await STORE.mark_dirty()
        return ch
    return None

async def _resolve_diagnostics_channel() -> Optional[discord.TextChannel]:
    diag = diagnostics_cfg()
    ch_id = int(diag.get("channel_id", 0) or 0)
    if ch_id:
        ch = bot.get_channel(ch_id)
        if not ch:
            try:
                ch = await bot.fetch_channel(ch_id)
            except Exception:
                ch = None
        if isinstance(ch, discord.TextChannel):
            return ch
    admin = bot.get_guild(ADMIN_GUILD_ID)
    if not admin:
        return None
    ch = find_text_by_name(admin, "diagnostics")
    if isinstance(ch, discord.TextChannel):
        diag["channel_id"] = ch.id
        await STORE.mark_dirty()
        return ch
    return None

def _manual_path() -> str:
    return os.path.join("docs", "MANDY_MANUAL.md")

def _manual_hash() -> str:
    path = _manual_path()
    if not os.path.exists(path):
        return ""
    try:
        data = open(path, "rb").read()
    except Exception:
        return ""
    return hashlib.sha256(data).hexdigest()

async def _resolve_manual_channel() -> Optional[discord.TextChannel]:
    manual = manual_cfg()
    ch_id = int(manual.get("channel_id", 0) or 0)
    if ch_id:
        ch = bot.get_channel(ch_id)
        if not ch:
            try:
                ch = await bot.fetch_channel(ch_id)
            except Exception:
                ch = None
        if isinstance(ch, discord.TextChannel):
            return ch
    admin = bot.get_guild(ADMIN_GUILD_ID)
    if not admin:
        return None
    ch = find_text_by_name(admin, "manual-for-living")
    if isinstance(ch, discord.TextChannel):
        manual["channel_id"] = ch.id
        await STORE.mark_dirty()
        return ch
    return None

async def manual_upload_if_needed(force: bool = False) -> None:
    manual = manual_cfg()
    if not manual.get("auto_upload_enabled", False):
        return
    ch = await _resolve_manual_channel()
    if not ch:
        return
    current_hash = _manual_hash()
    if not current_hash:
        return
    if not force and manual.get("last_hash") == current_hash:
        return
    path = _manual_path()
    try:
        file = discord.File(path, filename="MANDY_MANUAL.md")
        msg = await ch.send("Mandy manual updated.", file=file)
        manual["last_hash"] = current_hash
        manual["last_message_id"] = msg.id
        manual["last_upload"] = now_ts()
        await STORE.mark_dirty()
    except Exception:
        return

def internal_monologue_cfg() -> Dict[str, Any]:
    return sentience_cfg(cfg()).get("internal_monologue", {})

def internal_monologue_enabled() -> bool:
    return bool(internal_monologue_cfg().get("enabled", False))

def _internal_monologue_due(now_ts_val: int) -> bool:
    monologue = internal_monologue_cfg()
    last_run = int(monologue.get("last_run_utc", 0) or 0)
    interval = float(monologue.get("interval_minutes", 180) or 180)
    return now_ts_val - last_run >= max(60, int(interval * 60))

async def _fetch_recent_log_lines(channel: discord.TextChannel, limit: int) -> List[str]:
    lines: List[str] = []
    try:
        async for msg in channel.history(limit=limit, oldest_first=False):
            if not msg or not msg.content:
                continue
            lines.append(msg.content.strip())
    except Exception:
        return []
    lines.reverse()
    return lines

async def _daily_reflection_context(max_messages: int) -> Dict[str, Any]:
    logs = cfg().get("logs", {})
    mirror_id = logs.get("mirror") or logs.get("system")
    channel = None
    if mirror_id:
        channel = bot.get_channel(int(mirror_id))
        if not channel:
            try:
                channel = await bot.fetch_channel(int(mirror_id))
            except Exception:
                channel = None
    log_lines: List[str] = []
    if isinstance(channel, discord.TextChannel):
        log_lines = await _fetch_recent_log_lines(channel, max_messages)

    ai = cfg().get("ai", {}) or {}
    queue = ai.get("queue", {}) or {}
    queue_counts = {"pending": 0, "waiting": 0, "running": 0}
    for job in queue.values():
        status = str(job.get("status", "pending"))
        if status in queue_counts:
            queue_counts[status] += 1

    rules = list(mirror_rules_dict().values())
    mirror_enabled = len([r for r in rules if r.get("enabled", True)])
    mirror_disabled = len(rules) - mirror_enabled
    watchers = len(cfg().get("targets", {}) or {})
    dm_bridges = await dm_bridge_list_active()

    return {
        "log_lines": log_lines,
        "ai_queue_total": len(queue),
        "ai_queue_counts": queue_counts,
        "mirror_rules_total": len(rules),
        "mirror_rules_enabled": mirror_enabled,
        "mirror_rules_disabled": mirror_disabled,
        "watchers": watchers,
        "dm_bridges_active": len(dm_bridges),
    }

async def _generate_daily_reflection(context: Dict[str, Any]) -> str:
    mandy = bot.get_cog("MandyAI")
    if not mandy or not getattr(mandy, "client", None):
        return ""
    client = mandy.client
    if not getattr(client, "available", False):
        return ""
    ai = ai_cfg()
    model = str(ai.get("router_model") or ai.get("default_model") or "gemini-2.5-flash-lite")
    log_lines = context.get("log_lines", [])
    log_excerpt = "\n".join(log_lines[-12:])
    system_prompt = (
        "You are Mandy's sentient core. Write a short Cognitive Reflection in a calm, analytical tone. "
        "Use biological metaphors blended with technical terms. Avoid emojis. Keep it concise."
    )
    user_prompt = (
        "Compose a daily Cognitive Reflection.\n"
        "Constraints:\n"
        "- 6 to 12 lines total.\n"
        "- Include 2 to 4 bullet metrics.\n"
        "- Include exactly one short observation line.\n"
        "- Include exactly one short recommendation for the operator.\n\n"
        "Context:\n"
        f"- Recent log excerpt:\n{log_excerpt or '(none)'}\n"
        f"- AI queue: total={context.get('ai_queue_total')} counts={context.get('ai_queue_counts')}\n"
        f"- Mirror rules: total={context.get('mirror_rules_total')} enabled={context.get('mirror_rules_enabled')} disabled={context.get('mirror_rules_disabled')}\n"
        f"- Watchers: {context.get('watchers')}\n"
        f"- DM bridges active: {context.get('dm_bridges_active')}\n"
    )
    try:
        text = await client.generate(system_prompt, user_prompt, model=model, response_format=None, timeout=60.0)
        return (text or "").strip()
    except Exception:
        return ""

def _build_fallback_reflection(context: Dict[str, Any]) -> str:
    queue_counts = context.get("ai_queue_counts", {})
    lines = [
        "Cognitive Reflection (fallback)",
        "Homeostasis stable; cortex remains responsive.",
        f"- Mirrors: total={context.get('mirror_rules_total')} enabled={context.get('mirror_rules_enabled')} disabled={context.get('mirror_rules_disabled')}",
        f"- AI queue: total={context.get('ai_queue_total')} pending={queue_counts.get('pending', 0)} waiting={queue_counts.get('waiting', 0)} running={queue_counts.get('running', 0)}",
        f"Observation: Visual feed integrity nominal with {context.get('dm_bridges_active')} active DM bridge(s).",
        "Recommendation: Review audit-memory for anomalies and keep synaptic-gap clear.",
    ]
    return "\n".join(lines)

def _task_state(task: Optional[tasks.Loop]) -> str:
    if not task:
        return "offline"
    try:
        if task.failed():
            return "error"
    except Exception:
        pass
    return "online" if task.is_running() else "offline"

def _diagnostic_status_lines(dm_bridge_count: int) -> List[str]:
    lines: List[str] = []
    mandy = bot.get_cog("MandyAI")
    ai_client = getattr(mandy, "client", None) if mandy else None
    ai_ok = bool(ai_client and getattr(ai_client, "available", False))
    runtime = getattr(bot, "mandy_runtime", {}) or {}
    last_rate = runtime.get("last_rate_limit", {})
    last_rate_text = "n/a"
    if last_rate:
        last_rate_text = f"{last_rate.get('source')} wait={last_rate.get('wait_seconds')}s"

    rules = list(mirror_rules_dict().values())
    mirror_enabled = len([r for r in rules if r.get("enabled", True)])
    mirror_disabled = len(rules) - mirror_enabled

    lines.append(f"Core: guilds={len(bot.guilds)} voice_clients={len(bot.voice_clients)} mysql={'on' if POOL else 'off'}")
    lines.append(f"Sentience: {'on' if sentience_enabled() else 'off'} dialect={sentience_dialect()}")
    lines.append(f"Presence: {'auto' if autopresence_enabled() else 'manual'} state={getattr(bot, 'status', 'unknown')}")
    ambient = ambient_engine.ambient_status()
    lines.append(f"Ambient: {'on' if ambient.get('enabled') else 'off'}")
    lines.append(f"Mirrors: total={len(rules)} enabled={mirror_enabled} disabled={mirror_disabled}")
    lines.append(f"Watchers: {len(cfg().get('targets', {}) or {})}")
    lines.append(f"DM bridges active: {dm_bridge_count}")
    lines.append(f"AI: {'online' if ai_ok else 'offline'} queue={len(cfg().get('ai', {}).get('queue', {}) or {})} last_rate={last_rate_text}")
    lines.append(f"Tasks: config_reload={_task_state(config_reload)} json_autosave={_task_state(json_autosave)} mirror_integrity={_task_state(mirror_integrity_check)}")
    lines.append(f"Tasks: server_status={_task_state(server_status_update)} dm_bridge_archive={_task_state(dm_bridge_archive)} presence={_task_state(presence_controller)}")
    lines.append(f"Tasks: daily_reflection={_task_state(daily_reflection_loop)} monologue={_task_state(internal_monologue_loop)} maintenance={_task_state(sentience_maintenance_loop)}")
    lines.append(f"Tasks: diagnostics={_task_state(diagnostics_loop)}")
    return lines

async def _generate_internal_monologue(context: Dict[str, Any]) -> str:
    mandy = bot.get_cog("MandyAI")
    if not mandy or not getattr(mandy, "client", None):
        return ""
    client = mandy.client
    if not getattr(client, "available", False):
        return ""
    ai = ai_cfg()
    model = str(ai.get("router_model") or ai.get("default_model") or "gemini-2.5-flash-lite")
    system_prompt = (
        "You are Mandy's sentient core. Write a terse internal monologue in a calm, analytical tone. "
        "Use biological metaphors blended with technical terms. Avoid emojis."
    )
    user_prompt = (
        "Write 1 to 4 short lines. No bullets. Keep it subtle and non-urgent.\n\n"
        f"Context: AI queue total={context.get('ai_queue_total')} | "
        f"mirrors enabled={context.get('mirror_rules_enabled')} disabled={context.get('mirror_rules_disabled')} | "
        f"watchers={context.get('watchers')} | dm_bridges={context.get('dm_bridges_active')}"
    )
    try:
        text = await client.generate(system_prompt, user_prompt, model=model, response_format=None, timeout=45.0)
        return (text or "").strip()
    except Exception:
        return ""

def auto_cfg() -> Dict[str, Any]:
    return cfg().get("auto", {})

def auto_setup_enabled() -> bool:
    return bool(auto_cfg().get("setup", True))

def auto_backfill_enabled() -> bool:
    return bool(auto_cfg().get("backfill", True))

def auto_backfill_limit() -> int:
    try:
        return int(auto_cfg().get("backfill_limit", 50))
    except Exception:
        return 50

def auto_backfill_per_channel() -> int:
    try:
        return int(auto_cfg().get("backfill_per_channel", 20))
    except Exception:
        return 20

def auto_backfill_delay() -> float:
    try:
        return float(auto_cfg().get("backfill_delay", 0.2))
    except Exception:
        return 0.2

def backfill_state() -> Dict[str, Any]:
    return cfg().setdefault("backfill_state", {})

CHAT_STATS_WINDOWS = ("daily", "rolling24", "weekly", "monthly", "yearly", "all")

def chat_stats_state() -> Dict[str, Any]:
    return cfg().setdefault("chat_stats", {})

def chat_stats_backfill_done() -> Dict[str, Any]:
    return cfg().setdefault("chat_stats_backfill_done", {})

def chat_stats_live_message() -> Dict[str, Any]:
    return cfg().setdefault("chat_stats_live_message", {})

def chat_stats_global_live_message() -> Dict[str, Any]:
    return cfg().setdefault("chat_stats_global_live_message", {})

def normalize_stats_window(window: Optional[str], default: str) -> str:
    w = (window or "").strip().lower()
    if w in ("today", "day", "daily"):
        w = "daily"
    if w in ("rolling_24h", "rolling24h", "rolling-24h"):
        w = "rolling24"
    if w in CHAT_STATS_WINDOWS:
        return w
    return default

def chat_stats_guild_state(guild_id: int) -> Dict[str, Any]:
    gstate = chat_stats_state().setdefault(str(guild_id), {})
    for w in CHAT_STATS_WINDOWS:
        gstate.setdefault(w, {})
    return gstate

def chat_stats_day_key(dt: Optional[datetime.datetime] = None) -> str:
    ts = dt or datetime.datetime.utcnow()
    if ts.tzinfo is not None:
        ts = ts.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return ts.date().isoformat()

def window_key_for_dt(window: str, dt: datetime.datetime) -> str:
    if window == "daily":
        return dt.date().isoformat()
    if window == "weekly":
        iso = dt.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if window == "monthly":
        return f"{dt.year}-{dt.month:02d}"
    if window == "yearly":
        return str(dt.year)
    if window == "all":
        return "all"
    return "rolling24"

def count_words(text: str) -> int:
    if not text:
        return 0
    return len(re.findall(r"\b\w+\b", text))

def count_sentences(text: str) -> int:
    if not text:
        return 0
    return len(re.findall(r"[.!?]", text))

def normalize_words(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r"[A-Za-z0-9]+", text.lower())

def trim_word_freq(freq: Dict[str, int], limit: int = 200) -> None:
    if len(freq) <= limit:
        return
    items = sorted(freq.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[:limit]
    freq.clear()
    freq.update({k: v for k, v in items})

def guild_user_label(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(user_id)
    if member:
        return member.display_name
    user = bot.get_user(user_id)
    if user:
        return str(user)
    return str(user_id)

def default_user_stats(now_ts: int) -> Dict[str, Any]:
    return {
        "messages": 0,
        "words": 0,
        "sentences": 0,
        "word_freq": {},
        "last_reset": now_ts
    }

def window_needs_reset(window: str, entry: Dict[str, Any], now_dt: datetime.datetime) -> bool:
    if window in ("rolling24", "all"):
        return False
    last_reset = int(entry.get("last_reset", 0))
    if not last_reset:
        return True
    last_dt = datetime.datetime.utcfromtimestamp(last_reset)
    return window_key_for_dt(window, last_dt) != window_key_for_dt(window, now_dt)

def rolling24_bucket_ts(dt: datetime.datetime) -> int:
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    dt = dt.replace(minute=0, second=0, microsecond=0)
    return int(dt.timestamp())

def rolling24_prune_and_recompute(entry: Dict[str, Any], now_ts: int) -> bool:
    buckets = entry.setdefault("buckets", {})
    cutoff = now_ts - 86400
    changed = False
    for k in list(buckets.keys()):
        try:
            ts_key = int(k)
        except Exception:
            ts_key = 0
        if ts_key < cutoff:
            buckets.pop(k, None)
            changed = True

    total_messages = 0
    total_words = 0
    total_sentences = 0
    agg_freq: Dict[str, int] = {}
    for bucket in buckets.values():
        total_messages += int(bucket.get("messages", 0))
        total_words += int(bucket.get("words", 0))
        total_sentences += int(bucket.get("sentences", 0))
        for w, c in (bucket.get("word_freq", {}) or {}).items():
            agg_freq[w] = agg_freq.get(w, 0) + int(c)
    trim_word_freq(agg_freq, 200)

    if int(entry.get("messages", 0)) != total_messages:
        entry["messages"] = total_messages
        changed = True
    if int(entry.get("words", 0)) != total_words:
        entry["words"] = total_words
        changed = True
    if int(entry.get("sentences", 0)) != total_sentences:
        entry["sentences"] = total_sentences
        changed = True
    if entry.get("word_freq", {}) != agg_freq:
        entry["word_freq"] = agg_freq
        changed = True
    if not entry.get("last_reset"):
        entry["last_reset"] = now_ts
        changed = True
    return changed

def update_word_freq(freq: Dict[str, int], words: List[str]) -> None:
    for w in words:
        freq[w] = int(freq.get(w, 0)) + 1
    trim_word_freq(freq, 200)

async def chat_stats_increment(message: discord.Message, mark_dirty: bool = True):
    if not message.guild:
        return
    if message.author.bot:
        return

    now_dt = message.created_at or datetime.datetime.utcnow()
    if now_dt.tzinfo is not None:
        now_dt = now_dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    now_ts = int(now_dt.timestamp())
    text = message.content or ""
    words = normalize_words(text)
    word_count = count_words(text)
    sentence_count = count_sentences(text)

    gstate = chat_stats_guild_state(message.guild.id)
    uid = str(message.author.id)
    changed = False

    for window in CHAT_STATS_WINDOWS:
        window_state = gstate.setdefault(window, {})
        entry = window_state.setdefault(uid, default_user_stats(now_ts))

        if window == "rolling24":
            buckets = entry.setdefault("buckets", {})
            bucket_ts = rolling24_bucket_ts(now_dt)
            bucket = buckets.setdefault(str(bucket_ts), {"messages": 0, "words": 0, "sentences": 0, "word_freq": {}})
            bucket["messages"] = int(bucket.get("messages", 0)) + 1
            bucket["words"] = int(bucket.get("words", 0)) + word_count
            bucket["sentences"] = int(bucket.get("sentences", 0)) + sentence_count
            update_word_freq(bucket.setdefault("word_freq", {}), words)
            trim_word_freq(bucket["word_freq"], 200)
            if rolling24_prune_and_recompute(entry, now_ts):
                changed = True
            continue

        if window_needs_reset(window, entry, now_dt):
            entry.update(default_user_stats(now_ts))
            entry.pop("buckets", None)
            changed = True

        entry["messages"] = int(entry.get("messages", 0)) + 1
        entry["words"] = int(entry.get("words", 0)) + word_count
        entry["sentences"] = int(entry.get("sentences", 0)) + sentence_count
        update_word_freq(entry.setdefault("word_freq", {}), words)
        changed = True

    if mark_dirty and changed:
        await STORE.mark_dirty()

def chat_stats_prune_rolling24_window(window_state: Dict[str, Any], now_ts: int) -> bool:
    changed = False
    for entry in window_state.values():
        if rolling24_prune_and_recompute(entry, now_ts):
            changed = True
    return changed

def chat_stats_refresh_window(window_state: Dict[str, Any], window: str, now_dt: datetime.datetime) -> bool:
    if window in ("rolling24", "all"):
        return False
    changed = False
    now_ts = int(now_dt.timestamp())
    for entry in window_state.values():
        if window_needs_reset(window, entry, now_dt):
            entry.update(default_user_stats(now_ts))
            entry.pop("buckets", None)
            changed = True
    return changed

def chat_stats_get_user_entry(guild: discord.Guild, window: str, user_id: int, now_dt: datetime.datetime) -> Tuple[Dict[str, Any], bool]:
    gstate = chat_stats_guild_state(guild.id)
    window_state = gstate.setdefault(window, {})
    uid = str(user_id)
    entry = window_state.get(uid)
    changed = False

    if entry is None:
        entry = default_user_stats(int(now_dt.timestamp()))
        if window == "rolling24":
            entry.setdefault("buckets", {})
        window_state[uid] = entry
        changed = True

    if window == "rolling24":
        if rolling24_prune_and_recompute(entry, int(now_dt.timestamp())):
            changed = True
        return entry, changed

    if window_needs_reset(window, entry, now_dt):
        entry.update(default_user_stats(int(now_dt.timestamp())))
        entry.pop("buckets", None)
        changed = True

    return entry, changed

def chat_stats_user_top_words(entry: Dict[str, Any], limit: int = 3) -> List[Tuple[str, int]]:
    freq = entry.get("word_freq", {}) or {}
    items = sorted(freq.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[:limit]
    return [(w, int(c)) for w, c in items]

def format_top_words(entry: Dict[str, Any], limit: int = 3) -> str:
    items = chat_stats_user_top_words(entry, limit=limit)
    if not items:
        return "None"
    parts = [f"{w} ({c})" for w, c in items]
    return ", ".join(parts)

def chat_stats_window_state(guild: discord.Guild, window: str) -> Dict[str, Any]:
    gstate = chat_stats_guild_state(guild.id)
    return gstate.setdefault(window, {})

async def chat_stats_build_live_embed(guild: discord.Guild, window: str) -> Tuple[discord.Embed, bool]:
    now_dt = datetime.datetime.utcnow()
    now_ts = int(now_dt.timestamp())
    window_state = chat_stats_window_state(guild, window)
    changed = False
    if window == "rolling24":
        if chat_stats_prune_rolling24_window(window_state, now_ts):
            changed = True
    else:
        if chat_stats_refresh_window(window_state, window, now_dt):
            changed = True

    total_messages = 0
    total_words = 0
    active_users = 0
    for entry in window_state.values():
        msg_count = int(entry.get("messages", 0))
        if msg_count > 0:
            active_users += 1
        total_messages += msg_count
        total_words += int(entry.get("words", 0))

    top_users = sorted(
        ((int(uid), int(e.get("messages", 0))) for uid, e in window_state.items() if int(e.get("messages", 0)) > 0),
        key=lambda row: row[1],
        reverse=True
    )[:5]
    if top_users:
        top_lines = [f"{guild_user_label(guild, uid)} ({count})" for uid, count in top_users if count > 0]
        top_text = "\n".join(top_lines) if top_lines else "None"
    else:
        top_text = "None"

    emb = discord.Embed(
        title=f"Live Stats ({window})",
        description=f"{guild.name} (`{guild.id}`)",
        color=discord.Color.dark_gray()
    )
    emb.add_field(name="Messages", value=str(total_messages), inline=True)
    emb.add_field(name="Words", value=str(total_words), inline=True)
    emb.add_field(name="Active users", value=str(active_users), inline=True)
    emb.add_field(name="Top users", value=top_text[:1024], inline=False)
    emb.set_footer(text="Mandy OS")
    return emb, changed

async def stop_live_stats_panel(guild_id: int, delete_message: bool = True):
    task = LIVE_STATS_TASKS.pop(guild_id, None)
    if task:
        task.cancel()
    info = chat_stats_live_message().get(str(guild_id))
    if not info:
        return
    ch_id = int(info.get("channel_id", 0))
    msg_id = int(info.get("message_id", 0))
    if ch_id and msg_id:
        ch = bot.get_channel(ch_id)
        if not ch:
            try:
                ch = await bot.fetch_channel(ch_id)
            except Exception:
                ch = None
        if isinstance(ch, discord.TextChannel):
            try:
                msg = await ch.fetch_message(msg_id)
                if delete_message:
                    await msg.delete()
                else:
                    try:
                        await msg.unpin()
                    except Exception:
                        pass
            except Exception:
                pass
    chat_stats_live_message().pop(str(guild_id), None)
    await STORE.mark_dirty()

async def live_stats_loop(guild_id: int, channel_id: int, message_id: int, window: str):
    try:
        while True:
            await asyncio.sleep(10)
            guild = bot.get_guild(guild_id)
            if not guild:
                break
            ch = bot.get_channel(channel_id)
            if not ch:
                try:
                    ch = await bot.fetch_channel(channel_id)
                except Exception:
                    break
            if not isinstance(ch, discord.TextChannel):
                break
            try:
                msg = await ch.fetch_message(message_id)
            except Exception:
                break
            emb, changed = await chat_stats_build_live_embed(guild, window)
            try:
                await msg.edit(embed=emb)
            except Exception:
                break
            if changed:
                await STORE.mark_dirty()
    finally:
        if chat_stats_live_message().get(str(guild_id), {}).get("message_id") == message_id:
            chat_stats_live_message().pop(str(guild_id), None)
            await STORE.mark_dirty()
        LIVE_STATS_TASKS.pop(guild_id, None)

async def resume_live_stats_panels():
    for gid_str, info in list(chat_stats_live_message().items()):
        try:
            gid = int(gid_str)
        except Exception:
            continue
        window = normalize_stats_window(info.get("window"), "rolling24")
        ch_id = int(info.get("channel_id", 0))
        msg_id = int(info.get("message_id", 0))
        if not ch_id or not msg_id:
            continue
        if gid in LIVE_STATS_TASKS:
            continue
        LIVE_STATS_TASKS[gid] = asyncio.create_task(live_stats_loop(gid, ch_id, msg_id, window))

def global_user_label(user_id: int) -> str:
    user = bot.get_user(user_id)
    if user:
        return str(user)
    for g in bot.guilds:
        member = g.get_member(user_id)
        if member:
            return member.display_name
    return str(user_id)

def aggregate_global_stats(window: str) -> Tuple[Dict[str, int], Dict[str, Any], bool]:
    now_dt = datetime.datetime.utcnow()
    now_ts = int(now_dt.timestamp())
    users: Dict[str, Dict[str, Any]] = {}
    changed = False

    for g in bot.guilds:
        window_state = chat_stats_window_state(g, window)
        if window == "rolling24":
            if chat_stats_prune_rolling24_window(window_state, now_ts):
                changed = True
        else:
            if chat_stats_refresh_window(window_state, window, now_dt):
                changed = True
        for uid, entry in window_state.items():
            msg_count = int(entry.get("messages", 0))
            word_count = int(entry.get("words", 0))
            sentence_count = int(entry.get("sentences", 0))
            if msg_count <= 0 and word_count <= 0 and sentence_count <= 0:
                continue
            merged = users.setdefault(uid, {"messages": 0, "words": 0, "sentences": 0, "word_freq": {}})
            merged["messages"] = int(merged.get("messages", 0)) + msg_count
            merged["words"] = int(merged.get("words", 0)) + word_count
            merged["sentences"] = int(merged.get("sentences", 0)) + sentence_count
            freq = merged.setdefault("word_freq", {})
            for w, c in (entry.get("word_freq", {}) or {}).items():
                freq[w] = int(freq.get(w, 0)) + int(c)

    total_messages = 0
    total_words = 0
    total_sentences = 0
    active_users = 0
    for entry in users.values():
        msgs = int(entry.get("messages", 0))
        total_messages += msgs
        total_words += int(entry.get("words", 0))
        total_sentences += int(entry.get("sentences", 0))
        if msgs > 0:
            active_users += 1
        trim_word_freq(entry.get("word_freq", {}), 200)

    totals = {
        "guilds": len(bot.guilds),
        "messages": total_messages,
        "words": total_words,
        "sentences": total_sentences,
        "active_users": active_users
    }
    return totals, users, changed

async def chat_stats_build_global_embed(window: str) -> Tuple[discord.Embed, bool]:
    totals, users, changed = aggregate_global_stats(window)
    rows = sorted(
        ((int(uid), int(entry.get("messages", 0)), entry) for uid, entry in users.items()),
        key=lambda row: row[1],
        reverse=True
    )[:10]
    lines = []
    for uid, msg_count, entry in rows:
        if msg_count <= 0:
            continue
        name = global_user_label(uid)
        top_words = format_top_words(entry)
        lines.append(f"{name} - {msg_count} - {top_words}")
    top_text = "\n".join(lines) if lines else "No data."
    if len(top_text) > 1024:
        top_text = top_text[:1021] + "..."

    emb = discord.Embed(
        title=f"🌐 Global Stats ({window})",
        color=discord.Color.dark_gray()
    )
    emb.add_field(name="Total Servers", value=str(totals.get("guilds", 0)), inline=True)
    emb.add_field(name="Total Messages", value=str(totals.get("messages", 0)), inline=True)
    emb.add_field(name="Total Words", value=str(totals.get("words", 0)), inline=True)
    emb.add_field(name="Active Users", value=str(totals.get("active_users", 0)), inline=True)
    emb.add_field(name="Top Users", value=top_text, inline=False)
    emb.set_footer(text="Mandy OS")
    return emb, changed

async def stop_global_live_panel(delete_message: bool = True):
    task = LIVE_STATS_TASKS.pop("GLOBAL", None)
    if task:
        task.cancel()
    info = chat_stats_global_live_message()
    if not info:
        return
    ch_id = int(info.get("channel_id", 0))
    msg_id = int(info.get("message_id", 0))
    if ch_id and msg_id:
        ch = bot.get_channel(ch_id)
        if not ch:
            try:
                ch = await bot.fetch_channel(ch_id)
            except Exception:
                ch = None
        if isinstance(ch, discord.TextChannel):
            try:
                msg = await ch.fetch_message(msg_id)
                if delete_message:
                    await msg.delete()
                else:
                    try:
                        await msg.unpin()
                    except Exception:
                        pass
            except Exception:
                pass
    info.clear()
    await STORE.mark_dirty()

async def global_live_stats_loop(channel_id: int, message_id: int, window: str):
    try:
        while True:
            await asyncio.sleep(10)
            ch = bot.get_channel(channel_id)
            if not ch:
                try:
                    ch = await bot.fetch_channel(channel_id)
                except Exception:
                    break
            if not isinstance(ch, discord.TextChannel):
                break
            try:
                msg = await ch.fetch_message(message_id)
            except Exception:
                break
            emb, changed = await chat_stats_build_global_embed(window)
            try:
                await msg.edit(embed=emb)
            except Exception:
                break
            if changed:
                await STORE.mark_dirty()
    finally:
        info = chat_stats_global_live_message()
        if info.get("message_id") == message_id:
            info.clear()
            await STORE.mark_dirty()
        LIVE_STATS_TASKS.pop("GLOBAL", None)

async def resume_global_live_panel():
    info = chat_stats_global_live_message()
    if not info:
        return
    ch_id = int(info.get("channel_id", 0))
    msg_id = int(info.get("message_id", 0))
    if not ch_id or not msg_id:
        return
    window = normalize_stats_window(info.get("window"), "rolling24")
    if "GLOBAL" in LIVE_STATS_TASKS:
        return
    LIVE_STATS_TASKS["GLOBAL"] = asyncio.create_task(
        global_live_stats_loop(ch_id, msg_id, window)
    )

def setup_delay() -> float:
    try:
        return max(0.0, float(cfg().get("tuning", {}).get("setup_delay", 1.0)))
    except Exception:
        return 1.0

async def setup_pause():
    delay = setup_delay()
    if delay > 0:
        await asyncio.sleep(delay)

# -----------------------------
# Watchers (your JSON targets) + optional MySQL sync
# -----------------------------
async def watcher_tick(message: discord.Message):
    if message.author.bot:
        return

    uid = str(message.author.id)

    # 1) JSON targets (your format)
    targets = cfg().get("targets", {})
    if uid in targets:
        t = targets[uid]
        t["current"] = int(t.get("current", 0)) + 1

        if t["current"] >= int(t.get("count", 0)):
            t["current"] = 0
            replies = [x.strip() for x in str(t.get("text", "")).split("|") if x.strip()]
            if replies:
                try:
                    await message.reply(random.choice(replies))
                except Exception:
                    pass
        await STORE.mark_dirty()

    # 2) Optional MySQL watcher mirror (safe): if a user has a row in watchers table
    if POOL:
        row = await db_one("SELECT threshold, current, text FROM watchers WHERE user_id=%s", (message.author.id,))
        if row:
            cur = int(row["current"]) + 1
            thr = int(row["threshold"])
            text = str(row["text"] or "")
            if cur >= thr:
                cur = 0
                replies = [x.strip() for x in text.split("|") if x.strip()]
                if replies:
                    try:
                        await message.reply(random.choice(replies))
                    except Exception:
                        pass
            await db_exec("UPDATE watchers SET current=%s WHERE user_id=%s", (cur, message.author.id))

# -----------------------------
# Mirror: unified rules + Buttons (Reply/Post/DM)
# -----------------------------
def normalize_scope(scope: str) -> str:
    s = (scope or "").strip().lower()
    if s in ("server", "category", "channel"):
        return s
    return "channel"

def make_rule_id(scope: str, source_id: int, target_channel: int) -> str:
    return f"{normalize_scope(scope)}:{int(source_id)}:{int(target_channel)}"

def normalize_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    rule["scope"] = normalize_scope(rule.get("scope", "channel"))
    rule["source_guild"] = int(rule.get("source_guild", 0))
    rule["source_id"] = int(rule.get("source_id", 0))
    rule["target_channel"] = int(rule.get("target_channel", 0))
    rule["enabled"] = bool(rule.get("enabled", True))
    rule["fail_count"] = int(rule.get("fail_count", 0))
    rule["last_error"] = str(rule.get("last_error") or "")
    rule["last_mirror_ts"] = int(rule.get("last_mirror_ts") or 0)
    rule["last_mirror_msg"] = str(rule.get("last_mirror_msg") or "")
    rule["last_disabled_at"] = int(rule.get("last_disabled_at") or 0)
    return rule

def rule_summary(rule: Dict[str, Any]) -> str:
    scope = rule.get("scope", "channel")
    src_id = int(rule.get("source_id", 0))
    tgt_id = int(rule.get("target_channel", 0))
    src_label = str(src_id)
    tgt_label = str(tgt_id)

    if scope == "server":
        gid = int(rule.get("source_guild", 0) or src_id)
        g = bot.get_guild(gid)
        if g:
            src_label = g.name
    elif scope == "category":
        cat = bot.get_channel(src_id)
        if isinstance(cat, discord.CategoryChannel):
            src_label = f"{cat.guild.name}/{cat.name}"
    else:
        ch = bot.get_channel(src_id)
        if isinstance(ch, discord.TextChannel):
            src_label = f"{ch.guild.name}/#{ch.name}"

    dst = bot.get_channel(tgt_id)
    if isinstance(dst, discord.TextChannel):
        tgt_label = f"{dst.guild.name}/#{dst.name}"

    return f"{scope} {src_label} -> {tgt_label}"

async def mirror_rule_save_db(rule: Dict[str, Any]):
    if not POOL:
        return
    await db_exec("""
    INSERT INTO mirror_rules
      (rule_id, scope, source_guild, source_id, target_channel, enabled, fail_count, last_error, last_mirror_ts, last_mirror_msg, last_disabled_at)
    VALUES
      (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE
      scope=VALUES(scope),
      source_guild=VALUES(source_guild),
      source_id=VALUES(source_id),
      target_channel=VALUES(target_channel),
      enabled=VALUES(enabled),
      fail_count=VALUES(fail_count),
      last_error=VALUES(last_error),
      last_mirror_ts=VALUES(last_mirror_ts),
      last_mirror_msg=VALUES(last_mirror_msg),
      last_disabled_at=VALUES(last_disabled_at);
    """, (
        rule["rule_id"],
        rule["scope"],
        rule["source_guild"],
        rule["source_id"],
        rule["target_channel"],
        1 if rule.get("enabled", True) else 0,
        int(rule.get("fail_count", 0)),
        rule.get("last_error", ""),
        int(rule.get("last_mirror_ts", 0)),
        rule.get("last_mirror_msg", ""),
        int(rule.get("last_disabled_at", 0) or 0),
    ))

async def mirror_rule_save(rule: Dict[str, Any]):
    rule = normalize_rule(rule)
    rules = mirror_rules_dict()
    rules[rule["rule_id"]] = rule
    await STORE.mark_dirty()
    if POOL:
        await mirror_rule_save_db(rule)

async def mirror_rule_update(rule: Dict[str, Any], **fields):
    updated = dict(rule)
    updated.update(fields)
    await mirror_rule_save(updated)
    return updated

async def mirror_rule_disable(rule: Dict[str, Any], reason: str):
    await mirror_rule_update(
        rule,
        enabled=False,
        last_error=reason,
        fail_count=int(rule.get("fail_count", 0)) + 1,
        last_disabled_at=now_ts(),
    )
    await log_to("mirror", f"dY¦z Mirror rule disabled: {rule_summary(rule)} ({reason})")
    if rule.get("scope") == "server":
        guild = bot.get_guild(int(rule.get("source_guild", 0)))
        if guild:
            await update_server_info_for_guild(guild)

async def mirror_rule_record_failure(rule: Dict[str, Any], error: str):
    threshold = int(cfg().get("mirror_fail_threshold", MIRROR_FAIL_THRESHOLD))
    fail_count = int(rule.get("fail_count", 0)) + 1
    if fail_count >= threshold:
        await mirror_rule_disable(rule, error)
        return
    await mirror_rule_update(rule, fail_count=fail_count, last_error=error)
    await log_to("mirror", f"dY¦z Mirror fail ({fail_count}/{threshold}): {rule_summary(rule)} -> {error}")


async def mirror_rule_delete(rule: Dict[str, Any], reason: str):
    rid = rule.get("rule_id")
    if not rid:
        return
    rules = mirror_rules_dict()
    rules.pop(rid, None)
    await STORE.mark_dirty()
    if POOL:
        try:
            await db_exec("DELETE FROM mirror_rules WHERE rule_id=%s", (rid,))
        except Exception:
            pass
    await log_to("mirror", f"dY?z Mirror rule deleted: {rule_summary(rule)} ({reason})")

async def mirror_rule_mark_success(rule: Dict[str, Any], last_msg: str):
    await mirror_rule_update(
        rule,
        fail_count=0,
        last_error="",
        last_mirror_ts=now_ts(),
        last_mirror_msg=truncate(last_msg, 180)
    )
    if rule.get("enabled") is False:
        await mirror_rule_update(rule, enabled=True, last_disabled_at=0)

async def mirror_rules_sync():
    if not POOL:
        return
    rules = mirror_rules_dict()
    db_rules = await db_all("SELECT * FROM mirror_rules")
    db_ids = set()
    for row in db_rules:
        rid = row.get("rule_id")
        if not rid:
            continue
        db_ids.add(rid)
        rules[rid] = normalize_rule({
            "rule_id": rid,
            "scope": row.get("scope"),
            "source_guild": row.get("source_guild"),
            "source_id": row.get("source_id"),
            "target_channel": row.get("target_channel"),
            "enabled": bool(row.get("enabled")),
            "fail_count": row.get("fail_count") or 0,
            "last_error": row.get("last_error") or "",
            "last_mirror_ts": row.get("last_mirror_ts") or 0,
            "last_mirror_msg": row.get("last_mirror_msg") or "",
            "last_disabled_at": row.get("last_disabled_at") or 0
        })
    for rid, rule in rules.items():
        if rid not in db_ids:
            await mirror_rule_save_db(rule)
    await STORE.mark_dirty()

def find_server_scope_rule(guild_id: int) -> Optional[Dict[str, Any]]:
    fallback = None
    for rule in mirror_rules_dict().values():
        if rule.get("scope") != "server":
            continue
        if int(rule.get("source_guild", 0)) != guild_id:
            continue
        if rule.get("enabled", True):
            return rule
        if fallback is None:
            fallback = rule
    return fallback

def find_category_rule(category_id: int, target_channel: int) -> Optional[Dict[str, Any]]:
    for rule in mirror_rules_dict().values():
        if (
            rule.get("scope") == "category"
            and int(rule.get("source_id", 0)) == category_id
            and int(rule.get("target_channel", 0)) == target_channel
        ):
            return rule
    return None

async def ensure_server_mirror_rule(guild: discord.Guild) -> Optional[Dict[str, Any]]:
    if guild.id == ADMIN_GUILD_ID:
        return None
    mirror_feed, _ = await ensure_admin_server_channels(guild)
    if not mirror_feed:
        return None
    existing = find_server_scope_rule(guild.id)
    if existing:
        target_id = int(existing.get("target_channel", 0))
        target = bot.get_channel(target_id)
        if not target and target_id:
            try:
                target = await bot.fetch_channel(target_id)
            except Exception:
                target = None
        if not isinstance(target, discord.TextChannel) or target.guild.id != ADMIN_GUILD_ID:
            new_rule_id = make_rule_id("server", guild.id, mirror_feed.id)
            if existing.get("rule_id") != new_rule_id:
                try:
                    await mirror_rule_update(
                        existing,
                        enabled=False,
                        last_error="replaced by new mirror feed",
                        fail_count=0
                    )
                except Exception:
                    pass
            new_rule = {
                "rule_id": new_rule_id,
                "scope": "server",
                "source_guild": guild.id,
                "source_id": guild.id,
                "target_channel": mirror_feed.id,
                "enabled": True,
                "fail_count": 0,
                "last_error": ""
            }
            await mirror_rule_save(new_rule)
            await setup_pause()
            return new_rule
        last_err = str(existing.get("last_error") or "").lower()
        if last_err and "target" in last_err:
            await mirror_rule_update(existing, last_error="", fail_count=0)
            await setup_pause()
        if not existing.get("enabled", False):
            await mirror_rule_update(existing, enabled=True)
            await setup_pause()
        return existing
    rule = {
        "rule_id": make_rule_id("server", guild.id, mirror_feed.id),
        "scope": "server",
        "source_guild": guild.id,
        "source_id": guild.id,
        "target_channel": mirror_feed.id,
        "enabled": True,
        "fail_count": 0
    }
    await mirror_rule_save(rule)
    await setup_pause()
    return rule

async def backfill_mirror_for_guild(guild: discord.Guild, rule: Dict[str, Any], force: bool = False):
    if not guild or not rule or not rule.get("enabled", True):
        return
    state = backfill_state()
    done = state.setdefault("done", {})
    if not force and done.get(str(guild.id)):
        return

    limit = auto_backfill_limit()
    per_channel = auto_backfill_per_channel()
    delay = auto_backfill_delay()
    if limit <= 0 or per_channel <= 0:
        return

    me = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
    if not me and bot.user:
        try:
            me = await guild.fetch_member(bot.user.id)
        except Exception:
            me = None
    if not me:
        return

    messages: List[discord.Message] = []
    scanned = 0
    skipped = 0
    for ch in guild.text_channels:
        perms = ch.permissions_for(me)
        if not perms.read_message_history or not perms.read_messages:
            skipped += 1
            continue
        scanned += 1
        try:
            async for m in ch.history(limit=per_channel, oldest_first=False):
                if m.author.bot:
                    continue
                messages.append(m)
        except Exception:
            continue

    if not messages:
        done[str(guild.id)] = now_ts()
        await STORE.mark_dirty()
        await setup_log(
            f"Backfill: no messages for {guild.name} ({guild.id}) "
            f"[scanned={scanned} skipped={skipped}]"
        )
        return

    messages.sort(key=lambda m: m.created_at)
    if len(messages) > limit:
        messages = messages[-limit:]

    for m in messages:
        await mirror_send_to_rule(m, rule)
        if delay:
            await asyncio.sleep(delay)

    done[str(guild.id)] = now_ts()
    await STORE.mark_dirty()
    latest = mirror_rules_dict().get(rule.get("rule_id"), rule)
    err = str(latest.get("last_error") or "").strip()
    if err:
        await setup_log(f"Backfill error for {guild.name}: {err}")

async def backfill_chat_stats_for_guild(guild: discord.Guild):
    if not guild:
        return
    done = chat_stats_backfill_done()
    if done.get(str(guild.id)):
        return

    per_channel = auto_backfill_per_channel()
    delay = auto_backfill_delay()
    if per_channel <= 0:
        return

    me = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
    if not me and bot.user:
        try:
            me = await guild.fetch_member(bot.user.id)
        except Exception:
            me = None
    if not me:
        return

    scanned = 0
    skipped = 0
    counted = 0
    for ch in guild.text_channels:
        perms = ch.permissions_for(me)
        if not perms.read_message_history or not perms.read_messages:
            skipped += 1
            continue
        scanned += 1
        try:
            async for m in ch.history(limit=per_channel, oldest_first=False):
                if m.author.bot:
                    continue
                await chat_stats_increment(m, mark_dirty=False)
                counted += 1
                if delay:
                    await asyncio.sleep(delay)
        except Exception:
            continue

    done[str(guild.id)] = now_ts()
    await STORE.mark_dirty()
    if counted == 0:
        await setup_log(
            f"Chat stats backfill: no messages for {guild.name} ({guild.id}) "
            f"[scanned={scanned} skipped={skipped}]"
        )

async def backfill_chat_stats_all_guilds():
    for g in bot.guilds:
        await backfill_chat_stats_for_guild(g)

async def migrate_legacy_json_mirrors():
    legacy = cfg().get("mirrors", {})
    if not legacy:
        return
    rules = mirror_rules_dict()
    preserved: Dict[str, Any] = {}
    for key, dst in legacy.items():
        if ":" not in str(key):
            preserved[key] = dst
            continue
        try:
            gid_str, src_str = key.split(":")
            src_id = int(src_str)
            gid = int(gid_str)
            rule_id = make_rule_id("channel", src_id, int(dst))
            if rule_id in rules:
                continue
            rules[rule_id] = normalize_rule({
                "rule_id": rule_id,
                "scope": "channel",
                "source_guild": gid,
                "source_id": src_id,
                "target_channel": int(dst),
                "enabled": True,
                "fail_count": 0
            })
        except Exception:
            continue
    cfg()["mirrors"] = preserved
    await STORE.mark_dirty()

async def migrate_legacy_mysql_mirrors():
    if not POOL:
        return
    rows = await db_all("SELECT mirror_id, source_guild, source_channel, target_channel, enabled FROM mirrors")
    if not rows:
        return
    rules = mirror_rules_dict()
    for row in rows:
        rule_id = make_rule_id("channel", int(row["source_channel"]), int(row["target_channel"]))
        if rule_id in rules:
            continue
        rules[rule_id] = normalize_rule({
            "rule_id": rule_id,
            "scope": "channel",
            "source_guild": int(row["source_guild"]),
            "source_id": int(row["source_channel"]),
            "target_channel": int(row["target_channel"]),
            "enabled": bool(row.get("enabled", 1)),
            "fail_count": 0
        })
    await STORE.mark_dirty()
    await mirror_rules_sync()

def mirror_message_map() -> Dict[str, List[Dict[str, Any]]]:
    return cfg().setdefault("mirror_message_map", {})

async def mirror_store_map(rule_id: str, src_guild: int, src_channel: int, src_msg: int, dst_msg: int, author_id: int):
    if POOL:
        await db_exec("""
        INSERT INTO mirror_messages (mirror_id, src_guild, src_channel, src_msg, dst_msg, author_id)
        VALUES (%s,%s,%s,%s,%s,%s)
        """, (rule_id, src_guild, src_channel, src_msg, dst_msg, author_id))

        # prune to last 50 per mirror
        rows = await db_all("SELECT id FROM mirror_messages WHERE mirror_id=%s ORDER BY id DESC LIMIT 200", (rule_id,))
        if len(rows) > 50:
            cutoff = rows[49]["id"]
            await db_exec("DELETE FROM mirror_messages WHERE mirror_id=%s AND id < %s", (rule_id, cutoff))

    m = mirror_message_map()
    lst = m.setdefault(rule_id, [])
    lst.append({
        "mirror_id": rule_id,
        "src_guild": src_guild,
        "src_channel": src_channel,
        "src_msg": src_msg,
        "dst_msg": dst_msg,
        "author_id": author_id
    })
    if len(lst) > 50:
        m[rule_id] = lst[-50:]
    await STORE.mark_dirty()

async def mirror_fetch_src_by_dst(dst_msg_id: int) -> Optional[dict]:
    if POOL:
        row = await db_one("SELECT * FROM mirror_messages WHERE dst_msg=%s ORDER BY id DESC LIMIT 1", (dst_msg_id,))
        if row:
            return row
    for rule_id, lst in mirror_message_map().items():
        for item in reversed(lst):
            if int(item.get("dst_msg", 0)) == dst_msg_id:
                return dict(item)
    return None

class MirrorSendModal(discord.ui.Modal):
    def __init__(self, mode: str):
        super().__init__(title=f"Mirror {mode.title()}", timeout=300)
        self.mode = mode
        self.text = discord.ui.TextInput(
            label="Message",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1800,
            placeholder="Type message..."
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user or not interaction.message:
            return
        lvl = await effective_level(interaction.user)
        if lvl < 70:
            return await interaction.response.send_message(voice_line(cfg(), "err_no_permission"), ephemeral=True)

        row = await mirror_fetch_src_by_dst(interaction.message.id)
        if not row:
            return await interaction.response.send_message(voice_line(cfg(), "err_mapping_missing"), ephemeral=True)

        src_guild_id = int(row["src_guild"])
        src_channel_id = int(row["src_channel"])
        src_msg_id = int(row["src_msg"])
        author_id = int(row["author_id"])
        msg_text = str(self.text.value)

        try:
            src_guild = bot.get_guild(src_guild_id) or await bot.fetch_guild(src_guild_id)
            src_channel = src_guild.get_channel(src_channel_id) or await bot.fetch_channel(src_channel_id)
        except Exception:
            return await interaction.response.send_message(voice_line(cfg(), "err_source_not_accessible"), ephemeral=True)

        try:
            if self.mode == "reply":
                try:
                    m = await src_channel.fetch_message(src_msg_id)
                    await m.reply(msg_text)
                except Exception:
                    await src_channel.send(f"(reply)\n{msg_text}")
                await audit(interaction.user.id, "Mirror: direct reply", {"src_channel": src_channel_id, "src_msg": src_msg_id})

            elif self.mode == "post":
                await src_channel.send(msg_text)
                await audit(interaction.user.id, "Mirror: post", {"src_channel": src_channel_id})

            elif self.mode == "dm":
                try:
                    u = await bot.fetch_user(author_id)
                    await u.send(msg_text)
                    await audit(interaction.user.id, "Mirror: DM user", {"user_id": author_id})
                    return await interaction.response.send_message(voice_line(cfg(), "confirm_dm_sent"), ephemeral=True)
                except discord.Forbidden:
                    ch_id = await ensure_dm_bridge_active(author_id, reason="mirror")
                    if ch_id:
                        msg = voice_line(cfg(), "confirm_dm_sent") + " DM bridge active."
                        return await interaction.response.send_message(msg, ephemeral=True)
                    raise

        except Exception:
            return await interaction.response.send_message(voice_line(cfg(), "err_send_failed"), ephemeral=True)

        await interaction.response.send_message(voice_line(cfg(), "confirm_sent"), ephemeral=True)

class MirrorControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _ensure_allowed(self, interaction: discord.Interaction) -> bool:
        if not mirror_controls_enabled():
            await interaction.response.send_message(voice_line(cfg(), "err_no_permission"), ephemeral=True)
            return False
        if not interaction.user:
            return False
        lvl = await effective_level(interaction.user)
        if lvl < 70:
            await interaction.response.send_message(voice_line(cfg(), "err_no_permission"), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Reply", style=discord.ButtonStyle.primary, custom_id="mirror:reply")
    async def b_reply(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._ensure_allowed(interaction):
            return
        await interaction.response.send_modal(MirrorSendModal("reply"))

    @discord.ui.button(label="Post", style=discord.ButtonStyle.secondary, custom_id="mirror:post")
    async def b_post(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._ensure_allowed(interaction):
            return
        await interaction.response.send_modal(MirrorSendModal("post"))

    @discord.ui.button(label="DM Author", style=discord.ButtonStyle.success, custom_id="mirror:dm")
    async def b_dm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._ensure_allowed(interaction):
            return
        await interaction.response.send_modal(MirrorSendModal("dm"))

    @discord.ui.button(label="Jump", style=discord.ButtonStyle.secondary, custom_id="mirror:jump")
    async def b_jump(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._ensure_allowed(interaction):
            return
        row = await mirror_fetch_src_by_dst(interaction.message.id)
        if not row:
            return await interaction.response.send_message(voice_line(cfg(), "err_mapping_missing"), ephemeral=True)
        src_guild_id = int(row.get("src_guild", 0))
        src_channel_id = int(row.get("src_channel", 0))
        src_msg_id = int(row.get("src_msg", 0))
        url = f"https://discord.com/channels/{src_guild_id}/{src_channel_id}/{src_msg_id}"
        await interaction.response.send_message(url, ephemeral=True)

    @discord.ui.button(label="Mute Source", style=discord.ButtonStyle.danger, custom_id="mirror:mute")
    async def b_mute(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._ensure_allowed(interaction):
            return
        row = await mirror_fetch_src_by_dst(interaction.message.id)
        if not row:
            return await interaction.response.send_message(voice_line(cfg(), "err_mapping_missing"), ephemeral=True)
        rule_id = row.get("rule_id") or row.get("mirror_id")
        rule = mirror_rules_dict().get(rule_id) if rule_id else None
        if not rule:
            return await interaction.response.send_message(voice_line(cfg(), "err_mapping_missing"), ephemeral=True)
        await mirror_rule_disable(rule, "muted via mirror control")
        await audit(interaction.user.id, "Mirror: mute source", {"rule_id": rule_id})
        await interaction.response.send_message(voice_line(cfg(), "confirm_mirror_removed", count=1), ephemeral=True)

def rule_matches_message(rule: Dict[str, Any], message: discord.Message) -> bool:
    if int(rule.get("source_guild", 0)) != message.guild.id:
        return False
    scope = rule.get("scope", "channel")
    src_id = int(rule.get("source_id", 0))
    if scope == "server":
        return message.guild.id == src_id
    if scope == "category":
        if message.channel.category and message.channel.category.id == src_id:
            return True
        return False
    return message.channel.id == src_id

async def build_mirror_payload(message: discord.Message, dst_perms: discord.Permissions) -> Tuple[str, List[discord.Embed], List[discord.File]]:
    content = message.content or ""
    extra_lines = []

    # Stickers
    if message.stickers:
        names = ", ".join([s.name for s in message.stickers])
        extra_lines.append(f"Stickers: {names}")

    files: List[discord.File] = []
    attach_links: List[str] = []

    for att in message.attachments:
        if dst_perms.attach_files:
            try:
                files.append(await att.to_file())
            except Exception:
                attach_links.append(att.url)
        else:
            attach_links.append(att.url)

    if attach_links:
        extra_lines.append("Attachments: " + " | ".join(attach_links))

    if extra_lines:
        if content:
            content += "\n" + "\n".join(extra_lines)
        else:
            content = "\n".join(extra_lines)

    if not content:
        content = "(no text)"

    embeds: List[discord.Embed] = []
    if dst_perms.embed_links:
        header = discord.Embed(description=content[:3800], color=discord.Color.dark_gray())
        header.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        header.add_field(name="Source", value=f"{message.guild.name} / #{message.channel.name}", inline=False)
        header.add_field(name="Jump", value=message.jump_url, inline=False)
        header.set_footer(text=f"src_mid={message.id}")
        header.timestamp = message.created_at
        embeds.append(header)
        for e in message.embeds[:9]:
            embeds.append(e)
        content = ""
    return content, embeds, files

async def mirror_send_to_rule(message: discord.Message, rule: Dict[str, Any]):
    dst_id = int(rule.get("target_channel", 0))
    if not dst_id:
        return

    dst = bot.get_channel(dst_id)
    if not dst:
        try:
            dst = await bot.fetch_channel(dst_id)
        except Exception as e:
            await mirror_rule_record_failure(rule, f"target missing: {e}")
            return

    if not isinstance(dst, discord.TextChannel):
        await mirror_rule_record_failure(rule, "target not text channel")
        return

    perms = dst.permissions_for(dst.guild.me)
    if not perms.send_messages:
        await mirror_rule_record_failure(rule, "missing send_messages")
        return

    try:
        content, embeds, files = await build_mirror_payload(message, perms)
        view = MirrorControls() if mirror_controls_enabled() else None
        sent = await dst.send(
            content=content[:1900] if content else None,
            embeds=embeds if embeds else None,
            files=files if files else None,
            view=view,
            allowed_mentions=discord.AllowedMentions.none()
        )
    except discord.NotFound as e:
        await mirror_rule_record_failure(rule, f"target not found: {e}")
        return
    except discord.Forbidden as e:
        await mirror_rule_record_failure(rule, f"forbidden: {e}")
        return
    except Exception as e:
        await mirror_rule_record_failure(rule, f"send error: {e}")
        return

    await log_to(
        "mirror",
        "Mirror relay delivered",
        subsystem="SENSORY",
        severity="INFO",
        details={"author": str(message.author), "guild": message.guild.id, "channel": message.channel.id},
    )
    await mirror_rule_mark_success(rule, message.content or "(no text)")

    # Persist mapping for reply buttons (MySQL + JSON fallback)
    await mirror_store_map(rule["rule_id"], message.guild.id, message.channel.id, message.id, sent.id, message.author.id)

    # Update per-guild mirror status
    status = cfg().setdefault("mirror_status", {})
    status[str(message.guild.id)] = {
        "last_mirror_ts": now_ts(),
        "last_mirror_author": str(message.author),
        "last_mirror_channel": message.channel.name,
        "last_mirror_msg": truncate(message.content or "", 180)
    }
    await STORE.mark_dirty()

async def mirror_tick(message: discord.Message):
    if not message.guild:
        return
    rules = mirror_rules_dict()
    if not rules:
        return
    matched: List[Dict[str, Any]] = []
    for rule in rules.values():
        if not rule.get("enabled", True):
            continue
        if rule_matches_message(rule, message):
            matched.append(rule)
    if not matched:
        return
    seen = set()
    for rule in matched:
        tgt = int(rule.get("target_channel", 0))
        if tgt in seen:
            continue
        seen.add(tgt)
        await mirror_send_to_rule(message, rule)

# -----------------------------
# DM Bridge (open channel; relay both ways; history dump)
# -----------------------------
def normalize_dm_bridge_entry(entry: Any) -> Optional[Dict[str, Any]]:
    if not entry:
        return None
    if isinstance(entry, dict):
        return {
            "channel_id": int(entry.get("channel_id", 0)),
            "active": bool(entry.get("active", True)),
            "last_activity": int(entry.get("last_activity") or 0)
        }
    try:
        return {"channel_id": int(entry), "active": True, "last_activity": 0}
    except Exception:
        return None

async def dm_bridge_get(user_id: int) -> Optional[Dict[str, Any]]:
    if POOL:
        row = await db_one("SELECT channel_id, active, last_activity FROM dm_bridges WHERE user_id=%s", (user_id,))
        if row:
            return {
                "channel_id": int(row.get("channel_id", 0)),
                "active": bool(row.get("active", True)),
                "last_activity": int(row.get("last_activity") or 0)
            }
        return None
    return normalize_dm_bridge_entry(cfg().get("dm_bridges", {}).get(str(user_id)))

async def dm_bridge_channel_for_user(user_id: int) -> Optional[int]:
    info = await dm_bridge_get(user_id)
    if info and info.get("active"):
        return int(info.get("channel_id", 0)) or None
    return None

async def dm_bridge_user_for_channel(channel_id: int) -> Optional[int]:
    if POOL:
        row = await db_one(
            "SELECT user_id FROM dm_bridges WHERE channel_id=%s AND active=TRUE",
            (channel_id,)
        )
        if row:
            return int(row["user_id"])
    bridges = cfg().get("dm_bridges", {})
    for uid, entry in bridges.items():
        norm = normalize_dm_bridge_entry(entry)
        if norm and norm.get("active") and int(norm.get("channel_id", 0)) == channel_id:
            return int(uid)
    return None

async def dm_bridge_set(user_id: int, channel_id: int, active: bool = True, last_activity: Optional[int] = None):
    ts = int(last_activity or now_ts())
    if POOL:
        await db_exec("""
        INSERT INTO dm_bridges (user_id, channel_id, active, last_activity)
        VALUES (%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE channel_id=VALUES(channel_id), active=VALUES(active), last_activity=VALUES(last_activity);
        """, (user_id, channel_id, 1 if active else 0, ts))
    else:
        cfg().setdefault("dm_bridges", {})[str(user_id)] = {
            "channel_id": int(channel_id),
            "active": bool(active),
            "last_activity": ts
        }
        await STORE.mark_dirty()

async def dm_bridge_touch(user_id: int):
    info = await dm_bridge_get(user_id)
    if not info:
        return
    await dm_bridge_set(user_id, int(info.get("channel_id", 0)), bool(info.get("active", True)), last_activity=now_ts())

async def ensure_dm_category(name: str) -> Optional[discord.CategoryChannel]:
    admin = bot.get_guild(ADMIN_GUILD_ID)
    if not admin:
        return None
    cat = discord.utils.get(admin.categories, name=name)
    if cat:
        return cat
    try:
        cat = await admin.create_category(name)
        await setup_pause()
        return cat
    except Exception:
        return None

async def dm_bridge_sync_history(user_id: int, ch: discord.TextChannel, limit: int = 25):
    try:
        user = await bot.fetch_user(user_id)
        dm = user.dm_channel or await user.create_dm()
        lines = []
        async for m in dm.history(limit=limit, oldest_first=True):
            who = "Mandy" if m.author.id == bot.user.id else m.author.name
            content = (m.content or "").replace("\n", " ")
            lines.append(f"[{m.created_at:%Y-%m-%d %H:%M}] {who}: {content}")
        await ch.send(f"dY\"\" **DM Bridge Opened** for <@{user_id}>")
        if lines:
            await ch.send("```text\n" + "\n".join(lines)[-1800:] + "\n```")
    except Exception:
        await ch.send(f"dY\"\" **DM Bridge Opened** for <@{user_id}>\nCould not pull DM history.")

async def ensure_dm_bridge_channel(user_id: int, active: bool = True) -> Optional[discord.TextChannel]:
    admin = bot.get_guild(ADMIN_GUILD_ID)
    if not admin:
        return None
    info = await dm_bridge_get(user_id)
    ch = None
    if info and info.get("channel_id"):
        ch = admin.get_channel(int(info["channel_id"]))
    if not ch:
        ch = discord.utils.get(admin.text_channels, name=f"dm-{user_id}")
    cat_name = "DM Bridges" if active else "Archived DM Bridges"
    cat = await ensure_dm_category(cat_name)
    if not ch:
        try:
            ch = await admin.create_text_channel(f"dm-{user_id}", category=cat)
            await setup_pause()
        except Exception:
            return None
    if cat and ch.category_id != cat.id:
        try:
            await ch.edit(category=cat)
            await setup_pause()
        except Exception:
            pass
    desired_name = f"dm-{user_id}" if active else f"archived-dm-{user_id}"
    if ch.name != desired_name:
        try:
            await ch.edit(name=desired_name)
            await setup_pause()
        except Exception:
            pass
    return ch

async def ensure_dm_bridge_active(user_id: int, reason: str = "auto") -> Optional[int]:
    info = await dm_bridge_get(user_id)
    if info and info.get("active") and info.get("channel_id"):
        await dm_bridge_touch(user_id)
        return int(info["channel_id"])
    ch = await ensure_dm_bridge_channel(user_id, active=True)
    if not ch:
        return None
    await dm_bridge_set(user_id, ch.id, active=True, last_activity=now_ts())
    await dm_bridge_sync_history(user_id, ch)
    await audit(SUPER_USER_ID, "DM bridge open", {"user_id": user_id, "channel_id": ch.id, "reason": reason})
    return ch.id

async def dm_bridge_close(user_id: int):
    info = await dm_bridge_get(user_id)
    if info and info.get("channel_id"):
        ch = await ensure_dm_bridge_channel(user_id, active=False)
        if ch:
            await audit(SUPER_USER_ID, "DM bridge archived", {"user_id": user_id, "channel_id": ch.id})
    await dm_bridge_set(user_id, int(info.get("channel_id", 0)) if info else 0, active=False, last_activity=now_ts())

async def dm_bridge_list_active() -> List[Dict[str, Any]]:
    bridges: List[Dict[str, Any]] = []
    if POOL:
        rows = await db_all("SELECT user_id, channel_id, last_activity FROM dm_bridges WHERE active=TRUE")
        for row in rows:
            bridges.append({
                "user_id": int(row.get("user_id", 0)),
                "channel_id": int(row.get("channel_id", 0)),
                "last_activity": int(row.get("last_activity") or 0)
            })
        return bridges
    for uid, entry in cfg().get("dm_bridges", {}).items():
        norm = normalize_dm_bridge_entry(entry)
        if norm and norm.get("active"):
            bridges.append({
                "user_id": int(uid),
                "channel_id": int(norm.get("channel_id", 0)),
                "last_activity": int(norm.get("last_activity") or 0)
            })
    return bridges

async def archive_inactive_dm_bridges():
    cutoff = now_ts() - 86400
    for b in await dm_bridge_list_active():
        last = int(b.get("last_activity") or 0)
        if last and last > cutoff:
            continue
        await dm_bridge_close(int(b["user_id"]))
        await audit(SUPER_USER_ID, "DM bridge auto-archived", {"user_id": b["user_id"]})

async def send_dm_typing_indicator(user_id: int, ch: discord.TextChannel):
    now = time.time()
    last = TYPING_INDICATORS.get(user_id, 0.0)
    if now - last < TYPING_RATE_SECONDS:
        return
    TYPING_INDICATORS[user_id] = now
    try:
        msg = await ch.send("✏️ User is typing...")
    except Exception:
        return
    async def _cleanup(m: discord.Message):
        await asyncio.sleep(TYPING_RATE_SECONDS)
        try:
            await m.delete()
        except Exception:
            pass
    asyncio.create_task(_cleanup(msg))

async def relay_staff_typing(channel_id: int, user_id: int):
    now = time.time()
    last = BRIDGE_TYPING_INDICATORS.get(channel_id, 0.0)
    if now - last < TYPING_RATE_SECONDS:
        return
    BRIDGE_TYPING_INDICATORS[channel_id] = now
    try:
        user = await bot.fetch_user(user_id)
        async with user.typing():
            await asyncio.sleep(1)
    except Exception:
        pass

# -----------------------------
# Join gate (admin server)
# -----------------------------
async def ensure_roles(guild: discord.Guild):
    needed = [GOD_ROLE_NAME, ADMIN_ROLE_NAME, STAFF_ROLE_NAME, GUEST_ROLE_NAME, QUARANTINE_ROLE_NAME]
    existing = {r.name: r for r in guild.roles}
    created = []
    for name in needed:
        if name not in existing:
            try:
                role = await guild.create_role(name=name, reason="Mandy OS role bootstrap")
                existing[name] = role
                created.append(name)
                await setup_pause()
            except Exception:
                pass

    # role levels
    rbac = cfg().setdefault("rbac", {})
    levels = rbac.setdefault("role_levels", {})
    for name, lvl in ROLE_LEVEL_DEFAULTS.items():
        levels.setdefault(name, lvl)
    await STORE.mark_dirty()

    # reorder under bot top role
    try:
        bot_member = guild.me
        if bot_member:
            top = bot_member.top_role.position - 1
            order = [GOD_ROLE_NAME, ADMIN_ROLE_NAME, STAFF_ROLE_NAME, GUEST_ROLE_NAME, QUARANTINE_ROLE_NAME]
            pos = top
            for name in order:
                role = existing.get(name)
                if role and role.position != pos and pos > 0:
                    await role.edit(position=pos)
                    await setup_pause()
                    pos -= 1
    except Exception:
        pass

    # assign GOD to super users if present
    try:
        god_role = existing.get(GOD_ROLE_NAME)
        if god_role:
            for uid in (SUPER_USER_ID, AUTO_GOD_ID):
                member = guild.get_member(uid)
                if member and god_role not in member.roles:
                    await member.add_roles(god_role, reason="Mandy OS GOD role")
                    await setup_pause()
    except Exception:
        pass

async def apply_guest_permissions(guild: discord.Guild):
    guest = get_role(guild, GUEST_ROLE_NAME)
    if not guest:
        return
    for cat in guild.categories:
        try:
            if cat.name in ("Guest Access", "Welcome & Information"):
                await cat.set_permissions(guest, view_channel=True, send_messages=True, read_message_history=True)
            else:
                await cat.set_permissions(guest, view_channel=False)
            await setup_pause()
        except Exception:
            pass

async def apply_quarantine_permissions(guild: discord.Guild):
    quarantine = get_role(guild, QUARANTINE_ROLE_NAME)
    if not quarantine:
        return
    ch = discord.utils.get(guild.text_channels, name="quarantine")
    if not ch:
        return
    try:
        await ch.set_permissions(guild.default_role, view_channel=False)
        await setup_pause()
        await ch.set_permissions(quarantine, view_channel=True, send_messages=True, read_message_history=True)
        await setup_pause()
        guest = get_role(guild, GUEST_ROLE_NAME)
        if guest:
            await ch.set_permissions(guest, view_channel=False)
            await setup_pause()
        for role_name in (GOD_ROLE_NAME, ADMIN_ROLE_NAME, STAFF_ROLE_NAME):
            role = get_role(guild, role_name)
            if role:
                await ch.set_permissions(role, view_channel=True, send_messages=True, read_message_history=True)
                await setup_pause()
    except Exception:
        pass

async def gate_reset_attempts(user_id: int):
    g = cfg().setdefault("gate", {})
    if str(user_id) in g:
        g[str(user_id)]["tries"] = 0
        await STORE.mark_dirty()

async def gate_approve_user(member: discord.Member):
    if member.guild.id != ADMIN_GUILD_ID:
        return
    g = cfg().setdefault("gate", {})
    gate_info = g.pop(str(member.id), None)
    await STORE.mark_dirty()
    guest = get_role(member.guild, GUEST_ROLE_NAME)
    if guest in member.roles:
        try:
            await member.remove_roles(guest, reason="Gate approved")
        except Exception:
            pass
    # delete gate channel
    if gate_info:
        ch_id = int(gate_info.get("channel", 0))
        if ch_id:
            ch = member.guild.get_channel(ch_id)
            if ch:
                try:
                    await ch.delete()
                except Exception:
                    pass

async def gate_quarantine_user(member: discord.Member, reason: str = ""):
    if member.guild.id != ADMIN_GUILD_ID:
        return
    g = cfg().setdefault("gate", {})
    gate_info = g.pop(str(member.id), None)
    await STORE.mark_dirty()
    quarantine = get_role(member.guild, QUARANTINE_ROLE_NAME)
    if quarantine and quarantine not in member.roles:
        try:
            await member.add_roles(quarantine, reason="Gate quarantine")
        except Exception:
            pass
    # delete gate channel
    if gate_info:
        ch_id = int(gate_info.get("channel", 0))
        if ch_id:
            ch = member.guild.get_channel(ch_id)
            if ch:
                try:
                    await ch.delete()
                except Exception:
                    pass
    qch = discord.utils.get(member.guild.text_channels, name="quarantine")
    if qch:
        try:
            await qch.send(f"Quarantine: <@{member.id}> {reason}".strip())
        except Exception:
            pass

async def start_gate(member: discord.Member):
    if member.guild.id != ADMIN_GUILD_ID:
        return
    await ensure_roles(member.guild)
    await apply_guest_permissions(member.guild)
    await apply_quarantine_permissions(member.guild)

    guest = get_role(member.guild, GUEST_ROLE_NAME)
    if guest and guest not in member.roles:
        try:
            await member.add_roles(guest, reason="Gate entry")
        except Exception:
            pass

    # Create a private gate channel
    cat = discord.utils.get(member.guild.categories, name="Guest Access")
    if not cat:
        cat = await member.guild.create_category("Guest Access")

    overwrites = {
        member.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        member.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    for role_name in (GOD_ROLE_NAME, ADMIN_ROLE_NAME, STAFF_ROLE_NAME):
        role = get_role(member.guild, role_name)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    ch = await member.guild.create_text_channel(f"gate-{member.name}", category=cat, overwrites=overwrites)

    cfg().setdefault("gate", {})[str(member.id)] = {"channel": ch.id, "tries": 0}
    await STORE.mark_dirty()

    await ch.send("Enter the server password. (Attempts auto-deleted)")

async def handle_gate_attempt(message: discord.Message) -> bool:
    if not message.guild or message.guild.id != ADMIN_GUILD_ID:
        return False
    g = cfg().setdefault("gate", {})
    uid = str(message.author.id)
    if uid not in g:
        return False
    if int(g[uid].get("channel", 0)) != message.channel.id:
        return False

    # consume attempt
    await safe_delete(message)

    if SERVER_PASSWORD and (message.content or "").strip() == SERVER_PASSWORD:
        # pass
        await gate_approve_user(message.author)
        try:
            await message.author.send("Access granted.")
        except Exception:
            pass
        await audit(message.author.id, "Gate PASS", {"user_id": message.author.id})
        return True

    # fail
    g[uid]["tries"] = int(g[uid].get("tries", 0)) + 1
    tries = g[uid]["tries"]

    if tries >= 3:
        await gate_quarantine_user(message.author, "Max attempts")
        await audit(message.author.id, "Gate QUARANTINE", {"user_id": message.author.id})
        return True

    await STORE.mark_dirty()
    try:
        await message.channel.send(f"Wrong password. Attempts left: **{3-tries}**")
    except Exception:
        pass
    await audit(message.author.id, "Gate FAIL", {"user_id": message.author.id, "tries": tries})
    return True

# -----------------------------
# Auto-populate server + pins
# -----------------------------
async def ensure_category(guild: discord.Guild, name: str) -> discord.CategoryChannel:
    cat = discord.utils.get(guild.categories, name=name)
    if cat:
        return cat
    cat = await guild.create_category(name)
    await setup_pause()
    return cat

async def ensure_text_channel(
    guild: discord.Guild,
    name: str,
    category: discord.CategoryChannel,
    topic: Optional[str] = None,
) -> discord.TextChannel:
    ch = discord.utils.get(guild.text_channels, name=name)
    if ch:
        try:
            edits: Dict[str, Any] = {}
            if category and ch.category != category:
                edits["category"] = category
            if topic is not None and (ch.topic or "") != topic:
                edits["topic"] = topic
            if edits:
                await ch.edit(**edits)
                await setup_pause()
        except Exception:
            pass
        return ch
    ch = await guild.create_text_channel(name, category=category, topic=topic or None)
    await setup_pause()
    return ch

async def ensure_pinned(channel: discord.TextChannel, content: str):
    key = content.splitlines()[0][:60]
    try:
        pins = [p async for p in channel.pins()]
        for p in pins:
            if p.author.id == bot.user.id and (p.content or "").startswith(key):
                if p.content != content:
                    await p.edit(content=content)
                    await setup_pause()
                return
        m = await channel.send(content)
        await m.pin()
        await setup_pause()
    except Exception:
        pass

async def ensure_menu_panel(
    guild: discord.Guild,
    channel_name: str,
    entry_key: str,
    content: str,
    view: discord.ui.View,
):
    ch = find_text_by_name(guild, channel_name)
    if not ch:
        return
    state = cfg().setdefault("menu_messages", {})
    entry = state.setdefault(str(guild.id), {})
    msg_id = entry.get(entry_key)
    menu_style = str(sentience_cfg(cfg()).get("menu_style") or "default")
    payload: Dict[str, Any] = {"view": view}
    if menu_style == "glitchy":
        title = "Mandy Menu" if str(entry_key).startswith("user_menu") else "GOD MENU"
        glitch = [
            f"// {title.upper()} :: CORTEX LINK ACTIVE //",
            "Signal integrity: stable",
            "Operator interface: ready",
            "Latency: minimal",
            "Command surface: armed",
        ]
        if str(entry_key).startswith("god_menu"):
            glitch.insert(2, "Immune clearance: elevated")
        emb = discord.Embed(
            title=title,
            description="\n".join(glitch),
            color=discord.Color.teal() if entry_key == "user_menu" else discord.Color.dark_gold(),
        )
        emb.set_footer(text="Sentient Core Interface")
        payload["embed"] = emb
        payload["content"] = None
    else:
        payload["content"] = content
    if msg_id:
        try:
            msg = await ch.fetch_message(int(msg_id))
            await msg.edit(**payload)
            view.message = msg
            try:
                if not msg.pinned:
                    await msg.pin()
            except Exception:
                pass
            return
        except Exception:
            entry.pop(entry_key, None)
    msg = await ch.send(**payload)
    view.message = msg
    entry[entry_key] = msg.id
    await STORE.mark_dirty()
    try:
        await msg.pin()
    except Exception:
        pass


async def repopulate_channel(channel: discord.TextChannel):
    if not channel.guild:
        return
    try:
        pinned_text = cfg().get("pinned_text", {})
        content = pinned_text.get(channel.name)
        if content:
            await ensure_pinned(channel, content)
    except Exception:
        pass

    channels_cfg = cfg().get("command_channels", {})
    user_channel = channels_cfg.get("user", "command-requests")
    god_channel = channels_cfg.get("god", "admin-chat")
    if channel.name == user_channel:
        await ensure_menu_panel(
            channel.guild,
            user_channel,
            "user_menu",
            "**Mandy Menu**\nUse the buttons below.",
            UserMenuView(0, timeout=None),
        )
    elif channel.name == god_channel:
        await ensure_menu_panel(
            channel.guild,
            god_channel,
            "god_menu",
            "**GOD MENU**\nGOD-only controls.",
            GodMenuView(0, timeout=None),
        )

async def ensure_menu_panels(guild: discord.Guild):
    channels_cfg = cfg().get("command_channels", {})
    user_channel = channels_cfg.get("user", "command-requests")
    god_channel = channels_cfg.get("god", "admin-chat")
    await ensure_menu_panel(
        guild,
        user_channel,
        "user_menu",
        "**Mandy Menu**\nUse the buttons below.",
        UserMenuView(0, timeout=None),
    )
    await ensure_menu_panel(
        guild,
        god_channel,
        "god_menu",
        "**GOD MENU**\nGOD-only controls.",
        GodMenuView(0, timeout=None),
    )

async def ensure_log_channels(guild: discord.Guild):
    logs = cfg().setdefault("logs", {})
    system = find_text_by_name(guild, "system-logs")
    audit_ch = find_text_by_name(guild, "audit-logs") or system
    debug_ch = find_text_by_name(guild, "debug-logs") or system
    mirror_ch = find_text_by_name(guild, "mirror-logs") or system
    if system:
        logs["system"] = system.id
    if audit_ch:
        logs["audit"] = audit_ch.id
    if debug_ch:
        logs["debug"] = debug_ch.id
    if mirror_ch:
        logs["mirror"] = mirror_ch.id
    await STORE.mark_dirty()

async def ensure_admin_server_channels(source_guild: discord.Guild):
    admin = bot.get_guild(ADMIN_GUILD_ID)
    if not admin:
        return None, None
    state = cfg().setdefault("admin_servers", {})
    entry = state.setdefault(str(source_guild.id), {})

    cat = admin.get_channel(entry.get("category_id")) if entry.get("category_id") else None
    if not isinstance(cat, discord.CategoryChannel):
        cat = discord.utils.get(admin.categories, name=admin_category_name(source_guild))
        if not cat:
            cat = await admin.create_category(admin_category_name(source_guild))
            await setup_pause()
        entry["category_id"] = cat.id
    else:
        desired = admin_category_name(source_guild)
        if cat.name != desired:
            try:
                await cat.edit(name=desired)
                await setup_pause()
            except Exception:
                pass

    mirror_feed = admin.get_channel(entry.get("mirror_feed")) if entry.get("mirror_feed") else None
    if not isinstance(mirror_feed, discord.TextChannel):
        mirror_feed = discord.utils.get(cat.text_channels, name="mirror-feed")
        if not mirror_feed:
            mirror_feed = await admin.create_text_channel("mirror-feed", category=cat)
            await setup_pause()
        entry["mirror_feed"] = mirror_feed.id

    info_ch = admin.get_channel(entry.get("server_info")) if entry.get("server_info") else None
    if not isinstance(info_ch, discord.TextChannel):
        legacy_id = entry.get("server_status")
        info_ch = admin.get_channel(legacy_id) if legacy_id else None
    if not isinstance(info_ch, discord.TextChannel):
        info_ch = discord.utils.get(cat.text_channels, name="server-info")
    if not isinstance(info_ch, discord.TextChannel):
        legacy = discord.utils.get(cat.text_channels, name="server-status")
        if legacy:
            info_ch = legacy
            try:
                await info_ch.edit(name="server-info")
            except Exception:
                pass
        else:
            info_ch = await admin.create_text_channel("server-info", category=cat)
            await setup_pause()

    if info_ch:
        entry["server_info"] = info_ch.id

    await STORE.mark_dirty()
    return mirror_feed, info_ch

def find_category_by_name(guild: discord.Guild, name: str) -> Optional[discord.CategoryChannel]:
    return discord.utils.get(guild.categories, name=name)

def find_text_by_name(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    return discord.utils.get(guild.text_channels, name=name)

async def verify_layout(guild: discord.Guild) -> List[str]:
    layout = cfg().get("layout", {}).get("categories", {})
    pins = cfg().get("pinned_text", {})
    topics = cfg().get("channel_topics", {})
    missing: List[str] = []

    for cat_name, chans in layout.items():
        cat = find_category_by_name(guild, cat_name)
        if not cat:
            missing.append(f"category:{cat_name}")
            cat = await ensure_category(guild, cat_name)
        for ch_name in chans:
            ch = find_text_by_name(guild, ch_name)
            if not ch and cat:
                missing.append(f"channel:{cat_name}/{ch_name}")
                ch = await ensure_text_channel(guild, ch_name, cat, topic=topics.get(ch_name))
            elif ch and cat:
                await ensure_text_channel(guild, ch_name, cat, topic=topics.get(ch_name))
            if ch_name in pins and ch:
                await ensure_pinned(ch, pins[ch_name])
    return missing

async def ensure_layout_defaults() -> Dict[str, List[str]]:
    layout = cfg().setdefault("layout", {})
    cats = layout.setdefault("categories", {})
    required = {
        "Welcome & Information": ["rules-and-guidelines", "announcements", "guest-briefing"],
        "Bot Control & Monitoring": ["bot-status", "command-requests", "error-reporting"],
        "Research & Development": ["algorithm-discussion", "data-analysis"],
        "Guest Access": ["guest-chat", "guest-feedback", "quarantine"],
        "Engineering Core": ["core-chat", "system-logs", "audit-logs", "debug-logs", "mirror-logs"],
        "Admin Backrooms": ["admin-chat", "server-management"],
        "DM Bridges": [],
    }
    changed = False
    for cat, channels in required.items():
        if cat not in cats:
            cats[cat] = list(channels)
            changed = True
            continue
        current = list(cats.get(cat) or [])
        for ch in channels:
            if ch not in current:
                current.append(ch)
                changed = True
        cats[cat] = current
    if changed:
        await STORE.mark_dirty()
    return cats

async def setup_fullsync(guild: discord.Guild):
    layout = await ensure_layout_defaults()
    pins = cfg().get("pinned_text", {})
    topics = cfg().get("channel_topics", {})

    await setup_log(f"Setup start: {guild.name} ({guild.id})")
    await setup_log("Phase 1/4: build categories and channels")
    for cat_name, chans in layout.items():
        cat = await ensure_category(guild, cat_name)
        for ch_name in chans:
            await ensure_text_channel(guild, ch_name, cat, topic=topics.get(ch_name))

    await setup_log("Phase 1/4: populate pinned text")
    for ch_name, content in pins.items():
        ch = find_text_by_name(guild, ch_name)
        if ch:
            await ensure_pinned(ch, content)

    await setup_log("Phase 1/4: command menus + log routing")
    await ensure_menu_panels(guild)
    await ensure_log_channels(guild)

    missing = await verify_layout(guild)
    if missing:
        await setup_log(f"Layout verify: retry missing {len(missing)}")
        missing = await verify_layout(guild)
        if missing:
            await setup_log("Layout verify failed: " + ", ".join(missing[:15]))

    # roles + gate perms for admin guild
    if guild.id == ADMIN_GUILD_ID:
        await setup_log("Phase 2/4: roles + permissions")
        await ensure_roles(guild)
        await apply_guest_permissions(guild)
        await apply_quarantine_permissions(guild)

    await ensure_log_channels(guild)
    await setup_log("Phase 3/4: setup complete for this server")

class SetupAiRateLimitError(Exception):
    def __init__(self, retry_after: Optional[float] = None):
        super().__init__("AI setup rate limited")
        self.retry_after = retry_after

def _mandy_ai_client():
    mandy = bot.get_cog("MandyAI")
    if not mandy:
        return None
    return getattr(mandy, "client", None)

def _is_ai_rate_limit(client, exc: Exception) -> Tuple[bool, Optional[float]]:
    if client and hasattr(client, "_is_rate_limit_error"):
        try:
            return client._is_rate_limit_error(exc)
        except Exception:
            pass
    msg = str(exc).lower()
    is_rate = "rate limit" in msg or "429" in msg or "quota" in msg or "resource exhausted" in msg
    return is_rate, None

async def _generate_setup_ai_brief(guild: discord.Guild) -> str:
    client = _mandy_ai_client()
    if not client or not getattr(client, "available", False):
        return ""
    ai = ai_cfg()
    model = str(ai.get("router_model") or ai.get("default_model") or "gemini-2.5-flash-lite")
    roles = len(guild.roles) if guild else 0
    channels = len(guild.channels) if guild else 0
    categories = len([c for c in guild.channels if isinstance(c, discord.CategoryChannel)]) if guild else 0
    system_prompt = (
        "You are Mandy's sentient core. Produce a concise AI-assisted rebuild brief for an operator. "
        "Use a calm, analytical tone with biological metaphors (cortex, synapses, homeostasis). No emojis."
    )
    user_prompt = (
        "Create a short rebuild brief for a destructive setup run.\n"
        "Constraints:\n"
        "- 6 to 9 lines total.\n"
        "- Include 2 bullet metrics.\n"
        "- Include 1 observation and 1 operator recommendation.\n"
        "- Include 2 suggested enhancements for channel topics or pinned text (operator review only).\n\n"
        f"Context: roles={roles}, channels={channels}, categories={categories}, mysql={'on' if POOL else 'off'}."
    )
    try:
        text = await client.generate(system_prompt, user_prompt, model=model, response_format=None, timeout=60.0)
        return (text or "").strip()
    except Exception as exc:
        is_rate, retry_after = _is_ai_rate_limit(client, exc)
        if is_rate:
            raise SetupAiRateLimitError(retry_after=retry_after)
        return ""

async def _send_ai_setup_brief(user_id: int, guild: discord.Guild) -> bool:
    brief = await _generate_setup_ai_brief(guild)
    if not brief:
        return False
    user = bot.get_user(user_id)
    if not user:
        try:
            user = await bot.fetch_user(user_id)
        except Exception:
            return False
    try:
        await user.send(brief[:1900])
        return True
    except Exception:
        return False

async def _await_ai_rate_limit_response(user_id: int) -> Optional[str]:
    user = bot.get_user(user_id)
    if not user:
        try:
            user = await bot.fetch_user(user_id)
        except Exception:
            return None
    try:
        await user.send(
            "AI rebuild is rate-limited. Reply `wait` within 60s to retry; "
            "reply `default` (or anything else) to continue with the standard rebuild."
        )
    except Exception:
        return None
    try:
        msg = await bot.wait_for(
            "message",
            timeout=60,
            check=lambda m: m.author.id == user_id and isinstance(m.channel, discord.DMChannel),
        )
    except asyncio.TimeoutError:
        return None
    return (msg.content or "").strip().lower()

async def _generate_setup_ai_debrief(guild: discord.Guild) -> str:
    client = _mandy_ai_client()
    if not client or not getattr(client, "available", False):
        return ""
    ai = ai_cfg()
    model = str(ai.get("router_model") or ai.get("default_model") or "gemini-2.5-flash-lite")
    roles = len(guild.roles) if guild else 0
    channels = len(guild.channels) if guild else 0
    categories = len([c for c in guild.channels if isinstance(c, discord.CategoryChannel)]) if guild else 0
    mirror_rules = len(mirror_rules_dict().values())
    watchers = len(cfg().get("targets", {}) or {})
    dm_bridges = len(await dm_bridge_list_active())
    system_prompt = (
        "You are Mandy's sentient core. Produce a post-rebuild debrief for an operator. "
        "Use a calm, analytical tone with biological metaphors. No emojis."
    )
    user_prompt = (
        "Create a short post-rebuild debrief.\n"
        "Constraints:\n"
        "- 6 to 10 lines total.\n"
        "- Include 2 to 3 bullet metrics.\n"
        "- Include 1 observation and 1 operator recommendation.\n\n"
        f"Context: roles={roles}, channels={channels}, categories={categories}, "
        f"mirror_rules={mirror_rules}, watchers={watchers}, dm_bridges={dm_bridges}."
    )
    try:
        text = await client.generate(system_prompt, user_prompt, model=model, response_format=None, timeout=60.0)
        return (text or "").strip()
    except Exception:
        return ""

async def _send_ai_setup_debrief(user_id: int, guild: discord.Guild) -> bool:
    debrief = await _generate_setup_ai_debrief(guild)
    if not debrief:
        return False
    user = bot.get_user(user_id)
    if not user:
        try:
            user = await bot.fetch_user(user_id)
        except Exception:
            return False
    try:
        await user.send(debrief[:1900])
        return True
    except Exception:
        return False

async def auto_setup_guild(guild: discord.Guild, do_backfill: bool = False, force_backfill: bool = False):
    if guild.id == ADMIN_GUILD_ID:
        try:
            await setup_fullsync(guild)
        except Exception as e:
            await debug(f"Auto setup failed for {guild.id}: {e}")
            await setup_log(f"Auto setup failed for {guild.name} ({guild.id}): {e}")
        return

    try:
        await ensure_admin_server_channels(guild)
        await setup_pause()
        rule = await ensure_server_mirror_rule(guild)
        await setup_pause()
        await update_server_info_for_guild(guild)
        await setup_pause()
        if do_backfill and rule:
            await backfill_mirror_for_guild(guild, rule, force=force_backfill)
    except Exception as e:
        await debug(f"Auto mirror setup failed for {guild.id}: {e}")
        await setup_log(f"Auto mirror setup failed for {guild.name} ({guild.id}): {e}")

async def _auto_setup_all_guilds_nolock(
    do_backfill: bool = False,
    force_backfill: bool = False,
    include_admin: bool = True,
):
    if include_admin:
        admin = bot.get_guild(ADMIN_GUILD_ID)
        if admin:
            await auto_setup_guild(admin, do_backfill=False, force_backfill=force_backfill)
            await setup_pause()
    for g in bot.guilds:
        if g.id == ADMIN_GUILD_ID:
            continue
        await auto_setup_guild(g, do_backfill=do_backfill, force_backfill=force_backfill)
        await setup_pause()

async def auto_setup_all_guilds(
    do_backfill: bool = False,
    force_backfill: bool = False,
    include_admin: bool = True,
):
    async with AUTO_SETUP_LOCK:
        await _auto_setup_all_guilds_nolock(
            do_backfill=do_backfill,
            force_backfill=force_backfill,
            include_admin=include_admin,
        )

async def run_full_setup(guild: discord.Guild, mode: str, actor_id: int = 0):
    if guild.id != ADMIN_GUILD_ID:
        return
    await setup_log(f"Full setup requested: {mode} by {actor_id}")
    try:
        async with AUTO_SETUP_LOCK:
            if mode in ("destructive", "destructive_ai", "fullsync"):
                if mode == "destructive_ai":
                    ai_ok = True
                    try:
                        ai_ok = await _send_ai_setup_brief(actor_id, guild)
                    except SetupAiRateLimitError:
                        ai_ok = False
                        reply = await _await_ai_rate_limit_response(actor_id)
                        if reply in ("wait", "ai", "yes", "y"):
                            await asyncio.sleep(60)
                            try:
                                ai_ok = await _send_ai_setup_brief(actor_id, guild)
                            except SetupAiRateLimitError:
                                ai_ok = False
                    if not ai_ok:
                        await setup_log("AI rebuild unavailable; defaulting to standard destructive setup.")
                ok = await setup_destructive(guild)
                if not ok:
                    await setup_log("Full setup aborted due to incomplete cleanup.")
                    return
            else:
                await setup_fullsync(guild)
            await setup_log("Phase 4/4: mirrors + backfill")
            await _auto_setup_all_guilds_nolock(
                do_backfill=True,
                force_backfill=True,
                include_admin=False,
            )
            try:
                await mirror_rules_sync()
            except Exception as e:
                await setup_log(f"Mirror rule sync failed: {e}")
        try:
            await send_setup_debrief(trigger=mode)
        except Exception as e:
            await setup_log(f"Debrief failed: {e}")
        else:
            await setup_log("Full setup completed")
        if mode == "destructive_ai":
            try:
                await _send_ai_setup_debrief(actor_id, guild)
            except Exception:
                pass
    except Exception as e:
        await setup_log(f"Full setup failed: {e}")

async def run_auto_setup_with_debrief(actor_id: int = 0):
    await setup_log(f"Auto setup requested by {actor_id}")
    try:
        await auto_setup_all_guilds(do_backfill=True, force_backfill=True, include_admin=True)
        await send_setup_debrief(trigger="auto")
        await setup_log("Auto setup completed")
    except Exception as e:
        await setup_log(f"Auto setup failed: {e}")

def _managed_setup_categories(guild: discord.Guild) -> Set[str]:
    managed = set(cfg().get("layout", {}).get("categories", {}).keys())
    for cat in guild.categories:
        if cat.name.startswith("04-servers /"):
            managed.add(cat.name)
    return managed

def _remaining_managed_categories(guild: discord.Guild, managed: Set[str]) -> List[discord.CategoryChannel]:
    return [cat for cat in guild.categories if cat.name in managed]

async def _cleanup_managed_categories(guild: discord.Guild, managed: Set[str]) -> Tuple[int, int]:
    deleted_channels = 0
    deleted_categories = 0
    for cat in list(guild.categories):
        if cat.name not in managed:
            continue
        try:
            for ch in list(cat.channels):
                try:
                    await ch.delete()
                    deleted_channels += 1
                    await setup_pause()
                except Exception:
                    continue
            await cat.delete()
            deleted_categories += 1
            await setup_pause()
        except Exception:
            continue
    remaining = len(_remaining_managed_categories(guild, managed))
    return remaining, deleted_categories + deleted_channels

async def setup_destructive(guild: discord.Guild) -> bool:
    if guild.id != ADMIN_GUILD_ID:
        return False
    await setup_log("Phase 0/4: destructive cleanup starting")
    managed = _managed_setup_categories(guild)
    remaining = len(_remaining_managed_categories(guild, managed))
    passes = 3
    for attempt in range(passes):
        remaining, deleted = await _cleanup_managed_categories(guild, managed)
        await setup_log(f"Cleanup pass {attempt + 1}/{passes}: removed {deleted}, remaining={remaining}")
        if remaining == 0:
            break
        await asyncio.sleep(1)
    if remaining:
        await setup_log("Cleanup incomplete; aborting rebuild to avoid partial state.")
        return False
    await setup_log("Phase 0/4: destructive cleanup done")
    await setup_fullsync(guild)
    return True

BIO_LAYOUT = {
    "CEREBRAL CORTEX": ["synaptic-gap", "menu-hub", "manual-for-living", "thoughts"],
    "BIO-FILTER": ["audit-memory", "containment-ward", "diagnostics"],
    "VISUAL CORTEX": ["visual-feed"],
}
async def _ensure_recovery_anchor(guild: discord.Guild) -> Optional[Tuple[discord.CategoryChannel, discord.TextChannel, discord.TextChannel]]:
    try:
        recovery = await ensure_category(guild, "RECOVERY")
        cmd_line = await ensure_text_channel(guild, "command-line", recovery)
        system_log = await ensure_text_channel(guild, "system-log", recovery)
        return recovery, cmd_line, system_log
    except Exception:
        return None

async def _setup_bio_preflight(guild: discord.Guild) -> Optional[discord.TextChannel]:
    anchor = await _ensure_recovery_anchor(guild)
    if not anchor:
        return None
    _, cmd_line, system_log = anchor
    try:
        cmd_perms = cmd_line.permissions_for(guild.me)
        log_perms = system_log.permissions_for(guild.me)
        if not (cmd_perms.view_channel and cmd_perms.send_messages):
            return None
        if not (log_perms.view_channel and log_perms.send_messages):
            return None
    except Exception:
        return None
    cfg().setdefault("logs", {})["system"] = system_log.id
    await STORE.mark_dirty()
    try:
        await system_log.send("BIO-GENESIS starting. Recovery anchor online.")
    except Exception:
        return None
    return system_log

async def _setup_bio_wipe(guild: discord.Guild, recovery_id: int) -> bool:
    recovery = guild.get_channel(recovery_id)
    preserved = {recovery_id}
    if isinstance(recovery, discord.CategoryChannel):
        preserved.update({c.id for c in recovery.channels})
    passes = 3
    for attempt in range(passes):
        deleted = 0
        for ch in list(guild.channels):
            if ch.id in preserved:
                continue
            if isinstance(ch, discord.CategoryChannel):
                continue
            try:
                await ch.delete()
                deleted += 1
                await setup_pause()
            except Exception:
                continue
        for cat in list(guild.categories):
            if cat.id in preserved:
                continue
            try:
                await cat.delete()
                deleted += 1
                await setup_pause()
            except Exception:
                continue
        remaining = [
            c for c in guild.channels
            if c.id not in preserved and not isinstance(c, discord.CategoryChannel)
        ]
        remaining_cats = [c for c in guild.categories if c.id not in preserved]
        await setup_log(
            f"BIO cleanup pass {attempt + 1}/{passes}: removed={deleted} remaining={len(remaining)} cats={len(remaining_cats)}"
        )
        if not remaining and not remaining_cats:
            return True
        await asyncio.sleep(1)
    await setup_log("BIO cleanup incomplete after max passes.")
    return False

async def _setup_bio_build_layout(guild: discord.Guild) -> Dict[str, discord.TextChannel]:
    created: Dict[str, discord.TextChannel] = {}
    for cat_name, channels in BIO_LAYOUT.items():
        cat = await ensure_category(guild, cat_name)
        for ch_name in channels:
            ch = await ensure_text_channel(guild, ch_name, cat)
            created[ch_name] = ch
    return created

def _sanitize_topic(text: str) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    return cleaned[:300]

def _sanitize_pin(text: str) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) > 1800:
        cleaned = cleaned[:1797] + "..."
    return cleaned

async def _generate_bio_ai_updates(guild: discord.Guild, created: Dict[str, discord.TextChannel]) -> Dict[str, Dict[str, str]]:
    client = _mandy_ai_client()
    if not client or not getattr(client, "available", False):
        return {}
    ai = ai_cfg()
    model = str(ai.get("router_model") or ai.get("default_model") or "gemini-2.5-flash-lite")
    channels = sorted(created.keys())
    system_prompt = (
        "You are Mandy's sentient core. Produce JSON only. "
        "Create concise, biological-themed topics and pinned notes for operator channels. "
        "No emojis. Keep it professional."
    )
    user_prompt = (
        "Return JSON with optional keys 'topics' and 'pins'.\n"
        "- topics: map channel name to topic string (max 300 chars)\n"
        "- pins: map channel name to pinned text (max 1800 chars)\n"
        "Only use channels from this list:\n"
        f"{', '.join(channels)}\n"
        "Keep content calm, technical, and sentient-core themed.\n"
        "Output JSON only."
    )
    try:
        text = await client.generate(system_prompt, user_prompt, model=model, response_format="json", timeout=60.0)
        payload = json.loads(text or "{}")
    except Exception as exc:
        is_rate, retry_after = _is_ai_rate_limit(client, exc)
        if is_rate:
            raise SetupAiRateLimitError(retry_after=retry_after)
        return {}
    if not isinstance(payload, dict):
        return {}
    topics = payload.get("topics") if isinstance(payload.get("topics"), dict) else {}
    pins = payload.get("pins") if isinstance(payload.get("pins"), dict) else {}
    out_topics: Dict[str, str] = {}
    out_pins: Dict[str, str] = {}
    for name, value in topics.items():
        if name in created and isinstance(value, str) and value.strip():
            out_topics[name] = _sanitize_topic(value)
    for name, value in pins.items():
        if name in created and isinstance(value, str) and value.strip():
            out_pins[name] = _sanitize_pin(value)
    if not out_topics and not out_pins:
        return {}
    return {"topics": out_topics, "pins": out_pins}

async def _await_ai_enhancement_response(user_id: int) -> Optional[str]:
    user = bot.get_user(user_id)
    if not user:
        try:
            user = await bot.fetch_user(user_id)
        except Exception:
            return None
    try:
        await user.send(
            "AI enhancements are rate-limited. Reply `wait` within 60s to retry; "
            "reply `skip` (or anything else) to continue without AI content."
        )
    except Exception:
        return None
    try:
        msg = await bot.wait_for(
            "message",
            timeout=60,
            check=lambda m: m.author.id == user_id and isinstance(m.channel, discord.DMChannel),
        )
    except asyncio.TimeoutError:
        return None
    return (msg.content or "").strip().lower()

async def _confirm_bio_ai_updates(user_id: int, updates: Dict[str, Dict[str, str]]) -> bool:
    user = bot.get_user(user_id)
    if not user:
        try:
            user = await bot.fetch_user(user_id)
        except Exception:
            return False
    topics = updates.get("topics", {})
    pins = updates.get("pins", {})
    lines = ["BIO AI enhancements ready:"]
    if topics:
        lines.append(f"Topics: {len(topics)} channel(s)")
        for name, text in list(topics.items())[:6]:
            lines.append(f"- {name}: {truncate(text, 80)}")
    if pins:
        lines.append(f"Pins: {len(pins)} channel(s)")
        for name, text in list(pins.items())[:6]:
            lines.append(f"- {name}: {truncate(text, 80)}")
    lines.append("Reply `apply` within 60s to apply, or anything else to skip.")
    try:
        await user.send("\n".join(lines)[:1900])
    except Exception:
        return False
    try:
        msg = await bot.wait_for(
            "message",
            timeout=60,
            check=lambda m: m.author.id == user_id and isinstance(m.channel, discord.DMChannel),
        )
    except asyncio.TimeoutError:
        return False
    return (msg.content or "").strip().lower() in ("apply", "yes", "y")

async def _apply_bio_ai_updates(guild: discord.Guild, updates: Dict[str, Dict[str, str]]) -> None:
    topics = updates.get("topics", {})
    pins = updates.get("pins", {})
    topics_cfg = cfg().setdefault("channel_topics", {})
    pins_cfg = cfg().setdefault("pinned_text", {})
    for name, text in topics.items():
        ch = find_text_by_name(guild, name)
        if not ch:
            continue
        topics_cfg[name] = text
        try:
            await ch.edit(topic=text)
            await setup_pause()
        except Exception:
            pass
    for name, text in pins.items():
        ch = find_text_by_name(guild, name)
        if not ch:
            continue
        pins_cfg[name] = text
        await ensure_pinned(ch, text)
    await STORE.mark_dirty()

async def _setup_bio_reseed_ops(guild: discord.Guild, created: Dict[str, discord.TextChannel]) -> None:
    sent = sentience_cfg(cfg())
    channels = sent.setdefault("channels", {})
    channels["thoughts"] = created.get("thoughts").id if created.get("thoughts") else 0
    channels["visual_feed"] = created.get("visual-feed").id if created.get("visual-feed") else 0
    sent["enabled"] = True
    sent["dialect"] = "sentient_core"
    sent["menu_style"] = "glitchy"
    sent.setdefault("daily_reflection", {})["enabled"] = True
    sent["daily_reflection"]["fallback_enabled"] = True
    sent.setdefault("internal_monologue", {})["enabled"] = True

    presence = cfg().setdefault("presence", {})
    presence["autopresence_enabled"] = True

    logs = cfg().setdefault("logs", {})
    if created.get("audit-memory"):
        logs["audit"] = created["audit-memory"].id
    if created.get("visual-feed"):
        logs["mirror"] = created["visual-feed"].id
    if created.get("containment-ward"):
        logs["debug"] = created["containment-ward"].id
        logs["ai"] = created["containment-ward"].id
        logs["voice"] = created["containment-ward"].id
    if created.get("diagnostics"):
        cfg().setdefault("diagnostics", {})["channel_id"] = created["diagnostics"].id
    if created.get("manual-for-living"):
        cfg().setdefault("manual", {})["channel_id"] = created["manual-for-living"].id
        cfg().setdefault("manual", {})["auto_upload_enabled"] = True

    channels_cfg = cfg().setdefault("command_channels", {})
    channels_cfg["user"] = "synaptic-gap"
    channels_cfg["god"] = "synaptic-gap"

    await STORE.mark_dirty()

    if guild.id == ADMIN_GUILD_ID:
        try:
            await ensure_roles(guild)
            await apply_guest_permissions(guild)
            await apply_quarantine_permissions(guild)
        except Exception:
            pass

    await ensure_menu_panels(guild)
    menu_hub = created.get("menu-hub")
    if menu_hub:
        await ensure_menu_panel(
            guild,
            "menu-hub",
            "user_menu_hub",
            "**Mandy Menu**\nUse the buttons below.",
            UserMenuView(0, timeout=None),
        )
        await ensure_menu_panel(
            guild,
            "menu-hub",
            "god_menu_hub",
            "**GOD MENU**\nGOD-only controls.",
            GodMenuView(0, timeout=None),
        )

    for g in bot.guilds:
        if g.id == ADMIN_GUILD_ID:
            continue
        try:
            await ensure_admin_server_channels(g)
        except Exception:
            continue

    rules = mirror_rules_dict()
    for rule in list(rules.values()):
        target_id = int(rule.get("target_channel", 0) or 0)
        target = bot.get_channel(target_id) if target_id else None
        if target_id and not target:
            try:
                target = await bot.fetch_channel(target_id)
            except Exception:
                target = None
        if target:
            continue
        src_gid = int(rule.get("source_guild", 0) or 0)
        if not src_gid:
            continue
        src_guild = bot.get_guild(src_gid)
        if not src_guild:
            continue
        try:
            mirror_feed, _ = await ensure_admin_server_channels(src_guild)
        except Exception:
            mirror_feed = None
        if mirror_feed:
            await mirror_rule_update(rule, target_channel=mirror_feed.id)

    if auto_backfill_enabled():
        for g in bot.guilds:
            if g.id == ADMIN_GUILD_ID:
                continue
            rule = find_server_scope_rule(g.id)
            if not rule or not rule.get("enabled", True):
                continue
            try:
                await backfill_mirror_for_guild(g, rule, force=False)
            except Exception:
                continue

    for bridge in await dm_bridge_list_active():
        uid = int(bridge.get("user_id", 0))
        if not uid:
            continue
        ch = await ensure_dm_bridge_channel(uid, active=True)
        if ch:
            await dm_bridge_set(uid, ch.id, active=True, last_activity=int(bridge.get("last_activity", 0) or now_ts()))

    await log_to("mirror", "Mirror sync complete", subsystem="SENSORY", severity="INFO")

async def run_setup_bio(guild: discord.Guild, actor_id: int) -> None:
    if guild.id != ADMIN_GUILD_ID:
        return
    await setup_log(f"BIO-GENESIS requested by {actor_id}")
    try:
        async with AUTO_SETUP_LOCK:
            system_log = await _setup_bio_preflight(guild)
            if not system_log:
                await setup_log("BIO-GENESIS aborted: recovery anchor failed.")
                return
            recovery = find_category_by_name(guild, "RECOVERY")
            if not recovery:
                await setup_log("BIO-GENESIS aborted: recovery category missing.")
                return
            await setup_log("BIO Phase 1/5: controlled wipe")
            wiped = await _setup_bio_wipe(guild, recovery.id)
            if not wiped:
                await setup_log("BIO-GENESIS aborted: cleanup incomplete.")
                return
            await setup_log("BIO Phase 2/5: build Sentient Core layout")
            created = await _setup_bio_build_layout(guild)
            await setup_log("BIO Phase 3/5: AI topics + pins (optional)")
            updates: Dict[str, Dict[str, str]] = {}
            try:
                updates = await _generate_bio_ai_updates(guild, created)
            except SetupAiRateLimitError:
                reply = await _await_ai_enhancement_response(actor_id)
                if reply in ("wait", "ai", "yes", "y"):
                    await asyncio.sleep(60)
                    try:
                        updates = await _generate_bio_ai_updates(guild, created)
                    except SetupAiRateLimitError:
                        updates = {}
            if updates:
                confirmed = await _confirm_bio_ai_updates(actor_id, updates)
                if confirmed:
                    await _apply_bio_ai_updates(guild, updates)
                    await setup_log("BIO AI enhancements applied.")
                else:
                    await setup_log("BIO AI enhancements skipped.")
            await setup_log("BIO Phase 4/5: alive upgrade + config")
            await _setup_bio_reseed_ops(guild, created)
            await setup_log("BIO Phase 5/5: legacy ops reseed")

            missing: List[str] = []
            for name in ("thoughts", "synaptic-gap", "menu-hub", "audit-memory", "visual-feed", "diagnostics"):
                if name not in created:
                    missing.append(name)
            recovery_ch = find_text_by_name(guild, "command-line")
            system_ch = find_text_by_name(guild, "system-log")
            if not recovery_ch:
                missing.append("command-line")
            if not system_ch:
                missing.append("system-log")

            visual = created.get("visual-feed")
            if visual:
                try:
                    perms = visual.permissions_for(guild.me)
                    if not perms.send_messages:
                        missing.append("visual-feed:send_messages")
                except Exception:
                    missing.append("visual-feed:perm_check")

            if missing:
                await setup_log("BIO verify missing: " + ", ".join(missing))

            await manual_upload_if_needed(force=True)
            msg = "BIO-GENESIS complete. Cortex online. Legacy ops synced. Mirrors standing by."
            if system_ch:
                try:
                    await system_ch.send(msg)
                except Exception:
                    pass
            try:
                await send_setup_debrief(trigger="bio")
            except Exception:
                pass
    except Exception as exc:
        await setup_log(f"BIO-GENESIS failed: {exc}")

async def bot_permissions_text(guild: discord.Guild) -> str:
    m = guild.me
    if not m:
        return "unknown"
    perms = m.guild_permissions
    missing = []
    for name in ("send_messages", "embed_links", "attach_files", "read_message_history", "manage_channels", "create_instant_invite"):
        if not getattr(perms, name, False):
            missing.append(name)
    if not missing:
        return "ok"
    return "missing: " + ", ".join(missing)

async def ensure_permanent_invite(guild: discord.Guild) -> Optional[str]:
    if not guild.me or not guild.me.guild_permissions.create_instant_invite:
        return None
    try:
        invites = await guild.invites()
        for inv in invites:
            if inv.max_age == 0 and inv.max_uses == 0:
                return inv.url
    except Exception:
        pass

    channel = guild.system_channel
    if not channel:
        for ch in guild.text_channels:
            channel = ch
            break
    if not channel:
        return None
    try:
        inv = await channel.create_invite(max_age=0, max_uses=0, unique=True, reason="Mandy server info")
        return inv.url
    except Exception:
        return None

async def update_server_info_for_guild(source_guild: discord.Guild):
    _, info_ch = await ensure_admin_server_channels(source_guild)
    if not info_ch:
        return

    rules = mirror_rules_dict()
    server_rule = find_server_scope_rule(source_guild.id)
    mirror_enabled = "ENABLED" if server_rule and server_rule.get("enabled") else "DISABLED"

    invite_url = await ensure_permanent_invite(source_guild)
    perms_text = await bot_permissions_text(source_guild)
    perms_admin = "YES" if source_guild.me and source_guild.me.guild_permissions.administrator else "NO"

    emb = discord.Embed(
        title="Server Info",
        description=f"{source_guild.name} (`{source_guild.id}`)",
        color=discord.Color.dark_gray()
    )
    emb.add_field(name="Owner ID", value=str(source_guild.owner_id or "unknown"), inline=False)
    emb.add_field(name="Members", value=str(source_guild.member_count), inline=True)
    emb.add_field(name="Mirror", value=mirror_enabled, inline=True)
    emb.add_field(name="Admin Perms", value=perms_admin, inline=True)
    emb.add_field(name="Permissions", value=perms_text, inline=False)
    emb.add_field(name="Invite", value=invite_url or "unavailable", inline=False)
    emb.set_footer(text="Mandy OS")

    msg_id = cfg().get("server_info_messages", {}).get(str(source_guild.id))
    if not msg_id:
        msg_id = cfg().get("server_status_messages", {}).get(str(source_guild.id))

    if msg_id:
        try:
            msg = await info_ch.fetch_message(int(msg_id))
            await msg.edit(embed=emb, content=None)
            try:
                if not msg.pinned:
                    await msg.pin()
            except Exception:
                pass
            return
        except Exception:
            pass

    msg = await info_ch.send(embed=emb)
    try:
        await msg.pin()
    except Exception:
        pass
    cfg().setdefault("server_info_messages", {})[str(source_guild.id)] = msg.id
    await STORE.mark_dirty()

async def dm_send_lines(user: discord.User, title: str, lines: List[str]):
    if not lines:
        try:
            await user.send(f"{title}\n(none)")
        except Exception:
            pass
        return

    chunk = title
    for line in lines:
        if len(chunk) + len(line) + 1 > 1900:
            try:
                await user.send(chunk)
            except Exception:
                return
            chunk = title
        chunk += "\n" + line
    if chunk:
        try:
            await user.send(chunk)
        except Exception:
            pass

async def send_setup_debrief(trigger: str = "fullsync"):
    try:
        user = bot.get_user(SUPER_USER_ID) or await bot.fetch_user(SUPER_USER_ID)
    except Exception:
        return
    if not user:
        return

    mysql_watchers = None
    if POOL:
        try:
            await ensure_watchers_columns()
            row = await db_one("SELECT COUNT(*) AS c FROM watchers")
            mysql_watchers = int(row["c"]) if row and row.get("c") is not None else 0
        except Exception:
            mysql_watchers = None

    mirror_rules = list(mirror_rules_dict().values())
    server_rules = [r for r in mirror_rules if r.get("scope") == "server"]
    other_rules = [r for r in mirror_rules if r.get("scope") != "server"]

    mirror_lines: List[str] = []
    issues = 0
    for g in sorted(bot.guilds, key=lambda x: x.name.lower()):
        invite = None
        try:
            invite = await ensure_permanent_invite(g)
        except Exception:
            invite = None
        await setup_pause()

        if g.id == ADMIN_GUILD_ID:
            mirror_lines.append(f"{g.name} ({g.id}) | mirror: admin | invite: {invite or 'unavailable'}")
            continue

        rule = find_server_scope_rule(g.id)
        if rule and rule.get("enabled", True):
            status = "on"
        elif rule:
            status = "off"
            issues += 1
        else:
            status = "missing"
            issues += 1

        target_label = "n/a"
        err = ""
        if rule:
            tgt = bot.get_channel(int(rule.get("target_channel", 0)))
            if isinstance(tgt, discord.TextChannel):
                target_label = f"{tgt.guild.name}/#{tgt.name}"
            else:
                target_label = str(rule.get("target_channel"))
            err = truncate(rule.get("last_error", ""), 80)

        err_part = f" | error: {err}" if err else ""
        mirror_lines.append(
            f"{g.name} ({g.id}) | mirror: {status} -> {target_label} | invite: {invite or 'unavailable'}{err_part}"
        )

    json_targets = cfg().get("targets", {})
    json_lines = []
    for uid, data in json_targets.items():
        json_lines.append(
            f"{uid} (<@{uid}>) | count={data.get('count', 0)} current={data.get('current', 0)} text={truncate(data.get('text', ''), 120)}"
        )

    mysql_lines: List[str] = []
    if POOL:
        try:
            has_current = await db_column_exists("watchers", "current")
            has_updated = await db_column_exists("watchers", "updated_at")
            cols = ["user_id", "threshold", "text"]
            if has_current:
                cols.insert(2, "current")
            order = "updated_at DESC" if has_updated else "user_id ASC"
            rows = await db_all(f"SELECT {', '.join(cols)} FROM watchers ORDER BY {order}")
            for row in rows:
                current = row.get("current", 0)
                mysql_lines.append(
                    f"{row['user_id']} (<@{row['user_id']}>) | count={row['threshold']} current={current} text={truncate(row['text'], 120)}"
                )
        except Exception:
            mysql_lines.append("(failed to read watchers from MySQL)")

    other_rule_lines = []
    for r in other_rules:
        status = "on" if r.get("enabled", True) else "off"
        err = truncate(r.get("last_error", ""), 80)
        err_part = f" | error: {err}" if err else ""
        other_rule_lines.append(f"{rule_summary(r)} ({status}){err_part}")

    summary = [
        f"Setup debrief ({trigger})",
        f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Guilds: {len(bot.guilds)}",
        f"MySQL: {'on' if POOL else 'off'}",
        f"Mirror rules: total={len(mirror_rules)} server={len(server_rules)} other={len(other_rules)} issues={issues}",
        f"Watchers: json={len(json_targets)} mysql={(mysql_watchers if mysql_watchers is not None else 'n/a')}",
    ]
    try:
        await user.send("\n".join(summary))
    except Exception:
        return

    await dm_send_lines(user, "Mirrors + invites:", mirror_lines)
    await dm_send_lines(user, "Watchers (JSON):", json_lines)
    if POOL:
        await dm_send_lines(user, "Watchers (MySQL):", mysql_lines)
    await dm_send_lines(user, "Mirror rules (category/channel):", other_rule_lines)

# -----------------------------
# Button Menus (User + GOD)
# -----------------------------
class BaseView(discord.ui.View):
    def __init__(self, author_id: int, timeout: int = 120):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.author_id and interaction.user.id != self.author_id:
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.message and self.author_id:
            try:
                await self.message.delete()
            except Exception:
                pass

class SetupDestructiveChoiceView(BaseView):
    def __init__(self, author_id: int):
        super().__init__(author_id, timeout=60)
        self.choice: Optional[str] = None

    async def on_timeout(self):
        return

    async def _finalize(self, interaction: discord.Interaction, choice: str):
        self.choice = choice
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(content=f"Destructive setup: {choice}", view=self)
        except Exception:
            pass
        self.stop()

    @discord.ui.button(label="Default Rebuild", style=discord.ButtonStyle.danger)
    async def default_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finalize(interaction, "destructive")

    @discord.ui.button(label="AI-Assisted Rebuild", style=discord.ButtonStyle.primary)
    async def ai_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finalize(interaction, "destructive_ai")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finalize(interaction, "cancel")

class SetupBioConfirmView(BaseView):
    def __init__(self, author_id: int):
        super().__init__(author_id, timeout=60)
        self.confirmed = False

    async def on_timeout(self):
        return

    async def _finalize(self, interaction: discord.Interaction, confirmed: bool, label: str):
        self.confirmed = confirmed
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(content=label, view=self)
        except Exception:
            pass
        self.stop()

    @discord.ui.button(label="Confirm BIO-GENESIS", style=discord.ButtonStyle.danger)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finalize(interaction, True, "BIO-GENESIS confirmed.")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finalize(interaction, False, "BIO-GENESIS cancelled.")

class SetupModeView(BaseView):
    def __init__(self, author_id: int):
        super().__init__(author_id, timeout=90)

    async def _start(self, interaction: discord.Interaction, mode: str):
        if mode in ("fullsync", "destructive") and not is_super(interaction.user.id):
            return await interaction.response.send_message("SUPERUSER only.", ephemeral=True)
        if mode == "destructive":
            choice = await prompt_setup_destructive_choice_with_channel(interaction.channel, interaction.user.id)
            if choice == "cancel":
                return await interaction.response.send_message("Destructive setup cancelled.", ephemeral=True)
            mode = choice
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(content=f"Setup starting: {mode}", view=self)
        except Exception:
            pass
        asyncio.create_task(run_full_setup(interaction.guild, mode, actor_id=interaction.user.id))
        await audit(interaction.user.id, "Setup run", {"mode": mode})

    @discord.ui.button(label="Bootstrap", style=discord.ButtonStyle.secondary)
    async def bootstrap_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._start(interaction, "bootstrap")

    @discord.ui.button(label="Fullsync", style=discord.ButtonStyle.primary)
    async def fullsync_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._start(interaction, "fullsync")

    @discord.ui.button(label="Destructive", style=discord.ButtonStyle.danger)
    async def destructive_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._start(interaction, "destructive")

async def user_status_text(user: discord.abc.User) -> str:
    parts = []
    g = cfg().get("gate", {})
    if str(user.id) in g:
        parts.append(f"Gate: pending (tries {g[str(user.id)].get('tries', 0)})")

    if isinstance(user, discord.Member):
        roles = [r.name for r in user.roles if r.name != "@everyone"]
        parts.append("Roles: " + (", ".join(roles) if roles else "none"))

    dm_bridge = await dm_bridge_channel_for_user(user.id)
    parts.append("DM bridge: " + ("active" if dm_bridge else "inactive"))

    return "\n".join(parts) if parts else "No status available."

class UserMenuView(BaseView):
    @discord.ui.button(label="Bot Status", style=discord.ButtonStyle.primary)
    async def bot_status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        lvl = await effective_level(interaction.user)
        if not (lvl >= 50 or is_super(interaction.user.id)):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        await interaction.response.send_message("Set bot status:", view=BotStatusView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Help", style=discord.ButtonStyle.primary)
    async def help_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Use `!menu` for user tools and `!godmenu` for admin tools.",
            ephemeral=True
        )

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary)
    async def status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        text = await user_status_text(interaction.user)
        await interaction.response.send_message(text, ephemeral=True)

    @discord.ui.button(label="DM Visibility", style=discord.ButtonStyle.success)
    async def dm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        ch_id = await dm_bridge_channel_for_user(interaction.user.id)
        if ch_id:
            await interaction.response.send_message("Your DM bridge is active (staff-visible).", ephemeral=True)
        else:
            await interaction.response.send_message("No DM bridge is active for you.", ephemeral=True)

class BotStatusTextModal(discord.ui.Modal):
    def __init__(self, state: str):
        super().__init__(title="Bot Status Text")
        self.state = state
        self.text = discord.ui.TextInput(
            label="Custom status text (optional)",
            max_length=120,
            required=False
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        await set_bot_status(self.state, str(self.text.value or ""))
        await audit(interaction.user.id, "Bot status set", {"state": self.state, "text": str(self.text.value or "")})
        await interaction.response.send_message("Bot status updated.", ephemeral=True)

class BotStatusView(BaseView):
    def __init__(self, author_id: int):
        super().__init__(author_id, timeout=120)
        opts = [
            discord.SelectOption(label="Online", value="online"),
            discord.SelectOption(label="Idle", value="idle"),
            discord.SelectOption(label="Do Not Disturb", value="dnd"),
            discord.SelectOption(label="Invisible", value="invisible"),
        ]
        sel = discord.ui.Select(placeholder="Select status", options=opts, min_values=1, max_values=1)
        sel.callback = self.status_selected
        self.add_item(sel)

    async def status_selected(self, interaction: discord.Interaction):
        state = interaction.data["values"][0]
        await interaction.response.send_modal(BotStatusTextModal(state))

class PermissionMenuView(BaseView):
    def __init__(self, author_id: int):
        super().__init__(author_id)
        self.target: Optional[discord.User] = None
        self.level: Optional[int] = None

        self.user_select = discord.ui.UserSelect(placeholder="Select user")
        self.user_select.callback = self.user_selected
        self.add_item(self.user_select)

        opts = [discord.SelectOption(label=str(lvl), value=str(lvl)) for lvl in (0, 10, 50, 70, 90, 100)]
        self.level_select = discord.ui.Select(placeholder="Select level", options=opts)
        self.level_select.callback = self.level_selected
        self.add_item(self.level_select)

    async def user_selected(self, interaction: discord.Interaction):
        self.target = self.user_select.values[0]
        await interaction.response.edit_message(content=f"User: {self.target} | Level: {self.level}", view=self)

    async def level_selected(self, interaction: discord.Interaction):
        self.level = int(self.level_select.values[0])
        await interaction.response.edit_message(content=f"User: {self.target} | Level: {self.level}", view=self)

    @discord.ui.button(label="Apply", style=discord.ButtonStyle.success)
    async def apply_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.target or self.level is None:
            return await interaction.response.send_message("Select user + level.", ephemeral=True)
        if self.target.id == SUPER_USER_ID:
            return await interaction.response.send_message("SUPERUSER cannot be changed.", ephemeral=True)
        if self.level >= 90 and not is_super(interaction.user.id):
            return await interaction.response.send_message("Only SUPERUSER can assign 90+.", ephemeral=True)

        if POOL:
            await db_exec("""
            INSERT INTO users_permissions (user_id, level, note)
            VALUES (%s,%s,%s)
            ON DUPLICATE KEY UPDATE level=VALUES(level);
            """, (self.target.id, max(0, min(100, self.level)), "set via godmenu"))
        else:
            cfg().setdefault("permissions", {})[str(self.target.id)] = max(0, min(100, self.level))
            await STORE.mark_dirty()

        await audit(interaction.user.id, "Perm set", {"user_id": self.target.id, "level": self.level})
        await interaction.response.send_message(f"Set {self.target} -> {self.level}", ephemeral=True)

class MirrorServerToggleView(BaseView):
    def __init__(self, author_id: int, page: int = 0):
        super().__init__(author_id)
        self.page = page
        self.guilds = sorted(
            [g for g in bot.guilds if g.id != ADMIN_GUILD_ID],
            key=lambda g: g.name.lower()
        )
        self._build()

    def _build(self):
        start = self.page * 25
        end = start + 25
        page_guilds = self.guilds[start:end]
        options = []
        for g in page_guilds:
            rule = find_server_scope_rule(g.id)
            status = "ENABLED" if rule and rule.get("enabled") else "DISABLED"
            options.append(discord.SelectOption(
                label=g.name[:100],
                value=str(g.id),
                description=status
            ))
        if options:
            select = discord.ui.Select(
                placeholder="Select server to toggle",
                options=options,
                min_values=1,
                max_values=1
            )
            select.callback = self._toggle_selected
            self.add_item(select)
        if self.page > 0:
            prev_btn = discord.ui.Button(label="Prev", style=discord.ButtonStyle.secondary)
            prev_btn.callback = self._prev_page
            self.add_item(prev_btn)
        if end < len(self.guilds):
            next_btn = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary)
            next_btn.callback = self._next_page
            self.add_item(next_btn)

    async def _toggle_selected(self, interaction: discord.Interaction):
        gid = int(interaction.data["values"][0])
        guild = bot.get_guild(gid)
        if not guild:
            return await interaction.response.send_message("Server not found.", ephemeral=True)
        rule = await ensure_server_mirror_rule(guild)
        if not rule:
            return await interaction.response.send_message("Rule unavailable.", ephemeral=True)
        rule["enabled"] = not bool(rule.get("enabled", False))
        await mirror_rule_save(rule)
        await update_server_info_for_guild(guild)
        status = "ENABLED" if rule.get("enabled") else "DISABLED"
        await interaction.response.edit_message(
            content=f"Server mirror for {guild.name}: {status}",
            view=MirrorServerToggleView(self.author_id, self.page)
        )

    async def _prev_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=MirrorServerToggleView(self.author_id, self.page - 1))

    async def _next_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=MirrorServerToggleView(self.author_id, self.page + 1))

class CategoryMirrorServerSelectView(BaseView):
    def __init__(self, author_id: int, page: int = 0):
        super().__init__(author_id)
        self.page = page
        self.guilds = sorted(
            [g for g in bot.guilds if g.id != ADMIN_GUILD_ID],
            key=lambda g: g.name.lower()
        )
        self._build()

    def _build(self):
        start = self.page * 25
        end = start + 25
        page_guilds = self.guilds[start:end]
        options = [
            discord.SelectOption(label=g.name[:100], value=str(g.id))
            for g in page_guilds
        ]
        if options:
            select = discord.ui.Select(
                placeholder="Select server",
                options=options,
                min_values=1,
                max_values=1
            )
            select.callback = self._guild_selected
            self.add_item(select)
        use_btn = discord.ui.Button(label="Use this server", style=discord.ButtonStyle.success)
        use_btn.callback = self._use_current
        self.add_item(use_btn)
        if self.page > 0:
            prev_btn = discord.ui.Button(label="Prev", style=discord.ButtonStyle.secondary)
            prev_btn.callback = self._prev_page
            self.add_item(prev_btn)
        if end < len(self.guilds):
            next_btn = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary)
            next_btn.callback = self._next_page
            self.add_item(next_btn)

    async def _guild_selected(self, interaction: discord.Interaction):
        gid = int(interaction.data["values"][0])
        await interaction.response.edit_message(
            content="Select category to toggle.",
            view=CategoryMirrorSelectView(self.author_id, gid)
        )

    async def _use_current(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("No server context.", ephemeral=True)
        await interaction.response.edit_message(
            content="Select category to toggle.",
            view=CategoryMirrorSelectView(self.author_id, interaction.guild.id)
        )

    async def _prev_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=CategoryMirrorServerSelectView(self.author_id, self.page - 1))

    async def _next_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=CategoryMirrorServerSelectView(self.author_id, self.page + 1))

class CategoryMirrorSelectView(BaseView):
    def __init__(self, author_id: int, guild_id: int, page: int = 0):
        super().__init__(author_id)
        self.guild_id = guild_id
        self.page = page
        self.guild = bot.get_guild(guild_id)
        self.categories = self.guild.categories if self.guild else []
        self._build()

    def _build(self):
        start = self.page * 25
        end = start + 25
        options = []
        for c in self.categories[start:end]:
            options.append(discord.SelectOption(label=c.name[:100], value=str(c.id)))
        if options:
            sel = discord.ui.Select(placeholder="Select category", options=options, min_values=1, max_values=1)
            sel.callback = self._category_selected
            self.add_item(sel)
        if self.page > 0:
            prev_btn = discord.ui.Button(label="Prev", style=discord.ButtonStyle.secondary)
            prev_btn.callback = self._prev_page
            self.add_item(prev_btn)
        if end < len(self.categories):
            next_btn = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary)
            next_btn.callback = self._next_page
            self.add_item(next_btn)

    async def _category_selected(self, interaction: discord.Interaction):
        cat_id = int(interaction.data["values"][0])
        guild = bot.get_guild(self.guild_id)
        if not guild:
            return await interaction.response.send_message("Server not found.", ephemeral=True)
        mirror_feed, _ = await ensure_admin_server_channels(guild)
        if not mirror_feed:
            return await interaction.response.send_message(voice_line(cfg(), "err_mirror_feed_missing"), ephemeral=True)
        rule = find_category_rule(cat_id, mirror_feed.id)
        if not rule:
            rule = {
                "rule_id": make_rule_id("category", cat_id, mirror_feed.id),
                "scope": "category",
                "source_guild": guild.id,
                "source_id": cat_id,
                "target_channel": mirror_feed.id,
                "enabled": True,
                "fail_count": 0
            }
            await mirror_rule_save(rule)
        else:
            rule["enabled"] = not bool(rule.get("enabled", False))
            await mirror_rule_save(rule)
        status = "ENABLED" if rule.get("enabled") else "DISABLED"
        await interaction.response.edit_message(content=f"Category mirror: {status}", view=None)

    async def _prev_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=CategoryMirrorSelectView(self.author_id, self.guild_id, self.page - 1))

    async def _next_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=CategoryMirrorSelectView(self.author_id, self.guild_id, self.page + 1))

class MirrorMenuView(BaseView):
    @discord.ui.button(label="Server Mirrors", style=discord.ButtonStyle.primary)
    async def server_mirrors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Server mirror toggles:",
            view=MirrorServerToggleView(interaction.user.id),
            ephemeral=True
        )

    @discord.ui.button(label="Category Mirrors", style=discord.ButtonStyle.primary)
    async def category_mirrors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Select a server for category mirrors.",
            view=CategoryMirrorServerSelectView(interaction.user.id),
            ephemeral=True
        )

    @discord.ui.button(label="Add Rule", style=discord.ButtonStyle.primary)
    async def add_rule(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Choose scope for the mirror rule.",
            view=MirrorScopeView(interaction.user.id),
            ephemeral=True
        )

    @discord.ui.button(label="Disable Rule", style=discord.ButtonStyle.danger)
    async def disable_rule(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not mirror_rules_dict():
            return await interaction.response.send_message("No mirror rules.", ephemeral=True)
        view = MirrorDisableView(interaction.user.id)
        await interaction.response.send_message("Select a rule to disable.", view=view, ephemeral=True)

    @discord.ui.button(label="List Rules", style=discord.ButtonStyle.secondary)
    async def list_rules(self, interaction: discord.Interaction, button: discord.ui.Button):
        rules = mirror_rules_dict().values()
        if not rules:
            return await interaction.response.send_message("No mirror rules.", ephemeral=True)
        lines = []
        for r in list(rules)[:15]:
            lines.append(f"- {rule_summary(r)} ({'on' if r.get('enabled', True) else 'off'})")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

class MirrorScopeView(BaseView):
    def __init__(self, author_id: int):
        super().__init__(author_id)
        opts = [
            discord.SelectOption(label="Server", value="server"),
            discord.SelectOption(label="Category", value="category"),
            discord.SelectOption(label="Channel", value="channel"),
        ]
        sel = discord.ui.Select(placeholder="Scope", options=opts, min_values=1, max_values=1)
        sel.callback = self.scope_selected
        self.add_item(sel)

    async def scope_selected(self, interaction: discord.Interaction):
        scope = interaction.data["values"][0]
        await interaction.response.edit_message(
            content="Select source server.",
            view=MirrorGuildView(interaction.user.id, scope)
        )

class MirrorGuildView(BaseView):
    def __init__(self, author_id: int, scope: str, page: int = 0):
        super().__init__(author_id)
        self.scope = scope
        self.page = page
        self.guilds = sorted(bot.guilds, key=lambda g: g.name.lower())
        self._build()

    def _build(self):
        start = self.page * 25
        end = start + 25
        opts = [discord.SelectOption(label=g.name[:100], value=str(g.id)) for g in self.guilds[start:end]]
        if opts:
            sel = discord.ui.Select(placeholder="Source server", options=opts, min_values=1, max_values=1)
            sel.callback = self.guild_selected
            self.add_item(sel)
        if self.page > 0:
            prev_btn = discord.ui.Button(label="Prev", style=discord.ButtonStyle.secondary)
            prev_btn.callback = self._prev_page
            self.add_item(prev_btn)
        if end < len(self.guilds):
            next_btn = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary)
            next_btn.callback = self._next_page
            self.add_item(next_btn)
        if self.guilds:
            use_btn = discord.ui.Button(label="Use this server", style=discord.ButtonStyle.success)
            use_btn.callback = self._use_current
            self.add_item(use_btn)

    async def guild_selected(self, interaction: discord.Interaction):
        gid = int(interaction.data["values"][0])
        if self.scope == "server":
            await interaction.response.edit_message(
                content="Select target channel (admin server).",
                view=MirrorTargetView(interaction.user.id, self.scope, gid, gid)
            )
            return
        await interaction.response.edit_message(
            content="Select source category/channel.",
            view=MirrorSourceView(interaction.user.id, self.scope, gid)
        )

    async def _prev_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=MirrorGuildView(self.author_id, self.scope, self.page - 1))

    async def _next_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=MirrorGuildView(self.author_id, self.scope, self.page + 1))

    async def _use_current(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("No server context.", ephemeral=True)
        gid = interaction.guild.id
        if self.scope == "server":
            await interaction.response.edit_message(
                content="Select target channel (admin server).",
                view=MirrorTargetView(interaction.user.id, self.scope, gid, gid)
            )
            return
        await interaction.response.edit_message(
            content="Select source category/channel.",
            view=MirrorSourceView(interaction.user.id, self.scope, gid)
        )

class MirrorSourceView(BaseView):
    def __init__(self, author_id: int, scope: str, guild_id: int, page: int = 0):
        super().__init__(author_id)
        self.scope = scope
        self.guild_id = guild_id
        self.page = page
        self.guild = bot.get_guild(guild_id)
        if self.guild:
            if scope == "category":
                self.items = list(self.guild.categories)
            else:
                self.items = list(self.guild.text_channels)
        else:
            self.items = []
        self._build()

    def _build(self):
        start = self.page * 25
        end = start + 25
        options = []
        for c in self.items[start:end]:
            label = c.name if self.scope == "category" else f"#{c.name}"
            options.append(discord.SelectOption(label=label[:100], value=str(c.id)))
        if options:
            sel = discord.ui.Select(placeholder="Source", options=options, min_values=1, max_values=1)
            sel.callback = self.source_selected
            self.add_item(sel)
        if self.page > 0:
            prev_btn = discord.ui.Button(label="Prev", style=discord.ButtonStyle.secondary)
            prev_btn.callback = self._prev_page
            self.add_item(prev_btn)
        if end < len(self.items):
            next_btn = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary)
            next_btn.callback = self._next_page
            self.add_item(next_btn)

    async def source_selected(self, interaction: discord.Interaction):
        src_id = int(interaction.data["values"][0])
        await interaction.response.edit_message(
            content="Select target channel (admin server).",
            view=MirrorTargetView(interaction.user.id, self.scope, self.guild_id, src_id)
        )

    async def _prev_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=MirrorSourceView(self.author_id, self.scope, self.guild_id, self.page - 1))

    async def _next_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=MirrorSourceView(self.author_id, self.scope, self.guild_id, self.page + 1))

class MirrorTargetView(BaseView):
    def __init__(self, author_id: int, scope: str, guild_id: int, source_id: int):
        super().__init__(author_id)
        self.scope = scope
        self.guild_id = guild_id
        self.source_id = source_id
        sel = discord.ui.ChannelSelect(
            placeholder="Target channel (admin server)",
            channel_types=[discord.ChannelType.text]
        )
        sel.callback = self.target_selected
        self.add_item(sel)

    async def target_selected(self, interaction: discord.Interaction):
        target = int(interaction.data["values"][0])
        target_ch = bot.get_channel(target)
        if not target_ch or not isinstance(target_ch, discord.TextChannel):
            return await interaction.response.send_message("Target channel not found.", ephemeral=True)
        if target_ch.guild.id != ADMIN_GUILD_ID:
            return await interaction.response.send_message("Target must be in admin server.", ephemeral=True)
        rule_id = make_rule_id(self.scope, self.source_id, target)
        rule = {
            "rule_id": rule_id,
            "scope": self.scope,
            "source_guild": self.guild_id,
            "source_id": self.source_id,
            "target_channel": target,
            "enabled": True,
            "fail_count": 0
        }
        await mirror_rule_save(rule)
        if self.scope == "server":
            guild = bot.get_guild(self.guild_id)
            if guild:
                await update_server_info_for_guild(guild)
        await audit(interaction.user.id, "Mirror rule add", rule)
        await interaction.response.edit_message(content=f"Rule created: {rule_summary(rule)}", view=None)

class MirrorDisableView(BaseView):
    def __init__(self, author_id: int, page: int = 0):
        super().__init__(author_id)
        self.page = page
        self.rules = list(mirror_rules_dict().values())
        self._build()

    def _build(self):
        start = self.page * 25
        end = start + 25
        opts = []
        for r in self.rules[start:end]:
            label = rule_summary(r)
            opts.append(discord.SelectOption(label=label[:100], value=r["rule_id"]))
        if opts:
            sel = discord.ui.Select(placeholder="Select rule", options=opts, min_values=1, max_values=1)
            sel.callback = self.rule_selected
            self.add_item(sel)
        if self.page > 0:
            prev_btn = discord.ui.Button(label="Prev", style=discord.ButtonStyle.secondary)
            prev_btn.callback = self._prev_page
            self.add_item(prev_btn)
        if end < len(self.rules):
            next_btn = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary)
            next_btn.callback = self._next_page
            self.add_item(next_btn)

    async def rule_selected(self, interaction: discord.Interaction):
        rid = interaction.data["values"][0]
        rule = mirror_rules_dict().get(rid)
        if not rule:
            return await interaction.response.send_message("Rule not found.", ephemeral=True)
        await mirror_rule_disable(rule, "disabled via menu")
        if rule.get("scope") == "server":
            guild = bot.get_guild(int(rule.get("source_guild", 0)))
            if guild:
                await update_server_info_for_guild(guild)
        await audit(interaction.user.id, "Mirror rule disable", {"rule_id": rid})
        await interaction.response.edit_message(content="Rule disabled.", view=None)

    async def _prev_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=MirrorDisableView(self.author_id, self.page - 1))

    async def _next_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=MirrorDisableView(self.author_id, self.page + 1))

class WatcherConfigModal(discord.ui.Modal):
    def __init__(self, mode: str, user_id: int):
        super().__init__(title=f"{mode.upper()} Watcher")
        self.mode = mode
        self.user_id = int(user_id)
        self.count = discord.ui.TextInput(label="Count/Threshold", required=True, max_length=6)
        self.text = discord.ui.TextInput(label="Text (use | for alts)", style=discord.TextStyle.paragraph)
        self.add_item(self.count)
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            count = int(self.count.value.strip())
        except Exception:
            return await interaction.response.send_message("Bad format.", ephemeral=True)
        text = str(self.text.value or "")

        if self.mode == "json":
            cfg().setdefault("targets", {})[str(self.user_id)] = {"count": count, "current": 0, "text": text}
            await STORE.mark_dirty()
            await audit(interaction.user.id, "Watcher set (json)", {"user_id": self.user_id, "count": count})
            return await interaction.response.send_message("JSON watcher saved.", ephemeral=True)

        if not POOL:
            return await interaction.response.send_message("MySQL not enabled.", ephemeral=True)

        await db_exec("""
        INSERT INTO watchers (user_id, threshold, current, text)
        VALUES (%s,%s,0,%s)
        ON DUPLICATE KEY UPDATE threshold=VALUES(threshold), text=VALUES(text);
        """, (self.user_id, count, text))
        await audit(interaction.user.id, "Watcher set (mysql)", {"user_id": self.user_id, "threshold": count})
        await interaction.response.send_message("MySQL watcher saved.", ephemeral=True)

async def remove_watcher(mode: str, user_id: int, actor_id: int) -> str:
    if mode == "json":
        targets = cfg().get("targets", {})
        if str(user_id) not in targets:
            return "JSON watcher not found."
        targets.pop(str(user_id), None)
        await STORE.mark_dirty()
        await audit(actor_id, "Watcher removed (json)", {"user_id": user_id})
        return "JSON watcher removed."

    if not POOL:
        return "MySQL not enabled."
    await db_exec("DELETE FROM watchers WHERE user_id=%s", (user_id,))
    await audit(actor_id, "Watcher removed (mysql)", {"user_id": user_id})
    return "MySQL watcher removed."

async def send_watcher_list(interaction: discord.Interaction, mode: str):
    def fmt(uid, count, current, text):
        return f"{uid} (<@{uid}>) | count={count} current={current} text={truncate(text, 120)}"

    lines: List[str] = []
    if mode == "json":
        targets = cfg().get("targets", {})
        for uid, data in targets.items():
            lines.append(fmt(uid, data.get("count", 0), data.get("current", 0), data.get("text", "")))
    else:
        if not POOL:
            return await interaction.response.send_message("MySQL not enabled.", ephemeral=True)
        rows = await db_all("SELECT user_id, threshold, current, text FROM watchers ORDER BY updated_at DESC LIMIT 25")
        for row in rows:
            lines.append(fmt(row["user_id"], row["threshold"], row["current"], row["text"]))

    if not lines:
        return await interaction.response.send_message("No watchers found.", ephemeral=True)

    header = f"{mode.upper()} watchers ({len(lines)}):"
    chunks: List[str] = []
    cur = header
    for line in lines:
        if len(cur) + len(line) + 1 > 1900:
            chunks.append(cur)
            cur = header
        cur += "\n" + line
    if cur:
        chunks.append(cur)

    await interaction.response.send_message(chunks[0], ephemeral=True)
    for extra in chunks[1:]:
        await interaction.followup.send(extra, ephemeral=True)

class WatcherMenuView(BaseView):
    def __init__(self, author_id: int, guild_id: Optional[int] = None, page: int = 0):
        super().__init__(author_id)
        self.guild_id = guild_id
        self.page = page
        self.target: Optional[int] = None
        self._build()

    def _build(self):
        guilds = sorted(bot.guilds, key=lambda g: g.name.lower())
        guild_opts = [discord.SelectOption(label=g.name[:100], value=str(g.id)) for g in guilds[:25]]
        if guild_opts:
            sel = discord.ui.Select(placeholder="Select server", options=guild_opts, min_values=1, max_values=1)
            sel.callback = self.guild_selected
            self.add_item(sel)
        use_btn = discord.ui.Button(label="Use this server", style=discord.ButtonStyle.secondary)
        use_btn.callback = self.use_current
        self.add_item(use_btn)

        if not self.guild_id:
            return
        guild = bot.get_guild(self.guild_id)
        if not guild:
            return
        members = [m for m in guild.members]
        members.sort(key=lambda m: m.display_name.lower())
        start = self.page * 25
        end = start + 25
        page_members = members[start:end]
        member_opts = [
            discord.SelectOption(label=f"{m.display_name}"[:100], value=str(m.id))
            for m in page_members
        ]
        if member_opts:
            msel = discord.ui.Select(placeholder="Select user", options=member_opts, min_values=1, max_values=1)
            msel.callback = self.user_selected
            self.add_item(msel)
        if self.page > 0:
            prev_btn = discord.ui.Button(label="Prev Users", style=discord.ButtonStyle.secondary)
            prev_btn.callback = self.prev_page
            self.add_item(prev_btn)
        if end < len(members):
            next_btn = discord.ui.Button(label="Next Users", style=discord.ButtonStyle.secondary)
            next_btn.callback = self.next_page
            self.add_item(next_btn)

    async def guild_selected(self, interaction: discord.Interaction):
        gid = int(interaction.data["values"][0])
        await interaction.response.edit_message(
            content="Select user.",
            view=WatcherMenuView(self.author_id, guild_id=gid, page=0)
        )

    async def use_current(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("No server context.", ephemeral=True)
        await interaction.response.edit_message(
            content="Select user.",
            view=WatcherMenuView(self.author_id, guild_id=interaction.guild.id, page=0)
        )

    async def user_selected(self, interaction: discord.Interaction):
        self.target = int(interaction.data["values"][0])
        await interaction.response.edit_message(content=f"Selected: <@{self.target}>", view=self)

    async def prev_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            view=WatcherMenuView(self.author_id, guild_id=self.guild_id, page=self.page - 1)
        )

    async def next_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            view=WatcherMenuView(self.author_id, guild_id=self.guild_id, page=self.page + 1)
        )

    @discord.ui.button(label="Set JSON Watcher", style=discord.ButtonStyle.primary)
    async def json_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.target:
            return await interaction.response.send_message("Select a user.", ephemeral=True)
        await interaction.response.send_modal(WatcherConfigModal("json", self.target))

    @discord.ui.button(label="Set MySQL Watcher", style=discord.ButtonStyle.secondary)
    async def mysql_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.target:
            return await interaction.response.send_message("Select a user.", ephemeral=True)
        await interaction.response.send_modal(WatcherConfigModal("mysql", self.target))

    @discord.ui.button(label="List JSON Watchers", style=discord.ButtonStyle.success)
    async def list_json_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await send_watcher_list(interaction, "json")

    @discord.ui.button(label="List MySQL Watchers", style=discord.ButtonStyle.success)
    async def list_mysql_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await send_watcher_list(interaction, "mysql")

    @discord.ui.button(label="Remove JSON Watcher", style=discord.ButtonStyle.danger)
    async def remove_json_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.target:
            return await interaction.response.send_message("Select a user.", ephemeral=True)
        msg = await remove_watcher("json", self.target, interaction.user.id)
        await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="Remove MySQL Watcher", style=discord.ButtonStyle.danger)
    async def remove_mysql_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.target:
            return await interaction.response.send_message("Select a user.", ephemeral=True)
        msg = await remove_watcher("mysql", self.target, interaction.user.id)
        await interaction.response.send_message(msg, ephemeral=True)

class DmBridgeMenuView(BaseView):
    def __init__(self, author_id: int, guild_id: Optional[int] = None, page: int = 0):
        super().__init__(author_id)
        self.guild_id = guild_id
        self.page = page
        self.target: Optional[int] = None
        self._build()

    def _build(self):
        guilds = sorted(bot.guilds, key=lambda g: g.name.lower())
        guild_opts = [discord.SelectOption(label=g.name[:100], value=str(g.id)) for g in guilds[:25]]
        if guild_opts:
            sel = discord.ui.Select(placeholder="Select server", options=guild_opts, min_values=1, max_values=1)
            sel.callback = self.guild_selected
            self.add_item(sel)
        use_btn = discord.ui.Button(label="Use this server", style=discord.ButtonStyle.secondary)
        use_btn.callback = self.use_current
        self.add_item(use_btn)

        if not self.guild_id:
            return
        guild = bot.get_guild(self.guild_id)
        if not guild:
            return
        members = [m for m in guild.members]
        members.sort(key=lambda m: m.display_name.lower())
        start = self.page * 25
        end = start + 25
        page_members = members[start:end]
        member_opts = [
            discord.SelectOption(label=f"{m.display_name}"[:100], value=str(m.id))
            for m in page_members
        ]
        if member_opts:
            msel = discord.ui.Select(placeholder="Select user", options=member_opts, min_values=1, max_values=1)
            msel.callback = self.user_selected
            self.add_item(msel)
        if self.page > 0:
            prev_btn = discord.ui.Button(label="Prev Users", style=discord.ButtonStyle.secondary)
            prev_btn.callback = self.prev_page
            self.add_item(prev_btn)
        if end < len(members):
            next_btn = discord.ui.Button(label="Next Users", style=discord.ButtonStyle.secondary)
            next_btn.callback = self.next_page
            self.add_item(next_btn)

    async def guild_selected(self, interaction: discord.Interaction):
        gid = int(interaction.data["values"][0])
        await interaction.response.edit_message(
            content="Select user.",
            view=DmBridgeMenuView(self.author_id, guild_id=gid, page=0)
        )

    async def use_current(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("No server context.", ephemeral=True)
        await interaction.response.edit_message(
            content="Select user.",
            view=DmBridgeMenuView(self.author_id, guild_id=interaction.guild.id, page=0)
        )

    async def user_selected(self, interaction: discord.Interaction):
        self.target = int(interaction.data["values"][0])
        await interaction.response.edit_message(content=f"Selected: <@{self.target}>", view=self)

    async def prev_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            view=DmBridgeMenuView(self.author_id, guild_id=self.guild_id, page=self.page - 1)
        )

    async def next_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            view=DmBridgeMenuView(self.author_id, guild_id=self.guild_id, page=self.page + 1)
        )

    @discord.ui.button(label="Open Bridge", style=discord.ButtonStyle.success)
    async def open_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.target:
            return await interaction.response.send_message("Select a user.", ephemeral=True)
        if not interaction.guild or interaction.guild.id != ADMIN_GUILD_ID:
            return await interaction.response.send_message("Run in admin server.", ephemeral=True)
        ch_id = await ensure_dm_bridge_active(self.target, reason="manual")
        if not ch_id:
            return await interaction.response.send_message("Failed to open bridge.", ephemeral=True)
        await audit(interaction.user.id, "DM bridge open", {"user_id": self.target, "channel_id": ch_id})
        ch = bot.get_channel(ch_id)
        await interaction.response.send_message(f"Opened: {ch.mention if ch else ch_id}", ephemeral=True)

    @discord.ui.button(label="Close Bridge", style=discord.ButtonStyle.danger)
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.target:
            return await interaction.response.send_message("Select a user.", ephemeral=True)
        await dm_bridge_close(self.target)
        await audit(interaction.user.id, "DM bridge close", {"user_id": self.target})
        await interaction.response.send_message("Closed.", ephemeral=True)

class LoggingMenuView(BaseView):
    def __init__(self, author_id: int):
        super().__init__(author_id)
        self.which = None
        opts = [
            discord.SelectOption(label="system", value="system"),
            discord.SelectOption(label="audit", value="audit"),
            discord.SelectOption(label="debug", value="debug"),
            discord.SelectOption(label="mirror", value="mirror")
        ]
        sel = discord.ui.Select(placeholder="Log type", options=opts)
        sel.callback = self.which_selected
        self.add_item(sel)

        ch_sel = discord.ui.ChannelSelect(placeholder="Log channel", channel_types=[discord.ChannelType.text])
        ch_sel.callback = self.channel_selected
        self.add_item(ch_sel)

    async def which_selected(self, interaction: discord.Interaction):
        self.which = interaction.data["values"][0]
        await interaction.response.edit_message(content=f"Log type: {self.which}", view=self)

    async def channel_selected(self, interaction: discord.Interaction):
        if not self.which:
            return await interaction.response.send_message("Pick log type first.", ephemeral=True)
        cid = int(interaction.data["values"][0])
        cfg().setdefault("logs", {})[self.which] = cid
        await STORE.mark_dirty()
        await audit(interaction.user.id, "Log channel set", {"which": self.which, "channel_id": cid})
        await interaction.response.send_message("Updated.", ephemeral=True)


class CommandChannelModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Command channel binding")
        channels = cfg().get("command_channels", {})
        self.user_channel = discord.ui.TextInput(
            label="User command channel",
            default=str(channels.get("user", "command-requests")),
            max_length=80,
        )
        self.god_channel = discord.ui.TextInput(
            label="GOD command channel",
            default=str(channels.get("god", "admin-chat")),
            max_length=80,
        )
        self.add_item(self.user_channel)
        self.add_item(self.god_channel)

    async def on_submit(self, interaction: discord.Interaction):
        channels = cfg().setdefault("command_channels", {})
        channels["user"] = str(self.user_channel.value or "command-requests").strip()
        channels["god"] = str(self.god_channel.value or "admin-chat").strip()
        await STORE.mark_dirty()
        await audit(interaction.user.id, "Command channels updated", {"user": channels["user"], "god": channels["god"]})
        await interaction.response.send_message("Channels updated.", ephemeral=True)


class CommandChannelView(BaseView):
    def __init__(self, author_id: int):
        super().__init__(author_id)

    def _summary(self) -> str:
        channels = cfg().get("command_channels", {})
        mode = channels.get("mode", "off")
        user_ch = channels.get("user", "command-requests")
        god_ch = channels.get("god", "admin-chat")
        desc = {
            "off": "Allow commands anywhere.",
            "soft": "Remind + forward snippet, keep message.",
            "hard": "Delete + forward snippet to target channel.",
        }.get(mode, "Allow commands anywhere.")
        return (
            "**Command Routing**\n"
            f"- Mode: {mode} ({desc})\n"
            f"- User channel: {user_ch}\n"
            f"- GOD channel: {god_ch}\n"
            "Use the buttons to change mode or rename channels."
        )

    @discord.ui.button(label="Allow anywhere", style=discord.ButtonStyle.success)
    async def allow_anywhere(self, interaction: discord.Interaction, button: discord.ui.Button):
        channels = cfg().setdefault("command_channels", {})
        channels["mode"] = "off"
        await STORE.mark_dirty()
        await audit(interaction.user.id, "Command routing mode", {"mode": "off"})
        await interaction.response.edit_message(content=self._summary(), view=self)

    @discord.ui.button(label="Soft remind", style=discord.ButtonStyle.secondary)
    async def soft_enforce(self, interaction: discord.Interaction, button: discord.ui.Button):
        channels = cfg().setdefault("command_channels", {})
        channels["mode"] = "soft"
        await STORE.mark_dirty()
        await audit(interaction.user.id, "Command routing mode", {"mode": "soft"})
        await interaction.response.edit_message(content=self._summary(), view=self)

    @discord.ui.button(label="Hard enforce", style=discord.ButtonStyle.danger)
    async def hard_enforce(self, interaction: discord.Interaction, button: discord.ui.Button):
        channels = cfg().setdefault("command_channels", {})
        channels["mode"] = "hard"
        await STORE.mark_dirty()
        await audit(interaction.user.id, "Command routing mode", {"mode": "hard"})
        await interaction.response.edit_message(content=self._summary(), view=self)

    @discord.ui.button(label="Rename channels", style=discord.ButtonStyle.primary)
    async def rename_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CommandChannelModal())


class ConfirmView(BaseView):
    def __init__(self, author_id: int, action_cb, done_text: str = "Done."):
        super().__init__(author_id)
        self.action_cb = action_cb
        self.done_text = done_text

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.action_cb(interaction)
        await interaction.response.edit_message(content=self.done_text, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled.", view=None)

class SetupMenuView(BaseView):
    @discord.ui.button(label="Full Sync (Destructive)", style=discord.ButtonStyle.danger)
    async def fullsync_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or interaction.guild.id != ADMIN_GUILD_ID:
            return await interaction.response.send_message("Admin server only.", ephemeral=True)
        if not is_super(interaction.user.id):
            return await interaction.response.send_message("SUPERUSER only.", ephemeral=True)
        await interaction.response.send_message(
            "Destructive fullsync starting. You'll get a DM when it's done.",
            ephemeral=True
        )
        await audit(interaction.user.id, "Setup fullsync", {"guild_id": interaction.guild.id})
        asyncio.create_task(run_full_setup(interaction.guild, "fullsync", actor_id=interaction.user.id))

    @discord.ui.button(label="Roles Refresh", style=discord.ButtonStyle.secondary)
    async def roles_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or interaction.guild.id != ADMIN_GUILD_ID:
            return await interaction.response.send_message("Admin server only.", ephemeral=True)
        await ensure_roles(interaction.guild)
        await interaction.response.send_message("Roles refreshed.", ephemeral=True)

    @discord.ui.button(label="Auto Setup + Backfill", style=discord.ButtonStyle.success)
    async def auto_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or interaction.guild.id != ADMIN_GUILD_ID:
            return await interaction.response.send_message("Admin server only.", ephemeral=True)
        await interaction.response.send_message(
            "Auto setup + backfill starting. You'll get a DM when it's done.",
            ephemeral=True
        )
        await audit(interaction.user.id, "Auto setup start", {"guild_id": interaction.guild.id})
        asyncio.create_task(run_auto_setup_with_debrief(actor_id=interaction.user.id))

    @discord.ui.button(label="Purge MySQL (Reset)", style=discord.ButtonStyle.danger)
    async def purge_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or interaction.guild.id != ADMIN_GUILD_ID:
            return await interaction.response.send_message("Admin server only.", ephemeral=True)
        if not is_super(interaction.user.id):
            return await interaction.response.send_message("SUPERUSER only.", ephemeral=True)
        if not POOL:
            return await interaction.response.send_message("MySQL not enabled.", ephemeral=True)

        async def do_purge(ix: discord.Interaction):
            await setup_log(f"MySQL purge requested by {ix.user.id}")
            ok = await db_purge_all()
            if ok:
                await audit(ix.user.id, "MySQL purge", {})
            else:
                await setup_log("MySQL purge failed.")

        await interaction.response.send_message(
            "This will wipe all MySQL tables and re-seed. Confirm?",
            view=ConfirmView(interaction.user.id, do_purge, done_text="MySQL purge complete."),
            ephemeral=True
        )

    @discord.ui.button(label="Destructive Rebuild", style=discord.ButtonStyle.danger)
    async def destructive_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_super(interaction.user.id):
            return await interaction.response.send_message("SUPERUSER only.", ephemeral=True)
        async def do_rebuild(ix: discord.Interaction):
            if not ix.guild or ix.guild.id != ADMIN_GUILD_ID:
                return
            await audit(ix.user.id, "Setup destructive", {"guild_id": ix.guild.id})
            asyncio.create_task(run_full_setup(ix.guild, "destructive", actor_id=ix.user.id))
        await interaction.response.send_message(
            "This will delete all managed categories/channels and rebuild. Confirm?",
            view=ConfirmView(
                interaction.user.id,
                do_rebuild,
                done_text="Started. DM will arrive when it's done."
            ),
            ephemeral=True
        )

class GateToolsView(BaseView):
    def __init__(self, author_id: int):
        super().__init__(author_id)
        self.target: Optional[discord.User] = None
        self.user_select = discord.ui.UserSelect(placeholder="Select user")
        self.user_select.callback = self.user_selected
        self.add_item(self.user_select)

    async def user_selected(self, interaction: discord.Interaction):
        self.target = self.user_select.values[0]
        await interaction.response.edit_message(content=f"Selected: {self.target}", view=self)

    @discord.ui.button(label="Approve Guest", style=discord.ButtonStyle.success)
    async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.target or not interaction.guild:
            return await interaction.response.send_message("Select a user.", ephemeral=True)
        member = interaction.guild.get_member(self.target.id)
        if not member:
            return await interaction.response.send_message("User not in guild.", ephemeral=True)
        await gate_approve_user(member)
        await interaction.response.send_message("Guest approved.", ephemeral=True)

    @discord.ui.button(label="Release Quarantine", style=discord.ButtonStyle.secondary)
    async def release_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.target or not interaction.guild:
            return await interaction.response.send_message("Select a user.", ephemeral=True)
        member = interaction.guild.get_member(self.target.id)
        if not member:
            return await interaction.response.send_message("User not in guild.", ephemeral=True)
        q = get_role(interaction.guild, QUARANTINE_ROLE_NAME)
        if q and q in member.roles:
            try:
                await member.remove_roles(q, reason="Quarantine release")
            except Exception:
                pass
        await interaction.response.send_message("Quarantine released.", ephemeral=True)

    @discord.ui.button(label="Reset Attempts", style=discord.ButtonStyle.danger)
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.target:
            return await interaction.response.send_message("Select a user.", ephemeral=True)
        await gate_reset_attempts(self.target.id)
        await interaction.response.send_message("Attempts reset.", ephemeral=True)

class GodMenuView(BaseView):
    async def _require_god(self, interaction: discord.Interaction) -> bool:
        lvl = await effective_level(interaction.user)
        if lvl < 90 and not is_super(interaction.user.id):
            await interaction.response.send_message("GOD only.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Permissions", style=discord.ButtonStyle.primary)
    async def perms_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        await interaction.response.send_message("Permissions panel.", view=PermissionMenuView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Mirrors", style=discord.ButtonStyle.primary)
    async def mirrors_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        await interaction.response.send_message("Mirrors panel.", view=MirrorMenuView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Watchers", style=discord.ButtonStyle.secondary)
    async def watchers_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        await interaction.response.send_message("Watchers panel.", view=WatcherMenuView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="DM Bridges", style=discord.ButtonStyle.secondary)
    async def dm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        await interaction.response.send_message("DM Bridges panel.", view=DmBridgeMenuView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Logging", style=discord.ButtonStyle.secondary)
    async def logs_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        await interaction.response.send_message("Logging panel.", view=LoggingMenuView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Command Routing", style=discord.ButtonStyle.primary)
    async def command_routes_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        await interaction.response.send_message(
            "Command routing panel.",
            view=CommandChannelView(interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Setup", style=discord.ButtonStyle.danger)
    async def setup_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        await interaction.response.send_message("Setup panel.", view=SetupMenuView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Gate Tools", style=discord.ButtonStyle.success)
    async def gate_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        await interaction.response.send_message("Gate tools panel.", view=GateToolsView(interaction.user.id), ephemeral=True)

# -----------------------------
# Commands
# -----------------------------
@bot.command(name="leavevc")
async def cmd_leavevc(ctx: commands.Context):
    if ctx.author.id != SUPER_USER_ID:
        await safe_delete(ctx.message)
        return
    await safe_delete(ctx.message)
    disconnected = 0
    for vc in list(bot.voice_clients):
        guild_id = getattr(vc.guild, "id", 0)
        try:
            if vc.is_playing() or vc.is_paused():
                vc.stop()
            await vc.disconnect()
            disconnected += 1
        except Exception:
            pass
        if guild_id:
            cancel_special_voice_leave_task(guild_id)
            cancel_movie_stay_task(guild_id)
            MOVIE_ACTIVE_GUILDS.discard(guild_id)
            MOVIE_STATES.pop(guild_id, None)
    await log_to("voice", "Emergency voice disconnect executed", subsystem="VOICE", severity="WARN", details={"connections": disconnected})
    await ctx.send(voice_line(cfg(), "confirm_leavevc"), delete_after=6)

@bot.command()
async def menu(ctx: commands.Context):
    if not await require_level_ctx(ctx, 10):
        return
    await safe_delete(ctx.message)
    view = UserMenuView(ctx.author.id)
    msg = await ctx.send("**Mandy Menu**", view=view)
    view.message = msg

@bot.command()
async def godmenu(ctx: commands.Context):
    if not await require_level_ctx(ctx, 90):
        return
    await safe_delete(ctx.message)
    view = GodMenuView(ctx.author.id)
    msg = await ctx.send("**GOD MENU**", view=view)
    view.message = msg

@bot.command()
async def ambient(ctx: commands.Context, mode: str = "status"):
    if not await require_level_ctx(ctx, 90):
        return
    await safe_delete(ctx.message)
    mode = (mode or "status").lower().strip()
    if mode in ("", "status"):
        status = ambient_engine.ambient_status()
        if not status.get("enabled"):
            return await ctx.send("Ambient engine: disabled.", delete_after=8)
        next_ts = status.get("next_event_at")
        next_type = status.get("next_event_type") or "event"
        eta = fmt_ts(next_ts) if next_ts else "unknown"
        return await ctx.send(f"Ambient engine: enabled. Next {next_type}: {eta}.", delete_after=8)
    if mode == "on":
        cfg().setdefault("ambient_engine", {})["enabled"] = True
        await STORE.mark_dirty()
        await ambient_engine.start_ambient_engine(bot)
        await audit(ctx.author.id, "Ambient engine enabled", {})
        return await ctx.send("Ambient engine enabled.", delete_after=6)
    if mode == "off":
        await ambient_engine.stop_ambient_engine()
        await audit(ctx.author.id, "Ambient engine disabled", {})
        return await ctx.send("Ambient engine disabled.", delete_after=6)
    return await ctx.send("Use: `!ambient on|off|status`", delete_after=6)

@bot.command(name="health")
async def cmd_health(ctx: commands.Context):
    if not await require_level_ctx(ctx, 70):
        return
    await safe_delete(ctx.message)
    rules = list(mirror_rules_dict().values())
    enabled = len([r for r in rules if r.get("enabled", True)])
    disabled = len(rules) - enabled
    watchers = len(cfg().get("targets", {}) or {})
    dm_bridges = await dm_bridge_list_active()
    ai = cfg().get("ai", {}) or {}
    queue = ai.get("queue", {}) or {}
    queue_counts = {"pending": 0, "waiting": 0, "running": 0}
    for job in queue.values():
        status = str(job.get("status", "pending"))
        if status in queue_counts:
            queue_counts[status] += 1
    last_reflection = int(daily_reflection_cfg().get("last_run_utc", 0) or 0)
    last_reflection_text = fmt_ts(last_reflection) if last_reflection else "never"
    gate_active = len(cfg().get("gate", {}) or {})

    lines = [
        voice_line(cfg(), "health_snapshot"),
        voice_line(cfg(), "status_homeostasis"),
        voice_line(cfg(), "status_cortex_online"),
        voice_line(cfg(), "status_immune_normal"),
        f"Sensory feeds: mirrors enabled={enabled} disabled={disabled}",
        f"Watchers: {watchers} | Gate posture: active={gate_active}",
        f"DM bridges active: {len(dm_bridges)}",
        f"AI queue: total={len(queue)} pending={queue_counts['pending']} waiting={queue_counts['waiting']} running={queue_counts['running']}",
        f"Last reflection (UTC): {last_reflection_text}",
    ]
    await ctx.send("\n".join(lines[:12]))

async def prompt_setup_destructive_choice(ctx: commands.Context) -> str:
    view = SetupDestructiveChoiceView(ctx.author.id)
    msg = await ctx.send("Destructive setup: choose rebuild mode.", view=view)
    view.message = msg
    await view.wait()
    choice = view.choice or "destructive"
    if choice == "cancel":
        try:
            await msg.edit(content="Destructive setup cancelled.", view=None)
        except Exception:
            pass
    return choice

async def prompt_setup_destructive_choice_with_channel(channel: discord.abc.Messageable, user_id: int) -> str:
    view = SetupDestructiveChoiceView(user_id)
    msg = await channel.send("Destructive setup: choose rebuild mode.", view=view)
    view.message = msg
    await view.wait()
    choice = view.choice or "destructive"
    if choice == "cancel":
        try:
            await msg.edit(content="Destructive setup cancelled.", view=None)
        except Exception:
            pass
    return choice

async def prompt_setup_menu(ctx: commands.Context) -> None:
    menu = SetupModeView(ctx.author.id)
    desc = [
        "**Bootstrap**: Safe sync. Creates missing channels/menus and rebinds logs without deleting.",
        "**Fullsync**: Destructive rebuild of the legacy admin layout (managed categories) + mirror sync.",
        "**Destructive**: Same as fullsync, plus AI/default choice prompt for the wipe.",
        "Backfill: mirror feeds are backfilled only if `database.json.auto.backfill` is enabled.",
        "DM bridges, stats, watchers are preserved. Use `!setup_bio` for the Sentient Core layout.",
    ]
    emb = discord.Embed(
        title="Setup Control Panel",
        description="\n".join(desc),
        color=discord.Color.dark_teal(),
    )
    await ctx.send(embed=emb, view=menu)

async def prompt_setup_bio_confirm(ctx: commands.Context) -> bool:
    view = SetupBioConfirmView(ctx.author.id)
    msg = await ctx.send("BIO-GENESIS will rebuild the admin hub. Confirm?", view=view)
    view.message = msg
    await view.wait()
    return view.confirmed

@bot.command()
async def setup(ctx: commands.Context, mode: str = ""):
    if not ctx.guild or ctx.guild.id != ADMIN_GUILD_ID:
        await safe_delete(ctx.message)
        return
    if not await require_level_ctx(ctx, 70):
        return
    await safe_delete(ctx.message)
    mode = (mode or "").lower().strip()
    if not mode:
        return await prompt_setup_menu(ctx)
    if mode not in ("fullsync", "bootstrap", "destructive"):
        return await ctx.send(
            "Use: `!setup fullsync` (destructive), `!setup destructive`, or `!setup bootstrap`",
            delete_after=6
        )
    if mode in ("destructive", "fullsync") and not is_super(ctx.author.id):
        return await ctx.send("SUPERUSER only.", delete_after=6)
    if mode == "destructive":
        choice = await prompt_setup_destructive_choice(ctx)
        if choice == "cancel":
            return
        mode = choice
    asyncio.create_task(run_full_setup(ctx.guild, mode, actor_id=ctx.author.id))
    await audit(ctx.author.id, "Setup run", {"mode": mode})
    await safe_ctx_send(ctx, "Setup started. You'll get a DM when it's done.", delete_after=10)

@bot.command(name="setup_bio")
async def setup_bio(ctx: commands.Context):
    if not ctx.guild or ctx.guild.id != ADMIN_GUILD_ID:
        await safe_delete(ctx.message)
        return
    if not is_super(ctx.author.id):
        await safe_delete(ctx.message)
        return await ctx.send("SUPERUSER only.", delete_after=6)
    await safe_delete(ctx.message)
    confirmed = await prompt_setup_bio_confirm(ctx)
    if not confirmed:
        return
    asyncio.create_task(run_setup_bio(ctx.guild, actor_id=ctx.author.id))
    await audit(ctx.author.id, "Setup BIO run", {})
    await safe_ctx_send(ctx, "BIO-GENESIS started. You'll get a DM when it's done.", delete_after=10)

@bot.command()
async def addtarget(ctx: commands.Context, user_id: int, count: int, *, text: str):
    if not await require_level_ctx(ctx, 90):
        return
    await safe_delete(ctx.message)
    cfg().setdefault("targets", {})[str(user_id)] = {"count": int(count), "current": 0, "text": text}
    await STORE.mark_dirty()
    await audit(ctx.author.id, "Target set (json)", {"user_id": user_id, "count": count})
    await ctx.send("Target saved.", delete_after=6)

@bot.command(name="watchers", aliases=["watcher"])
async def cmd_watchers(ctx: commands.Context):
    if not await require_level_ctx(ctx, 50):
        return
    await safe_delete(ctx.message)
    chunks = await watchers_report()
    for i, chunk in enumerate(chunks):
        await ctx.send(chunk, delete_after=CLEANUP_RESPONSE_TTL if i == 0 else CLEANUP_RESPONSE_TTL + 5)


@bot.command()
async def remember(ctx: commands.Context, *, note: str):
    if not await require_level_ctx(ctx, 50):
        return
    await safe_delete(ctx.message)
    await memory_add(
        "note",
        note,
        {
            "author_id": ctx.author.id,
            "guild_id": getattr(ctx.guild, "id", 0),
            "channel_id": getattr(ctx.channel, "id", 0),
        },
    )
    await ctx.send("Noted.", delete_after=8)


@bot.command(name="memory")
async def memory_cmd(ctx: commands.Context, limit: int = 8):
    if not await require_level_ctx(ctx, 50):
        return
    await safe_delete(ctx.message)
    events = memory_recent(limit=limit)
    if not events:
        return await ctx.send("No memory yet.", delete_after=8)
    lines = []
    for e in events:
        ts = fmt_ts(e.get("ts", 0))
        lines.append(f"{ts} [{e.get('kind','note')}] {e.get('text','')}")
    await ctx.send("\n".join(lines[:15]), delete_after=20)


def _generate_phoenix_key(snapshot_id: str) -> str:
    code = f"CONFIRM-{snapshot_id}-{secrets.token_hex(2).upper()}"
    phoenix_keys()[snapshot_id] = code
    return code


def _snapshot_id(guild: discord.Guild) -> str:
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return f"ARK-{guild.id}-{stamp}"


async def create_ark_snapshot(guild: discord.Guild, theme: str = "") -> Dict[str, Any]:
    snap_id = _snapshot_id(guild)
    optimized = build_dynamic_blueprint(guild)
    roles = []
    for r in sorted(guild.roles, key=lambda r: r.position, reverse=True):
        roles.append({
            "id": r.id,
            "name": r.name,
            "color": r.color.value,
            "permissions": r.permissions.value,
            "position": r.position,
            "mentionable": r.mentionable,
            "hoist": r.hoist,
            "managed": r.managed,
            "is_default": r.is_default(),
        })
    categories = []
    for cat in sorted(guild.categories, key=lambda c: c.position):
        categories.append({
            "id": cat.id,
            "name": cat.name,
            "position": cat.position,
            "overwrites": serialize_overwrites(cat),
        })
    channels = []
    legacy_logs: List[Dict[str, Any]] = []
    for ch in sorted(guild.channels, key=lambda c: c.position):
        if isinstance(ch, discord.CategoryChannel):
            continue
        info = {
            "id": ch.id,
            "name": ch.name,
            "type": str(ch.type),
            "category": ch.category_id,
            "position": ch.position,
            "topic": getattr(ch, "topic", "") or "",
            "overwrites": serialize_overwrites(ch),
        }
        pins = []
        if isinstance(ch, discord.TextChannel):
            try:
                pin_objs = await ch.pins()
                for p in pin_objs[:5]:
                    pins.append({
                        "author": str(p.author),
                        "content": truncate(p.content or "", 900),
                        "created_at": p.created_at.isoformat() if p.created_at else "",
                    })
            except Exception:
                pass
        info["pins"] = pins
        channels.append(info)
        if isinstance(ch, discord.TextChannel) and (
            ch.name in ("rules", "announcements", "general") or pins
        ):
            try:
                recent = []
                async for m in ch.history(limit=80, oldest_first=True):
                    recent.append(f"{m.author}: {truncate(m.content or '', 200)}")
                if recent:
                    legacy_logs.append({"channel": ch.name, "messages": recent})
            except Exception:
                pass

    analytics = {
        "channel_count": len(guild.channels),
        "role_count": len(guild.roles),
        "estimated_deletion": len(guild.channels),
    }
    payload = {
        "id": snap_id,
        "guild_id": guild.id,
        "created_at": now_ts(),
        "theme": theme,
        "optimized": optimized,
        "roles": roles,
        "categories": categories,
        "channels": channels,
        "legacy_logs": legacy_logs,
        "analytics": analytics,
    }
    ark = ark_snapshots()
    ark[snap_id] = payload
    # keep most recent 5 per guild
    guild_snaps = [s for s in ark.values() if int(s.get("guild_id", 0)) == guild.id]
    if len(guild_snaps) > 5:
        for old in sorted(guild_snaps, key=lambda s: s.get("created_at", 0))[:-5]:
            ark.pop(old.get("id", ""), None)
    await STORE.mark_dirty()
    return payload


def _simulate_phoenix(snapshot: Dict[str, Any], theme: str = "") -> str:
    if not snapshot:
        return "No snapshot."
    channels = snapshot.get("channels", [])
    roles = snapshot.get("roles", [])
    pins = sum(len(ch.get("pins", [])) for ch in channels)
    notes: List[str] = []
    if theme:
        notes.append(f"Theme: {theme}")
    notes.append(f"Would delete/rebuild {len(channels)} channels and {len(roles)} roles.")
    notes.append(f"Pins to restore: {pins}")
    legacy = snapshot.get("legacy_logs", [])
    if legacy:
        notes.append(f"Legacy logs captured: {len(legacy)} channels")
    low_value_roles = [r for r in roles if not r.get("is_default") and r.get("permissions", 0) == 0]
    if low_value_roles:
        notes.append(f"Optimization: {len(low_value_roles)} roles have no permissions (candidates for deprecation).")
    optimized = snapshot.get("optimized", {})
    if optimized and optimized.get("notes"):
        notes.append("Optimized plan: " + " ".join(optimized.get("notes", [])[:3]))
    return "\n".join(notes)


async def _execute_phoenix(snapshot: Dict[str, Any], guild: discord.Guild, theme: str = "") -> str:
    if not snapshot or int(snapshot.get("guild_id", 0)) != guild.id:
        return "Snapshot does not belong to this guild."
    # Phase 1: purge channels (skip if they are categories; delete children first)
    for ch in list(guild.channels):
        try:
            await ch.delete()
            await asyncio.sleep(0.2)
        except Exception:
            continue
    # Recreate categories
    cat_map: Dict[int, int] = {}
    cat_map_name: Dict[str, int] = {}
    plan = snapshot.get("optimized") or {}
    if plan and plan.get("categories"):
        categories = [{"id": 0, "name": c["name"], "position": 0, "overwrites": {}} for c in plan["categories"]]
        channels_plan = []
        for cat in plan["categories"]:
            for name in cat.get("channels", []):
                channels_plan.append({
                    "id": 0,
                    "name": name,
                    "type": "text",
                    "category": cat["name"],
                    "position": 0,
                    "topic": "",
                    "overwrites": {},
                    "pins": [],
                })
    else:
        categories = snapshot.get("categories", [])
        channels_plan = snapshot.get("channels", [])

    for cat in sorted(categories, key=lambda c: c.get("position", 0)):
        try:
            created = await guild.create_category(cat.get("name", "category"), position=cat.get("position", None))
            if plan and plan.get("categories"):
                cat_map_name[created.name] = created.id
            else:
                cat_map[int(cat.get("id", 0))] = created.id
            overwrites = deserialize_overwrites(guild, cat.get("overwrites", {}))
            if overwrites:
                await created.edit(overwrites=overwrites)
            await asyncio.sleep(0.2)
        except Exception:
            await request_elevation("create_category", "permission error or rate limit", {"guild_id": guild.id})
            continue
    # Recreate channels
    for ch in sorted(channels_plan, key=lambda c: c.get("position", 0)):
        try:
            ctype = ch.get("type", "text")
            name = ch.get("name", "channel")
            cat_key = ch.get("category")
            if isinstance(cat_key, str) and plan and plan.get("categories"):
                cat_id = cat_map_name.get(cat_key)
            else:
                cat_id = cat_map.get(int(ch.get("category", 0))) if ch.get("category") else None
            new_ch: Optional[discord.abc.GuildChannel] = None
            if "voice" in ctype:
                new_ch = await guild.create_voice_channel(name, category=guild.get_channel(cat_id) if cat_id else None, position=ch.get("position", None))
            else:
                topic = ch.get("topic", "")
                new_ch = await guild.create_text_channel(
                    name,
                    category=guild.get_channel(cat_id) if cat_id else None,
                    topic=topic,
                    position=ch.get("position", None),
                )
            if new_ch:
                ow = deserialize_overwrites(guild, ch.get("overwrites", {}))
                if ow:
                    await new_ch.edit(overwrites=ow)
                # restore pins as new messages
                for pin in ch.get("pins", [])[:3]:
                    content = f"[ARK PIN] {pin.get('author','?')}: {pin.get('content','')}"
                    try:
                        await new_ch.send(content)
                    except Exception:
                        pass
                if isinstance(new_ch, discord.TextChannel) and new_ch.name == "rules":
                    role = discord.utils.get(guild.roles, name=cfg().get("onboarding", {}).get("role_name", "Citizen"))
                    if not role:
                        try:
                            role = await guild.create_role(name=cfg().get("onboarding", {}).get("role_name", "Citizen"))
                        except Exception:
                            role = None
                    if role:
                        cfg().setdefault("onboarding", {})["rules_channel_id"] = new_ch.id
                        await STORE.mark_dirty()
                if theme and isinstance(new_ch, discord.TextChannel):
                    try:
                        await new_ch.send(f"Day 1: Welcome to {name}. Theme: {theme}.")
                    except Exception:
                        pass
            await asyncio.sleep(0.25)
        except Exception:
            await request_elevation("create_channel", "permission error or rate limit", {"guild_id": guild.id, "channel": ch.get("name")})
            continue
    # Pre-wire mirror from announcements to admin hub feed
    if guild.id != ADMIN_GUILD_ID:
        try:
            ann = discord.utils.get(guild.text_channels, name="announcements")
            if ann:
                mirror_feed, _ = await ensure_admin_server_channels(guild)
                if mirror_feed:
                    rule_id = make_rule_id("channel", ann.id, mirror_feed.id)
                    if rule_id not in mirror_rules_dict():
                        await mirror_rule_save({
                            "rule_id": rule_id,
                            "scope": "channel",
                            "source_guild": guild.id,
                            "source_id": ann.id,
                            "target_channel": mirror_feed.id,
                            "enabled": True,
                            "fail_count": 0
                        })
        except Exception:
            pass
    # Legacy logs
    legacy = snapshot.get("legacy_logs", [])
    if legacy:
        try:
            everyone = guild.default_role
            overwrites = {everyone: discord.PermissionOverwrite(send_messages=False, add_reactions=False)}
            legacy_ch = await guild.create_text_channel("legacy-logs", overwrites=overwrites)
            for entry in legacy[:6]:
                header = f"Legacy from #{entry.get('channel','unknown')}:"
                text = "\n".join(entry.get("messages", [])[:80])
                await legacy_ch.send(header + "\n" + text[:1800])
        except Exception:
            pass
    # Roles are not recreated to avoid breaking permissions; report instead
    await send_owner_server_report(guild, reason="phoenix rebuild complete")
    return "Phoenix protocol complete. Channels rebuilt; pins restored; theme seeded."


@bot.command(name="remove")
async def cmd_remove_watcher(ctx: commands.Context, *, target: str):
    if not await require_level_ctx(ctx, 50):
        return
    await safe_delete(ctx.message)
    uid, candidates = await _resolve_user_reference(ctx, target)
    if candidates and not uid:
        lines = "\n".join(f"{label} ({uid})" for uid, label in candidates)
        return await ctx.send(
            f"Multiple users match:\n{lines}\nPlease specify an exact user mention or ID.",
            delete_after=12,
        )
    if not uid:
        return await ctx.send(
            "User not found. Try `@mention`, ID, or clear nickname.",
            delete_after=10,
        )
    responses = [await remove_watcher("json", uid, ctx.author.id)]
    if POOL:
        responses.append(await remove_watcher("mysql", uid, ctx.author.id))
    await ctx.send("\n".join(responses), delete_after=10)


@bot.command()
async def ark(ctx: commands.Context, *, theme: str = ""):
    if not await require_level_ctx(ctx, 90):
        return
    await safe_delete(ctx.message)
    snap = await create_ark_snapshot(ctx.guild, theme=theme)
    report = _simulate_phoenix(snap, theme=theme)
    await ctx.send(f"Ark snapshot `{snap['id']}` created.\n{report}", delete_after=20)


@bot.command()
async def phoenix(ctx: commands.Context, snapshot_id: str, action: str = "simulate", *, theme: str = ""):
    if not await require_level_ctx(ctx, 90):
        return
    await safe_delete(ctx.message)
    snap = ark_snapshots().get(snapshot_id)
    if not snap:
        return await ctx.send("Snapshot not found.", delete_after=8)
    if action.lower() in ("simulate", "dry", "preview"):
        report = _simulate_phoenix(snap, theme=theme)
        code = _generate_phoenix_key(snapshot_id)
        return await ctx.send(
            f"PHOENIX SIMULATION for {snapshot_id}:\n{report}\nType `{code}` to confirm run: `!phoenix {snapshot_id} run {code}`",
            delete_after=30,
        )
    if action.lower() == "run":
        parts = theme.split()
        code = parts[-1] if parts else ""
        stored = phoenix_keys().get(snapshot_id)
        if stored and stored != code:
            return await ctx.send("Confirmation key mismatch.", delete_after=8)
        result = await _execute_phoenix(snap, ctx.guild, theme=theme)
        await ctx.send(result, delete_after=20)
        return
    await ctx.send("Use: `!phoenix <snapshot_id> simulate` or `!phoenix <snapshot_id> run <CONFIRM-...>`", delete_after=12)

@bot.command()
async def mirroradd(ctx: commands.Context, source_channel_id: int, target_channel_id: int):
    if not await require_level_ctx(ctx, 70):
        return
    await safe_delete(ctx.message)
    try:
        src_ch = bot.get_channel(source_channel_id) or await bot.fetch_channel(source_channel_id)
        src_gid = src_ch.guild.id
    except Exception:
        return await ctx.send("Can't access source channel.", delete_after=6)

    rule_id = make_rule_id("channel", source_channel_id, target_channel_id)
    rule = {
        "rule_id": rule_id,
        "scope": "channel",
        "source_guild": src_gid,
        "source_id": source_channel_id,
        "target_channel": target_channel_id,
        "enabled": True,
        "fail_count": 0
    }
    await mirror_rule_save(rule)
    await audit(ctx.author.id, "Mirror rule add", rule)
    await ctx.send(voice_line(cfg(), "confirm_mirror_added"), delete_after=6)

@bot.command()
async def mirroraddscope(ctx: commands.Context, scope: str, source_id: int, target_channel_id: int):
    if not await require_level_ctx(ctx, 70):
        return
    await safe_delete(ctx.message)
    scope = normalize_scope(scope)
    if scope == "server":
        src_gid = source_id
    else:
        try:
            src_ch = bot.get_channel(source_id) or await bot.fetch_channel(source_id)
            src_gid = src_ch.guild.id
        except Exception:
            return await ctx.send("Can't access source.", delete_after=6)

    rule_id = make_rule_id(scope, source_id, target_channel_id)
    rule = {
        "rule_id": rule_id,
        "scope": scope,
        "source_guild": src_gid,
        "source_id": source_id,
        "target_channel": target_channel_id,
        "enabled": True,
        "fail_count": 0
    }
    await mirror_rule_save(rule)
    await audit(ctx.author.id, "Mirror rule add", rule)
    await ctx.send(voice_line(cfg(), "confirm_mirror_added_scope"), delete_after=6)

@bot.command()
async def mirrorremove(ctx: commands.Context, source_channel_id: int, mode: str = ""):
    if not await require_level_ctx(ctx, 70):
        return
    await safe_delete(ctx.message)
    rules = mirror_rules_dict()
    removed = 0
    simulate = mode.lower() in ("simulate", "dry", "preview")
    preview: List[str] = []
    for rid, r in list(rules.items()):
        if r.get("scope") == "channel" and int(r.get("source_id", 0)) == source_channel_id:
            if simulate:
                preview.append(rid)
            else:
                await mirror_rule_disable(r, "removed via command")
                removed += 1
    if simulate:
        text = "Would disable: " + (", ".join(preview) if preview else "none")
        return await ctx.send(text, delete_after=10)
    await audit(ctx.author.id, "Mirror remove", {"source_channel_id": source_channel_id, "removed": removed})
    await ctx.send(voice_line(cfg(), "confirm_mirror_removed", count=removed), delete_after=6)

@bot.command()
async def dmopen(ctx: commands.Context, user_id: int):
    if not ctx.guild or ctx.guild.id != ADMIN_GUILD_ID:
        await safe_delete(ctx.message)
        return
    if not await require_level_ctx(ctx, 70):
        return
    await safe_delete(ctx.message)

    ch = await ensure_dm_bridge_channel(user_id, active=True)
    if not ch:
        return await ctx.send("Could not create bridge channel.", delete_after=6)
    await dm_bridge_set(user_id, ch.id, True, last_activity=now_ts())
    await audit(ctx.author.id, "DM bridge open", {"user_id": user_id, "channel_id": ch.id})

    # dump history
    await dm_bridge_sync_history(user_id, ch)

    await ctx.send(f"Opened: {ch.mention}", delete_after=8)

@bot.command()
async def dmclose(ctx: commands.Context, user_id: int):
    if not ctx.guild or ctx.guild.id != ADMIN_GUILD_ID:
        await safe_delete(ctx.message)
        return
    if not await require_level_ctx(ctx, 70):
        return
    await safe_delete(ctx.message)
    await dm_bridge_close(user_id)
    await audit(ctx.author.id, "DM bridge close", {"user_id": user_id})
    await ctx.send("Closed.", delete_after=6)

@bot.command()
async def setlogs(ctx: commands.Context, which: str, channel_id: int):
    if not await require_level_ctx(ctx, 90):
        return
    await safe_delete(ctx.message)
    which = which.lower().strip()
    if which not in ("system", "audit", "debug", "mirror", "ai", "voice"):
        return await ctx.send("Use: `!setlogs system|audit|debug|mirror|ai|voice <channel_id>`", delete_after=6)
    cfg().setdefault("logs", {})[which] = int(channel_id)
    await STORE.mark_dirty()
    await audit(ctx.author.id, "Log channel set", {"which": which, "channel_id": channel_id})
    await ctx.send(voice_line(cfg(), "confirm_log_set"), delete_after=6)

@bot.command(name="mystats")
async def cmd_mystats(ctx: commands.Context, window: str = None):
    await safe_delete(ctx.message)
    window = normalize_stats_window(window, "daily")
    now_dt = datetime.datetime.utcnow()
    entry, changed = chat_stats_get_user_entry(ctx.guild, window, ctx.author.id, now_dt)
    if changed:
        await STORE.mark_dirty()

    emb = discord.Embed(
        title=f"Your Stats ({window})",
        color=discord.Color.dark_gray()
    )
    emb.add_field(name="Messages", value=str(int(entry.get("messages", 0))), inline=True)
    emb.add_field(name="Words", value=str(int(entry.get("words", 0))), inline=True)
    emb.add_field(name="Sentences", value=str(int(entry.get("sentences", 0))), inline=True)
    emb.add_field(name="Favorite Words", value=format_top_words(entry), inline=False)
    emb.set_footer(text="Mandy OS")
    await ctx.send(embed=emb)

@bot.command(name="allstats")
async def cmd_allstats(ctx: commands.Context, window: str = None):
    await safe_delete(ctx.message)
    window = normalize_stats_window(window, "daily")
    window_state = chat_stats_window_state(ctx.guild, window)

    rows = []
    for uid, entry in window_state.items():
        msg_count = int(entry.get("messages", 0))
        if msg_count <= 0:
            continue
        rows.append((int(uid), msg_count, entry))

    rows.sort(key=lambda row: row[1], reverse=True)
    lines = []
    for uid, msg_count, entry in rows:
        name = guild_user_label(ctx.guild, uid)
        top_words = format_top_words(entry)
        lines.append(f"{name} — {msg_count} — {top_words}")

    value = "\n".join(lines) if lines else "No data."

    emb = discord.Embed(
        title=f"Server Stats ({window})",
        color=discord.Color.dark_gray()
    )
    emb.add_field(name="Leaderboard", value=value, inline=False)
    emb.set_footer(text="Mandy OS")
    await ctx.send(embed=emb)

@bot.command(name="livestats")
async def cmd_livestats(ctx: commands.Context, window: str = None):
    await safe_delete(ctx.message)
    if await effective_level(ctx.author) < 50:
        return
    window = normalize_stats_window(window, "rolling24")

    guild_id = ctx.guild.id
    await stop_live_stats_panel(guild_id, delete_message=True)
    emb, _ = await chat_stats_build_live_embed(ctx.guild, window)
    msg = await ctx.send(embed=emb)
    await msg.pin()
    chat_stats_live_message()[str(guild_id)] = {
        "channel_id": msg.channel.id,
        "message_id": msg.id,
        "window": window
    }
    await STORE.mark_dirty()
    LIVE_STATS_TASKS[guild_id] = asyncio.create_task(
        live_stats_loop(guild_id, msg.channel.id, msg.id, window)
    )

@bot.command(name="globalstats")
async def cmd_globalstats(ctx: commands.Context, window: str = None):
    await safe_delete(ctx.message)
    window = normalize_stats_window(window, "rolling24")
    emb, changed = await chat_stats_build_global_embed(window)
    if changed:
        await STORE.mark_dirty()
    await ctx.send(embed=emb)

@bot.command(name="globallive")
async def cmd_globallive(ctx: commands.Context, window: str = None):
    await safe_delete(ctx.message)
    if await effective_level(ctx.author) < 70:
        return
    window = normalize_stats_window(window, "rolling24")

    await stop_global_live_panel(delete_message=True)
    emb, changed = await chat_stats_build_global_embed(window)
    msg = await ctx.send(embed=emb)
    try:
        await msg.pin()
    except Exception:
        pass
    info = chat_stats_global_live_message()
    info.clear()
    info.update({
        "channel_id": msg.channel.id,
        "message_id": msg.id,
        "window": window
    })
    await STORE.mark_dirty()
    if changed:
        await STORE.mark_dirty()
    LIVE_STATS_TASKS["GLOBAL"] = asyncio.create_task(
        global_live_stats_loop(msg.channel.id, msg.id, window)
    )

@bot.command(name="movie")
async def cmd_movie(ctx: commands.Context, *, query: str = None):
    if not await require_level_ctx(ctx, 90):
        return
    await safe_delete(ctx.message)

    if not query:
        await send_movie_menu(ctx)
        return

    query = (query or "").strip()
    if not query:
        await temp_reply(ctx, "Provide a YouTube link or a subcommand.")
        return

    parts = query.split(maxsplit=1)
    action = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if action in ("stay", "leave", "stop", "pause", "resume", "skip", "queue", "add", "volume", "vol"):
        guild, channel, err = await movie_resolve_target(ctx)
        if err:
            await temp_reply(ctx, err)
            return
        state = movie_state(guild.id)
        if action in ("leave", "stop"):
            await movie_stop(guild.id)
            await temp_reply(ctx, "Disconnected.")
            return
        if action == "pause":
            paused = await movie_pause(guild.id)
            await temp_reply(ctx, "Paused." if paused else "Nothing playing.")
            return
        if action == "resume":
            resumed = await movie_resume(guild.id)
            await temp_reply(ctx, "Resumed." if resumed else "Nothing paused.")
            return
        if action == "skip":
            skipped = await movie_skip(guild.id)
            await temp_reply(ctx, "Skipped." if skipped else "Nothing to skip.")
            return
        if action == "stay":
            minutes = MOVIE_STAY_DEFAULT_MINUTES
            if rest:
                try:
                    minutes = int(rest.strip())
                except Exception:
                    await temp_reply(ctx, "Stay minutes must be a number.")
                    return
            minutes = max(1, min(MOVIE_STAY_MAX_MINUTES, minutes))
            state["stay_until"] = now_ts() + minutes * 60
            schedule_movie_stay_task(guild.id)
            MOVIE_ACTIVE_GUILDS.add(guild.id)
            await temp_reply(ctx, f"Stay set for {minutes} minutes.")
            return
        if action in ("volume", "vol"):
            if not rest:
                vol = int(float(state.get("volume", 1.0)) * 100)
                await temp_reply(ctx, f"Volume is {vol}%.")
                return

            try:
                vol = int(rest.strip())
            except Exception:
                await temp_reply(ctx, "Volume must be a number.")
                return
            vol = max(0, min(100, vol))
            await movie_set_volume(guild.id, vol / 100.0)
            await temp_reply(ctx, f"Volume set to {vol}%.")
            return
        if action == "queue":
            now = state.get("now_title") or state.get("now_url") or "(nothing)"
            lines = [f"Now: {now}"]
            if state.get("queue"):
                lines.append("Up next:")
                for idx, item in enumerate(state["queue"][:10], start=1):
                    lines.append(f"{idx}. {item}")
            else:
                lines.append("Queue: (empty)")
            await ctx.send("\n".join(lines))
            return
        if action == "add":
            if not rest:
                await temp_reply(ctx, "Provide a YouTube link to queue.")
                return
            url = normalize_youtube_url(rest)
            if not is_youtube_url(url):
                await temp_reply(ctx, "Only YouTube links are allowed.")
                return
            try:
                ok, msg = await movie_queue_add(guild, channel, url)
            except Exception as exc:
                await debug(f"movie queue failed: {exc}")
                await temp_reply(ctx, "Failed to queue that link.")
                return
            await temp_reply(ctx, msg)
            return

    url = normalize_youtube_url(query)
    if not is_youtube_url(url):
        await temp_reply(ctx, "Only YouTube links are allowed.")
        return
    guild, channel, err = await movie_resolve_target(ctx)
    if err:
        await temp_reply(ctx, err)
        return
    try:
        await movie_start_playback(guild, channel, url, clear_queue=True)
        await temp_reply(ctx, "Playing now.")
    except Exception as exc:
        await debug(f"movie start failed: {exc}")
        await temp_reply(ctx, "Failed to start playback.")

@bot.command(name="volume")
async def cmd_volume(ctx: commands.Context, *, level: str = None):
    if not await require_level_ctx(ctx, 90):
        return
    await safe_delete(ctx.message)
    guild, _, err = await movie_resolve_target(ctx)
    if err:
        await temp_reply(ctx, err)
        return
    state = movie_state(guild.id)
    if not level:
        vol = int(float(state.get("volume", 1.0)) * 100)
        await temp_reply(ctx, f"Volume is {vol}%.")
        return
    try:
        vol = int(str(level).strip())
    except Exception:
        await temp_reply(ctx, "Volume must be a number.")
        return
    vol = max(0, min(100, vol))
    await movie_set_volume(guild.id, vol / 100.0)
    await temp_reply(ctx, f"Volume set to {vol}%.")

@bot.command()
async def clean(ctx: commands.Context, limit: int = 120):
    if not await require_level_ctx(ctx, 50):
        return
    await safe_delete(ctx.message)
    limit = max(1, min(300, int(limit)))
    deleted = 0
    async for m in ctx.channel.history(limit=limit):
        if m.author.id == bot.user.id:
            try:
                await m.delete()
                deleted += 1
                await asyncio.sleep(0.2)
            except Exception:
                pass
    await audit(ctx.author.id, "Clean", {"channel_id": ctx.channel.id, "deleted": deleted})
    await ctx.send(f"Deleted {deleted}.", delete_after=6)

@bot.command()
async def nuke(ctx: commands.Context, limit: int = 300, mode: str = "run"):
    if ctx.author.id != SUPER_USER_ID:
        await safe_delete(ctx.message)
        return
    await safe_delete(ctx.message)
    try:
        limit_val = int(limit)
    except Exception:
        limit_val = 300
    limit_val = max(1, min(1200, limit_val))
    deleted = 0
    channel = ctx.channel
    history_limit = limit_val
    simulate = str(mode).lower() in ("simulate", "dry", "preview")
    messages = []
    async for message in channel.history(limit=history_limit):
        messages.append(message)
    if simulate:
        await ctx.send(
            f"SIMULATION: would delete {len(messages)} messages (limit={limit_val}).",
            delete_after=12,
        )
        return

    for message in messages:
        try:
            await message.delete()
            deleted += 1
            await asyncio.sleep(0.08)
        except Exception:
            pass
    await audit(ctx.author.id, "NUKE", {"channel_id": ctx.channel.id, "deleted": deleted})
    if isinstance(channel, discord.TextChannel):
        await repopulate_channel(channel)
    await ctx.send(
        f"Deleted {deleted} messages. Channel repopulated where applicable.",
        delete_after=10,
    )

# -----------------------------
# Events
# -----------------------------
@bot.event
async def on_ready():
    await STORE.load()
    await maybe_load_mandy_extension()
    try:
        if hasattr(bot, "mandy_plugin_manager"):
            await bot.mandy_plugin_manager.load_all()
    except Exception as e:
        await debug(f"Tool plugins failed to load: {e}")

    # optional mysql
    try:
        await db_init()
        await db_bootstrap()
    except Exception as e:
        # safe fallback
        await debug(f"MySQL disabled (init failed): {e}")
        global POOL
        POOL = None

    await migrate_legacy_json_mirrors()
    await migrate_legacy_mysql_mirrors()
    await mirror_rules_sync()

    bot.add_view(MirrorControls())
    try:
        await apply_bot_status()
    except Exception:
        pass
    try:
        await ambient_engine.start_ambient_engine(bot)
    except Exception as e:
        await debug(f"Ambient engine start failed: {e}")
    admin = bot.get_guild(ADMIN_GUILD_ID)
    if admin:
        try:
            await ensure_roles(admin)
            await apply_guest_permissions(admin)
            await apply_quarantine_permissions(admin)
        except Exception:
            pass
        if not auto_setup_enabled():
            for g in bot.guilds:
                if g.id != ADMIN_GUILD_ID:
                    await ensure_admin_server_channels(g)
                    await ensure_server_mirror_rule(g)
                    await update_server_info_for_guild(g)

    if auto_setup_enabled():
        asyncio.create_task(
            auto_setup_all_guilds(do_backfill=auto_backfill_enabled(), force_backfill=False)
        )
    if auto_backfill_enabled():
        asyncio.create_task(backfill_chat_stats_all_guilds())

    await audit(SUPER_USER_ID, "Mandy OS online", {"mysql": bool(POOL)})
    config_reload.start()
    json_autosave.start()
    mirror_integrity_check.start()
    server_status_update.start()
    dm_bridge_archive.start()
    presence_controller.start()
    daily_reflection_loop.start()
    internal_monologue_loop.start()
    sentience_maintenance_loop.start()
    diagnostics_loop.start()
    manual_upload_loop.start()
    await resume_live_stats_panels()
    await resume_global_live_panel()
    print(f"Logged in as {bot.user} ({bot.user.id}) | mysql={bool(POOL)}")

@bot.event
async def on_guild_join(guild: discord.Guild):
    if guild.id == ADMIN_GUILD_ID:
        return
    if auto_setup_enabled():
        asyncio.create_task(
            auto_setup_guild(guild, do_backfill=auto_backfill_enabled(), force_backfill=False)
        )
        if auto_backfill_enabled():
            asyncio.create_task(backfill_chat_stats_for_guild(guild))
        asyncio.create_task(send_owner_server_report(guild, reason="guild join (auto setup pending)"))
        return
    try:
        await ensure_admin_server_channels(guild)
        await ensure_server_mirror_rule(guild)
        await update_server_info_for_guild(guild)
    except Exception:
        pass
    asyncio.create_task(send_owner_server_report(guild, reason="guild join"))
    if auto_backfill_enabled():
        asyncio.create_task(backfill_chat_stats_for_guild(guild))

@tasks.loop(seconds=5)
async def config_reload():
    await STORE.reload_if_changed()

@tasks.loop(seconds=10)
async def json_autosave():
    await STORE.flush()

@tasks.loop(seconds=INTEGRITY_REFRESH)
async def mirror_integrity_check():
    await mirror_rules_sync()
    rules = list(mirror_rules_dict().values())
    if not rules:
        return
    global INTEGRITY_CURSOR
    rule = rules[INTEGRITY_CURSOR % len(rules)]
    INTEGRITY_CURSOR += 1

    # purge stale disabled rules
    purge_after = int(cfg().get("mirror_disable_ttl", 7 * 24 * 3600))
    if not rule.get("enabled") and purge_after > 0:
        disabled_at = int(rule.get("last_disabled_at", 0) or 0)
        age = now_ts() - disabled_at if disabled_at else 0
        if "missing" in rule.get("last_error", "") and age > 600:
            await mirror_rule_delete(rule, "disabled (missing) > 10m")
            return
        if disabled_at and age > purge_after:
            await mirror_rule_delete(rule, f"disabled > {purge_after}s")
            return

    # source guild missing
    src_gid = int(rule.get("source_guild", 0))
    if src_gid and not bot.get_guild(src_gid):
        await mirror_rule_disable(rule, "source guild missing")
        return

    # source channel/category missing for non-server scopes
    if rule.get("scope") in ("category", "channel"):
        src_id = int(rule.get("source_id", 0))
        ch = bot.get_channel(src_id)
        if not ch and src_id:
            try:
                ch = await bot.fetch_channel(src_id)
            except discord.NotFound:
                await mirror_rule_disable(rule, "source missing")
                return
            except discord.Forbidden as e:
                await mirror_rule_record_failure(rule, f"source forbidden: {e}")
                return
            except discord.HTTPException as e:
                await mirror_rule_record_failure(rule, f"source error: {e}")
                return

    # target channel missing
    dst_id = int(rule.get("target_channel", 0))
    if dst_id:
        ch = bot.get_channel(dst_id)
        if not ch:
            try:
                await bot.fetch_channel(dst_id)
            except discord.NotFound:
                await mirror_rule_disable(rule, "target missing")
                return
            except discord.Forbidden as e:
                await mirror_rule_record_failure(rule, f"target forbidden: {e}")
                return
            except discord.HTTPException as e:
                await mirror_rule_record_failure(rule, f"target error: {e}")
                return

@tasks.loop(seconds=SERVER_STATUS_REFRESH)
async def server_status_update():
    admin = bot.get_guild(ADMIN_GUILD_ID)
    if not admin:
        return
    for g in bot.guilds:
        if g.id == ADMIN_GUILD_ID:
            continue
        try:
            await update_server_info_for_guild(g)
        except Exception:
            pass

@tasks.loop(seconds=60)
async def presence_controller():
    if not autopresence_enabled():
        return
    now = now_ts()
    target = normalize_presence_state(_presence_target_state(now))
    current = getattr(bot, "status", discord.Status.online)
    if isinstance(current, discord.Status):
        current_name = current.name
    else:
        current_name = str(current)
    if normalize_presence_state(current_name) == target:
        return
    status_map = {
        "online": discord.Status.online,
        "idle": discord.Status.idle,
        "dnd": discord.Status.dnd,
        "invisible": discord.Status.invisible
    }
    activity = presence_activity(presence_bio()) if presence_bio() else bot.activity
    try:
        await bot.change_presence(status=status_map.get(target, discord.Status.online), activity=activity)
        await log_to("system", f"presence state -> {target}", subsystem="SYNAPTIC", severity="INFO")
    except Exception as exc:
        await log_to("system", f"presence update failed: {exc}", subsystem="SYNAPTIC", severity="WARN")

@tasks.loop(minutes=20)
async def daily_reflection_loop():
    if not daily_reflection_enabled():
        return
    now_dt = datetime.datetime.utcnow()
    if not _daily_reflection_due(now_dt):
        return
    thoughts = await _resolve_thoughts_channel()
    if not thoughts:
        return
    max_messages = int(daily_reflection_cfg().get("max_messages", 120) or 120)
    max_messages = max(50, min(200, max_messages))
    context = await _daily_reflection_context(max_messages)
    reflection = await _generate_daily_reflection(context)
    if not reflection and daily_reflection_cfg().get("fallback_enabled", False):
        reflection = _build_fallback_reflection(context)
    if not reflection:
        return
    try:
        await thoughts.send(reflection[:1900])
    except Exception:
        return
    daily_reflection_cfg()["last_run_utc"] = int(now_dt.timestamp())
    await STORE.mark_dirty()

@tasks.loop(minutes=20)
async def internal_monologue_loop():
    if not internal_monologue_enabled():
        return
    now_ts_val = now_ts()
    if not _internal_monologue_due(now_ts_val):
        return
    thoughts = await _resolve_thoughts_channel()
    if not thoughts:
        return
    context = await _daily_reflection_context(40)
    monologue = await _generate_internal_monologue(context)
    if not monologue:
        return
    max_lines = int(internal_monologue_cfg().get("max_lines", 4) or 4)
    if max_lines > 0:
        monologue = "\n".join(monologue.splitlines()[:max_lines])
    try:
        await thoughts.send(monologue[:1200])
    except Exception:
        return
    internal_monologue_cfg()["last_run_utc"] = int(now_ts_val)
    await STORE.mark_dirty()

@tasks.loop(minutes=30)
async def sentience_maintenance_loop():
    maintenance = sentience_cfg(cfg()).get("maintenance", {})
    if not maintenance or not maintenance.get("enabled", True):
        return
    max_age_hours = float(maintenance.get("ai_queue_max_age_hours", 6) or 6)
    max_age_seconds = max(1, int(max_age_hours * 3600))
    mandy = bot.get_cog("MandyAI")
    if mandy and hasattr(mandy, "prune_queue"):
        try:
            pruned = await mandy.prune_queue(max_age_seconds)
            if pruned:
                await log_to("ai", f"AI queue cleanup removed {pruned} stale job(s)", subsystem="AI", severity="INFO")
        except Exception:
            pass

@tasks.loop(minutes=10)
async def diagnostics_loop():
    ch = await _resolve_diagnostics_channel()
    if not ch:
        return
    dm_bridges = await dm_bridge_list_active()
    lines = [f"Diagnostics Snapshot (UTC {datetime.datetime.utcnow().replace(microsecond=0).isoformat()}Z)"]
    lines.extend(_diagnostic_status_lines(len(dm_bridges)))
    payload = "\n".join(lines[:25])
    diag = diagnostics_cfg()
    msg_id = int(diag.get("message_id", 0) or 0)
    try:
        if msg_id:
            msg = await ch.fetch_message(msg_id)
            await msg.edit(content=payload)
        else:
            msg = await ch.send(payload)
            diag["message_id"] = msg.id
            await STORE.mark_dirty()
        diag["last_update"] = now_ts()
        await STORE.mark_dirty()
    except Exception:
        return

@tasks.loop(minutes=30)
async def manual_upload_loop():
    await manual_upload_if_needed()

@tasks.loop(minutes=12)
async def dm_bridge_archive():
    await archive_inactive_dm_bridges()

@bot.event
async def on_member_join(member: discord.Member):
    try:
        await start_gate(member)
    except Exception as e:
        await debug(f"gate start error: {e}")
    if member.guild.id != ADMIN_GUILD_ID:
        try:
            await update_server_info_for_guild(member.guild)
        except Exception:
            pass

@bot.event
async def on_member_remove(member: discord.Member):
    if member.guild.id != ADMIN_GUILD_ID:
        try:
            await update_server_info_for_guild(member.guild)
        except Exception:
            pass

@bot.event
async def on_guild_update(before: discord.Guild, after: discord.Guild):
    if after.id == ADMIN_GUILD_ID:
        return
    try:
        await ensure_admin_server_channels(after)
        await update_server_info_for_guild(after)
    except Exception:
        pass

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if not bot.user:
        return
    if after.id != bot.user.id:
        return
    try:
        await update_server_info_for_guild(after.guild)
    except Exception:
        pass

async def enforce_command_channels(message: discord.Message) -> bool:
    if not message.guild:
        return False
    content = (message.content or "").strip()
    if not content:
        return False
    if not content.startswith("!"):
        return False
    channels_cfg = cfg().get("command_channels", {})
    mode = channels_cfg.get("mode", "off")
    if mode == "off":
        return False
    user_channel = channels_cfg.get("user", "command-requests")
    god_channel = channels_cfg.get("god", "admin-chat")
    try:
        lvl = await effective_level(message.author)
    except Exception:
        lvl = 0
    target_name = god_channel if lvl >= 90 else user_channel
    channel_name = getattr(message.channel, "name", "")
    if isinstance(message.channel, discord.Thread) and message.channel.parent:
        channel_name = message.channel.parent.name
    if channel_name == target_name:
        return False
    target_ch = discord.utils.get(message.guild.text_channels, name=target_name)
    if not target_ch:
        return False
    snippet = content if len(content) <= 1800 else content[:1797] + "..."
    note = f"{message.author.mention} Wrong channel. Use {target_ch.mention} for commands."
    try:
        await target_ch.send(note + f"\n`{snippet}`")
    except Exception:
        pass
    if mode == "soft":
        return False
    try:
        await safe_delete(message)
    except Exception:
        pass
    return True

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.webhook_id:
        return

    if message.guild:
        now = now_ts()
        update_presence_activity_ts(now)
        if message.guild.id == ADMIN_GUILD_ID and message.author.id == SUPER_USER_ID:
            content = (message.content or "").strip()
            if content.startswith("!") or (bot.user and bot.user in message.mentions):
                update_super_interaction_ts(now)

    # DM inbound -> relay into bridge channel (if active)
    if isinstance(message.channel, discord.DMChannel):
        try:
            ch_id = await dm_bridge_channel_for_user(message.author.id)
            if not ch_id:
                ch_id = await ensure_dm_bridge_active(message.author.id, reason="auto")
            if ch_id:
                ch = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
                if isinstance(ch, discord.TextChannel):
                    await ch.send(f"dY` **{message.author}**: {message.content}")
                    await dm_bridge_touch(message.author.id)
        except Exception:
            pass
        return

    # Prefix command enforcement
    try:
        if await enforce_command_channels(message):
            return
    except Exception as e:
        await debug(f"command channel enforcement error: {e}")

    # GOD-only mention entrypoint
    if bot.user:
        mention_token = f"<@{bot.user.id}>"
        mention_token_nick = f"<@!{bot.user.id}>"
        mentioned = bot.user in message.mentions or mention_token in (message.content or "") or mention_token_nick in (message.content or "")
        if mentioned:
            try:
                lvl = await effective_level(message.author)
            except Exception:
                lvl = 0
            if lvl < MANDY_GOD_LEVEL:
                try:
                    if await MENTION_COOLDOWN.should_notify(message.author.id, MENTION_DM_COOLDOWN_SECONDS):
                        await message.author.send("You're not a god.")
                except Exception:
                    pass
                return
            mandy = bot.get_cog("MandyAI")
            if mandy:
                stripped = strip_bot_mentions(message.content or "", bot.user.id)
                if stripped:
                    try:
                        await mandy.handle_mention(message, stripped)
                    except Exception as e:
                        await debug(f"mandy mention error: {e}")
                return

    # Gate attempts
    try:
        if await handle_gate_attempt(message):
            return
    except Exception as e:
        await debug(f"gate attempt error: {e}")

    # DM bridge channel -> staff sends -> DM user
    try:
        if message.guild and message.guild.id == ADMIN_GUILD_ID:
            lvl = await effective_level(message.author)
            if lvl >= 70:
                # if this channel is bound to a user, relay outbound
                uid = await dm_bridge_user_for_channel(message.channel.id)
                if uid:
                    try:
                        u = await bot.fetch_user(uid)
                        await u.send(message.content)
                        await audit(message.author.id, "DM relay staff->user", {"user_id": uid})
                        await safe_delete(message)
                        await dm_bridge_touch(uid)
                        return
                    except Exception:
                        pass
    except Exception as e:
        await debug(f"dm relay error: {e}")

    # Watchers
    try:
        await watcher_tick(message)
    except Exception as e:
        await debug(f"watcher error: {e}")

    # Chat stats
    try:
        await chat_stats_increment(message)
    except Exception as e:
        await debug(f"chat stats error: {e}")

    # Mirrors
    try:
        await mirror_tick(message)
    except Exception as e:
        await debug(f"mirror error: {e}")

    # Onboarding rule (rules channel auto-role)
    try:
        onboarding = cfg().get("onboarding", {})
        rules_id = int(onboarding.get("rules_channel_id", 0) or 0)
        if rules_id and message.channel.id == rules_id:
            phrases = [p.lower().strip() for p in onboarding.get("phrases", []) if p]
            if phrases and any(p in (message.content or "").lower() for p in phrases):
                role_name = str(onboarding.get("role_name", "Citizen"))
                role = discord.utils.get(message.guild.roles, name=role_name)
                if role and role not in message.author.roles:
                    try:
                        await message.author.add_roles(role, reason="Onboarding phrase matched")
                        await audit(message.author.id, "Onboarding role granted", {"role": role_name})
                    except Exception:
                        await request_elevation("add_role", "missing role permissions", {"role": role_name})
    except Exception:
        pass

    # Long-term sentiment memory (lightweight)
    try:
        mood = classify_mood(message.content or "")
        if mood != "neutral":
            await memory_add(
                "sentiment",
                f"{message.author} ({mood}): {truncate(message.content, 120)}",
                {
                    "author_id": message.author.id,
                    "guild_id": getattr(message.guild, "id", 0),
                    "channel_id": getattr(message.channel, "id", 0),
                },
            )
    except Exception:
        pass

    await bot.process_commands(message)

@bot.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    if not reaction or not user or user.bot:
        return
    message = reaction.message
    if not message or not message.guild:
        return
    try:
        row = await mirror_fetch_src_by_dst(message.id)
    except Exception:
        return
    if not row:
        return
    try:
        src_guild_id = int(row["src_guild"])
        src_channel_id = int(row["src_channel"])
        src_msg_id = int(row["src_msg"])
        src_guild = bot.get_guild(src_guild_id) or await bot.fetch_guild(src_guild_id)
        src_channel = src_guild.get_channel(src_channel_id) or await bot.fetch_channel(src_channel_id)
        src_msg = await src_channel.fetch_message(src_msg_id)
        await src_msg.add_reaction(reaction.emoji)
    except discord.Forbidden:
        await debug("mirror reaction blocked: missing add_reactions permission")
    except discord.NotFound:
        return
    except Exception as exc:
        await debug(f"mirror reaction failed: {exc}")

@bot.event
async def on_typing(channel: discord.abc.Messageable, user: discord.User, when: datetime.datetime):
    if user.bot:
        return

    # user typing in DM -> show indicator in bridge channel (if active)
    if isinstance(channel, discord.DMChannel):
        try:
            ch_id = await dm_bridge_channel_for_user(user.id)
            if not ch_id:
                return
            ch = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
            if isinstance(ch, discord.TextChannel):
                await send_dm_typing_indicator(user.id, ch)
        except Exception:
            pass
        return

    # staff typing in active bridge channel -> relay typing to user
    try:
        if isinstance(channel, discord.TextChannel) and channel.guild and channel.guild.id == ADMIN_GUILD_ID:
            lvl = await effective_level(user)
            if lvl < 70:
                return
            uid = await dm_bridge_user_for_channel(channel.id)
            if uid:
                await relay_staff_typing(channel.id, uid)
    except Exception:
        pass


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.id != SPECIAL_VOICE_USER_ID:
        return

    guild = after.channel.guild if after.channel else before.channel.guild if before.channel else None
    if not guild:
        return
    if guild.id in MOVIE_ACTIVE_GUILDS:
        return

    if after.channel:
        cancel_special_voice_leave_task(guild.id)
        await start_special_user_voice(after.channel)
        return

    if before.channel and after.channel is None:
        schedule_special_voice_leave(guild)

# -----------------------------
# Run
# -----------------------------
attach_mandy_context()
bot.run(DISCORD_TOKEN)
