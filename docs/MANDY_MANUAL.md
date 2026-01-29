# Mandy SOC Runbook (Single-File Manual)

This is the canonical "how to run Mandy" manual for operators (Staff/Admin/GOD) running the bot across multiple servers with a central admin hub.

Mandy OS is a Discord control-plane bot: command routing, logging, mirrors, DM bridges, watchers, stats, and an AI operator (Gemini) that can run approved tools.

## 0x00 Capabilities (What Mandy Can and Cannot Do)

Mandy can:
- Work across every guild the bot is in (cross-server user/channel lookup).
- Mirror messages into a central admin hub (your "SOC").
- Maintain stats, watchers, DM bridges, and audit logs.
- Use Gemini to plan actions and run approved tools (GOD-only by default).

Mandy cannot:
- Access servers she is not in.
- DM users who have DMs closed or who block the bot.
- Bypass Discord permissions (View Channel, Read Message History, Send Messages, etc.).

## 0x01 First Boot (Quick Start)

### 0) Discord Developer Portal checklist
Enable in the bot's settings:
- Message Content Intent (required).
- Server Members Intent (recommended; improves cross-server name lookup).

### 1) Install dependencies
```bash
pip install -r requirements.txt
```

### 2) Configure secrets
Copy `passwords.example.txt` to `passwords.txt` and fill in:
- `DISCORD_TOKEN` (required).
- `GEMINI_API_KEY` (optional; enables Mandy AI features).
- `MYSQL_*` (optional; enables MySQL persistence).
- `SERVER_PASSWORD` (optional; enables the admin-server gate).

Never commit `passwords.txt`.

### 3) Pick your Admin Hub server ("control room")
Mandy has a special admin hub guild where she centralizes monitoring and staff tooling.

Edit these constants in `main.py` to match your IDs:
- `ADMIN_GUILD_ID`: your admin hub server (where `!setup` works).
- `SUPER_USER_ID`: the owner (level 100).
- `AUTO_GOD_ID`: optional extra GOD account (level 90).

### 4) Run
```bash
python main.py
```

### 5) Bootstrap the admin hub (admin hub only)
- `!setup bootstrap` (safe sync).
- `!setup fullsync` / `!setup destructive` (SUPERUSER-only; rebuilds layout).
- `!setup_audit` (shows missing pieces and permission issues after setup).

### 6) First checks ("ping the perimeter")
- `!menu`, `!godmenu`, and `!servermenu`.
- `!setlogs system <channel_id>` (and the other log routes).
- `!watchers` and `!health`.
- `!ambient status`.
- If Gemini is configured: `!mandy health`, `!mandy queue`, `!mandy tools`, `!mandyping`.

## 0x02 Mental Model (How Mandy Is Wired)

### Admin hub vs. satellite servers
Think of Mandy like this:
- Admin hub: your control room (logs, mirror feeds, server info panels, DM bridges, gate tools).
- Other guilds: satellites that can be mirrored and monitored.

When auto-setup is enabled, Mandy can:
- Create a server folder in the admin hub for each satellite guild.
- Create/maintain a server-scope mirror rule so messages flow into the admin hub.
- Post a pinned Server Info panel (invite link + mirror status + permissions).

### State lives in `database.json` (and optionally MySQL)
Mandy uses `database.json` as the live state/config store. If MySQL is configured, some data is mirrored/persisted there too (watchers, DM bridges, mirror rules, audit logs, etc.).

Good habits:
- Back up `database.json` periodically.
- If you enable MySQL, back up the database too.

## 0x03 Access Control (RBAC)

Mandy uses numeric access levels:
- 100: SUPERUSER (`SUPER_USER_ID`).
- 90: GOD (`GOD` role by default).
- 70: Admin.
- 50: Staff.
- 10: Member (required for `!menu`).
- 1: Guest / Quarantine (limited).

Effective level is computed as:
1) User override level (MySQL `users_permissions` if enabled, else `database.json.permissions`).
2) Highest mapped role level (`database.json.rbac.role_levels`).
3) The max of (1) and (2) wins.

Tip: To change what Staff/Admin/GOD means, edit `database.json.rbac.role_levels`.

## 0x04 Consoles (Menus)

Mandy is menu-driven by design. The menus are your "terminal UI".

- `!menu` (level >= 10): user menu panel (status/help/DM visibility/roast opt-in).
- `!godmenu` (level >= 90): admin control panel (perms, mirrors, watchers, DM bridges, logs, routing, setup, gate tools, roast settings, live JSON editor).
- `!servermenu` (level >= 90): server-only admin panel (includes Bot Interop/manual tests/macros).

