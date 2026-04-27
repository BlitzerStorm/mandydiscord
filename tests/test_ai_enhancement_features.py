from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from mandy_v1.bot import MandyBot
from mandy_v1.config import Settings
from mandy_v1.services.ai_service import AIService
from mandy_v1.storage import MessagePackStore


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


def _make_store(tmp_path: Path) -> MessagePackStore:
    store = MessagePackStore(tmp_path / "state.msgpack")
    asyncio.run(store.load())
    return store


def _make_ai(tmp_path: Path) -> AIService:
    return AIService(_make_settings(tmp_path), _make_store(tmp_path))


def _message(*, guild_id: int = 77, channel_id: int = 55, user_id: int = 2001, content: str) -> object:
    author = SimpleNamespace(id=user_id, bot=False, display_name=f"user-{user_id}", name=f"user-{user_id}")
    guild = SimpleNamespace(id=guild_id, name=f"guild-{guild_id}", me=SimpleNamespace(id=9999))
    channel = SimpleNamespace(id=channel_id, name=f"chan-{channel_id}")
    return SimpleNamespace(
        guild=guild,
        author=author,
        content=content,
        clean_content=content,
        channel=channel,
        attachments=[],
        mentions=[],
        reference=None,
    )


def test_reflection_and_curiosity_update_from_messages(tmp_path: Path) -> None:
    ai = _make_ai(tmp_path)
    ai.capture_message(
        _message(content="remember when the server lore arc happened again? we still need to figure out why"),
        touch=False,
    )

    summary = ai.reflection_summary(77)
    assert summary["storylines"]
    assert summary["unresolved_threads"]
    assert "unresolved thread" in ai.plan_curiosity_question(77, 2001, "I think this is still weird")


def test_fun_mode_changes_prompt_block(tmp_path: Path) -> None:
    ai = _make_ai(tmp_path)
    row = ai.set_fun_mode(77, "lore")

    assert row["mode"] == "lore"
    assert "server lore" in ai.fun_mode_summary(77)
    assert "ADAPTIVE FUN MODE" in ai.build_contextual_system_prompt(guild_id=77, user_id=1, topic="hello")


def test_memory_controls_pin_edit_and_forget(tmp_path: Path) -> None:
    ai = _make_ai(tmp_path)
    ai.capture_message(_message(content="my favorite game is chess"), touch=False)

    rows = ai.list_user_memory(77, 2001)
    assert rows and rows[0]["fact"].startswith("favorite game")
    assert ai.pin_user_memory(77, 2001, 0) is True
    assert ai.list_user_memory(77, 2001)[0]["pinned"] is True
    assert ai.edit_user_memory(77, 2001, 0, "favorite game: go") is True
    assert ai.list_user_memory(77, 2001)[0]["fact"] == "favorite game: go"
    assert ai.forget_user_memory(77, 2001, 0) is True
    assert ai.list_user_memory(77, 2001) == []


def test_relationship_arcs_and_capabilities(tmp_path: Path) -> None:
    ai = _make_ai(tmp_path)
    for _ in range(5):
        ai._note_relationship_signal(user_id=42, user_name="friend", text="thank you mandy appreciate you", source="test")  # noqa: SLF001
    ai._note_relationship_signal(user_id=42, user_name="friend", text="remember when our inside joke started", source="test")  # noqa: SLF001

    rel = ai.relationship_snapshot(42)
    assert rel["arc_stage"] in {"warming", "trusted"}
    assert rel["inside_jokes"]
    assert any(line.startswith("fun_modes:") for line in ai.capability_lines())


def test_autonomy_proposal_ledger_records_blocked_action(tmp_path: Path) -> None:
    bot = MandyBot(_make_settings(tmp_path))
    asyncio.run(bot.store.load())

    row = bot._record_autonomy_proposal(77, {"action": "kick_member", "target": 42}, status="blocked", reason="test")  # noqa: SLF001
    assert row["id"] == 1
    assert bot._mark_autonomy_proposal(1, status="approved", actor_id=99)["status"] == "approved"  # noqa: SLF001
    assert bot.store.data["autonomy_policy"]["proposals"][0]["reviewed_by"] == 99
