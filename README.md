# Mandy v1

Mandy v1 is a Discord control-plane bot with mirrored satellite management, SOC-tier access control, DM relay, watcher automation, and a layered AI persona system built around persistent MessagePack state.

This repo now includes the original control-plane features plus the sentience/autonomy layer implemented in code:
- emotional state
- episodic memory
- identity/opinions
- per-user persona adaptation
- per-server culture adaptation
- attention gating
- internal thought logging
- proactive initiative loops
- autonomous server-action planning
- expansion/invite workflows

## Overview

Mandy currently does all of the following:

- Maintains an Admin Hub layout and satellite debug/mirror structure.
- Mirrors satellite traffic into Admin Hub views.
- Enforces SOC-style access tiers with a hard GOD override.
- Supports guest-password onboarding and invite workflows.
- Runs DM bridge channels for staff-visible DM relay.
- Tracks watcher thresholds and sends configured responses.
- Runs AI chat and roast behavior with cached completions, warmup scans, and learning modes.
- Learns lightweight relationship/profile/style state from live traffic.
- Tracks a persistent emotional state and injects it into prompts.
- Records episodic memories per guild and recalls them into future prompts.
- Seeds and evolves Mandy opinions/interests/dislikes.
- Builds per-user persona profiles, relationship arcs, inside references, and slang absorption.
- Builds per-server culture profiles and assigns Mandy a role per guild.
- Scores attention before deciding whether to ignore, react, or reply.
- Logs reply reasoning into a private `mandy-thoughts` engineering channel.
- Plans optional autonomous server actions alongside replies.
- Runs proactive loops for callbacks, absent-user mentions, curiosity bursts, lore callbacks, relationship maintenance, and self-nickname changes.
- Tracks expansion targets and invite/distribution state for cross-server growth.

All persistent runtime data is stored in MessagePack through `src/mandy_v1/storage.py`. There is no SQL layer.

## Architecture

Main runtime:
- `run_bot.py`
- `src/mandy_v1/bot.py`

Core persistence/config:
- `src/mandy_v1/storage.py`
- `src/mandy_v1/config.py`
- `src/mandy_v1/prompts.py`

Operational services:
- `src/mandy_v1/services/logger_service.py`
- `src/mandy_v1/services/soc_service.py`
- `src/mandy_v1/services/admin_layout_service.py`
- `src/mandy_v1/services/mirror_service.py`
- `src/mandy_v1/services/onboarding_service.py`
- `src/mandy_v1/services/dm_bridge_service.py`
- `src/mandy_v1/services/watcher_service.py`
- `src/mandy_v1/services/shadow_league_service.py`

AI and sentience services:
- `src/mandy_v1/services/ai_service.py`
- `src/mandy_v1/services/emotion_service.py`
- `src/mandy_v1/services/episodic_memory_service.py`
- `src/mandy_v1/services/identity_service.py`
- `src/mandy_v1/services/persona_service.py`
- `src/mandy_v1/services/culture_service.py`
- `src/mandy_v1/services/server_control_service.py`
- `src/mandy_v1/services/expansion_service.py`
- `src/mandy_v1/services/proactive_service.py`

UI helpers:
- `src/mandy_v1/ui/global_menu.py`
- `src/mandy_v1/ui/satellite_debug.py`
- `src/mandy_v1/ui/mirror_actions.py`
- `src/mandy_v1/ui/dm_bridge.py`

## Requirements

- Python 3.11+
- A Discord bot token
- A Discord Admin Hub guild ID
- Discord developer portal intents enabled:
- `MESSAGE CONTENT INTENT`
- `SERVER MEMBERS INTENT`

Install:

```bash
pip install -r requirements.txt
```

## Configuration

Mandy reads config from `passwords.txt` in the repo root.

1. Copy `passwords.example.txt` to `passwords.txt`
2. Fill in values

Required:
- `DISCORD_TOKEN`
- `ADMIN_GUILD_ID`

