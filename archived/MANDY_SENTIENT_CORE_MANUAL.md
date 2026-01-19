# Mandy Sentient Core User Manual (Single File)

This manual documents the unified Mandy Sentient Core system, including the new
biological theme and the full legacy feature set (menus, setup, mirrors, stats,
watchers, DM bridges, media, ark/phoenix, and AI). It is designed to be easy to
read and is auto-published inside the Admin Server.

## 1) Overview and Mental Model

Mandy Sentient Core treats the bot like a living organism.
- The Admin Server is the brain (cortex).
- Remote servers are sensory inputs (visual feed).
- Cogs represent systems (synaptic, immune, sensory).
- Legacy commands still work, but are now integrated into the new system.

The system separates data (theme, names, lore) from logic (cogs and services).
Channel names, role names, and lore lines live in mandy_core/themes/sentient_theme.py.

## 2) Quick Start

1. Install dependencies:
   - pip install -r requirements.txt

2. Create passwords.txt from passwords.example.txt:
   - Copy passwords.example.txt to passwords.txt
   - Fill in DISCORD_TOKEN and ADMIN_GUILD_ID
   - Optional: SERVER_PASSWORD and GEMINI_API_KEY

3. Run the bot:
   - python main.py

The root main.py delegates to the Sentient Core runner.

## 3) Configuration and Secrets

Configuration is loaded by mandy_core/config.py from passwords.txt or environment.

Keys:
- DISCORD_TOKEN (required)
- ADMIN_GUILD_ID (required for backroom integration)
- COMMAND_PREFIX (optional, default !)
- SERVER_PASSWORD (optional, gate requirement)
- GEMINI_API_KEY (optional, enables Mandy AI routing)

Optional environment overrides:
- SUPER_USER_ID (legacy SUPERUSER id)
- AUTO_GOD_ID (legacy auto-GOD id)
- MANDY_LEGACY_OPS (0 disables legacy ops layer)
- MANDY_LEGACY_AUTO_SETUP (1 re-enables legacy auto setup)

If DISCORD_TOKEN is missing, the bot exits.

## 4) Discord Intents and Permissions

The runner enables:
- Members
- Message Content
- Voice States

Required permissions in the Admin Server:
- Manage Channels (to create categories and channels)
- Manage Roles (to assign Citizen/Quarantine)
- Read/Send Messages

On remote servers, only Read/Send permissions are required for mirroring.

## 5) File Layout

The new architecture is modular. Key files:

- main.py
  Wrapper that calls mandy_core/main.py

- mandy_core/main.py
  Lightweight entry point that configures the bot and loads extensions

- mandy_core/config.py
  Loads secrets and settings

- mandy_core/themes/sentient_theme.py
  DNA for the system: category names, channel names, lore lines, roles

- mandy_core/utils/bio_ui.py
  MandyBioUI: standardized embed styles

- mandy_core/utils/manual_publisher.py
  Auto-publishes this manual to the Admin Server channel

- mandy_core/utils/tools.py
  Minimal tool registry (used when legacy ops is disabled)

- mandy_core/cogs/synaptic.py
  Creates and integrates cortex layout in Admin Server
  Creates feed channels for remote servers
  Publishes the manual to manual-for-living

- mandy_core/cogs/immune.py
  Gate and quarantine flow for new members in Admin Server

- mandy_core/cogs/sensory.py
  Mirrors remote messages into the Admin Server visual feed
  Includes a stub auditory_hallucination task

Compatibility shims:
- cogs/synaptic.py, cogs/immune.py, cogs/sensory.py
  Thin wrappers so extensions load as cogs.*

Legacy feature layer:
- mandy_core/legacy_ops.py
  Wraps the legacy monolith and installs its commands/listeners into the bot

Existing AI cog:
- cogs/mandy_ai.py
  Core AI router and tool execution

Other supporting modules:
- capability_registry.py
- resolver.py
- intelligence_layer.py
- intelligent_command_processor.py
- clarify_ui.py

## 6) Theme and DNA

The cortex layout is defined in mandy_core/themes/sentient_theme.py.

Categories:
- CEREBRAL CORTEX (logs and thought streams)
- BIO-FILTER (security and gate)
- VISUAL CORTEX (mirrored feeds)

Channels:
- thoughts (internal monologue stream)
- audit-memory (record of actions taken)
- synaptic-gap (direct interface terminal)
- scan-gate (bio-signature scanning)
- containment-ward (pathogen isolation)
- manual-for-living (operator manual channel)

Lore lines:
- startup: Neural pathways integrated. Cortex online.
- ban: Pathogen eliminated. System homeostasis restoring.
- unknown_user: Foreign Bio-Signature Detected.

Roles:
- Citizen
- Quarantine

Channel names are not hardcoded in cogs. They are imported from the theme file.

## 7) Cortex Integration (Synaptic)

File: mandy_core/cogs/synaptic.py

Behavior:
- On ready, integrates the Admin Server if available.
- On guild join:
  - If Admin Server, builds cortex layout.
  - If remote server, creates a feed channel in the Admin Server visual cortex.

The feed channel name is derived from the remote guild name:
- feed-<normalized-guild-name>