## 0x05 Clean Channels (Command Routing)

Mandy can keep servers tidy by forcing commands into dedicated channels.

Configure in GOD menu:
- `!godmenu` -> Command Routing.

Modes (`database.json.command_channels.mode`):
- `off`: commands allowed anywhere.
- `soft`: forward a reminder + command snippet to the proper channel (does not delete).
- `hard`: forward a reminder + snippet and delete the original command message.

Targets (`database.json.command_channels`):
- `user`: normal command channel name (default `command-requests`).
- `god`: GOD command channel name (default `admin-chat`).

## 0x06 Logs (Wiretap Your Own Bot)

Logs are routed by channel ID in `database.json.logs`.

Command (level >= 90):
- `!setlogs <system|audit|debug|mirror|ai|voice> <channel_id>`

## 0x07 Sentience Layer (Optional)

Mandy can run a lightweight sentience layer that changes tone, status posture, and periodic reports.

Core toggles:
- `database.json.sentience.enabled`: master switch.
- `database.json.sentience.dialect`: `sentient_core` or `plain`.

Presence controller (bio never changes):
- `database.json.presence.bio`: activity text (set once; never modified by code).
- `database.json.presence.autopresence_enabled`: enable status automation.

Daily reflection (optional):
- `database.json.sentience.daily_reflection.enabled`
- `database.json.sentience.daily_reflection.hour_utc` (0-23)
- `database.json.sentience.daily_reflection.max_messages`
- Posts to `#thoughts` in the admin hub if the channel exists (silent skip if missing).

Internal monologue (optional):
- `database.json.sentience.internal_monologue.enabled`
- `database.json.sentience.internal_monologue.interval_minutes`
- `database.json.sentience.internal_monologue.max_lines`

Self-maintenance:
- `database.json.sentience.maintenance.ai_queue_max_age_hours`: drop stale AI jobs.

Mirror interactivity:
- `database.json.mirrors.interactive_controls_enabled`: buttons under mirrored messages.

## 0x08 Modules (What You Actually Operate)

### Watchers (Tripwires)

A watcher is a message counter trigger for a user:
- After they send N messages, Mandy replies with configured text.
- Multiple reply variants can be separated by `|` (Mandy picks one at random).

Where it lives:
- JSON: `database.json.targets[user_id] = {count,current,text}`
- MySQL (optional): table `watchers`

Commands:
- View: `!watchers` (level >= 50).
- Add: `!addtarget <user_id> <count> <text>` (level >= 90).
- Remove: `!remove <user>` (drops JSON + MySQL watches; IDs/mentions/nicknames) (level >= 50).

Long-term memory (notes + sentiment):
- `!remember <note>`
- `!memory [limit]`

### Mirrors (Relays)

Mirrors are how messages flow from a satellite server/channel into the admin hub.

Scopes:
- server: mirror a whole guild (except excluded).
- category: mirror a category.
- channel: mirror a single channel.

Commands (level >= 70):
- `!mirroradd <source_channel_id> <target_channel_id>`
- `!mirroraddscope <server|category|channel> <source_id> <target_channel_id>`
- `!mirrorremove <source_channel_id> [simulate]`

Failure handling:
- Mirror rules disable after N send failures.
- Disabled rules are auto-deleted after a TTL (`database.json.mirror_disable_ttl`).

Optional: reaction propagation + interactive controls on mirrored messages (buttons under mirror feed).

### DM Bridges (Staff-Visible DMs)

DM bridges create staff-visible channels in the admin hub that mirror DMs to/from a user.

Commands (admin hub only):
- `!dmopen <user_id>` (level >= 70).
- `!dmclose <user_id>` (level >= 70).
- `!dmai on|off|status|list <user_id|@user|#channel|this>` (GOD-only).

Notes:
- DM bridges auto-archive after inactivity.
- DM channels include a pinned control panel.

### Gate (Password-Protected Entry)

If `SERVER_PASSWORD` is set:
- New joiners get a private gate channel.
- They get 3 attempts.
- Failing puts them into a quarantine role/channel.

Gate tools live in `!godmenu`.

### Roast Mode (Opt-In, Playful Only)

Roasts are automatic (no `!roast` command). If enabled, Mandy will reply directly to a user who opts in.

