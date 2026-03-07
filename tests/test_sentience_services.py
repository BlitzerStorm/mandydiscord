from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from mandy_v1.config import Settings
from mandy_v1.services.ai_service import AIService
from mandy_v1.services.culture_service import CultureService
from mandy_v1.services.emotion_service import EmotionService
from mandy_v1.services.episodic_memory_service import EpisodicMemoryService
from mandy_v1.services.identity_service import IdentityService
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.services.persona_service import PersonaService
from mandy_v1.services.server_control_service import ServerControlService
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


def _message(*, guild_id: int, channel_id: int, user_id: int, content: str) -> object:
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


def test_emotion_shift_transitions(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    logger = LoggerService(store)
    emotion = EmotionService(store, logger)

    mood = emotion.shift("burst_spam", 0.3)
    assert mood["state"] == "irritated"
    assert float(mood["intensity"]) > 0.5


def test_episodic_record_and_search(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    logger = LoggerService(store)
    episodic = EpisodicMemoryService(store, logger)

    episode = asyncio.run(
        episodic.record(
            88,
            55,
            ["alice", "bob"],
            [
                {"author": "alice", "text": "we kept talking about gaming and late night drama"},
                {"author": "bob", "text": "the gaming server drama is still funny"},
            ],
        )
    )

    assert episode is not None
    results = episodic.search(88, "gaming drama", limit=2)
    assert results
    assert "summary" in results[0]


def test_persona_profile_updates_and_slang(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    logger = LoggerService(store)
    persona = PersonaService(store, logger)

    msg = _message(
        guild_id=77,
        channel_id=12,
        user_id=2001,
        content="deadass i never say this but honestly gaming drama has me spiraling a little",
    )
    for _ in range(3):
        asyncio.run(persona.update_profile(2001, msg))

    row = persona.root()["2001"]
    assert row["communication_style"] in {"casual", "chaotic", "playful", "dry", "intense", "formal"}
    assert float(row["avg_message_length"]) > 0
    assert row["topics_they_care_about"]
    assert int(row["absorbed_slang"].get("deadass", 0)) >= 3


def test_attention_score_stays_bounded(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = _make_store(tmp_path)
    logger = LoggerService(store)
    ai = AIService(settings, store)
    emotion = EmotionService(store, logger)
    identity = IdentityService(store, logger)
    episodic = EpisodicMemoryService(store, logger)
    persona = PersonaService(store, logger)
    culture = CultureService(store, logger)
    ai.attach_context_services(
        emotion=emotion,
        identity=identity,
        episodic=episodic,
        personas=persona,
        culture=culture,
    )

    low = _message(guild_id=10, channel_id=1, user_id=1, content="ok")
    low_score = ai.compute_attention_score(low, bot_user_id=9999)
    assert 0.0 <= low_score <= 1.0

    row = persona.root().setdefault("2", persona._profile("2"))
    row["relationship_depth"] = 1.0
    emotion.shift("interest_keyword_match", 0.3)
    emotion.shift("new_server_joined", 0.5)
    asyncio.run(episodic.record(10, 1, ["user-2"], [{"author": "user-2", "text": "social dynamics and patterns matter"}]))
    high = _message(guild_id=10, channel_id=1, user_id=2, content="mandy what do you think about social dynamics?")
    high.mentions = [SimpleNamespace(id=9999)]
    high_score = ai.compute_attention_score(high, bot_user_id=9999)
    assert 0.0 <= high_score <= 1.0
    assert high_score == 1.0


def test_culture_calibration_completes_after_50_messages(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    logger = LoggerService(store)
    culture = CultureService(store, logger)
    guild = SimpleNamespace(id=90, name="chaos-room")

    for _ in range(50):
        asyncio.run(culture.observe(guild, _message(guild_id=90, channel_id=4, user_id=1, content="lmao the mod incident again fr")))

    row = culture.root()["90"]
    assert bool(row["calibration_complete"]) is True
    assert int(row["messages_observed"]) >= 50


class _DummyResponse:
    status = 403
    reason = "Forbidden"
    text = ""


def test_server_control_permission_failures(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = _make_store(tmp_path)
    logger = LoggerService(store)
    service = ServerControlService(settings, store, logger)
    exc = discord.Forbidden(_DummyResponse(), "forbidden")

    class FakeGuild:
        id = 55

        async def create_text_channel(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise exc

    class FakeMember:
        id = 9

        async def edit(self, **kwargs):  # noqa: ANN003
            raise exc

    guild = FakeGuild()
    member = FakeMember()

    created = asyncio.run(service.create_channel(guild, "fail-channel"))
    nicked = asyncio.run(service.nickname_member(guild, member, "fail"))

    assert created is None
    assert nicked is False
