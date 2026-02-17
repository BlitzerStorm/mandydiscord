from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

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
        alibaba_api_key="fake-key",
        alibaba_base_url="https://example.invalid/v1",
        alibaba_model="qwen-plus",
    )


def _make_store(tmp_path: Path) -> MessagePackStore:
    store = MessagePackStore(tmp_path / "state.msgpack")
    asyncio.run(store.load())
    return store


def _stub_message(*, guild_id: int, user_id: int, content: str) -> object:
    author = SimpleNamespace(id=user_id, bot=False, display_name=f"user-{user_id}")
    guild = SimpleNamespace(id=guild_id)
    channel = SimpleNamespace(id=55)
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


def test_prompt_injection_combines_master_and_guild(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = _make_store(tmp_path)
    ai = AIService(settings, store)

    ai.set_prompt_injection(guild_id=0, prompt_text="MASTER", learning_mode="full", actor_user_id=1, source="test")
    ai.set_prompt_injection(guild_id=77, prompt_text="GUILD", learning_mode="light", actor_user_id=1, source="test")

    row = ai.get_prompt_injection(77)
    assert row["learning_mode"] == "light"
    assert "MASTER" in row["effective_prompt"]
    assert "GUILD" in row["effective_prompt"]


def test_learning_off_skips_profile_and_facts(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = _make_store(tmp_path)
    ai = AIService(settings, store)

    ai.set_prompt_injection(guild_id=77, prompt_text="quiet", learning_mode="off", actor_user_id=1, source="test")
    msg = _stub_message(guild_id=77, user_id=2001, content="my name is Peter and I love bots")
    ai.capture_message(msg, touch=False)

    profiles = ai._ai_root().setdefault("profiles", {}).get("77", {})
    facts = ai._ai_root().setdefault("memory_facts", {}).get("77", {})
    assert profiles == {}
    assert facts == {}


def test_decide_chat_action_can_choose_direct_reply(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = _make_store(tmp_path)
    ai = AIService(settings, store)

    msg = _stub_message(guild_id=77, user_id=2001, content="mandy can you help with this?")
    action = ai.decide_chat_action(msg, bot_user_id=9999)
    assert action.action in {"direct_reply", "react", "ignore", "reply"}
    assert action.action == "direct_reply"


def test_style_summary_collects_slang_signal(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = _make_store(tmp_path)
    ai = AIService(settings, store)

    for text in ("fr bro that is lit", "ngl this is fire fr", "*walks in* bro fr", "idk bro fr"):
        ai.capture_message(_stub_message(guild_id=88, user_id=3001, content=text), touch=False)

    summary = ai.guild_style_summary(88)
    assert "slang" in summary or "roleplay" in summary or "first-person" in summary
