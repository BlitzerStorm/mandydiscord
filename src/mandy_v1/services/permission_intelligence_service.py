from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord


CAPABILITY_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "talk": ("view_channel", "send_messages"),
    "read_history": ("view_channel", "read_message_history"),
    "manage_nicknames": ("manage_nicknames",),
    "manage_roles": ("manage_roles",),
    "manage_channels": ("manage_channels",),
    "create_invites": ("create_instant_invite",),
    "moderate_members": ("moderate_members",),
    "manage_messages": ("manage_messages", "read_message_history"),
}


class PermissionIntelligenceService:
    def __init__(self, store: Any, logger: Any | None = None) -> None:
        self.store = store
        self.logger = logger

    def root(self) -> dict[str, Any]:
        root = self.store.data.setdefault("permission_intelligence", {})
        root.setdefault("guilds", {})
        root.setdefault("requests", [])
        root.setdefault("voice_policy", {"story_mode": False, "ambient_chat": True, "ambient_threshold": 0.72})
        return root

    def voice_policy(self) -> dict[str, Any]:
        policy = self.root().setdefault("voice_policy", {})
        if not isinstance(policy, dict):
            self.root()["voice_policy"] = {"story_mode": False, "ambient_chat": True, "ambient_threshold": 0.72}
            policy = self.root()["voice_policy"]
        policy.setdefault("story_mode", False)
        policy.setdefault("ambient_chat", True)
        policy.setdefault("ambient_threshold", 0.72)
        return policy

    def set_voice_policy(self, *, story_mode: bool | None = None, ambient_chat: bool | None = None) -> dict[str, Any]:
        policy = self.voice_policy()
        if story_mode is not None:
            policy["story_mode"] = bool(story_mode)
        if ambient_chat is not None:
            policy["ambient_chat"] = bool(ambient_chat)
        self._touch()
        return dict(policy)

    def prompt_block(self, guild_id: int) -> str:
        row = self.guild_snapshot(guild_id)
        policy = self.voice_policy()
        missing = row.get("missing_capabilities", [])
        authorities = row.get("authorities", [])
        return (
            "[PERMISSION INTELLIGENCE]\n"
            f"Story/lore voice enabled: {bool(policy.get('story_mode', False))}\n"
            f"Ambient chat enabled: {bool(policy.get('ambient_chat', True))}\n"
            f"Missing capabilities: {', '.join(str(x) for x in missing[:8]) or 'unknown/not scanned'}\n"
            f"Likely authorities: {', '.join(str(x.get('label', x.get('id', ''))) for x in authorities[:5] if isinstance(x, dict)) or 'unknown'}\n"
            "If blocked, ask the best authority for the exact missing permission instead of claiming you can bypass it."
        )

    def guild_snapshot(self, guild_id: int) -> dict[str, Any]:
        guilds = self.root().setdefault("guilds", {})
        if not isinstance(guilds, dict):
            self.root()["guilds"] = {}
            guilds = self.root()["guilds"]
        row = guilds.get(str(int(guild_id)))
        return row if isinstance(row, dict) else {}

    def scan_guild(self, guild: discord.Guild, bot_member: discord.Member | None = None) -> dict[str, Any]:
        me = bot_member or getattr(guild, "me", None)
        guild_perms = getattr(me, "guild_permissions", None)
        capabilities: dict[str, bool] = {}
        missing: list[str] = []
        for capability, perm_names in CAPABILITY_PERMISSIONS.items():
            ok = all(bool(getattr(guild_perms, name, False)) for name in perm_names)
            capabilities[capability] = ok
            if not ok:
                missing.append(capability)

        channels: list[dict[str, Any]] = []
        for channel in list(getattr(guild, "text_channels", []) or [])[:80]:
            perms = channel.permissions_for(me) if me is not None and hasattr(channel, "permissions_for") else None
            channels.append(
                {
                    "id": int(getattr(channel, "id", 0) or 0),
                    "name": str(getattr(channel, "name", ""))[:80],
                    "view": bool(getattr(perms, "view_channel", False)),
                    "send": bool(getattr(perms, "send_messages", False)),
                    "history": bool(getattr(perms, "read_message_history", False)),
                    "invite": bool(getattr(perms, "create_instant_invite", False)),
                }
            )

        authorities = self.resolve_authorities(guild)
        row = {
            "guild_id": int(getattr(guild, "id", 0) or 0),
            "guild_name": str(getattr(guild, "name", ""))[:120],
            "owner_id": int(getattr(guild, "owner_id", 0) or 0),
            "capabilities": capabilities,
            "missing_capabilities": missing,
            "authorities": authorities,
            "channels": channels,
            "scanned_ts": datetime.now(tz=timezone.utc).isoformat(),
        }
        self.root().setdefault("guilds", {})[str(row["guild_id"])] = row
        self._touch()
        return row

    def resolve_authorities(self, guild: discord.Guild) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        owner_id = int(getattr(guild, "owner_id", 0) or 0)
        if owner_id > 0:
            owner = guild.get_member(owner_id) if hasattr(guild, "get_member") else None
            rows.append(
                {
                    "id": owner_id,
                    "label": f"owner:{getattr(owner, 'display_name', owner_id)}",
                    "reason": "server_owner",
                    "score": 1000,
                }
            )
        for member in list(getattr(guild, "members", []) or [])[:500]:
            if bool(getattr(member, "bot", False)):
                continue
            perms = getattr(member, "guild_permissions", None)
            roles = [str(getattr(role, "name", "")) for role in getattr(member, "roles", []) or []]
            score = 0
            reasons: list[str] = []
            if bool(getattr(perms, "administrator", False)):
                score += 80
                reasons.append("administrator")
            if bool(getattr(perms, "manage_guild", False)):
                score += 55
                reasons.append("manage_server")
            if bool(getattr(perms, "manage_roles", False)):
                score += 35
                reasons.append("manage_roles")
            if any(role in {"ACCESS:SOC", "ACCESS:Admin"} for role in roles):
                score += 50
                reasons.append("admin_hub_role")
            if score <= 0:
                continue
            rows.append(
                {
                    "id": int(getattr(member, "id", 0) or 0),
                    "label": str(getattr(member, "display_name", getattr(member, "name", member.id)))[:80],
                    "reason": ",".join(reasons),
                    "score": score,
                }
            )
        deduped: dict[int, dict[str, Any]] = {}
        for row in rows:
            uid = int(row.get("id", 0) or 0)
            if uid <= 0:
                continue
            previous = deduped.get(uid)
            if previous is None or int(row.get("score", 0) or 0) > int(previous.get("score", 0) or 0):
                deduped[uid] = row
        return sorted(deduped.values(), key=lambda item: int(item.get("score", 0) or 0), reverse=True)[:12]

    def record_permission_request(
        self,
        *,
        guild_id: int,
        capability: str,
        requester_id: int,
        target_user_id: int,
        reason: str,
    ) -> dict[str, Any]:
        requests = self.root().setdefault("requests", [])
        if not isinstance(requests, list):
            self.root()["requests"] = []
            requests = self.root()["requests"]
        row = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "guild_id": int(guild_id),
            "capability": str(capability)[:80],
            "requester_id": int(requester_id),
            "target_user_id": int(target_user_id),
            "reason": str(reason)[:240],
            "status": "sent",
        }
        requests.append(row)
        if len(requests) > 500:
            del requests[: len(requests) - 500]
        self._touch()
        return row

    def _touch(self) -> None:
        if hasattr(self.store, "touch"):
            self.store.touch()
