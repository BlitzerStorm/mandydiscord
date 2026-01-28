import asyncio
from typing import Optional, Tuple

from . import state
from .store import cfg


def setup_delay_base() -> float:
    try:
        return max(0.0, float(cfg().get("tuning", {}).get("setup_delay", 1.0)))
    except Exception:
        return 1.0


def setup_delay() -> float:
    override = state.SETUP_DELAY_OVERRIDE
    if override is not None:
        try:
            return max(0.0, float(override))
        except Exception:
            return setup_delay_base()
    return setup_delay_base()


def _rate_limit_info(exc: Exception) -> Tuple[bool, Optional[float]]:
    status = getattr(exc, "status", None)
    retry_after = getattr(exc, "retry_after", None)
    if status == 429 or retry_after is not None:
        try:
            retry_val = float(retry_after) if retry_after is not None else None
        except Exception:
            retry_val = None
        return True, retry_val
    return False, None


def _setup_adaptive_enabled() -> bool:
    if state.SETUP_ADAPTIVE_ACTIVE:
        return True
    try:
        tuning = cfg().get("tuning", {})
        if not isinstance(tuning, dict):
            return False
        return bool(tuning.get("setup_adaptive", False))
    except Exception:
        return False


def _setup_adjust_delay(success: bool, retry_after: Optional[float] = None) -> None:
    if not _setup_adaptive_enabled():
        return
    current = state.SETUP_DELAY_OVERRIDE if state.SETUP_DELAY_OVERRIDE is not None else setup_delay_base()
    if success:
        new_delay = max(state.SETUP_DELAY_MIN, current - state.SETUP_DELAY_STEP)
    else:
        bump = (retry_after + 0.25) if retry_after else (current * 1.4 + 0.25)
        new_delay = min(state.SETUP_DELAY_MAX, max(current, bump))
    state.SETUP_DELAY_OVERRIDE = new_delay


async def setup_pause(success: bool = True, retry_after: Optional[float] = None):
    _setup_adjust_delay(success, retry_after)
    delay = setup_delay()
    if delay > 0:
        await asyncio.sleep(delay)


async def _setup_pause_on_rate_limit(exc: Exception) -> None:
    is_rate, retry_after = _rate_limit_info(exc)
    if is_rate:
        await setup_pause(success=False, retry_after=retry_after)
