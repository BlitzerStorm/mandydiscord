# Mandy OS Debug Report (2026-01-27)

## Scope
Static audit and debug pass with local tooling. Runtime integration (Discord API, voice, MySQL, Gemini) cannot be fully exercised in this environment.

## Checks Run
- `python -m pytest -q` → **7 passed**
- `python -m compileall -q .` → **no errors**

## Imports & Module Wiring
Reviewed and validated module export hubs:
- `mandy/app/core.py`
- `mandy/app/db.py`
- `mandy/app/logging.py`
- `mandy/app/media.py`
- `mandy/app/store.py`
- `mandy/intelligence_layer.py`

All re-export targets resolve to tracked files and compile cleanly.

## Recent Hardening
- YouTube-only guard enforced at YTDL layer (`mandy/app/media_ytdl.py`) with tests.
- Discord stubs expanded for tests (`tests/conftest.py`).
- Deprecation warnings for `datetime.utcnow()` removed in `cogs/mandy_ai.py`.

## Known Limitations (cannot be fully validated locally)
- Discord API actions: message send/delete, reactions, permissions, channels, embeds, voice.
- Voice playback dependencies: `ffmpeg`, `yt-dlp` network calls.
- Gemini and external HTTP calls.
- MySQL operations (schema creation, migrations, runtime DB ops).

## Remaining Risk Areas
- Extension validator remains permissive (AI tool generation is effectively trusted code).
- Limited test coverage for mirrors, watchers, DM bridges, gate, sentience, and stats.

## Recommended Next Steps
1. Run the bot in a staging Discord server and perform a manual smoke test of menus + commands.
2. If MySQL is enabled, run a migration/test in a sandbox DB.
3. Add integration tests for mirrors, watchers, and DM bridges.