Optional:
- `GOD_USER_ID` default `741470965359443970`
- `COMMAND_PREFIX` default `!`
- `STORE_PATH` default `data/mandy_v1.msgpack`
- `ALIBABA_API_KEY`
- `ALIBABA_BASE_URL` default `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
- `ALIBABA_MODEL` default `qwen-plus`

Example:

```txt
DISCORD_TOKEN=your_bot_token_here
ADMIN_GUILD_ID=123456789012345678
GOD_USER_ID=741470965359443970
COMMAND_PREFIX=!
STORE_PATH=data/mandy_v1.msgpack
ALIBABA_API_KEY=
ALIBABA_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
ALIBABA_MODEL=qwen-plus
```

AI key probing order:
1. `ALIBABA_API_KEY` from settings
2. environment vars `ALIBABA_API_KEY`, `DASHSCOPE_API_KEY`, `QWEN_API_KEY`, `AI_API_KEY`
3. `passwords.txt` keys `ALIBABA_API_KEY`, `DASHSCOPE_API_KEY`, `QWEN_API_KEY`, `AI_API_KEY`, `API_KEY`

## Running Mandy

```bash
python run_bot.py
```

On startup Mandy:
- loads MessagePack state
- starts autosave
- ensures Admin Hub structure
- reconciles satellites
- restores DM bridge panels
- starts housekeeping, shadow, onboarding recheck, send-probe, hive-sync, satellite reconcile, self-automation, and proactive loops

## Admin Hub Layout

Default categories:
- `WELCOME`
- `OPERATIONS`
- `SHADOW LEAGUE`
- `SATELLITES`
- `GUEST ACCESS`
- `ENGINEERING`
- `GOD CORE`

Key engineering/control channels:
- `system-log`
- `audit-log`
- `debug-log`
- `mirror-log`
- `data-lab`
- `dm-bridges`
- `mandy-thoughts`

`mandy-thoughts` is private to SOC-tier users. Mandy writes internal pre-reply thought summaries there before replying.

## Core Runtime Behavior

### Satellite management

For each non-admin guild Mandy:
- ensures Admin Hub satellite mirror/debug channels
- maintains debug dashboards
- syncs access visibility
- warms up AI context from recent visible history

### Watchers

Watchers are persistent counters with threshold-triggered responses. They are global in store, but filtered by permission context in commands/UI.

### DM bridge

DMs can be opened into `dm-<user_id>` channels in Admin Hub. Each bridge exposes:
- AI response toggle
- open/close toggle
- history refresh

### Shadow League

Shadow League remains active and autonomous. It uses existing shadow planning plus the broader AI memory/context layers.

## Sentience and Autonomy Features

### Emotion state

Stored under `emotion`:

```python
"emotion": {
    "state": "neutral",
    "intensity": 0.5,
    "last_updated": 0,
    "event_log": []
}
```

Implemented behavior:
- mood fetch via `EmotionService.get_mood()`
- state shifts via `shift(trigger, delta)`
- prompt tag injection via `mood_tag()`
- decay toward neutral over idle time
- tracked triggers for spam, warm interactions, interest hits, ignored messages, reply sends, quiet periods, and guild joins

### Episodic memory

Stored under `episodic`:

```python
"episodic": {
    "episodes": {}
}
```

Implemented behavior:
- rolling per-channel buffers in `bot.py`
- flush every 10 messages with up to 15 buffered entries
- per-guild episode storage capped to 200
- search, weighted recall, and boost
- top matching memories injected as `[MEMORY]` blocks into AI prompts

### Identity and opinions

Stored under `identity`:

```python
"identity": {
    "seeded": False,
    "opinions": {},
    "interests": [],
    "dislikes": []
}
```

Implemented behavior:
- seed opinions/interests/dislikes on first boot
- build an identity block for every prompt
- optionally form new opinions from episodic records

### Persona profiles

Stored under `personas` keyed by user id.

Tracked profile fields include:
- aliases
- communication style
- average message length
- vocab complexity
- cared-about topics
- emotional register
- response-to-Mandy mode
- relationship depth
- inside references
- total interactions
- notable moments
- arc
- absorbed slang

Implemented behavior:
- per-message profile updates
- relationship depth growth
- arc transitions
- inside-reference capture
- slang absorption and cross-server reuse thresholds
- prompt voice block per user

### Culture profiles

Stored under `culture` keyed by guild id.

Tracked profile fields include:
- detected tone
- dominant topics
- activity peaks
- humor style
- formality
- average message length
- emoji density
- lore refs
- dominant language style
- Mandy adopted persona
- calibration state

Implemented behavior:
- passive observation on every readable guild message
- calibration after 50 observed messages
- AI/fallback persona assignment per server
- culture block injection per prompt

### Attention gating

Before public AI replies Mandy computes a bounded attention score from:
- relationship warmth
- curiosity mood boost
- interest keyword hit
- episodic relevance hit
- recency bonus

Decision bands:
- `< 0.2` ignore
- `0.2-0.45` react
- `0.45-0.65` mixed reply/react
- `> 0.65` reply

Wake-word or explicit Mandy mention still forces attention to `1.0`.

### Internal monologue

Before every reply Mandy can write a short summary into `#mandy-thoughts`:

```txt
[HH:MM] #channel | @user
Mood: state/intensity | Attention: score
Memories: ... | Decision: reply
```

Dedup is in-memory with a short TTL to avoid spammy duplicates.

### Server control layer

`ServerControlService` centralizes autonomous Discord mutations behind try/except wrappers.

Implemented service coverage includes:
- create/delete/rename channel
- set topic
- set slowmode
- lock/unlock channel
- pin/unpin message
- create/delete/rename role
- assign/remove role
- nickname member
- kick/timeout member
- bulk delete
- send normal message or embed
- react
- set server name
- create invite
- list members/channels

`AIService` can now plan optional structured `server_action` payloads alongside replies. `bot.py` executes them through `ServerControlService`.

Autonomous action logging:
- writes to Admin Hub debug/internal note flow first
- writes to `mandy-thoughts`
- emits structured logger events

### Expansion engine

Stored under `expansion`:

```python
"expansion": {
    "target_users": {},
    "known_servers": {},
    "approach_log": [],
    "invite_links": {},
    "cooldowns": {},
    "queue": [],
    "last_scan_ts": 0
}
```

Implemented behavior:
- identify candidate users across current guilds
- queue approaches
- send soft outreach DMs
- track positive signals mentioning other servers/invites
- generate invite pitches
- create and distribute actual invites with rotation state
- log newly joined guilds

### Proactive engine

Stored under `proactive`:

```python
"proactive": {
    "guild_cooldowns": {},
    "user_cooldowns": {},
    "nicknames": {},
    "last_loop_ts": 0
}
```

Implemented background loop behavior:
- absent-user callouts
- episodic callback messages
- curiosity bursts
- expansion queue processing
- confidant maintenance DMs
- lore callbacks
- self-directed nickname updates based on culture

## Prompt Assembly

Per-message prompt assembly in `AIService` now layers context in this order:

1. base Mandy persona
2. mood tag
3. identity block
4. server culture block
5. user profile block
6. episodic memory block
7. per-guild prompt override
8. global prompt override
9. extra instruction block for the active mode

Block caps currently enforced in code:
- base persona: 200 chars
- mood: 40 chars
- identity: 300 chars
- server culture: 400 chars
- user profile: 500 chars
- memory: 300 chars

Guild/global prompt override clamping continues to use the existing prompt clamp logic.

## AI Runtime Notes

Existing AI behavior that still applies:
- completion caching
- prompt clamping
- API cooldown backoff
- model fallback probing
- vision-aware reply path
- startup warmup scans
- learning modes `off|light|full`
- burst spam short-circuiting

New AI/runtime additions:
- attention score calculation
- contextual prompt builder
- reply payload path with optional server action
- sentiment/persona/culture/memory conditioning
- internal thought logging before reply

## Commands

Prefix default is `!`.

Health/setup:
- `!health`
- `!selfcheck [api]`
- `!setup`
- `!menupanel`
- `!housekeep`
- `!housekeephere [channel]`
- `!satellitesync`
- `!syncaccess`
- `!setprompt <global|guild_id> <off|light|full> <prompt...>`
- `!showprompt [global|guild_id]`
- `!permlist`
- `!permgrant <satellite_guild_id> <user_id> <action> <once|perm|revoke>`

