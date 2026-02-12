# Mandy v1 (Discord Control Plane)

Mandy v1 is a centralized Discord SOC/control-plane bot for multi-server monitoring, access control, mirrors, onboarding, and staff DM relay.

This build is intentionally lean and optimized for reliable v1 operations.

## 1) What Mandy v1 does

- Auto-builds and maintains Admin Hub layout.
- Auto-onboards joined servers into satellite mirror folders.
- Mirrors satellite messages into Admin Hub mirror feeds.
- Tracks global watcher counters and triggers responses at configurable thresholds.
- Enforces SOC tier-based access with hard GOD bypass.
- Supports onboarding invites and guest-password access in Admin Hub.
- Relays user DMs into staff-visible bridge channels and supports outbound relay.
- In AI chat mode, performs startup memory scan and adaptive live decisioning (ignore/react/reply).
- In AI chat mode, debounces burst prompts so rapid multi-message asks are merged into fewer smarter replies.
- In AI chat mode, Mandy can respond without wake-word and use image understanding internally; visual breakdown is only exposed when explicitly requested.
- In AI chat mode, memory is weighted: stable user facts/preferences are pinned, while low-signal chatter decays out.
- In AI chat mode, Mandy tracks relationship tone and preferred aliases to adapt replies more naturally per user.
- Before watcher, roast, and AI chat replies, Mandy shows a random typing delay (2-10s).
- If a Mandy response exceeds Discord text limits, it auto-continues in follow-up messages.
- Auto-trims debug/log and mirror channels on a schedule so control channels stay readable.
- Optional autonomy mode can plan and execute admin actions in the Admin Hub without approval.
- Autonomy can read cross-server context but is write-locked to the Admin Hub guild.
- Stores runtime state in MessagePack only (no JSON DB, no SQL).

## 2) v1 architecture

Main runtime:
- `run_bot.py`
- `src/mandy_v1/bot.py`

Core modules:
- `src/mandy_v1/storage.py` MessagePack state + atomic autosave
- `src/mandy_v1/services/soc_service.py` SOC tiers and permission checks
- `src/mandy_v1/services/admin_layout_service.py` Admin Hub layout/bootstrap
- `src/mandy_v1/services/mirror_service.py` satellite provisioning + mirror pipeline
- `src/mandy_v1/services/watcher_service.py` global watcher counters/triggers
- `src/mandy_v1/services/ai_service.py` AI chat/roast mode and Alibaba API test
- `src/mandy_v1/services/autonomy_service.py` autonomous admin planner/executor (write-locked to Admin Hub)
- `src/mandy_v1/services/onboarding_service.py` onboarding invites + bypass state
- `src/mandy_v1/services/dm_bridge_service.py` DM bridge open/relay
- `src/mandy_v1/services/logger_service.py` structured runtime log sink
- `src/mandy_v1/ui/mirror_actions.py` interactive mirror action buttons
- `src/mandy_v1/ui/satellite_debug.py` satellite debug dashboard/menu + permission requests

## 3) Requirements

- Python 3.11+ (tested in this repo with Python 3.13)
- Discord bot token
- Bot added to Admin Hub and satellite servers
- Discord intents enabled in Developer Portal:
  - `MESSAGE CONTENT INTENT`
  - `SERVER MEMBERS INTENT`

Install:

```bash
pip install -r requirements.txt
```

## 4) Configuration (`passwords.txt`)

Mandy v1 reads config from `passwords.txt` in repo root.

1. Copy `passwords.example.txt` to `passwords.txt`
2. Fill values

Required keys:
- `DISCORD_TOKEN`
- `ADMIN_GUILD_ID`

Optional keys:
- `GOD_USER_ID` (default `741470965359443970`)
- `COMMAND_PREFIX` (default `!`)
- `STORE_PATH` (default `data/mandy_v1.msgpack`)
- `ALIBABA_API_KEY` (for AI mode + roast mode API calls)
- `ALIBABA_BASE_URL` (default `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`)
- `ALIBABA_MODEL` (default `qwen-plus`)

AI key auto-probing:
- API test and AI calls probe these sources automatically in order:
  1) `ALIBABA_API_KEY` from settings
  2) environment vars (`ALIBABA_API_KEY`, `DASHSCOPE_API_KEY`, `QWEN_API_KEY`, `AI_API_KEY`)
  3) `passwords.txt` keys (`ALIBABA_API_KEY`, `DASHSCOPE_API_KEY`, `QWEN_API_KEY`, `AI_API_KEY`, `API_KEY`)
- Model is auto-selected from available candidates and cached after successful API test.

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

Security:
- `passwords.txt` is gitignored.
- Do not commit real secrets.

## 5) Run

```bash
python run_bot.py
```

On first successful `on_ready`, Mandy auto-runs Admin Hub layout ensure + satellite ensure.

## 6) Auto-setup behavior

### Admin Hub self-setup

Mandy ensures categories:
- `WELCOME`
- `OPERATIONS`
- `SATELLITES`
- `GUEST ACCESS`
- `ENGINEERING`
- `GOD CORE`

