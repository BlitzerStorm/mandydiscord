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
        "category": "Guest Access",
        "guest_chat": "guest-chat",
        "guest_briefing": "guest-briefing",
        "quarantine": "quarantine",
    },
    "mirror_fail_threshold": config.MIRROR_FAIL_THRESHOLD,
    "mirror_disable_ttl": 7 * 24 * 3600,
    "logs": {"system": None, "audit": None, "debug": None, "mirror": None, "ai": None, "voice": None},
    "command_channels": {"user": "command-requests", "god": "admin-chat", "mode": "off"},
    "typing_delay_seconds": 5.0,
    "dm_bridge_history_limit": 50,
    "menu_messages": {},
    "rbac": {"role_levels": config.ROLE_LEVEL_DEFAULTS.copy()},
    "auto": {"setup": True, "backfill": True, "backfill_limit": 50, "backfill_per_channel": 20, "backfill_delay": 0.2},
    "tuning": {
        "setup_delay": 2.5,
        "setup_adaptive": True,
        "discord_send_delay": 0.25,
        "discord_send_adaptive": True,
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
            "Welcome & Information": ["rules-and-guidelines", "announcements", "guest-briefing", "manual-for-living"],
            "Bot Control & Monitoring": ["bot-status", "command-requests", "error-reporting"],
            "Research & Development": ["algorithm-discussion", "data-analysis"],
            "Guest Access": ["guest-chat", "guest-feedback", "quarantine"],
            "Engineering Core": ["core-chat", "system-logs", "audit-logs", "debug-logs", "mirror-logs"],
            "Admin Backrooms": ["admin-chat", "server-management"],
            "DM Bridges": [],
        }
    },
    "channel_topics": {
        "rules-and-guidelines": "Read these first. Required for all members.",
        "announcements": "Server announcements and updates.",
        "guest-briefing": "How to join and get approved.",
        "manual-for-living": "Latest SOC manual uploads and operator runbooks.",
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
        "server-management": "Server ops notes and maintenance.",
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
        "bot-status": ("dY\"O **Bot Status & Help**\n" "Menus auto-populate in command channels.\n"),
        "system-logs": ("dY\"O **System Logs**\n" "General system log stream.\n"),
        "command-requests": ("dY\"O **Command Requests**\n" "Use the menu panel below for user tools.\n"),
        "error-reporting": (
            "dY\"O **Error Reporting**\n"
            "Post issues with timestamps and screenshots if possible.\n"
        ),
        "audit-logs": ("dY\"O **Audit Logs**\n" "Privileged actions and security events.\n"),
        "debug-logs": ("dY\"O **Debug Logs**\n" "Diagnostic output and errors.\n"),
        "mirror-logs": ("dY\"O **Mirror Logs**\n" "Mirror events, failures, and status.\n"),
        "guest-briefing": (
            "dY\"O **Guest Briefing**\n"
            "This server uses a password gate. Ask staff if youź?Tre stuck.\n"
        ),
        "quarantine": ("dY\"O **Quarantine**\n" "Quarantined users wait here until staff releases them.\n"),
        "admin-chat": ("dY\"O **Admin Chat**\n" "GOD-only command channel. Use the panel below.\n"),
    },
}


def new_default_json() -> Dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_JSON))
