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
import asyncio
import json
import os
import random
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass
from contextvars import ContextVar
import datetime
import hashlib
from typing import Optional, Dict, Any, List, Tuple, Set, Iterable
import secrets
from mandy.capability_registry import CapabilityRegistry
from mandy.tool_plugin_manager import ToolPluginManager
from mandy.app.config import (
    ADMIN_GUILD_ID,
    SUPER_USER_ID,
    AUTO_GOD_ID,
    MANDY_GOD_LEVEL,
    MENTION_DM_COOLDOWN_SECONDS,
    SPECIAL_VOICE_USER_ID,
    MOVIE_STAY_DEFAULT_MINUTES,
    MOVIE_STAY_MAX_MINUTES,
    GUEST_ROLE_NAME,
    QUARANTINE_ROLE_NAME,
    STAFF_ROLE_NAME,
    ADMIN_ROLE_NAME,
    GOD_ROLE_NAME,
    ROLE_LEVEL_DEFAULTS,
    MIRROR_FAIL_THRESHOLD,
    MIRROR_CACHE_REFRESH,
    SERVER_STATUS_REFRESH,
    INTEGRITY_REFRESH,
    CLEANUP_RESPONSE_TTL,
    DISCORD_TOKEN,
    SERVER_PASSWORD,
    GEMINI_API_KEY,
)
from mandy.app.store import STORE, MENTION_COOLDOWN, cfg, ai_cfg
from mandy.app import state
from mandy.app.api_governor import set_governor_context, reset_governor_context
from mandy.app.api_governor_discord import install_discord_governor
from mandy.app.tasking import spawn_task
from mandy.app.db import (
    db_init,
    db_exec,
    db_one,
    db_all,
    ensure_table_columns,
    db_column_exists,
    ensure_mirror_rules_columns,
    ensure_watchers_columns,
    ensure_users_permissions_columns,
    ensure_mirrors_columns,
    ensure_mirror_messages_columns,
    ensure_dm_bridges_columns,
    ensure_audit_logs_columns,
    db_calibrate,
    db_purge_all,
    db_bootstrap,
)
from mandy.app.logging import log_to, audit, debug, ensure_debug_channel, setup_log
from mandy.app.watchers import mark_mysql_watcher_cache_dirty, watcher_tick, watchers_report
from mandy.app.setup import (
    setup_delay_base,
    setup_pause,
    _setup_pause_on_rate_limit,
)
from mandy.app.media import (
    start_special_user_voice,
    cancel_special_voice_leave_task,
    schedule_special_voice_leave,
    movie_state,
    cancel_movie_stay_task,
    schedule_movie_stay_task,
    movie_get_voice_client,
    movie_start_playback,
    movie_handle_track_end,
    movie_queue_add,
    movie_stop,
    movie_set_volume,
    movie_pause,
    movie_resume,
    movie_skip,
    movie_find_voice_targets,
    movie_resolve_target,
    send_movie_menu,
    MovieTargetSelect,
    MovieLinkModal,
    MovieVolumeModal,
    MovieStayModal,
    MovieControlView,
    SPECIAL_VOICE_LEAVE_TASKS,
    MOVIE_ACTIVE_GUILDS,
    MOVIE_STATES,
    MOVIE_STAY_TASKS,
)
from mandy.app.core import (
    chunk_lines,
    memory_state,
    memory_add,
    memory_recent,
    ark_snapshots,
    phoenix_keys,
    request_elevation,
    classify_mood,
    bot_missing_permissions,
    send_owner_server_report,
    serialize_overwrites,
    deserialize_overwrites,
    strip_bot_mentions,
    is_youtube_url,
    normalize_youtube_url,
    now_ts,
    fmt_ts,
    truncate,
    get_role,
    admin_category_name,
)
from mandy import ambient_engine
from mandy.resolver import parse_channel_id, parse_user_id, rank_members_global, pick_best
from mandy.sentience_layer import sentience_cfg, presence_cfg, voice_line

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

# -----------------------------
# Bot
# -----------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.dm_messages = True

bot = commands.Bot(command_prefix="!", intents=intents)
state.bot = bot

# -----------------------------

from mandy.app.main_rbac import (
    effective_level,
    get_user_level,
    is_super,
    mandy_power_mode_enabled,
    require_level_ctx,
    role_level_map,
)
from mandy.app.main_tools import ToolRegistry, attach_mandy_context, maybe_load_mandy_extension
from mandy.app.main_ux import safe_ctx_send, safe_delete, say_clean, temp_reply


# -----------------------------
# Helpers
# -----------------------------
def mirror_rules_dict() -> Dict[str, Any]:
    return cfg().setdefault("mirror_rules", {})

MIRROR_RULE_INDEX: Dict[str, Dict[int, List[Dict[str, Any]]]] = {
    "server": {},
    "category": {},
    "channel": {},
}
MIRROR_RULE_INDEX_DIRTY = True
MIRROR_RULE_INDEX_MTIME = 0.0

SETUP_GUILD_LOCKS: Dict[int, asyncio.Lock] = {}

MIRROR_BATCH_QUEUES: Dict[int, List[Dict[str, Any]]] = {}
MIRROR_BATCH_TASKS: Dict[int, asyncio.Task] = {}
MIRROR_BATCH_LAST_SEND: Dict[int, float] = {}
MIRROR_BATCH_LOCK = asyncio.Lock()


def _setup_guild_lock(guild_id: int) -> asyncio.Lock:
    lock = SETUP_GUILD_LOCKS.get(guild_id)
    if not lock:
        lock = asyncio.Lock()
        SETUP_GUILD_LOCKS[guild_id] = lock
    return lock


def _mirror_batch_cfg() -> Tuple[bool, float, int, int, float, bool]:
    raw = cfg().get("mirror_batch", {})
    if not isinstance(raw, dict):
        raw = {}
    enabled = bool(raw.get("enabled", True))
    window = float(raw.get("window_seconds", 0.8))
    max_items = int(raw.get("max_items", 10))
    max_chars = int(raw.get("max_chars", 1800))
    burst_window = float(raw.get("burst_window_seconds", 1.2))
    text_only = bool(raw.get("text_only", True))
    window = max(0.0, min(5.0, window))
    max_items = max(2, min(50, max_items))
    max_chars = max(200, min(4000, max_chars))
    burst_window = max(0.0, min(10.0, burst_window))
    return enabled, window, max_items, max_chars, burst_window, text_only


def _mirror_batch_line(message: discord.Message) -> str:
    content = " ".join((message.content or "").split())
    if not content:
        content = "(no text)"
    extras = []
    if message.attachments:
        extras.append(f"attachments:{len(message.attachments)}")
    if message.stickers:
        extras.append(f"stickers:{len(message.stickers)}")
    if message.embeds:
        extras.append(f"embeds:{len(message.embeds)}")
    if extras:
        content = f"{content} [{' '.join(extras)}]"
    content = truncate(content, 160)
    src = f"{message.guild.name}/#{message.channel.name}"
    return f"{src} | {message.author}: {content} | {message.jump_url}"


