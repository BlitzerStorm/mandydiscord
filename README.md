# Mandy Discord

Mandy is a Discord control-plane bot with satellite server management, SOC-tier access control, DM relay, watcher automation, and a layered AI runtime. The current build adds adaptive memory, permission awareness, approval-gated autonomy, agency decisions, telemetry, and Discord-native controls.

Mandy is not conscious and does not have real free will. In code, she can evaluate context, choose from allowed actions, log why she chose them, and ask the right authority for missing permissions.

## What Mandy Does

- Builds and maintains an Admin Hub for operations, logs, mirrors, DM bridges, and engineering controls.
- Mirrors satellite server activity into Admin Hub channels.
- Enforces SOC tiers with a GOD override and server-owner-aware controls.
- Relays user DMs through staff-visible bridge channels.
- Runs watcher counters and configured responses.
- Runs AI chat, roast, DM, shadow, and hive behaviors with caching, cooldowns, prompt clamping, and API fallback tracking.
- Learns lightweight user facts, relationship arcs, inside jokes, server style, language patterns, and memory summaries.
- Lets admins inspect, pin, edit, export, or forget memory.
- Supports privacy controls: pause learning, resume learning, export memory, forget user memory, and audit logs.
- Scores messages before deciding to ignore, react, reply, or direct-reply.
- Adds agency mode for ambient messages, with configurable thresholds and a decision ledger.
- Uses permission intelligence to detect missing Discord permissions, identify server owners/high-authority users, and ask for help.
- Gates autonomous actions through modes, allow-lists, approval proposals, cooldowns, audit logs, and rollback hints where available.
- Runs scheduled reflection compaction to turn noisy learned memory into cleaner summaries.
- Tracks AI telemetry: calls, models, cache hits, inflight joins, fallback rate, cooldowns, budget throttles, tokens, and cost estimate.
- Provides a controlled wake broadcast command for known DM contacts; it is manual, permission-gated, and cooldown protected.
- Runs tests automatically in GitHub Actions on push and pull request.

## Runtime Shape

Entry points:

- `run_bot.py`
- `src/mandy_v1/bot.py`

Core services:

- `src/mandy_v1/storage.py` - MessagePack state, default schema, corrupt-store recovery, atomic save.
- `src/mandy_v1/config.py` - settings from `passwords.txt` and environment variables.
- `src/mandy_v1/services/ai_service.py` - AI calls, memory, prompt assembly, attention, agency, telemetry, privacy.
- `src/mandy_v1/services/autonomy_engine.py` - continuous behavior selection and outcome tracking.
- `src/mandy_v1/services/behavior_library.py` - autonomous behavior factories.
- `src/mandy_v1/services/agent_core_service.py` - final policy/directive layer for proposed actions.
- `src/mandy_v1/services/permission_intelligence_service.py` - permission scans, owner/authority detection, permission request planning.
- `src/mandy_v1/services/server_control_service.py` - guarded Discord mutations.
- `src/mandy_v1/cogs/intelligence_controls.py` - Discord commands for privacy, telemetry, reflection compaction, agency, and wake broadcast.

Other major service groups:

- Admin/SOC/layout: `admin_layout_service.py`, `soc_service.py`, `logger_service.py`
- Mirrors and DM bridges: `mirror_service.py`, `dm_bridge_service.py`
- AI state: `emotion_service.py`, `episodic_memory_service.py`, `identity_service.py`, `persona_service.py`, `culture_service.py`
- Expansion/shadow/runtime: `expansion_service.py`, `shadow_league_service.py`, `runtime_coordinator_service.py`

UI helpers live in `src/mandy_v1/ui/`.

## Requirements

- Python 3.11+
- Discord bot token
- Discord Admin Hub guild ID
- Discord Developer Portal intents:
  - Message Content Intent
  - Server Members Intent

Install dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

Mandy reads `passwords.txt` from the repo root and also supports environment variables for the same settings.

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

AI key lookup order:

1. Settings value.
2. Environment variables: `ALIBABA_API_KEY`, `DASHSCOPE_API_KEY`, `QWEN_API_KEY`, `AI_API_KEY`.
3. `passwords.txt` keys: `ALIBABA_API_KEY`, `DASHSCOPE_API_KEY`, `QWEN_API_KEY`, `AI_API_KEY`, `API_KEY`.

