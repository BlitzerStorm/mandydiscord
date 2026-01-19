# Mandy OS Manual (A-Z)

Mandy OS is a Discord control-plane bot: command routing, logging, mirrors, DM bridges, watchers, stats, and an AI operator (Gemini) that can run approved tools.

This manual is for operators (Staff/Admin/GOD) running Mandy across multiple servers.

## What Mandy Can (and Cannot) Do

She can:
- Work across every guild the bot is in (cross-server user/channel lookup).
- Mirror messages into a central admin hub.
- Maintain stats, watchers, DM bridges, and audit logs.
- Use Gemini to plan actions and run approved tools (GOD-only by default).

She cannot:
- Access servers she is not in.
- DM users who have DMs closed or block the bot.
- Bypass Discord permissions (missing View Channel, Read Message History, Send Messages, etc.).

## Quick Start

### 0) Discord developer portal checklist
In your bot settings, enable:
- Message Content Intent (required)
- Server Members Intent (recommended; improves cross-server name lookup)

### 1) Install dependencies
```bash
pip install -r requirements.txt
```

### 2) Configure secrets
Copy `passwords.example.txt` to `passwords.txt` and fill in:
- `DISCORD_TOKEN` (required)
- `GEMINI_API_KEY` (optional, enables Mandy AI features)
- `MYSQL_*` (optional, enables MySQL persistence)
- `SERVER_PASSWORD` (optional, enables the admin-server gate)

Never commit `passwords.txt`.

### 3) Choose your "Admin Hub" server
Mandy has a special admin hub guild where she centralizes monitoring and staff tooling.

Edit these constants in `main.py` to match your IDs:
- `ADMIN_GUILD_ID`: your admin hub server (where `!setup` works)
- `SUPER_USER_ID`: the owner (level 100)
- `AUTO_GOD_ID`: optional extra GOD account (level 90)

### 4) Run the bot
```bash
python main.py
```

### 5) Bootstrap the admin hub
In the admin hub (only), run:
- `!setup bootstrap` (safe sync)
- `!setup fullsync` / `!setup destructive` (SUPERUSER-only; rebuilds layout)

### 6) First checks
- `!menu` and `!godmenu`
- `!watchers` (shows watch list)
- `!ambient status`
- If Gemini is configured: `!mandy health`, `!mandy queue`, `!mandy tools`

## Mental Model (How Mandy OS Is Organized)

### Admin hub vs. all other servers
Think of Mandy like this:
- Admin hub: your control room (logs, mirror feeds, server info, DM bridges, gate)
- Other guilds: satellite servers that can be mirrored and monitored

When auto-setup is enabled, Mandy will:
- Create a server folder in the admin hub for each satellite guild.
- Create/maintain a server-scope mirror rule so messages flow into the admin hub.
- Post a pinned Server Info panel (invite link + mirror status + permissions).

### State lives in `database.json` (and optionally MySQL)
Mandy uses `database.json` as the live state/config store. If MySQL is configured, some data is mirrored/persisted there too (watchers, DM bridges, mirror rules, audit logs, etc.).

Good habits:
- Back up `database.json` periodically.
- If you enable MySQL, back up the database too.

## Permissions, Roles, and Levels (RBAC)

Mandy uses a numeric access model:
- 100: SUPERUSER (`SUPER_USER_ID`)
- 90: GOD (`GOD` role by default)
- 70: Admin
- 50: Staff
- 1: Guest / Quarantine (limited)

Effective level is computed as:
1) User override level (MySQL `users_permissions` if enabled, else `database.json.permissions`)
2) Highest mapped role level (`database.json.rbac.role_levels`)
3) The maximum of (1) and (2) wins

Tip: To change what Staff/Admin/GOD means, edit `database.json.rbac.role_levels`.

## Command Routing (Clean Channels)

Mandy can keep servers tidy by forcing commands into dedicated channels.

Configure in GOD menu:
- `!godmenu` -> Command Routing

Modes (`database.json.command_channels.mode`):
- `off`: commands allowed anywhere
- `soft`: forward a reminder + command snippet to the proper channel (does not delete)
- `hard`: forward a reminder + snippet and delete the original command message

