from __future__ import annotations

import asyncio
from pathlib import Path

from mandy_v1.bot import MandyBot
from mandy_v1.config import Settings


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


def test_self_automation_tasks_returns_backing_dict(tmp_path: Path) -> None:
    bot = _make_bot(tmp_path)
    tasks = bot._self_automation_tasks()
    tasks["probe"] = {"task_id": "probe", "interval_sec": 300}
    assert "probe" in bot.store.data["ai"]["self_automation"]["tasks"]


def test_workspace_guard_blocks_escape(tmp_path: Path) -> None:
    bot = _make_bot(tmp_path)
    try:
        bot._resolve_workspace_path("../outside.txt")
    except ValueError:
        blocked = True
    else:
        blocked = False
    assert blocked is True


def test_internal_selfcheck_no_hard_failures(tmp_path: Path) -> None:
    bot = _make_bot(tmp_path)
    report = bot._run_internal_selfcheck()
    assert isinstance(report, dict)
    assert "fail" in report
    assert report["fail"] == []


def test_automation_command_allowlist(tmp_path: Path) -> None:
    bot = _make_bot(tmp_path)
    assert bot._is_allowed_automation_command("python --version") is True
    assert bot._is_allowed_automation_command("rm -rf .") is False
    assert bot._is_allowed_automation_command("Remove-Item -Recurse .") is False
