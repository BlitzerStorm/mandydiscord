import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple

import discord

from capability_registry import CapabilityRegistry
from clarify_ui import ConfirmActionView, IntentChoiceView, QuickNumberView
from resolver import (
    GuildIndexCache,
    ResolutionCandidate,
    parse_channel_id,
    parse_role_id,
    parse_user_id,
    pick_best,
    rank_channels,
    rank_members,
    rank_roles,
)


FILLER_WORDS = {
    "please",
    "pls",
    "hey",
    "yo",
    "can",
    "could",
    "would",
    "will",
    "you",
    "me",
    "the",
    "a",
    "an",
    "to",
    "for",
    "with",
    "and",
}

PRONOUNS = {"him", "her", "them", "he", "she", "they"}


@dataclass
class IntentSpec:
    name: str
    capability: Optional[str]
    keywords: List[str]
    patterns: List[Pattern]
    confirm: bool = False


@dataclass
class IntentMatch:
    spec: IntentSpec
    score: float
    match: Optional[re.Match]


@dataclass
class ActionPlan:
    capability: Optional[str]
    actions: List[Dict[str, Any]]
    args: Dict[str, Any]
    confidence: float
    confirm: bool
    summary: str
    local_action: Optional[str] = None
    clarify: Optional[Dict[str, Any]] = None


