from __future__ import annotations

from typing import Any

import discord

from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore


DEFAULT_LAYOUT: dict[str, list[str]] = {
    "WELCOME": ["rules", "announcements", "guest-briefing", "manual-for-living"],
    "OPERATIONS": ["console", "requests", "reports", "diagnostics"],
    "SATELLITES": [],
    "GUEST ACCESS": ["guest-chat", "guest-feedback", "quarantine"],
    "ENGINEERING": ["system-log", "audit-log", "debug-log", "mirror-log", "data-lab", "dm-bridges"],
    "GOD CORE": ["admin-chat", "server-management", "layout-control", "blueprint-export", "incident-room"],
}

DEFAULT_TOPICS: dict[str, str] = {
    "rules": "Mandy v1 rules and operational boundaries.",
    "announcements": "System announcements and operator notices.",
    "guest-briefing": "Guest onboarding guidance.",
    "manual-for-living": "Published Mandy v1 operator manual.",
    "console": "Live control-plane console updates.",
    "requests": "Operator requests and command coordination.",
    "reports": "Incident and investigation reports.",
    "diagnostics": "Minimal system health and setup diagnostics.",
    "guest-chat": "Guest-only discussion area.",
    "guest-feedback": "Guest feedback intake.",
    "quarantine": "Restricted quarantine area.",
    "system-log": "System-level log feed.",
    "audit-log": "Audit trail for privileged actions.",
    "debug-log": "Debug event feed.",
    "mirror-log": "Mirror sync and delivery logs.",
    "data-lab": "Data review and analysis workspace.",
    "dm-bridges": "DM bridge home and staff relay operations.",
    "admin-chat": "Admin-only control room chat.",
    "server-management": "Cross-server management operations.",
    "layout-control": "Layout and provisioning control surface.",
    "blueprint-export": "Blueprint snapshots and export notes.",
    "incident-room": "High-priority incident handling.",
}

DEFAULT_PINS: dict[str, str] = {
    "rules": "<!--PIN:rules-->\nMandy v1 is a control plane. Follow SOC rules and Discord ToS.",
    "console": "<!--PIN:console-->\nOperational console. Keep this channel signal-only.",
    "requests": "<!--PIN:requests-->\nUse this channel for operator requests and command intent.",
    "diagnostics": "<!--PIN:diagnostics-->\nDiagnostics panel is maintained by Mandy v1 setup.",
    "admin-chat": "<!--PIN:admin-chat-->\nAdmin coordination channel. Use for privileged decisions.",
    "manual-for-living": "<!--PIN:manual-for-living-->\nLatest operator manual publication target.",
}