SOC:
- `!socset <user_id> <tier>`
- `!socrole <role_name> <tier>`

Watchers:
- `!watchers`
- `!watchers add <user_id> <threshold> <response_text>`
- `!watchers remove <user_id>`
- `!watchers reset <user_id>`

Onboarding:
- `!onboarding`
- `!onboarding <user_id>`

DM bridge:
- `!user`
- `!user <user_id>`
- `!close dm`
- `!dmreopen`

Guest password:
- `!setguestpass <password>`
- `!guestpass <password>`

Satellite debug:
- `!debugpanel`

Self automation:
- `!selftasks`
- `!selftasks create <interval> <name...>`
- `!selftasks run <task_id>`
- `!selftasks delete <task_id>`
- `!selftasks enable <task_id> <on|off>`
- `!selftasks prompt <task_id> <prompt...>`

Hidden GOD mode:
- `!mandyaicall ...`

## SOC Tiers

Default tiers:
- `1` guest
- `10` member
- `50` engineer
- `70` admin
- `90` SOC
- hardcoded GOD bypass by `GOD_USER_ID`

Satellite owners are treated as fully authorized for their own guild-scoped control paths where applicable.

## Persistence

Default state path:
- `data/mandy_v1.msgpack`

Store behavior:
- schema default backfill on load
- atomic `.tmp` write/replace
- periodic autosave loop
- all new autonomy/sentience state is part of the same MessagePack store

## Logging and Diagnostics

Structured runtime events are written to:
- in-memory/store log rows
- stdout
- Admin Hub debug/internal note channels
- satellite debug channels for relevant events

Autonomous server-mutating actions are logged before execution in the format:

```txt
[AUTONOMOUS] {action} on {target} — reason: {reason}
```

## Permissions

Recommended Discord permissions:
- Manage Channels
- Manage Roles
- View Channels
- Send Messages
- Read Message History
- Add Reactions
- Manage Messages
- Create Invite
- Moderate Members
- Kick Members

If permissions are missing, Mandy generally degrades silently and logs the failure instead of crashing a live handler.

## Testing

Useful validation:

```bash
python -m py_compile src/mandy_v1/bot.py src/mandy_v1/services/*.py
python -m pytest -q
```

Current tests cover:
- AI prompt injection behavior
- AI caching/cooldown controls
- bot self-check/task flows
- DM bridge behavior
- mirror cleanup/access behavior
- emotion transitions
- episodic record/search
- persona profile update behavior
- attention score bounds
- culture calibration
- server control permission failures

## Troubleshooting

`DISCORD_TOKEN is required in passwords.txt`:
- create `passwords.txt`
- fill `DISCORD_TOKEN`

`ADMIN_GUILD_ID is required in passwords.txt`:
- set a valid admin guild id

No satellite mirror/debug channels:
- confirm the bot joined the target guild
- confirm Admin Hub exists and Mandy can create channels/roles
- inspect `debug-log`

AI replies not happening:
- confirm AI chat mode is enabled for that guild
- confirm an API key is available if the path needs remote completion
- inspect prompt injection mode and last API test in dashboard/selfcheck

No `mandy-thoughts` output:
- confirm Admin Hub layout was re-ensured after updating
- confirm the channel exists under `ENGINEERING`
- confirm the reply path actually reached AI reply generation

Autonomous actions not firing:
- Mandy only plans them when the prompt path sees a clear reason
- execution also depends on live Discord permissions
- check `debug-log` and `mandy-thoughts`

## Quick Start

1. Configure `passwords.txt`
2. Install requirements
3. Run `python run_bot.py`
4. Verify Admin Hub categories/channels exist
5. Verify `ENGINEERING/mandy-thoughts` exists
6. Run `!selfcheck`
7. Run `!selfcheck api`
8. Invite Mandy to a satellite guild
9. Enable AI mode for that guild from the debug panel/menu
10. Watch `debug-log`, `data-lab`, and `mandy-thoughts` while sending test traffic
