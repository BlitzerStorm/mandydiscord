from __future__ import annotations

import datetime
import time


def now_ts() -> int:
    return int(time.time())


def fmt_ts(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts).isoformat()

