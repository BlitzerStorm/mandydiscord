import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple

import discord


USER_MENTION_RE = re.compile(r"<@!?(\d+)>")
CHANNEL_MENTION_RE = re.compile(r"<#(\d+)>")
ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")


@dataclass
class ResolutionCandidate:
    entity_id: int
    label: str
    score: float
    match_type: str


@dataclass
class IndexedEntry:
    entity_id: int
    labels: List[str]
    normalized: List[str]


@dataclass
class GuildIndex:
    members: List[IndexedEntry]
    channels: List[IndexedEntry]
    roles: List[IndexedEntry]
    member_exact: Dict[str, List[Tuple[int, str]]] = field(default_factory=dict)
    channel_exact: Dict[str, List[Tuple[int, str]]] = field(default_factory=dict)
    role_exact: Dict[str, List[Tuple[int, str]]] = field(default_factory=dict)


@lru_cache(maxsize=8192)
def normalize_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").strip().lower())


def parse_user_id(text: str) -> Optional[int]:
    if not text:
        return None
    match = USER_MENTION_RE.search(text)
    if match:
        return int(match.group(1))
    raw = text.strip()
    if raw.isdigit():
        return int(raw)
    return None


def parse_channel_id(text: str) -> Optional[int]:
    if not text:
        return None
    match = CHANNEL_MENTION_RE.search(text)
    if match:
        return int(match.group(1))
    raw = text.strip().lstrip("#")
    if raw.isdigit():
        return int(raw)
    return None


def parse_role_id(text: str) -> Optional[int]:
    if not text:
        return None
    match = ROLE_MENTION_RE.search(text)
    if match:
        return int(match.group(1))
    raw = text.strip().lstrip("@")
    if raw.isdigit():
        return int(raw)
    return None


@lru_cache(maxsize=16384)
def _score_norm(query_norm: str, name_norm: str) -> Tuple[float, str]:
    if not query_norm or not name_norm:
        return 0.0, "none"
    if query_norm == name_norm:
        return 1.0, "exact"
    if name_norm.startswith(query_norm):
        return 0.92, "prefix"
    if query_norm in name_norm:
        return 0.86, "contains"
    ratio = SequenceMatcher(None, query_norm, name_norm).ratio()
    return 0.6 * ratio, "fuzzy"


def _build_exact_map(entries: List[IndexedEntry]) -> Dict[str, List[Tuple[int, str]]]:
    exact: Dict[str, List[Tuple[int, str]]] = {}
    for entry in entries:
        for label, norm in zip(entry.labels, entry.normalized):
            if not label or not norm:
                continue
            bucket = exact.setdefault(norm, [])
            bucket.append((int(entry.entity_id), label))
    return exact


def _rank_indexed(
    query: str,
    entries: Iterable[IndexedEntry],
    recent_ids: Optional[Iterable[int]] = None,
    limit: int = 5,
) -> List[ResolutionCandidate]:
    query_norm = normalize_token(query)
    if not query_norm:
        return []
    recent = set(int(x) for x in recent_ids) if recent_ids else set()
    scored: List[ResolutionCandidate] = []
    for entry in entries:
        best_score = 0.0
        best_type = "none"
        best_label = ""
        for label, norm in zip(entry.labels, entry.normalized):
            if not label or not norm:
                continue
            score, match_type = _score_norm(query_norm, norm)
            if score > best_score:
                best_score = score
                best_type = match_type
                best_label = label
        if best_score <= 0.0:
            continue
        if entry.entity_id in recent:
            best_score = min(0.98, best_score + 0.04)
        scored.append(ResolutionCandidate(entity_id=entry.entity_id, label=best_label, score=best_score, match_type=best_type))
    scored.sort(key=lambda c: (-c.score, c.label.lower()))
    return scored[:limit]


def pick_best(candidates: List[ResolutionCandidate], min_score: float = 0.82, gap: float = 0.06) -> Optional[int]:
    if not candidates:
        return None
    top = candidates[0]
    if top.score < min_score:
        return None
    if len(candidates) > 1 and (top.score - candidates[1].score) < gap:
        return None
    return int(top.entity_id)


def _build_member_entries(guild: discord.Guild) -> List[IndexedEntry]:
    entries: List[IndexedEntry] = []
    for member in guild.members:
        labels = [label for label in (
            member.display_name,
            member.name,
            getattr(member, "global_name", None),
        ) if label]
        norms = [normalize_token(label) for label in labels]
        entries.append(IndexedEntry(entity_id=member.id, labels=labels, normalized=norms))
    return entries


