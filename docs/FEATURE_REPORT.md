# Mandy OS Feature Report (2026-01-27)

This report summarizes the bot’s current capabilities, key entrypoints, and known gaps, based on a code + docs walk-through.

## Core Control Plane
- **Prefix commands:** `!menu`, `!godmenu`, `!setup`, `!watchers`, `!mirroradd|mirroraddscope|mirrorremove`, `!dmopen|!dmclose|!dmai`, `!mystats|allstats|livestats|globalstats|globallive`, `!movie`, `!clean`, `!nuke`, `!health`, `!leavevc`, `!ambient`.
- **Menus (UI):** User menu (status/help/DM visibility/roast opt-in); God menu (perms, mirrors, watchers, DM bridges, logs, command routing, setup, gate tools, roast settings, live JSON editor).
- **Command routing:** `database.json.command_channels` off/soft/hard with user/god channel targets.

## State & Config
- Primary store `database.json` with defaults in `mandy/app/store_defaults.py`.
- Optional MySQL persistence for watchers, mirrors, audit logs, etc.
- Autosave/reload loops keep JSON in sync.

## RBAC
- Numeric levels (Guest→Staff→Admin→GOD→SUPERUSER). Overrides via `permissions` map or role-level map `rbac.role_levels`.
- Helpers in `mandy/app/main_rbac.py` (untracked file currently in repo tree).

## AI (Gemini)
- Cog: `cogs/mandy_ai.py`; client: `cogs/mandy_ai_gemini.py`.
- Entry: `!mandy <prompt>` and bot mention (GOD-only).
- Fast path intents (DM, mirror, watchers, stats, status, queue/health, extensions, selftest).
- Router JSON actions validated against `mandy/capability_registry.py`; tools provided by `mandy/app/main_tools.py` (untracked in tree).
- Queue + backoff; power mode toggle; limits/cooldowns in `database.json.ai`.
- Extensions loader (`extensions/*.py`) with permissive validator.

## Sentience Layer
- Optional tone/presence/daily reflection/internal monologue/maintenance loops governed by `database.json.sentience` and `presence`.
- Diagnostics board auto-updates (#diagnostics) every 10 minutes.

## Mirrors
- Unified mirror rules (`mirror_rules`) with scopes: server/category/channel.
- Integrity checks, failure thresholds, disable/delete after TTL (`mirror_fail_threshold`, `mirror_disable_ttl`).
- Interactive controls on mirrored messages (reply/DM/jump/mute) when enabled.

## Watchers
- JSON + optional MySQL “after N messages, reply” triggers (`targets`).
- Menu and commands `!addtarget`, `!remove`, `!watchers`; AI can add/remove.

## DM Bridges
- Staff-visible DM channels in admin hub; auto-archive after 24h inactivity.
- Commands: `!dmopen`, `!dmclose`, `!dmai on|off|status|list`.

## Stats
- Windows: daily/weekly/monthly/yearly/rolling24.
- Local/global live panels with pinning and resume; backfill support.

## Media
- VIP auto-join clip for `SPECIAL_VOICE_USER_ID`.
- Movie player (`!movie`, `!volume`): YouTube-only enforcement via `is_youtube_url`; queue, pause/resume/skip/stay/leave; DM control panel.

## Roast Mode (Playful, Opt-in)
- Regex trigger for “mandy” variants + roast intent keywords.
- Per-user opt-in list, channel allow/block, cooldown, history length.
- Gemini-generated roast (toggle `roast.use_ai`) with fallback generator.
- Menu controls + Gemini diagnostic + live JSON editor.

## Gate / Onboarding
- Password gate with per-user private channel and 3 attempts; quarantine role handling.
- Gate tools in God menu (approve, release, reset).

## Ark / Phoenix
- Ark snapshots (roles/channels/pins/log snippets); Phoenix rebuild with confirm key, simulate/run; owner DM reports.

## Logging / Audit
- Structured audit/log routing (`logs` config); logging menu to set targets.
- Debug/system/mirror/AI/voice channels.

## Maintenance Loops
- Config reload, JSON autosave, mirror integrity check, server status update, presence controller, daily reflection, internal monologue, diagnostics board, manual upload, DM bridge archive.

## Tests
- `python -m pytest -q` passes (5 tests). Stubs for `discord` added in `tests/conftest.py`. Coverage is minimal (AI happy-path + docs existence).

## Known Gaps / Risks
- Many untracked/modified core files remain (core/db/logging/media/rbac/tools/intelligence modules). They likely need review/integration before shipping.
- Extensions validator is permissive; AI-generated tools are effectively trusted.
- Media player accepts any URL at YTDL layer despite YouTube check at command entry; if other callers bypass the check, non-YouTube could play.
- Minimal tests; no coverage for mirrors, stats, media, gate, sentience, or RBAC.
- Deprecation warnings resolved for `utcnow`, but other time/UTC handling not audited.

## Recommendations
- Decide fate of untracked modules: align, delete, or commit.
- Harden extension validator (restrict imports/IO/exec).
- Add tests for mirrors/watchers/DM bridge flows and command routing.
- Add guardrails to media_ytdl to reject non-YouTube if called directly.
- Add config schema validation at startup and a `!diag config` report.