Trigger requirements:
- Roast must be enabled (`roast.enabled`).
- The user must be opted in (via `!menu` -> Roast Opt-In), or auto-opt-in must be enabled for the guild.
- Message must mention the bot OR match a regex variant of the trigger word (default `mandy`).
- Message must include roast intent (ex: `roast me`, `insult`, `make fun`, `mock`, `diss`, `clown me`, `trash me`), or Gemini can classify it as roast intent.

Admin controls:
- `!godmenu` -> Roast Settings: enable/disable, toggle Gemini generation, manage allowlists.
- Mass allowlists (GOD-level):
  - `!roast_whitelist_guild [here|all|<guild_id>] [add|remove|clear]` (controls `roast.allowed_guilds`).
  - `!roast_whitelist_users [here|all|<guild_id>] [add|remove|clear]` (controls `roast.auto_opt_in_guilds`).

Common config keys (`database.json.roast`):
- `enabled`, `trigger_word`, `use_ai`, `cooldown_seconds`, `max_history`
- `opt_in_users`, `allowed_guilds`, `auto_opt_in_guilds`
- `allowed_channels`, `blocked_channels`

### Stats (Local + Global)

Windows:
- `daily`, `weekly`, `monthly`, `yearly`, `rolling24`

Local commands:
- `!mystats [window]`
- `!allstats [window]`
- `!livestats [window]` (staff+)

Global commands:
- `!globalstats [window]` (admin+)
- `!globallive [window]` (admin+)

Auto backfill:
- `database.json.auto.backfill`

### Cleanup (Purge)

- `!clean [limit]` (level >= 50): delete the bot's own recent messages in the channel.
- `!nuke [limit] [simulate]` (SUPERUSER): heavy cleanup; add `simulate`/`dry` to preview.

### Media + Voice

- `!movie` (GOD-only): opens the control panel (DM or server). YouTube-only is enforced.
- `!movie <url>` / `!movie add <url>` / `!movie queue`
- `!movie pause` / `!movie resume` / `!movie skip`
- `!movie stay [minutes]` / `!movie leave`
- `!movie volume <0-100>` or `!volume <0-100>`
- `!leavevc` (SUPERUSER): emergency voice disconnect

VIP auto-join:
- If `SPECIAL_VOICE_USER_ID` is set, Mandy can auto-join when that user joins voice (disabled during movie playback).

### Ark + Phoenix (Rebuild With Memory)

- `!ark [theme]`: captures roles, channels, overwrites, pins, plus legacy logs for key channels.
- `!phoenix <snapshot_id> simulate`: generates a report and a confirmation key.
- `!phoenix <snapshot_id> run <CONFIRM-...>`: executes the rebuild only after the key matches.

### Ambient Engine (Optional "Life")

- `!ambient status` (GOD)
- `!ambient on` (GOD)
- `!ambient off` (GOD)

### Ops / Health

- `!health` (level >= 70): health snapshot.
- `!cancel` (SUPERUSER): cancels running background tasks/loops.

## 0x09 Mandy AI (Gemini Operator)

By default, Mandy AI is GOD-only.

Entry points:
- `!mandy <prompt>`
- `!mandyai <prompt>` (bypass fast path/regex and go straight to AI)
- Mention the bot with a prompt (GOD-only): `@Mandy do the thing`

Control commands:
- `!mandy_model <flash-lite|flash|pro|model-name>`
- `!mandy_queue`
- `!mandy_cancel <job_id>`
- `!mandy_limits`
- `!mandy_power on|off|status`
- `!mandyping`

How requests are handled:
1) The intelligent "fast path" tries to interpret common natural-language intents without calling Gemini.
2) If fast path cannot confidently handle it, Mandy asks whether to submit to AI.
   - `!mandyai` skips the fast path and submits directly to AI.
3) If Gemini fails or rate-limits, Mandy automatically falls back to the other model (AgentRouter) and announces it.
4) If both models fail, the request is cancelled and the user is notified.

Fast path examples (natural language inside `!mandy ...`):
- `!mandy dm john, sarah hello`
- `!mandy open dm john`
- `!mandy watch john after 5 say ping|pong|hello`
- `!mandy stop watching john`
- `!mandy show watchers`
- `!mandy mirror #general to #archive`
- `!mandy stats weekly for john`
- `!mandy health`
- `!mandy queue`
- `!mandy tools`
- `!mandy selftest`

