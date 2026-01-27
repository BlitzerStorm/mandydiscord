# Mandy OS Feature Report (2026-01-27)

This report summarizes the bot's current capabilities and parity against archived manuals/commands, based on a code + docs walk-through.

## Core Control Plane
- Prefix commands: `!menu`, `!godmenu`, `!setup`, `!watchers`, `!addtarget`, `!remove`, `!mirroradd`, `!mirroraddscope`, `!mirrorremove`, `!dmopen`, `!dmclose`, `!dmai`, `!mystats`, `!allstats`, `!livestats`, `!globalstats`, `!globallive`, `!movie`, `!volume`, `!clean`, `!nuke`, `!health`, `!leavevc`, `!ambient`, `!ark`, `!phoenix`, `!remember`, `!memory`.
- Menus (UI): User menu (status/help/DM visibility/roast opt-in); God menu (perms, mirrors, watchers, DM bridges, logs, command routing, setup, gate tools, roast settings, live JSON editor).
- Command routing: `database.json.command_channels` off/soft/hard with user/god channel targets and optional cleanup in hard mode.

## State & Config
- Primary store `database.json` with defaults in `mandy/app/store_defaults.py`.
- Optional MySQL persistence for watchers, mirrors, audit logs, etc.
- Autosave/reload loops keep JSON in sync; auto-setup/backfill are configurable.

## RBAC
- Numeric levels (Guest -> Staff -> Admin -> GOD -> SUPERUSER). Overrides via `permissions` map or role-level map `rbac.role_levels`.
- Helpers in `mandy/app/main_rbac.py`.

## AI + Intelligence (Gemini + Rules)
- Cog: `cogs/mandy_ai.py`; client: `cogs/mandy_ai_gemini.py`.
- Entry: `!mandy <prompt>` and bot mention (GOD-only).
- Fast path intents + IntelligentCommandProcessor for natural language; falls back to the Gemini router if needed.
- Queue + backoff; self-tuning cooldown/router model; limits and counters in `database.json.ai`.
- Commands: `!mandy_model`, `!mandy_limits`, `!mandy_queue`, `!mandy_cancel`.
- Router JSON actions validated against `mandy/capability_registry.py`; tools provided by `mandy/app/main_tools.py`.

## Resolver + Clarify UI
- Cross-server user/channel lookup with fuzzy ranking.
- Clarification prompts when ambiguous (buttons/selects).

## Memory + Notes
- `!remember <note>` stores operator notes; `!memory [limit]` shows recent entries.
- Lightweight sentiment logging on chat for long-term context.

## Mirrors
- Unified mirror rules (`mirror_rules`) with scopes: server/category/channel.
- Integrity checks, failure thresholds, disable/delete after TTL (`mirror_fail_threshold`, `mirror_disable_ttl`).
- Reactions on mirrored messages are propagated back to the source when permissions allow.
- Interactive controls on mirrored messages (reply/DM/jump/mute) when enabled.

## Watchers
- JSON + optional MySQL "after N messages, reply" triggers (`targets`).
- Menu and commands `!addtarget`, `!remove`, `!watchers`; AI can add/remove.

## DM Bridges
- Staff-visible DM channels in admin hub; auto-archive after 24h inactivity.
- Commands: `!dmopen`, `!dmclose`, `!dmai on|off|status|list`.

## Stats
- Windows: daily/weekly/monthly/yearly/rolling24.
- Local/global live panels with pinning and resume; backfill support.

## Media + Voice
- VIP auto-join clip for `SPECIAL_VOICE_USER_ID` (suppressed during movie playback).
- Movie player (`!movie`, `!volume`): YouTube-only enforced in YTDL layer, queue, pause/resume/skip/stay/leave, DM control panel.

## Roast Mode (Opt-in)
- Regex trigger for "mandy" variants + roast intent keywords.
- Per-user opt-in list, channel allow/block, cooldown, history length.
- Gemini-generated roast (toggle `roast.use_ai`) with fallback generator.
- Menu controls + Gemini diagnostic + live JSON editor.

## Gate / Onboarding
- Password gate with per-user private channel and 3 attempts; quarantine role handling.
- Gate tools in God menu (approve, release, reset).

## Ark / Phoenix
- Ark snapshots (roles/channels/pins/log snippets); Phoenix rebuild with confirm key, simulate/run.
- Owner DM reports on guild join and Phoenix completion.

## Logging / Audit
- Structured audit/log routing (`logs` config); logging menu to set targets.
- Debug/system/mirror/AI/voice channels.

## Maintenance Loops
- Config reload, JSON autosave, mirror integrity check, server status update, presence controller, daily reflection, internal monologue, diagnostics board, manual upload, DM bridge archive.

## Extensions
- Extensions loader (`extensions/*.py`) with permissive validator.
- AI tool design/build flow via Gemini (design -> confirm -> write -> load).

## Tests
- `python -m pytest -q` passes (7 tests). Stubs for `discord` in `tests/conftest.py`. Coverage is minimal (AI happy-path + docs existence).

## Parity Check (Archived Docs vs Current Code)
- Commands/features listed in archived manuals (`archived/COMMANDS.md`, `archived/MANDY_MANUAL.md`) are present in the current codebase.
- No missing features were identified during this pass.

## Known Gaps / Risks
- Many modified core files remain; they likely need review/integration before shipping.
- Extensions validator is permissive; AI-generated tools are effectively trusted.
- Minimal tests; no coverage for mirrors, stats, media, gate, sentience, or RBAC.
- Discord, voice, MySQL, and Gemini integrations still need runtime smoke testing.

## Recommendations
- Decide fate of modified modules: align, delete, or commit.
- Harden extension validator (restrict imports/IO/exec) before enabling AI tool creation broadly.
- Add tests for mirrors/watchers/DM bridge flows and command routing.
- Add config schema validation at startup and a `!diag config` report.
