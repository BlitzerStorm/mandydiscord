import asyncio
import random
import time
from collections import deque
from typing import Optional, Dict, Any, Deque, List

import discord
from discord.ext import commands

TYPING_INTERVAL_RANGE = (3 * 60, 10 * 60)
PRESENCE_INTERVAL_RANGE = (5 * 60, 15 * 60)
TYPING_DURATION_RANGE = (2, 20)
ACTIVITY_WINDOW_SECONDS = 60

TYPING_CHANNEL_LIMIT_WINDOW = 10 * 60
TYPING_CHANNEL_LIMIT_COUNT = 50
PRESENCE_LIMIT_WINDOW = 3 * 60
PRESENCE_LIMIT_COUNT = 3
ERROR_LIMIT_WINDOW = 5 * 60
ERROR_LIMIT_COUNT = 3

_AMBIENT_BOT: Optional[commands.Bot] = None
_TYPING_TASK: Optional[asyncio.Task] = None
_PRESENCE_TASK: Optional[asyncio.Task] = None
_LISTENERS_ATTACHED = False

_RECENT_ACTIVITY: Dict[int, float] = {}
_TYPING_HISTORY: Dict[int, Deque[float]] = {}
_PRESENCE_HISTORY: Deque[float] = deque()
_ERROR_HISTORY: Deque[float] = deque()

_NEXT_TYPING_AT: Optional[float] = None
_NEXT_PRESENCE_AT: Optional[float] = None
_PRESENCE_CYCLE: List[str] = []


def _now() -> float:
    return time.time()


