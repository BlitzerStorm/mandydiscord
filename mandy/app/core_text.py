from __future__ import annotations

import re


def chunk_lines(lines: list, header: str, limit: int = 1900) -> list:
    """Chunk text with a header repeated per chunk."""
    chunks = []
    cur = header
    for line in lines:
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = header
        cur += "\n" + line
    if cur:
        chunks.append(cur)
    return chunks


def truncate(text: str, limit: int = 180) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def strip_bot_mentions(text: str, bot_id: int) -> str:
    if not text or not bot_id:
        return ""
    cleaned = re.sub(rf"<@!?{bot_id}>", "", text)
    return " ".join(cleaned.split())


def is_youtube_url(url: str) -> bool:
    if not url:
        return False
    return "youtube.com" in url or "youtu.be" in url


def normalize_youtube_url(url: str) -> str:
    if not url:
        return ""
    return url.split("&")[0].strip()


def classify_mood(text: str) -> str:
    lower = (text or "").lower()
    negative = ("angry", "mad", "hate", "annoyed", "wtf", "stupid", "dumb", "trash")
    positive = ("love", "awesome", "great", "thanks", "thank you", "nice", "cool")
    if any(w in lower for w in positive):
        return "positive"
    if any(w in lower for w in negative):
        return "negative"
    return "neutral"

