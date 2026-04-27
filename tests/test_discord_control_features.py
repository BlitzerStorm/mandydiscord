from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from mandy_v1.bot import MandyBot
from mandy_v1.config import Settings
from mandy_v1.services.ai_service import AIService
from mandy_v1.storage import MessagePackStore


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        discord_token="token",
        admin_guild_id=123,
        god_user_id=741470965359443970,
        command_prefix="!",
        store_path=tmp_path / "state.msgpack",
        alibaba_api_key="fake",
        alibaba_base_url="https://example.invalid/v1",
        alibaba_model="stub",
    )


def _store(tmp_path: Path) -> MessagePackStore:
    store = MessagePackStore(tmp_path / "state.msgpack")
    asyncio.run(store.load())
    return store


def _message(user_id: int, text: str) -> object:
    return SimpleNamespace(
        guild=SimpleNamespace(id=77, name="guild"),
        channel=SimpleNamespace(id=55, name="chat"),
        author=SimpleNamespace(id=user_id, bot=False, display_name="user"),
        clean_content=text,
        content=text,
        attachments=[],
        mentions=[],
        reference=None,
    )


def test_privacy_pause_export_and_forget(tmp_path: Path) -> None:
    ai = AIService(_settings(tmp_path), _store(tmp_path))
    ai.capture_message(_message(42, "my favorite color is blue"), touch=False)
    assert ai.list_user_memory(77, 42)

    ai.set_learning_paused(42, True, actor_id=1, reason="user request")
    assert ai.is_learning_paused(42) is True
    ai.capture_message(_message(42, "my favorite food is ramen"), touch=False)
    assert all("ramen" not in row["fact"] for row in ai.list_user_memory(77, 42))

    exported = ai.export_user_memory(42)
    assert exported["learning_paused"] is True
    removed = ai.forget_user_everywhere(42, actor_id=1, reason="test")
    assert removed["facts"] >= 1
    assert ai.list_user_memory(77, 42) == []
    assert ai.privacy_audit_lines()


def test_reflection_compaction_dedupes_rows(tmp_path: Path) -> None:
    ai = AIService(_settings(tmp_path), _store(tmp_path))
    row = ai._reflection_row(77)  # noqa: SLF001
    row["storylines"] = ["same lore", "same lore", "another lore"]
    result = ai.compact_reflections(guild_id=77)

    assert result["compacted"] >= 1
    assert row["storylines"] == ["same lore", "another lore"]


def test_telemetry_records_cache_and_failures(tmp_path: Path) -> None:
    class StubAI(AIService):
        async def _chat_completion(self, messages, max_tokens=180, temperature=0.7, *, api_key: str, model: str) -> str:
            return "ok"

    ai = StubAI(_settings(tmp_path), _store(tmp_path))
    first = asyncio.run(ai.complete_text(system_prompt="s", user_prompt="u", cache_ttl_sec=120))
    second = asyncio.run(ai.complete_text(system_prompt="s", user_prompt="u", cache_ttl_sec=120))
    snapshot = ai.telemetry_snapshot()

    assert first == "ok"
    assert second == "ok"
    assert snapshot["calls"] == 1
    assert snapshot["cache_hits"] == 1
    assert snapshot["successes"] == 1
    assert snapshot["estimated_tokens"] > 0


def test_autonomy_approval_executes_payload(tmp_path: Path) -> None:
    bot = MandyBot(_settings(tmp_path))
    asyncio.run(bot.store.load())
    guild = SimpleNamespace(id=77)
    bot.get_guild = lambda gid: guild if int(gid) == 77 else None  # type: ignore[assignment]
    bot._is_autonomous_action_allowed = lambda guild_id, payload: (True, "ok")  # type: ignore[method-assign]

    class Control:
        def __init__(self) -> None:
            self.payload = None

        async def dispatch_action(self, guild_arg, payload, *, source_message=None):
            self.payload = payload
            return True

    control = Control()
    bot.server_control = control
    bot._record_autonomy_proposal(77, {"action": "rename_channel", "target": 55, "name": "new"}, status="pending", reason="test")  # noqa: SLF001

    ok, message = asyncio.run(bot._approve_and_execute_autonomy_proposal(1, actor_id=99))  # noqa: SLF001

    assert ok is True
    assert "executed=`True`" in message
    assert control.payload["action"] == "rename_channel"
    assert bot.store.data["autonomy_policy"]["proposals"][0]["status"] == "executed"