The feed mapping is stored in memory on bot.mandy_feeds.

## 8) Immune System (Gate and Quarantine)

File: mandy_core/cogs/immune.py

Flow for Admin Server members:
1. Create a private scan channel: scan-<username>
2. Send a bio-signature alert embed
3. If SERVER_PASSWORD is correct:
   - Assign Citizen role
   - Log "Entity integrated."
4. If 3 failed attempts:
   - Assign Quarantine role
   - Log "Threat isolated."
   - Notify containment-ward

Notes:
- Gate state is in memory only.
- If SERVER_PASSWORD is empty, all users pass immediately.

## 9) Sensory System (Mirrors and Ghosting)

File: mandy_core/cogs/sensory.py

Mirroring:
- Messages from remote servers are mirrored into the Admin Server feed channel
- Embed footer: "Visual input received from Sector <Guild Name>."

Auditory:
- auditory_hallucination is a placeholder task
- It does nothing yet, but is ready for future VC heartbeat behavior

## 10) Core Setup Command

- `!setup_core bootstrap`: build/update the Sentient Core cortex layout in the Admin Server.
- `!setup_core destructive`: remove managed cortex categories/channels and rebuild with the new biological layout.
- Admin-server only; requires administrator permissions.

## 10) Legacy Features (Menus, Setup, Mirrors, Stats, Watchers)

Legacy features are preserved by mandy_core/legacy_ops.py and still work:
- !setup bootstrap / !setup fullsync / !setup destructive
- !menu / !godmenu
- watchers and mirrors
- DM bridges
- stats (local and global)
- ark/phoenix
- media/movie controls
- ambient engine
- audits and logs

Legacy auto-setup is disabled by default to avoid double layouts.
To re-enable it, set MANDY_LEGACY_AUTO_SETUP=1.

## 11) Mandy AI

File: cogs/mandy_ai.py

This cog provides the AI router and tool execution.
It expects certain bot attributes to exist, which are provided by
mandy_core/legacy_ops.py or mandy_core/utils/tools.py.

Default commands:
- !mandy <query>
- !mandy_model <model>
- !mandy_queue
- !mandy_cancel <job_id>
- !mandy_limits

Natural language behavior:
- The router decides between TALK, ACTION, DESIGN_TOOL, BUILD_TOOL
- It enforces tool schemas from capability_registry.py

## 12) Command Reference (Legacy + Core)

Common commands (legacy layer):
- !setup bootstrap
- !setup fullsync
- !menu
- !godmenu
- !watchers
- !addtarget <user_id> <count> <text>
- !mirroradd <source_channel_id> <target_channel_id>
- !mirrorremove <source_channel_id>
- !dmopen <user_id>
- !dmclose <user_id>
- !movie
- !movie add <url>
- !movie queue
- !movie pause / !movie resume / !movie skip
- !clean / !nuke

AI commands:
- !mandy <query>
- !mandy_model <flash-lite|flash|pro|model-name>
- !mandy_queue
- !mandy_cancel <job_id>
- !mandy_limits

For a fuller command list, see docs/MANDY_MANUAL.md.

## 13) Extensions

Extensions live in the extensions/ folder.
They must export TOOL_EXPORTS and define setup(bot).

Validator:
- extensions/validator.py enforces basic safety rules.
- validator_unrestricted.py exists but should only be used if you trust the code.

## 14) Data and Persistence

Legacy persistence uses database.json and optional MySQL.
The new system preserves and reuses that data structure.

Key data:
- targets (watchers)
- mirror_rules (mirrors)
- dm_bridges
- permissions / rbac
- ai config and queue

## 15) Manual Auto-Publish

This file is auto-published to the Admin Server channel:
- manual-for-living

On boot, the bot computes a SHA-256 hash of this file.
If the hash changes, it deletes old manual messages and re-posts.

## 16) Troubleshooting

Missing DISCORD_TOKEN:
- Ensure passwords.txt exists and includes DISCORD_TOKEN

Admin Server not integrating:
- Ensure ADMIN_GUILD_ID is set
- Ensure the bot has Manage Channels and Manage Roles

Mirrors not showing:
- Ensure bot can send messages in the Admin Server feed channel
- Ensure the remote server allows message content intent

AI commands failing:
- Ensure GEMINI_API_KEY is set
- Ensure bot.mandy_tools and bot.mandy_registry exist

## 17) Customization

Theme:
- Update mandy_core/themes/sentient_theme.py to change names and lore

Roles:
- Change Citizen or Quarantine names in the theme file

Cogs:
- Add new cogs in mandy_core/cogs and load them in mandy_core/main.py

Permissions:
- Update effective_level in legacy ops or tools layer for custom RBAC

## 18) Security Notes

- Do not commit passwords.txt
- Keep SERVER_PASSWORD private
- Avoid loading untrusted extensions
- Review validator.py rules before enabling auto tool builds

## 19) Development Notes

Logging:
- Cogs use logging with log_event decorators for key actions
- Adjust logging in mandy_core/main.py

Tests:
- python -m unittest discover -s tests

Runtime:
- Use python main.py from the repo root

End of manual.