Targets (`database.json.command_channels`):
- `user`: normal command channel name (default `command-requests`)
- `god`: GOD command channel name (default `admin-chat`)

## Sentience Layer (Optional)

Mandy can run a lightweight sentience layer that changes tone, status posture, and daily reflections.

Core toggles:
- `database.json.sentience.enabled`: master switch
- `database.json.sentience.dialect`: `sentient_core` or `plain`

Presence controller (bio never changes):
- `database.json.presence.bio`: activity text (set once; never modified by code)
- `database.json.presence.autopresence_enabled`: enable status automation
- Rules: recent messages => online; otherwise idle if members online; otherwise dnd if SUPERUSER active in admin hub; else invisible

Daily cognition report:
- `database.json.sentience.daily_reflection.enabled`: enable daily reflection
- `database.json.sentience.daily_reflection.hour_utc`: optional hour (0-23)
- `database.json.sentience.daily_reflection.max_messages`: context size for logs
- Posts to `#thoughts` in the admin hub if the channel exists (silent skip if missing).

Internal monologue (optional):
- `database.json.sentience.internal_monologue.enabled`: enable periodic monologue
- `database.json.sentience.internal_monologue.interval_minutes`: spacing between posts
- `database.json.sentience.internal_monologue.max_lines`: target line count

Mirror interactivity:
- `database.json.mirrors.interactive_controls_enabled`: buttons under mirrored messages

Self-maintenance:
- `database.json.sentience.maintenance.ai_queue_max_age_hours`: drop stale AI jobs

## Core Commands (Prefix: `!`)

Quick reference lives in `COMMANDS.md`. The highlights:

### Menus
- `!menu`: user menu panel (level >= 10)
- `!godmenu`: admin panel (level >= 90)

### Setup / Ops (admin hub only)
- `!setup bootstrap`
- `!setup fullsync` / `!setup destructive` (SUPERUSER only)
- `!leavevc`: emergency voice disconnect (SUPERUSER only)
- `!health`: health snapshot (level >= 70)

### Watchers / Mirrors / DM bridges
- `!watchers` / `!watcher`: show watch list (level >= 50)
- `!addtarget <user_id> <count> <text>`: add JSON watcher (level >= 90)
- `!remove <user>`: drop someone from JSON + MySQL watches (IDs, mentions, nicknames)
- `!mirroradd <source_channel_id> <target_channel_id>` (level >= 70)
- `!mirroraddscope <server|category|channel> <source_id> <target_channel_id>` (level >= 70)
- `!mirrorremove <source_channel_id> [simulate]` (level >= 70)
- `!dmopen <user_id>` / `!dmclose <user_id>` (admin hub only, level >= 70)
- `!setlogs <system|audit|debug|mirror|ai|voice> <channel_id>` (level >= 90)

### Stats
- `!mystats [daily|weekly|monthly|yearly|rolling24]`
- `!allstats [window]`
- `!livestats [window]` (staff+)
- `!globalstats [window]` (admin+)
- `!globallive [window]` (admin+)

### Cleanup
- `!clean [limit]`: delete bot messages in a channel (level >= 50)
- `!nuke [limit] [simulate]`: SUPERUSER-only heavy cleanup; add `simulate`/`dry` to preview and avoid deleting, Mandy repopulates managed pins/menus after a real nuke

### Media
- `!movie`: GOD-only. Opens the control panel (DM or server). Use the panel or `!movie <url>` to play. Only YouTube links are accepted.
- `!movie add <url>`: Queue a YouTube link.
- `!movie queue`: Show now playing and the queue.
- `!movie stay [minutes]`: Keep the bot in VC after playback (default 15, max 30).
- `!movie leave`: Stop and disconnect.
- `!movie pause` / `!movie resume` / `!movie skip`
- `!movie volume <0-100>` or `!volume <0-100>`
- DM `!movie` opens a control panel with Play/Queue/Skip/Volume/Stay/Leave buttons and a server selector if you're in multiple VCs.

## Mandy AI (Gemini): The Operator

