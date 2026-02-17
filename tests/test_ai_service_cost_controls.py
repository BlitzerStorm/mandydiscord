from __future__ import annotations

import asyncio
from pathlib import Path

from mandy_v1.config import Settings
from mandy_v1.services.ai_service import AIService
from mandy_v1.storage import MessagePackStore


class StubAIService(AIService):
    def __init__(self, settings: Settings, store: MessagePackStore) -> None:
        super().__init__(settings, store)
        self.calls = 0
        self.should_fail = False

    def _model_candidates(self) -> list[str]:
        return ["stub-model"]

    async def _chat_completion(
        self,
        messages,
        max_tokens: int = 180,
        temperature: float = 0.7,
        *,
        api_key: str,
        model: str,
    ) -> str:
        self.calls += 1
        if self.should_fail:
            raise RuntimeError("forced failure")
        return f"ok-{self.calls}"


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        discord_token="token",
        admin_guild_id=123,
        god_user_id=741470965359443970,
        command_prefix="!",
        store_path=tmp_path / "state.msgpack",
        alibaba_api_key="fake-key",
        alibaba_base_url="https://example.invalid/v1",
        alibaba_model="stub-model",
    )


def _make_store(tmp_path: Path) -> MessagePackStore:
    store = MessagePackStore(tmp_path / "state.msgpack")
    asyncio.run(store.load())
    return store


def test_complete_text_uses_cache(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = _make_store(tmp_path)
    ai = StubAIService(settings, store)

    first = asyncio.run(
        ai.complete_text(
            system_prompt="sys",
            user_prompt="hello world",
            max_tokens=40,
            temperature=0.2,
            cache_ttl_sec=120,
        )
    )
    second = asyncio.run(
        ai.complete_text(
            system_prompt="sys",
            user_prompt="hello world",
            max_tokens=40,
            temperature=0.2,
            cache_ttl_sec=120,
        )
    )

    assert first == "ok-1"
    assert second == "ok-1"
    assert ai.calls == 1


def test_api_cooldown_short_circuits_calls(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = _make_store(tmp_path)
    ai = StubAIService(settings, store)
    ai.should_fail = True

    _ = asyncio.run(ai.complete_text(system_prompt="s", user_prompt="u", cache_ttl_sec=0))
    _ = asyncio.run(ai.complete_text(system_prompt="s", user_prompt="u2", cache_ttl_sec=0))
    calls_after_two_failures = ai.calls
    _ = asyncio.run(ai.complete_text(system_prompt="s", user_prompt="u3", cache_ttl_sec=0))

    assert calls_after_two_failures == 2
    assert ai.calls == 2


def test_prompt_clamping_trims_large_input(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = _make_store(tmp_path)
    ai = StubAIService(settings, store)

    huge = "x" * 9000
    trimmed = ai._clamp_prompt(huge, limit=2000)
    assert len(trimmed) <= 2200
    assert "truncated for token budget" in trimmed
