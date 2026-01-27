from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import discord
import yt_dlp
import re

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "extract_flat": False,
    "quiet": True,
    "no_warnings": True,
    "ignoreerrors": True,
    "default_search": "auto",
}
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


def _is_youtube_url(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return "youtube.com" in u or "youtu.be" in u


class YTDLSource(discord.PCMVolumeTransformer):
    ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

    def __init__(self, source: discord.AudioSource, *, data: Dict[str, Any]):
        super().__init__(source)
        self.data = data
        self.url = data.get("url")

    @classmethod
    async def from_url(cls, url: str, *, loop: Optional[asyncio.AbstractEventLoop] = None, stream: bool = True):
        if not _is_youtube_url(url):
            raise ValueError("Only YouTube links are allowed for playback.")
        if loop is None:
            loop = asyncio.get_running_loop()

        data = await loop.run_in_executor(None, lambda: cls.ytdl.extract_info(url, download=not stream))
        if not data:
            raise ValueError("Unable to retrieve voice media metadata.")
        if "entries" in data:
            data = data["entries"][0]
        if not data:
            raise ValueError("Unable to resolve a playable voice entry.")

        source = discord.FFmpegPCMAudio(data["url"], **FFMPEG_OPTIONS)
        return cls(source, data=data)