By default, Mandy AI is GOD-only.

### Silent action handling
- When Gemini decides to perform an action (e.g., DM someone or send a report) but has nothing to say, Mandy now records an empty text response instead of rejecting the packet. This prevents the old “Router validation failed: response must be string” crash when the AI stays quiet.

### Entry points
- `!mandy <prompt>`: ask Mandy to answer and/or run tools
- Mention the bot with a prompt (GOD-only): `@Mandy do the thing`

### Two brains: Fast Path vs. Gemini
When you run `!mandy ...`:
1) Fast Path handles common intents without calling Gemini.
2) If Fast Path cannot confidently handle it, Mandy calls Gemini (router model).

### Cross-server smartness
When you say a name like `dm john hello`, Mandy:
- Tries the current server first.
- Falls back to a global search across all guilds the bot is in.
- Uses fuzzy scoring (exact/prefix/contains/fuzzy).
- If ambiguous, she asks you to pick from candidates.

Same idea applies to channels when you reference `#general` without an ID.

### Self-tuning (rate limits)
When Gemini rate limits hit, Mandy automatically adjusts:
- `database.json.ai.cooldown_seconds`
- `database.json.ai.router_model` (switches to a lighter model if needed)

### Queue (rate-limit resistant)
If an AI job is rate-limited, Mandy can enqueue it and retry later.

Commands:
- `!mandy_queue`: show queued jobs
- `!mandy_cancel <job_id>`: cancel a job
- `!mandy_limits`: show cooldown + usage counters

### Prompting tips
Fast Path examples:
- `!mandy dm john, sarah hello`
- `!mandy open dm john`
- `!mandy watch john after 5 say ping|pong|hello`
- `!mandy mirror #general to #archive`
- `!mandy stats weekly for john`
- `!mandy set status dnd building Mandy OS`

Gemini examples (tool + reasoning):
- `!mandy summarize the last 50 messages in #debug-logs`
- `!mandy post an announcement in #announcements: maintenance at 5pm`
- `!mandy build a tool that bans spammers after 3 links (design first)`

## Watchers (The Watch List)

A watcher is a "message counter trigger" for a user:
- After they send N messages, Mandy replies with configured text.
- Multiple reply variants can be separated by `|` (Mandy picks one at random).

Where they live:
- JSON: `database.json.targets["<user_id>"] = {"count": N, "current": 0, "text": "a|b|c"}`
- MySQL (optional): table `watchers`

How to manage:
- Menu: `!godmenu` watcher controls (JSON/MySQL)
- Command: `!addtarget <user_id> <count> <text>` (JSON)
- Command: `!remove <user>`: drop someone from both JSON and MySQL watches
- AI: `!mandy watch <name> after <count> say <text>`
- View: `!watchers`

Important: Watchers are global by user ID. If that user chats in any server the bot sees, the watcher can fire in that server.

### Long-term memory (notes + sentiment)
- `!remember <note>`: save a staff note to Mandy's memory
- `!memory [limit]`: show recent notes/moods
- Mandy auto-logs non-neutral sentiments from chat for richer summaries over time.

## Mirrors (Cross-server Message Relays)

Mandy supports unified mirror rules stored in `database.json.mirror_rules` (and optionally MySQL).

### Rule scopes
- `server`: mirror an entire guild into the admin hub
- `category`: mirror all channels under a category
- `channel`: mirror one source channel to one target channel

### Failure handling (auto-disable + auto-clean)
- `database.json.mirror_fail_threshold`: after N failures, a rule disables itself
- `database.json.mirror_disable_ttl`: if a rule stays disabled longer than this, it is deleted

### Creating mirrors
- Classic: `!mirroradd ...` / `!mirroraddscope ...`
- Menu: `!godmenu` mirror panels
- AI: `!mandy mirror #source to #dest` (cross-server channel lookup supported)
- Removal: `!mirrorremove <source_channel_id> [simulate]`

### Reactions on mirror feeds
- Reacting to a mirrored message in the admin hub will mirror that emoji back onto the original source message (when permissions allow).

