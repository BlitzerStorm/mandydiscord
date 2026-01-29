import asyncio
from typing import Dict, Optional, Set

import aiomysql
from discord.ext import commands

from mandy.resolver import GuildIndexCache

from .store import MENTION_COOLDOWN, STORE

bot: Optional[commands.Bot] = None
POOL: Optional[aiomysql.Pool] = None

INTEGRITY_CURSOR = 0
GLOBAL_USER_RESOLVER = GuildIndexCache(ttl_seconds=120)
AUTO_SETUP_LOCK = asyncio.Lock()
TYPING_RATE_SECONDS = 6.0
TYPING_INDICATORS: Dict[int, float] = {}
BRIDGE_TYPING_INDICATORS: Dict[int, float] = {}
LIVE_STATS_TASKS: Dict[int, asyncio.Task] = {}
ACTIVE_TASKS: Dict[str, Set[asyncio.Task]] = {}

MANDY_EXTENSION = "cogs.mandy_ai"
MANDY_LOADED = False

API_GOVERNOR = None

SETUP_ADAPTIVE_ACTIVE = False
SETUP_DELAY_OVERRIDE: Optional[float] = None
SETUP_DELAY_MIN = 0.4
SETUP_DELAY_MAX = 4.0
SETUP_DELAY_STEP = 0.05

DISCORD_SEND_DELAY_OVERRIDE: Optional[float] = None
DISCORD_SEND_DELAY_MIN = 0.0
DISCORD_SEND_DELAY_MAX = 6.0
DISCORD_SEND_DELAY_STEP = 0.1
