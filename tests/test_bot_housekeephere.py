from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mandy_v1.bot import MandyBot
from mandy_v1.config import Settings


class StubMessage:
    def __init__(self, message_id: int, created_at: datetime, *, pinned: bool = False) -> None:
        self.id = message_id
        self.created_at = created_at
        self.pinned = pinned
        self.channel: StubChannel | None = None
        self.deleted = False

    async def delete(self) -> None:
        if self.deleted:
            return
        self.deleted = True
        if self.channel and self in self.channel.messages:
            self.channel.messages.remove(self)


class StubChannel:
    def __init__(self, messages: list[StubMessage]) -> None:
        self.messages = list(messages)
        for msg in self.messages:
            msg.channel = self

    async def history(self, limit=None, oldest_first: bool = False):
        ordered = sorted(self.messages, key=lambda row: row.created_at, reverse=not oldest_first)
        count = 0
        for msg in ordered:
            if limit is not None and count >= int(limit):
                break
            count += 1
            yield msg

    async def delete_messages(self, batch: list[StubMessage]) -> None:
        for msg in list(batch):
            await msg.delete()


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        discord_token="token",
        admin_guild_id=123,
        god_user_id=741470965359443970,
        command_prefix="!",
        store_path=tmp_path / "state.msgpack",
        alibaba_api_key="",
        alibaba_base_url="https://example.invalid/v1",
        alibaba_model="qwen-plus",
    )


def _make_bot(tmp_path: Path) -> MandyBot:
    bot = MandyBot(_make_settings(tmp_path))
    asyncio.run(bot.store.load())
    return bot


def test_wipe_channel_messages_deletes_recent_old_and_pinned(tmp_path: Path) -> None:
    bot = _make_bot(tmp_path)
    now = datetime.now(tz=timezone.utc)
    channel = StubChannel(
        [
            StubMessage(1, now - timedelta(hours=1), pinned=False),
            StubMessage(2, now - timedelta(days=1), pinned=True),
            StubMessage(3, now - timedelta(days=20), pinned=False),
        ]
    )

    scanned, deleted = asyncio.run(bot._wipe_channel_messages(channel, max_passes=1))

    assert scanned == 3
    assert deleted == 3
    assert channel.messages == []
