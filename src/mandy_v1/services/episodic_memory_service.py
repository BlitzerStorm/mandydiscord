from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from typing import Any


LOGGER = logging.getLogger("mandy.episodic")
SENTIMENT_WORDS = {
    "positive": {"love", "great", "good", "awesome", "thanks", "excited"},
    "negative": {"hate", "bad", "worst", "annoying", "stupid", "trash", "angry"},
}
STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "before",
    "being",
    "from",
    "have",
    "just",
    "like",
    "more",
    "really",
    "that",
    "their",
    "them",
    "they",
    "this",
    "with",
    "your",
}


class EpisodicMemoryService:
    """Stores and retrieves simple episodic memory snippets per guild."""

    def __init__(self, storage: Any, ai_service: Any | None = None) -> None:
        """Capture dependencies and initialize channel buffers."""
        self.storage = storage
        self.ai_service = ai_service
        self._buffers: dict[int, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=15))
        self._counts: dict[int, int] = defaultdict(int)

    def _root(self) -> dict[str, Any]:
        """Return episodic root with defaults."""
        node = self.storage.data.setdefault("episodic", {})
        node.setdefault("episodes", {})
        return node

    def _mark_dirty(self) -> None:
        """Mark storage dirty using compatible store API."""
        if hasattr(self.storage, "mark_dirty"):
            self.storage.mark_dirty()
        elif hasattr(self.storage, "touch"):
            self.storage.touch()

    async def record(self, guild_id: int, channel_id: int, author_id: Any, author_name: Any = "", content: Any = "") -> dict[str, Any] | None:
        """Record a message and flush channel buffer every 10 messages."""
        try:
            # Compatibility path: record(guild_id, channel_id, participants, message_window)
            if isinstance(author_id, list) and isinstance(author_name, list):
                participants = [str(x).strip() for x in author_id if str(x).strip()]
                window = [row for row in author_name if isinstance(row, dict)]
                if int(guild_id) <= 0 or int(channel_id) <= 0 or not window:
                    return None
                combined = " ".join(str(row.get("text", ""))[:180] for row in window[:15]).strip()
                summary = combined[:220] or "conversation snapshot"
                episode = {
                    "guild_id": str(int(guild_id)),
                    "channel_id": str(int(channel_id)),
                    "author_id": "0",
                    "author_name": ", ".join(participants[:3]) or "participants",
                    "content": summary,
                    "summary": summary,
                    "ts": int(time.time()),
                    "weight": 1.0,
                    "boost": 1.0,
                }
                episodes = self._root().setdefault("episodes", {})
                guild_rows = episodes.setdefault(str(int(guild_id)), [])
                if not isinstance(guild_rows, list):
                    guild_rows = []
                    episodes[str(int(guild_id))] = guild_rows
                guild_rows.append(episode)
                if len(guild_rows) > 200:
                    del guild_rows[: len(guild_rows) - 200]
                self._mark_dirty()
                return episode

            text = str(content or "").strip()
            if int(guild_id) <= 0 or int(channel_id) <= 0 or not text:
                return None
            row = {
                "guild_id": str(int(guild_id)),
                "channel_id": str(int(channel_id)),
                "author_id": str(int(author_id)),
                "author_name": str(author_name or "")[:80],
                "content": text[:280],
                "summary": text[:220],
                "ts": int(time.time()),
                "weight": 1.0,
                "boost": 1.0,
            }
            buffer = self._buffers[int(channel_id)]
            buffer.append(row)
            self._counts[int(channel_id)] += 1
            if self._counts[int(channel_id)] % 10 != 0:
                return row
            episodes = self._root().setdefault("episodes", {})
            guild_rows = episodes.setdefault(str(int(guild_id)), [])
            if not isinstance(guild_rows, list):
                guild_rows = []
                episodes[str(int(guild_id))] = guild_rows
            for item in list(buffer)[-15:]:
                guild_rows.append(dict(item))
            if len(guild_rows) > 200:
                del guild_rows[: len(guild_rows) - 200]
            self._mark_dirty()
            return row
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to record episodic message.")
            return None

    def search(self, guild_id: int, query: str, top_n: int = 5, limit: int | None = None) -> list[dict[str, Any]]:
        """Return most relevant episodes by keyword overlap and weighting."""
        try:
            rows = self._root().setdefault("episodes", {}).get(str(int(guild_id)), [])
            if not isinstance(rows, list) or not rows:
                return []
            if limit is not None:
                top_n = int(limit)
            query_terms = set(self._terms(query))
            scored: list[tuple[float, dict[str, Any]]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                content_terms = set(self._terms(str(row.get("content", ""))))
                overlap = len(query_terms.intersection(content_terms))
                if overlap <= 0:
                    continue
                weight = float(row.get("weight", 1.0) or 1.0)
                boost = float(row.get("boost", 1.0) or 1.0)
                score = overlap * weight * boost
                scored.append((score, row))
            scored.sort(key=lambda item: item[0], reverse=True)
            return [dict(item[1]) for item in scored[: max(1, int(top_n))]]
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed episodic search.")
            return []

    def boost(self, guild_id: int, episode_index: int, amount: float = 0.2) -> None:
        """Boost an episode weight by list index in a guild memory list."""
        try:
            rows = self._root().setdefault("episodes", {}).get(str(int(guild_id)), [])
            if not isinstance(rows, list):
                return
            index = int(episode_index)
            if index < 0 or index >= len(rows):
                return
            row = rows[index]
            if not isinstance(row, dict):
                return
            row["weight"] = round(max(0.1, float(row.get("weight", 1.0) or 1.0) + float(amount)), 4)
            self._mark_dirty()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed episodic boost.")

    def recall_block(self, guild_id: int, query: str) -> str:
        """Build capped prompt memory block from matched episodes."""
        try:
            lines: list[str] = []
            used = 0
            for row in self.search(guild_id, query, top_n=5):
                author = str(row.get("author_name", "someone")).strip() or "someone"
                content = str(row.get("content", "")).strip()
                if not content:
                    continue
                line = f"[MEMORY] {author}: {content}"
                extra = len(line) + (1 if lines else 0)
                if used + extra > 300:
                    break
                lines.append(line)
                used += extra
            return "\n".join(lines)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to build recall block.")
            return ""

    def format_memory_block(self, guild_id: int, query: str, *, limit: int = 2, char_limit: int = 300) -> tuple[str, list[str]]:
        """Compatibility helper returning formatted memory block and summary list."""
        try:
            lines: list[str] = []
            summaries: list[str] = []
            used = 0
            for row in self.search(guild_id, query, top_n=max(1, int(limit))):
                author = str(row.get("author_name", "someone")).strip() or "someone"
                content = str(row.get("content", "")).strip()
                if not content:
                    continue
                line = f"[MEMORY] {author}: {content}"
                extra = len(line) + (1 if lines else 0)
                if used + extra > max(50, int(char_limit)):
                    break
                lines.append(line)
                used += extra
                summaries.append(content[:120])
            return ("\n".join(lines), summaries[: max(1, int(limit))])
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to format memory block.")
            return ("", [])

    def form_opinions_from_episodes(self, guild_id: int) -> list[str]:
        """Generate simple rule-based opinion lines from recent episodes."""
        try:
            rows = self._root().setdefault("episodes", {}).get(str(int(guild_id)), [])
            if not isinstance(rows, list) or len(rows) < 5:
                return []
            recent = rows[-40:]
            topic_counts: dict[str, int] = {}
            positive = 0
            negative = 0
            for row in recent:
                if not isinstance(row, dict):
                    continue
                content = str(row.get("content", "")).lower()
                for term in self._terms(content):
                    topic_counts[term] = topic_counts.get(term, 0) + 1
                words = set(self._terms(content))
                if words.intersection(SENTIMENT_WORDS["positive"]):
                    positive += 1
                if words.intersection(SENTIMENT_WORDS["negative"]):
                    negative += 1
            ranked = sorted(topic_counts.items(), key=lambda item: item[1], reverse=True)
            opinions: list[str] = []
            for topic, count in ranked[:3]:
                if count < 3:
                    continue
                opinions.append(f"{topic} keeps resurfacing here.")
            if negative > positive + 3:
                opinions.append("Tension spikes fast when people feel ignored.")
            elif positive > negative + 3:
                opinions.append("This crowd softens quickly when someone leads with warmth.")
            return opinions[:3]
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to form opinions from episodes.")
            return []

    def _terms(self, text: str) -> list[str]:
        """Tokenize text into searchable keyword terms."""
        terms: list[str] = []
        seen: set[str] = set()
        for token in re.findall(r"[a-z0-9']{3,24}", str(text or "").lower()):
            if token in STOPWORDS:
                continue
            if token in seen:
                continue
            seen.add(token)
            terms.append(token)
        return terms
