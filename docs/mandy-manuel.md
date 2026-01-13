# mandy-manuel

This is a complete, single manual for the Mandy Discord bot system as it exists in this workspace.
All details below are derived from the current code in `d:\DIscord bot`.

---

## 1) System overview

Mandy is a Discord bot with:
- A full moderation/control plane (roles, gates, logs, menus, mirrors, watchers, stats).
- An AI layer (Gemini-based router) for GOD-only natural language requests.
- A deterministic fast-path NLU layer for common actions (DM, mirror, stats, tools, etc).
- A tool registry and plugin system for extensions.
- Optional MySQL storage with JSON fallback.

There are three major processing paths:
1) Prefix commands (e.g., `!menu`, `!setup`, `!mirroradd`) handled in `main.py`.
2) GOD-only natural language fast path in `cogs/mandy_ai.py` using `UniversalIntelligenceLayer`.
3) GOD-only AI router (Gemini) in `cogs/mandy_ai.py` for complex intents.

There is also a legacy intelligent processor (`intelligent_command_processor.py`) present but it is not currently wired into runtime execution.

---

## 2) Directory layout (key files)

Root:
- `main.py`: bot startup, core services, menus, and prefix commands.
- `cogs/mandy_ai.py`: Mandy AI (router, queue, tools execution, fast path).
- `intelligence_layer.py`: deterministic NLU for natural language tools.
- `intelligent_command_processor.py`: legacy NLU module (not used in runtime).
- `capability_registry.py`: tool definitions and argument validation.
- `tool_plugin_manager.py`: dynamic extension loader and tool registration.
- `resolver.py`: fuzzy resolver for users/channels/roles.
- `clarify_ui.py`: UI views used in clarification flows.
- `cooldown_store.py`: cooldown storage for mention-based DM warnings.
- `update_restrictions.py`: helper script to remove DM restrictions in `cogs/mandy_ai.py`.
- `quick_wins.py`: helper utilities (not invoked by default).
- `verify_integration.py`: optional verification helper.

Folders:
- `cogs/`: bot cogs, including `mandy_ai.py`.
- `extensions/`: extension tools and validators.
- `tests/`: basic tests for Mandy AI and docs.
- `docs/`: existing manual and system summaries.
- `_zip_extract/`: extracted backup copy of the project.

---

## 3) Boot and configuration

### 3.1 Secrets and environment
Mandy loads secrets from `passwords.txt` and env vars:
- `DISCORD_TOKEN` (required)
- `SERVER_PASSWORD` (join gate)
- `MYSQL_HOST`, `MYSQL_DB`, `MYSQL_USER`, `MYSQL_PASS` (optional)
- `GEMINI_API_KEY` (Gemini AI router)

If `DISCORD_TOKEN` is missing, the bot exits on startup.

### 3.2 JSON store
`database.json` is the live data store. On first run, it is created from `DEFAULT_JSON`.
The store auto-loads, merges new defaults, and autosaves regularly.

Important sections:
- `targets`: watcher definitions (JSON mode).
- `mirror_rules`, `mirror_status`, `mirror_message_map`: mirroring system.
- `dm_bridges`: active DM bridge mapping.
- `permissions`: per-user overrides.
- `rbac.role_levels`: role-to-level mapping.
- `logs`: system/audit/debug/mirror channels.
- `command_channels`: where commands are allowed.
- `ai`: model config, limits, queue, extensions.
- `gate`: join gate status.
- `auto`: auto-setup and backfill options.

### 3.3 Optional MySQL
MySQL is optional. If connected, the bot auto-creates and migrates tables:
- `users_permissions`
- `mirrors`, `mirror_rules`, `mirror_messages`
- `watchers`
- `dm_bridges`
- `audit_logs`

If MySQL fails, Mandy falls back to JSON without crashing.

---

## 4) Permissions and roles

Role levels:
- `GOD`: 90
- `Admin`: 70
- `Staff`: 50
- `Guest`: 1
- `Quarantine`: 1

Effective level is based on:
- MySQL `users_permissions` (if enabled), or
- JSON `permissions` overrides, or
- Highest assigned role level.

Key constants:
- `MANDY_GOD_LEVEL = 90`
- `SUPER_USER_ID` has special access for `!nuke`.

---

## 5) Command routing and enforcement

### 5.1 Prefix commands
Prefix is `!`. Commands are only allowed in specific channels:
- `command_channels.user` (default `command-requests`)
- `command_channels.god` (default `admin-chat`)

If a user types a prefix command in the wrong channel, the message is deleted and redirected.

### 5.2 GOD mention entrypoint
If a GOD mentions the bot, the content after the mention is routed to Mandy AI fast path only.
Non-GOD mentions trigger a cooldown DM ("You're not a god.") handled by `CooldownStore`.

