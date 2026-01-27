from __future__ import annotations

from typing import Any, Dict, List, Optional

from .core_text import truncate
from .core_time import now_ts
from .store import STORE, cfg


def memory_state() -> Dict[str, Any]:
    return cfg().setdefault("memory", {}).setdefault("events", [])


async def memory_add(kind: str, text: str, meta: Optional[Dict[str, Any]] = None):
    events = cfg().setdefault("memory", {}).setdefault("events", [])
    events.append(
        {
            "ts": now_ts(),
            "kind": kind,
            "text": truncate(text, 500),
            "meta": meta or {},
        }
    )
    if len(events) > 200:
        del events[:-200]
    await STORE.mark_dirty()


def memory_recent(limit: int = 10) -> List[Dict[str, Any]]:
    events = list(cfg().setdefault("memory", {}).get("events", []))
    return list(events[-limit:])


def ark_snapshots() -> Dict[str, Any]:
    return cfg().setdefault("ark_snapshots", {})


def phoenix_keys() -> Dict[str, str]:
    return cfg().setdefault("phoenix_keys", {})

