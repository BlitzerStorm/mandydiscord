from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from mandy_v1.bot import MandyBot
from mandy_v1.config import Settings
from mandy_v1.services.permission_intelligence_service import PermissionIntelligenceService
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


def _perms(**kwargs):
    defaults = {
        "view_channel": True,
        "send_messages": True,
        "read_message_history": True,
        "create_instant_invite": False,
        "manage_nicknames": False,
        "manage_roles": False,
        "manage_channels": False,
        "moderate_members": False,
        "manage_messages": False,
        "administrator": False,
        "manage_guild": False,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_permission_scan_tracks_missing_and_authorities(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = PermissionIntelligenceService(store)
    owner = SimpleNamespace(id=10, display_name="Owner", bot=False, guild_permissions=_perms(administrator=True), roles=[])
    admin = SimpleNamespace(
        id=11,
        display_name="Admin",
        bot=False,
        guild_permissions=_perms(manage_guild=True, manage_roles=True),
        roles=[SimpleNamespace(name="ACCESS:Admin")],
    )
    me = SimpleNamespace(id=99, guild_permissions=_perms(send_messages=True))
    channel = SimpleNamespace(id=55, name="general", permissions_for=lambda member: _perms(send_messages=True))
    guild = SimpleNamespace(
        id=77,
        name="guild",
        owner_id=10,
        me=me,
        members=[owner, admin],
        text_channels=[channel],
        get_member=lambda uid: owner if uid == 10 else admin if uid == 11 else None,
    )

    row = service.scan_guild(guild)

    assert "manage_nicknames" in row["missing_capabilities"]
    assert row["authorities"][0]["id"] == 10
    assert row["channels"][0]["send"] is True


def test_voice_policy_defaults_disable_story_mode(tmp_path: Path) -> None:
    service = PermissionIntelligenceService(_store(tmp_path))

    assert service.voice_policy()["story_mode"] is False
    service.set_voice_policy(story_mode=True, ambient_chat=False)
    assert "Story/lore voice enabled: True" in service.prompt_block(77)
    assert "Ambient chat enabled: False" in service.prompt_block(77)


def test_permission_request_records_best_authority(tmp_path: Path) -> None:
    bot = MandyBot(_settings(tmp_path))
    asyncio.run(bot.store.load())
    owner = SimpleNamespace(id=10, display_name="Owner", bot=False, guild_permissions=_perms(administrator=True), roles=[])
    me = SimpleNamespace(id=99, guild_permissions=_perms())
    guild = SimpleNamespace(
        id=77,
        name="guild",
        owner_id=10,
        me=me,
        members=[owner],
        text_channels=[],
        get_member=lambda uid: owner if uid == 10 else None,
    )
    bot.get_guild = lambda gid: guild if int(gid) == 77 else None  # type: ignore[assignment]
    bot.get_user = lambda uid: None  # type: ignore[assignment]
    bot.fetch_user = lambda uid: None  # type: ignore[assignment]
    bot._resolve_god_admin_channel = lambda: None  # type: ignore[method-assign]
    bot._resolve_admin_debug_channel = lambda: None  # type: ignore[method-assign]

    ok, note = asyncio.run(
        bot._ask_authority_for_permission(  # noqa: SLF001
            guild_id=77,
            capability="manage_nicknames",
            requester_id=42,
            reason="test",
        )
    )

    assert ok is False
    requests = bot.store.data["permission_intelligence"]["requests"]
    assert requests[0]["target_user_id"] == 10
    assert "Could not DM" in note