class ContextCache:
    def __init__(self, ttl_seconds: int = 600):
        self.ttl_seconds = int(ttl_seconds)
        self._data: Dict[Tuple[int, int], Dict[str, Any]] = {}

    def _key(self, guild_id: int, user_id: int) -> Tuple[int, int]:
        return int(guild_id or 0), int(user_id or 0)

    def get(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        key = self._key(guild_id, user_id)
        entry = self._data.get(key)
        if not entry:
            return {}
        if time.time() - float(entry.get("at", 0)) > self.ttl_seconds:
            self._data.pop(key, None)
            return {}
        return dict(entry.get("ctx", {}))

    def update(self, guild_id: int, user_id: int, updates: Dict[str, Any]) -> None:
        key = self._key(guild_id, user_id)
        ctx = self._data.get(key, {}).get("ctx", {})
        ctx.update(updates)
        self._data[key] = {"at": time.time(), "ctx": ctx}


class UniversalIntelligenceLayer:
    def __init__(
        self,
        bot: discord.Client,
        tools: Any,
        registry: Optional[CapabilityRegistry],
        execute_actions: Callable[..., Any],
        send_func: Callable[[discord.abc.Messageable, str], Any],
        is_god: Callable[[discord.User], Any],
        local_actions: Optional[Dict[str, Callable[[], str]]] = None,
    ):
        self.bot = bot
        self.tools = tools
        self.registry = registry or (CapabilityRegistry(tools) if tools else None)
        self.execute_actions = execute_actions
        self.send_func = send_func
        self.is_god = is_god
        self.local_actions = local_actions or {}
        self.context = ContextCache(ttl_seconds=600)
        self._index_cache = GuildIndexCache(ttl_seconds=120)
        self.pending: Dict[str, Dict[str, Any]] = {}
        self.intent_specs = self._build_intents()

    def _build_intents(self) -> List[IntentSpec]:
        def rx(pattern: str) -> Pattern:
            return re.compile(pattern, re.IGNORECASE)

        return [
            IntentSpec(
                name="send_dm",
                capability="send_dm",
                keywords=["dm", "message", "msg", "tell", "ping"],
                patterns=[
                    rx(r"^(?:dm|message|msg|tell|ping)\s+(?P<target>.+?)\s*(?:->|<-|:)\s*(?P<message>.+)$"),
                    rx(r"^(?:dm|message|msg|tell|ping)\s+(?P<target>.+?)\s+(?P<message>.+)$"),
                    rx(r"^(?P<target>.+?)\s*<-\s*(?P<message>.+)$"),
                ],
                confirm=True,
            ),
            IntentSpec(
                name="send_message",
                capability="send_message",
                keywords=["say", "send", "post", "announce"],
                patterns=[
                    rx(r"^(?:say|send|post)\s+(?:in\s+)?(?P<channel>.+?)\s*(?:->|:)\s*(?P<message>.+)$"),
                ],
                confirm=True,
            ),
            IntentSpec(
                name="mirror_create",
                capability="create_mirror",
                keywords=["mirror", "relay", "link", "sync"],
                patterns=[
                    rx(r"^(?:mirror|relay|link|sync)\s+(?P<src>.+?)\s*(?:to|->|and|with)\s+(?P<dest>.+)$"),
                ],
                confirm=True,
            ),
            IntentSpec(
                name="add_watcher",
                capability="add_watcher",
                keywords=["watch", "watcher", "monitor"],
                patterns=[
                    rx(r"^(?:watch|monitor|add\s+watcher)\s+(?P<target>.+?)\s+(?:after|every)\s+(?P<count>\d+)\s+(?:message|msg)s?\s*(?:say\s+)?(?P<message>.+)$"),
                    rx(r"^(?:watch|monitor)\s+(?P<target>.+?)\s+(?P<count>\d+)\s+(?:say\s+)?(?P<message>.+)$"),
                    rx(r"^(?:watch|monitor)\s+(?P<target>.+?)\s+say\s+(?P<message>.+)$"),
                ],
            ),
            IntentSpec(
                name="remove_watcher",
                capability="remove_watcher",
                keywords=["unwatch", "remove", "stop", "watcher"],
                patterns=[rx(r"^(?:remove|stop)\s+(?:watcher|watch|watching)\s+(?P<target>.+)$")],
            ),
            IntentSpec(
                name="list_watchers",
                capability="list_watchers",
                keywords=["watchers", "list", "show"],
                patterns=[rx(r"^(?:list|show)\s+watchers?$"), rx(r"^watchers?$")],
            ),
            IntentSpec(
                name="show_stats",
                capability="show_stats",
                keywords=["stats", "statistics", "logs", "activity"],
                patterns=[
                    rx(r"^(?:stats|statistics|logs)\s*(?P<scope>daily|weekly|monthly|yearly|rolling24|rolling-24h|rolling_24h|today|week|month|year)?\s*(?P<target>.+)?$"),
                    rx(r"^(?:my|me)\s+stats$"),
                ],
            ),
            IntentSpec(
                name="set_status",
                capability="set_bot_status",
                keywords=["status", "presence"],
                patterns=[rx(r"^(?:set\s+status|status)\s+(?P<state>online|idle|dnd|invisible)(?:\s+(?P<text>.+))?$")],
            ),
            IntentSpec(
                name="list_capabilities",
                capability="list_capabilities",
                keywords=["tools", "tool", "capabilities"],
                patterns=[rx(r"^(?:tools?|capabilities)$")],
            ),
            IntentSpec(
                name="show_health",
                capability=None,
                keywords=["health", "status"],
                patterns=[rx(r"^(?:health|bot\s+health|status)$")],
            ),
            IntentSpec(
                name="show_queue",
                capability=None,
                keywords=["queue", "jobs"],
                patterns=[rx(r"^(?:queue|jobs|pending)$")],
            ),
            IntentSpec(
                name="list_mirror_rules",
                capability="list_mirror_rules",
                keywords=["mirrors", "mirror", "rules"],
                patterns=[rx(r"^(?:list|show)\s+mirrors?$")],
            ),
            IntentSpec(
                name="disable_mirror_rule",
                capability="disable_mirror_rule",
                keywords=["disable", "mirror", "rule"],
                patterns=[rx(r"^(?:disable|remove)\s+mirror\s+(?P<rule>[a-z0-9:_-]+)$")],
                confirm=True,
            ),
        ]

    def _tokenize(self, text: str) -> List[str]:
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        return [t for t in tokens if t and t not in FILLER_WORDS]

    def _detect_intent(self, raw_text: str) -> Optional[IntentMatch]:
        cleaned = " ".join(raw_text.strip().split())
        tokens = set(self._tokenize(cleaned))
        matches: List[IntentMatch] = []
        for spec in self.intent_specs:
            best_match = None
            for pattern in spec.patterns:
                m = pattern.match(cleaned)
                if m:
                    best_match = m
                    break
            if best_match:
                matches.append(IntentMatch(spec=spec, score=1.0, match=best_match))
                continue
            if spec.keywords:
                keyword_hits = sum(1 for k in spec.keywords if k in tokens)
                score = keyword_hits / max(1, len(spec.keywords))
            else:
                score = 0.0
            if score > 0:
                matches.append(IntentMatch(spec=spec, score=score, match=None))
        if not matches:
            return None
        matches.sort(key=lambda m: m.score, reverse=True)
        top = matches[0]
        if len(matches) > 1 and (top.score - matches[1].score) < 0.12 and top.score < 0.9:
            return IntentMatch(spec=top.spec, score=0.0, match=None)
        if top.score < 0.45:
            return IntentMatch(spec=top.spec, score=0.0, match=None)
        return top

    async def process(
        self,
        user: discord.User,
        channel: discord.abc.Messageable,
        guild: Optional[discord.Guild],
        message: Optional[discord.Message],
        raw_text: str,
    ) -> bool:
        if not raw_text or not raw_text.strip():
            return False
        if not await self.is_god(user):
            await self.send_func(channel, "You're not a god.")
            return True
        if guild and getattr(channel, "id", 0):
            self.context.update(guild.id, user.id, {"current_channel_id": int(channel.id)})
        intent = self._detect_intent(raw_text)
        if not intent:
            return False
        if intent.score == 0.0:
            await self._prompt_intent_choice(user, channel, guild, raw_text)
            return True
        plan = await self._build_plan(intent, user, channel, guild, message, raw_text)
        if plan.clarify:
            await self._dispatch_clarify(user, channel, guild, plan)
            return True
        if plan.confirm:
            await self._dispatch_confirm(user, channel, guild, plan)
            return True
        await self._execute_plan(user, channel, guild, message, plan)
        return True

    async def _build_plan(
        self,
        intent: IntentMatch,
        user: discord.User,
        channel: discord.abc.Messageable,
        guild: Optional[discord.Guild],
        message: Optional[discord.Message],
        raw_text: str,
    ) -> ActionPlan:
        spec = intent.spec
        context = self.context.get(getattr(guild, "id", 0), user.id)
        if spec.name == "send_dm":
            return await self._plan_send_dm(intent, user, guild, raw_text, context)
        if spec.name == "send_message":
            return await self._plan_send_message(intent, user, guild, raw_text, context)
        if spec.name == "mirror_create":
            return await self._plan_mirror_create(intent, user, guild, raw_text, context)
        if spec.name == "add_watcher":
            return await self._plan_add_watcher(intent, user, guild, raw_text, context)
        if spec.name == "remove_watcher":
            return await self._plan_remove_watcher(intent, user, guild, raw_text, context)
        if spec.name == "list_watchers":
            return self._simple_plan(spec.capability, "List watchers.", intent.score)
        if spec.name == "show_stats":
            return await self._plan_show_stats(intent, user, guild, raw_text, context)
        if spec.name == "set_status":
            return await self._plan_set_status(intent, raw_text, intent.match)
        if spec.name == "list_capabilities":
            return self._simple_plan(spec.capability, "List capabilities.", intent.score)
        if spec.name == "show_health":
            return ActionPlan(
                capability=None,
                actions=[],
                args={},
                confidence=intent.score,
                confirm=False,
                summary="Show health.",
                local_action="health",
            )
        if spec.name == "show_queue":
            return ActionPlan(
                capability=None,
                actions=[],
                args={},
                confidence=intent.score,
                confirm=False,
                summary="Show queue.",
                local_action="queue",
            )
        if spec.name == "list_mirror_rules":
            return self._simple_plan(spec.capability, "List mirror rules.", intent.score)
        if spec.name == "disable_mirror_rule":
            rule_id = ""
            if intent.match and intent.match.groupdict().get("rule"):
                rule_id = intent.match.group("rule").strip()
            if not rule_id:
                return ActionPlan(
                    capability=spec.capability,
                    actions=[],
                    args={},
                    confidence=intent.score,
                    confirm=False,
                    summary="Disable mirror rule.",
                    clarify={"kind": "text", "field": "rule_id", "prompt": "Which rule id should I disable?"},
                )
            return ActionPlan(
                capability=spec.capability,
                actions=[{"tool": spec.capability, "args": {"rule_id": rule_id}}],
                args={"rule_id": rule_id},
                confidence=intent.score,
                confirm=spec.confirm,
                summary=f"Disable mirror rule {rule_id}.",
            )
        return self._simple_plan(None, "Unhandled.", intent.score)

    def _simple_plan(self, capability: Optional[str], summary: str, confidence: float) -> ActionPlan:
        actions = [{"tool": capability, "args": {}}] if capability else []
        return ActionPlan(
            capability=capability,
            actions=actions,
            args={},
            confidence=confidence,
            confirm=False,
            summary=summary,
        )

    async def _plan_send_dm(
        self,
        intent: IntentMatch,
        user: discord.User,
        guild: Optional[discord.Guild],
        raw_text: str,
        context: Dict[str, Any],
    ) -> ActionPlan:
        target_text, message_text = self._extract_target_message(raw_text, intent.match)
        if not target_text:
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={},
                confidence=intent.score,
                confirm=False,
                summary="Send DM.",
                clarify={"kind": "text", "field": "target", "prompt": "Who should I DM?"},
            )
        if not message_text:
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={"target": target_text},
                confidence=intent.score,
                confirm=False,
                summary="Send DM.",
                clarify={"kind": "text", "field": "message", "prompt": "What message should I send?"},
            )

        targets = self._split_targets(target_text)
        resolved_ids, unresolved = await self._resolve_users(guild, user, targets, context)
        if unresolved:
            token, candidates = unresolved[0]
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={"targets": targets, "message": message_text, "resolved_ids": resolved_ids, "pending": unresolved},
                confidence=intent.score,
                confirm=False,
                summary="Resolve DM target.",
                clarify={
                    "kind": "user",
                    "field": "user_id",
                    "prompt": f"Which user did you mean for '{token}'?",
                    "options": candidates,
                },
            )

        actions = [{"tool": "send_dm", "args": {"user_id": uid, "text": message_text}} for uid in resolved_ids]
        summary = f"Send DM to {len(resolved_ids)} user(s)."
        confirm = intent.spec.confirm and len(resolved_ids) > 1
        return ActionPlan(
            capability=intent.spec.capability,
            actions=actions,
            args={"user_ids": resolved_ids, "text": message_text},
            confidence=intent.score,
            confirm=confirm,
            summary=summary,
        )

    async def _plan_send_message(
        self,
        intent: IntentMatch,
        user: discord.User,
        guild: Optional[discord.Guild],
        raw_text: str,
        context: Dict[str, Any],
    ) -> ActionPlan:
        channel_text, message_text = self._extract_target_message(raw_text, intent.match, field="channel")
        if not channel_text:
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={},
                confidence=intent.score,
                confirm=False,
                summary="Send message.",
                clarify={"kind": "text", "field": "channel", "prompt": "Which channel should I post in?"},
            )
        if not message_text:
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={"channel": channel_text},
                confidence=intent.score,
                confirm=False,
                summary="Send message.",
                clarify={"kind": "text", "field": "message", "prompt": "What message should I send?"},
            )

        channel_id, candidates = await self._resolve_channel(guild, channel_text, context)
        if not channel_id:
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={"channel": channel_text, "message": message_text},
                confidence=intent.score,
                confirm=False,
                summary="Resolve channel.",
                clarify={
                    "kind": "channel",
                    "field": "channel_id",
                    "prompt": f"Which channel did you mean for '{channel_text}'?",
                    "options": candidates,
                },
            )

        return ActionPlan(
            capability=intent.spec.capability,
            actions=[{"tool": "send_message", "args": {"channel_id": channel_id, "text": message_text}}],
            args={"channel_id": channel_id, "text": message_text},
            confidence=intent.score,
            confirm=intent.spec.confirm,
            summary=f"Send message to <#{channel_id}>.",
        )

    async def _plan_mirror_create(
        self,
        intent: IntentMatch,
        user: discord.User,
        guild: Optional[discord.Guild],
        raw_text: str,
        context: Dict[str, Any],
    ) -> ActionPlan:
        src_text = ""
        dst_text = ""
        if intent.match:
            src_text = intent.match.groupdict().get("src") or ""
            dst_text = intent.match.groupdict().get("dest") or ""
        if not src_text or not dst_text:
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={},
                confidence=intent.score,
                confirm=False,
                summary="Create mirror.",
                clarify={"kind": "text", "field": "mirror", "prompt": "Mirror which source channel to which target?"},
            )

        src_id, src_candidates = await self._resolve_channel(guild, src_text, context)
        if not src_id:
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={"src": src_text, "dest": dst_text},
                confidence=intent.score,
                confirm=False,
                summary="Resolve source channel.",
                clarify={
                    "kind": "channel",
                    "field": "source_channel_id",
                    "prompt": f"Which source channel did you mean for '{src_text}'?",
                    "options": src_candidates,
                },
            )

        dst_id, dst_candidates = await self._resolve_channel(guild, dst_text, context)
        if not dst_id:
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={"source_channel_id": src_id, "dest": dst_text},
                confidence=intent.score,
                confirm=False,
                summary="Resolve target channel.",
                clarify={
                    "kind": "channel",
                    "field": "target_channel_id",
                    "prompt": f"Which target channel did you mean for '{dst_text}'?",
                    "options": dst_candidates,
                },
            )

        return ActionPlan(
            capability=intent.spec.capability,
            actions=[{"tool": "create_mirror", "args": {"source_channel_id": src_id, "target_channel_id": dst_id}}],
            args={"source_channel_id": src_id, "target_channel_id": dst_id},
            confidence=intent.score,
            confirm=True,
            summary=f"Create mirror <#{src_id}> -> <#{dst_id}>.",
        )

    async def _plan_add_watcher(
        self,
        intent: IntentMatch,
        user: discord.User,
        guild: Optional[discord.Guild],
        raw_text: str,
        context: Dict[str, Any],
    ) -> ActionPlan:
        target_text = ""
        count_text = ""
        message_text = ""
        if intent.match:
            target_text = (intent.match.groupdict().get("target") or "").strip()
            count_text = (intent.match.groupdict().get("count") or "").strip()
            message_text = (intent.match.groupdict().get("message") or "").strip()
        if not target_text:
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={},
                confidence=intent.score,
                confirm=False,
                summary="Add watcher.",
                clarify={"kind": "text", "field": "target", "prompt": "Who should I watch?"},
            )
        target_id, candidates = await self._resolve_single_user(guild, user, target_text, context)
        if not target_id:
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={"target": target_text, "count": count_text, "message": message_text},
                confidence=intent.score,
                confirm=False,
                summary="Resolve watcher target.",
                clarify={
                    "kind": "user",
                    "field": "target_user_id",
                    "prompt": f"Which user did you mean for '{target_text}'?",
                    "options": candidates,
                },
            )
        count = int(count_text) if count_text.isdigit() else 0
        if not count:
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={"target_user_id": target_id, "message": message_text},
                confidence=intent.score,
                confirm=False,
                summary="Add watcher.",
                clarify={"kind": "number", "field": "count", "prompt": "After how many messages?"},
            )
        if not message_text:
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={"target_user_id": target_id, "count": count},
                confidence=intent.score,
                confirm=False,
                summary="Add watcher.",
                clarify={"kind": "text", "field": "message", "prompt": "What should I say?"},
            )

        return ActionPlan(
            capability=intent.spec.capability,
            actions=[{"tool": "add_watcher", "args": {"target_user_id": target_id, "count": count, "text": message_text}}],
            args={"target_user_id": target_id, "count": count, "text": message_text},
            confidence=intent.score,
            confirm=False,
            summary=f"Watch <@{target_id}> after {count} messages.",
        )

    async def _plan_remove_watcher(
        self,
        intent: IntentMatch,
        user: discord.User,
        guild: Optional[discord.Guild],
        raw_text: str,
        context: Dict[str, Any],
    ) -> ActionPlan:
        target_text = ""
        if intent.match:
            target_text = (intent.match.groupdict().get("target") or "").strip()
        if not target_text:
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={},
                confidence=intent.score,
                confirm=False,
                summary="Remove watcher.",
                clarify={"kind": "text", "field": "target", "prompt": "Who should I stop watching?"},
            )
        target_id, candidates = await self._resolve_single_user(guild, user, target_text, context)
        if not target_id:
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={"target": target_text},
                confidence=intent.score,
                confirm=False,
                summary="Resolve watcher target.",
                clarify={
                    "kind": "user",
                    "field": "target_user_id",
                    "prompt": f"Which user did you mean for '{target_text}'?",
                    "options": candidates,
                },
            )
        return ActionPlan(
            capability=intent.spec.capability,
            actions=[{"tool": "remove_watcher", "args": {"target_user_id": target_id}}],
            args={"target_user_id": target_id},
            confidence=intent.score,
            confirm=False,
            summary=f"Stop watching <@{target_id}>.",
        )

    async def _plan_show_stats(
        self,
        intent: IntentMatch,
        user: discord.User,
        guild: Optional[discord.Guild],
        raw_text: str,
        context: Dict[str, Any],
    ) -> ActionPlan:
        scope = "daily"
        target_text = ""
        if intent.match:
            scope = (intent.match.groupdict().get("scope") or "").strip() or scope
            target_text = (intent.match.groupdict().get("target") or "").strip()
        if scope in ("today", "day"):
            scope = "daily"
        if scope in ("week",):
            scope = "weekly"
        if scope in ("month",):
            scope = "monthly"
        if scope in ("year",):
            scope = "yearly"
        if scope in ("rolling-24h", "rolling_24h"):
            scope = "rolling24"
        if target_text in ("me", "my", "mystats") or raw_text.strip().lower() in ("mystats", "my stats"):
            target_text = "me"

        user_id = None
        if target_text:
            user_id, candidates = await self._resolve_single_user(guild, user, target_text, context)
            if not user_id:
                return ActionPlan(
                    capability=intent.spec.capability,
                    actions=[],
                    args={"scope": scope},
                    confidence=intent.score,
                    confirm=False,
                    summary="Resolve stats target.",
                    clarify={
                        "kind": "user",
                        "field": "user_id",
                        "prompt": f"Which user did you mean for '{target_text}'?",
                        "options": candidates,
                    },
                )

        args = {"scope": scope}
        if user_id:
            args["user_id"] = user_id
        return ActionPlan(
            capability=intent.spec.capability,
            actions=[{"tool": "show_stats", "args": args}],
            args=args,
            confidence=intent.score,
            confirm=False,
            summary="Show stats.",
        )

    async def _plan_set_status(self, intent: IntentMatch, raw_text: str, match: Optional[re.Match]) -> ActionPlan:
        state = ""
        text = ""
        if match:
            state = (match.groupdict().get("state") or "").strip()
            text = (match.groupdict().get("text") or "").strip()
        if not state:
            return ActionPlan(
                capability=intent.spec.capability,
                actions=[],
                args={},
                confidence=intent.score,
                confirm=False,
                summary="Set status.",
                clarify={"kind": "text", "field": "state", "prompt": "What status state? (online/idle/dnd/invisible)"},
            )
        return ActionPlan(
            capability=intent.spec.capability,
            actions=[{"tool": "set_bot_status", "args": {"state": state, "text": text}}],
            args={"state": state, "text": text},
            confidence=intent.score,
            confirm=False,
            summary=f"Set status to {state}.",
        )

    def _extract_target_message(
        self,
        raw_text: str,
        match: Optional[re.Match],
        field: str = "target",
    ) -> Tuple[str, str]:
        if match:
            target = (match.groupdict().get(field) or "").strip()
            message = (match.groupdict().get("message") or "").strip()
            return target, message
        parts = re.split(r"\s*(?:->|<-|:)\s*", raw_text, maxsplit=1)
        if len(parts) == 2:
            left, right = parts
            return left.strip(), right.strip()
        return raw_text.strip(), ""

    def _split_targets(self, text: str) -> List[str]:
        cleaned = re.sub(r"\s+and\s+", ",", text, flags=re.IGNORECASE)
        parts = [p.strip() for p in cleaned.split(",") if p.strip()]
        return parts

    async def _resolve_users(
        self,
        guild: Optional[discord.Guild],
        user: discord.User,
        targets: List[str],
        context: Dict[str, Any],
    ) -> Tuple[List[int], List[Tuple[str, List[ResolutionCandidate]]]]:
        resolved: List[int] = []
        unresolved: List[Tuple[str, List[ResolutionCandidate]]] = []
        index = self._index_cache.get(guild)
        for token in targets:
            uid = await self._resolve_pronoun(token, user, context)
            if uid:
                resolved.append(uid)
                continue
            direct = parse_user_id(token)
            if direct:
                resolved.append(direct)
                continue
            candidates = rank_members(guild, token, recent_ids=self._recent_user_ids(context), index=index)
            picked = pick_best(candidates)
            if picked:
                resolved.append(picked)
                continue
            unresolved.append((token, candidates))
        return resolved, unresolved

    async def _resolve_single_user(
        self,
        guild: Optional[discord.Guild],
        user: discord.User,
        token: str,
        context: Dict[str, Any],
    ) -> Tuple[Optional[int], List[ResolutionCandidate]]:
        uid = await self._resolve_pronoun(token, user, context)
        if uid:
            return uid, []
        direct = parse_user_id(token)
        if direct:
            return direct, []
        index = self._index_cache.get(guild)
        candidates = rank_members(guild, token, recent_ids=self._recent_user_ids(context), index=index)
        picked = pick_best(candidates)
        return picked, candidates

    async def _resolve_channel(
        self,
        guild: Optional[discord.Guild],
        token: str,
        context: Dict[str, Any],
    ) -> Tuple[Optional[int], List[ResolutionCandidate]]:
        lowered = (token or "").strip().lower()
        if lowered in ("here", "this", "current"):
            cid = context.get("current_channel_id") or context.get("last_channel_id")
            if cid:
                return int(cid), []
        direct = parse_channel_id(token)
        if direct:
            return direct, []
        index = self._index_cache.get(guild)
        candidates = rank_channels(guild, token, recent_ids=self._recent_channel_ids(context), index=index)
        picked = pick_best(candidates)
        return picked, candidates

    async def _resolve_role(
        self,
        guild: Optional[discord.Guild],
        token: str,
        context: Dict[str, Any],
    ) -> Tuple[Optional[int], List[ResolutionCandidate]]:
        direct = parse_role_id(token)
        if direct:
            return direct, []
        index = self._index_cache.get(guild)
        candidates = rank_roles(guild, token, recent_ids=self._recent_role_ids(context), index=index)
        picked = pick_best(candidates)
        return picked, candidates

    async def _resolve_pronoun(self, token: str, user: discord.User, context: Dict[str, Any]) -> Optional[int]:
        lowered = token.strip().lower()
        if lowered in ("me", "myself", "self", "i"):
            return int(user.id)
        if lowered in PRONOUNS:
            return int(context.get("last_user_id", 0) or 0) or None
        return None

    def _recent_user_ids(self, context: Dict[str, Any]) -> List[int]:
        uid = context.get("last_user_id")
        return [int(uid)] if uid else []

    def _recent_channel_ids(self, context: Dict[str, Any]) -> List[int]:
        cid = context.get("last_channel_id")
        return [int(cid)] if cid else []

    def _recent_role_ids(self, context: Dict[str, Any]) -> List[int]:
        rid = context.get("last_role_id")
        return [int(rid)] if rid else []

    async def _dispatch_clarify(
        self,
        user: discord.User,
        channel: discord.abc.Messageable,
        guild: Optional[discord.Guild],
        plan: ActionPlan,
    ) -> None:
        session_id = uuid.uuid4().hex[:8]
        self.pending[session_id] = {
            "user_id": user.id,
            "guild_id": getattr(guild, "id", 0),
            "channel_id": getattr(channel, "id", 0),
            "plan": plan,
            "created_at": time.time(),
        }
        clarify = plan.clarify or {}
        kind = clarify.get("kind")
        prompt = clarify.get("prompt") or "Clarify:"
        options = clarify.get("options") or []

        async def on_timeout():
            self.pending.pop(session_id, None)

        async def on_cancel():
            self.pending.pop(session_id, None)
            await self.send_func(channel, "Cancelled.")

        if kind in ("user", "channel", "role", "intent"):
            select_options = []
            for cand in options[:10]:
                if isinstance(cand, ResolutionCandidate):
                    label = cand.label or str(cand.entity_id)
                    value = str(cand.entity_id)
                    select_options.append(discord.SelectOption(label=label[:80], value=value))
                else:
                    select_options.append(discord.SelectOption(label=str(cand)[:80], value=str(cand)))

            async def on_pick(value: str):
                await self._resume_selection(session_id, kind, value)

            view = IntentChoiceView(user.id, select_options, on_pick, on_cancel, on_timeout=on_timeout)
            msg = await channel.send(prompt, view=view)
            view.message = msg
            return

        if kind == "number":
            async def on_pick(value: int):
                await self._resume_selection(session_id, "number", value)

            async def on_custom():
                value = await self._prompt_number(channel, user, prompt)
                if value is None:
                    await on_cancel()
                    return
                await on_pick(value)

            view = QuickNumberView(user.id, [1, 3, 5, 10], on_pick, on_custom, on_cancel, on_timeout=on_timeout)
            msg = await channel.send(prompt, view=view)
            view.message = msg
            return

        if kind == "text":
            value = await self._prompt_text(channel, user, prompt)
            if value is None:
                await on_cancel()
                return
            await self._resume_selection(session_id, "text", value)
            return

        await on_cancel()

    async def _dispatch_confirm(
        self,
        user: discord.User,
        channel: discord.abc.Messageable,
        guild: Optional[discord.Guild],
        plan: ActionPlan,
    ) -> None:
        session_id = uuid.uuid4().hex[:8]
        self.pending[session_id] = {
            "user_id": user.id,
            "guild_id": getattr(guild, "id", 0),
            "channel_id": getattr(channel, "id", 0),
            "plan": plan,
            "created_at": time.time(),
        }

        async def on_timeout():
            self.pending.pop(session_id, None)

        async def on_confirm():
            payload = self.pending.pop(session_id, None)
            if payload:
                await self._execute_plan(user, channel, guild, None, payload["plan"])

        async def on_cancel():
            self.pending.pop(session_id, None)
            await self.send_func(channel, "Cancelled.")

        view = ConfirmActionView(user.id, on_confirm, on_cancel, on_timeout=on_timeout)
        msg = await channel.send(f"Confirm: {plan.summary}", view=view)
        view.message = msg

    async def _resume_selection(self, session_id: str, kind: str, value: Any) -> None:
        payload = self.pending.pop(session_id, None)
        if not payload:
            return
        plan: ActionPlan = payload["plan"]
        clarify = plan.clarify or {}
        field = clarify.get("field")
        if not field:
            return
        if kind in ("user", "channel", "role"):
            try:
                plan.args[field] = int(value)
            except Exception:
                plan.args[field] = value
        elif kind == "number":
            plan.args[field] = int(value)
        elif kind == "text":
            plan.args[field] = str(value)
        elif kind == "intent":
            plan = await self._plan_from_intent_choice(payload, str(value))
        await self._resume_plan_with_args(payload, plan)

    async def _resume_plan_with_args(self, payload: Dict[str, Any], plan: ActionPlan) -> None:
        user_id = int(payload.get("user_id", 0))
        channel_id = int(payload.get("channel_id", 0))
        guild_id = int(payload.get("guild_id", 0))
        channel = self.bot.get_channel(channel_id)
        if not channel and channel_id:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                channel = None
        guild = self.bot.get_guild(guild_id) if guild_id else None
        user = self.bot.get_user(user_id)
        if not user and user_id:
            try:
                user = await self.bot.fetch_user(user_id)
            except Exception:
                user = None
        if not user or not channel:
            return

        if plan.capability == "send_dm" and "targets" in plan.args:
            resolved = list(plan.args.get("resolved_ids", []))
            pending = list(plan.args.get("pending", []))
            if plan.args.get("user_id"):
                resolved.append(int(plan.args["user_id"]))
            if pending:
                pending.pop(0)
                remaining = [t for t, _ in pending]
                resolved_ids, unresolved = await self._resolve_users(guild, user, remaining, self.context.get(guild_id, user_id))
                resolved.extend(resolved_ids)
                if unresolved:
                    next_token, candidates = unresolved[0]
                    plan.args["resolved_ids"] = resolved
                    plan.args["pending"] = unresolved
                    plan.clarify = {
                        "kind": "user",
                        "field": "user_id",
                        "prompt": f"Which user did you mean for '{next_token}'?",
                        "options": candidates,
                    }
                    await self._dispatch_clarify(user, channel, guild, plan)
                    return
            message_text = plan.args.get("message", "")
            actions = [{"tool": "send_dm", "args": {"user_id": uid, "text": message_text}} for uid in resolved]
            plan.actions = actions
            plan.args = {"user_ids": resolved, "text": message_text}

        if plan.capability == "add_watcher":
            target_id = plan.args.get("target_user_id")
            count = plan.args.get("count")
            message = plan.args.get("message") or plan.args.get("text")
            if target_id and count and message:
                plan.actions = [{"tool": "add_watcher", "args": {"target_user_id": int(target_id), "count": int(count), "text": str(message)}}]
                plan.args = {"target_user_id": int(target_id), "count": int(count), "text": str(message)}

        if plan.capability == "remove_watcher":
            target_id = plan.args.get("target_user_id")
            if target_id:
                plan.actions = [{"tool": "remove_watcher", "args": {"target_user_id": int(target_id)}}]
                plan.args = {"target_user_id": int(target_id)}

        if plan.capability == "send_message":
            channel_id = plan.args.get("channel_id")
            message_text = plan.args.get("message") or plan.args.get("text")
            if channel_id and message_text:
                plan.actions = [{"tool": "send_message", "args": {"channel_id": int(channel_id), "text": str(message_text)}}]
                plan.args = {"channel_id": int(channel_id), "text": str(message_text)}

        if plan.capability == "create_mirror":
            src_id = plan.args.get("source_channel_id")
            dst_id = plan.args.get("target_channel_id")
            if src_id and dst_id:
                plan.actions = [{"tool": "create_mirror", "args": {"source_channel_id": int(src_id), "target_channel_id": int(dst_id)}}]
                plan.args = {"source_channel_id": int(src_id), "target_channel_id": int(dst_id)}

        if plan.capability == "disable_mirror_rule":
            rule_id = plan.args.get("rule_id")
            if rule_id:
                plan.actions = [{"tool": "disable_mirror_rule", "args": {"rule_id": str(rule_id)}}]
                plan.args = {"rule_id": str(rule_id)}

        if plan.capability == "show_stats":
            args = dict(plan.args)
            if "scope" not in args:
                args["scope"] = "daily"
            plan.actions = [{"tool": "show_stats", "args": args}]
            plan.args = args

        if plan.capability == "set_bot_status":
            state = plan.args.get("state")
            text = plan.args.get("text", "")
            if state:
                plan.actions = [{"tool": "set_bot_status", "args": {"state": str(state), "text": str(text)}}]
                plan.args = {"state": str(state), "text": str(text)}

        if plan.confirm:
            await self._dispatch_confirm(user, channel, guild, plan)
            return
        await self._execute_plan(user, channel, guild, None, plan)

    async def _execute_plan(
        self,
        user: discord.User,
        channel: discord.abc.Messageable,
        guild: Optional[discord.Guild],
        message: Optional[discord.Message],
        plan: ActionPlan,
    ) -> None:
        if plan.local_action:
            handler = self.local_actions.get(plan.local_action)
            if callable(handler):
                await self.send_func(channel, str(handler()))
            else:
                await self.send_func(channel, "Action not available.")
            return
        if not plan.capability:
            await self.send_func(channel, "Unhandled request.")
            return
        if not self.registry:
            await self.send_func(channel, "Capability registry missing.")
            return
        actions = plan.actions
        if not actions and plan.capability:
            actions = [{"tool": plan.capability, "args": plan.args}]
        results = await self.execute_actions(user.id, actions, guild=guild, channel=channel, message_id=getattr(message, "id", 0))
        await self._send_results(channel, results)
        self._update_context(guild, user, plan)

    async def _send_results(self, channel: discord.abc.Messageable, results: List[str]) -> None:
        if not results:
            await self.send_func(channel, "No results.")
            return
        ok = [r for r in results if "OK" in r]
        err = [r for r in results if "ERROR" in r]
        if len(results) > 1:
            summary = f"Completed {len(ok)}/{len(results)} actions."
            if err:
                summary += "\nErrors:\n" + "\n".join(err[:5])
            await self.send_func(channel, summary)
            return
        await self.send_func(channel, results[0])

    def _update_context(self, guild: Optional[discord.Guild], user: discord.User, plan: ActionPlan) -> None:
        if not guild:
            return
        updates: Dict[str, Any] = {"last_intent": plan.capability or plan.local_action}
        if "user_ids" in plan.args and plan.args["user_ids"]:
            updates["last_user_id"] = int(plan.args["user_ids"][0])
        if "user_id" in plan.args:
            updates["last_user_id"] = int(plan.args["user_id"])
        if "target_user_id" in plan.args:
            updates["last_user_id"] = int(plan.args["target_user_id"])
        if "channel_id" in plan.args:
            updates["last_channel_id"] = int(plan.args["channel_id"])
        if "source_channel_id" in plan.args:
            updates["last_channel_id"] = int(plan.args["source_channel_id"])
        if "target_channel_id" in plan.args:
            updates["last_channel_id"] = int(plan.args["target_channel_id"])
        if "role_id" in plan.args:
            updates["last_role_id"] = int(plan.args["role_id"])
        if updates:
            self.context.update(guild.id, user.id, updates)

    async def _prompt_text(
        self,
        channel: discord.abc.Messageable,
        user: discord.User,
        prompt: str,
        timeout: int = 60,
    ) -> Optional[str]:
        await self.send_func(channel, prompt)

        def check(msg: discord.Message) -> bool:
            return msg.author.id == user.id and msg.channel.id == getattr(channel, "id", 0)

        try:
            msg = await self.bot.wait_for("message", timeout=timeout, check=check)
        except Exception:
            return None
        return (msg.content or "").strip()

    async def _prompt_number(
        self,
        channel: discord.abc.Messageable,
        user: discord.User,
        prompt: str,
        timeout: int = 45,
    ) -> Optional[int]:
        text = await self._prompt_text(channel, user, prompt, timeout=timeout)
        if not text:
            return None
        if text.isdigit():
            return int(text)
        return None

    async def _prompt_intent_choice(
        self,
        user: discord.User,
        channel: discord.abc.Messageable,
        guild: Optional[discord.Guild],
        raw_text: str,
    ) -> None:
        options = [
            discord.SelectOption(label="DM", value="send_dm"),
            discord.SelectOption(label="Mirror", value="mirror_create"),
            discord.SelectOption(label="Watch", value="add_watcher"),
            discord.SelectOption(label="Stats", value="show_stats"),
            discord.SelectOption(label="Cancel", value="cancel"),
        ]

        async def on_pick(value: str):
            if value == "cancel":
                await self.send_func(channel, "Cancelled.")
                return
            payload = {
                "user_id": user.id,
                "guild_id": getattr(guild, "id", 0),
                "channel_id": getattr(channel, "id", 0),
                "raw_text": raw_text,
                "intent_choice": value,
            }
            plan = await self._plan_from_intent_choice(payload, value)
            if plan.clarify:
                await self._dispatch_clarify(user, channel, guild, plan)
                return
            if plan.confirm:
                await self._dispatch_confirm(user, channel, guild, plan)
                return
            await self._execute_plan(user, channel, guild, None, plan)

        async def on_cancel():
            await self.send_func(channel, "Cancelled.")

        view = IntentChoiceView(user.id, options, on_pick, on_cancel)
        msg = await channel.send("What did you mean?", view=view)
        view.message = msg

    async def _plan_from_intent_choice(self, payload: Dict[str, Any], choice: str) -> ActionPlan:
        spec = next((s for s in self.intent_specs if s.name == choice or s.capability == choice), None)
        if not spec:
            return self._simple_plan(None, "Cancelled.", 0.0)
        intent = IntentMatch(spec=spec, score=0.6, match=None)
        user = self.bot.get_user(int(payload.get("user_id", 0)))
        channel = self.bot.get_channel(int(payload.get("channel_id", 0)))
        guild = self.bot.get_guild(int(payload.get("guild_id", 0)))
        raw_text = payload.get("raw_text", "")
        if not user or not channel:
            return self._simple_plan(None, "Cancelled.", 0.0)
        return await self._build_plan(intent, user, channel, guild, None, raw_text)
