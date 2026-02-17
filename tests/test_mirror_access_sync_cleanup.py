from __future__ import annotations

import asyncio
from pathlib import Path

from mandy_v1.config import Settings
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.services.mirror_service import MirrorService
from mandy_v1.storage import MessagePackStore


class StubRole:
    def __init__(self, rid: int, name: str) -> None:
        self.id = rid
        self.name = name


class StubGuild:
    def __init__(self, gid: int, *, roles: list[StubRole] | None = None, present_members: set[int] | None = None) -> None:
        self.id = gid
        self.roles = list(roles or [])
        self._present_members = set(present_members or set())

    def get_member(self, user_id: int):
        if user_id in self._present_members:
            return object()
        return None


class StubMember:
    def __init__(self, uid: int, guild: StubGuild, roles: list[StubRole]) -> None:
        self.id = uid
        self.guild = guild
        self.roles = list(roles)
        self.bot = False

    async def add_roles(self, *roles: StubRole, reason: str | None = None) -> None:
        for role in roles:
            if role not in self.roles:
                self.roles.append(role)

    async def remove_roles(self, *roles: StubRole, reason: str | None = None) -> None:
        for role in roles:
            if role in self.roles:
                self.roles.remove(role)


class StubBot:
    def __init__(self, guilds: dict[int, StubGuild]) -> None:
        self._guilds = guilds

    def get_guild(self, guild_id: int):
        return self._guilds.get(guild_id)


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        discord_token="token",
        admin_guild_id=1,
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


def test_sync_admin_member_access_removes_stale_soc_server_roles(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = _make_store(tmp_path)
    logger = LoggerService(store)
    service = MirrorService(settings, store, logger)

    keep_role = StubRole(201, "SOC:SERVER:2")
    stale_role = StubRole(202, "SOC:SERVER:3")
    baseline_role = StubRole(203, "ACCESS:Member")

    admin_guild = StubGuild(1, roles=[keep_role, stale_role, baseline_role])
    satellite_2 = StubGuild(2, present_members=set())
    bot = StubBot({1: admin_guild, 2: satellite_2})

    member = StubMember(uid=55, guild=admin_guild, roles=[keep_role, stale_role, baseline_role])
    store.data["mirrors"]["servers"] = {"2": {"mirror_feed_id": 1, "debug_channel_id": 2, "category_id": 3}}

    asyncio.run(service.sync_admin_member_access(bot, member, bypass_user_ids=set()))

    names = {role.name for role in member.roles}
    assert "ACCESS:Member" in names
    assert "SOC:SERVER:2" not in names
    assert "SOC:SERVER:3" not in names
