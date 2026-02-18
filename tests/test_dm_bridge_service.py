from __future__ import annotations

import asyncio
from pathlib import Path

from mandy_v1.config import Settings
from mandy_v1.services.dm_bridge_service import DMBridgeService
from mandy_v1.services.logger_service import LoggerService
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


def _make_service(tmp_path: Path) -> DMBridgeService:
    settings = _make_settings(tmp_path)
    store = MessagePackStore(settings.store_path)
    asyncio.run(store.load())
    logger = LoggerService(store)
    return DMBridgeService(settings, store, logger)


def test_bridge_row_defaults_and_toggle_state(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    row = service.bridge_row(1001, create=True)
    assert isinstance(row, dict)
    assert row["active"] is True
    assert row["ai_enabled"] is True
    assert row["history_message_ids"] == []

    assert service.set_active(1001, False) is False
    assert service.toggle_ai_enabled(1001) is False

    row_after = service.bridge_row(1001, create=False)
    assert isinstance(row_after, dict)
    assert row_after["active"] is False
    assert row_after["ai_enabled"] is False


def test_parse_user_id_from_bridge_channel_name(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    assert service.parse_user_id_from_channel_name("dm-123456") == 123456
    assert service.parse_user_id_from_channel_name("dm-abc") is None
    assert service.parse_user_id_from_channel_name("debug-log") is None


def test_history_snapshot_normalizes_ids_and_reason(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    service.set_history_snapshot(
        4242,
        message_ids=[1, 0, -5, 99],  # type: ignore[list-item]
        history_count=8,
        reason="manual.refresh",
    )
    row = service.bridge_row(4242, create=False)
    assert isinstance(row, dict)
    assert row["history_message_ids"] == [1, 99]
    assert row["history_count"] == 8
    assert row["last_refresh_reason"] == "manual.refresh"
