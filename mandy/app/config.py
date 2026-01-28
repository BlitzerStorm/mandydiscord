import os
from typing import Any, Dict

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
    "Can't stop smiling about that VC; you feel the same?",
]

MOVIE_PROMPT_TIMEOUT_SECONDS = 60
MOVIE_STAY_DEFAULT_MINUTES = 15
MOVIE_STAY_MAX_MINUTES = 30
MOVIE_QUEUE_LIMIT = 25

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
    QUARANTINE_ROLE_NAME: 1,
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
MYSQL_DB = SECRETS.get("MYSQL_DB") or os.getenv("MYSQL_DB")
MYSQL_USER = SECRETS.get("MYSQL_USER") or os.getenv("MYSQL_USER")
MYSQL_PASS = SECRETS.get("MYSQL_PASS") or os.getenv("MYSQL_PASS")
GEMINI_API_KEY = SECRETS.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")
AGENT_ROUTER_TOKEN = (
    SECRETS.get("AGENT_ROUTER_TOKEN")
    or SECRETS.get("AGENTKEY")
    or SECRETS.get("AGENT_KEY")
    or os.getenv("AGENT_ROUTER_TOKEN")
    or os.getenv("AGENTKEY")
    or os.getenv("AGENT_KEY")
)
AGENT_ROUTER_BASE_URL = SECRETS.get("AGENT_ROUTER_BASE_URL") or os.getenv("AGENT_ROUTER_BASE_URL") or "https://agentrouter.org/v1"

MYSQL_ENABLED = bool(MYSQL_HOST and MYSQL_DB and MYSQL_USER is not None)

if not DISCORD_TOKEN:
    raise SystemExit("Missing DISCORD_TOKEN in passwords.txt or env")