## Running

```bash
python run_bot.py
```

On startup Mandy loads MessagePack state, starts autosave, ensures Admin Hub structure, reconciles satellites, restores DM bridge panels, starts background loops, and shuts those loops down cleanly when the bot exits.

Core mode defaults to off. When enabled, core mode suppresses the more autonomous loops while keeping the bot controllable.

## Admin Hub

Default categories:

- `WELCOME`
- `OPERATIONS`
- `SHADOW LEAGUE`
- `SATELLITES`
- `GUEST ACCESS`
- `ENGINEERING`
- `GOD CORE`

Important channels:

- `system-log`
- `audit-log`
- `debug-log`
- `mirror-log`
- `data-lab`
- `dm-bridges`
- `mandy-thoughts`

`mandy-thoughts` is private to authorized users and receives short decision summaries for AI replies and autonomy events.

## AI, Memory, and Agency

Mandy builds prompts from persona, mood, identity, server culture, user profile, reflections, fun mode, curiosity plan, agency state, episodic memory, runtime context, agent core, permission intelligence, and prompt overrides.

Attention and agency:

- Direct Mandy mentions or wake words force high attention.
- Otherwise Mandy scores context from relationship, curiosity, interests, memory relevance, and recency.
- Addressed messages can be ignored, reacted to, replied to, or direct-replied to.
- Unaddressed messages go through agency mode when enabled.
- Agency mode can choose ambient ignore, reaction, or reply based on thresholds, cooldowns, and permission policy.
- Agency decisions are stored in `ai.agency.decision_log`.

Memory controls:

- Long-term memory facts are inspectable and editable.
- Admins can pin important facts, edit noisy facts, or forget facts.
- Users can pause learning, export their memory, or request forgetting.
- Reflection compaction periodically summarizes noisy memory into stable traits, preferences, open social threads, and ongoing storylines.

## Autonomy and Safety

Autonomy is policy-gated. Mandy can propose and execute structured Discord actions only when the active autonomy mode, allow-list, approval policy, cooldowns, permissions, and agent core all allow it.

Important safety behavior:

- High-risk or external-contact actions require approval.
- `!autonomyapproval on` stores proposed payloads instead of executing immediately.
- `!autonomyapprove <proposal_id>` executes a stored proposal after review.
- Action attempts are logged with status and reason.
- Permission intelligence can identify who to ask when Mandy lacks permissions.
- Server owners are treated as high-authority users for their own guild.

The bot should not mass-DM people automatically on deploy. Wake broadcast is an explicit command with preview, cooldown, and SOC authorization.

## Commands

Prefix default: `!`

Health/setup:

- `!health`
- `!selfcheck [api]`
- `!setup`
- `!menupanel`
- `!housekeep`
- `!housekeephere [channel]`
- `!satellitesync`
- `!leaveserver <guild_id> confirm [public_message]`
- `!syncaccess`

Prompt and AI controls:

- `!setprompt <global|guild_id> <off|light|full> <prompt...>`
- `!showprompt [global|guild_id]`
- `!funmode this|<guild_id> show|balanced|chaotic|cozy|serious|roast|lore|helper`
- `!reflect [guild_id]`
- `!compactreflections [guild_id]`
- `!skills`
- `!telemetry`
- `!agentcore show|on|off|directive <text>`
- `!agency show|on|off|thresholds <ambient_min> <reply_min>`

Memory and privacy:

- `!memory <user_id>`
- `!memory pin <user_id> <index>`
- `!memory unpin <user_id> <index>`
- `!memory edit <user_id> <index> <fact_text>`
- `!memory forget <user_id> <index>`
- `!privacy status|pause|resume|export|forget [user_id] [reason]`
- `!privacyaudit`

Autonomy:

- `!autonomymode show|assist|trusted|wild|off`
- `!autonomyapproval show|on|off`
- `!autonomyallow`
- `!autonomyallow add <action>`
- `!autonomyallow remove <action>`
- `!autonomydash`
- `!autonomyapprove <proposal_id>`

Permission intelligence:

- `!permscan [guild_id]`
- `!authority [guild_id]`
- `!permask <guild_id> <capability> [reason]`
- `!storymode show|on|off`
- `!ambient show|on|off`

SOC and permissions:

- `!socset <user_id> <tier>`
- `!socrole <role_name> <tier>`
- `!permlist`
- `!permgrant <satellite_guild_id> <user_id> <action> <once|perm|revoke>`

Watchers:

- `!watchers`
- `!watchers add <user_id> <threshold> <response_text>`
- `!watchers remove <user_id>`
- `!watchers reset <user_id>`

DM bridge:

- `!user`
- `!user <user_id>`
- `!close dm`
- `!dmreopen`

Onboarding and guests:

- `!onboarding`
- `!onboarding <user_id>`
- `!setguestpass <password>`
- `!guestpass <password>`

Self automation:

- `!selftasks`
- `!selftasks create <interval> <name...>`
- `!selftasks run <task_id>`
- `!selftasks delete <task_id>`
- `!selftasks enable <task_id> <on|off>`
- `!selftasks prompt <task_id> <prompt...>`

Controlled outreach:

- `!wakebroadcast preview [limit]`
- `!wakebroadcast send <limit> [message]`

Hidden/GOD:

- `!mandyaicall ...`

## SOC Tiers

Default tiers:

- `1` guest
- `10` member
- `50` engineer
- `70` admin
- `90` SOC
- GOD bypass by `GOD_USER_ID`

Satellite owners are treated as fully authorized for their own guild-scoped control paths where applicable.

## Persistence

Default state path:

- `data/mandy_v1.msgpack`

Store behavior:

- Recursive schema backfill on load.
- Corrupt store backup/reset.
- Atomic `.tmp` write and replace.
- Periodic autosave.
- Runtime state is stored in MessagePack, not SQL.

Important roots include `ai`, `emotion`, `episodic`, `identity`, `personas`, `culture`, `autonomy`, `autonomy_policy`, `wake_broadcast`, and Admin Hub/satellite state.

## Permissions

Recommended Discord permissions:

- View Channels
- Send Messages
- Read Message History
- Add Reactions
- Manage Messages
- Manage Channels
- Manage Roles
- Manage Nicknames
- Create Invite
- Moderate Members
- Kick Members

When permissions are missing, Mandy should log the failure, avoid crashing live handlers, and use permission intelligence to identify the right person to ask.

## Testing

Run:

```bash
python -m compileall -q src tests run_bot.py
python -m pytest -q
```

GitHub Actions also runs tests on push and pull request through `.github/workflows/tests.yml`.

Current tests cover prompt injection, AI cache/cooldowns, self-checks, DM bridges, mirror cleanup, emotion, episodic memory, persona updates, attention bounds, culture calibration, server control failures, autonomy approvals, permission intelligence, wake broadcast safety, and agency decisions.

## Troubleshooting

`DISCORD_TOKEN is required in passwords.txt`:

- Create `passwords.txt`.
- Set `DISCORD_TOKEN`.

`ADMIN_GUILD_ID is required in passwords.txt`:

- Set a valid Admin Hub guild id.

AI replies are not happening:

- Confirm chat mode is enabled for the guild.
- Confirm an API key is available.
- Check `!telemetry`, `!selfcheck api`, prompt learning mode, cooldowns, and budget throttles.

Autonomous actions are not executing:

- Check `!autonomydash`.
- Check `!autonomyapproval`.
- Check whether the action is allowed for the current autonomy mode.
- Check Discord permissions with `!permscan`.
- Review `debug-log` and `mandy-thoughts`.

Mandy is not talking ambiently:

- Check `!agency show`.
- Check `!ambient show`.
- Lower thresholds with `!agency thresholds <ambient_min> <reply_min>` if desired.
- Confirm the channel is not under cooldown.

No `mandy-thoughts` output:

- Re-run `!setup`.
- Confirm the channel exists under `ENGINEERING`.
- Confirm the reply or autonomy path actually reached a decision.

## Quick Start

1. Configure `passwords.txt`.
2. Install dependencies.
3. Run `python run_bot.py`.
4. Run `!setup` in the Admin Hub.
5. Run `!selfcheck` and `!selfcheck api`.
6. Invite Mandy to a satellite guild.
7. Run `!permscan`.
8. Enable or tune AI behavior with `!funmode`, `!agency`, `!ambient`, and `!autonomymode`.
9. Watch `debug-log`, `data-lab`, and `mandy-thoughts` while sending test traffic.
