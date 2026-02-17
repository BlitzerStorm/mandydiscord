from __future__ import annotations

import discord

from mandy_v1.config import Settings
from mandy_v1.storage import MessagePackStore

SUPER_USER_ID = 741470965359443970


class SocService:
    def __init__(self, settings: Settings, store: MessagePackStore) -> None:
        self.settings = settings
        self.store = store

    def get_tier(self, member: discord.abc.User | discord.Member) -> int:
        if member.id == SUPER_USER_ID:
            return 100
        user_tier = int(self.store.data["soc"]["user_tiers"].get(str(member.id), 0))
        if isinstance(member, discord.Member):
            role_tiers = self.store.data["soc"]["role_tiers"]
            role_tier = max((int(role_tiers.get(role.name, 0)) for role in member.roles), default=0)
        else:
            role_tier = 0
        return max(user_tier, role_tier)

    def can_run(self, member: discord.abc.User | discord.Member, min_tier: int) -> bool:
        return self.get_tier(member) >= min_tier
