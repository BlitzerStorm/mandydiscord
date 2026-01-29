from __future__ import annotations

import types
from typing import Any, Dict, Optional, Tuple

import discord

from . import state
from .api_governor import (
    Bucket,
    BucketConfig,
    GovernorConfig,
    GOVERNOR_CONTEXT,
    AdaptiveGovernor,
)
from .store import cfg


def _load_bucket_configs() -> Dict[Bucket, BucketConfig]:
    raw = cfg().get("api_governor", {}) if isinstance(cfg().get("api_governor", {}), dict) else {}
    buckets = raw.get("buckets", {}) if isinstance(raw.get("buckets", {}), dict) else {}

    def _bucket(name: str, default: BucketConfig) -> BucketConfig:
        data = buckets.get(name, {}) if isinstance(buckets.get(name, {}), dict) else {}
        return BucketConfig(
            max_rate=float(data.get("max_rate", default.max_rate)),
            refill_speed=float(data.get("refill_speed", default.refill_speed)),
            cooldown_multiplier=float(data.get("cooldown_multiplier", default.cooldown_multiplier)),
            min_rate=float(data.get("min_rate", default.min_rate)),
        )

    return {
        Bucket.LOW: _bucket("LOW", BucketConfig(2.0, 2.0, 1.5, min_rate=0.2)),
        Bucket.MEDIUM: _bucket("MEDIUM", BucketConfig(5.0, 5.0, 1.8, min_rate=0.3)),
        Bucket.HIGH: _bucket("HIGH", BucketConfig(10.0, 10.0, 2.2, min_rate=0.4)),
        Bucket.CRITICAL: _bucket("CRITICAL", BucketConfig(20.0, 20.0, 2.5, min_rate=0.6)),
    }


def _load_governor_config() -> GovernorConfig:
    raw = cfg().get("api_governor", {}) if isinstance(cfg().get("api_governor", {}), dict) else {}
    breaker = raw.get("breaker", {}) if isinstance(raw.get("breaker", {}), dict) else {}
    return GovernorConfig(
        safe_window_seconds=float(raw.get("safe_window_seconds", 90.0)),
        recovery_step=float(raw.get("recovery_step", 0.08)),
        jitter_ratio=float(raw.get("jitter_ratio", 0.2)),
        min_jitter_ms=int(raw.get("min_jitter_ms", 15)),
        max_jitter_ms=int(raw.get("max_jitter_ms", 120)),
        breaker_threshold=int(breaker.get("threshold", 4)),
        breaker_window_seconds=float(breaker.get("window_seconds", 30.0)),
        breaker_cooldown_seconds=float(breaker.get("cooldown_seconds", 60.0)),
        max_queue=int(raw.get("max_queue", 10000)),
    )


def _is_rate_limit_error(exc: Exception) -> bool:
    status = getattr(exc, "status", None)
    if status == 429:
        return True
    if exc.__class__.__name__ == "RateLimited":
        return True
    msg = str(exc).lower()
    return "rate limit" in msg or "429" in msg or "too many requests" in msg


def _retry_after(exc: Exception) -> Optional[float]:
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        try:
            return float(retry_after)
        except Exception:
            return None
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers and "Retry-After" in headers:
        try:
            return float(headers.get("Retry-After"))
        except Exception:
            return None
    return None


def _classify_route(route: discord.http.Route) -> Tuple[Bucket, bool, int]:
    method = route.method.upper()
    path = route.path.lower()

    essential = False
    priority = 10
    bucket = Bucket.LOW

    if "/interactions/" in path or "/webhooks/" in path:
        bucket = Bucket.CRITICAL
        essential = True
        priority = 100
        return bucket, essential, priority

    if method in {"POST", "PATCH", "PUT", "DELETE"}:
        if any(k in path for k in ("/roles", "/permissions", "/overwrites", "/channels", "/guilds", "/invites")):
            bucket = Bucket.HIGH
            priority = 70
        elif "/messages" in path:
            bucket = Bucket.HIGH
            priority = 60
        else:
            bucket = Bucket.MEDIUM
            priority = 50
    else:
        if any(k in path for k in ("/messages", "/members", "/audit-logs", "/invites")):
            bucket = Bucket.MEDIUM
            priority = 30
        else:
            bucket = Bucket.LOW
            priority = 10

    return bucket, essential, priority


def install_discord_governor(bot: discord.Client) -> Optional[AdaptiveGovernor]:
    raw = cfg().get("api_governor", {}) if isinstance(cfg().get("api_governor", {}), dict) else {}
    if not bool(raw.get("enabled", True)):
        return None

    if getattr(state, "API_GOVERNOR", None) is not None:
        return state.API_GOVERNOR

    bucket_cfgs = _load_bucket_configs()
    governor_cfg = _load_governor_config()
    governor = AdaptiveGovernor(
        bucket_cfgs,
        governor_cfg,
        is_rate_limit=_is_rate_limit_error,
        retry_after=_retry_after,
    )
    governor.start()
    state.API_GOVERNOR = governor

    if getattr(bot.http, "_governor_installed", False):
        return governor

    orig_request = bot.http.request
    bot.http._governor_orig_request = orig_request

    async def _governed_request(self, route: discord.http.Route, *, files=None, form=None, **kwargs: Any) -> Any:
        bucket, essential, priority = _classify_route(route)
        ctx = GOVERNOR_CONTEXT.get()
        if ctx.bucket_override is not None:
            bucket = ctx.bucket_override
        essential = essential or ctx.essential
        priority = max(priority, ctx.priority)

        async def _call():
            return await orig_request(route, files=files, form=form, **kwargs)

        return await governor.submit(bucket=bucket, func=_call, priority=priority, essential=essential)

    bot.http.request = types.MethodType(_governed_request, bot.http)
    bot.http._governor_installed = True
    return governor