### Interactive controls (optional)
When enabled, mirrored messages in the admin hub include buttons:
- Reply (send a reply back to the origin)
- DM Author (send DM or open a DM bridge)
- Jump (link to source message)
- Mute Source (disable the mirror rule)

## Ark + Phoenix Protocol (Rebuild With Memory)

Mandy can snapshot a server (Ark) and rebuild it (Phoenix) with analysis and safety gates.

### Ark (Intelligent Snapshot)
- `!ark [theme]`: captures roles, channels, overwrites, pins, plus legacy logs for key channels.
- Builds an optimized blueprint from real traffic (top words) to suggest new categories.

### Phoenix (Pre-mortem + Rebuild)
- `!phoenix <snapshot_id> simulate`: generates a report and a confirmation key.
- `!phoenix <snapshot_id> run <CONFIRM-...>`: executes the rebuild only after the key matches.

Phoenix behaviors:
- Dynamic architecture based on usage (adds DEVELOPMENT or GAMING categories when relevant).
- Prewired automation (rules channel auto-role via "I agree").
- Legacy preservation (creates `legacy-logs` and reposts recent history).
- Prewired mirrors (announcements mirrored to admin hub feed).
- Owner report after rebuild (members, watchers, and missing permissions).

## Owner Reports (Auto)

Mandy sends an owner DM report when:
- The bot joins a server
- Phoenix rebuild completes

Report contents:
- Member count and a sample list
- Watcher IDs present in that guild
- Missing bot permissions (view/read/send/manage roles/channels/messages)

## DM Bridges (Staff-Visible DMs)

DM bridges let staff handle user DMs inside the admin hub:
- If a user DMs the bot, Mandy can create/activate a `dm-<user_id>` channel.
- Inbound DM is copied into that channel.
- Staff messages in that channel are relayed back to the user (staff level >= 70).
- Old/inactive bridges auto-archive after ~24 hours of inactivity.

Manual controls (admin hub only):
- `!dmopen <user_id>`: open/activate the bridge and dump recent DM history
- `!dmclose <user_id>`: archive the bridge

## Gate (Password-protected Entry)

The admin hub can require a server password:
- Configure `SERVER_PASSWORD` in `passwords.txt` (or env var).
- On join, Mandy creates a private `gate-<username>` channel.
- The user gets 3 attempts; failures quarantine them.

Staff tools:
- `!godmenu` -> Gate Tools (approve guest, release quarantine, reset attempts)

## VIP Voice Auto-Join

- When `SUPER_USER_ID` (your owner/GOD) joins any voice channel, Mandy auto-joins, streams `https://youtu.be/UukrfHmWmuY` with `yt-dlp` + `ffmpeg`, and stays until that VIP leaves.
- After the VIP exits, the bot waits ~27 seconds and disconnects.
- Ensure `ffmpeg` is installed and the bot retains `CONNECT/SPEAK` plus `SEND_MESSAGES` rights where it should speak afterward.
- While a `!movie` session is active in a guild, the VIP auto-join clip is suppressed to avoid competing audio.
- Emergency exit: `!leavevc` forces a hard disconnect from all voice channels (SUPERUSER only).

## Stats (Local + Global)

Mandy tracks message stats and can render panels.

Windows:
- `daily`, `weekly`, `monthly`, `yearly`, `rolling24`

Local server commands:
- `!mystats [window]`: your stats
- `!allstats [window]`: leaderboard
- `!livestats [window]`: pins a live-updating panel

Global commands:
- `!globalstats [window]`: global totals across all guilds
- `!globallive [window]`: pinned live global panel

Auto backfill:
- `database.json.auto.backfill`: backfills recent history so stats are not empty after a restart.

## Ambient Engine (Optional "Life")

The ambient engine adds small alive behaviors (status/typing style effects).

Commands (GOD):
- `!ambient status`
- `!ambient on`
- `!ambient off`

## Configuration Reference (database.json)