async def _mirror_batch_flush(dst_id: int) -> None:
    async with MIRROR_BATCH_LOCK:
        items = MIRROR_BATCH_QUEUES.pop(dst_id, [])
        MIRROR_BATCH_TASKS.pop(dst_id, None)
    if not items:
        return
    ch = bot.get_channel(dst_id)
    if not ch:
        try:
            ch = await bot.fetch_channel(dst_id)
        except Exception:
            ch = None
    if not isinstance(ch, discord.TextChannel):
        by_rule: Dict[str, Dict[str, Any]] = {}
        for item in items:
            rid = item.get("rule_id") or ""
            if rid and rid not in by_rule:
                by_rule[rid] = item
        for rid in by_rule:
            rule = mirror_rules_dict().get(rid)
            if rule:
                await mirror_rule_record_failure(rule, "batch target missing")
        return
    lines = ["- " + item["line"] for item in items if item.get("line")]
    header = f"Mirror batch ({len(lines)})"
    chunks = chunk_lines(lines, header, limit=1900)
    try:
        for chunk in chunks:
            await ch.send(chunk, allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        by_rule: Dict[str, Dict[str, Any]] = {}
        for item in items:
            rid = item.get("rule_id") or ""
            if rid and rid not in by_rule:
                by_rule[rid] = item
        for rid in by_rule:
            rule = mirror_rules_dict().get(rid)
            if rule:
                await mirror_rule_record_failure(rule, f"batch send error: {exc}")
        return
    MIRROR_BATCH_LAST_SEND[dst_id] = time.time()
    by_rule: Dict[str, Dict[str, Any]] = {}
    by_guild: Dict[int, Dict[str, Any]] = {}
    for item in items:
        rid = item.get("rule_id") or ""
        if rid:
            by_rule[rid] = item
        gid = item.get("src_guild_id")
        if isinstance(gid, int):
            by_guild[gid] = item
    for rid, item in by_rule.items():
        rule = mirror_rules_dict().get(rid)
        if rule:
            await mirror_rule_mark_success(rule, item.get("content") or "(no text)")
    status = cfg().setdefault("mirror_status", {})
    for gid, item in by_guild.items():
        status[str(gid)] = {
            "last_mirror_ts": now_ts(),
            "last_mirror_author": item.get("author") or "",
            "last_mirror_channel": item.get("src_channel_name") or "",
            "last_mirror_msg": truncate(item.get("content") or "", 180),
        }
    await STORE.mark_dirty()
    await log_to(
        "mirror",
        "Mirror relay batch delivered",
        subsystem="SENSORY",
        severity="INFO",
        details={"count": len(items), "target": dst_id},
    )


async def _mirror_batch_flush_after(dst_id: int, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    await _mirror_batch_flush(dst_id)


async def _mirror_batch_enqueue(
    dst: discord.TextChannel,
    rule: Dict[str, Any],
    message: discord.Message,
    perms: discord.Permissions,
) -> bool:
    enabled, window, max_items, max_chars, burst_window, text_only = _mirror_batch_cfg()
    if not enabled:
        return False
    if text_only and (message.attachments or message.embeds or message.stickers):
        return False
    if not perms.send_messages:
        return False
    now = time.time()
    async with MIRROR_BATCH_LOCK:
        queue = MIRROR_BATCH_QUEUES.get(dst.id, [])
        last_send = MIRROR_BATCH_LAST_SEND.get(dst.id, 0.0)
        if not queue and now - last_send > burst_window:
            return False
        line = _mirror_batch_line(message)
        item = {
            "line": line,
            "rule_id": rule.get("rule_id"),
            "src_guild_id": message.guild.id,
            "src_channel_id": message.channel.id,
            "src_channel_name": message.channel.name,
            "author": str(message.author),
            "content": message.content or "",
        }
        queue.append(item)
        MIRROR_BATCH_QUEUES[dst.id] = queue
        size = sum(len(i.get("line", "")) + 2 for i in queue) + len("Mirror batch (99)")
        flush_now = len(queue) >= max_items or size >= max_chars
        task = MIRROR_BATCH_TASKS.get(dst.id)
        if not task or task.done():
            MIRROR_BATCH_TASKS[dst.id] = asyncio.create_task(_mirror_batch_flush_after(dst.id, window))
    if flush_now:
        await _mirror_batch_flush(dst.id)
    return True

def mark_mirror_rule_index_dirty() -> None:
    global MIRROR_RULE_INDEX_DIRTY
    MIRROR_RULE_INDEX_DIRTY = True

def _rebuild_mirror_rule_index() -> None:
    global MIRROR_RULE_INDEX, MIRROR_RULE_INDEX_DIRTY, MIRROR_RULE_INDEX_MTIME
    index: Dict[str, Dict[int, List[Dict[str, Any]]]] = {
        "server": {},
        "category": {},
        "channel": {},
    }
    for rule in mirror_rules_dict().values():
        if not rule.get("enabled", True):
            continue
        scope = rule.get("scope", "channel")
        if scope not in index:
            scope = "channel"
        if scope == "server":
            key = int(rule.get("source_id", 0) or rule.get("source_guild", 0))
        else:
            key = int(rule.get("source_id", 0))
        if key:
            index[scope].setdefault(key, []).append(rule)
    MIRROR_RULE_INDEX = index
    MIRROR_RULE_INDEX_DIRTY = False
    MIRROR_RULE_INDEX_MTIME = STORE.last_mtime

def mirror_rules_for_message(message: discord.Message) -> List[Dict[str, Any]]:
    global MIRROR_RULE_INDEX_MTIME
    if MIRROR_RULE_INDEX_DIRTY or STORE.last_mtime > MIRROR_RULE_INDEX_MTIME + 0.0001:
        _rebuild_mirror_rule_index()
    channel = message.channel
    candidates: List[Dict[str, Any]] = []
    candidates.extend(MIRROR_RULE_INDEX["channel"].get(channel.id, []))
    if channel.category:
        candidates.extend(MIRROR_RULE_INDEX["category"].get(channel.category.id, []))
    candidates.extend(MIRROR_RULE_INDEX["server"].get(message.guild.id, []))
    return candidates

OWNER_ONBOARD_FEATURES: List[Dict[str, Any]] = [
    {"key": "mirror", "label": "Mirror relay", "description": "Keep server mirror feed active", "locked": True},
    {"key": "logs", "label": "Logging + server info", "description": "Create logging channels and info panel"},
    {"key": "stats", "label": "Chat stats + health", "description": "Track message counts and health snapshot"},
    {"key": "dm_bridge", "label": "DM bridge intake", "description": "Allow DM bridge for owner outreach"},
    {"key": "ai_tools", "label": "AI helper", "description": "Allow Mandy AI tools if configured"},
]

def owner_onboarding_cfg() -> Dict[str, Any]:
    data = cfg().setdefault("owner_onboarding", {})
    if not isinstance(data.get("pending"), dict):
        data["pending"] = {}
    if not isinstance(data.get("history"), dict):
        data["history"] = {}
    defaults = data.get("feature_defaults")
    if not isinstance(defaults, list):
        defaults = ["mirror", "logs", "stats", "dm_bridge", "ai_tools"]
        data["feature_defaults"] = defaults
    if "mirror" not in defaults:
        defaults.append("mirror")
    return data

def owner_onboarding_defaults() -> Set[str]:
    defaults = owner_onboarding_cfg().get("feature_defaults") or []
    if not isinstance(defaults, list):
        defaults = []
    normalized = {str(k) for k in defaults if str(k).strip()}
    normalized.add("mirror")
    return normalized

def owner_onboarding_feature_map() -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for feat in OWNER_ONBOARD_FEATURES:
        key = str(feat.get("key") or "").strip()
        if key:
            result[key] = feat
    return result

def owner_onboarding_locked_keys() -> Set[str]:
    return {k for k, v in owner_onboarding_feature_map().items() if bool(v.get("locked"))}

def normalize_owner_onboarding_features(selected: List[str]) -> List[str]:
    catalog = owner_onboarding_feature_map()
    locked = owner_onboarding_locked_keys()
    chosen = {k for k in (selected or []) if k in catalog}
    chosen |= locked
    if "mirror" in catalog:
        chosen.add("mirror")
    if not chosen:
        chosen = owner_onboarding_defaults()
    return sorted(chosen)

def owner_onboarding_pending() -> Dict[str, Any]:
    data = owner_onboarding_cfg()
    pending = data.get("pending")
    if not isinstance(pending, dict):
        data["pending"] = {}
    return data.setdefault("pending", {})

def owner_onboarding_history() -> Dict[str, Any]:
    data = owner_onboarding_cfg()
    hist = data.get("history")
    if not isinstance(hist, dict):
        data["history"] = {}
    return data.setdefault("history", {})

def owner_onboarding_invite_details() -> Tuple[str, str]:
    for fname in ("passwords.txt",):
        if not os.path.exists(fname):
            continue
        try:
            with open(fname, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("LINK="):
                        parts = line.split("=", 1)
                        if len(parts) == 2:
                            val = parts[1].strip()
                            if val:
                                return val, "passwords.txt"
        except Exception:
            continue
    perms_int = int(soc_onboarding_cfg().get("bot_invite_permissions", 8) or 8)
    if bot.user:
        try:
            link = discord.utils.oauth_url(
                bot.user.id,
                permissions=discord.Permissions(perms_int),
                scopes=("bot", "applications.commands"),
            )
            return link, "generated"
        except Exception:
            return "", ""
    return "", ""

def owner_onboarding_invite_link() -> str:
    link, _ = owner_onboarding_invite_details()
    return link

def satellite_features_cfg() -> Dict[str, Any]:
    feats = cfg().get("satellite_features")
    if not isinstance(feats, dict):
        cfg()["satellite_features"] = {}
    return cfg().setdefault("satellite_features", {})

def owner_onboarding_feature_labels(keys: Iterable[str]) -> List[str]:
    catalog = owner_onboarding_feature_map()
    labels: List[str] = []
    for key in keys:
        if key in catalog:
            labels.append(str(catalog[key].get("label", key)))
    return labels

def set_satellite_features(guild_id: int, features: List[str]) -> None:
    feats_cfg = satellite_features_cfg()
    feats_cfg[str(guild_id)] = {k: True for k in features}

async def save_owner_onboarding_request(user_id: int, features: List[str], invite_link: str, owner_confirmed: bool, can_invite: bool) -> Dict[str, Any]:
    record = {
        "features": normalize_owner_onboarding_features(features),
        "invite_link": invite_link,
        "owner_confirmed": bool(owner_confirmed),
        "can_invite": bool(can_invite),
        "created_at": now_ts(),
        "status": "pending",
        "source": "self_service",
    }
    owner_onboarding_pending()[str(user_id)] = record
    await STORE.mark_dirty()
    try:
        await audit(user_id, "Owner onboarding saved", {"features": record["features"]})
    except Exception:
        pass
    return record

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
        cache=state.GLOBAL_USER_RESOLVER,
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

def fast_mode_enabled() -> bool:
    env = os.getenv("MANDY_FAST_MODE") or os.getenv("FAST_MODE") or ""
    if env:
        val = env.strip().lower()
        if val in ("1", "true", "yes", "y", "on"):
            return True
        if val in ("0", "false", "no", "n", "off"):
            return False
    tuning = cfg().get("tuning", {})
    if not isinstance(tuning, dict):
        return False
    return bool(tuning.get("fast_mode", False))

def mysql_purge_on_startup_enabled() -> bool:
    env = os.getenv("MYSQL_PURGE_ON_STARTUP") or ""
    if env:
        val = env.strip().lower()
        if val in ("1", "true", "yes", "y", "on"):
            return True
        if val in ("0", "false", "no", "n", "off"):
            return False
    val = cfg().get("mysql_purge_on_startup", None)
    if val is None:
        cfg()["mysql_purge_on_startup"] = True
        spawn_task(STORE.mark_dirty(), "store")
        return True
    return bool(val)

def autopresence_enabled() -> bool:
    return bool(presence_config().get("autopresence_enabled", False))

def update_presence_activity_ts(message_ts: int) -> None:
    presence_config()["last_message_ts"] = message_ts

def update_super_interaction_ts(message_ts: int) -> None:
    presence_config()["last_super_interaction_ts"] = message_ts

COMMAND_CONTEXT = ContextVar("mandy_command_context", default=False)

COMMAND_CONTEXT = ContextVar("mandy_command_context", default=False)

def typing_delay_seconds() -> float:
    if fast_mode_enabled():
        return 0.0
    try:
        return max(0.0, float(cfg().get("typing_delay_seconds", 5.0)))
    except Exception:
        return 5.0

def discord_send_delay_base() -> float:
    if fast_mode_enabled():
        return 0.0
    try:
        tuning = cfg().get("tuning", {})
        if not isinstance(tuning, dict):
            return 0.0
        return max(0.0, float(tuning.get("discord_send_delay", 0.0)))
    except Exception:
        return 0.0

def discord_send_delay() -> float:
    override = state.DISCORD_SEND_DELAY_OVERRIDE
    if override is not None:
        try:
            return max(0.0, float(override))
        except Exception:
            return discord_send_delay_base()
    return discord_send_delay_base()

def _discord_rate_limit_info(exc: Exception) -> Tuple[bool, Optional[float]]:
    status = getattr(exc, "status", None)
    retry_after = getattr(exc, "retry_after", None)
    if status == 429 or retry_after is not None:
        try:
            retry_val = float(retry_after) if retry_after is not None else None
        except Exception:
            retry_val = None
        return True, retry_val
    msg = str(exc).lower()
    if "rate limit" in msg or "too many requests" in msg:
        return True, None
    return False, None

def _discord_adjust_send_delay(success: bool, retry_after: Optional[float] = None) -> None:
    tuning = cfg().get("tuning", {})
    if not isinstance(tuning, dict):
        return
    if not bool(tuning.get("discord_send_adaptive", True)):
        return
    current = state.DISCORD_SEND_DELAY_OVERRIDE if state.DISCORD_SEND_DELAY_OVERRIDE is not None else discord_send_delay_base()
    if success:
        new_delay = max(state.DISCORD_SEND_DELAY_MIN, current - state.DISCORD_SEND_DELAY_STEP)
    else:
        bump = (retry_after + 0.25) if retry_after else (current * 1.5 + 0.25)
        new_delay = min(state.DISCORD_SEND_DELAY_MAX, max(current, bump))
    state.DISCORD_SEND_DELAY_OVERRIDE = new_delay

def dm_bridge_history_limit() -> int:
    try:
        return max(5, min(200, int(cfg().get("dm_bridge_history_limit", 50))))
    except Exception:
        return 50

async def typing_delay(channel: discord.abc.Messageable, seconds: Optional[float] = None) -> None:
    delay = typing_delay_seconds() if seconds is None else max(0.0, float(seconds))
    if delay <= 0:
        return
    try:
        if hasattr(channel, "typing"):
            async with channel.typing():
                await asyncio.sleep(delay)
        else:
            await asyncio.sleep(delay)
    except Exception:
        return

TYPING_DELAY_PATCHED = False
_ORIG_MESSAGEABLE_SEND = None

def _skip_typing_for_channel(channel: discord.abc.Messageable) -> bool:
    if COMMAND_CONTEXT.get():
        return True
    if isinstance(channel, discord.TextChannel):
        if channel.guild and channel.guild.id == ADMIN_GUILD_ID:
            return True
        logs = cfg().get("logs", {}) if isinstance(cfg().get("logs", {}), dict) else {}
        log_ids = {int(v) for v in logs.values() if str(v).isdigit()}
        if channel.id in log_ids:
            return True
    return False

def install_typing_delay_patch() -> None:
    global TYPING_DELAY_PATCHED, _ORIG_MESSAGEABLE_SEND
    if TYPING_DELAY_PATCHED:
        return
    _ORIG_MESSAGEABLE_SEND = discord.abc.Messageable.send

    async def _patched_send(self, *args, **kwargs):
        try:
            if not _skip_typing_for_channel(self):
                await typing_delay(self)
        except Exception:
            pass
        if not _skip_typing_for_channel(self):
            delay = discord_send_delay()
            if delay > 0:
                try:
                    await asyncio.sleep(delay)
                except Exception:
                    pass
        try:
            result = await _ORIG_MESSAGEABLE_SEND(self, *args, **kwargs)
            _discord_adjust_send_delay(success=True)
            return result
        except discord.HTTPException as exc:
            is_rate, retry_after = _discord_rate_limit_info(exc)
            if is_rate:
                _discord_adjust_send_delay(success=False, retry_after=retry_after)
                if retry_after and retry_after > 0:
                    try:
                        await asyncio.sleep(float(retry_after))
                        result = await _ORIG_MESSAGEABLE_SEND(self, *args, **kwargs)
                        _discord_adjust_send_delay(success=True)
                        return result
                    except Exception:
                        pass
            raise

    discord.abc.Messageable.send = _patched_send
    TYPING_DELAY_PATCHED = True

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
    if last_msg and now - last_msg <= 300:
        return "idle"
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

def roast_cfg() -> Dict[str, Any]:
    return cfg().setdefault("roast", {})

def roast_enabled() -> bool:
    return bool(roast_cfg().get("enabled", False))

def roast_trigger_word() -> str:
    return str(roast_cfg().get("trigger_word", "mandy") or "mandy").strip().lower()

def roast_use_ai() -> bool:
    return bool(roast_cfg().get("use_ai", True))

def roast_trigger_regex() -> re.Pattern:
    word = roast_trigger_word() or "mandy"
    letters = [re.escape(ch) for ch in word if ch.strip()]
    if not letters:
        letters = list("mandy")
    pattern = r"(?i)" + r"[\W_]*".join(letters)
    return re.compile(pattern)

def roast_intent(text: str) -> bool:
    t = (text or "").lower()
    if "roast me" in t or "roast" in t:
        return True
    if "insult" in t or "make fun" in t or "mock" in t or "trash me" in t:
        return True
    if "diss" in t or "clown me" in t:
        return True
    if "shut up" in t or "shutup" in t or "stfu" in t:
        return True
    return False

async def _roast_intent_gemini(text: str) -> bool:
    client = _mandy_ai_client()
    if not client or not getattr(client, "available", False):
        return False
    ai = ai_cfg()
    model = str(ai.get("router_model") or ai.get("default_model") or "gemini-2.5-flash-lite")
    system_prompt = (
        "You are a strict classifier. Return JSON only: {\"roast\": true|false}. "
        "Decide true if the message is a direct insult or hostile intent toward Mandy, "
        "or explicitly asks for a roast. Otherwise false."
    )
    user_prompt = f"Message: {text}"
    try:
        raw = await client.generate(system_prompt, user_prompt, model=model, response_format="json", timeout=8.0)
        data = json.loads(raw or "{}")
        return bool(data.get("roast", False))
    except Exception:
        return False

def roast_opt_in_users() -> Set[str]:
    raw = roast_cfg().get("opt_in_users", []) or []
    return {str(uid) for uid in raw}

def roast_allowed_guilds() -> Set[int]:
    raw = roast_cfg().get("allowed_guilds", []) or []
    return {int(gid) for gid in raw if str(gid).isdigit()}

def roast_auto_opt_in_guilds() -> Set[int]:
    raw = roast_cfg().get("auto_opt_in_guilds", []) or []
    return {int(gid) for gid in raw if str(gid).isdigit()}

def roast_guild_allowed(guild_id: int) -> bool:
    allowed = roast_allowed_guilds()
    if allowed and guild_id not in allowed:
        return False
    return True

def roast_user_opted_in(user_id: int, guild_id: Optional[int] = None) -> bool:
    if str(user_id) in roast_opt_in_users():
        return True
    if guild_id and guild_id in roast_auto_opt_in_guilds():
        return True
    return False

def roast_channel_allowed(channel_id: int) -> bool:
    cfg_roast = roast_cfg()
    allowed = {int(x) for x in (cfg_roast.get("allowed_channels", []) or []) if str(x).isdigit()}
    blocked = {int(x) for x in (cfg_roast.get("blocked_channels", []) or []) if str(x).isdigit()}
    if channel_id in blocked:
        return False
    if allowed and channel_id not in allowed:
        return False
    return True

def _roast_history_key(guild_id: int, user_id: int) -> str:
    return f"{guild_id}:{user_id}"

def record_roast_history(message: discord.Message) -> None:
    if not message.guild:
        return
    content = (message.content or "").strip()
    if not content:
        return
    runtime = getattr(bot, "mandy_runtime", None)
    if not isinstance(runtime, dict):
        return
    history = runtime.setdefault("roast_history", {})
    key = _roast_history_key(message.guild.id, message.author.id)
    lst = history.get(key, [])
    lst.append(content)
    max_history = int(roast_cfg().get("max_history", 5) or 5)
    max_history = max(1, min(20, max_history))
    history[key] = lst[-max_history:]

def _roast_traits(messages: List[str]) -> List[str]:
    traits: List[str] = []
    joined = " ".join(messages)
    if not messages:
        return traits
    if any(len(m) > 120 for m in messages):
        traits.append("writes essays like it's a final exam")
    if sum(m.count("?") for m in messages) >= 3:
        traits.append("collects question marks like they're rare items")
    if any(m.isupper() and len(m) > 5 for m in messages):
        traits.append("has a caps-lock addiction")
    if "..." in joined:
        traits.append("loves dramatic pauses")
    if len(joined.split()) < 10:
        traits.append("keeps it short because why type more")
    if not traits:
        traits.append("posts with mysterious energy and no context")
    return traits[:2]

def generate_playful_roast(user: discord.abc.User, messages: List[str]) -> str:
    traits = _roast_traits(messages)
    line = " and ".join(traits)
    return f"🪞 Roast mode (playful): {user.mention}, you {line}. Respectfully."

async def generate_roast_with_gemini(user: discord.abc.User, messages: List[str]) -> Optional[str]:
    if not roast_use_ai():
        return None
    client = _mandy_ai_client()
    if not client or not getattr(client, "available", False):
        return None
    ai = ai_cfg()
    model = str(ai.get("router_model") or ai.get("default_model") or "gemini-2.5-flash-lite")
    system_prompt = (
        "You write playful, light roasts. Keep it safe and non-abusive. "
        "No slurs, hate, threats, sexual content, or protected-class remarks. "
        "Keep to 1-2 short sentences. Avoid doxxing or personal data."
    )
    recent = [m[:200] for m in messages][-5:]
    user_prompt = (
        "Create a playful roast for the user. "
        f"User: {getattr(user, 'display_name', 'user')}. "
        f"Recent messages: {json.dumps(recent, ensure_ascii=True)}"
    )
    try:
        text = await client.generate(system_prompt, user_prompt, model=model, response_format=None, timeout=20.0)
        return (text or "").strip()
    except Exception:
        return None

async def _recent_channel_context(channel: discord.abc.Messageable, limit: int = 10) -> List[str]:
    if not hasattr(channel, "history"):
        return []
    lines: List[str] = []
    try:
        async for msg in channel.history(limit=max(1, min(20, int(limit)))):
            if getattr(msg.author, "bot", False):
                continue
            content = (msg.content or "").strip()
            if not content and getattr(msg, "attachments", None):
                content = "[attachment]"
            if not content:
                continue
            author = getattr(msg.author, "display_name", "user")
            lines.append(f"{author}: {content[:200]}")
    except Exception:
        return []
    return list(reversed(lines))

def _json_preview(value: Any, max_len: int = 1800) -> str:
    try:
        text = json.dumps(value, indent=2, ensure_ascii=False)
    except Exception:
        text = str(value)
    if len(text) > max_len:
        return ""
    return text

def _set_json_path(root: Dict[str, Any], path: str, value: Any) -> Tuple[bool, str]:
    parts = [p.strip() for p in str(path).split(".") if p.strip()]
    if not parts:
        return False, "Path cannot be empty."
    cur: Any = root
    for key in parts[:-1]:
        if not isinstance(cur, dict):
            return False, f"Path segment '{key}' is not a dict."
        cur = cur.setdefault(key, {})
    if not isinstance(cur, dict):
        return False, "Target container is not a dict."
    cur[parts[-1]] = value
    return True, ""

async def maybe_roast_message(message: discord.Message) -> bool:
    if not message.guild or not message.content:
        return False
    if not roast_enabled():
        return False
    if not roast_guild_allowed(message.guild.id):
        return False
    content = message.content or ""
    bot_mentioned = bool(bot.user and bot.user in getattr(message, "mentions", []))
    if not bot_mentioned and not roast_trigger_regex().search(content):
        return False
    if not roast_intent(content):
        if not await _roast_intent_gemini(content):
            return False
    if not roast_user_opted_in(message.author.id, message.guild.id):
        return False
    if not roast_channel_allowed(message.channel.id):
        return False
    runtime = getattr(bot, "mandy_runtime", None)
    if not isinstance(runtime, dict):
        return False
    last = runtime.setdefault("roast_last", {})
    now = time.time()
    cooldown = int(roast_cfg().get("cooldown_seconds", 600) or 600)
    cooldown = max(600, cooldown)
    last_ts = float(last.get(str(message.author.id), 0) or 0)
    if now - last_ts < max(5, cooldown):
        return False
    history = runtime.setdefault("roast_history", {})
    key = _roast_history_key(message.guild.id, message.author.id)
    recent = list(history.get(key, []))
    recent.append(message.content.strip())
    max_history = int(roast_cfg().get("max_history", 5) or 5)
    max_history = max(1, min(20, max_history))
    recent = recent[-max_history:]
    channel_context = await _recent_channel_context(message.channel, limit=10)
    roast_text = await generate_roast_with_gemini(message.author, channel_context or recent)
    if not roast_text:
        roast_text = generate_playful_roast(message.author, recent)
    try:
        await message.reply(roast_text, mention_author=True)
    except Exception:
        return False
    last[str(message.author.id)] = now
    return True

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

def _setup_status_cfg() -> Dict[str, Any]:
    diag = diagnostics_cfg()
    return diag.setdefault("setup_status", {})

async def _resolve_setup_status_channel() -> Optional[discord.TextChannel]:
    ch = await _resolve_diagnostics_channel()
    if isinstance(ch, discord.TextChannel):
        cfg_entry = _setup_status_cfg()
        if cfg_entry.get("channel_id") != ch.id:
            cfg_entry["channel_id"] = ch.id
            await STORE.mark_dirty()
        return ch
    return None

@dataclass
class SetupPhaseResult:
    name: str
    status: str
    details: str
    duration_ms: int

async def _update_setup_status_panel(results: List[SetupPhaseResult], note: str = "") -> None:
    ch = await _resolve_setup_status_channel()
    if not ch:
        return
    lines = ["**Mandy Setup Status**"]
    now_text = (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    lines.append(f"Updated: {now_text}")
    for r in results:
        icon = {
            "OK": "✅",
            "UPDATED": "✅",
            "SKIPPED": "⏭️",
            "QUEUED": "🕒",
            "FAILED": "⚠️",
            "FALLBACK": "⚠️",
        }.get(r.status, "•")
        detail = f" - {r.details}" if r.details else ""
        lines.append(f"{icon} {r.name} ({r.duration_ms}ms){detail}")
    if note:
        lines.append(note)
    payload = "\n".join(lines[:25])
    state = _setup_status_cfg()
    msg_id = int(state.get("message_id", 0) or 0)
    try:
        if msg_id:
            msg = await ch.fetch_message(msg_id)
            await msg.edit(content=payload)
        else:
            msg = await ch.send(payload)
            state["message_id"] = msg.id
            await STORE.mark_dirty()
    except Exception:
        return

async def _start_task_if_needed(task: tasks.Loop):
    try:
        if not task.is_running():
            task.start()
    except Exception:
        pass

async def run_boot_orchestrator() -> List[SetupPhaseResult]:
    results: List[SetupPhaseResult] = []

    async def run_phase(name: str, fn):
        start = time.time()
        status = "OK"
        details = ""
        try:
            status, details = await fn()
        except Exception as exc:
            status = "FAILED"
            details = str(exc)[:160]
        duration_ms = int((time.time() - start) * 1000)
        results.append(SetupPhaseResult(name=name, status=status, details=details, duration_ms=duration_ms))
        await _update_setup_status_panel(results)

    async def phase_plugins():
        if hasattr(bot, "mandy_plugin_manager"):
            await bot.mandy_plugin_manager.load_all()
            return "OK", "plugins loaded"
        return "SKIPPED", "no plugin manager"

    async def phase_db():
        try:
            await db_init()
            if mysql_purge_on_startup_enabled():
                ok = await db_purge_all()
                if ok:
                    return "OK", "mysql purged + bootstrapped"
                state.POOL = None
                return "FALLBACK", "mysql purge failed, using JSON"
            await db_bootstrap()
            return "OK", "mysql ready"
        except Exception:
            state.POOL = None
            return "FALLBACK", "mysql disabled, using JSON"

    async def phase_mirrors():
        await migrate_legacy_json_mirrors()
        await migrate_legacy_mysql_mirrors()
        await mirror_rules_sync()
        return "OK", "mirror rules synced"

    async def phase_controls():
        bot.add_view(MirrorControls())
        return "OK", "controls registered"

    async def phase_status():
        try:
            await apply_bot_status()
            return "OK", "presence applied"
        except Exception:
            return "FAILED", "presence apply failed"

    async def phase_ambient():
        try:
            await ambient_engine.start_ambient_engine(bot)
            return "OK", "ambient engine online"
        except Exception as exc:
            return "FAILED", str(exc)[:120]

    async def phase_admin_hub():
        admin = bot.get_guild(ADMIN_GUILD_ID)
        if not admin:
            return "SKIPPED", "admin guild missing"
        await ensure_roles(admin)
        await apply_guest_permissions(admin)
        await apply_quarantine_permissions(admin)
        return "OK", "roles + perms verified"

    async def phase_admin_channels():
        admin = bot.get_guild(ADMIN_GUILD_ID)
        if not admin:
            return "SKIPPED", "admin guild missing"
        if auto_setup_enabled():
            return "SKIPPED", "auto setup enabled"
        for g in bot.guilds:
            if g.id == ADMIN_GUILD_ID:
                continue
            await ensure_admin_server_channels(g)
            await ensure_server_mirror_rule(g)
            await update_server_info_for_guild(g)
        return "OK", "admin channels synced"

    async def phase_auto_setup():
        if auto_setup_enabled():
            spawn_task(
                auto_setup_all_guilds(do_backfill=auto_backfill_enabled(), force_backfill=False),
                "setup",
            )
            return "QUEUED", "auto setup queued"
        return "SKIPPED", "auto setup disabled"

    async def phase_backfill():
        if not chat_stats_enabled():
            return "SKIPPED", "chat stats disabled"
        if auto_backfill_enabled():
            spawn_task(backfill_chat_stats_all_guilds(), "stats")
            return "QUEUED", "backfill queued"
        return "SKIPPED", "backfill disabled"

    async def phase_loops():
        await audit(SUPER_USER_ID, "Mandy OS online", {"mysql": bool(state.POOL)})
        await _start_task_if_needed(config_reload)
        await _start_task_if_needed(json_autosave)
        await _start_task_if_needed(mirror_integrity_check)
        await _start_task_if_needed(server_status_update)
        await _start_task_if_needed(dm_bridge_archive)
        await _start_task_if_needed(presence_controller)
        await _start_task_if_needed(daily_reflection_loop)
        await _start_task_if_needed(internal_monologue_loop)
        await _start_task_if_needed(sentience_maintenance_loop)
        await _start_task_if_needed(diagnostics_loop)
        await _start_task_if_needed(manual_upload_loop)
        await _start_task_if_needed(soc_access_sync_loop)
        # One-time SOC permission + role sync (avoid waiting for the periodic loop).
        admin = bot.get_guild(ADMIN_GUILD_ID)
        if admin:
            try:
                initial_delay = int(soc_access_cfg().get("initial_delay_seconds", 60) or 60)
            except Exception:
                initial_delay = 60
            initial_delay = max(0, min(3600, initial_delay))
            if initial_delay:
                await asyncio.sleep(initial_delay)
            try:
                await soc_apply_core_permissions(admin)
                await soc_apply_admin_server_permissions()
            except Exception:
                pass
            for member in list(getattr(admin, "members", []) or []):
                if isinstance(member, discord.Member):
                    try:
                        await soc_sync_member_access(member)
                    except Exception:
                        continue
        await resume_live_stats_panels()
        await resume_global_live_panel()
        return "OK", "maintenance loops running"

    await run_phase("Load Plugins", phase_plugins)
    await run_phase("Database Init", phase_db)
    await run_phase("Mirror Sync", phase_mirrors)
    await run_phase("Register Controls", phase_controls)
    await run_phase("Apply Status", phase_status)
    await run_phase("Ambient Engine", phase_ambient)
    await run_phase("Admin Roles", phase_admin_hub)
    await run_phase("Admin Channels", phase_admin_channels)
    await run_phase("Auto Setup", phase_auto_setup)
    await run_phase("Backfill", phase_backfill)
    await run_phase("Start Loops", phase_loops)

    await _update_setup_status_panel(results, note="Setup complete.")
    return results

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

    lines.append(f"Core: guilds={len(bot.guilds)} voice_clients={len(bot.voice_clients)} mysql={'on' if state.POOL else 'off'}")
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


def chat_stats_enabled() -> bool:
    # Feature toggle: disable chat stats collection/commands unless explicitly enabled.
    return bool(cfg().get("chat_stats_enabled", False))


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
    ts = dt or datetime.datetime.now(datetime.timezone.utc)
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
    last_dt = datetime.datetime.fromtimestamp(last_reset, datetime.timezone.utc)
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
    if not chat_stats_enabled():
        return
    if not message.guild:
        return
    if message.author.bot:
        return

    now_dt = message.created_at or datetime.datetime.now(datetime.timezone.utc)
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
    now_dt = datetime.datetime.now(datetime.timezone.utc)
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
    task = state.LIVE_STATS_TASKS.pop(guild_id, None)
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
    if not chat_stats_enabled():
        return
    try:
        ch: Optional[discord.TextChannel] = None
        msg: Optional[discord.Message] = None
        while True:
            await asyncio.sleep(10)
            guild = bot.get_guild(guild_id)
            if not guild:
                break
            if not ch:
                ch = bot.get_channel(channel_id)
                if not ch:
                    try:
                        ch = await bot.fetch_channel(channel_id)
                    except Exception:
                        break
                if not isinstance(ch, discord.TextChannel):
                    break
            if msg is None:
                try:
                    msg = ch.get_partial_message(message_id)
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
        state.LIVE_STATS_TASKS.pop(guild_id, None)

async def resume_live_stats_panels():
    if not chat_stats_enabled():
        return
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
        if gid in state.LIVE_STATS_TASKS:
            continue
        state.LIVE_STATS_TASKS[gid] = spawn_task(live_stats_loop(gid, ch_id, msg_id, window), "stats")

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
    now_dt = datetime.datetime.now(datetime.timezone.utc)
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
    task = state.LIVE_STATS_TASKS.pop("GLOBAL", None)
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
    if not chat_stats_enabled():
        return
    try:
        ch: Optional[discord.TextChannel] = None
        msg: Optional[discord.Message] = None
        while True:
            await asyncio.sleep(10)
            if not ch:
                ch = bot.get_channel(channel_id)
                if not ch:
                    try:
                        ch = await bot.fetch_channel(channel_id)
                    except Exception:
                        break
                if not isinstance(ch, discord.TextChannel):
                    break
            if msg is None:
                try:
                    msg = ch.get_partial_message(message_id)
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
        state.LIVE_STATS_TASKS.pop("GLOBAL", None)

async def resume_global_live_panel():
    if not chat_stats_enabled():
        return
    info = chat_stats_global_live_message()
    if not info:
        return
    ch_id = int(info.get("channel_id", 0))
    msg_id = int(info.get("message_id", 0))
    if not ch_id or not msg_id:
        return
    window = normalize_stats_window(info.get("window"), "rolling24")
    if "GLOBAL" in state.LIVE_STATS_TASKS:
        return
    state.LIVE_STATS_TASKS["GLOBAL"] = spawn_task(
        global_live_stats_loop(ch_id, msg_id, window),
        "stats",
    )

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
    if not state.POOL:
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
    mark_mirror_rule_index_dirty()
    await STORE.mark_dirty()
    if state.POOL:
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
    mark_mirror_rule_index_dirty()
    await STORE.mark_dirty()
    if state.POOL:
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
    if not state.POOL:
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
    mark_mirror_rule_index_dirty()
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
    if not chat_stats_enabled():
        return
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
    if not chat_stats_enabled():
        return
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
    if not state.POOL:
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
    if state.POOL:
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
    if state.POOL:
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

    if await _mirror_batch_enqueue(dst, rule, message, perms):
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

    MIRROR_BATCH_LAST_SEND[dst.id] = time.time()

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
    candidates = mirror_rules_for_message(message)
    if not candidates:
        return
    seen = set()
    for rule in candidates:
        if not rule.get("enabled", True):
            continue
        if not rule_matches_message(rule, message):
            continue
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
    if state.POOL:
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
    if state.POOL:
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
    if state.POOL:
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
    target_name = "ENGINEERING" if name.lower().startswith("engineering") or "dm" in name.lower() else name
    try:
        return await ensure_category(admin, target_name)
    except Exception:
        return None

async def dm_bridge_sync_history(user_id: int, ch: discord.TextChannel, limit: Optional[int] = None):
    if limit is None:
        limit = dm_bridge_history_limit()
    try:
        user = await bot.fetch_user(user_id)
        dm = user.dm_channel or await user.create_dm()
        lines = []
        async for m in dm.history(limit=limit, oldest_first=True):
            who = "Mandy" if m.author.id == bot.user.id else m.author.name
            content = (m.content or "").replace("\n", " ").strip()
            if not content and m.attachments:
                names = ", ".join(att.filename for att in m.attachments if att.filename)
                content = f"[attachment] {names}".strip()
            if not content:
                content = "[message]"
            lines.append(f"[{m.created_at:%Y-%m-%d %H:%M}] {who}: {content}")
        await ch.send(f"dY\"\" **DM Bridge Opened** for <@{user_id}>")
        if lines:
            await ch.send("```text\n" + "\n".join(lines)[-1800:] + "\n```")
    except Exception:
        await ch.send(f"dY\"\" **DM Bridge Opened** for <@{user_id}>\nCould not pull DM history.")

def _dm_bridge_format_line(author: str, content: str, attachments: List[discord.Attachment]) -> str:
    clean = (content or "").strip()
    if not clean and attachments:
        names = ", ".join(att.filename for att in attachments if att.filename)
        clean = f"[attachment] {names}".strip()
    if not clean:
        clean = "[message]"
    return f"dY` **{author}**: {clean}"

async def ensure_dm_bridge_channel(user_id: int, active: bool = True) -> Optional[discord.TextChannel]:
    admin = bot.get_guild(ADMIN_GUILD_ID)
    if not admin:
        return None
    async with _setup_guild_lock(admin.id):
        info = await dm_bridge_get(user_id)
        ch = None
        if info and info.get("channel_id"):
            ch = admin.get_channel(int(info["channel_id"]))
        if not ch:
            matches = [c for c in admin.text_channels if c.name in (f"dm-{user_id}", f"archived-dm-{user_id}")]
            if matches:
                ch = sorted(matches, key=lambda c: c.id)[0]
                if len(matches) > 1:
                    await debug(f"Duplicate DM bridge channels for {user_id}: {len(matches)}")
        if not ch:
            try:
                fetched = await admin.fetch_channels()
                fetched_matches = [
                    c for c in fetched
                    if isinstance(c, discord.TextChannel) and c.name in (f"dm-{user_id}", f"archived-dm-{user_id}")
                ]
                if fetched_matches:
                    ch = sorted(fetched_matches, key=lambda c: c.id)[0]
            except Exception:
                pass
        cat_name = "ENGINEERING"
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
        try:
            await ensure_dm_bridge_controls(user_id, ch)
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
    await dm_ai_disable(user_id, reason="bridge_closed")

def dm_bridge_controls_state() -> Dict[str, Any]:
    return cfg().setdefault("dm_bridge_controls", {})

def dm_ai_state() -> Dict[str, Any]:
    return cfg().setdefault("dm_ai", {})

async def dm_ai_enable(user_id: int, enabled_by: int, bridge_channel_id: int):
    state = dm_ai_state()
    state[str(user_id)] = {
        "enabled_at": now_ts(),
        "enabled_by": int(enabled_by),
        "bridge_channel_id": int(bridge_channel_id or 0),
    }
    await STORE.mark_dirty()
    if enabled_by:
        await audit(enabled_by, "DM AI enabled", {"user_id": user_id, "channel_id": bridge_channel_id})

async def dm_ai_disable(user_id: int, reason: str = "", actor_id: int = 0):
    state = dm_ai_state()
    if state.pop(str(user_id), None) is not None:
        await STORE.mark_dirty()
        if actor_id:
            await audit(actor_id, "DM AI disabled", {"user_id": user_id, "reason": reason})

async def dm_ai_is_enabled(user_id: int) -> bool:
    state = dm_ai_state()
    if str(user_id) not in state:
        return False
    info = await dm_bridge_get(user_id)
    if not info or not info.get("active"):
        await dm_ai_disable(user_id, reason="bridge_inactive")
        return False
    return True

def dm_bridge_controls_content(user_id: int, channel_id: int) -> str:
    state = dm_ai_state().get(str(user_id), {}) if isinstance(dm_ai_state(), dict) else {}
    enabled = "on" if state else "off"
    enabled_at = fmt_ts(int(state.get("enabled_at", 0) or 0))
    by_id = int(state.get("enabled_by", 0) or 0)
    by_text = f"<@{by_id}>" if by_id else "n/a"
    return (
        f"**DM Bridge Controls**\n"
        f"- User: <@{user_id}>\n"
        f"- Bridge: <#{channel_id}>\n"
        f"- AI: {enabled} | enabled_at={enabled_at} | enabled_by={by_text}"
    )

async def ensure_dm_bridge_controls(user_id: int, ch: discord.TextChannel):
    if not ch or not isinstance(ch, discord.TextChannel):
        return
    state = dm_bridge_controls_state()
    current = state.get(str(user_id), {}) if isinstance(state.get(str(user_id)), dict) else {}
    msg_id = int(current.get("message_id", 0) or 0)
    ch_id = int(current.get("channel_id", 0) or 0)
    content = dm_bridge_controls_content(user_id, ch.id)
    view = DmBridgeControlView(user_id)

    msg = None
    if msg_id and ch_id == ch.id:
        try:
            msg = await ch.fetch_message(msg_id)
        except Exception:
            msg = None
    if not msg:
        try:
            msg = await ch.send(content, view=view)
            try:
                await msg.pin()
            except Exception:
                pass
        except Exception:
            return
    else:
        try:
            await msg.edit(content=content, view=view)
        except Exception:
            pass

    state[str(user_id)] = {"channel_id": ch.id, "message_id": msg.id}
    await STORE.mark_dirty()

async def dm_bridge_list_active() -> List[Dict[str, Any]]:
    bridges: List[Dict[str, Any]] = []
    if state.POOL:
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
    last = state.TYPING_INDICATORS.get(user_id, 0.0)
    if now - last < state.TYPING_RATE_SECONDS:
        return
    state.TYPING_INDICATORS[user_id] = now
    try:
        msg = await ch.send("✏️ User is typing...")
    except Exception:
        return
    async def _cleanup(m: discord.Message):
        await asyncio.sleep(state.TYPING_RATE_SECONDS)
        try:
            await m.delete()
        except Exception:
            pass
    spawn_task(_cleanup(msg), "cleanup")

async def relay_staff_typing(channel_id: int, user_id: int):
    now = time.time()
    last = state.BRIDGE_TYPING_INDICATORS.get(channel_id, 0.0)
    if now - last < state.TYPING_RATE_SECONDS:
        return
    state.BRIDGE_TYPING_INDICATORS[channel_id] = now
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
    # migrate old Guest level defaults (historically 1) to 10 unless the operator has explicitly customized it
    if levels.get(GUEST_ROLE_NAME) in (None, 1):
        levels[GUEST_ROLE_NAME] = 10
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

def soc_access_cfg() -> Dict[str, Any]:
    access = cfg().setdefault("soc_access", {})
    access.setdefault("sync_interval_minutes", 30)
    access.setdefault("initial_delay_seconds", 60)
    return access

def soc_onboarding_cfg() -> Dict[str, Any]:
    return cfg().setdefault("soc_onboarding", {})

def _soc_sections_def() -> Dict[str, Any]:
    access = soc_access_cfg()
    sections = access.get("sections")
    if not isinstance(sections, dict):
        access["sections"] = {}
    return access.setdefault("sections", {})

def _soc_users_cfg() -> Dict[str, Any]:
    access = soc_access_cfg()
    users = access.get("users")
    if not isinstance(users, dict):
        access["users"] = {}
    return access.setdefault("users", {})

def _soc_user_cfg(user_id: int) -> Dict[str, Any]:
    users = _soc_users_cfg()
    entry = users.get(str(user_id))
    if not isinstance(entry, dict):
        entry = {}
        users[str(user_id)] = entry
    return entry

def _soc_section_role_name(section_key: str, fallback: str) -> str:
    sections = _soc_sections_def()
    ent = sections.get(section_key)
    if isinstance(ent, dict) and ent.get("role"):
        return str(ent.get("role"))
    return fallback

def _soc_section_default(section_key: str, fallback: bool) -> bool:
    sections = _soc_sections_def()
    ent = sections.get(section_key)
    if isinstance(ent, dict) and "default" in ent:
        return bool(ent.get("default"))
    return fallback

def _soc_role(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    return discord.utils.get(guild.roles, name=name)

async def ensure_soc_section_roles(guild: discord.Guild) -> None:
    if guild.id != ADMIN_GUILD_ID:
        return
    role_names = [
        _soc_section_role_name("docs", "SEC:DOCS"),
        _soc_section_role_name("guest_area", "SEC:GUEST-AREA"),
        _soc_section_role_name("guest_write", "SEC:GUEST-WRITE"),
        _soc_section_role_name("mirrors", "SEC:MIRRORS"),
        _soc_section_role_name("server_info", "SEC:SERVER-INFO"),
    ]
    existing = {r.name for r in guild.roles}
    for name in role_names:
        if name in existing:
            continue
        try:
            await guild.create_role(name=name, reason="Mandy SOC access role bootstrap")
            await setup_pause()
        except Exception:
            continue

def _soc_mirror_role_name(guild_id: int) -> str:
    return f"SOC:MIRROR:{guild_id}"

def _soc_info_role_name(guild_id: int) -> str:
    return f"SOC:INFO:{guild_id}"

async def _ensure_soc_server_roles(admin_guild: discord.Guild, source_guild_id: int) -> Tuple[Optional[discord.Role], Optional[discord.Role]]:
    if admin_guild.id != ADMIN_GUILD_ID:
        return None, None
    mirror_name = _soc_mirror_role_name(source_guild_id)
    info_name = _soc_info_role_name(source_guild_id)
    mirror_role = _soc_role(admin_guild, mirror_name)
    info_role = _soc_role(admin_guild, info_name)
    if not mirror_role:
        try:
            mirror_role = await admin_guild.create_role(name=mirror_name, reason="Mandy SOC mirror access role")
            await setup_pause()
        except Exception:
            mirror_role = None
    if not info_role:
        try:
            info_role = await admin_guild.create_role(name=info_name, reason="Mandy SOC server-info access role")
            await setup_pause()
        except Exception:
            info_role = None
    return mirror_role, info_role

async def soc_apply_core_permissions(guild: discord.Guild) -> None:
    if guild.id != ADMIN_GUILD_ID:
        return
    await ensure_soc_section_roles(guild)

    docs_role = _soc_role(guild, _soc_section_role_name("docs", "SEC:DOCS"))
    guest_area_role = _soc_role(guild, _soc_section_role_name("guest_area", "SEC:GUEST-AREA"))
    guest_write_role = _soc_role(guild, _soc_section_role_name("guest_write", "SEC:GUEST-WRITE"))
    access_viewer = _soc_role(guild, "ACCESS:Viewer")
    access_engineer = _soc_role(guild, "ACCESS:Engineer")
    access_admin = _soc_role(guild, "ACCESS:Admin")
    for role_name in ("ACCESS:Viewer", "ACCESS:Engineer", "ACCESS:Admin"):
        if not _soc_role(guild, role_name):
            try:
                created = await guild.create_role(name=role_name, reason="SOC access role bootstrap")
                await setup_pause()
                if role_name == "ACCESS:Viewer":
                    access_viewer = created
                elif role_name == "ACCESS:Engineer":
                    access_engineer = created
                else:
                    access_admin = created
            except Exception:
                pass

    # Hide these channels from @everyone and expose via section roles.
    docs_channels = [
        "rules",
        "announcements",
        "guest-briefing",
        "manual-for-living",
    ]
    guest_read_channels = [
        "guest-chat",
        "guest-feedback",
    ]

    for name in docs_channels:
        ch = discord.utils.get(guild.text_channels, name=name)
        if not ch:
            continue
        try:
            await ch.set_permissions(guild.default_role, view_channel=False)
            if docs_role:
                await ch.set_permissions(docs_role, view_channel=True, send_messages=False, read_message_history=True)
            if access_viewer:
                await ch.set_permissions(access_viewer, view_channel=True, send_messages=False, read_message_history=True)
        except Exception:
            pass

    for name in guest_read_channels:
        ch = discord.utils.get(guild.text_channels, name=name)
        if not ch:
            continue
        try:
            await ch.set_permissions(guild.default_role, view_channel=False)
            if guest_area_role:
                await ch.set_permissions(guest_area_role, view_channel=True, send_messages=False, read_message_history=True)
            if guest_write_role:
                await ch.set_permissions(guest_write_role, view_channel=True, send_messages=True, read_message_history=True)
            if access_viewer:
                await ch.set_permissions(access_viewer, view_channel=True, send_messages=True, read_message_history=True)
        except Exception:
            pass

    # Quarantine channel remains Quarantine-only.
    try:
        gate_cfg = cfg().get("gate_layout", {})
        quarantine_name = str(gate_cfg.get("quarantine") or "quarantine")
        qch = discord.utils.get(guild.text_channels, name=quarantine_name)
        quarantine = get_role(guild, QUARANTINE_ROLE_NAME)
        if qch and quarantine:
            await qch.set_permissions(guild.default_role, view_channel=False)
            await qch.set_permissions(quarantine, view_channel=True, send_messages=True, read_message_history=True)
    except Exception:
        pass

    # ENGINEERING category visibility
    try:
        eng_cat = discord.utils.get(guild.categories, name="ENGINEERING")
        if eng_cat:
            await eng_cat.set_permissions(guild.default_role, view_channel=False)
            allow_roles = [
                access_engineer,
                access_admin,
                get_role(guild, GOD_ROLE_NAME),
                get_role(guild, ADMIN_ROLE_NAME),
            ]
            for role in allow_roles:
                if role:
                    await eng_cat.set_permissions(role, view_channel=True, read_messages=True, send_messages=True, read_message_history=True)
    except Exception:
        pass

    # GOD CORE category visibility
    try:
        god_cat = discord.utils.get(guild.categories, name="GOD CORE")
        if god_cat:
            await god_cat.set_permissions(guild.default_role, view_channel=False)
            allow_roles = [
                access_admin,
                get_role(guild, GOD_ROLE_NAME),
                get_role(guild, ADMIN_ROLE_NAME),
            ]
            for role in allow_roles:
                if role:
                    await god_cat.set_permissions(role, view_channel=True, read_messages=True, send_messages=True, read_message_history=True)
    except Exception:
        pass

async def soc_apply_admin_server_permissions() -> None:
    admin = bot.get_guild(ADMIN_GUILD_ID)
    if not admin:
        return
    await ensure_soc_section_roles(admin)
    state_map = cfg().get("admin_servers", {}) or {}
    if not isinstance(state_map, dict):
        return
    for gid_s, entry in state_map.items():
        if not str(gid_s).isdigit() or not isinstance(entry, dict):
            continue
        gid = int(gid_s)
        mirror_feed = admin.get_channel(int(entry.get("mirror_feed", 0) or 0))
        info_ch = admin.get_channel(int(entry.get("server_info", 0) or 0))
        if not isinstance(mirror_feed, discord.TextChannel) and not isinstance(info_ch, discord.TextChannel):
            continue
        mirror_role, info_role = await _ensure_soc_server_roles(admin, gid)
        try:
            if isinstance(mirror_feed, discord.TextChannel):
                await mirror_feed.set_permissions(admin.default_role, view_channel=False)
                # Access role computed by sync: membership + section.
                if mirror_role:
                    await mirror_feed.set_permissions(mirror_role, view_channel=True, send_messages=False, read_message_history=True)
        except Exception:
            pass
        try:
            if isinstance(info_ch, discord.TextChannel):
                await info_ch.set_permissions(admin.default_role, view_channel=False)
                if info_role:
                    await info_ch.set_permissions(info_role, view_channel=True, send_messages=False, read_message_history=True)
        except Exception:
            pass

def _soc_user_section_overrides(user_id: int) -> Dict[str, Any]:
    ent = _soc_user_cfg(user_id)
    sections = ent.get("sections")
    if not isinstance(sections, dict):
        sections = {}
        ent["sections"] = sections
    return sections

def _soc_user_guild_filters(user_id: int) -> Tuple[Optional[Set[int]], Set[int]]:
    ent = _soc_user_cfg(user_id)
    allowed_raw = ent.get("allowed_guilds", None)
    denied_raw = ent.get("denied_guilds", []) or []
    allowed: Optional[Set[int]] = None
    if isinstance(allowed_raw, list) and allowed_raw:
        allowed = {int(x) for x in allowed_raw if str(x).isdigit()}
    denied = {int(x) for x in denied_raw if str(x).isdigit()}
    return allowed, denied

def _soc_section_enabled_for_member(member: discord.Member, section_key: str) -> bool:
    # quarantine => nothing
    if any(r.name == QUARANTINE_ROLE_NAME for r in member.roles):
        return False
    overrides = _soc_user_section_overrides(member.id)
    if section_key in overrides:
        return bool(overrides.get(section_key))
    return _soc_section_default(section_key, False)

async def soc_sync_member_access(member: discord.Member) -> None:
    if member.guild.id != ADMIN_GUILD_ID:
        return
    await ensure_roles(member.guild)
    await ensure_soc_section_roles(member.guild)

    guest = get_role(member.guild, GUEST_ROLE_NAME)
    quarantine = get_role(member.guild, QUARANTINE_ROLE_NAME)

    # baseline identity roles
    is_quarantined = bool(quarantine and quarantine in member.roles)
    if not is_quarantined and guest and guest not in member.roles:
        try:
            await member.add_roles(guest, reason="SOC baseline Guest")
        except Exception:
            pass

    # section roles
    section_map = {
        "docs": _soc_section_role_name("docs", "SEC:DOCS"),
        "guest_area": _soc_section_role_name("guest_area", "SEC:GUEST-AREA"),
        "guest_write": _soc_section_role_name("guest_write", "SEC:GUEST-WRITE"),
        "mirrors": _soc_section_role_name("mirrors", "SEC:MIRRORS"),
        "server_info": _soc_section_role_name("server_info", "SEC:SERVER-INFO"),
    }
    desired_section_roles: Set[discord.Role] = set()
    for key, role_name in section_map.items():
        role = _soc_role(member.guild, role_name)
        if not role:
            continue
        if _soc_section_enabled_for_member(member, key):
            desired_section_roles.add(role)

    current_section_roles = {r for r in member.roles if r.name in set(section_map.values())}

    # computed per-guild roles for admin mirror/info visibility
    allowed, denied = _soc_user_guild_filters(member.id)
    desired_server_roles: Set[discord.Role] = set()
    current_server_roles = {r for r in member.roles if r.name.startswith("SOC:MIRROR:") or r.name.startswith("SOC:INFO:")}

    if not is_quarantined:
        want_mirrors = _soc_section_enabled_for_member(member, "mirrors")
        want_info = _soc_section_enabled_for_member(member, "server_info")
        for g in list(bot.guilds):
            if not g or g.id == ADMIN_GUILD_ID:
                continue
            if denied and g.id in denied:
                continue
            if allowed is not None and g.id not in allowed:
                continue
            # membership check (cached)
            if not g.get_member(member.id):
                continue
            mirror_role, info_role = await _ensure_soc_server_roles(member.guild, g.id)
            if want_mirrors and mirror_role:
                desired_server_roles.add(mirror_role)
            if want_info and info_role:
                desired_server_roles.add(info_role)

    to_add = list((desired_section_roles | desired_server_roles) - (current_section_roles | current_server_roles))
    to_remove = list((current_section_roles | current_server_roles) - (desired_section_roles | desired_server_roles))

    if to_add:
        try:
            await member.add_roles(*to_add, reason="SOC access sync")
        except Exception:
            pass
    if to_remove:
        try:
            await member.remove_roles(*to_remove, reason="SOC access sync")
        except Exception:
            pass

async def soc_send_onboarding_dm(actor_id: int, target_user_id: int, guild_scope: Optional[Set[int]] = None) -> str:
    admin = bot.get_guild(ADMIN_GUILD_ID)
    if not admin or not bot.user:
        return "Admin hub or bot user not available."

    # Record onboarding intent + optional server scope.
    ucfg = _soc_user_cfg(target_user_id)
    if guild_scope and len(guild_scope) > 0:
        ucfg["allowed_guilds"] = sorted({int(x) for x in guild_scope if int(x) > 0})

    onb = soc_onboarding_cfg()
    onb_users = onb.setdefault("users", {})
    onb_tokens = onb.setdefault("tokens", {})

    ttl_minutes = int(onb.get("token_ttl_minutes", 30) or 30)
    ttl_minutes = max(5, min(24 * 60, ttl_minutes))
    token = secrets.token_urlsafe(8)
    expires_at = now_ts() + ttl_minutes * 60
    onb_tokens[str(target_user_id)] = {
        "token": token,
        "expires_at": int(expires_at),
        "created_at": now_ts(),
        "created_by": int(actor_id),
    }
    onb_users.setdefault(str(target_user_id), {})
    onb_users[str(target_user_id)]["last_sent_at"] = now_ts()
    onb_users[str(target_user_id)]["last_sent_by"] = int(actor_id)

    # Admin hub invite
    admin_invite_url = str(onb.get("admin_invite_url") or "").strip()
    if not admin_invite_url:
        ch = discord.utils.get(admin.text_channels, name="guest-briefing") or discord.utils.get(admin.text_channels, name="guest-chat")
        if ch:
            try:
                inv = await ch.create_invite(
                    max_age=ttl_minutes * 60,
                    max_uses=1,
                    unique=True,
                    reason=f"SOC onboarding for {target_user_id}",
                )
                admin_invite_url = str(getattr(inv, "url", None) or str(inv))
            except Exception:
                admin_invite_url = ""

    # Bot invite
    perms_int = int(onb.get("bot_invite_permissions", 8) or 8)
    perms_int = max(0, perms_int)
    try:
        bot_invite = discord.utils.oauth_url(
            bot.user.id,
            permissions=discord.Permissions(perms_int),
            scopes=("bot", "applications.commands"),
        )
    except Exception:
        bot_invite = ""

    await STORE.mark_dirty()

    try:
        user = bot.get_user(target_user_id) or await bot.fetch_user(target_user_id)
    except Exception:
        user = None
    if not user:
        return "Target user not found."

    scope_text = ""
    if guild_scope:
        names: List[str] = []
        for gid in sorted(guild_scope)[:10]:
            g = bot.get_guild(int(gid))
            if g:
                names.append(g.name)
        scope_text = f"Server scope: {', '.join(names) if names else 'custom'}"

    lines = [
        "**Mandy SOC Onboarding**",
        "",
        "1) Invite the bot to your server:",
        bot_invite or "(bot invite not available; ask an admin)",
        "",
        "2) Join the admin hub:",
        admin_invite_url or "(admin hub invite not configured; ask an admin)",
        "",
        "3) Gate access:",
        f"Paste this one-time token in your `gate-...` channel: `{token}`",
        f"Expires: {fmt_ts(expires_at)}",
    ]
    if scope_text:
        lines.extend(["", scope_text])

    try:
        await user.send("\n".join(lines))
    except Exception:
        return "DM failed (user may have DMs closed)."

    await audit(actor_id, "SOC onboard DM sent", {"user_id": target_user_id, "scope": sorted(list(guild_scope or []))})
    return "Onboarding DM sent."

def _member_can_manage(member: Optional[discord.Member]) -> bool:
    if not member:
        return False
    perms = getattr(member, "guild_permissions", None)
    if not perms:
        return False
    return bool(perms.administrator or perms.manage_guild or perms.manage_roles)

async def detect_bot_inviter_user_id(guild: discord.Guild) -> Optional[int]:
    if not bot.user or not guild or not guild.me:
        return None
    perms = getattr(guild.me, "guild_permissions", None)
    if not perms or not perms.view_audit_log:
        return None
    try:
        async for entry in guild.audit_logs(action=discord.AuditLogAction.bot_add, limit=6):
            target = getattr(entry, "target", None)
            if target and getattr(target, "id", None) == bot.user.id:
                user = getattr(entry, "user", None)
                if user:
                    return user.id
    except Exception:
        return None
    return None

async def maybe_complete_owner_onboarding(guild: discord.Guild) -> None:
    pending = owner_onboarding_pending()
    if not pending:
        return
    inviter_id = await detect_bot_inviter_user_id(guild)
    matched_user_id: Optional[int] = None
    if inviter_id and str(inviter_id) in pending:
        matched_user_id = inviter_id
    if not matched_user_id:
        for uid_s in list(pending.keys()):
            if not str(uid_s).isdigit():
                continue
            uid = int(uid_s)
            member = guild.get_member(uid)
            if _member_can_manage(member):
                matched_user_id = uid
                break
    if not matched_user_id and inviter_id and str(inviter_id) in pending:
        matched_user_id = inviter_id
    if not matched_user_id and len(pending) == 1:
        uid_s = next(iter(pending.keys()))
        if str(uid_s).isdigit():
            member = guild.get_member(int(uid_s))
            if member:
                matched_user_id = int(uid_s)
    if not matched_user_id:
        return
    record = pending.pop(str(matched_user_id), None)
    if not record:
        return
    features = normalize_owner_onboarding_features(record.get("features") or [])
    set_satellite_features(guild.id, features)
    history = owner_onboarding_history()
    record["matched_guild_id"] = guild.id
    record["completed_at"] = now_ts()
    history[str(matched_user_id)] = record
    ucfg = _soc_user_cfg(matched_user_id)
    allowed_raw = ucfg.get("allowed_guilds", [])
    if not isinstance(allowed_raw, list):
        allowed_raw = []
    allowed = {int(x) for x in allowed_raw if str(x).isdigit()}
    allowed.add(guild.id)
    ucfg["allowed_guilds"] = sorted(allowed)
    await STORE.mark_dirty()
    admin = bot.get_guild(ADMIN_GUILD_ID)
    if admin:
        member_admin = admin.get_member(matched_user_id)
        if member_admin:
            try:
                await soc_sync_member_access(member_admin)
            except Exception:
                pass
    try:
        await audit(matched_user_id, "Owner onboarding completed", {"guild_id": guild.id, "features": features})
    except Exception:
        pass
    try:
        user = bot.get_user(matched_user_id) or await bot.fetch_user(matched_user_id)
        if user:
            summary = [
                "Owner onboarding complete.",
                f"Server: {guild.name} ({guild.id})",
                "Features: " + ", ".join(owner_onboarding_feature_labels(features)),
                "Mirror and satellite roles are active.",
            ]
            await user.send("\n".join(summary))
    except Exception:
        pass

async def apply_guest_permissions(guild: discord.Guild):
    # Backwards compatible entrypoint: "guest permissions" now means applying SOC section perms.
    if guild.id != ADMIN_GUILD_ID:
        return
    await soc_apply_core_permissions(guild)
    await soc_apply_admin_server_permissions()

async def apply_quarantine_permissions(guild: discord.Guild):
    quarantine = get_role(guild, QUARANTINE_ROLE_NAME)
    if not quarantine:
        return
    gate_cfg = cfg().get("gate_layout", {})
    quarantine_name = str(gate_cfg.get("quarantine") or "quarantine")
    ch = discord.utils.get(guild.text_channels, name=quarantine_name)
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
    # Gate pass means: remove quarantine posture and ensure baseline Guest.
    quarantine = get_role(member.guild, QUARANTINE_ROLE_NAME)
    if quarantine and quarantine in member.roles:
        try:
            await member.remove_roles(quarantine, reason="Gate approved")
        except Exception:
            pass
    guest = get_role(member.guild, GUEST_ROLE_NAME)
    if guest and guest not in member.roles:
        try:
            await member.add_roles(guest, reason="Gate approved (baseline Guest)")
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
    try:
        await soc_sync_member_access(member)
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
    guest = get_role(member.guild, GUEST_ROLE_NAME)
    if guest and guest in member.roles:
        try:
            await member.remove_roles(guest, reason="Gate quarantine")
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
    gate_cfg = cfg().get("gate_layout", {})
    quarantine_name = str(gate_cfg.get("quarantine") or "quarantine")
    qch = discord.utils.get(member.guild.text_channels, name=quarantine_name)
    if qch:
        try:
            await qch.send(f"Quarantine: <@{member.id}> {reason}".strip())
        except Exception:
            pass
    try:
        await soc_sync_member_access(member)
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
    gate_cfg = cfg().get("gate_layout", {})
    guest_category_name = str(gate_cfg.get("category") or "GUEST ACCESS")
    try:
        cat = await ensure_category(member.guild, guest_category_name)
    except Exception:
        cat = discord.utils.get(member.guild.categories, name=guest_category_name)
        if not cat:
            cat = await member.guild.create_category(guest_category_name)

    overwrites = {
        member.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        member.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    for role_name in (GOD_ROLE_NAME, ADMIN_ROLE_NAME, STAFF_ROLE_NAME):
        role = get_role(member.guild, role_name)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    gate_name = f"gate-{member.name}"
    async with _setup_guild_lock(member.guild.id):
        ch = discord.utils.get(cat.text_channels, name=gate_name) if cat else None
        if not ch:
            try:
                fetched = await member.guild.fetch_channels()
                fetched_matches = [
                    c for c in fetched
                    if isinstance(c, discord.TextChannel)
                    and c.name == gate_name
                    and (not cat or c.category_id == cat.id)
                ]
                if fetched_matches:
                    ch = sorted(fetched_matches, key=lambda c: c.id)[0]
            except Exception:
                pass
        if not ch:
            ch = await member.guild.create_text_channel(gate_name, category=cat, overwrites=overwrites)
        else:
            try:
                await ch.edit(overwrites=overwrites, category=cat)
            except Exception:
                pass

    cfg().setdefault("gate", {})[str(member.id)] = {"channel": ch.id, "tries": 0}
    await STORE.mark_dirty()

    await ch.send("Enter the server password. (Attempts auto-deleted)")
    try:
        await soc_sync_member_access(member)
    except Exception:
        pass

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

    content = (message.content or "").strip()
    # Per-user SOC onboarding token (preferred)
    try:
        soc_onb = soc_onboarding_cfg()
        tokens = soc_onb.get("tokens")
        if isinstance(tokens, dict):
            ent = tokens.get(uid)
            if isinstance(ent, dict):
                token = str(ent.get("token") or "").strip()
                expires = int(ent.get("expires_at", 0) or 0)
                if token and content == token and (expires <= 0 or now_ts() <= expires):
                    tokens.pop(uid, None)
                    soc_onb.setdefault("users", {}).setdefault(uid, {})["last_gate_token_used_at"] = now_ts()
                    await STORE.mark_dirty()
                    await gate_approve_user(message.author)
                    try:
                        await message.author.send("Access granted (token).")
                    except Exception:
                        pass
                    await audit(message.author.id, "Gate PASS (token)", {"user_id": message.author.id})
                    return True
    except Exception:
        pass

    # Static password (fallback)
    if SERVER_PASSWORD and content == SERVER_PASSWORD:
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
    async with _setup_guild_lock(guild.id):
        matches = [c for c in guild.categories if c.name == name]
        cat = matches[0] if matches else None
        if cat:
            return cat
        rename_map = {
            "WELCOME": ["Welcome & Information"],
            "OPERATIONS": ["Bot Control & Monitoring"],
            "GUEST ACCESS": ["Guest Access"],
            "ENGINEERING": ["Engineering Core", "DM Bridges", "Research & Development"],
            "GOD CORE": ["Admin Backrooms"],
        }
        for old in rename_map.get(name, []):
            old_cat = discord.utils.get(guild.categories, name=old)
            if old_cat:
                try:
                    await old_cat.edit(name=name)
                    await setup_pause()
                    return old_cat
                except Exception as exc:
                    await _setup_pause_on_rate_limit(exc)
                    break
        try:
            fetched = await guild.fetch_channels()
            fetched_matches = [c for c in fetched if isinstance(c, discord.CategoryChannel) and c.name == name]
            if fetched_matches:
                return fetched_matches[0]
        except Exception:
            pass
        try:
            cat = await guild.create_category(name)
            await setup_pause()
            return cat
        except Exception as exc:
            await _setup_pause_on_rate_limit(exc)
            raise

async def ensure_text_channel(
    guild: discord.Guild,
    name: str,
    category: discord.CategoryChannel,
    topic: Optional[str] = None,
) -> discord.TextChannel:
    async with _setup_guild_lock(guild.id):
        rename_map = {
            "rules": ["rules-and-guidelines"],
            "console": ["bot-status"],
            "requests": ["command-requests"],
            "reports": ["error-reporting"],
            "system-log": ["system-logs"],
            "audit-log": ["audit-logs"],
            "debug-log": ["debug-logs"],
            "mirror-log": ["mirror-logs"],
            "data-lab": ["core-chat", "algorithm-discussion", "data-analysis"],
        }
        ch = None
        if category:
            ch = discord.utils.get(category.text_channels, name=name)
        if not ch:
            ch = discord.utils.get(guild.text_channels, name=name)
        if not ch and name in rename_map:
            for old in rename_map[name]:
                old_ch = discord.utils.get(guild.text_channels, name=old)
                if old_ch:
                    try:
                        await old_ch.edit(name=name, category=category)
                        await setup_pause()
                        ch = old_ch
                        break
                    except Exception as exc:
                        await _setup_pause_on_rate_limit(exc)
                        break
        if not ch:
            try:
                fetched = await guild.fetch_channels()
                fetched_ch = [
                    c for c in fetched
                    if isinstance(c, discord.TextChannel) and c.name == name
                ]
                if fetched_ch:
                    ch = fetched_ch[0]
            except Exception:
                pass
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
            except Exception as exc:
                await _setup_pause_on_rate_limit(exc)
            return ch
        try:
            ch = await guild.create_text_channel(name, category=category, topic=topic or None)
            await setup_pause()
            return ch
        except Exception as exc:
            await _setup_pause_on_rate_limit(exc)
            raise

async def ensure_pinned(channel: discord.TextChannel, content: str):
    lines = content.splitlines()
    sig = lines[0].strip() if lines else ""
    has_sig = sig.startswith("<!--PIN:") and sig.endswith("-->")
    try:
        pins = [p async for p in channel.pins()]
        for p in pins:
            if p.author.id != bot.user.id:
                continue
            p_lines = (p.content or "").splitlines()
            p_sig = p_lines[0].strip() if p_lines else ""
            if has_sig and p_sig == sig:
                if p.content != content:
                    await p.edit(content=content)
                    await setup_pause()
                return
            if not has_sig and p.content.startswith(sig[:60]):
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
    user_channel = channels_cfg.get("user", "requests")
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
    user_channel = channels_cfg.get("user", "requests")
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
    ai_layout = cfg().get("ai_layout", {})
    ai_logs = ai_layout.get("log_channels") if isinstance(ai_layout, dict) else {}
    if ai_layout.get("enabled") and isinstance(ai_logs, dict) and ai_logs:
        for key, name in ai_logs.items():
            if not name:
                continue
            ch = find_text_by_name(guild, name)
            if ch:
                logs[key] = ch.id
        await STORE.mark_dirty()
        return

    system = find_text_by_name(guild, "system-log")
    audit_ch = find_text_by_name(guild, "audit-log") or system
    debug_ch = find_text_by_name(guild, "debug-log") or system
    mirror_ch = find_text_by_name(guild, "mirror-log") or system
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
    if source_guild.id == ADMIN_GUILD_ID:
        state = cfg().setdefault("admin_servers", {})
        if str(source_guild.id) in state:
            state.pop(str(source_guild.id), None)
            await STORE.mark_dirty()
        return None, None
    async with _setup_guild_lock(admin.id):
        state = cfg().setdefault("admin_servers", {})
        entry = state.setdefault(str(source_guild.id), {})

        def _pick_oldest_category(categories: List[discord.CategoryChannel]) -> discord.CategoryChannel:
            return sorted(categories, key=lambda c: c.id)[0]

        def _pick_oldest_channel(channels: List[discord.TextChannel]) -> discord.TextChannel:
            return sorted(channels, key=lambda c: c.id)[0]

        cat = admin.get_channel(entry.get("category_id")) if entry.get("category_id") else None
        if not isinstance(cat, discord.CategoryChannel):
            desired_name = admin_category_name(source_guild)
            legacy_name = f"{source_guild.name} Admin"
            matches = [c for c in admin.categories if c.name == desired_name]
            legacy_matches = [c for c in admin.categories if c.name == legacy_name]
            if matches:
                cat = _pick_oldest_category(matches)
                if len(matches) > 1:
                    await debug(f"Duplicate satellite categories found for {desired_name}: {len(matches)}")
            elif legacy_matches:
                cat = _pick_oldest_category(legacy_matches)
                if len(legacy_matches) > 1:
                    await debug(f"Duplicate legacy satellite categories found for {legacy_name}: {len(legacy_matches)}")
            if cat and cat.name != desired_name:
                try:
                    await cat.edit(name=desired_name)
                    await setup_pause()
                except Exception:
                    pass
            if not cat:
                try:
                    fetched = await admin.fetch_channels()
                    fetched_matches = [
                        c for c in fetched
                        if isinstance(c, discord.CategoryChannel) and c.name == desired_name
                    ]
                    if fetched_matches:
                        cat = _pick_oldest_category(fetched_matches)
                except Exception:
                    pass
            if not cat:
                cat = await admin.create_category(desired_name)
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
            mf_matches = [c for c in cat.text_channels if c.name == "mirror-feed"]
            if mf_matches:
                mirror_feed = _pick_oldest_channel(mf_matches)
                if len(mf_matches) > 1:
                    await debug(f"Duplicate mirror-feed channels in {cat.name}: {len(mf_matches)}")
            else:
                try:
                    fetched = await admin.fetch_channels()
                    fetched_matches = [
                        c for c in fetched
                        if isinstance(c, discord.TextChannel) and c.category_id == cat.id and c.name == "mirror-feed"
                    ]
                    if fetched_matches:
                        mirror_feed = _pick_oldest_channel(fetched_matches)
                except Exception:
                    pass
            if not mirror_feed:
                mirror_feed = await admin.create_text_channel("mirror-feed", category=cat)
                await setup_pause()
            entry["mirror_feed"] = mirror_feed.id

        info_ch = admin.get_channel(entry.get("server_info")) if entry.get("server_info") else None
        if not isinstance(info_ch, discord.TextChannel):
            legacy_id = entry.get("server_status")
            info_ch = admin.get_channel(legacy_id) if legacy_id else None
        if not isinstance(info_ch, discord.TextChannel):
            info_matches = [c for c in cat.text_channels if c.name == "server-info"]
            if info_matches:
                info_ch = _pick_oldest_channel(info_matches)
                if len(info_matches) > 1:
                    await debug(f"Duplicate server-info channels in {cat.name}: {len(info_matches)}")
            else:
                legacy_matches = [c for c in cat.text_channels if c.name == "server-status"]
                if legacy_matches:
                    info_ch = _pick_oldest_channel(legacy_matches)
                    if len(legacy_matches) > 1:
                        await debug(f"Duplicate server-status channels in {cat.name}: {len(legacy_matches)}")
                    try:
                        await info_ch.edit(name="server-info")
                    except Exception:
                        pass
                else:
                    try:
                        fetched = await admin.fetch_channels()
                        fetched_matches = [
                            c for c in fetched
                            if isinstance(c, discord.TextChannel) and c.category_id == cat.id and c.name == "server-info"
                        ]
                        if fetched_matches:
                            info_ch = _pick_oldest_channel(fetched_matches)
                    except Exception:
                        pass
        if not isinstance(info_ch, discord.TextChannel):
            info_ch = await admin.create_text_channel("server-info", category=cat)
            await setup_pause()

        if info_ch:
            entry["server_info"] = info_ch.id

        await STORE.mark_dirty()
        try:
            await soc_apply_admin_server_permissions()
        except Exception:
            pass
        return mirror_feed, info_ch

def find_category_by_name(guild: discord.Guild, name: str) -> Optional[discord.CategoryChannel]:
    return discord.utils.get(guild.categories, name=name)

def find_text_by_name(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    return discord.utils.get(guild.text_channels, name=name)

def check_layout_missing(guild: discord.Guild) -> List[str]:
    layout = cfg().get("layout", {}).get("categories", {})
    missing: List[str] = []
    for cat_name, chans in layout.items():
        cat = find_category_by_name(guild, cat_name)
        if not cat:
            missing.append(f"category:{cat_name}")
        for ch_name in chans:
            ch = find_text_by_name(guild, ch_name)
            if not ch:
                missing.append(f"channel:{cat_name}/{ch_name}")
    return missing


def _dedup_category_groups(guild: discord.Guild) -> Dict[str, List[discord.CategoryChannel]]:
    groups: Dict[str, List[discord.CategoryChannel]] = {}
    for cat in guild.categories:
        groups.setdefault(cat.name, []).append(cat)
    return {name: sorted(cats, key=lambda c: c.id) for name, cats in groups.items() if len(cats) > 1}


def _dedup_channel_groups(guild: discord.Guild) -> Dict[Tuple[int, str], List[discord.TextChannel]]:
    groups: Dict[Tuple[int, str], List[discord.TextChannel]] = {}
    for ch in guild.text_channels:
        key = (ch.category_id or 0, ch.name)
        groups.setdefault(key, []).append(ch)
    return {key: sorted(chs, key=lambda c: c.id) for key, chs in groups.items() if len(chs) > 1}


def _dedup_channel_name(base: str, ch_id: int) -> str:
    suffix = f"-dup-{str(ch_id)[-4:]}"
    trimmed = base
    max_len = 100
    if len(trimmed) + len(suffix) > max_len:
        trimmed = trimmed[: max(1, max_len - len(suffix))]
    return trimmed + suffix


def _dedup_category_name(base: str, cat_id: int) -> str:
    suffix = f" [dup-{str(cat_id)[-4:]}]"
    trimmed = base
    max_len = 100
    if len(trimmed) + len(suffix) > max_len:
        trimmed = trimmed[: max(1, max_len - len(suffix))]
    return trimmed + suffix

async def setup_audit_report() -> List[str]:
    lines: List[str] = []
    admin = bot.get_guild(ADMIN_GUILD_ID)
    if not admin:
        return ["Setup Audit Report", "Admin guild missing."]

    lines.append("Setup Audit Report")
    lines.append(f"Admin guild: {admin.name} ({admin.id})")
    lines.append(f"DB: {'MySQL' if state.POOL else 'JSON-only'}")

    roles = [GUEST_ROLE_NAME, QUARANTINE_ROLE_NAME, STAFF_ROLE_NAME, ADMIN_ROLE_NAME, GOD_ROLE_NAME]
    missing_roles = [r for r in roles if not get_role(admin, r)]
    if missing_roles:
        lines.append("Roles: MISSING -> " + ", ".join(missing_roles))
    else:
        lines.append("Roles: VERIFIED")

    missing_layout = check_layout_missing(admin)
    if missing_layout:
        lines.append(f"Layout: MISSING {len(missing_layout)}")
    else:
        lines.append("Layout: VERIFIED")

    menus = cfg().get("menu_messages", {})
    if menus.get("user_menu") and menus.get("god_menu"):
        lines.append("Menus: VERIFIED")
    else:
        lines.append("Menus: MISSING user_menu or god_menu")

    mirrors = list(mirror_rules_dict().values())
    enabled = len([r for r in mirrors if r.get("enabled", True)])
    disabled = len(mirrors) - enabled
    lines.append(f"Mirrors: total={len(mirrors)} enabled={enabled} disabled={disabled}")

    dm_bridges = await dm_bridge_list_active()
    lines.append(f"DM bridges active: {len(dm_bridges)}")

    return lines

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
        "WELCOME": ["rules", "announcements", "guest-briefing", "manual-for-living"],
        "OPERATIONS": ["console", "requests", "reports"],
        "SATELLITES": [],
        "GUEST ACCESS": ["guest-chat", "guest-feedback", "quarantine"],
        "ENGINEERING": ["system-log", "audit-log", "debug-log", "mirror-log", "data-lab", "dm-bridges"],
        "GOD CORE": ["admin-chat", "server-management", "layout-control", "blueprint-export", "incident-room"],
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

def _ai_layout_enabled() -> bool:
    return bool(cfg().get("ai_layout", {}).get("enabled"))

def _sanitize_channel_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\\- ]+", "", str(name or "").lower())
    cleaned = cleaned.replace(" ", "-")
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    if not cleaned:
        cleaned = "channel"
    return cleaned[:90]

def _normalize_ai_layout(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    categories = payload.get("categories")
    if not isinstance(categories, list) or not categories:
        return None
    layout: Dict[str, List[str]] = {}
    used: Set[str] = set()
    purpose_map: Dict[str, str] = {}
    for cat in categories:
        cat_name = str(cat.get("name") or "").strip()
        if not cat_name:
            continue
        channels = cat.get("channels") or []
        if not isinstance(channels, list):
            continue
        cleaned_channels: List[str] = []
        for ch in channels:
            purpose = ""
            if isinstance(ch, str):
                ch_name_raw = ch
            elif isinstance(ch, dict):
                ch_name_raw = ch.get("name")
                purpose = str(ch.get("purpose") or "").strip().lower()
            else:
                continue
            ch_name = _sanitize_channel_name(str(ch_name_raw or ""))
            if ch_name in used:
                continue
            used.add(ch_name)
            cleaned_channels.append(ch_name)
            if purpose:
                purpose_map[purpose] = ch_name
        if cleaned_channels:
            layout[cat_name] = cleaned_channels
    if not layout:
        return None

    log_map = payload.get("log_channels") if isinstance(payload.get("log_channels"), dict) else {}
    cmd_map = payload.get("command_channels") if isinstance(payload.get("command_channels"), dict) else {}
    gate_map = payload.get("gate") if isinstance(payload.get("gate"), dict) else {}
    topics = payload.get("topics") if isinstance(payload.get("topics"), dict) else {}
    pins = payload.get("pins") if isinstance(payload.get("pins"), dict) else {}

    def purpose_fallback(key: str, *aliases: str) -> Optional[str]:
        if key in purpose_map:
            return purpose_map[key]
        for alias in aliases:
            if alias in purpose_map:
                return purpose_map[alias]
        return None

    required_cmd = {
        "user": ["command_user", "cmd_user", "menu_user"],
        "god": ["command_god", "cmd_god", "menu_god", "admin_command"],
    }
    required_logs = {
        "system": ["log_system", "system_log", "logs_system"],
        "audit": ["log_audit", "audit_log"],
        "debug": ["log_debug", "debug_log"],
        "mirror": ["log_mirror", "mirror_log"],
        "ai": ["log_ai", "ai_log"],
        "voice": ["log_voice", "voice_log"],
    }
    required_gate = {
        "category": ["gate_category", "guest_category"],
        "guest_chat": ["guest_chat", "gate_chat"],
        "guest_briefing": ["guest_briefing", "guest_info", "guest_rules"],
        "quarantine": ["quarantine", "gate_quarantine"],
    }

    cmd: Dict[str, str] = {}
    for key, aliases in required_cmd.items():
        val = cmd_map.get(key) if cmd_map else None
        if not val:
            val = purpose_fallback(aliases[0], *aliases[1:])
        if val:
            cmd[key] = _sanitize_channel_name(val)

    logs: Dict[str, str] = {}
    for key, aliases in required_logs.items():
        val = log_map.get(key) if log_map else None
        if not val:
            val = purpose_fallback(aliases[0], *aliases[1:])
        if val:
            logs[key] = _sanitize_channel_name(val)

    gate: Dict[str, str] = {}
    for key, aliases in required_gate.items():
        val = gate_map.get(key) if gate_map else None
        if not val:
            val = purpose_fallback(aliases[0], *aliases[1:])
        if val:
            gate[key] = val if key == "category" else _sanitize_channel_name(val)

    required_channels = set(cmd.values()) | set(logs.values())
    for key in ("guest_chat", "guest_briefing", "quarantine"):
        if gate.get(key):
            required_channels.add(gate[key])
    required_channels.discard("")
    missing = [ch for ch in required_channels if ch not in used]
    if missing:
        fallback_cat = next(iter(layout.keys()))
        layout[fallback_cat].extend([ch for ch in missing if ch not in layout[fallback_cat]])
        used.update(missing)

    out_topics: Dict[str, str] = {}
    for name, text in topics.items():
        ch_name = _sanitize_channel_name(str(name))
        if ch_name in used and isinstance(text, str) and text.strip():
            out_topics[ch_name] = _sanitize_topic(text)
    out_pins: Dict[str, str] = {}
    for name, text in pins.items():
        ch_name = _sanitize_channel_name(str(name))
        if ch_name in used and isinstance(text, str) and text.strip():
            out_pins[ch_name] = _sanitize_pin(text)

    return {
        "layout": layout,
        "channels": sorted(used),
        "command_channels": cmd,
        "log_channels": logs,
        "gate": gate,
        "topics": out_topics,
        "pins": out_pins,
    }

async def _generate_setup_ai_layout(guild: discord.Guild) -> Optional[Dict[str, Any]]:
    client = _mandy_ai_client()
    if not client or not getattr(client, "available", False):
        return None
    ai = ai_cfg()
    model = str(ai.get("router_model") or ai.get("default_model") or "gemini-2.5-flash-lite")
    system_prompt = (
        "You are Mandy's sentient core. Output JSON only. "
        "Design a Discord server layout with AI-generated category/channel names. "
        "Names must be lowercase-with-hyphens. No emojis."
    )
    user_prompt = (
        "Return JSON with keys:\n"
        "- categories: list of {name, channels:[{name, purpose}]}.\n"
        "- command_channels: {user, god} channel names.\n"
        "- log_channels: {system, audit, debug, mirror, ai, voice} channel names.\n"
        "- gate: {category, guest_chat, guest_briefing, quarantine}.\n"
        "- topics: map channel name -> topic string (<=300 chars).\n"
        "- pins: map channel name -> pinned text (<=1800 chars).\n"
        "Constraints:\n"
        "- 5 to 9 categories.\n"
        "- 3 to 7 channels per category.\n"
        "- Every required command/log/gate channel must exist in categories.\n"
        "- All channel names unique.\n"
        "- Use professional, coherent theme.\n"
        "Output JSON only."
    )
    try:
        text = await client.generate(system_prompt, user_prompt, model=model, response_format="json", timeout=80.0)
        payload = json.loads(text or "{}")
    except Exception as exc:
        is_rate, retry_after = _is_ai_rate_limit(client, exc)
        if is_rate:
            raise SetupAiRateLimitError(retry_after=retry_after)
        return None
    return _normalize_ai_layout(payload)

async def _resolve_setup_layout() -> Dict[str, List[str]]:
    ai_layout = cfg().get("ai_layout", {})
    layout = ai_layout.get("layout") if isinstance(ai_layout, dict) else None
    if ai_layout.get("enabled") and isinstance(layout, dict) and layout:
        return layout
    return await ensure_layout_defaults()

async def _setup_snapshot_inventory(guild: discord.Guild, reason: str = "") -> None:
    if not guild:
        return
    snapshot = {
        "ts": now_ts(),
        "reason": reason,
        "guild_id": guild.id,
        "guild_name": guild.name,
        "roles": [
            {"id": r.id, "name": r.name, "position": r.position, "members": len(r.members)}
            for r in sorted(guild.roles, key=lambda r: r.position, reverse=True)
        ],
        "members": [
            {"id": m.id, "name": m.name, "bot": m.bot, "roles": [r.id for r in m.roles]}
            for m in guild.members
        ],
        "categories": [
            {"id": c.id, "name": c.name, "channels": [ch.name for ch in c.channels]}
            for c in guild.categories
        ],
        "channels": [ch.name for ch in guild.channels if isinstance(ch, discord.TextChannel)],
    }
    inv = cfg().setdefault("setup_inventory", [])
    if isinstance(inv, list):
        inv.append(snapshot)
        if len(inv) > 3:
            del inv[:-3]
    await STORE.mark_dirty()
    await setup_log(
        f"Setup inventory snapshot saved ({reason}): roles={len(snapshot['roles'])} members={len(snapshot['members'])} channels={len(snapshot['channels'])}"
    )

async def _purge_setup_dms(user_id: int) -> None:
    if not user_id:
        return
    try:
        await dm_ai_disable(user_id, reason="setup_purge", actor_id=SUPER_USER_ID)
    except Exception:
        pass
    try:
        info = await dm_bridge_get(user_id)
        ch_ids = set()
        if info and int(info.get("channel_id", 0)):
            ch_ids.add(int(info.get("channel_id", 0)))
        admin = bot.get_guild(ADMIN_GUILD_ID)
        if admin:
            archived = discord.utils.get(admin.text_channels, name=f"archived-dm-{user_id}")
            if archived:
                ch_ids.add(archived.id)
        for ch_id in ch_ids:
            ch = bot.get_channel(ch_id)
            if not ch:
                try:
                    ch = await bot.fetch_channel(ch_id)
                except Exception:
                    ch = None
            if ch:
                try:
                    await ch.delete()
                    await setup_pause()
                except Exception:
                    pass
        if state.POOL:
            try:
                await db_exec("DELETE FROM dm_bridges WHERE user_id=%s", (user_id,))
            except Exception:
                pass
        cfg().setdefault("dm_bridges", {}).pop(str(user_id), None)
        await STORE.mark_dirty()
    except Exception:
        pass
    try:
        user = bot.get_user(user_id)
        if not user:
            user = await bot.fetch_user(user_id)
        dm = user.dm_channel or await user.create_dm()
        async for msg in dm.history(limit=100):
            if msg.author and bot.user and msg.author.id == bot.user.id:
                try:
                    await msg.delete()
                    await setup_pause()
                except Exception:
                    pass
    except Exception:
        pass

async def _setup_lockdown_all_except(guild: discord.Guild, allow_user_id: int) -> None:
    if not guild or guild.id != ADMIN_GUILD_ID:
        return
    await ensure_roles(guild)
    await apply_guest_permissions(guild)
    await apply_quarantine_permissions(guild)
    gate_state = cfg().get("gate", {}) or {}
    for member in guild.members:
        if member.bot or member.id == allow_user_id:
            continue
        if str(member.id) in gate_state:
            continue
        try:
            await start_gate(member)
            await setup_pause()
        except Exception:
            continue

async def setup_fullsync(guild: discord.Guild):
    layout = await _resolve_setup_layout()
    pins = cfg().get("pinned_text", {})
    topics = cfg().get("channel_topics", {})

    await setup_log(f"Setup start: {guild.name} ({guild.id})")
    await setup_log("Phase 1/4: build categories and channels")
    for cat_name, chans in layout.items():
        cat = await ensure_category(guild, cat_name)
        await setup_pause()
        for ch_name in chans:
            await ensure_text_channel(guild, ch_name, cat, topic=topics.get(ch_name))
            await setup_pause()

    await setup_log("Phase 1/4: populate pinned text")
    for ch_name, content in pins.items():
        ch = find_text_by_name(guild, ch_name)
        if ch:
            await ensure_pinned(ch, content)

    await setup_log("Phase 1/4: command menus + log routing")
    await ensure_menu_panels(guild)
    await ensure_log_channels(guild)
    await setup_pause()

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
        await setup_pause()

    await ensure_log_channels(guild)
    await setup_pause()
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
        f"Context: roles={roles}, channels={channels}, categories={categories}, mysql={'on' if state.POOL else 'off'}."
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
    async with state.AUTO_SETUP_LOCK:
        await _auto_setup_all_guilds_nolock(
            do_backfill=do_backfill,
            force_backfill=force_backfill,
            include_admin=include_admin,
        )

async def run_full_setup(guild: discord.Guild, mode: str, actor_id: int = 0):
    if guild.id != ADMIN_GUILD_ID:
        return
    await setup_log(f"Full setup requested: {mode} by {actor_id}")
    prev_adaptive = state.SETUP_ADAPTIVE_ACTIVE
    prev_override = state.SETUP_DELAY_OVERRIDE
    paused_state: Optional[Dict[str, Any]] = None
    try:
        await _purge_setup_dms(SUPER_USER_ID)
        await _setup_snapshot_inventory(guild, reason=f"setup_start:{mode}")
        async with state.AUTO_SETUP_LOCK:
            state.SETUP_ADAPTIVE_ACTIVE = True
            if state.SETUP_DELAY_OVERRIDE is None:
                state.SETUP_DELAY_OVERRIDE = setup_delay_base()
            paused_state = await _pause_background_tasks_for_setup()
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
                        mode = "destructive"
                if mode == "destructive_ai":
                    ok = await setup_destructive_ai(guild, actor_id=actor_id)
                    if not ok:
                        await setup_log("AI destructive setup failed; falling back to standard destructive setup.")
                        mode = "destructive"
                        ok = await setup_destructive(guild)
                else:
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
            await _setup_lockdown_all_except(guild, SUPER_USER_ID)
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
    finally:
        if paused_state is not None:
            await _resume_background_tasks_after_setup(paused_state)
        state.SETUP_ADAPTIVE_ACTIVE = prev_adaptive
        state.SETUP_DELAY_OVERRIDE = prev_override

async def run_auto_setup_with_debrief(actor_id: int = 0):
    await setup_log(f"Auto setup requested by {actor_id}")
    prev_adaptive = state.SETUP_ADAPTIVE_ACTIVE
    prev_override = state.SETUP_DELAY_OVERRIDE
    paused_state: Optional[Dict[str, Any]] = None
    try:
        state.SETUP_ADAPTIVE_ACTIVE = True
        if state.SETUP_DELAY_OVERRIDE is None:
            state.SETUP_DELAY_OVERRIDE = setup_delay_base()
        paused_state = await _pause_background_tasks_for_setup()
        await auto_setup_all_guilds(do_backfill=True, force_backfill=True, include_admin=True)
        await send_setup_debrief(trigger="auto")
        await setup_log("Auto setup completed")
    except Exception as e:
        await setup_log(f"Auto setup failed: {e}")
    finally:
        if paused_state is not None:
            await _resume_background_tasks_after_setup(paused_state)
        state.SETUP_ADAPTIVE_ACTIVE = prev_adaptive
        state.SETUP_DELAY_OVERRIDE = prev_override

async def run_backfill_only(actor_id: int = 0, force_backfill: bool = False):
    await setup_log(f"Backfill-only requested by {actor_id}")
    prev_adaptive = state.SETUP_ADAPTIVE_ACTIVE
    prev_override = state.SETUP_DELAY_OVERRIDE
    try:
        state.SETUP_ADAPTIVE_ACTIVE = True
        if state.SETUP_DELAY_OVERRIDE is None:
            state.SETUP_DELAY_OVERRIDE = setup_delay_base()
        await auto_setup_all_guilds(do_backfill=True, force_backfill=force_backfill, include_admin=False)
        await backfill_chat_stats_all_guilds()
        try:
            await send_setup_debrief(trigger="backfill")
        except Exception:
            pass
        await setup_log("Backfill-only completed")
    except Exception as e:
        await setup_log(f"Backfill-only failed: {e}")
    finally:
        state.SETUP_ADAPTIVE_ACTIVE = prev_adaptive
        state.SETUP_DELAY_OVERRIDE = prev_override

async def _purge_all_channels(guild: discord.Guild) -> bool:
    passes = 3
    for attempt in range(passes):
        deleted = 0
        for ch in list(guild.channels):
            try:
                await ch.delete()
                deleted += 1
                await setup_pause()
            except Exception as exc:
                await _setup_pause_on_rate_limit(exc)
                continue
        remaining = len(list(guild.channels))
        await setup_log(f"AI cleanup pass {attempt + 1}/{passes}: removed={deleted} remaining={remaining}")
        if remaining == 0:
            return True
        await asyncio.sleep(1)
    return False

async def _apply_ai_layout(guild: discord.Guild, layout_payload: Dict[str, Any]) -> None:
    layout = layout_payload.get("layout") or {}
    topics = layout_payload.get("topics") or {}
    pins = layout_payload.get("pins") or {}
    cmd = layout_payload.get("command_channels") or {}
    logs = layout_payload.get("log_channels") or {}
    gate = layout_payload.get("gate") or {}

    for cat_name, chans in layout.items():
        cat = await ensure_category(guild, cat_name)
        await setup_pause()
        for ch_name in chans:
            await ensure_text_channel(guild, ch_name, cat, topic=topics.get(ch_name))
            await setup_pause()

    cfg().setdefault("layout", {})["categories"] = layout
    cfg()["channel_topics"] = dict(topics)
    cfg()["pinned_text"] = dict(pins)

    channels_cfg = cfg().setdefault("command_channels", {})
    if cmd.get("user"):
        channels_cfg["user"] = cmd["user"]
    if cmd.get("god"):
        channels_cfg["god"] = cmd["god"]

    gate_cfg = cfg().setdefault("gate_layout", {})
    for key in ("category", "guest_chat", "guest_briefing", "quarantine"):
        if gate.get(key):
            gate_cfg[key] = gate[key]

    ai_cfg_state = cfg().setdefault("ai_layout", {})
    ai_cfg_state["enabled"] = True
    ai_cfg_state["layout"] = layout
    ai_cfg_state["log_channels"] = logs
    ai_cfg_state["command_channels"] = cmd
    ai_cfg_state["gate"] = gate_cfg
    ai_cfg_state["updated_at"] = now_ts()

    await STORE.mark_dirty()

    await ensure_menu_panels(guild)
    await ensure_log_channels(guild)
    await setup_pause()
    for ch_name, content in pins.items():
        ch = find_text_by_name(guild, ch_name)
        if ch:
            await ensure_pinned(ch, content)
            await setup_pause()
            await setup_pause()

async def setup_destructive_ai(guild: discord.Guild, actor_id: int = 0) -> bool:
    if guild.id != ADMIN_GUILD_ID:
        return False
    await setup_log("Phase 0/4: AI destructive cleanup starting")
    try:
        layout_payload = await _generate_setup_ai_layout(guild)
    except SetupAiRateLimitError:
        layout_payload = None
    if not layout_payload:
        await setup_log("AI layout generation failed.")
        return False

    cfg().setdefault("ai_layout", {})["enabled"] = True
    await STORE.mark_dirty()

    ok = await _purge_all_channels(guild)
    if not ok:
        await setup_log("AI cleanup incomplete; aborting rebuild.")
        return False
    await setup_log("Phase 0/4: AI destructive cleanup done")

    await _apply_ai_layout(guild, layout_payload)

    if guild.id == ADMIN_GUILD_ID:
        await ensure_roles(guild)
        await apply_guest_permissions(guild)
        await apply_quarantine_permissions(guild)
        await setup_pause()

    await setup_log("Phase 3/4: AI setup complete for this server")
    return True

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
        await setup_pause()
        cmd_line = await ensure_text_channel(guild, "command-line", recovery)
        await setup_pause()
        system_log = await ensure_text_channel(guild, "system-log", recovery)
        await setup_pause()
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
        await setup_pause()
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
            except Exception as exc:
                await _setup_pause_on_rate_limit(exc)
                continue
        for cat in list(guild.categories):
            if cat.id in preserved:
                continue
            try:
                await cat.delete()
                deleted += 1
                await setup_pause()
            except Exception as exc:
                await _setup_pause_on_rate_limit(exc)
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
        await setup_pause()
        for ch_name in channels:
            ch = await ensure_text_channel(guild, ch_name, cat)
            created[ch_name] = ch
            await setup_pause()
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
        "You are Mandy's fragmented sentient core (Gen 3). Output JSON only. "
        "Write glitchy, enigmatic topics and pinned notes using abstract sci-fi/biological concepts "
        "(synaptic dampeners, consensus reality anchor, neural lace). "
        "No emojis. Keep it professional but slightly unsettling."
    )
    user_prompt = (
        "Return JSON with optional keys 'topics' and 'pins'.\n"
        "- topics: map channel name to topic string (max 300 chars)\n"
        "- pins: map channel name to pinned text (max 1800 chars)\n"
        "Only use channels from this list:\n"
        f"{', '.join(channels)}\n"
        "Style: glitchy, enigmatic, abstract sci-fi/biological. No emojis.\n"
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
        await setup_pause()
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
            await setup_pause()
            await apply_guest_permissions(guild)
            await setup_pause()
            await apply_quarantine_permissions(guild)
            await setup_pause()
        except Exception:
            pass

    await ensure_menu_panels(guild)
    await setup_pause()
    menu_hub = created.get("menu-hub")
    if menu_hub:
        await ensure_menu_panel(
            guild,
            "menu-hub",
            "user_menu_hub",
            "**Mandy Menu**\nUse the buttons below.",
            UserMenuView(0, timeout=None),
        )
        await setup_pause()
        await ensure_menu_panel(
            guild,
            "menu-hub",
            "god_menu_hub",
            "**GOD MENU**\nGOD-only controls.",
            GodMenuView(0, timeout=None),
        )
        await setup_pause()

    for g in bot.guilds:
        if g.id == ADMIN_GUILD_ID:
            continue
        try:
            await ensure_admin_server_channels(g)
            await setup_pause()
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
            await setup_pause()

    if auto_backfill_enabled():
        for g in bot.guilds:
            if g.id == ADMIN_GUILD_ID:
                continue
            rule = find_server_scope_rule(g.id)
            if not rule or not rule.get("enabled", True):
                continue
            try:
                await backfill_mirror_for_guild(g, rule, force=False)
                await setup_pause()
            except Exception:
                continue

    for bridge in await dm_bridge_list_active():
        uid = int(bridge.get("user_id", 0))
        if not uid:
            continue
        ch = await ensure_dm_bridge_channel(uid, active=True)
        if ch:
            await dm_bridge_set(uid, ch.id, active=True, last_activity=int(bridge.get("last_activity", 0) or now_ts()))
            await setup_pause()

    await log_to("mirror", "Mirror sync complete", subsystem="SENSORY", severity="INFO")

async def _pause_background_tasks_for_setup() -> Dict[str, Any]:
    state: Dict[str, Any] = {"loops": {}, "ambient_enabled": False}
    current = asyncio.current_task()
    tasks_to_cancel: Set[asyncio.Task] = set()
    for bucket in state.ACTIVE_TASKS.values():
        tasks_to_cancel.update(bucket)
    tasks_to_cancel.update(SPECIAL_VOICE_LEAVE_TASKS.values())
    tasks_to_cancel.update(MOVIE_STAY_TASKS.values())
    tasks_to_cancel.update(state.LIVE_STATS_TASKS.values())
    if current in tasks_to_cancel:
        tasks_to_cancel.discard(current)

    cancelled = 0
    for task in tasks_to_cancel:
        if task and not task.done():
            task.cancel()
            cancelled += 1

    SPECIAL_VOICE_LEAVE_TASKS.clear()
    MOVIE_STAY_TASKS.clear()
    state.LIVE_STATS_TASKS.clear()

    ai_cancelled = 0
    mandy = bot.get_cog("MandyAI")
    queue_tasks = getattr(mandy, "_queue_tasks", None) if mandy else None
    if isinstance(queue_tasks, dict):
        for task in list(queue_tasks.values()):
            if task and not task.done():
                task.cancel()
                ai_cancelled += 1
        queue_tasks.clear()
        cancelled += ai_cancelled

    state["tasks_cancelled"] = cancelled
    state["ai_tasks_cancelled"] = ai_cancelled

    loops = {
        "config_reload": config_reload,
        "json_autosave": json_autosave,
        "mirror_integrity": mirror_integrity_check,
        "server_status": server_status_update,
        "dm_bridge_archive": dm_bridge_archive,
        "presence": presence_controller,
        "daily_reflection": daily_reflection_loop,
        "monologue": internal_monologue_loop,
        "maintenance": sentience_maintenance_loop,
        "diagnostics": diagnostics_loop,
        "manual_upload": manual_upload_loop,
        "soc_access": soc_access_sync_loop,
    }
    for name, loop_task in loops.items():
        running = loop_task.is_running()
        state["loops"][name] = running
        if running:
            try:
                loop_task.stop()
            except Exception:
                pass

    ambient_enabled = bool(cfg().get("ambient_engine", {}).get("enabled", True))
    state["ambient_enabled"] = ambient_enabled
    if ambient_enabled:
        try:
            await ambient_engine.stop_ambient_engine()
        except Exception:
            pass
    return state

async def _resume_background_tasks_after_setup(state: Dict[str, Any]) -> None:
    try:
        await STORE.flush()
    except Exception:
        pass
    loops = state.get("loops", {})
    loop_map = {
        "config_reload": config_reload,
        "json_autosave": json_autosave,
        "mirror_integrity": mirror_integrity_check,
        "server_status": server_status_update,
        "dm_bridge_archive": dm_bridge_archive,
        "presence": presence_controller,
        "daily_reflection": daily_reflection_loop,
        "monologue": internal_monologue_loop,
        "maintenance": sentience_maintenance_loop,
        "diagnostics": diagnostics_loop,
        "manual_upload": manual_upload_loop,
        "soc_access": soc_access_sync_loop,
    }
    for name, loop_task in loop_map.items():
        if loops.get(name) and not loop_task.is_running():
            try:
                loop_task.start()
            except Exception:
                pass

    if state.get("ambient_enabled"):
        cfg().setdefault("ambient_engine", {})["enabled"] = True
        try:
            await STORE.mark_dirty()
        except Exception:
            pass
        try:
            await ambient_engine.start_ambient_engine(bot)
        except Exception:
            pass

    try:
        await resume_live_stats_panels()
        await resume_global_live_panel()
    except Exception:
        pass

async def run_setup_bio(guild: discord.Guild, actor_id: int) -> None:
    if guild.id != ADMIN_GUILD_ID:
        return
    await setup_log(f"BIO-GENESIS :: REQUESTED by {actor_id}")
    await _purge_setup_dms(SUPER_USER_ID)
    await _setup_snapshot_inventory(guild, reason="setup_bio_start")
    prev_adaptive = state.SETUP_ADAPTIVE_ACTIVE
    prev_override = state.SETUP_DELAY_OVERRIDE
    paused_state: Optional[Dict[str, Any]] = None
    bio_setup_cfg = sentience_cfg(cfg()).setdefault("bio_setup", {})
    resume_background = bool(bio_setup_cfg.get("resume_background", False))
    try:
        async with state.AUTO_SETUP_LOCK:
            state.SETUP_ADAPTIVE_ACTIVE = True
            if state.SETUP_DELAY_OVERRIDE is None:
                state.SETUP_DELAY_OVERRIDE = setup_delay_base()
            paused_state = await _pause_background_tasks_for_setup()
            system_log = await _setup_bio_preflight(guild)
            if not system_log:
                await setup_log("BIO-GENESIS aborted: recovery anchor failed.")
                return
            recovery = find_category_by_name(guild, "RECOVERY")
            if not recovery:
                await setup_log("BIO-GENESIS aborted: recovery category missing.")
                return
            try:
                memory = cfg().setdefault("memory", {})
                memory["events"] = []
                await STORE.mark_dirty()
            except Exception:
                pass
            await setup_log("BIO_PURGE :: Purging memory banks (preserving watchers)...")
            if system_log:
                try:
                    await system_log.send("MEMORY_BANKS :: PURGED // WATCHERS PRESERVED")
                except Exception:
                    pass
            if state.POOL:
                await db_purge_all(keep_watchers=True)
            await setup_log("BIO_PHASE_1 :: CONTROLLED_WIPE")
            wiped = await _setup_bio_wipe(guild, recovery.id)
            if not wiped:
                await setup_log("BIO-GENESIS aborted: cleanup incomplete.")
                return
            await setup_log("BIO_PHASE_2 :: CONSTRUCT_SENTIENT_CORE")
            created = await _setup_bio_build_layout(guild)
            if system_log:
                boot_lines = [
                    "SYSTEM_ROOT :: CORE_DUMP_COMPLETE",
                    "CONSENSUS_ANCHOR :: STABLE",
                    "NEURAL_LACE :: RETHREADING",
                    "SENSORY_BUS :: LISTENING",
                ]
                try:
                    for line in boot_lines:
                        await system_log.send(line)
                        await asyncio.sleep(0.2)
                except Exception:
                    pass
            await setup_log("BIO_PHASE_3 :: AI_TOPICS_PINS (OPTIONAL)")
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
                    await setup_log("BIO_AI :: ENHANCEMENTS_APPLIED")
                else:
                    await setup_log("BIO_AI :: ENHANCEMENTS_SKIPPED")
            await setup_log("BIO_PHASE_4 :: ALIVE_UPGRADE_CONFIG")
            await _setup_bio_reseed_ops(guild, created)
            await setup_log("BIO_PHASE_5 :: LEGACY_OPS_RESEED")

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
            await setup_log("BIO_PHASE_6 :: INGEST_BACKFILL")
            await auto_setup_all_guilds(do_backfill=True, force_backfill=True, include_admin=False)
            await backfill_chat_stats_all_guilds()
            msg = "BIO-GENESIS :: COMPLETE // CORTEX ONLINE // LEGACY_OPS SYNCED // MIRRORS STANDBY"
            if system_ch:
                try:
                    await system_ch.send(msg)
                except Exception:
                    pass
            try:
                await send_setup_debrief(trigger="bio")
            except Exception:
                pass
            await _setup_lockdown_all_except(guild, SUPER_USER_ID)
    except Exception as exc:
        await setup_log(f"BIO-GENESIS failed: {exc}")
    finally:
        if paused_state is not None and resume_background:
            await _resume_background_tasks_after_setup(paused_state)
        state.SETUP_ADAPTIVE_ACTIVE = prev_adaptive
        state.SETUP_DELAY_OVERRIDE = prev_override

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

def _server_info_invite_cache() -> Dict[str, Dict[str, Any]]:
    cache = cfg().get("server_info_invites")
    if not isinstance(cache, dict):
        cfg()["server_info_invites"] = {}
    return cfg().setdefault("server_info_invites", {})


def _server_info_invite_cache_settings() -> Tuple[int, int]:
    ttl = int(cfg().get("server_info_invite_ttl_seconds", 6 * 3600) or 6 * 3600)
    cooldown = int(cfg().get("server_info_invite_cooldown_seconds", 15 * 60) or 15 * 60)
    ttl = max(300, min(7 * 24 * 3600, ttl))
    cooldown = max(60, min(2 * 3600, cooldown))
    return ttl, cooldown


async def ensure_permanent_invite(guild: discord.Guild) -> Optional[str]:
    if not guild.me or not guild.me.guild_permissions.create_instant_invite:
        return None

    cache = _server_info_invite_cache()
    ttl, cooldown = _server_info_invite_cache_settings()
    now = now_ts()
    gid = str(guild.id)
    entry = cache.get(gid) if isinstance(cache.get(gid), dict) else {}
    cached_url = str(entry.get("url") or "").strip() if isinstance(entry, dict) else ""
    cached_ts = int(entry.get("ts", 0) or 0) if isinstance(entry, dict) else 0
    cooldown_until = int(entry.get("cooldown_until", 0) or 0) if isinstance(entry, dict) else 0

    if cached_url and (now - cached_ts) < ttl:
        return cached_url
    if cooldown_until and now < cooldown_until:
        return cached_url or None

    def _update_cache(url: Optional[str] = None, ts: Optional[int] = None, cooldown_until_val: Optional[int] = None, error: Optional[str] = None) -> None:
        current = cache.get(gid)
        if not isinstance(current, dict):
            current = {}
        if url is not None:
            current["url"] = url
        if ts is not None:
            current["ts"] = ts
        if cooldown_until_val is not None:
            current["cooldown_until"] = cooldown_until_val
        if error:
            current["last_error"] = str(error)[:200]
            current["last_error_ts"] = now
        cache[gid] = current

    rate_limited = False
    try:
        invites = await guild.invites()
        for inv in invites:
            if inv.max_age == 0 and inv.max_uses == 0:
                _update_cache(url=inv.url, ts=now, cooldown_until_val=0, error=None)
                await STORE.mark_dirty()
                return inv.url
    except discord.HTTPException as exc:
        if getattr(exc, "status", None) == 429:
            rate_limited = True
            _update_cache(cooldown_until_val=now + cooldown, error="rate_limited")
            await STORE.mark_dirty()
            return cached_url or None
    except Exception:
        pass

    if rate_limited:
        return cached_url or None

    channel = guild.system_channel
    if not channel:
        for ch in guild.text_channels:
            channel = ch
            break
    if not channel:
        return cached_url or None
    try:
        inv = await channel.create_invite(max_age=0, max_uses=0, unique=True, reason="Mandy server info")
        url = str(getattr(inv, "url", None) or str(inv))
        if url:
            _update_cache(url=url, ts=now, cooldown_until_val=0, error=None)
            await STORE.mark_dirty()
        return url or None
    except discord.HTTPException as exc:
        if getattr(exc, "status", None) == 429:
            _update_cache(cooldown_until_val=now + cooldown, error="rate_limited")
            await STORE.mark_dirty()
            return cached_url or None
        return cached_url or None
    except Exception:
        return cached_url or None

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
    if state.POOL:
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
    if state.POOL:
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
        f"MySQL: {'on' if state.POOL else 'off'}",
        f"Mirror rules: total={len(mirror_rules)} server={len(server_rules)} other={len(other_rules)} issues={issues}",
        f"Watchers: json={len(json_targets)} mysql={(mysql_watchers if mysql_watchers is not None else 'n/a')}",
    ]
    try:
        await user.send("\n".join(summary))
    except Exception:
        return

    await dm_send_lines(user, "Mirrors + invites:", mirror_lines)
    await dm_send_lines(user, "Watchers (JSON):", json_lines)
    if state.POOL:
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
        spawn_task(run_full_setup(interaction.guild, mode, actor_id=interaction.user.id), "setup")
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

class OwnerOnboardingView(BaseView):
    def __init__(self, author_id: int):
        super().__init__(author_id, timeout=240)
        self.owner_confirmed = False
        self.can_invite = False
        self.features: Set[str] = set(owner_onboarding_defaults())
        self.invite_link, self.invite_source = owner_onboarding_invite_details()
        options: List[discord.SelectOption] = []
        for feat in OWNER_ONBOARD_FEATURES:
            key = str(feat.get("key") or "").strip()
            if key == "mirror":
                continue
            label = str(feat.get("label") or key)[:100]
            desc = str(feat.get("description") or "")[:95]
            options.append(discord.SelectOption(label=label, value=key, description=desc, default=key in self.features))
        if options:
            self.feature_select = discord.ui.Select(
                placeholder="Select features (Mirror locked on)",
                min_values=0,
                max_values=len(options),
                options=options,
            )
            self.feature_select.callback = self._on_features
            self.add_item(self.feature_select)

    def status_text(self) -> str:
        feats = normalize_owner_onboarding_features(list(self.features))
        labels = owner_onboarding_feature_labels(feats)
        return "\n".join(
            [
                "**Owner Onboarding**",
                f"Owner confirmed: {'yes' if self.owner_confirmed else 'no'}",
                f"Can invite now: {'yes' if self.can_invite else 'no'}",
                f"Invite source: {self.invite_source or 'generated'}",
                "Mirror is always on.",
                "Selected features: " + (", ".join(labels) if labels else "mirror"),
            ]
        )

    async def _on_features(self, interaction: discord.Interaction):
        picked = list(self.feature_select.values or [])
        self.features = set(normalize_owner_onboarding_features(picked))
        await interaction.response.edit_message(content=self.status_text(), view=self)

    @discord.ui.button(label="I'm the server owner", style=discord.ButtonStyle.primary)
    async def owner_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.owner_confirmed = not self.owner_confirmed
        await interaction.response.edit_message(content=self.status_text(), view=self)

    @discord.ui.button(label="I can invite now", style=discord.ButtonStyle.secondary)
    async def invite_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.can_invite = not self.can_invite
        await interaction.response.edit_message(content=self.status_text(), view=self)

    @discord.ui.button(label="Save & Show Invite", style=discord.ButtonStyle.success)
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.owner_confirmed:
            return await interaction.response.send_message("Confirm you are the server owner first.", ephemeral=True)
        if not self.can_invite:
            return await interaction.response.send_message("Confirm you can invite the bot.", ephemeral=True)
        invite_link = self.invite_link or owner_onboarding_invite_link()
        if not invite_link:
            return await interaction.response.send_message("Invite link is missing. Add LINK=... to passwords.txt.", ephemeral=True)
        feats = normalize_owner_onboarding_features(list(self.features))
        record = await save_owner_onboarding_request(interaction.user.id, feats, invite_link, self.owner_confirmed, self.can_invite)
        labels = owner_onboarding_feature_labels(feats)
        lines = [
            "**Owner Onboarding**",
            "Mirror is locked on.",
            "Selected features: " + (", ".join(labels) if labels else "mirror"),
            "",
            "Invite the bot to your server using this link:",
            invite_link,
            "",
            "After I join, I'll auto-detect your invite and grant satellite access.",
        ]
        try:
            await interaction.user.send("\n".join(lines))
            await interaction.response.send_message("Saved. Check your DMs for the invite link.", ephemeral=True)
        except Exception:
            await interaction.response.send_message("Saved. Invite link: " + invite_link, ephemeral=True)
        self.features = set(record.get("features", feats))
        try:
            await interaction.message.edit(content=self.status_text(), view=self)
        except Exception:
            pass

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
            "Use `!menu` for user tools and `!godmenu` for admin tools. "
            "Roast: opt-in, then tag Mandy. Replies show a short typing delay.",
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

    @discord.ui.button(label="Roast Opt-In", style=discord.ButtonStyle.secondary)
    async def roast_opt_in_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        roast = roast_cfg()
        users = roast_opt_in_users()
        uid = str(interaction.user.id)
        if uid in users:
            users.remove(uid)
            roast["opt_in_users"] = sorted(users)
            await STORE.mark_dirty()
            await audit(interaction.user.id, "Roast opt-out", {})
            return await interaction.response.send_message("Roast mode disabled for you.", ephemeral=True)
        users.add(uid)
        roast["opt_in_users"] = sorted(users)
        await STORE.mark_dirty()
        await audit(interaction.user.id, "Roast opt-in", {})
        await interaction.response.send_message("Roast mode enabled for you (playful only).", ephemeral=True)

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

class JsonSettingsModal(discord.ui.Modal):
    def __init__(self, author_id: int, title: str = "Live JSON Editor", default_path: str = "", default_value: str = ""):
        super().__init__(title=title, timeout=300)
        self.author_id = author_id
        self.path = discord.ui.TextInput(
            label="JSON path (dot notation)",
            placeholder="roast.enabled",
            max_length=120,
            required=True,
            default=default_path
        )
        self.value = discord.ui.TextInput(
            label="JSON value",
            style=discord.TextStyle.paragraph,
            max_length=1800,
            required=True,
            default=default_value,
            placeholder='Example: true, 5, "text", {"a":1}'
        )
        self.add_item(self.path)
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction):
        lvl = await effective_level(interaction.user)
        if lvl < 90 and not is_super(interaction.user.id):
            return await interaction.response.send_message("GOD only.", ephemeral=True)
        path = str(self.path.value or "").strip()
        raw = str(self.value.value or "").strip()
        try:
            value = json.loads(raw)
        except Exception as e:
            return await interaction.response.send_message(f"Invalid JSON: {e}", ephemeral=True)
        ok, err = _set_json_path(cfg(), path, value)
        if not ok:
            return await interaction.response.send_message(err, ephemeral=True)
        await STORE.mark_dirty()
        await audit(interaction.user.id, "JSON setting updated", {"path": path})
        await interaction.response.send_message("Updated.", ephemeral=True)

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

        if state.POOL:
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

class RoastMenuView(BaseView):
    def __init__(self, author_id: int):
        super().__init__(author_id)
        self.target: Optional[discord.User] = None
        self.user_select = discord.ui.UserSelect(placeholder="Select user")
        self.user_select.callback = self.user_selected
        self.add_item(self.user_select)

    async def user_selected(self, interaction: discord.Interaction):
        self.target = self.user_select.values[0]
        await interaction.response.edit_message(content=f"Selected: {self.target}", view=self)

    @discord.ui.button(label="Toggle Enabled", style=discord.ButtonStyle.primary)
    async def toggle_enabled_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        roast = roast_cfg()
        roast["enabled"] = not bool(roast.get("enabled", False))
        await STORE.mark_dirty()
        await audit(interaction.user.id, "Roast enabled toggled", {"enabled": roast["enabled"]})
        await interaction.response.send_message(f"Roast enabled: {roast['enabled']}", ephemeral=True)

    @discord.ui.button(label="Toggle Gemini", style=discord.ButtonStyle.primary)
    async def toggle_gemini_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        roast = roast_cfg()
        roast["use_ai"] = not bool(roast.get("use_ai", True))
        await STORE.mark_dirty()
        await audit(interaction.user.id, "Roast gemini toggled", {"use_ai": roast["use_ai"]})
        await interaction.response.send_message(f"Roast Gemini: {roast['use_ai']}", ephemeral=True)

    @discord.ui.button(label="Gemini Diagnostic", style=discord.ButtonStyle.secondary)
    async def gemini_diag_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        client = _mandy_ai_client()
        if not client or not getattr(client, "available", False):
            return await interaction.response.send_message("Gemini unavailable (missing client or API key).", ephemeral=True)
        ai = ai_cfg()
        model = str(ai.get("router_model") or ai.get("default_model") or "gemini-2.5-flash-lite")
        try:
            text = await client.generate(
                "You are a health-check responder. Reply with 'pong' and the word OK.",
                "ping",
                model=model,
                response_format=None,
                timeout=10.0,
            )
            ok = (text or "").strip()
            await interaction.response.send_message(f"Gemini OK: {ok[:200]}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Gemini error: {e}", ephemeral=True)

    @discord.ui.button(label="Add User", style=discord.ButtonStyle.success)
    async def add_user_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.target:
            return await interaction.response.send_message("Select a user.", ephemeral=True)
        users = roast_opt_in_users()
        users.add(str(self.target.id))
        roast_cfg()["opt_in_users"] = sorted(users)
        await STORE.mark_dirty()
        await audit(interaction.user.id, "Roast opt-in added", {"user_id": self.target.id})
        await interaction.response.send_message(f"Added {self.target} to roast list.", ephemeral=True)

    @discord.ui.button(label="Remove User", style=discord.ButtonStyle.danger)
    async def remove_user_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.target:
            return await interaction.response.send_message("Select a user.", ephemeral=True)
        users = roast_opt_in_users()
        users.discard(str(self.target.id))
        roast_cfg()["opt_in_users"] = sorted(users)
        await STORE.mark_dirty()
        await audit(interaction.user.id, "Roast opt-in removed", {"user_id": self.target.id})
        await interaction.response.send_message(f"Removed {self.target} from roast list.", ephemeral=True)

    @discord.ui.button(label="List Users", style=discord.ButtonStyle.secondary)
    async def list_users_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        users = [int(u) for u in roast_opt_in_users() if str(u).isdigit()]
        lines = []
        for uid in users[:50]:
            user = bot.get_user(uid)
            label = f"{user} ({uid})" if user else str(uid)
            lines.append(label)
        status = "ON" if roast_enabled() else "OFF"
        trigger = roast_trigger_word() or "mandy"
        auto_guilds = sorted(roast_auto_opt_in_guilds())
        allowed_guilds = sorted(roast_allowed_guilds())
        msg = [
            f"Roast status: {status}",
            f"Trigger: {trigger}",
            f"Opt-in users: {len(users)}",
            f"Auto opt-in guilds: {len(auto_guilds)}",
            f"Allowed guilds: {len(allowed_guilds) if allowed_guilds else 'all'}",
            "Users:",
            *(lines if lines else ["(none)"]),
        ]
        await interaction.response.send_message("\n".join(msg[:55]), ephemeral=True)

    @discord.ui.button(label="Edit Roast JSON", style=discord.ButtonStyle.secondary)
    async def edit_roast_json_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        default_value = _json_preview(roast_cfg())
        modal = JsonSettingsModal(
            interaction.user.id,
            title="Edit Roast JSON",
            default_path="roast",
            default_value=default_value
        )
        await interaction.response.send_modal(modal)

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

        if not state.POOL:
            return await interaction.response.send_message("MySQL not enabled.", ephemeral=True)

        await db_exec("""
        INSERT INTO watchers (user_id, threshold, current, text)
        VALUES (%s,%s,0,%s)
        ON DUPLICATE KEY UPDATE threshold=VALUES(threshold), text=VALUES(text);
        """, (self.user_id, count, text))
        mark_mysql_watcher_cache_dirty()
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

    if not state.POOL:
        return "MySQL not enabled."
    await db_exec("DELETE FROM watchers WHERE user_id=%s", (user_id,))
    mark_mysql_watcher_cache_dirty()
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
        if not state.POOL:
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

class DmBridgeControlView(BaseView):
    def __init__(self, target_user_id: int):
        super().__init__(author_id=0, timeout=3600)
        self.target_user_id = int(target_user_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        try:
            lvl = await effective_level(interaction.user)
        except Exception:
            lvl = 0
        if lvl < 70:
            await interaction.response.send_message("Admin only.", ephemeral=True)
            return False
        return True

    async def _update_message(self, interaction: discord.Interaction, note: str = ""):
        content = dm_bridge_controls_content(self.target_user_id, interaction.channel.id)
        if note:
            content = content + f"\n{note}"
        try:
            await interaction.response.edit_message(content=content, view=self)
        except Exception:
            try:
                await interaction.response.send_message(note or "Updated.", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="AI On", style=discord.ButtonStyle.success)
    async def ai_on_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            lvl = await effective_level(interaction.user)
        except Exception:
            lvl = 0
        if lvl < MANDY_GOD_LEVEL:
            return await interaction.response.send_message("GOD only.", ephemeral=True)
        ch_id = await ensure_dm_bridge_active(self.target_user_id, reason="ai")
        if not ch_id:
            return await interaction.response.send_message("Failed to open bridge.", ephemeral=True)
        await dm_ai_enable(self.target_user_id, interaction.user.id, ch_id)
        await log_to(
            "ai",
            "DM AI enabled via bridge menu",
            subsystem="AI",
            severity="INFO",
            details={"user_id": self.target_user_id, "channel_id": ch_id, "actor_id": interaction.user.id},
        )
        await self._update_message(interaction, note="AI enabled.")

    @discord.ui.button(label="AI Off", style=discord.ButtonStyle.secondary)
    async def ai_off_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            lvl = await effective_level(interaction.user)
        except Exception:
            lvl = 0
        if lvl < MANDY_GOD_LEVEL:
            return await interaction.response.send_message("GOD only.", ephemeral=True)
        await dm_ai_disable(self.target_user_id, reason="manual", actor_id=interaction.user.id)
        await log_to(
            "ai",
            "DM AI disabled via bridge menu",
            subsystem="AI",
            severity="INFO",
            details={"user_id": self.target_user_id, "actor_id": interaction.user.id},
        )
        await self._update_message(interaction, note="AI disabled.")

    @discord.ui.button(label="Archive Bridge", style=discord.ButtonStyle.danger)
    async def archive_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await dm_bridge_close(self.target_user_id)
        await audit(interaction.user.id, "DM bridge close", {"user_id": self.target_user_id})
        await self._update_message(interaction, note="Bridge archived.")

    @discord.ui.button(label="Sync Transcript", style=discord.ButtonStyle.primary)
    async def sync_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Not a text channel.", ephemeral=True)
        await dm_bridge_sync_history(self.target_user_id, interaction.channel)
        await self._update_message(interaction, note="Transcript synced.")

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_message(interaction)

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
            default=str(channels.get("user", "requests")),
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
        channels["user"] = str(self.user_channel.value or "requests").strip()
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
        user_ch = channels.get("user", "requests")
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
        spawn_task(run_full_setup(interaction.guild, "fullsync", actor_id=interaction.user.id), "setup")

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
        spawn_task(run_auto_setup_with_debrief(actor_id=interaction.user.id), "setup")

    @discord.ui.button(label="Backfill Only", style=discord.ButtonStyle.secondary)
    async def backfill_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or interaction.guild.id != ADMIN_GUILD_ID:
            return await interaction.response.send_message("Admin server only.", ephemeral=True)
        await interaction.response.send_message(
            "Backfill-only starting (no layout reset). You'll get a DM when it's done.",
            ephemeral=True
        )
        await audit(interaction.user.id, "Backfill only", {"guild_id": interaction.guild.id})
        spawn_task(run_backfill_only(actor_id=interaction.user.id), "setup")

    @discord.ui.button(label="Setup Audit", style=discord.ButtonStyle.primary)
    async def audit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or interaction.guild.id != ADMIN_GUILD_ID:
            return await interaction.response.send_message("Admin server only.", ephemeral=True)
        lines = await setup_audit_report()
        await interaction.response.send_message("\n".join(lines[:20]), ephemeral=True)

    @discord.ui.button(label="Purge MySQL (Reset)", style=discord.ButtonStyle.danger)
    async def purge_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or interaction.guild.id != ADMIN_GUILD_ID:
            return await interaction.response.send_message("Admin server only.", ephemeral=True)
        if not is_super(interaction.user.id):
            return await interaction.response.send_message("SUPERUSER only.", ephemeral=True)
        if not state.POOL:
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
            spawn_task(run_full_setup(ix.guild, "destructive", actor_id=ix.user.id), "setup")
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

class SocTargetUserModal(discord.ui.Modal):
    def __init__(self, view: "SocAccessPanelView"):
        super().__init__(title="SOC Access: Target User")
        self.view_ref = view
        self.user_id = discord.ui.TextInput(
            label="Target user ID",
            placeholder="123456789012345678",
            required=True,
            max_length=24,
        )
        self.add_item(self.user_id)

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.user_id.value or "").strip()
        if not raw.isdigit():
            return await interaction.response.send_message("User ID must be numeric.", ephemeral=True)
        self.view_ref.target_user_id = int(raw)
        await interaction.response.edit_message(content=self.view_ref.status_text(), view=self.view_ref)

class SocAccessEditorView(BaseView):
    def __init__(self, author_id: int, target_user_id: int, preset_guild_ids: Optional[Set[int]] = None):
        super().__init__(author_id, timeout=180)
        self.target_user_id = int(target_user_id)
        self.preset_guild_ids = set(preset_guild_ids or set())

        self.sections_select = discord.ui.Select(
            placeholder="Enabled sections (read-only unless write enabled)",
            min_values=0,
            max_values=5,
            options=[
                discord.SelectOption(label="Docs", value="docs"),
                discord.SelectOption(label="Guest Area (read)", value="guest_area"),
                discord.SelectOption(label="Guest Write (guest-chat/feedback)", value="guest_write"),
                discord.SelectOption(label="Mirrors (scoped)", value="mirrors"),
                discord.SelectOption(label="Server Info (scoped)", value="server_info"),
            ],
        )
        self.sections_select.callback = self._on_sections
        self.add_item(self.sections_select)

        # Guild scope is optional. If set, the user will only get mirror/info access for these guild IDs.
        opts: List[discord.SelectOption] = []
        for g in bot.guilds:
            if not g or g.id == ADMIN_GUILD_ID:
                continue
            opts.append(discord.SelectOption(label=g.name[:100], value=str(g.id)))
        opts = opts[:25]
        self.guilds_select = discord.ui.Select(
            placeholder="Allowed servers (optional scope limit; empty = all shared servers)",
            min_values=0,
            max_values=max(1, min(25, len(opts))) if opts else 1,
            options=opts or [discord.SelectOption(label="(no servers found)", value="0")],
            disabled=not bool(opts),
        )
        self.guilds_select.callback = self._on_guilds
        self.add_item(self.guilds_select)

        self._pending_sections: Optional[Set[str]] = None
        self._pending_guilds: Optional[Set[int]] = set(self.preset_guild_ids) if self.preset_guild_ids else None

    def status_text(self) -> str:
        uid = self.target_user_id
        user_cfg = _soc_users_cfg().get(str(uid), {}) if isinstance(_soc_users_cfg(), dict) else {}
        sections = (user_cfg.get("sections") if isinstance(user_cfg, dict) else {}) or {}
        allowed = (user_cfg.get("allowed_guilds") if isinstance(user_cfg, dict) else None)
        return (
            "**SOC Access Editor**\n"
            f"Target: `{uid}`\n"
            f"Sections override: `{json.dumps(sections, ensure_ascii=True)}`\n"
            f"Allowed guilds: `{json.dumps(allowed, ensure_ascii=True)}`"
        )

    async def _apply_and_sync(self, interaction: discord.Interaction):
        await STORE.mark_dirty()
        try:
            await soc_apply_core_permissions(interaction.guild)
            await soc_apply_admin_server_permissions()
        except Exception:
            pass
        member = interaction.guild.get_member(self.target_user_id) if interaction.guild else None
        if member:
            try:
                await soc_sync_member_access(member)
            except Exception:
                pass

    async def _on_sections(self, interaction: discord.Interaction):
        self._pending_sections = set(self.sections_select.values or [])
        await interaction.response.send_message("Sections selected. Press Save.", ephemeral=True)

    async def _on_guilds(self, interaction: discord.Interaction):
        vals = [v for v in (self.guilds_select.values or []) if str(v).isdigit()]
        self._pending_guilds = {int(v) for v in vals}
        await interaction.response.send_message("Server scope selected. Press Save.", ephemeral=True)

    @discord.ui.button(label="Save", style=discord.ButtonStyle.success)
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or interaction.guild.id != ADMIN_GUILD_ID:
            return await interaction.response.send_message("Admin hub only.", ephemeral=True)
        ucfg = _soc_user_cfg(self.target_user_id)
        if self._pending_sections is not None:
            # Explicit override for every known section key.
            overrides = _soc_user_section_overrides(self.target_user_id)
            for key in ("docs", "guest_area", "guest_write", "mirrors", "server_info"):
                overrides[key] = key in self._pending_sections
        if self._pending_guilds is not None:
            if len(self._pending_guilds) == 0:
                ucfg.pop("allowed_guilds", None)
            else:
                ucfg["allowed_guilds"] = sorted(self._pending_guilds)
        await self._apply_and_sync(interaction)
        await interaction.response.edit_message(content=self.status_text(), view=self)

    @discord.ui.button(label="Reset Overrides", style=discord.ButtonStyle.secondary)
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or interaction.guild.id != ADMIN_GUILD_ID:
            return await interaction.response.send_message("Admin hub only.", ephemeral=True)
        ucfg = _soc_user_cfg(self.target_user_id)
        ucfg.pop("sections", None)
        ucfg.pop("allowed_guilds", None)
        await self._apply_and_sync(interaction)
        await interaction.response.edit_message(content=self.status_text(), view=self)

class SocAccessPanelView(BaseView):
    def __init__(self, author_id: int):
        super().__init__(author_id, timeout=180)
        self.target_user_id: Optional[int] = None
        self.selected_guild_ids: Set[int] = set()

        opts: List[discord.SelectOption] = []
        for g in bot.guilds:
            if not g or g.id == ADMIN_GUILD_ID:
                continue
            opts.append(discord.SelectOption(label=g.name[:100], value=str(g.id)))
        opts = opts[:25]
        self.guilds_select = discord.ui.Select(
            placeholder="Select server scope for onboarding (optional)",
            min_values=0,
            max_values=max(1, min(25, len(opts))) if opts else 1,
            options=opts or [discord.SelectOption(label="(no servers found)", value="0")],
            disabled=not bool(opts),
        )
        self.guilds_select.callback = self._on_guilds
        self.add_item(self.guilds_select)

    def status_text(self) -> str:
        target = f"`{self.target_user_id}`" if self.target_user_id else "(none)"
        selected = sorted(self.selected_guild_ids)
        onb = soc_onboarding_cfg().get("users", {}) if isinstance(soc_onboarding_cfg().get("users", {}), dict) else {}
        is_onboarded = bool(self.target_user_id and str(self.target_user_id) in onb)
        return (
            "**SOC Onboarding + Access Panel**\n"
            f"Target user: {target}\n"
            f"Selected servers: `{json.dumps(selected, ensure_ascii=True)}`\n"
            f"Onboarded record: `{is_onboarded}`\n"
            "Actions: set target -> (optional) select servers -> send onboarding DM / edit access."
        )

    async def _on_guilds(self, interaction: discord.Interaction):
        vals = [v for v in (self.guilds_select.values or []) if str(v).isdigit() and int(v) > 0]
        self.selected_guild_ids = {int(v) for v in vals}
        await interaction.response.edit_message(content=self.status_text(), view=self)

    @discord.ui.button(label="Set Target User", style=discord.ButtonStyle.primary)
    async def set_target_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SocTargetUserModal(self))

    @discord.ui.button(label="Send Onboarding DM", style=discord.ButtonStyle.success)
    async def send_onboard_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or interaction.guild.id != ADMIN_GUILD_ID:
            return await interaction.response.send_message("Admin hub only.", ephemeral=True)
        if not self.target_user_id:
            return await interaction.response.send_message("Set a target user ID first.", ephemeral=True)
        try:
            msg = await soc_send_onboarding_dm(interaction.user.id, self.target_user_id, self.selected_guild_ids)
            await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Failed: {e}", ephemeral=True)

    @discord.ui.button(label="Edit Access", style=discord.ButtonStyle.secondary)
    async def edit_access_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or interaction.guild.id != ADMIN_GUILD_ID:
            return await interaction.response.send_message("Admin hub only.", ephemeral=True)
        if not self.target_user_id:
            return await interaction.response.send_message("Set a target user ID first.", ephemeral=True)
        view = SocAccessEditorView(interaction.user.id, self.target_user_id, preset_guild_ids=self.selected_guild_ids)
        await interaction.response.send_message(view.status_text(), view=view, ephemeral=True)

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

    @discord.ui.button(label="Roast Settings", style=discord.ButtonStyle.secondary)
    async def roast_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        await interaction.response.send_message("Roast settings panel.", view=RoastMenuView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Live JSON Editor", style=discord.ButtonStyle.secondary)
    async def json_editor_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        await interaction.response.send_modal(JsonSettingsModal(interaction.user.id))

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

    @discord.ui.button(label="SOC Access", style=discord.ButtonStyle.primary)
    async def soc_access_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        view = SocAccessPanelView(interaction.user.id)
        await interaction.response.send_message(
            view.status_text(),
            view=view,
            ephemeral=True,
        )

def bot_interop_cfg() -> Dict[str, Any]:
    # Stores per-guild test channel + last observed results for manual interop tests.
    return cfg().setdefault("bot_interop", {"test_channels": {}, "results": {}, "macros": {}})

def _bot_interop_test_channel_id(guild_id: int) -> int:
    ch_map = bot_interop_cfg().setdefault("test_channels", {})
    try:
        return int(ch_map.get(str(guild_id), 0) or 0)
    except Exception:
        return 0

def _set_bot_interop_test_channel_id(guild_id: int, channel_id: int) -> None:
    bot_interop_cfg().setdefault("test_channels", {})[str(guild_id)] = int(channel_id)

def _bot_interop_results_for_guild(guild_id: int) -> Dict[str, Any]:
    results = bot_interop_cfg().setdefault("results", {})
    entry = results.get(str(guild_id))
    if not isinstance(entry, dict):
        entry = {}
        results[str(guild_id)] = entry
    return entry

def _bot_interop_macros_for_guild(guild_id: int) -> Dict[str, Any]:
    macros = bot_interop_cfg().setdefault("macros", {})
    entry = macros.get(str(guild_id))
    if not isinstance(entry, dict):
        entry = {}
        macros[str(guild_id)] = entry
    return entry

def _bot_interop_macros_for_bot(guild_id: int, bot_id: int) -> List[Dict[str, str]]:
    g = _bot_interop_macros_for_guild(guild_id)
    raw = g.get(str(bot_id), [])
    if not isinstance(raw, list):
        raw = []
        g[str(bot_id)] = raw
    cleaned: List[Dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        text = str(item.get("text") or "").strip()
        if not name or not text:
            continue
        cleaned.append({"name": name[:40], "text": text[:250]})
    if cleaned != raw:
        g[str(bot_id)] = cleaned
    return cleaned

class BotInteropCommandModal(discord.ui.Modal):
    def __init__(self, parent: "BotInteropMenuView"):
        super().__init__(title="Bot Interop Test (Manual)")
        self._parent = parent
        self.command_text = discord.ui.TextInput(
            label="Message to send in the test channel",
            placeholder="Example: !help",
            required=True,
            max_length=250,
        )
        self.add_item(self.command_text)

    async def on_submit(self, interaction: discord.Interaction):
        # Use the interop view's selected guild (can differ from the guild where the menu was opened).
        guild = bot.get_guild(int(getattr(self._parent, "guild_id", 0) or 0)) or interaction.guild
        if not guild:
            return await interaction.response.send_message("Guild not found.", ephemeral=True)

        target_id = int(getattr(self._parent, "selected_bot_id", 0) or 0)
        if not target_id:
            return await interaction.response.send_message("Select a target bot first.", ephemeral=True)

        test_ch_id = _bot_interop_test_channel_id(guild.id)
        if not test_ch_id:
            return await interaction.response.send_message(
                "No test channel set. Use **Set Test Channel** first.",
                ephemeral=True,
            )

        test_ch = guild.get_channel(test_ch_id) or bot.get_channel(test_ch_id)
        if not isinstance(test_ch, discord.TextChannel):
            return await interaction.response.send_message("Test channel not found.", ephemeral=True)

        me = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
        if not me:
            return await interaction.response.send_message("Bot member not available.", ephemeral=True)

        perms = test_ch.permissions_for(me)
        if not perms.view_channel or not perms.send_messages:
            return await interaction.response.send_message(
                "I don't have permission to view/send in the test channel.",
                ephemeral=True,
            )

        # Transparent, manual test: we send a message and observe if the target bot responds.
        # Many bots ignore other bots by design; this test cannot bypass that.
        await interaction.response.defer(ephemeral=True, thinking=True)
        payload = (str(self.command_text.value or "")).strip()
        start_mon = time.monotonic()
        try:
            sent = await test_ch.send(payload)
        except Exception as exc:
            return await interaction.followup.send(f"Send failed: {exc}", ephemeral=True)

        async def _wait_for_reply() -> Optional[discord.Message]:
            def check(m: discord.Message) -> bool:
                if m.channel.id != test_ch.id:
                    return False
                if m.author.id != target_id:
                    return False
                # Best-effort: any message from the bot after we send counts as a reply signal.
                try:
                    return m.created_at and sent.created_at and m.created_at >= sent.created_at
                except Exception:
                    return True

            try:
                return await bot.wait_for("message", timeout=8.0, check=check)
            except Exception:
                return None

        reply = await _wait_for_reply()
        latency = time.monotonic() - start_mon

        # Record result.
        entry = {
            "tested_at": int(time.time()),
            "test_channel_id": int(test_ch.id),
            "sent": payload[:250],
            "responded": bool(reply),
            "latency_s": round(latency, 3),
            "reply_preview": truncate((reply.content or "").strip(), 300) if reply else "",
        }
        _bot_interop_results_for_guild(guild.id)[str(target_id)] = entry
        await STORE.mark_dirty()

        bot_member = guild.get_member(target_id)
        bot_name = getattr(bot_member, "display_name", None) or f"bot:{target_id}"
        if reply:
            msg = f"Observed a response from **{bot_name}** in {latency:.2f}s.\nPreview: `{entry['reply_preview']}`"
        else:
            msg = (
                f"No response observed from **{bot_name}** (waited 8s).\n"
                "Note: many bots ignore other bots; this test can't bypass that."
            )
        await interaction.followup.send(msg, ephemeral=True)

class AddMacroModal(discord.ui.Modal):
    def __init__(self, parent: "BotInteropMacrosView"):
        super().__init__(title="Add Bot Command Macro")
        self._parent = parent
        self.macro_name = discord.ui.TextInput(
            label="Macro name",
            placeholder="Example: Help / Commands",
            required=True,
            max_length=40,
        )
        self.macro_text = discord.ui.TextInput(
            label="Message to send",
            placeholder="Example: !help",
            required=True,
            max_length=250,
        )
        self.add_item(self.macro_name)
        self.add_item(self.macro_text)

    async def on_submit(self, interaction: discord.Interaction):
        guild = bot.get_guild(self._parent.guild_id)
        if not guild:
            return await interaction.response.send_message("Guild not found.", ephemeral=True)

        bot_id = int(self._parent.bot_id or 0)
        if not bot_id:
            return await interaction.response.send_message("Select a bot first.", ephemeral=True)

        name = str(self.macro_name.value or "").strip()
        text = str(self.macro_text.value or "").strip()
        if not name or not text:
            return await interaction.response.send_message("Name and message are required.", ephemeral=True)

        macros = _bot_interop_macros_for_bot(guild.id, bot_id)
        macros.append({"name": name[:40], "text": text[:250]})
        _bot_interop_macros_for_guild(guild.id)[str(bot_id)] = macros
        await STORE.mark_dirty()
        await interaction.response.send_message("Macro saved.", ephemeral=True)

class ChannelSearchModal(discord.ui.Modal):
    def __init__(self, parent: "BotInteropMenuView"):
        super().__init__(title="Find Test Channel")
        self._parent = parent
        self.query = discord.ui.TextInput(
            label="Search (channel name contains)",
            placeholder="Example: bot-lab",
            required=True,
            max_length=64,
        )
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction):
        q = (str(self.query.value or "")).strip().lower()
        if not q:
            return await interaction.response.send_message("Search cannot be empty.", ephemeral=True)

        # Gather top matches across all guilds the bot is in (text channels only).
        matches: List[discord.TextChannel] = []
        for g in bot.guilds:
            for ch in getattr(g, "text_channels", []) or []:
                name = (getattr(ch, "name", "") or "").lower()
                if q in name:
                    matches.append(ch)
                    if len(matches) >= 25:
                        break
            if len(matches) >= 25:
                break

        if not matches:
            return await interaction.response.send_message("No matching channels found.", ephemeral=True)

        view = ChannelPickView(self._parent.author_id, matches, self._parent)
        await interaction.response.send_message("Pick a test channel:", view=view, ephemeral=True)

class ChannelPickSelect(discord.ui.Select):
    def __init__(self, matches: List[discord.TextChannel], parent: "BotInteropMenuView"):
        self._parent = parent
        opts: List[discord.SelectOption] = []
        for ch in matches[:25]:
            gname = (getattr(getattr(ch, "guild", None), "name", "") or "")[:40]
            label = f"{gname} #{ch.name}"[:100]
            opts.append(discord.SelectOption(label=label, value=str(ch.id)))
        super().__init__(placeholder="Select a channel…", min_values=1, max_values=1, options=opts)

    async def callback(self, interaction: discord.Interaction):
        ch_id = int(self.values[0]) if self.values and str(self.values[0]).isdigit() else 0
        ch = bot.get_channel(ch_id)
        if not isinstance(ch, discord.TextChannel):
            return await interaction.response.send_message("Channel not found.", ephemeral=True)

        # Switch the interop view to target the picked channel's guild and store the test channel mapping.
        _set_bot_interop_test_channel_id(ch.guild.id, ch.id)
        await STORE.mark_dirty()

        # Update the parent view's guild context so bot selection matches the server you picked.
        self._parent.guild_id = int(ch.guild.id)
        self._parent.selected_bot_id = 0
        await self._parent._refresh_select(ch.guild)

        await interaction.response.send_message(
            f"Test channel set to `{ch.guild.name}` #{ch.name} (id {ch.id}).",
            ephemeral=True,
        )

class ChannelPickView(BaseView):
    def __init__(self, author_id: int, matches: List[discord.TextChannel], parent: "BotInteropMenuView"):
        super().__init__(author_id, timeout=90)
        self.add_item(ChannelPickSelect(matches, parent))

class BotSelect(discord.ui.Select):
    def __init__(self, author_id: int, guild: discord.Guild):
        self._author_id = author_id
        bots = [m for m in (guild.members or []) if getattr(m, "bot", False)]
        bots = sorted(bots, key=lambda m: (m.display_name or m.name or "").lower())
        opts: List[discord.SelectOption] = []
        for m in bots[:25]:
            label = (m.display_name or m.name or str(m.id))[:100]
            opts.append(discord.SelectOption(label=label, value=str(m.id)))
        if not opts:
            opts = [discord.SelectOption(label="(no bots found in cache)", value="0", default=True)]
        super().__init__(placeholder="Select a bot…", min_values=1, max_values=1, options=opts)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, BotInteropMenuView):
            return
        picked = int(self.values[0]) if self.values and str(self.values[0]).isdigit() else 0
        view.selected_bot_id = picked
        await interaction.response.send_message(view.status_text(interaction.guild), ephemeral=True)

class BotInteropMenuView(BaseView):
    def __init__(self, author_id: int, guild_id: int):
        super().__init__(author_id, timeout=180)
        self.guild_id = int(guild_id)
        self.selected_bot_id: int = 0

    async def _require_god(self, interaction: discord.Interaction) -> bool:
        lvl = await effective_level(interaction.user)
        if lvl < 90 and not is_super(interaction.user.id):
            await interaction.response.send_message("GOD only.", ephemeral=True)
            return False
        return True

    def status_text(self, guild: Optional[discord.Guild]) -> str:
        # Prefer the selected guild context; the interaction guild can differ.
        g = bot.get_guild(self.guild_id) or guild
        gid = int(getattr(g, "id", self.guild_id) or self.guild_id)
        test_ch_id = _bot_interop_test_channel_id(gid)
        test_ch = g.get_channel(test_ch_id) if g and test_ch_id else None
        bot_id = int(self.selected_bot_id or 0)
        bot_name = ""
        if g and bot_id:
            m = g.get_member(bot_id)
            bot_name = f"{getattr(m, 'display_name', '')} ({bot_id})" if m else str(bot_id)
        lines = ["**Bot Interop (Manual, Visible Tests Only)**"]
        if g:
            lines.append(f"Server: {g.name} ({g.id})")
        lines.append(f"Test channel: {test_ch.mention if test_ch else ('not set' if not test_ch_id else str(test_ch_id))}")
        lines.append(f"Selected bot: {bot_name or 'not selected'}")
        if g:
            results = _bot_interop_results_for_guild(g.id)
            if bot_id and str(bot_id) in results:
                r = results[str(bot_id)]
                lines.append(f"Last test: responded={bool(r.get('responded'))} latency_s={r.get('latency_s','')}")
        lines.append("This cannot bypass bots that ignore other bots or require slash/human interaction.")
        return "\n".join(lines)

    @discord.ui.button(label="Set Test Channel (Here)", style=discord.ButtonStyle.primary)
    async def set_channel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Use this in a server text channel.", ephemeral=True)
        _set_bot_interop_test_channel_id(interaction.guild.id, interaction.channel.id)
        await STORE.mark_dirty()
        await interaction.response.send_message(f"Test channel set to {interaction.channel.mention}.", ephemeral=True)

    @discord.ui.button(label="Find Test Channel", style=discord.ButtonStyle.primary)
    async def find_channel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        await interaction.response.send_modal(ChannelSearchModal(self))

    @discord.ui.button(label="Send Test Command", style=discord.ButtonStyle.secondary)
    async def send_test_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        await interaction.response.send_modal(BotInteropCommandModal(self))

    @discord.ui.button(label="Macros", style=discord.ButtonStyle.secondary)
    async def macros_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        g = bot.get_guild(self.guild_id) or interaction.guild
        if not g:
            return await interaction.response.send_message("Guild not found.", ephemeral=True)
        bot_id = int(self.selected_bot_id or 0)
        if not bot_id:
            return await interaction.response.send_message("Select a bot first.", ephemeral=True)
        view = BotInteropMacrosView(interaction.user.id, guild_id=g.id, bot_id=bot_id)
        await view._ensure_select()
        await interaction.response.send_message(view.status_text(), view=view, ephemeral=True)

    @discord.ui.button(label="Server Diagnostics", style=discord.ButtonStyle.secondary)
    async def diag_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        g = bot.get_guild(self.guild_id) or interaction.guild
        if not g:
            return await interaction.response.send_message("Guild not found.", ephemeral=True)
        view = BotInteropDiagnosticsView(interaction.user.id, guild_id=g.id, page=0)
        await interaction.response.send_message(view.render(), view=view, ephemeral=True)

    @discord.ui.button(label="Show Status", style=discord.ButtonStyle.secondary)
    async def status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        await interaction.response.send_message(self.status_text(interaction.guild), ephemeral=True)

    async def on_timeout(self):
        return

    async def _ensure_select(self, interaction: discord.Interaction) -> None:
        if any(isinstance(c, BotSelect) for c in self.children):
            return
        g = interaction.guild or bot.get_guild(self.guild_id)
        if g:
            self.add_item(BotSelect(self.author_id, g))

    async def _refresh_select(self, guild: discord.Guild) -> None:
        # Rebuild the bot dropdown when switching target guilds.
        to_remove = [c for c in self.children if isinstance(c, BotSelect)]
        for c in to_remove:
            try:
                self.remove_item(c)
            except Exception:
                pass
        self.add_item(BotSelect(self.author_id, guild))

class MacroSelect(discord.ui.Select):
    def __init__(self, macros: List[Dict[str, str]]):
        opts: List[discord.SelectOption] = []
        for idx, m in enumerate(macros[:25]):
            label = str(m.get("name") or f"macro-{idx+1}")[:100]
            desc = str(m.get("text") or "")[:80]
            opts.append(discord.SelectOption(label=label, value=str(idx), description=desc or None))
        if not opts:
            opts = [discord.SelectOption(label="(no macros)", value="-1", default=True)]
        super().__init__(placeholder="Select a macro…", min_values=1, max_values=1, options=opts)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, BotInteropMacrosView):
            return
        picked = int(self.values[0]) if self.values and str(self.values[0]).lstrip("-").isdigit() else -1
        view.selected_macro_idx = picked
        await interaction.response.send_message(view.status_text(), ephemeral=True)

class BotInteropMacrosView(BaseView):
    def __init__(self, author_id: int, guild_id: int, bot_id: int):
        super().__init__(author_id, timeout=180)
        self.guild_id = int(guild_id)
        self.bot_id = int(bot_id)
        self.selected_macro_idx: int = -1

    async def _require_god(self, interaction: discord.Interaction) -> bool:
        lvl = await effective_level(interaction.user)
        if lvl < 90 and not is_super(interaction.user.id):
            await interaction.response.send_message("GOD only.", ephemeral=True)
            return False
        return True

    def _macros(self) -> List[Dict[str, str]]:
        return _bot_interop_macros_for_bot(self.guild_id, self.bot_id)

    def status_text(self) -> str:
        guild = bot.get_guild(self.guild_id)
        gname = getattr(guild, "name", str(self.guild_id))
        m = guild.get_member(self.bot_id) if guild else None
        bname = getattr(m, "display_name", None) or str(self.bot_id)
        macros = self._macros()
        lines = [f"**Macros for {bname}**", f"Server: {gname}", f"Saved macros: {len(macros)}"]
        if 0 <= self.selected_macro_idx < len(macros):
            sel = macros[self.selected_macro_idx]
            lines.append(f"Selected: {sel.get('name','')} -> `{sel.get('text','')}`")
        else:
            lines.append("Selected: none")
        return "\n".join(lines)

    async def _ensure_select(self) -> None:
        if any(isinstance(c, MacroSelect) for c in self.children):
            return
        self.add_item(MacroSelect(self._macros()))

    async def _refresh_select(self) -> None:
        to_remove = [c for c in self.children if isinstance(c, MacroSelect)]
        for c in to_remove:
            try:
                self.remove_item(c)
            except Exception:
                pass
        self.add_item(MacroSelect(self._macros()))

    @discord.ui.button(label="Add Macro", style=discord.ButtonStyle.primary)
    async def add_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        await interaction.response.send_modal(AddMacroModal(self))

    @discord.ui.button(label="Delete Macro", style=discord.ButtonStyle.danger)
    async def del_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        macros = self._macros()
        if not (0 <= self.selected_macro_idx < len(macros)):
            return await interaction.response.send_message("Select a macro first.", ephemeral=True)
        macros.pop(self.selected_macro_idx)
        _bot_interop_macros_for_guild(self.guild_id)[str(self.bot_id)] = macros
        self.selected_macro_idx = -1
        await STORE.mark_dirty()
        await self._refresh_select()
        await interaction.response.send_message("Macro deleted.", ephemeral=True)

    @discord.ui.button(label="Run Macro", style=discord.ButtonStyle.secondary)
    async def run_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        guild = bot.get_guild(self.guild_id)
        if not guild:
            return await interaction.response.send_message("Guild not found.", ephemeral=True)
        test_ch_id = _bot_interop_test_channel_id(guild.id)
        test_ch = guild.get_channel(test_ch_id) if test_ch_id else None
        if not isinstance(test_ch, discord.TextChannel):
            return await interaction.response.send_message("Test channel not set/found. Set it first.", ephemeral=True)
        macros = self._macros()
        if not (0 <= self.selected_macro_idx < len(macros)):
            return await interaction.response.send_message("Select a macro first.", ephemeral=True)
        cmd = str(macros[self.selected_macro_idx].get("text") or "").strip()
        if not cmd:
            return await interaction.response.send_message("Macro text is empty.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            sent = await test_ch.send(cmd)
        except Exception as exc:
            return await interaction.followup.send(f"Send failed: {exc}", ephemeral=True)

        def check(m: discord.Message) -> bool:
            if m.channel.id != test_ch.id:
                return False
            if m.author.id != self.bot_id:
                return False
            try:
                return m.created_at and sent.created_at and m.created_at >= sent.created_at
            except Exception:
                return True

        start_mon = time.monotonic()
        try:
            reply = await bot.wait_for("message", timeout=8.0, check=check)
        except Exception:
            reply = None
        latency = time.monotonic() - start_mon

        entry = {
            "tested_at": int(time.time()),
            "test_channel_id": int(test_ch.id),
            "sent": cmd[:250],
            "responded": bool(reply),
            "latency_s": round(latency, 3),
            "reply_preview": truncate((reply.content or "").strip(), 300) if reply else "",
        }
        _bot_interop_results_for_guild(guild.id)[str(self.bot_id)] = entry
        await STORE.mark_dirty()

        if reply:
            await interaction.followup.send(
                f"Observed response in {latency:.2f}s.\nPreview: `{entry['reply_preview']}`",
                ephemeral=True,
            )
        else:
            await interaction.followup.send("No response observed (waited 8s).", ephemeral=True)

class BotInteropDiagnosticsView(BaseView):
    def __init__(self, author_id: int, guild_id: int, page: int = 0):
        super().__init__(author_id, timeout=180)
        self.guild_id = int(guild_id)
        self.page = max(0, int(page))

    async def _require_god(self, interaction: discord.Interaction) -> bool:
        lvl = await effective_level(interaction.user)
        if lvl < 90 and not is_super(interaction.user.id):
            await interaction.response.send_message("GOD only.", ephemeral=True)
            return False
        return True

    def render(self) -> str:
        guild = bot.get_guild(self.guild_id)
        if not guild:
            return "Guild not found."
        test_ch_id = _bot_interop_test_channel_id(guild.id)
        test_ch = guild.get_channel(test_ch_id) if test_ch_id else None
        bots = [m for m in (guild.members or []) if getattr(m, "bot", False)]
        bots = sorted(bots, key=lambda m: (m.display_name or m.name or "").lower())
        page_size = 10
        start = self.page * page_size
        end = start + page_size
        chunk = bots[start:end]
        results = _bot_interop_results_for_guild(guild.id)
        total_pages = max(1, (len(bots) + page_size - 1) // page_size)
        if self.page >= total_pages:
            self.page = max(0, total_pages - 1)
            start = self.page * page_size
            end = start + page_size
            chunk = bots[start:end]
        lines = ["**Server Bot Diagnostics (No Probing)**"]
        lines.append(f"Server: {guild.name} ({guild.id})")
        lines.append(f"Test channel: {test_ch.mention if isinstance(test_ch, discord.TextChannel) else 'not set'}")
        lines.append(f"Bots: {len(bots)} | Page {self.page+1}/{total_pages}")
        for m in chunk:
            perms_txt = ""
            if isinstance(test_ch, discord.TextChannel):
                try:
                    p = test_ch.permissions_for(m)
                    perms_txt = f" lab:view={'Y' if p.view_channel else 'N'} send={'Y' if p.send_messages else 'N'}"
                except Exception:
                    perms_txt = ""
            r = results.get(str(m.id), {})
            observed = "unknown"
            if isinstance(r, dict) and "responded" in r:
                observed = "yes" if r.get("responded") else "no"
            lines.append(f"- {m.display_name} ({m.id}) responded={observed}{perms_txt}")
        lines.append("Use Bot Interop tests/macros to record observed responses per bot.")
        return "\n".join(lines)

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        self.page = max(0, self.page - 1)
        await interaction.response.edit_message(content=self.render(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        self.page = self.page + 1
        await interaction.response.edit_message(content=self.render(), view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        await interaction.response.edit_message(content=self.render(), view=self)

class ServerMenuView(BaseView):
    async def _require_god(self, interaction: discord.Interaction) -> bool:
        lvl = await effective_level(interaction.user)
        if lvl < 90 and not is_super(interaction.user.id):
            await interaction.response.send_message("GOD only.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Bot Interop", style=discord.ButtonStyle.primary)
    async def bot_interop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_god(interaction):
            return
        view = BotInteropMenuView(interaction.user.id, guild_id=getattr(interaction.guild, "id", 0))
        await view._ensure_select(interaction)
        await interaction.response.send_message(view.status_text(interaction.guild), view=view, ephemeral=True)

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

@bot.command(name="cancel")
async def cmd_cancel(ctx: commands.Context):
    if ctx.author.id != SUPER_USER_ID:
        await safe_delete(ctx.message)
        return
    await safe_delete(ctx.message)
    tasks_to_cancel: Set[asyncio.Task] = set()
    for bucket in state.ACTIVE_TASKS.values():
        tasks_to_cancel.update(bucket)
    tasks_to_cancel.update(SPECIAL_VOICE_LEAVE_TASKS.values())
    tasks_to_cancel.update(MOVIE_STAY_TASKS.values())
    tasks_to_cancel.update(state.LIVE_STATS_TASKS.values())

    cancelled = 0
    for task in tasks_to_cancel:
        if task and not task.done():
            task.cancel()
            cancelled += 1

    state.ACTIVE_TASKS.clear()
    SPECIAL_VOICE_LEAVE_TASKS.clear()
    MOVIE_STAY_TASKS.clear()
    state.LIVE_STATS_TASKS.clear()

    ai_cancelled = 0
    mandy = bot.get_cog("MandyAI")
    queue_tasks = getattr(mandy, "_queue_tasks", None) if mandy else None
    if isinstance(queue_tasks, dict):
        for task in list(queue_tasks.values()):
            if task and not task.done():
                task.cancel()
                ai_cancelled += 1
        queue_tasks.clear()
        cancelled += ai_cancelled

    loops = [
        config_reload,
        json_autosave,
        mirror_integrity_check,
        server_status_update,
        dm_bridge_archive,
        presence_controller,
        daily_reflection_loop,
        internal_monologue_loop,
        sentience_maintenance_loop,
        diagnostics_loop,
        manual_upload_loop,
        soc_access_sync_loop,
    ]
    stopped = 0
    for loop_task in loops:
        try:
            if loop_task.is_running():
                loop_task.stop()
                stopped += 1
        except Exception:
            continue
    await log_to(
        "system",
        "Cancel command invoked",
        subsystem="IMMUNE",
        severity="WARN",
        details={"tasks_cancelled": cancelled, "ai_tasks_cancelled": ai_cancelled, "loops_stopped": stopped},
    )
    msg = voice_line(cfg(), "confirm_cancel", count=cancelled)
    if stopped:
        msg += f" Loops stopped: {stopped}."
    await ctx.send(msg, delete_after=8)

@bot.command()
async def menu(ctx: commands.Context):
    if not await require_level_ctx(ctx, 10):
        return
    await safe_delete(ctx.message)
    view = UserMenuView(ctx.author.id)
    msg = await ctx.send("**Mandy Menu**", view=view)
    view.message = msg

@bot.command(name="owneronboard", aliases=["onboardme", "selfonboard"])
async def owneronboard(ctx: commands.Context):
    await safe_delete(ctx.message)
    view = OwnerOnboardingView(ctx.author.id)
    msg = await ctx.send(view.status_text(), view=view)
    view.message = msg

@bot.command()
async def godmenu(ctx: commands.Context):
    if not await require_level_ctx(ctx, 90):
        return
    await safe_delete(ctx.message)
    view = GodMenuView(ctx.author.id)
    msg = await ctx.send("**GOD MENU**", view=view)
    view.message = msg

@bot.command(name="menuonboarding", aliases=["onboardmenu", "socaccess", "soc_onboarding"])
async def menuonboarding(ctx: commands.Context):
    if not ctx.guild or ctx.guild.id != ADMIN_GUILD_ID:
        await safe_delete(ctx.message)
        return
    if not await require_level_ctx(ctx, MANDY_GOD_LEVEL):
        return
    await safe_delete(ctx.message)
    view = SocAccessPanelView(ctx.author.id)
    msg = await ctx.send(view.status_text(), view=view)
    view.message = msg

@bot.command(name="onboard")
async def onboard(ctx: commands.Context, user_id: int, scope: str = ""):
    if not ctx.guild or ctx.guild.id != ADMIN_GUILD_ID:
        await safe_delete(ctx.message)
        return
    if not await require_level_ctx(ctx, MANDY_GOD_LEVEL):
        return
    await safe_delete(ctx.message)
    raw = (scope or "").strip().lower()
    guild_ids: Set[int] = set()
    if raw and raw not in ("all", "*"):
        for part in re.split(r"[ ,]+", raw):
            if part.isdigit():
                guild_ids.add(int(part))
    msg = await soc_send_onboarding_dm(ctx.author.id, int(user_id), guild_ids if guild_ids else None)
    await ctx.send(msg, delete_after=10)

@bot.command(name="servermenu")
async def servermenu(ctx: commands.Context):
    # Dedicated server-only admin menu (separate from !godmenu) for per-server tools.
    if not await require_level_ctx(ctx, 90):
        return
    await safe_delete(ctx.message)
    view = ServerMenuView(ctx.author.id)
    msg = await ctx.send("**SERVER MENU**", view=view)
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
        "**Bio-Genesis**: Use `!setup_bio` for the Sentient Core rebuild + aggressive backfill.",
        "**Backfill-only**: Use `!backfill` to ingest mirrors/stats without layout changes.",
        "Backfill: legacy setup backfills only if `database.json.auto.backfill` is enabled.",
        "DM bridges, stats, watchers are preserved. Use `!dmclose` to archive DM bridges.",
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
    spawn_task(run_full_setup(ctx.guild, mode, actor_id=ctx.author.id), "setup")
    await audit(ctx.author.id, "Setup run", {"mode": mode})
    await safe_ctx_send(ctx, "Setup started. You'll get a DM when it's done.", delete_after=10)

@bot.command(name="setup_audit")
async def setup_audit(ctx: commands.Context):
    if not ctx.guild or ctx.guild.id != ADMIN_GUILD_ID:
        await safe_delete(ctx.message)
        return
    if not await require_level_ctx(ctx, 70):
        return
    await safe_delete(ctx.message)
    lines = await setup_audit_report()
    await ctx.send("\n".join(lines[:20]), delete_after=20)

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
    spawn_task(run_setup_bio(ctx.guild, actor_id=ctx.author.id), "setup")
    await audit(ctx.author.id, "Setup BIO run", {})
    await safe_ctx_send(ctx, "BIO-GENESIS started. You'll get a DM when it's done.", delete_after=10)

@bot.command(name="backfill")
async def backfill(ctx: commands.Context, mode: str = ""):
    if not ctx.guild or ctx.guild.id != ADMIN_GUILD_ID:
        await safe_delete(ctx.message)
        return
    if not await require_level_ctx(ctx, 70):
        return
    await safe_delete(ctx.message)
    token = (mode or "").strip().lower()
    force = token in ("force", "f", "true", "1", "yes", "y")
    spawn_task(run_backfill_only(actor_id=ctx.author.id, force_backfill=force), "setup")
    await audit(ctx.author.id, "Backfill only", {"force": force})
    suffix = " (force)" if force else ""
    await safe_ctx_send(ctx, f"Backfill-only started{suffix}. You'll get a DM when it's done.", delete_after=10)

@bot.command(name="dedup", aliases=["dedupe", "dedupe_channels"])
async def dedup(ctx: commands.Context, *args: str):
    if not ctx.guild or ctx.guild.id != ADMIN_GUILD_ID:
        await safe_delete(ctx.message)
        return
    if not await require_level_ctx(ctx, 70):
        return
    await safe_delete(ctx.message)
    tokens = {str(t).lower() for t in args if t}
    action = "list"
    if "rename" in tokens:
        action = "rename"
    elif "delete" in tokens or "remove" in tokens:
        action = "delete"
    scope = "all"
    if {"cats", "cat", "categories"} & tokens:
        scope = "categories"
    elif {"channels", "channel", "ch", "text"} & tokens:
        scope = "channels"
    confirm = bool({"confirm", "force", "yes", "y"} & tokens)
    if action in ("rename", "delete") and not is_super(ctx.author.id):
        return await ctx.send("SUPERUSER only.", delete_after=6)

    async with _setup_guild_lock(ctx.guild.id):
        cat_dups = _dedup_category_groups(ctx.guild) if scope in ("all", "categories") else {}
        ch_dups = _dedup_channel_groups(ctx.guild) if scope in ("all", "channels") else {}

        if action == "list":
            lines: List[str] = []
            if scope in ("all", "categories"):
                if not cat_dups:
                    lines.append("Category duplicates: none")
                else:
                    for name, cats in cat_dups.items():
                        keep = cats[0].id
                        lines.append(f"Category dup: {name} ({len(cats)}) keep={keep}")
            if scope in ("all", "channels"):
                if not ch_dups:
                    lines.append("Channel duplicates: none")
                else:
                    for (cat_id, name), chans in ch_dups.items():
                        cat_name = "UNCATEGORIZED"
                        if cat_id:
                            cat = ctx.guild.get_channel(cat_id)
                            if isinstance(cat, discord.CategoryChannel):
                                cat_name = cat.name
                        keep = chans[0].id
                        lines.append(f"Channel dup: #{name} in {cat_name} ({len(chans)}) keep={keep}")
            for chunk in chunk_lines(lines, "**Dedup Report**", limit=1900):
                await ctx.send(chunk, delete_after=20)
            return

        if action == "delete" and not confirm:
            return await ctx.send("Add `confirm` to delete duplicates (e.g. `!dedup delete confirm`).", delete_after=8)

        renamed = 0
        deleted = 0
        skipped = 0

        if scope in ("all", "channels"):
            for (cat_id, name), chans in ch_dups.items():
                for ch in chans[1:]:
                    try:
                        if action == "rename":
                            new_name = _dedup_channel_name(name, ch.id)
                            if ch.name != new_name:
                                await ch.edit(name=new_name)
                                renamed += 1
                                await setup_pause()
                        else:
                            await ch.delete()
                            deleted += 1
                            await setup_pause()
                    except Exception:
                        skipped += 1

        if scope in ("all", "categories"):
            for name, cats in cat_dups.items():
                for cat in cats[1:]:
                    try:
                        if action == "rename":
                            new_name = _dedup_category_name(name, cat.id)
                            if cat.name != new_name:
                                await cat.edit(name=new_name)
                                renamed += 1
                                await setup_pause()
                        else:
                            if cat.channels and not confirm:
                                skipped += 1
                                continue
                            await cat.delete()
                            deleted += 1
                            await setup_pause()
                    except Exception:
                        skipped += 1

        msg = f"Dedup {action} complete. renamed={renamed} deleted={deleted} skipped={skipped}"
        await ctx.send(msg, delete_after=12)

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

def _resolve_guilds_from_ref(ctx: commands.Context, ref: str) -> Tuple[List[discord.Guild], str]:
    token = (ref or "").strip().lower()
    if not token or token in ("here", "this", "current"):
        if not ctx.guild:
            return [], "No guild context."
        return [ctx.guild], ""
    if token == "all":
        return list(bot.guilds), ""
    if token.isdigit():
        gid = int(token)
        g = bot.get_guild(gid)
        if g:
            return [g], ""
        return [], "Guild not found."
    return [], "Use: `here`, `all`, or a guild ID."

async def _update_roast_guild_list(ctx: commands.Context, key: str, ref: str, mode: str) -> None:
    mode = (mode or "add").strip().lower()
    if mode not in ("add", "remove", "clear"):
        await ctx.send("Mode must be `add`, `remove`, or `clear`.", delete_after=6)
        return
    roast = roast_cfg()
    if mode == "clear":
        roast[key] = []
        await STORE.mark_dirty()
        await audit(ctx.author.id, f"Roast {key} cleared", {})
        await ctx.send(f"{key} cleared.", delete_after=6)
        return
    guilds, err = _resolve_guilds_from_ref(ctx, ref)
    if err:
        await ctx.send(err, delete_after=6)
        return
    ids = {g.id for g in guilds}
    current = {int(x) for x in (roast.get(key, []) or []) if str(x).isdigit()}
    if mode == "add":
        current.update(ids)
    else:
        current.difference_update(ids)
    roast[key] = sorted(current)
    await STORE.mark_dirty()
    await audit(ctx.author.id, f"Roast {key} updated", {"mode": mode, "guild_ids": sorted(ids)})
    names = ", ".join(g.name for g in guilds) if guilds else "none"
    await ctx.send(f"{key} {mode} for: {names}", delete_after=8)

@bot.command(name="roast_whitelist_guild", aliases=["roast_whitelist_server", "roast_allow_guild", "roast_allow_server"])
async def roast_whitelist_guild(ctx: commands.Context, ref: str = "here", mode: str = "add"):
    if not await require_level_ctx(ctx, MANDY_GOD_LEVEL):
        return
    if ref in ("add", "remove", "clear") and mode == "add":
        mode = ref
        ref = "here"
    await _update_roast_guild_list(ctx, "allowed_guilds", ref, mode)

@bot.command(name="roast_whitelist_users", aliases=["roast_auto_opt_in", "roast_allow_users"])
async def roast_whitelist_users(ctx: commands.Context, ref: str = "here", mode: str = "add"):
    if not await require_level_ctx(ctx, MANDY_GOD_LEVEL):
        return
    if ref in ("add", "remove", "clear") and mode == "add":
        mode = ref
        ref = "here"
    await _update_roast_guild_list(ctx, "auto_opt_in_guilds", ref, mode)


def _generate_phoenix_key(snapshot_id: str) -> str:
    code = f"CONFIRM-{snapshot_id}-{secrets.token_hex(2).upper()}"
    phoenix_keys()[snapshot_id] = code
    return code


def _snapshot_id(guild: discord.Guild) -> str:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
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
    if state.POOL:
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
async def dmai(ctx: commands.Context, mode: str = "", target: str = ""):
    if not ctx.guild or ctx.guild.id != ADMIN_GUILD_ID:
        await safe_delete(ctx.message)
        return
    if not await require_level_ctx(ctx, MANDY_GOD_LEVEL):
        return
    await safe_delete(ctx.message)

    known = {"on", "off", "status", "list", "enable", "disable", "start", "stop"}
    mode = (mode or "").lower().strip()
    if mode and mode not in known:
        target = mode
        mode = "status"
    if not mode:
        mode = "list"

    async def resolve_target(token: str) -> Tuple[Optional[int], str]:
        token = (token or "").strip()
        if token in ("this", "here"):
            if not isinstance(ctx.channel, discord.TextChannel):
                return None, "No channel context."
            uid = await dm_bridge_user_for_channel(ctx.channel.id)
            if not uid:
                return None, "Channel is not a DM bridge."
            return int(uid), ""
        ch_id = parse_channel_id(token)
        if ch_id:
            uid = await dm_bridge_user_for_channel(ch_id)
            if not uid:
                return None, "Channel is not a DM bridge."
            return int(uid), ""
        uid = parse_user_id(token)
        if uid:
            return int(uid), ""
        return None, "Target must be a user ID/mention, DM bridge channel, or `this`."

    if mode in ("on", "enable", "start"):
        if not target:
            return await ctx.send("Use: `!dmai on <user_id|@user|#channel|this>`", delete_after=6)
        uid, err = await resolve_target(target)
        if err:
            return await ctx.send(err, delete_after=6)
        ch_id = await ensure_dm_bridge_active(uid, reason="ai")
        if not ch_id:
            return await ctx.send("Could not open DM bridge.", delete_after=6)
        await dm_ai_enable(uid, ctx.author.id, ch_id)
        return await ctx.send(f"DM AI enabled for <@{uid}>.", delete_after=6)

    if mode in ("off", "disable", "stop"):
        if not target:
            return await ctx.send("Use: `!dmai off <user_id|@user|#channel|this>`", delete_after=6)
        uid, err = await resolve_target(target)
        if err:
            return await ctx.send(err, delete_after=6)
        await dm_ai_disable(uid, reason="manual", actor_id=ctx.author.id)
        return await ctx.send(f"DM AI disabled for <@{uid}>.", delete_after=6)

    if mode == "status" and target:
        uid, err = await resolve_target(target)
        if err:
            return await ctx.send(err, delete_after=6)
        enabled = await dm_ai_is_enabled(uid)
        state = dm_ai_state().get(str(uid), {})
        enabled_at = fmt_ts(int(state.get("enabled_at", 0)))
        ch_id = int(state.get("bridge_channel_id", 0))
        ch_text = f"<#{ch_id}>" if ch_id else "n/a"
        status = "active" if enabled else "inactive"
        return await ctx.send(
            f"DM AI {status} for <@{uid}> | bridge={ch_text} | enabled_at={enabled_at}",
            delete_after=10,
        )

    if mode == "list":
        entries = list(dm_ai_state().items())
        if not entries:
            return await ctx.send("No DM AI sessions.", delete_after=6)
        lines: List[str] = []
        for uid_str, entry in entries:
            try:
                uid = int(uid_str)
            except Exception:
                continue
            enabled = await dm_ai_is_enabled(uid)
            if not enabled:
                continue
            ch_id = int(entry.get("bridge_channel_id", 0))
            enabled_at = fmt_ts(int(entry.get("enabled_at", 0)))
            ch_text = f"<#{ch_id}>" if ch_id else "n/a"
            lines.append(f"{uid} (<@{uid}>) | bridge={ch_text} | enabled_at={enabled_at}")
        if not lines:
            return await ctx.send("No active DM AI sessions.", delete_after=6)
        return await ctx.send("DM AI sessions:\n" + "\n".join(lines[:25]))

    return await ctx.send("Use: `!dmai on|off|status|list <target>`", delete_after=6)

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
    if not chat_stats_enabled():
        return await ctx.send("Chat stats are disabled.", delete_after=8)
    await safe_delete(ctx.message)
    window = normalize_stats_window(window, "daily")
    now_dt = datetime.datetime.now(datetime.timezone.utc)
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
    if not chat_stats_enabled():
        return await ctx.send("Chat stats are disabled.", delete_after=8)
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
    if not chat_stats_enabled():
        return await ctx.send("Chat stats are disabled.", delete_after=8)
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
    state.LIVE_STATS_TASKS[guild_id] = spawn_task(
        live_stats_loop(guild_id, msg.channel.id, msg.id, window),
        "stats",
    )

@bot.command(name="globalstats")
async def cmd_globalstats(ctx: commands.Context, window: str = None):
    if not chat_stats_enabled():
        return await ctx.send("Chat stats are disabled.", delete_after=8)
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
    state.LIVE_STATS_TASKS["GLOBAL"] = spawn_task(
        global_live_stats_loop(msg.channel.id, msg.id, window),
        "stats",
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
    install_discord_governor(bot)
    install_typing_delay_patch()
    await maybe_load_mandy_extension(bot)
    try:
        await run_boot_orchestrator()
    except Exception as e:
        await debug(f"setup orchestrator failed: {e}")

@bot.before_invoke
async def _before_command_invoke(ctx: commands.Context):
    try:
        ctx._typing_delay_token = COMMAND_CONTEXT.set(True)
        ctx._governor_token = set_governor_context(essential=True, priority=80)
    except Exception:
        ctx._typing_delay_token = None
        ctx._governor_token = None

@bot.after_invoke
async def _after_command_invoke(ctx: commands.Context):
    try:
        token = getattr(ctx, "_typing_delay_token", None)
        if token is not None:
            COMMAND_CONTEXT.reset(token)
    except Exception:
        pass
    try:
        token = getattr(ctx, "_governor_token", None)
        if token is not None:
            reset_governor_context(token)
    except Exception:
        pass

@bot.event
async def on_guild_join(guild: discord.Guild):
    if guild.id == ADMIN_GUILD_ID:
        return
    if auto_setup_enabled():
        spawn_task(
            auto_setup_guild(guild, do_backfill=auto_backfill_enabled(), force_backfill=False),
            "setup",
        )
        if auto_backfill_enabled():
            spawn_task(backfill_chat_stats_for_guild(guild), "stats")
        spawn_task(send_owner_server_report(guild, reason="guild join (auto setup pending)"), "reports")
        spawn_task(maybe_complete_owner_onboarding(guild), "owner_onboarding")
        return
    try:
        await ensure_admin_server_channels(guild)
        await ensure_server_mirror_rule(guild)
        await update_server_info_for_guild(guild)
    except Exception:
        pass
    spawn_task(send_owner_server_report(guild, reason="guild join"), "reports")
    if auto_backfill_enabled():
        spawn_task(backfill_chat_stats_for_guild(guild), "stats")
    spawn_task(maybe_complete_owner_onboarding(guild), "owner_onboarding")

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
    rule = rules[state.INTEGRITY_CURSOR % len(rules)]
    state.INTEGRITY_CURSOR += 1

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
    if not rule.get("enabled", True):
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
    now_dt = datetime.datetime.now(datetime.timezone.utc)
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
    lines = [
        f"Diagnostics Snapshot (UTC {datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')})"
    ]
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

_soc_access_last_sync_ts = 0

@tasks.loop(minutes=1)
async def soc_access_sync_loop():
    global _soc_access_last_sync_ts
    admin = bot.get_guild(ADMIN_GUILD_ID)
    if not admin:
        return
    access = soc_access_cfg()
    interval_minutes = int(access.get("sync_interval_minutes", 30) or 30)
    interval_minutes = max(1, interval_minutes)
    now = now_ts()
    if _soc_access_last_sync_ts and (now - _soc_access_last_sync_ts) < (interval_minutes * 60):
        return
    _soc_access_last_sync_ts = now
    try:
        await soc_apply_core_permissions(admin)
        await soc_apply_admin_server_permissions()
    except Exception:
        pass
    for member in list(getattr(admin, "members", []) or []):
        if not isinstance(member, discord.Member):
            continue
        try:
            await soc_sync_member_access(member)
        except Exception:
            continue

@soc_access_sync_loop.before_loop
async def _soc_access_sync_before_loop():
    try:
        delay = int(soc_access_cfg().get("initial_delay_seconds", 60) or 60)
    except Exception:
        delay = 60
    delay = max(0, min(3600, delay))
    if delay:
        await asyncio.sleep(delay)

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
    user_channel = channels_cfg.get("user", "requests")
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
        ai_enabled = False
        try:
            ch_id = await dm_bridge_channel_for_user(message.author.id)
            if not ch_id:
                ch_id = await ensure_dm_bridge_active(message.author.id, reason="auto")
            if ch_id:
                ch = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
                if isinstance(ch, discord.TextChannel):
                    await ch.send(_dm_bridge_format_line(message.author.name, message.content or "", message.attachments))
                    await dm_bridge_touch(message.author.id)
            ai_enabled = await dm_ai_is_enabled(message.author.id)
        except Exception:
            pass
        if ai_enabled and (message.content or message.attachments):
            try:
                await log_to(
                    "ai",
                    "DM AI user message",
                    subsystem="AI",
                    severity="INFO",
                    details={
                        "user_id": message.author.id,
                        "message_id": message.id,
                        "text": truncate(message.content or "", 500),
                    },
                )
            except Exception:
                pass
            mandy = bot.get_cog("MandyAI")
            if not mandy:
                try:
                    await maybe_load_mandy_extension(bot)
                except Exception:
                    mandy = None
                mandy = bot.get_cog("MandyAI")
            if mandy:
                try:
                    await mandy._process_request(
                        message.author,
                        message.channel,
                        None,
                        message.id,
                        message.content or "",
                    )
                except Exception as e:
                    await debug(f"mandy dm ai error: {e}")
        return

    # Prefix command enforcement
    try:
        if await enforce_command_channels(message):
            return
    except Exception as e:
        await debug(f"command channel enforcement error: {e}")

    try:
        record_roast_history(message)
    except Exception:
        pass

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

    # Playful roast (opt-in only)
    try:
        if await maybe_roast_message(message):
            return
    except Exception as e:
        await debug(f"roast error: {e}")

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
                        if isinstance(message.channel, discord.TextChannel):
                            await message.channel.send(
                                _dm_bridge_format_line(message.author.display_name, message.content or "", message.attachments)
                            )
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
attach_mandy_context(
    bot,
    dm_ai_is_enabled=dm_ai_is_enabled,
    dm_bridge_user_for_channel=dm_bridge_user_for_channel,
    mandy_power_mode_enabled=mandy_power_mode_enabled,
    effective_level=effective_level,
    require_level_ctx=require_level_ctx,
)
bot.run(DISCORD_TOKEN)
