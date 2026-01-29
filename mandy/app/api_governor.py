from __future__ import annotations

import asyncio
import contextvars
import random
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Deque, Dict, Optional, Tuple


class Bucket(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class GovernorState(str, Enum):
    NORMAL = "NORMAL"
    THROTTLED = "THROTTLED"
    RECOVERY = "RECOVERY"


@dataclass(frozen=True)
class GovernorContext:
    essential: bool = False
    priority: int = 0
    bucket_override: Optional[Bucket] = None


GOVERNOR_CONTEXT = contextvars.ContextVar("mandy_governor_context", default=GovernorContext())


def set_governor_context(
    *,
    essential: Optional[bool] = None,
    priority: Optional[int] = None,
    bucket_override: Optional[Bucket] = None,
) -> contextvars.Token:
    current = GOVERNOR_CONTEXT.get()
    updated = GovernorContext(
        essential=current.essential if essential is None else bool(essential),
        priority=current.priority if priority is None else int(priority),
        bucket_override=bucket_override if bucket_override is not None else current.bucket_override,
    )
    return GOVERNOR_CONTEXT.set(updated)


def reset_governor_context(token: contextvars.Token) -> None:
    try:
        GOVERNOR_CONTEXT.reset(token)
    except Exception:
        pass


@dataclass
class BucketConfig:
    max_rate: float
    refill_speed: float
    cooldown_multiplier: float
    min_rate: float = 0.1


@dataclass
class BucketState:
    current_rate: float
    current_refill: float
    tokens: float
    last_refill_ts: float
    cooldown_until: float = 0.0
    strike_count: int = 0
    last_429_ts: float = 0.0


@dataclass
class GovernorConfig:
    safe_window_seconds: float = 90.0
    recovery_step: float = 0.08
    jitter_ratio: float = 0.2
    min_jitter_ms: int = 15
    max_jitter_ms: int = 120
    breaker_threshold: int = 4
    breaker_window_seconds: float = 30.0
    breaker_cooldown_seconds: float = 60.0
    max_queue: int = 10000


@dataclass
class Intent:
    seq: int
    created_at: float
    bucket: Bucket
    priority: int
    essential: bool
    func: Callable[[], Awaitable[Any]]
    future: asyncio.Future
    ready_at: float
    attempts: int = 0


RateLimitDetector = Callable[[Exception], bool]
RetryAfterExtractor = Callable[[Exception], Optional[float]]


class AdaptiveGovernor:
    def __init__(
        self,
        bucket_configs: Dict[Bucket, BucketConfig],
        config: Optional[GovernorConfig] = None,
        *,
        is_rate_limit: Optional[RateLimitDetector] = None,
        retry_after: Optional[RetryAfterExtractor] = None,
        time_fn: Callable[[], float] = time.monotonic,
        rand: Optional[random.Random] = None,
    ):
        self._config = config or GovernorConfig()
        self._time_fn = time_fn
        self._rand = rand or random.Random()
        self._queue: asyncio.PriorityQueue[Tuple[float, int, int, Intent]] = asyncio.PriorityQueue()
        self._seq = 0
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._state = GovernorState.NORMAL
        self._last_global_429_ts = 0.0
        self._recent_429s: Deque[float] = deque()
        self._breaker_open = False
        self._breaker_until = 0.0

        self._bucket_configs = bucket_configs
        now = self._time_fn()
        self._buckets: Dict[Bucket, BucketState] = {}
        for bucket, cfg in bucket_configs.items():
            self._buckets[bucket] = BucketState(
                current_rate=cfg.max_rate,
                current_refill=cfg.refill_speed,
                tokens=cfg.max_rate,
                last_refill_ts=now,
            )

        self._is_rate_limit = is_rate_limit or (lambda exc: False)
        self._retry_after = retry_after or (lambda exc: None)

    @property
    def state(self) -> GovernorState:
        return self._state

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="adaptive-api-governor")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
            self._task = None

    async def submit(
        self,
        *,
        bucket: Bucket,
        func: Callable[[], Awaitable[Any]],
        priority: int = 0,
        essential: bool = False,
        ready_in: Optional[float] = None,
    ) -> Any:
        if not self._running:
            self.start()

        if self._queue.qsize() >= self._config.max_queue and not essential:
            raise RuntimeError("API governor queue is full")

        now = self._time_fn()
        ready_at = now + (ready_in if ready_in is not None else self._base_jitter())
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        self._seq += 1
        intent = Intent(
            seq=self._seq,
            created_at=now,
            bucket=bucket,
            priority=priority,
            essential=essential,
            func=func,
            future=fut,
            ready_at=ready_at,
        )
        await self._queue.put((intent.ready_at, -intent.priority, intent.seq, intent))
        return await fut

    def note_rate_limit(self, bucket: Bucket, retry_after: Optional[float] = None) -> None:
        now = self._time_fn()
        self._last_global_429_ts = now
        self._state = GovernorState.THROTTLED
        state = self._buckets[bucket]
        state.strike_count += 1
        state.last_429_ts = now

        cfg = self._bucket_configs[bucket]
        # Slowdown decision:
        # - On any 429, immediately reduce this bucket's rate/refill using exponential backoff.
        # - This clamps throughput fast to prevent repeated limits.
        factor = cfg.cooldown_multiplier ** state.strike_count
        state.current_rate = max(cfg.min_rate, cfg.max_rate / factor)
        state.current_refill = max(cfg.min_rate, cfg.refill_speed / factor)
        state.tokens = min(state.tokens, state.current_rate)

        # Cooldown pause honors API retry hints when available, adding a buffer.
        pause = retry_after if retry_after is not None else max(0.5, 1.0 * factor)
        state.cooldown_until = max(state.cooldown_until, now + pause)

        # Trip breaker if repeated 429s occur in a short window.
        self._recent_429s.append(now)
        self._trim_recent_429s(now)
        if len(self._recent_429s) >= self._config.breaker_threshold:
            self._breaker_open = True
            self._breaker_until = max(self._breaker_until, now + self._config.breaker_cooldown_seconds)
            self._state = GovernorState.THROTTLED

    def _trim_recent_429s(self, now: float) -> None:
        window = self._config.breaker_window_seconds
        while self._recent_429s and (now - self._recent_429s[0]) > window:
            self._recent_429s.popleft()

    def _base_jitter(self) -> float:
        # Jitter avoids synchronized spikes from periodic tasks.
        ms = self._rand.randint(self._config.min_jitter_ms, self._config.max_jitter_ms)
        base = ms / 1000.0
        return base * (1.0 + self._config.jitter_ratio * self._rand.random())

    def _refill_bucket(self, bucket: Bucket, now: float) -> None:
        state = self._buckets[bucket]
        if now < state.cooldown_until:
            state.tokens = 0.0
            state.last_refill_ts = now
            return

        elapsed = max(0.0, now - state.last_refill_ts)
        if elapsed <= 0:
            return
        state.tokens = min(state.current_rate, state.tokens + elapsed * state.current_refill)
        state.last_refill_ts = now

    def _should_allow(self, intent: Intent, now: float) -> bool:
        if self._breaker_open and now < self._breaker_until and not intent.essential:
            return False
        if self._breaker_open and now >= self._breaker_until:
            self._breaker_open = False
        return True

    def _maybe_recover(self, now: float) -> None:
        if self._last_global_429_ts and (now - self._last_global_429_ts) < self._config.safe_window_seconds:
            self._state = GovernorState.THROTTLED
            return

        if self._state == GovernorState.THROTTLED:
            self._state = GovernorState.RECOVERY

        if self._state != GovernorState.RECOVERY:
            return

        # Speed-up decision:
        # - After a safe window with no 429s, restore rate/refill slowly toward baseline.
        # - This avoids oscillation between too-fast and too-slow.
        all_done = True
        for bucket, cfg in self._bucket_configs.items():
            state = self._buckets[bucket]
            rate_diff = cfg.max_rate - state.current_rate
            refill_diff = cfg.refill_speed - state.current_refill
            if abs(rate_diff) > 0.01:
                state.current_rate += rate_diff * self._config.recovery_step
                all_done = False
            if abs(refill_diff) > 0.01:
                state.current_refill += refill_diff * self._config.recovery_step
                all_done = False

            if abs(rate_diff) <= 0.01:
                state.current_rate = cfg.max_rate
            if abs(refill_diff) <= 0.01:
                state.current_refill = cfg.refill_speed
            if (now - state.last_429_ts) > self._config.safe_window_seconds:
                state.strike_count = max(0, state.strike_count - 1)

        if all_done:
            self._state = GovernorState.NORMAL

    async def _run(self) -> None:
        while self._running:
            ready_at, _, _, intent = await self._queue.get()
            now = self._time_fn()

            if ready_at > now:
                await asyncio.sleep(ready_at - now)

            now = self._time_fn()
            self._maybe_recover(now)

            if not self._should_allow(intent, now):
                intent.ready_at = now + self._base_jitter()
                await self._queue.put((intent.ready_at, -intent.priority, intent.seq, intent))
                self._queue.task_done()
                continue

            self._refill_bucket(intent.bucket, now)
            state = self._buckets[intent.bucket]

            if now < state.cooldown_until or state.tokens < 1.0:
                if now < state.cooldown_until:
                    delay = max(0.05, state.cooldown_until - now)
                else:
                    delay = max(0.05, 1.0 / max(state.current_refill, 0.1))
                delay += self._base_jitter()
                intent.ready_at = now + delay
                await self._queue.put((intent.ready_at, -intent.priority, intent.seq, intent))
                self._queue.task_done()
                continue

            state.tokens -= 1.0

            try:
                result = await intent.func()
                if not intent.future.done():
                    intent.future.set_result(result)
            except Exception as exc:
                if self._is_rate_limit(exc):
                    retry_after = self._retry_after(exc)
                    self.note_rate_limit(intent.bucket, retry_after=retry_after)
                if not intent.future.done():
                    intent.future.set_exception(exc)
            finally:
                self._queue.task_done()

    def snapshot(self) -> Dict[str, Any]:
        now = self._time_fn()
        buckets = {}
        for bucket, state in self._buckets.items():
            buckets[bucket.value] = {
                "rate": round(state.current_rate, 3),
                "refill": round(state.current_refill, 3),
                "tokens": round(state.tokens, 3),
                "strike": state.strike_count,
                "cooldown_until": round(state.cooldown_until - now, 3),
            }
        return {
            "state": self._state.value,
            "breaker_open": self._breaker_open,
            "queue": self._queue.qsize(),
            "buckets": buckets,
        }