Gemini examples (tool + reasoning):
- `!mandy summarize the last 50 messages in #debug-logs`
- `!mandy post an announcement in #announcements: maintenance at 5pm`
- `!mandy build a tool that bans spammers after 3 links (design first)`

Self-tuning (rate limits):
- When Gemini rate limits hit, Mandy can automatically adjust `database.json.ai.cooldown_seconds` and switch `database.json.ai.router_model` to a lighter model.
- Fallback model: `database.json.ai.agent_router_model` (default `gpt-4o-mini`) is used for automatic failover.

## 0x0A Setup / Rebuild Runbooks (Admin Hub Only)

Setup commands (admin hub only):
- `!setup` (menu: Bootstrap vs Fullsync vs Destructive).
- `!setup bootstrap`
- `!setup fullsync` / `!setup destructive` (SUPERUSER only).
- `!setup_bio` (SUPERUSER only): bio-themed admin hub rebuild.
- `!setup_audit`: quick audit report after rebuilds.

Manual auto-upload:
- Enabled by default (`manual.auto_upload_enabled`).
- Mandy posts the latest `docs/MANDY_MANUAL.md` to `#manual-for-living` (tracked by file hash).

Diagnostics:
- `#diagnostics` is auto-updated status board (periodic loop).

## 0x0B Configuration Reference (database.json)

Common keys you will edit:
- `logs`: channel IDs for `system|audit|debug|mirror|ai|voice`.
- `command_channels`: routing mode + channel names.
- `rbac.role_levels`: role-name -> numeric level mapping.
- `permissions`: user-id -> numeric level overrides.
- `ai`: Gemini models, cooldown, limits, queue, installed extensions.
- `roast`: roast-mode toggles, allowlists, channels, cooldown.
- `auto`: auto-setup/backfill toggles.
- `manual`: manual upload config and last hash.
- `presence`: bio + autopresence controller toggles.
- `sentience`: dialect, reflection schedule, maintenance toggles.
- `targets`: watchers.
- `mirror_rules`: mirrors (unified rules).
- `mirrors.interactive_controls_enabled`: mirror buttons in admin hub.
- `mirror_disable_ttl`: seconds before a disabled mirror rule is auto-deleted.
- `dm_bridges`: DM bridge state.
- `ark_snapshots`, `phoenix_keys`, `memory`, `onboarding`.
- `layout`, `channel_topics`, `pinned_text`: what `!setup` creates and pins.

Note: Mandy reloads `database.json` automatically and autosaves when she changes state.

## 0x0C Extensions (Tools/Plugins)

Extensions are how Mandy gains new tools.

What is a tool?
- A tool is an async function Mandy can call with structured arguments (send a message, DM a user, create a mirror, manage watchers, etc.).
- Built-in tools are defined by `CapabilityRegistry` and implemented by `ToolRegistry` in `main.py`.

Writing an extension manually:
- Put a file in `extensions/` that defines `TOOL_EXPORTS` (see `extensions/tool_ping.py`).

Loading extensions:
- On startup Mandy loads all `extensions/*.py` (except `__init__.py` and `validator.py`).

Building tools with Mandy AI:
1) Ask for a tool ("design a tool that...").
2) Mandy returns a design (confirmable).
3) If you confirm, Mandy writes `extensions/<slug>.py` and loads it.

Validator and safety:
- Extensions are validated by `extensions/validator.py`.
- Current repo note: the validator is permissive (it mostly checks syntax/size). Treat AI-generated tools as trusted code unless you harden the validator.

## 0x0D Troubleshooting (Incident Response)

Roast mode not firing:
- Confirm `database.json.roast.enabled` is `true`.
- Confirm you opted in via `!menu` -> Roast Opt-In (or your guild is in `roast.auto_opt_in_guilds`).
- Confirm you are in an allowed guild/channel (allow/block lists).
- Confirm you're mentioning the bot or using the trigger word (default `mandy`) plus roast intent.

Menus not opening:
- Check your effective RBAC level (menu is >= 10; godmenu/servermenu are >= 90).
- Check command routing mode (hard mode may delete commands outside the command channel).

Stats look empty:
- Enable `database.json.auto.backfill` and/or wait for new messages to accumulate.

Gemini not responding:
- Confirm `GEMINI_API_KEY` exists and the bot can reach the network.
- Use `!mandy health` and `!mandy_limits` to inspect rate limits and cooldown.

## 0x0E Glossary (A-Z, minimal)