def _get_cfg() -> Dict[str, Any]:
    bot = _AMBIENT_BOT
    if not bot:
        return {}
    getter = getattr(bot, "mandy_cfg", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return {}
    return {}


def _get_store():
    bot = _AMBIENT_BOT
    if not bot:
        return None
    return getattr(bot, "mandy_store", None)


def _ambient_cfg() -> Dict[str, Any]:
    cfg = _get_cfg()
    ambient = cfg.setdefault("ambient_engine", {})
    ambient.setdefault("enabled", True)
    ambient.setdefault("last_typing", 0)
    ambient.setdefault("last_presence", 0)
    return ambient


def _is_enabled() -> bool:
    return bool(_ambient_cfg().get("enabled", False))


async def _mark_dirty() -> None:
    store = _get_store()
    if not store:
        return
    try:
        await store.mark_dirty()
    except Exception:
        pass


def _prune(deq: Deque[float], window_seconds: int, now: float) -> None:
    while deq and now - deq[0] > window_seconds:
        deq.popleft()


def _record_activity(channel_id: int) -> None:
    _RECENT_ACTIVITY[channel_id] = _now()


async def _on_message(message: discord.Message) -> None:
    try:
        if message.author.bot or message.webhook_id:
            return
        if isinstance(message.channel, discord.TextChannel):
            _record_activity(message.channel.id)
    except Exception:
        return


async def _on_typing(channel: discord.abc.Messageable, user: discord.User, when) -> None:
    try:
        if user.bot:
            return
        if isinstance(channel, discord.TextChannel):
            _record_activity(channel.id)
    except Exception:
        return


def _attach_listeners(bot: commands.Bot) -> None:
    global _LISTENERS_ATTACHED
    if _LISTENERS_ATTACHED:
        return
    bot.add_listener(_on_message, "on_message")
    bot.add_listener(_on_typing, "on_typing")
    _LISTENERS_ATTACHED = True


def _is_public_channel(channel: discord.TextChannel) -> bool:
    if not channel.guild:
        return False
    try:
        default_role = channel.guild.default_role
        if not channel.permissions_for(default_role).view_channel:
            return False
        me = channel.guild.me
        if not me:
            return False
        perms = channel.permissions_for(me)
        return perms.view_channel and perms.send_messages
    except Exception:
        return False


def _pick_active_channel(now: float) -> Optional[discord.TextChannel]:
    for cid, ts in list(_RECENT_ACTIVITY.items()):
        if now - ts > ACTIVITY_WINDOW_SECONDS:
            _RECENT_ACTIVITY.pop(cid, None)
    candidates: List[discord.TextChannel] = []
    bot = _AMBIENT_BOT
    if not bot:
        return None
    for cid, ts in _RECENT_ACTIVITY.items():
        if now - ts > ACTIVITY_WINDOW_SECONDS:
            continue
        ch = bot.get_channel(cid)
        if not isinstance(ch, discord.TextChannel):
            continue
        if not _is_public_channel(ch):
            continue
        if not _can_type_in_channel(cid, now):
            continue
        candidates.append(ch)
    if not candidates:
        return None
    return random.choice(candidates)


def _can_type_in_channel(channel_id: int, now: float) -> bool:
    hist = _TYPING_HISTORY.setdefault(channel_id, deque())
    _prune(hist, TYPING_CHANNEL_LIMIT_WINDOW, now)
    return len(hist) < TYPING_CHANNEL_LIMIT_COUNT


def _record_typing(channel_id: int, now: float) -> None:
    hist = _TYPING_HISTORY.setdefault(channel_id, deque())
    _prune(hist, TYPING_CHANNEL_LIMIT_WINDOW, now)
    hist.append(now)


def _can_change_presence(now: float) -> bool:
    _prune(_PRESENCE_HISTORY, PRESENCE_LIMIT_WINDOW, now)
    return len(_PRESENCE_HISTORY) < PRESENCE_LIMIT_COUNT


def _record_presence_change(now: float) -> None:
    _prune(_PRESENCE_HISTORY, PRESENCE_LIMIT_WINDOW, now)
    _PRESENCE_HISTORY.append(now)


def _current_presence_state() -> str:
    bot = _AMBIENT_BOT
    if not bot:
        return "online"
    st = getattr(bot, "status", discord.Status.online)
    if isinstance(st, discord.Status):
        name = st.name
    else:
        name = str(st)
    if name not in ("online", "idle", "dnd"):
        return "online"
    return name


def _next_presence_state(current: str) -> str:
    global _PRESENCE_CYCLE
    if not _PRESENCE_CYCLE:
        _PRESENCE_CYCLE = ["online", "idle", "dnd"]
        random.shuffle(_PRESENCE_CYCLE)
    for _ in range(len(_PRESENCE_CYCLE)):
        nxt = _PRESENCE_CYCLE.pop(0)
        if nxt != current:
            return nxt
        _PRESENCE_CYCLE.append(nxt)
    for fallback in ("online", "idle", "dnd"):
        if fallback != current:
            return fallback
    return "online"


def _log_console(text: str) -> None:
    print(text)


async def _send_embed(action: str, channel: Optional[discord.TextChannel]) -> None:
    bot = _AMBIENT_BOT
    if not bot:
        return
    cfg = _get_cfg()
    logs = cfg.get("logs", {}) if isinstance(cfg.get("logs", {}), dict) else {}
    target_id = logs.get("audit") or logs.get("debug")
    if not target_id:
        return
    try:
        target_id = int(target_id)
    except Exception:
        return
    ch = bot.get_channel(target_id)
    if not ch:
        try:
            ch = await bot.fetch_channel(target_id)
        except Exception:
            return
    try:
        channel_text = "n/a"
        if channel:
            name = getattr(channel, "name", "")
            if name:
                channel_text = f"{name} ({channel.id})"
            else:
                channel_text = str(channel.id)
        emb = discord.Embed(title="Ambient Engine Activity", color=discord.Color.dark_gray())
        emb.add_field(name="Action", value=action, inline=False)
        emb.add_field(name="Channel", value=channel_text, inline=False)
        emb.add_field(name="Timestamp", value=f"<t:{int(_now())}:F>", inline=False)
        await ch.send(embed=emb)
    except Exception:
        return


async def _log_typing_start(channel: discord.TextChannel) -> None:
    _log_console(f"[AMBIENT] typing_start channel_id={channel.id} reason=random_presence")
    await _send_embed("typing_start reason=random_presence", channel)


async def _log_typing_stop(channel: discord.TextChannel) -> None:
    _log_console(f"[AMBIENT] typing_stop channel_id={channel.id}")
    await _send_embed("typing_stop", channel)


async def _log_presence_change(prev_state: str, next_state: str) -> None:
    _log_console(f"[AMBIENT] presence_change from={prev_state} to={next_state}")
    await _send_embed(f"presence_change from={prev_state} to={next_state}", None)


async def _log_auto_disable(reason: str) -> None:
    _log_console(f"[AMBIENT] auto_disable reason={reason}")
    await _send_embed(f"auto_disable reason={reason}", None)


async def _record_error(action: str, exc: Exception) -> None:
    now = _now()
    _ERROR_HISTORY.append(now)
    _prune(_ERROR_HISTORY, ERROR_LIMIT_WINDOW, now)
    _log_console(f"[AMBIENT] error action={action} error={type(exc).__name__}")
    if len(_ERROR_HISTORY) >= ERROR_LIMIT_COUNT:
        await _auto_disable("api_errors")


async def _auto_disable(reason: str) -> None:
    ambient = _ambient_cfg()
    if not ambient.get("enabled", False):
        return
    ambient["enabled"] = False
    await _mark_dirty()
    await _log_auto_disable(reason)
    await stop_ambient_engine()


async def _perform_typing() -> None:
    now = _now()
    channel = _pick_active_channel(now)
    if not channel:
        return
    if not _can_type_in_channel(channel.id, now):
        return
    duration = random.randint(TYPING_DURATION_RANGE[0], TYPING_DURATION_RANGE[1])
    started = False
    try:
        async with channel.typing():
            started = True
            _record_typing(channel.id, now)
            ambient = _ambient_cfg()
            ambient["last_typing"] = int(now)
            await _mark_dirty()
            await _log_typing_start(channel)
            await asyncio.sleep(duration)
    except asyncio.CancelledError:
        if started:
            await _log_typing_stop(channel)
        raise
    except Exception as e:
        await _record_error("typing", e)
        return
    if started:
        await _log_typing_stop(channel)


async def _perform_presence_change() -> None:
    now = _now()
    if not _can_change_presence(now):
        return
    bot = _AMBIENT_BOT
    if not bot:
        return
    prev_state = _current_presence_state()
    next_state = _next_presence_state(prev_state)
    if next_state == prev_state:
        return
    status_map = {
        "online": discord.Status.online,
        "idle": discord.Status.idle,
        "dnd": discord.Status.dnd
    }
    try:
        await bot.change_presence(status=status_map.get(next_state, discord.Status.online), activity=bot.activity)
    except Exception as e:
        await _record_error("presence", e)
        return
    _record_presence_change(now)
    ambient = _ambient_cfg()
    ambient["last_presence"] = int(now)
    await _mark_dirty()
    await _log_presence_change(prev_state, next_state)


async def _typing_loop() -> None:
    global _NEXT_TYPING_AT
    try:
        while True:
            if not _is_enabled():
                return
            delay = random.uniform(TYPING_INTERVAL_RANGE[0], TYPING_INTERVAL_RANGE[1])
            _NEXT_TYPING_AT = _now() + delay
            await asyncio.sleep(delay)
            if not _is_enabled():
                continue
            await _perform_typing()
    except asyncio.CancelledError:
        return
    finally:
        _NEXT_TYPING_AT = None


async def _presence_loop() -> None:
    global _NEXT_PRESENCE_AT
    try:
        while True:
            if not _is_enabled():
                return
            delay = random.uniform(PRESENCE_INTERVAL_RANGE[0], PRESENCE_INTERVAL_RANGE[1])
            _NEXT_PRESENCE_AT = _now() + delay
            await asyncio.sleep(delay)
            if not _is_enabled():
                continue
            await _perform_presence_change()
    except asyncio.CancelledError:
        return
    finally:
        _NEXT_PRESENCE_AT = None


async def start_ambient_engine(bot: commands.Bot) -> None:
    global _AMBIENT_BOT, _TYPING_TASK, _PRESENCE_TASK
    _AMBIENT_BOT = bot
    _attach_listeners(bot)
    _ambient_cfg()
    if not _is_enabled():
        return
    if not _TYPING_TASK or _TYPING_TASK.done():
        _TYPING_TASK = asyncio.create_task(_typing_loop())
    if not _PRESENCE_TASK or _PRESENCE_TASK.done():
        _PRESENCE_TASK = asyncio.create_task(_presence_loop())


async def stop_ambient_engine() -> None:
    global _TYPING_TASK, _PRESENCE_TASK, _NEXT_TYPING_AT, _NEXT_PRESENCE_AT
    ambient = _ambient_cfg()
    ambient["enabled"] = False
    await _mark_dirty()
    current = asyncio.current_task()
    for task in (_TYPING_TASK, _PRESENCE_TASK):
        if task and not task.done() and task is not current:
            task.cancel()
    _TYPING_TASK = None
    _PRESENCE_TASK = None
    _NEXT_TYPING_AT = None
    _NEXT_PRESENCE_AT = None
    bot = _AMBIENT_BOT
    if not bot:
        return
    try:
        await bot.change_presence(status=discord.Status.online, activity=bot.activity)
    except Exception:
        return


def ambient_status() -> Dict[str, Any]:
    ambient = _ambient_cfg() if _AMBIENT_BOT else {"enabled": False}
    now = _now()
    next_typing = _NEXT_TYPING_AT if _NEXT_TYPING_AT and _NEXT_TYPING_AT > now else None
    next_presence = _NEXT_PRESENCE_AT if _NEXT_PRESENCE_AT and _NEXT_PRESENCE_AT > now else None
    candidates: List[tuple] = []
    if next_typing:
        candidates.append(("typing", next_typing))
    if next_presence:
        candidates.append(("presence", next_presence))
    next_event = min(candidates, key=lambda item: item[1]) if candidates else None
    return {
        "enabled": bool(ambient.get("enabled", False)),
        "next_typing_at": int(next_typing) if next_typing else None,
        "next_presence_at": int(next_presence) if next_presence else None,
        "next_event_at": int(next_event[1]) if next_event else None,
        "next_event_type": next_event[0] if next_event else None
    }
