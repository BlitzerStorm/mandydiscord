# MandyDiscord Bot Commands

Prefix: `!`

The bot supports both **classic commands** and **natural language** (via Mandy AI). Target resolution (users/channels) is **cross-server**: it can search across every guild the bot is in, and will ask to clarify if ambiguous.

## Core AI (Mandy Cog)
- `!mandy <prompt>`: Ask Mandy to answer and/or run allowed tools.
- `@Bot <prompt>`: Same as `!mandy` (GOD-only mention handler).
- `!mandy_model <flash-lite|flash|pro|model-name>`: Set default Gemini model.
- `!mandy_limits`: Show cooldown + rate-limit tracking.
- `!mandy_queue`: Show queued AI jobs.
- `!mandy_cancel <job_id>`: Cancel a queued AI job.
- `!dmai on|off|status|list <target>`: Toggle DM AI mode for a user/bridge (GOD-only, admin server).

### Natural-language examples
- `dm john hey` / `dm john, sarah hey` (multi-target)
- `open dm john` (opens DM with a default handshake message)
- `watch john after 5 say ping me`
- `stats daily` / `stats weekly for john`
- `mirror #general to #backup` (cross-server channel lookup if needed)

## Menus
- `!menu`: User menu panel.
- `!godmenu`: Admin panel (GOD).
  - Includes **Command Routing** (allow anywhere / soft remind / hard enforce+cleanup).
- `!servermenu`: Server-only admin panel (GOD). Includes Bot Interop (manual tests in a chosen channel).
  - Bot Interop supports searching across servers for a test channel, running a visible manual test message, saving per-bot results, and creating "macros" (message templates) you can run again.

## Setup / Server Ops (admin server only)
- `!setup bootstrap`: Create/update roles, categories, channels, topics, pins, and menus.
- `!setup destructive` / `!setup fullsync`: Full layout sync (SUPERUSER only); destructive now prompts for Default vs AI-assisted rebuild.
- `!setup_bio`: Bio-themed admin hub rebuild with recovery anchor (SUPERUSER only).

## Watchers / Mirrors / DM bridges
These are primarily managed through the menus, but key commands exist:
- `!addtarget <user_id> <count> <text>`: Add JSON watcher.
- `!mirroradd <source_channel_id> <target_channel_id>`: Add a channel mirror rule.
- `!mirroraddscope <server|category|channel> <source_id> <target_channel_id>`: Add scoped mirror rule.
- `!mirrorremove <source_channel_id>`: Remove mirror rule(s) for a channel.
- `!dmopen <user_id>`: Open DM bridge (admin server).
- `!dmclose <user_id>`: Close DM bridge.
- DM bridge channels include a pinned control panel to archive or toggle DM AI (AI toggles require GOD).
- `!setlogs <system|audit|debug|mirror|ai|voice> <channel_id>`: Route logs.
- `!health`: Health snapshot (staff/admin).
- `!leavevc`: Emergency voice disconnect (SUPERUSER).

## Stats panels
- `!mystats [window]`: Your stats for this server.
- `!allstats [window]`: Server leaderboard.
- `!livestats [window]`: Pin a live-updating server stats panel (staff+).
- `!globalstats [window]`: Global stats across all guilds.
- `!globallive [window]`: Pin a live-updating global stats panel (admin+).

## Cleanup
- `!clean [limit]`: Delete the bot’s own recent messages in the channel.
- `!nuke [limit]`: SUPERUSER-only heavy cleanup.

## Media
- `!movie`: GOD-only. Opens the control panel (DM or server). Use the panel or `!movie <url>` to play.
- `!movie add <url>`: Queue a YouTube link.
- `!movie queue`: Show now playing and the queue.
- `!movie stay [minutes]`: Keep the bot in VC after playback (default 15, max 30).
- `!movie leave`: Stop and disconnect.
- `!movie pause` / `!movie resume` / `!movie skip`
- `!movie volume <0-100>` or `!volume <0-100>`