### 5.3 Mandy AI command
`!mandy` is GOD-only and runs:
1) Fast path NLU (deterministic tools).
2) Audio transcription (if an attachment exists).
3) Gemini router (JSON intent) if fast path did not handle.
4) Tool execution via `_execute_actions`.

---

## 6) Mandy AI: router and queue

### 6.1 Router protocol
The Gemini router returns JSON with keys:
- `intent`: one of `TALK`, `ACTION`, `NEEDS_CONFIRMATION`, `DESIGN_TOOL`, `BUILD_TOOL`
- `response`, `actions`, `tool_design`, `build`, `confirm` (as required by intent)

Invalid or malformed JSON is rejected.

### 6.2 Rate limits and queue
Local rate limits are enforced per model:
- `rpm`, `tpm`, `rpd` from `ai.limits` in JSON config.

When limited:
- The job can be queued.
- The user can choose WAIT or CANCEL.
- Backoff uses `BACKOFF_STEPS = [10, 30, 60, 120, 240, 480, 600]`.

Queue is stored in `ai.queue`.

### 6.3 Commands for AI
GOD-only commands in `cogs/mandy_ai.py`:
- `!mandy <query>`: run AI router.
- `!mandy_model <name>`: set default model.
- `!mandy_queue`: show queued jobs.
- `!mandy_cancel <job_id>`: cancel a queued job.
- `!mandy_limits`: show local limits and usage.

---

## 7) Fast path NLU (deterministic)

The fast path uses `UniversalIntelligenceLayer`:
- Detects intent via regex and keyword scoring.
- Resolves users/channels/roles with fuzzy matching (`resolver.py`).
- Asks clarifying questions using Discord UI (select menus or prompts).
- Can require confirmation for sensitive actions.

Supported fast-path intents:
- DM a user
- Send message to a channel
- Create mirror
- Add/remove/list watchers
- Show stats
- Set bot status
- List capabilities
- Show health / queue
- List/disable mirror rules

The fast path can also handle direct text commands:
- `tools`, `health`, `queue`, `extensions`, `selftest`, `cancel <job_id>`

Note: `intelligent_command_processor.py` exists but is not invoked by runtime code.

---

## 8) Tools and capabilities

`capability_registry.py` defines tool specs, validation, and summaries.
Built-in tools include:
- `send_message`, `reply_to_message`, `set_bot_status`
- `get_recent_transcript`
- `add_watcher`, `remove_watcher`, `list_watchers`
- `list_mirror_rules`, `create_mirror`, `disable_mirror_rule`
- `show_stats`, `send_dm`
- `list_capabilities`

Each tool has:
- required and optional args
- type validation and ranges
- permission level metadata
- cost tier and side effects

---

## 9) Extensions and plugins

### 9.1 Extension loader
`tool_plugin_manager.py` loads modules from `extensions/`.
Each extension must export `TOOL_EXPORTS`:
- `description`
- `args_schema`
- `side_effect` ("read" or "write")
- `cost` ("cheap", "normal", "expensive")
- `handler` (async function, first param `ctx` or `bot_ctx`)

The manager validates:
- tool name format
- schema fields and types
- handler signature

### 9.2 Validator behavior (current)
`extensions/validator.py` is currently unrestricted:
- no import restrictions
- no blocked calls
- size limit (500 KB)
- syntax check only

`extensions/validator_unrestricted.py` is similarly permissive.

### 9.3 Example extension
`extensions/tool_ping.py` shows a minimal tool with no args.

---

## 10) Mirrors

Mirrors replicate messages across channels based on rules:
- Rule scopes: server, category, channel.
- Rules stored in JSON and (optionally) MySQL.

Mirror features:
- Message ID mapping for replies and DM actions.
- Mirror integrity checks (disables broken rules).
- Automatic mirror feed per server in the admin guild.
- Optional backfill of recent messages.

Mirror controls:
- `MirrorControls` view adds buttons: direct reply, post, DM user.

Commands:
- `!mirroradd <source_channel_id> <target_channel_id>`
- `!mirroraddscope <scope> <source_id> <target_channel_id>`
- `!mirrorremove <source_channel_id>`

Menu access is also available through GOD menus.

---

## 11) Watchers

Watchers send a message after a user posts N messages.
Two storage modes:
- JSON `targets` in `database.json`
- MySQL `watchers` table (optional)

Tick flow:
- `watcher_tick()` increments per-message counters.
- When threshold is reached, a message is sent and the count resets.

Tools and menus:
- `add_watcher`, `remove_watcher`, `list_watchers`
- GOD menu views for JSON/MySQL watcher management.

---

## 12) DM bridge

DM bridge creates a private admin channel per user:
- User DMs to bot are forwarded to the bridge channel.
- Staff replies in the bridge channel are DM'd back to the user.
- Typing indicators are mirrored.