Mandy ensures channels (per blueprint), applies topics, and keeps signature-pinned panels updated.
Mandy also maintains a unified control panel message in `OPERATIONS/menu`.

Mandy ensures access roles:
- `ACCESS:Guest`
- `ACCESS:Member`
- `ACCESS:Engineer`
- `ACCESS:Admin`
- `ACCESS:SOC`

### Satellite self-setup

For each non-admin guild:
- Creates/ensures `SATELLITES / Active / <Server Name>` category in Admin Hub
- Creates `mirror-feed` and `debug` channels
- Creates/ensures role `SOC:SERVER:<guild_id>`
- Applies visibility permissions for mirror/debug channels
- Maintains a debug dashboard + button menu in each satellite `debug` channel

## 7) Commands

Prefix default is `!`.

Health/setup:
- `!health` (tier >= 50)
- `!setup` (tier >= 90, Admin Hub)
- `!menupanel` (tier >= 50, Admin Hub)
- `!housekeep` (tier >= 70, Admin Hub; run cleanup immediately)
- `!syncaccess` (tier >= 90, Admin Hub)

SOC:
- `!socset <user_id> <tier>` (tier >= 90)

Watchers:
- `!watchers` (tier >= 50)
- `!watchers add <user_id> <threshold> <response_text>` (tier >= 70)
- `!watchers remove <user_id>` (tier >= 70)
- `!watchers reset <user_id>` (tier >= 70)

Onboarding:
- `!onboarding` (tier >= 70; selector mode)
- `!onboarding <user_id>` (tier >= 70; direct invite)

Guest password flow:
- `!setguestpass <password>` (tier >= 90)
- `!guestpass <password>` (Admin Hub members use this to verify)

Satellite debug:
- `!debugpanel` (tier >= 50; in satellite guilds, refreshes dashboard/menu)
- `!autonomy` (tier >= 90; status snapshot)
- `!autonomy status|on|off`
- `!autonomy run <prompt>` (tier >= 90; force a single autonomy planning/execution pass)

Global menu:
- Admin Hub `menu` channel includes the full control panel and satellite entry menu

## 8) SOC tiers

Default numeric tiers:
- `1` guest
- `10` member
- `50` staff
- `70` admin
- `90` soc-admin
- `100` GOD bypass (hardcoded by `GOD_USER_ID`)

Tier evaluation:
- Hardcoded GOD user bypass always wins.
- Otherwise per-user configured tier is checked.

## 9) Mirror interactive actions

Buttons on mirrored posts:
- `Direct Reply`
- `DM User`
- `Add to Watch List`
- `Ignore`

Reaction propagation:
- Reactions added on mirrored messages are forwarded back to source messages.
- Mapping is in-memory in v1 (non-goal: persistent reaction mapping store).

## 10) MessagePack persistence

State file:
- Default `data/mandy_v1.msgpack`

Store behavior:
- Schema defaults auto-filled on load
- Atomic writes (`.tmp` -> replace)
- Autosave loop flushes dirty state periodically

## 11) Logging and diagnostics

Mandy records structured runtime events in store and stdout, including:
- setup events
- watcher events
- onboarding events
- DM bridge events
- AI mode events

Runtime events are also pushed into debug channels (excluding `mirror.*` events) and into Admin Hub `debug-log` (fallback `diagnostics`).
AI warmup events include startup scan summaries per satellite (`ai.warmup_*`).
Housekeeping trims old messages in debug/log and mirror channels while preserving pinned/menu/dashboard panels.

## 12) Discord permission notes

Bot should have, at minimum:
- Manage Channels
- Manage Roles
- View Channels
- Send Messages
- Read Message History
- Add Reactions
- Manage Messages (for pin management helps)
- Create Invite (for onboarding invite generation)

If permissions are missing, setup or relay operations will partially fail and log events.

## 13) Troubleshooting

`DISCORD_TOKEN is required in passwords.txt`:
- Confirm `passwords.txt` exists and key is set.

`ADMIN_GUILD_ID is required in passwords.txt`:
- Add valid admin guild ID.

No mirrors appearing:
- Confirm bot is in satellite guild.
- Confirm Admin Hub exists and bot has channel/role permissions.
- Check stdout logs for `mirror.ensure_failed` or `guild.join_setup_failed`.

Onboarding invite failures:
- Ensure bot can create invites in at least one Admin Hub text channel.

Guestpass not granting access:
- Ensure `!setguestpass` was set by SOC admin.
- Ensure command is run in Admin Hub.

## 14) v1 non-goals (intentional)

- No SQL dependency
- No MySQL mode
- No channel-scope mirror rules (server-scope default provisioning)
- No persistent reaction mapping storage
- No write access outside `ADMIN_GUILD_ID` for autonomy actions

## 15) Quick start checklist

1. Configure `passwords.txt`
2. Start bot with `python run_bot.py`
3. Verify Admin Hub categories/channels were created
4. Run `!setup` once in Admin Hub to confirm idempotent sync
5. Invite bot to a satellite server and verify mirror/debug folder appears
6. Test `!watchers add ...` and trigger behavior
7. Test `!onboarding` invite flow
8. Set guest password and test `!guestpass`
