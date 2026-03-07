from __future__ import annotations

import json
import random
import re
import time
import uuid
from typing import Any

from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore


KEYWORD_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "been",
    "being",
    "from",
    "have",
    "just",
    "like",
    "more",
    "only",
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
    def __init__(self, store: MessagePackStore, logger: LoggerService, ai: Any | None = None) -> None:
        self.store = store
        self.logger = logger
        self.ai = ai
        self._rng = random.Random()

    def root(self) -> dict[str, Any]:
        node = self.store.data.setdefault("episodic", {})
        node.setdefault("episodes", {})
        return node

    async def record(
        self,
        guild_id: int,
        channel_id: int,
        participants: list[str],
        message_window: list[Any],
    ) -> dict[str, Any] | None:
        try:
            gid = str(int(guild_id))
            cid = str(int(channel_id))
            lines = self._normalize_window(message_window)
            if not gid or gid == "0" or not lines:
                return None
            summary_row = await self._summarize_episode(guild_id=int(guild_id), channel_id=int(channel_id), lines=lines)
            episode = {
                "id": uuid.uuid4().hex[:8],
                "ts": int(time.time()),
                "guild_id": gid,
                "channel_id": cid,
                "participants": sorted({str(item) for item in participants if str(item).strip()}),
                "summary": str(summary_row.get("summary", "")).strip()[:220],
                "keywords": [str(word)[:40] for word in summary_row.get("keywords", [])[:8]],
                "sentiment": str(summary_row.get("sentiment", "neutral")).strip()[:16] or "neutral",
                "weight": round(max(0.0, min(1.0, float(summary_row.get("weight", 0.55) or 0.55))), 3),
            }
            episodes = self.root().setdefault("episodes", {})
            rows = episodes.setdefault(gid, [])
            if not isinstance(rows, list):
                rows = []
                episodes[gid] = rows
            rows.append(episode)
            if len(rows) > 200:
                del rows[: len(rows) - 200]
            self.store.touch()
            return episode
        except Exception as exc:  # noqa: BLE001
            self.logger.log("episodic.record_failed", guild_id=guild_id, error=str(exc)[:240])
            return None

    def search(self, guild_id: int, query: str, limit: int = 3) -> list[dict[str, Any]]:
        try:
            rows = self.root().setdefault("episodes", {}).get(str(int(guild_id)), [])
            if not isinstance(rows, list) or not rows:
                return []
            now = time.time()
            query_terms = set(self._keywords(query))
            scored: list[tuple[float, dict[str, Any]]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                base_weight = max(0.0, min(1.0, float(row.get("weight", 0.5) or 0.5)))
                ts = int(row.get("ts", 0) or 0)
                age_days = max(0.0, (now - ts) / 86400.0) if ts > 0 else 999.0
                freshness = max(0.0, 0.25 - (age_days * 0.01))
                overlap = 0.0
                haystack_terms = set(self._keywords(str(row.get("summary", ""))))
                haystack_terms.update(str(item).lower() for item in row.get("keywords", []) if str(item).strip())
                if query_terms:
                    overlap = min(0.6, 0.22 * len(query_terms.intersection(haystack_terms)))
                score = round(base_weight + freshness + overlap, 4)
                if score <= 0:
                    continue
                scored.append((score, row))
            scored.sort(key=lambda item: item[0], reverse=True)
            out: list[dict[str, Any]] = []
            for score, row in scored[: max(1, int(limit))]:
                copy = dict(row)
                copy["score"] = score
                out.append(copy)
            return out
        except Exception as exc:  # noqa: BLE001
            self.logger.log("episodic.search_failed", guild_id=guild_id, error=str(exc)[:240])
            return []

    def recall_random(self, guild_id: int) -> dict[str, Any] | None:
        try:
            rows = self.root().setdefault("episodes", {}).get(str(int(guild_id)), [])
            if not isinstance(rows, list) or not rows:
                return None
            now = time.time()
            weighted_rows: list[tuple[float, dict[str, Any]]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ts = int(row.get("ts", 0) or 0)
                age_days = max(0.0, (now - ts) / 86400.0) if ts > 0 else 0.0
                bias = 0.25 if age_days >= 3 else 0.05
                score = max(0.05, float(row.get("weight", 0.5) or 0.5) + bias - (age_days * 0.01))
                weighted_rows.append((score, row))
            total = sum(weight for weight, _row in weighted_rows)
            if total <= 0:
                return dict(weighted_rows[-1][1]) if weighted_rows else None
            pick = self._rng.random() * total
            running = 0.0
            for weight, row in weighted_rows:
                running += weight
                if running >= pick:
                    return dict(row)
            return dict(weighted_rows[-1][1]) if weighted_rows else None
        except Exception as exc:  # noqa: BLE001
            self.logger.log("episodic.recall_failed", guild_id=guild_id, error=str(exc)[:240])
            return None

    def boost(self, episode_id: str) -> bool:
        try:
            for rows in self.root().setdefault("episodes", {}).values():
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("id", "")) != str(episode_id):
                        continue
                    row["weight"] = round(min(1.0, float(row.get("weight", 0.5) or 0.5) + 0.15), 3)
                    self.store.touch()
                    return True
            return False
        except Exception as exc:  # noqa: BLE001
            self.logger.log("episodic.boost_failed", episode_id=str(episode_id)[:40], error=str(exc)[:240])
            return False

    def format_memory_block(self, guild_id: int, query: str, *, limit: int = 2, char_limit: int = 300) -> tuple[str, list[str]]:
        try:
            matches = self.search(guild_id, query, limit=limit)
            if not matches:
                return "", []
            lines: list[str] = []
            summaries: list[str] = []
            for row in matches[:limit]:
                summary = str(row.get("summary", "")).strip()
                if not summary:
                    continue
                summaries.append(summary[:140])
                lines.append(f"- {summary[:140]}")
                self.boost(str(row.get("id", "")))
            if not lines:
                return "", []
            block = f"[MEMORY]\n{chr(10).join(lines)}"
            return block[:char_limit], summaries[:limit]
        except Exception as exc:  # noqa: BLE001
            self.logger.log("episodic.memory_block_failed", guild_id=guild_id, error=str(exc)[:240])
            return "", []

    def _normalize_window(self, message_window: list[Any]) -> list[str]:
        lines: list[str] = []
        for item in message_window[-15:]:
            if isinstance(item, dict):
                author = str(item.get("author", "")).strip()
                text = str(item.get("text", "")).strip()
            else:
                author = str(getattr(getattr(item, "author", None), "display_name", "")).strip()
                text = str(getattr(item, "clean_content", "")).strip()
            if not text:
                continue
            prefix = f"{author}: " if author else ""
            lines.append(f"{prefix}{text[:240]}")
        return lines[-15:]

    async def _summarize_episode(self, *, guild_id: int, channel_id: int, lines: list[str]) -> dict[str, Any]:
        fallback = self._fallback_episode(lines)
        if self.ai is None or not hasattr(self.ai, "complete_text"):
            return fallback
        try:
            prompt = (
                "Summarize this Discord conversation window as strict JSON with keys: "
                "summary, keywords, sentiment, weight. summary must be 1-2 sentences. "
                "keywords must be max 8 short strings. sentiment must be positive, negative, neutral, or heated. "
                "weight must be a float from 0 to 1."
            )
            user_prompt = (
                f"Guild: {guild_id}\n"
                f"Channel: {channel_id}\n"
                f"Window:\n{chr(10).join(lines)}\n"
                "Return JSON only."
            )
            raw = await self.ai.complete_text(system_prompt=prompt, user_prompt=user_prompt, max_tokens=220, temperature=0.3)
            parsed = self._extract_json(raw or "")
            if not parsed:
                return fallback
            summary = str(parsed.get("summary", "")).strip()[:220]
            keywords = parsed.get("keywords", [])
            if not isinstance(keywords, list):
                keywords = []
            sentiment = str(parsed.get("sentiment", "neutral")).strip().lower()
            if sentiment not in {"positive", "negative", "neutral", "heated"}:
                sentiment = fallback["sentiment"]
            weight = parsed.get("weight", fallback["weight"])
            try:
                weight_value = float(weight)
            except (TypeError, ValueError):
                weight_value = float(fallback["weight"])
            return {
                "summary": summary or fallback["summary"],
                "keywords": [str(item)[:40] for item in keywords[:8]] or fallback["keywords"],
                "sentiment": sentiment,
                "weight": round(max(0.0, min(1.0, weight_value)), 3),
            }
        except Exception as exc:  # noqa: BLE001
            self.logger.log("episodic.ai_summary_failed", guild_id=guild_id, error=str(exc)[:240])
            return fallback

    def _fallback_episode(self, lines: list[str]) -> dict[str, Any]:
        joined = " ".join(lines)
        keywords = self._keywords(joined)
        sentiment = "heated" if any(token in joined.lower() for token in ("wtf", "shut up", "hate", "mad")) else "neutral"
        if any(token in joined.lower() for token in ("thanks", "love", "appreciate", "good")):
            sentiment = "positive"
        if any(token in joined.lower() for token in ("bad", "annoying", "dumb", "worst")):
            sentiment = "negative"
        summary = lines[-2:] if len(lines) >= 2 else lines
        summary_text = " ".join(summary)[:180]
        return {
            "summary": summary_text or "A short exchange unfolded and left an impression.",
            "keywords": keywords[:8],
            "sentiment": sentiment,
            "weight": 0.55,
        }

    def _keywords(self, text: str) -> list[str]:
        words = re.findall(r"[a-z0-9']{3,24}", str(text or "").lower())
        ranked: list[str] = []
        seen: set[str] = set()
        for word in words:
            if word in KEYWORD_STOPWORDS:
                continue
            if word in seen:
                continue
            seen.add(word)
            ranked.append(word)
        return ranked[:12]

    def _extract_json(self, raw: str) -> dict[str, Any] | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
        return None
