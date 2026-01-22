import asyncio
from typing import Any

from .state import ACTIVE_TASKS


def track_task(task: asyncio.Task, label: str) -> asyncio.Task:
    bucket = ACTIVE_TASKS.setdefault(label, set())
    bucket.add(task)

    def _cleanup(done_task: asyncio.Task) -> None:
        bucket.discard(done_task)
        if not bucket:
            ACTIVE_TASKS.pop(label, None)

    task.add_done_callback(_cleanup)
    return task


def spawn_task(coro: Any, label: str) -> asyncio.Task:
    return track_task(asyncio.create_task(coro), label)