A - Ambient Engine: optional alive behaviors (`!ambient ...`)
B - Backfill: fills stats/mirrors history after joining/restart
C - Command Routing: off/soft/hard enforcement of command channels
D - DM Bridges: staff-visible DM relay channels in the admin hub
E - Extensions: `extensions/*.py` tool plugins
F - Fail Threshold: mirror auto-disable after N failures
G - Gate: password entry flow in the admin hub
H - Health: `!health` and `!mandy health`
I - Intelligence Layer: fast path + clarifications before Gemini router
J - Jobs: queued AI requests when rate limited
K - Keys: `passwords.txt` / environment variables
L - Logs: system/audit/debug/mirror routing
M - Mirrors: cross-server relays with scopes and cleanup
N - Nicknames: fuzzy/global lookup across guilds
O - Ops: `!setup`, MySQL bootstrap, server info panels
P - Permissions: RBAC + per-user overrides (JSON/MySQL)
Q - Quarantine: gate failure holding role/channel
R - Resolver: fuzzy matching for users/channels/roles
S - Stats: per-guild + global analytics and live panels
T - Tools: the internal capability API Mandy can call
U - UI: menus, confirms, and pickers (buttons/selects/modals)
V - Validator: decides what extensions may load
W - Watchers: "after N messages, reply with ..." triggers
X - eXtras: MySQL mode, auto-setup, and plugins
Y - Yearly window: long-range stats (`yearly`)
Z - Zero-drama reload: `database.json` hot reload + autosave

---

## Appendix A: Command Cheat Sheet (Verbatim Snapshot)

# Mandy OS Command Cheat Sheet (Hacker Edition)

Prefix: `!`

Canonical operator manual: `docs/MANDY_MANUAL.md` (this file is the quick terminal sheet).

## Menus / Consoles
- `!menu` (level >= 10): user panel (status/help/DM visibility/roast opt-in)
- `!godmenu` (level >= 90): admin panel (routing/logs/mirrors/watchers/DM bridges/gate/roast/live JSON)
- `!servermenu` (level >= 90): server-only admin panel (Bot Interop/manual tests/macros)

## Setup / Ops (admin hub only)
- `!setup` (menu: bootstrap/fullsync/destructive)
- `!setup bootstrap`
- `!setup fullsync` / `!setup destructive` (SUPERUSER only)
- `!setup_bio` (SUPERUSER only)
- `!setup_audit` (level >= 70): setup audit report

## Watchers / Mirrors / DM bridges
- `!watchers` / `!watcher` (level >= 50): show watcher list
- `!addtarget <user_id> <count> <text>` (level >= 90): add watcher
- `!remove <user>` (level >= 50): remove watcher (JSON + MySQL if configured)
- `!mirroradd <source_channel_id> <target_channel_id>` (level >= 70)
- `!mirroraddscope <server|category|channel> <source_id> <target_channel_id>` (level >= 70)
- `!mirrorremove <source_channel_id> [simulate]` (level >= 70)
- `!dmopen <user_id>` / `!dmclose <user_id>` (admin hub only, level >= 70)
- `!dmai on|off|status|list <user_id|@user|#channel|this>` (admin hub only, GOD)

## Logging / Routing
- `!setlogs <system|audit|debug|mirror|ai|voice> <channel_id>` (level >= 90)

## Roast Mode (opt-in only)
- User: `!menu` -> Roast Opt-In
- Admin: `!godmenu` -> Roast Settings
- Mass enablement (GOD):
  - `!roast_whitelist_guild [here|all|<guild_id>] [add|remove|clear]`
  - `!roast_whitelist_users [here|all|<guild_id>] [add|remove|clear]`
- Trigger (after opt-in): mention Mandy or include the trigger word (default `mandy`) + intent like "roast me"

## Stats
- `!mystats [daily|weekly|monthly|yearly|rolling24]`
- `!allstats [window]`
- `!livestats [window]` (staff+)
- `!globalstats [window]` (admin+)
- `!globallive [window]` (admin+)
- `!remember <note>`
- `!memory [limit]`

## Cleanup
- `!clean [limit]` (level >= 50)
- `!nuke [limit] [simulate]` (SUPERUSER)

## Media + Voice
- `!movie` (GOD): control panel (DM or server)
- `!movie <url>` / `!movie add <url>` / `!movie queue`
- `!movie pause` / `!movie resume` / `!movie skip`
- `!movie stay [minutes]` / `!movie leave`
- `!movie volume <0-100>` or `!volume <0-100>`
- `!leavevc` (SUPERUSER)

