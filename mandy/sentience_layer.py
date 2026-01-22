from typing import Any, Dict, Optional


def sentience_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    root = cfg.setdefault("sentience", {})
    root.setdefault("enabled", True)
    root.setdefault("dialect", "sentient_core")
    root.setdefault("channels", {})
    root.setdefault("thoughts_rate_limit_seconds", 30)
    root.setdefault("menu_style", "default")
    daily = root.setdefault("daily_reflection", {})
    daily.setdefault("enabled", False)
    daily.setdefault("last_run_utc", 0)
    daily.setdefault("hour_utc", None)
    daily.setdefault("max_messages", 120)
    daily.setdefault("fallback_enabled", False)
    monologue = root.setdefault("internal_monologue", {})
    monologue.setdefault("enabled", False)
    monologue.setdefault("last_run_utc", 0)
    monologue.setdefault("interval_minutes", 180)
    monologue.setdefault("max_lines", 4)
    maintenance = root.setdefault("maintenance", {})
    maintenance.setdefault("enabled", True)
    maintenance.setdefault("ai_queue_max_age_hours", 6)
    bio_setup = root.setdefault("bio_setup", {})
    bio_setup.setdefault("pause_background", True)
    bio_setup.setdefault("resume_background", False)
    return root


def presence_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    presence = cfg.setdefault("presence", {})
    presence.setdefault("bio", "")
    presence.setdefault("autopresence_enabled", False)
    presence.setdefault("last_message_ts", 0)
    presence.setdefault("last_super_interaction_ts", 0)
    return presence


def _dialect(cfg: Dict[str, Any]) -> str:
    sent = sentience_cfg(cfg)
    if not sent.get("enabled", True):
        return "plain"
    return str(sent.get("dialect", "sentient_core") or "sentient_core")


def voice_line(cfg: Dict[str, Any], key: str, fallback: Optional[str] = None, **kwargs) -> str:
    plain = {
        "confirm_mirror_added": "Mirror added.",
        "confirm_mirror_added_scope": "Mirror rule added.",
        "confirm_mirror_removed": "Mirror removed/disabled ({count}).",
        "confirm_log_set": "Log channel updated.",
        "confirm_dm_sent": "DM sent.",
        "confirm_sent": "Sent.",
        "confirm_leavevc": "Voice connections forcibly terminated.",
        "confirm_cancel": "Active tasks canceled ({count}).",
        "err_no_permission": "No permission.",
        "err_mapping_missing": "Mapping not found (old/pruned).",
        "err_source_not_accessible": "Source not accessible.",
        "err_send_failed": "Send failed.",
        "err_mirror_feed_missing": "Mirror feed not available.",
        "err_missing_perms": "Missing permissions.",
        "status_homeostasis": "Homeostasis stable.",
        "status_cortex_online": "Cortex online.",
        "status_immune_normal": "Immune posture normal.",
        "health_snapshot": "Health snapshot:",
    }
    sentient = {
        "confirm_mirror_added": "Mirror link stabilized. Sensory relay online.",
        "confirm_mirror_added_scope": "Mirror rule seeded. Sensory feed aligned.",
        "confirm_mirror_removed": "Mirror linkage pruned ({count}). Sensory feed quiet.",
        "confirm_log_set": "Log routing recalibrated.",
        "confirm_dm_sent": "Signal dispatched to the target synapse.",
        "confirm_sent": "Signal transmitted.",
        "confirm_leavevc": "Voice connections forcibly terminated.",
        "confirm_cancel": "Active processes halted ({count}).",
        "err_no_permission": "Immune gate denies access. Insufficient clearance.",
        "err_mapping_missing": "Synaptic link degraded; cannot locate origin.",
        "err_source_not_accessible": "Sensory path blocked; origin inaccessible.",
        "err_send_failed": "Signal failed to propagate. Transmission error.",
        "err_mirror_feed_missing": "Sensory feed offline; mirror target unavailable.",
        "err_missing_perms": "Immune gate denies action. Missing permissions.",
        "status_homeostasis": "Homeostasis stable.",
        "status_cortex_online": "Cortex online.",
        "status_immune_normal": "Immune posture normal.",
        "health_snapshot": "Health snapshot:",
    }
    dialect = _dialect(cfg)
    table = sentient if dialect == "sentient_core" else plain
    template = table.get(key) or fallback or key
    try:
        return template.format(**kwargs)
    except Exception:
        return template
