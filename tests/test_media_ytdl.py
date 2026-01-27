import asyncio

import pytest

from mandy.app import media_ytdl


class DummySource:
    def __init__(self, url):
        self.url = url


def _patch_runtime(monkeypatch, called):
    class FakeYTDL:
        def extract_info(self, url, download=True):
            called["extract"] = True
            return {"url": "http://example.com/audio"}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def fake_run_in_executor(self, executor, func):
        return func()

    monkeypatch.setattr(loop, "run_in_executor", fake_run_in_executor.__get__(loop))
    monkeypatch.setattr(media_ytdl.YTDLSource, "ytdl", FakeYTDL())
    monkeypatch.setattr(media_ytdl.discord, "FFmpegPCMAudio", lambda url, **opts: DummySource(url))
    return loop


def test_rejects_non_youtube(monkeypatch):
    called = {"extract": False}
    loop = _patch_runtime(monkeypatch, called)
    async def run():
        with pytest.raises(ValueError):
            await media_ytdl.YTDLSource.from_url("https://soundcloud.com/notyoutube", loop=loop)
    loop.run_until_complete(run())
    loop.close()
    assert called["extract"] is False


def test_accepts_youtube_and_uses_stub(monkeypatch):
    called = {"extract": False}
    loop = _patch_runtime(monkeypatch, called)
    async def run():
        src = await media_ytdl.YTDLSource.from_url("https://youtu.be/dQw4w9WgXcQ", loop=loop)
        assert isinstance(src, media_ytdl.YTDLSource)
        assert src.url == "http://example.com/audio"
    loop.run_until_complete(run())
    loop.close()
    assert called["extract"] is True