Top-level keys you will edit most often:
- `logs`: channel IDs for `system|audit|debug|mirror|ai|voice`
- `command_channels`: routing mode + channel names
- `rbac.role_levels`: role-name -> numeric level mapping
- `permissions`: user-id -> numeric level overrides
- `ai`: Gemini models, cooldown, limits, queue, installed extensions
- `auto`: auto-setup/backfill toggles
- `presence`: bio + autopresence controller toggles
- `sentience`: dialect, daily reflection schedule, maintenance toggles
- `targets`: watchers (JSON)
- `mirror_rules`: mirrors (unified rules)
- `mirrors.interactive_controls_enabled`: mirror buttons in admin hub
- `dm_bridges`: DM bridge state
- `ark_snapshots`, `phoenix_keys`, `memory`, `onboarding`
- `layout`, `channel_topics`, `pinned_text`: what `!setup` creates and pins

Note: Mandy reloads `database.json` automatically and autosaves when she changes state.

## Extension Development

Extensions are how Mandy gains new tools.

### What is a tool?
A tool is an async function Mandy can call with structured arguments, for example:
- send a message
- DM a user
- create a mirror
- manage watchers

Built-in tools are defined by `CapabilityRegistry` and implemented by `ToolRegistry` in `main.py`.

### Writing an extension manually
Put a file in `extensions/` that defines `TOOL_EXPORTS`.

Minimal example (see `extensions/tool_ping.py`):
```py
TOOL_EXPORTS = {
  "tool_ping": {
    "description": "Simple ping tool for plugin testing.",
    "args_schema": {},
    "side_effect": "read",
    "cost": "cheap",
    "handler": _tool_ping,
  }
}

async def _tool_ping(ctx):
  return "OK"
```

Schema fields:
- `description`: short human description
- `args_schema`: dict of arg-name -> schema (`type`, optional ranges/choices)
- `side_effect`: `read` or `write`
- `cost`: `cheap`, `normal`, or `expensive`
- `handler`: `async def handler(ctx, ...)` (first param must be `ctx` or `bot_ctx`)

### Loading extensions
On startup Mandy loads all `extensions/*.py` (except `__init__.py` and `validator.py`).

### Building tools with Mandy AI
Mandy can propose and build new tools through the Gemini router:
1) Ask for a tool ("design a tool that...")
2) Mandy returns a design (confirmable)
3) If you confirm, Mandy writes `extensions/<slug>.py` and loads it

### Validator and safety (important)
Extensions are validated by `extensions/validator.py`.

Current repo note: the validator is permissive (it mostly checks syntax/size). Treat AI-generated tools as trusted code. If you want a locked-down model, tighten the validator (deny dangerous imports/calls) before letting Mandy generate tools freely.

## A-Z Glossary (because you asked for A-Z)

A - Ambient Engine: optional alive behaviors (`!ambient ...`)
B - Backfill: fills stats/mirrors history after joining/restart
C - Command Routing: off/soft/hard enforcement of command channels
D - DM Bridges: staff-visible DM relay channels in the admin hub
E - Extensions: `extensions/*.py` tool plugins
F - Fail Threshold: mirror auto-disable after N failures
G - Gate: password entry flow in the admin hub
H - Health: `!mandy health` / status reports
I - Intelligence Layer: rule-based natural language + clarifications
J - Jobs: queued AI requests when rate limited
K - Keys: `passwords.txt` / environment variables
L - Logs: system/audit/debug/mirror routing
M - Mirrors: cross-server relays with scopes and cleanup
N - Nicknames: fuzzy/global lookup across guilds
O - Ops: `!setup`, MySQL bootstrap, server info panels
P - Permissions: RBAC + per-user overrides (JSON/MySQL)
Q - Quarantine: gate failure holding role/channel
R - Resolver: fuzzy matching for users/channels/roles
S - Stats: per-guild + global message analytics and live panels
T - Tools: the internal capability API Mandy can call
U - UI: menus, confirms, and pickers (buttons/selects/modals)
V - Validator: decides what extensions may load
W - Watchers: "after N messages, reply with ..." triggers
X - eXtras: MySQL mode, auto-setup, and plugins
Y - Yearly window: long-range stats (`yearly`)
Z - Zero-drama reload: `database.json` hot reload + autosave
