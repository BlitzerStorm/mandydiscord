from __future__ import annotations

import json
from typing import Any, Dict

from . import config


DEFAULT_JSON: Dict[str, Any] = {
    "targets": {},
    "mirrors": {"interactive_controls_enabled": True},
    "mirror_rules": {},
    "mirror_status": {},
    "admin_servers": {},
    "server_status_messages": {},
    "server_info_messages": {},
    "mirror_message_map": {},
    "dm_bridges": {},
    "dm_bridge_controls": {},
    "dm_ai": {},
    "bot_status": {"state": "online", "text": ""},
    "presence": {
        "bio": "",
        "autopresence_enabled": False,
        "last_message_ts": 0,
        "last_super_interaction_ts": 0,
    },
    "ambient_engine": {"enabled": True, "last_typing": 0, "last_presence": 0},
    "permissions": {},
    "gate": {},
    "gate_layout": {
        "category": "GUEST ACCESS",
        "guest_chat": "guest-chat",
        "guest_briefing": "guest-briefing",
        "quarantine": "quarantine",
    },
    "mirror_fail_threshold": config.MIRROR_FAIL_THRESHOLD,
    "mirror_disable_ttl": 7 * 24 * 3600,
    "logs": {"system": None, "audit": None, "debug": None, "mirror": None, "ai": None, "voice": None},
    "command_channels": {"user": "requests", "god": "admin-chat", "mode": "off"},
    "typing_delay_seconds": 5.0,
    "dm_bridge_history_limit": 50,
    "menu_messages": {},
    "rbac": {"role_levels": config.ROLE_LEVEL_DEFAULTS.copy()},
    "auto": {"setup": True, "backfill": True, "backfill_limit": 50, "backfill_per_channel": 20, "backfill_delay": 0.2},
    "tuning": {
        "setup_delay": 4.0,
        "setup_adaptive": True,
        "discord_send_delay": 0.25,
        "discord_send_adaptive": True,
    },
    "api_governor": {
        "enabled": True,
        "safe_window_seconds": 90.0,
        "recovery_step": 0.08,
        "jitter_ratio": 0.2,
        "min_jitter_ms": 15,
        "max_jitter_ms": 120,
        "max_queue": 10000,
        "breaker": {"threshold": 4, "window_seconds": 30.0, "cooldown_seconds": 60.0},
        "buckets": {
            "LOW": {"max_rate": 2.0, "refill_speed": 2.0, "cooldown_multiplier": 1.5, "min_rate": 0.2},
            "MEDIUM": {"max_rate": 5.0, "refill_speed": 5.0, "cooldown_multiplier": 1.8, "min_rate": 0.3},
            "HIGH": {"max_rate": 10.0, "refill_speed": 10.0, "cooldown_multiplier": 2.2, "min_rate": 0.4},
            "CRITICAL": {"max_rate": 20.0, "refill_speed": 20.0, "cooldown_multiplier": 2.5, "min_rate": 0.6},
        },
    },
    "ai": {
        "default_model": "gemini-2.5-flash-lite",
        "router_model": "gemini-2.5-flash-lite",
        "build_model": "gemini-2.5-pro",
        "agent_router_model": "gpt-4o-mini",
        "tts_model": "",
        "cooldown_seconds": 5,
        "fast_path": False,
        "router_only": False,
        "auto_build_tools": False,
        "disable_limits": False,
        "limits": config.DEFAULT_AI_LIMITS.copy(),
        "queue": {},
        "rolling": {},
        "daily": {},
        "installed_extensions": [],
    },
    "mandy": {"mention_dm_cooldowns": {}, "power_mode": True},
    "ai_layout": {"enabled": False, "layout": {}, "log_channels": {}, "command_channels": {}, "gate": {}, "updated_at": 0},
    "soc_access": {
        "sync_interval_minutes": 30,
        "initial_delay_seconds": 60,
        "sections": {
            "docs": {"role": "SEC:DOCS", "default": True},
            "guest_area": {"role": "SEC:GUEST-AREA", "default": True},
            "guest_write": {"role": "SEC:GUEST-WRITE", "default": False},
            "mirrors": {"role": "SEC:MIRRORS", "default": True},
            "server_info": {"role": "SEC:SERVER-INFO", "default": True},
        },
        "users": {},
    },
    "soc_onboarding": {
        "admin_invite_url": "",
        "bot_invite_permissions": 8,
        "token_ttl_minutes": 30,
        "users": {},
        "tokens": {},
    },
    "owner_onboarding": {"pending": {}, "history": {}, "feature_defaults": ["mirror", "logs", "stats", "dm_bridge", "ai_tools"]},
    "satellite_features": {},
    "roast": {
        "enabled": False,
        "trigger_word": "mandy",
        "max_history": 5,
        "cooldown_seconds": 600,
        "opt_in_users": [],
        "allowed_guilds": [],
        "auto_opt_in_guilds": [],
        "allowed_channels": [],
        "blocked_channels": [],
        "style": "playful",
        "use_ai": True,
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
            "fallback_enabled": False,
        },
        "internal_monologue": {
            "enabled": False,
            "last_run_utc": 0,
            "interval_minutes": 180,
            "max_lines": 4,
        },
        "maintenance": {"enabled": True, "ai_queue_max_age_hours": 6},
    },
    "diagnostics": {"channel_id": 0, "message_id": 0, "last_update": 0},
    "manual": {
        "channel_id": 0,
        "last_hash": "",
        "last_message_id": 0,
        "last_upload": 0,
        "auto_upload_enabled": True,
    },
    "memory": {"events": []},
    "ark_snapshots": {},
    "phoenix_keys": {},
    "onboarding": {"rules_channel_id": 0, "role_name": "Citizen", "phrases": ["i agree"]},
    "backfill_state": {"done": {}},
    "chat_stats": {},
    "chat_stats_backfill_done": {},
    "chat_stats_live_message": {},
    "chat_stats_global_live_message": {},
    "layout": {
        "categories": {
            "WELCOME": ["rules", "announcements", "guest-briefing", "manual-for-living"],
            "OPERATIONS": ["console", "requests", "reports"],
            "SATELLITES": [],
            "GUEST ACCESS": ["guest-chat", "guest-feedback", "quarantine"],
            "ENGINEERING": ["system-log", "audit-log", "debug-log", "mirror-log", "data-lab", "dm-bridges"],
            "GOD CORE": ["admin-chat", "server-management", "layout-control", "blueprint-export", "incident-room"],
        }
    },
    "channel_topics": {
        "rules": "Read these first. Required for all members.",
        "announcements": "Server announcements and updates.",
        "guest-briefing": "How to join and get approved.",
        "manual-for-living": "Latest SOC manual uploads and operator runbooks.",
        "guest-chat": "Guest chat (limited).",
        "guest-feedback": "Feedback and questions from guests.",
        "quarantine": "Restricted holding channel.",
        "console": "Bot status updates and presence controls.",
        "requests": "User command requests. Commands outside this channel are removed.",
        "reports": "Report issues or errors with commands.",
        "data-lab": "Core engineering, algorithms, and data analysis.",
        "system-log": "System log stream (general).",
        "audit-log": "Audit trail for privileged actions.",
        "debug-log": "Debug output and diagnostics.",
        "mirror-log": "Mirror pipeline events and failures.",
        "admin-chat": "GOD-only commands and admin coordination.",
        "server-management": "Server ops notes and maintenance.",
        "layout-control": "Layout rebuild and setup coordination.",
        "blueprint-export": "Blueprint export / backups.",
        "incident-room": "High-severity incident response.",
        "dm-bridges": "Active DM bridge channels live here.",
    },
    "pinned_text": {
        "rules": (
            "<!--PIN:rules-->\n"
            "**Rules & Guidelines**\n"
            "- Be respectful.\n"
            "- No spam.\n"
            "- Follow staff instructions.\n\n"
            "**Commands (prefix):**\n"
            "- `!menu`\n"
            "- `!godmenu`\n"
            "- `!setup fullsync`\n"
        ),
        "console": ("<!--PIN:console-->\n**Bot Status & Help**\nMenus auto-populate in command channels.\n"),
        "system-log": ("<!--PIN:system-log-->\n**System Logs**\nGeneral system log stream.\n"),
        "requests": ("<!--PIN:requests-->\n**Command Requests**\nUse the menu panel below for user tools.\n"),
        "reports": (
            "<!--PIN:reports-->\n"
            "**Error Reporting**\n"
            "Post issues with timestamps and screenshots if possible.\n"
        ),
        "audit-log": ("<!--PIN:audit-log-->\n**Audit Logs**\nPrivileged actions and security events.\n"),
        "debug-log": ("<!--PIN:debug-log-->\n**Debug Logs**\nDiagnostic output and errors.\n"),
        "mirror-log": ("<!--PIN:mirror-log-->\n**Mirror Logs**\nMirror events, failures, and status.\n"),
        "guest-briefing": (
            "<!--PIN:guest-briefing-->\n"
            "**Guest Briefing**\n"
            "This server uses a password gate. Ask staff if you're stuck.\n"
        ),
        "quarantine": ("<!--PIN:quarantine-->\n**Quarantine**\nQuarantined users wait here until staff releases them.\n"),
        "admin-chat": ("<!--PIN:admin-chat-->\n**Admin Chat**\nGOD-only command channel. Use the panel below.\n"),
    },
}


def new_default_json() -> Dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_JSON))