def _build_channel_entries(guild: discord.Guild) -> List[IndexedEntry]:
    entries: List[IndexedEntry] = []
    for ch in guild.channels:
        if isinstance(ch, (discord.TextChannel, discord.Thread)):
            labels = [ch.name] if ch.name else []
            norms = [normalize_token(label) for label in labels]
            entries.append(IndexedEntry(entity_id=ch.id, labels=labels, normalized=norms))
    return entries


def _build_role_entries(guild: discord.Guild) -> List[IndexedEntry]:
    entries: List[IndexedEntry] = []
    for role in guild.roles:
        if role.is_default():
            continue
        labels = [role.name] if role.name else []
        norms = [normalize_token(label) for label in labels]
        entries.append(IndexedEntry(entity_id=role.id, labels=labels, normalized=norms))
    return entries


class GuildIndexCache:
    def __init__(self, ttl_seconds: int = 120):
        self.ttl_seconds = int(ttl_seconds)
        self._cache: Dict[int, Dict[str, Any]] = {}

    def get(self, guild: Optional[discord.Guild]) -> Optional[GuildIndex]:
        if not guild:
            return None
        gid = int(guild.id)
        now = time.time()
        member_count = len(guild.members)
        channel_count = len(guild.channels)
        role_count = len(guild.roles)
        entry = self._cache.get(gid)
        if entry:
            same_counts = (
                entry.get("member_count") == member_count
                and entry.get("channel_count") == channel_count
                and entry.get("role_count") == role_count
            )
            fresh = (now - float(entry.get("at", 0))) < self.ttl_seconds
            if same_counts and fresh:
                return entry.get("index")

        index = GuildIndex(
            members=_build_member_entries(guild),
            channels=_build_channel_entries(guild),
            roles=_build_role_entries(guild),
            member_exact={},
            channel_exact={},
            role_exact={},
        )
        index.member_exact = _build_exact_map(index.members)
        index.channel_exact = _build_exact_map(index.channels)
        index.role_exact = _build_exact_map(index.roles)
        self._cache[gid] = {
            "at": now,
            "member_count": member_count,
            "channel_count": channel_count,
            "role_count": role_count,
            "index": index,
        }
        return index


def rank_members(
    guild: Optional[discord.Guild],
    query: str,
    recent_ids: Optional[Iterable[int]] = None,
    limit: int = 5,
    index: Optional[GuildIndex] = None,
) -> List[ResolutionCandidate]:
    if not guild or not query:
        return []
    if index:
        query_norm = normalize_token(query)
        if query_norm:
            exact = index.member_exact.get(query_norm)
            if exact:
                return [
                    ResolutionCandidate(entity_id=entity_id, label=label, score=1.0, match_type="exact")
                    for entity_id, label in exact
                ]
        return _rank_indexed(query, index.members, recent_ids=recent_ids, limit=limit)
    entries = _build_member_entries(guild)
    return _rank_indexed(query, entries, recent_ids=recent_ids, limit=limit)


def rank_channels(
    guild: Optional[discord.Guild],
    query: str,
    recent_ids: Optional[Iterable[int]] = None,
    limit: int = 5,
    index: Optional[GuildIndex] = None,
) -> List[ResolutionCandidate]:
    if not guild or not query:
        return []
    if index:
        query_norm = normalize_token(query.lstrip("#"))
        if query_norm:
            exact = index.channel_exact.get(query_norm)
            if exact:
                return [
                    ResolutionCandidate(entity_id=entity_id, label=label, score=1.0, match_type="exact")
                    for entity_id, label in exact
                ]
        return _rank_indexed(query.lstrip("#"), index.channels, recent_ids=recent_ids, limit=limit)
    entries = _build_channel_entries(guild)
    return _rank_indexed(query.lstrip("#"), entries, recent_ids=recent_ids, limit=limit)


def rank_roles(
    guild: Optional[discord.Guild],
    query: str,
    recent_ids: Optional[Iterable[int]] = None,
    limit: int = 5,
    index: Optional[GuildIndex] = None,
) -> List[ResolutionCandidate]:
    if not guild or not query:
        return []
    if index:
        query_norm = normalize_token(query.lstrip("@"))
        if query_norm:
            exact = index.role_exact.get(query_norm)
            if exact:
                return [
                    ResolutionCandidate(entity_id=entity_id, label=label, score=1.0, match_type="exact")
                    for entity_id, label in exact
                ]
        return _rank_indexed(query.lstrip("@"), index.roles, recent_ids=recent_ids, limit=limit)
    entries = _build_role_entries(guild)
    return _rank_indexed(query.lstrip("@"), entries, recent_ids=recent_ids, limit=limit)
