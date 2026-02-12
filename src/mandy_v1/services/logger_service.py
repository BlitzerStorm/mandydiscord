from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from mandy_v1.storage import MessagePackStore


class LoggerService:
    def __init__(self, store: MessagePackStore) -> None:
        self.store = store
        self._listeners: list[Callable[[dict[str, object]], None]] = []

    def subscribe(self, listener: Callable[[dict[str, object]], None]) -> None:
        self._listeners.append(listener)

    def log(self, event: str, **data: object) -> None:
        row = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "event": event,
            "data": data,
        }
        logs = self.store.data["logs"]
        logs.append(row)
        if len(logs) > 2000:
            del logs[: len(logs) - 2000]
        self.store.touch()
        print(f"[{row['ts']}] {event} {data}")
        for listener in self._listeners:
            try:
                listener(row)
            except Exception:  # noqa: BLE001
                continue