Commands:
- `!dmopen <user_id>`
- `!dmclose <user_id>`

Bridges can auto-open and auto-archive based on activity.

---

## 13) Gate system (admin guild only)

The gate uses `SERVER_PASSWORD`:
- New members get a private gate channel.
- Correct password grants access; repeated failures quarantine.
- Gate status is tracked in `database.json`.

Tools and menus:
- Gate controls in GOD menu.
- `gate_approve_user`, `gate_quarantine_user`, `gate_reset_attempts`.

---

## 14) Chat stats

Stats tracking:
- per-user and global stats
- windows: daily, weekly, monthly, yearly, rolling24

Commands:
- `!mystats [window]`
- `!allstats [window]`
- `!livestats [window]`
- `!globalstats [window]`
- `!globallive [window]`

Live panels are stored and restored on restart.

---

## 15) Menus and UI

Discord UI views provide interactive management:
- User menu: quick actions and status.
- GOD menu: mirrors, watchers, logs, gate, setup, etc.
- Bot status view: set presence and status text.
- Mirror menus: toggle rules and scopes.
- Logging menu: set audit/debug/mirror channels.

Commands:
- `!menu` (level >= 10)
- `!godmenu` (level >= 90)

---

## 16) Logging and audit

Log channels:
- `system`, `audit`, `debug`, `mirror`

Audit logging writes to:
- `audit_logs` table if MySQL is enabled
- Audit channel in Discord

Set log channels:
- `!setlogs audit|debug|mirror <channel_id>`

---

## 17) Auto setup and backfill

Auto setup builds categories, channels, and pinned text based on `layout` and `channel_topics`.
Backfill options:
- `auto.setup`
- `auto.backfill`
- `auto.backfill_limit`
- `auto.backfill_per_channel`
- `auto.backfill_delay`

Setup can be invoked from:
- `!setup [fullsync|auto|destructive]` (admin guild, level >= 70)

---

## 18) AI build and design flows

The AI router can propose tools:
- `DESIGN_TOOL` creates a tool design and requests confirmation.
- `BUILD_TOOL` writes an extension file after confirmation.

Build steps:
1) Validate file path.
2) Validate source (currently unrestricted).
3) Write file to `extensions/`.
4) Load plugin via `ToolPluginManager`.
5) Add module to `ai.installed_extensions`.

---

## 19) Tests and docs

Tests:
- `tests/test_mandy_ai.py` validates Mandy AI behavior.
- `tests/test_docs.py` checks that `docs/MANDY_MANUAL.md` exists and contains key sections.

Docs:
- Existing manuals in `docs/` remain as-is.
- This file is the new complete system manual.

---

## 20) Troubleshooting

Startup failures:
- Missing `DISCORD_TOKEN` will stop the bot.

AI errors:
- Missing `GEMINI_API_KEY` disables the AI router.
- Gemini rate limits are handled by queue/backoff.

MySQL issues:
- If MySQL init fails, system falls back to JSON only.

Mirrors:
- Integrity checks auto-disable broken rules.
- Mirror logs show errors in `mirror-logs`.

DM bridge:
- If a bridge channel is missing, it is re-created on demand.

Gate:
- Password is checked against `SERVER_PASSWORD`.

---

## 21) Command reference (prefix)

User or staff:
- `!menu`
- `!mystats [daily|weekly|monthly|yearly|rolling24]`
- `!allstats [daily|weekly|monthly|yearly|rolling24]`
- `!livestats [window]` (level >= 50)
- `!globalstats [window]`
- `!globallive [window]` (level >= 70)
- `!clean [limit]` (level >= 50)

Admin or GOD:
- `!godmenu` (level >= 90)
- `!setup [fullsync|auto|destructive]` (admin guild only, level >= 70)
- `!addtarget <user_id> <count> <text>` (level >= 90)
- `!mirroradd <source_channel_id> <target_channel_id>` (level >= 70)
- `!mirroraddscope <scope> <source_id> <target_channel_id>` (level >= 70)
- `!mirrorremove <source_channel_id>` (level >= 70)
- `!dmopen <user_id>` (admin guild only, level >= 70)
- `!dmclose <user_id>` (admin guild only, level >= 70)
- `!setlogs audit|debug|mirror <channel_id>` (level >= 90)
- `!nuke [limit]` (SUPER_USER_ID only)

AI:
- `!mandy <query>` (GOD-only)
- `!mandy_model <name>` (GOD-only)
- `!mandy_queue` (GOD-only)
- `!mandy_cancel <job_id>` (GOD-only)
- `!mandy_limits` (GOD-only)

---

## 22) What is "legacy"

The following components are present but not actively used in runtime:
- `intelligent_command_processor.py` (legacy NLU, replaced by `UniversalIntelligenceLayer`).

If you want that module re-integrated, it can be wired into `_handle_fast_path`.

---

End of manual.
