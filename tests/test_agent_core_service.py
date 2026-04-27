from __future__ import annotations

import asyncio
from pathlib import Path

from mandy_v1.bot import AUTONOMY_DESTRUCTIVE_ACTIONS, AUTONOMY_RESTRICTED_EXTERNAL_ACTIONS, MandyBot
from mandy_v1.config import Settings
from mandy_v1.services.agent_core_service import AgentCoreService
from mandy_v1.storage import MessagePackStore


def _settings(tmp_path: Path) -> Settings:
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


def _store(tmp_path: Path) -> MessagePackStore:
    store = MessagePackStore(tmp_path / "state.msgpack")
    asyncio.run(store.load())
    return store


def test_agent_core_records_low_risk_verdict(tmp_path: Path) -> None:
    store = _store(tmp_path)
    core = AgentCoreService(store)

    verdict = core.evaluate_action(
        guild_id=77,
        payload={"action": "rename_channel", "target": 55},
        base_allowed=True,
        base_reason="ok",
        approval_required=False,
        destructive_actions=AUTONOMY_DESTRUCTIVE_ACTIONS,
        external_actions=AUTONOMY_RESTRICTED_EXTERNAL_ACTIONS,
    )

    assert verdict.allowed is True
    assert verdict.risk == "low"
    assert store.data["agent_core"]["last_verdict"]["action"] == "rename_channel"
    assert store.data["agent_core"]["risk_counts"]["low"] == 1


def test_agent_core_blocks_high_risk_without_approval(tmp_path: Path) -> None:
    store = _store(tmp_path)
    core = AgentCoreService(store)

    verdict = core.evaluate_action(
        guild_id=77,
        payload={"action": "kick_member", "target": 42},
        base_allowed=True,
        base_reason="ok",
        approval_required=False,
        destructive_actions=AUTONOMY_DESTRUCTIVE_ACTIONS,
        external_actions=AUTONOMY_RESTRICTED_EXTERNAL_ACTIONS,
    )

    assert verdict.allowed is False
    assert verdict.reason == "high_risk_requires_approval"
    assert store.data["agent_core"]["risk_counts"]["blocked"] == 1


def test_bot_autonomy_check_uses_agent_core(tmp_path: Path) -> None:
    bot = MandyBot(_settings(tmp_path))
    asyncio.run(bot.store.load())
    root = bot._autonomy_policy_root()  # noqa: SLF001
    root["mode"] = "god"
    root["require_approval"] = False

    allowed, reason = bot._is_autonomous_action_allowed(77, {"action": "kick_member", "target": 42})  # noqa: SLF001

    assert allowed is False
    assert reason == "high_risk_requires_approval"
    assert bot.agent_core.root()["last_verdict"]["action"] == "kick_member"


def test_agent_core_prompt_block_mentions_control_shell(tmp_path: Path) -> None:
    core = AgentCoreService(_store(tmp_path))
    block = core.prompt_block()

    assert "AGENT CONTROL SHELL" in block
    assert "typed tools" in block
