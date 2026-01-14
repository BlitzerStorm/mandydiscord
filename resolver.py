import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
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
    member_lookup: Dict[str, List[Tuple[int, str]]]
    channel_lookup: Dict[str, List[Tuple[int, str]]]
    role_lookup: Dict[str, List[Tuple[int, str]]]


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


def _rank_indexed(
    query: str,
    entries: Iterable[IndexedEntry],
    recent_ids: Optional[Iterable[int]] = None,
    limit: int = 5,
    exact_lookup: Optional[Dict[str, List[Tuple[int, str]]]] = None,
) -> List[ResolutionCandidate]:
    query_norm = normalize_token(query)
    recent = set(int(x) for x in (recent_ids or []))
    if exact_lookup and query_norm:
        exact = exact_lookup.get(query_norm)
        if exact:
            candidates = [
                ResolutionCandidate(
                    entity_id=entity_id,
                    label=label,
                    score=1.0,
                    match_type="exact",
                )
                for entity_id, label in exact
            ]
            candidates.sort(key=lambda c: (-c.score, c.label.lower()))
            return candidates[:limit]
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


def _build_member_entries(guild: discord.Guild) -> Tuple[List[IndexedEntry], Dict[str, List[Tuple[int, str]]]]:
    entries: List[IndexedEntry] = []
    lookup: Dict[str, List[Tuple[int, str]]] = {}
    for member in guild.members:
        labels = [
            member.display_name,
            member.name,
            getattr(member, "global_name", None),
        ]
        norms = [normalize_token(label) for label in labels if label]
        for label, norm in zip(labels, norms):
            if label and norm:
                lookup.setdefault(norm, []).append((member.id, label))
        entries.append(IndexedEntry(entity_id=member.id, labels=labels, normalized=norms))
    return entries, lookup


def _build_channel_entries(guild: discord.Guild) -> Tuple[List[IndexedEntry], Dict[str, List[Tuple[int, str]]]]:
    entries: List[IndexedEntry] = []
    lookup: Dict[str, List[Tuple[int, str]]] = {}
    for ch in guild.channels:
        if isinstance(ch, (discord.TextChannel, discord.Thread)):
            labels = [ch.name]
            norms = [normalize_token(ch.name)]
            if norms and norms[0]:
                lookup.setdefault(norms[0], []).append((ch.id, ch.name))
            entries.append(IndexedEntry(entity_id=ch.id, labels=labels, normalized=norms))
    return entries, lookup


def _build_role_entries(guild: discord.Guild) -> Tuple[List[IndexedEntry], Dict[str, List[Tuple[int, str]]]]:
    entries: List[IndexedEntry] = []
    lookup: Dict[str, List[Tuple[int, str]]] = {}
    for role in guild.roles:
        if role.is_default():
            continue
        labels = [role.name]
        norms = [normalize_token(role.name)]
        if norms and norms[0]:
            lookup.setdefault(norms[0], []).append((role.id, role.name))
        entries.append(IndexedEntry(entity_id=role.id, labels=labels, normalized=norms))
    return entries, lookup


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

        members, member_lookup = _build_member_entries(guild)
        channels, channel_lookup = _build_channel_entries(guild)
        roles, role_lookup = _build_role_entries(guild)
        index = GuildIndex(
            members=members,
            channels=channels,
            roles=roles,
            member_lookup=member_lookup,
            channel_lookup=channel_lookup,
            role_lookup=role_lookup,
        )
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
        return _rank_indexed(
            query,
            index.members,
            recent_ids=recent_ids,
            limit=limit,
            exact_lookup=index.member_lookup,
        )
    entries, lookup = _build_member_entries(guild)
    return _rank_indexed(query, entries, recent_ids=recent_ids, limit=limit, exact_lookup=lookup)


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
        return _rank_indexed(
            query.lstrip("#"),
            index.channels,
            recent_ids=recent_ids,
            limit=limit,
            exact_lookup=index.channel_lookup,
        )
    entries, lookup = _build_channel_entries(guild)
    return _rank_indexed(query.lstrip("#"), entries, recent_ids=recent_ids, limit=limit, exact_lookup=lookup)


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
        return _rank_indexed(
            query.lstrip("@"),
            index.roles,
            recent_ids=recent_ids,
            limit=limit,
            exact_lookup=index.role_lookup,
        )
    entries, lookup = _build_role_entries(guild)
    return _rank_indexed(query.lstrip("@"), entries, recent_ids=recent_ids, limit=limit, exact_lookup=lookup)