class AdminLayoutService:
    def __init__(self, store: MessagePackStore, logger: LoggerService) -> None:
        self.store = store
        self.logger = logger

    async def ensure(self, guild: discord.Guild) -> dict[str, Any]:
        roles = await self._ensure_roles(guild)
        layout = self._layout_map()
        topics = self._topic_map()
        pins = self._pin_map()
        created_categories = 0
        created_channels = 0
        ensured_channels: dict[str, discord.TextChannel] = {}

        for category_name, channel_names in layout.items():
            category, was_created = await self._ensure_category(guild, category_name)
            if was_created:
                created_categories += 1
            await self._apply_category_permissions(category, category_name, roles, guild)
            for channel_name in channel_names:
                channel, ch_created = await self._ensure_text_channel(guild, category, channel_name, topics.get(channel_name, ""))
                if ch_created:
                    created_channels += 1
                ensured_channels[channel_name] = channel

        for channel_name, pin_text in pins.items():
            channel = ensured_channels.get(channel_name) or discord.utils.get(guild.text_channels, name=channel_name)
            if channel:
                await self._ensure_pin(channel, pin_text)

        self.logger.log(
            "admin.layout_ensured",
            guild_id=guild.id,
            created_categories=created_categories,
            created_channels=created_channels,
        )
        return {"created_categories": created_categories, "created_channels": created_channels}

    async def _ensure_roles(self, guild: discord.Guild) -> dict[str, discord.Role]:
        role_names = ("ACCESS:Guest", "ACCESS:Member", "ACCESS:Engineer", "ACCESS:Admin", "ACCESS:SOC")
        roles: dict[str, discord.Role] = {}
        for role_name in role_names:
            role = discord.utils.get(guild.roles, name=role_name)
            if role is None:
                role = await guild.create_role(name=role_name, reason="Mandy v1 Admin Hub role setup")
            roles[role_name] = role
        return roles

    async def _ensure_category(self, guild: discord.Guild, category_name: str) -> tuple[discord.CategoryChannel, bool]:
        category = discord.utils.get(guild.categories, name=category_name)
        if category is not None:
            return category, False
        category = await guild.create_category(category_name, reason="Mandy v1 Admin Hub layout")
        return category, True

    async def _ensure_text_channel(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        channel_name: str,
        topic: str,
    ) -> tuple[discord.TextChannel, bool]:
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        created = False
        if channel is None:
            channel = await guild.create_text_channel(channel_name, category=category, topic=topic or None, reason="Mandy v1 Admin Hub layout")
            created = True
        else:
            needs_edit = channel.category_id != category.id or (topic and channel.topic != topic)
            if needs_edit:
                await channel.edit(category=category, topic=topic or channel.topic, reason="Mandy v1 Admin Hub sync")
        return channel, created

    async def _apply_category_permissions(
        self,
        category: discord.CategoryChannel,
        category_name: str,
        roles: dict[str, discord.Role],
        guild: discord.Guild,
    ) -> None:
        everyone = guild.default_role
        if category_name == "ENGINEERING":
            await category.set_permissions(everyone, view_channel=False)
            await category.set_permissions(roles["ACCESS:Engineer"], view_channel=True)
            await category.set_permissions(roles["ACCESS:Admin"], view_channel=True)
            await category.set_permissions(roles["ACCESS:SOC"], view_channel=True)
        elif category_name == "GOD CORE":
            await category.set_permissions(everyone, view_channel=False)
            await category.set_permissions(roles["ACCESS:Admin"], view_channel=True)
            await category.set_permissions(roles["ACCESS:SOC"], view_channel=True)
        elif category_name == "SATELLITES":
            await category.set_permissions(everyone, view_channel=False)
            await category.set_permissions(roles["ACCESS:SOC"], view_channel=True)
            await category.set_permissions(roles["ACCESS:Admin"], view_channel=True)
        else:
            await category.set_permissions(everyone, view_channel=False)
            await category.set_permissions(roles["ACCESS:Guest"], view_channel=True)
            await category.set_permissions(roles["ACCESS:Member"], view_channel=True)
            await category.set_permissions(roles["ACCESS:Admin"], view_channel=True)
            await category.set_permissions(roles["ACCESS:SOC"], view_channel=True)

    async def _ensure_pin(self, channel: discord.TextChannel, content: str) -> None:
        signature = content.splitlines()[0].strip() if content.strip() else ""
        if not signature:
            return
        try:
            pins = await channel.pins()
        except discord.HTTPException:
            return
        for pin in pins:
            first = (pin.content.splitlines()[0].strip() if pin.content else "")
            if first == signature:
                if pin.content != content:
                    await pin.edit(content=content)
                return
        msg = await channel.send(content)
        await msg.pin(reason="Mandy v1 pinned panel sync")

    def _layout_map(self) -> dict[str, list[str]]:
        config = self.store.data.setdefault("layout", {})
        default_categories = {key: list(value) for key, value in DEFAULT_LAYOUT.items()}
        categories = config.setdefault("categories", default_categories)
        self.store.touch()
        return categories

    def _topic_map(self) -> dict[str, str]:
        topics = self.store.data.setdefault("channel_topics", DEFAULT_TOPICS.copy())
        self.store.touch()
        return topics

    def _pin_map(self) -> dict[str, str]:
        pins = self.store.data.setdefault("pinned_text", DEFAULT_PINS.copy())
        self.store.touch()
        return pins
