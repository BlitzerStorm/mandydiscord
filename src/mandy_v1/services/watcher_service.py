from __future__ import annotations

import random
from dataclasses import dataclass

import discord

from mandy_v1.storage import MessagePackStore


@dataclass
class WatcherHit:
    user_id: int
    response: str
    threshold: int
    count: int


class WatcherService:
    def __init__(self, store: MessagePackStore) -> None:
        self.store = store

    def add_or_update(self, user_id: int, threshold: int, response_text: str) -> None:
        self.store.data["watchers"][str(user_id)] = {
            "threshold": int(threshold),
            "response_text": response_text.strip(),
        }
        self.store.touch()

    def remove(self, user_id: int) -> bool:
        key = str(user_id)
        watchers = self.store.data["watchers"]
        existed = key in watchers
        watchers.pop(key, None)
        self.store.data["watcher_counts"].pop(key, None)
        self.store.touch()
        return existed

    def reset_count(self, user_id: int) -> None:
        self.store.data["watcher_counts"][str(user_id)] = 0
        self.store.touch()

    def list_all(self) -> dict[str, dict]:
        return self.store.data["watchers"]

    def on_message(self, message: discord.Message) -> WatcherHit | None:
        if message.author.bot:
            return None
        key = str(message.author.id)
        counts = self.store.data["watcher_counts"]
        counts[key] = int(counts.get(key, 0)) + 1
        watcher = self.store.data["watchers"].get(key)
        self.store.touch()
        if not watcher:
            return None
        threshold = max(1, int(watcher.get("threshold", 1)))
        count = int(counts[key])
        if count % threshold != 0:
            return None
        choices = [part.strip() for part in str(watcher.get("response_text", "")).split("|") if part.strip()]
        if not choices:
            return None
        return WatcherHit(
            user_id=message.author.id,
            response=random.choice(choices),
            threshold=threshold,
            count=count,
        )