## Ark / Phoenix
- `!ark [theme]`
- `!phoenix <snapshot_id> simulate`
- `!phoenix <snapshot_id> run <CONFIRM-...>`

## Ambient Engine
- `!ambient status` / `!ambient on` / `!ambient off` (GOD)

## Health / Control
- `!health` (level >= 70)
- `!cancel` (SUPERUSER)

## Mandy AI (Gemini)
- `!mandy <prompt>`: natural language + tools (GOD-only by default)
- Mention `@Bot <prompt>`: GOD-only mention handler
- `!mandy_model <flash-lite|flash|pro|model-name>`
- `!mandy_limits`
- `!mandy_queue`
- `!mandy_cancel <job_id>`
- `!mandy_power on|off|status`
- `!mandyping`

Natural-language examples (via `!mandy ...`):
- `!mandy dm john hey`
- `!mandy watch john after 5 say ping me`
- `!mandy mirror #general to #backup`
- `!mandy stats daily for john`

---

## Appendix B: Intelligent Command Processor Notes (Verbatim Snapshot)

# Intelligent Command Processor (Fast Path) - Operator Notes

Canonical operator manual: `docs/MANDY_MANUAL.md`

This doc is a focused note on the "fast path" natural-language layer used by the `!mandy` command before Gemini is invoked.

## What It Is

The Intelligent Command Processor (ICP) is a rule-based interpreter that recognizes common operator intents (DMs, watchers, mirrors, stats, health, queue) using pattern matching and fuzzy extraction.

It is designed to:
- reduce Gemini calls for routine tasks,
- ask clarification questions instead of failing when ambiguous,
- fall back safely to the rest of Mandy's logic if it cannot handle the message.

## Where It Lives

- Core engine: `mandy/intelligent_command_processor.py`
- Integration (executor bridge + wiring): `cogs/mandy_ai.py`
  - Executor: `_ManydAICommandExecutor`
  - Processor: `IntelligentCommandProcessor`

## Command Flow (Simplified)

```
!mandy <text>
  -> Fast path (ICP) attempts to handle
     -> If handled: runs Mandy tools and returns
     -> If not handled: other fast-path logic runs
        -> If still not handled: Gemini router runs
```

## Supported Intents (Examples)

These examples are typically written as `!mandy ...` prompts.

1) Send DMs
```
dm @john hello
message john "hello"
tell john hi
```

2) Add watcher
```
watch john after 5 messages say hey
monitor john and say hey every 5 messages
```

3) Remove watcher
```
stop watching john
remove watcher john
unwatch john
```

4) List watchers
```
show watchers
list watchers
what watchers are active
```

5) Mirror channels
```
mirror #general to #backup
sync #a with #b
```

6) Show stats
```
show stats
stats daily
how much has john messaged today
```

7) Health check
```
health
status
check bot status
```

8) Queue status
```
queue
show queue
check jobs
```

## Notes / Limitations

- ICP is best-effort: if it cannot confidently execute, it should clarify or fall back.
- ICP does not replace prefix commands; it just makes `!mandy ...` feel like a terminal.
- Exact patterns evolve over time; treat this doc as a behavior overview, not a spec.

---

## Appendix C: Feature Report (2026-01-27) (Verbatim Snapshot)

# Mandy OS Feature Report (2026-01-27)

Canonical operator manual: `docs/MANDY_MANUAL.md`

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
- Per-user opt-in list, guild allowlist + auto-opt-in by guild, channel allow/block, cooldown, history length.
- Gemini-generated roast (toggle `roast.use_ai`) with fallback generator.
- Menu controls + Gemini diagnostic + live JSON editor.
- Commands: `!roast_whitelist_guild` and `!roast_whitelist_users` for mass enablement.

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

## Extension Development
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

---

## Appendix D: Debug Report (2026-01-27) (Verbatim Snapshot)

# Mandy OS Debug Report (2026-01-27)

Canonical operator manual: `docs/MANDY_MANUAL.md`

## Scope
Static audit and debug pass with local tooling. Runtime integration (Discord API, voice, MySQL, Gemini) cannot be fully exercised in this environment.

## Checks Run
- `python -m pytest -q` -> **7 passed**
- `python -m compileall -q .` -> **no errors**
- Import audit across `mandy/`, `cogs/`, `extensions/` with Discord stubs -> **no errors**

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
- Discord stubs expanded for tests/import audit (`tests/conftest.py`).
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
