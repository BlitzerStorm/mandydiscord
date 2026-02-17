from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from mandy_v1.bot import MandyBot
from mandy_v1.config import Settings


class StubGuild:
    def __init__(self, gid: int, *, owner_id: int, present_members: set[int] | None = None) -> None:
        self.id = gid
        self.owner_id = owner_id
        self._present_members = set(present_members or set())

    def get_member(self, user_id: int):
        if user_id in self._present_members:
            return object()
        return None


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


def test_satellite_owner_can_run_high_tier_satellite_actions(tmp_path: Path) -> None:
    bot = _make_bot(tmp_path)
    bot.soc.can_run = lambda user, tier: False  # type: ignore[assignment]

    owner = SimpleNamespace(id=41)
    guild = StubGuild(777, owner_id=41)
    bot.get_guild = lambda guild_id: guild if int(guild_id) == 777 else None  # type: ignore[assignment]
    bot.store.data["mirrors"]["servers"] = {"777": {}}

    assert bot._can_control_satellite(owner, 777, min_tier=90) is True
    assert bot._can_run_menu_action(owner, 777, "toggle_ai_mode", 90) is True


def test_non_owner_without_soc_cannot_control_satellite(tmp_path: Path) -> None:
    bot = _make_bot(tmp_path)
    bot.soc.can_run = lambda user, tier: False  # type: ignore[assignment]

    outsider = SimpleNamespace(id=55)
    guild = StubGuild(777, owner_id=41)
    bot.get_guild = lambda guild_id: guild if int(guild_id) == 777 else None  # type: ignore[assignment]
    bot.store.data["mirrors"]["servers"] = {"777": {}}

    assert bot._can_control_satellite(outsider, 777, min_tier=70) is False


def test_watcher_visibility_and_target_scope_for_satellite_owner(tmp_path: Path) -> None:
    bot = _make_bot(tmp_path)
    bot.soc.can_run = lambda user, tier: False  # type: ignore[assignment]

    owner = SimpleNamespace(id=41)
    guild = StubGuild(777, owner_id=41, present_members={1001})
    bot.get_guild = lambda guild_id: guild if int(guild_id) == 777 else None  # type: ignore[assignment]
    bot.store.data["mirrors"]["servers"] = {"777": {}}

    rows = {
        1001: {"threshold": 2, "response_text": "watch-a"},
        2002: {"threshold": 3, "response_text": "watch-b"},
    }
    visible = bot._visible_watcher_rows_for_user(owner, rows)

    assert set(visible.keys()) == {1001}
    assert bot._can_manage_watcher_target(owner, 1001) is True
    assert bot._can_manage_watcher_target(owner, 2002) is False
