# Mandy OS — Technical Manual (Code-In-English)

This document is a high-detail, implementation-faithful description of how Mandy OS behaves at runtime: what is built, what is synced, which data is stored where, and how each major subsystem reacts to events.

Constraints:
- No command catalog and no “how to run commands” section (this is not an operator cheat sheet).
- This is a behavioral spec of the code as it exists.

Terminology:
- **Admin Hub / SOC**: the single control-plane guild (`ADMIN_GUILD_ID`) where monitoring, access control, and satellite folders live.
- **Satellite**: any other guild the bot is in (non-admin-hub).
- **Store / cfg**: the live configuration/state object persisted to `database.json` via `STORE` and accessed through `cfg()`.
- **Layout**: the intended Admin Hub category/channel blueprint stored in `cfg().layout.categories`.

---

## 1) System Overview

Mandy OS is a Discord bot with these core responsibilities:
- Maintain an Admin Hub layout (categories, channels, topics, pinned “panels”), and keep it consistent over time.
- Maintain a per-satellite folder in the Admin Hub (category + `mirror-feed` + `server-info`) for every satellite guild.
- Mirror messages from satellites into the Admin Hub (server-scope and optional finer scopes).
- Maintain a gate/quarantine flow for new members in the Admin Hub.
- Maintain “SOC access” visibility (section roles + per-satellite visibility roles) based on membership and per-user config.
- Provide a DM bridge subsystem (creates per-user channels inside the Admin Hub for DM intake/relay).
- Provide optional AI tooling, diagnostics panels, and periodic maintenance loops.

This document focuses on the *automatic* behavior: boot, sync, background loops, and event handlers.

---

## 2) Data Model (State, Persistence, and Key Config Maps)

### 2.1 Primary persistence
- **`database.json`** is the primary persistence store.
- The store is loaded at boot and flushed periodically (see “Loops”).

### 2.2 Runtime scheduling primitives (timing + causality)

Mandy’s behavior is driven by three scheduling mechanisms:

1) **Discord events (reactive / immediate)**
- Example triggers: bot ready, message received, member joins, guild joins.
- These handlers run “now” on the event loop, and may either:
  - do work inline (awaiting each step), or
  - enqueue work via `spawn_task(...)` (fire-and-forget background work).

2) **Background loops (`tasks.loop`) (periodic / ongoing)**
- Each loop has a fixed tick interval (seconds or minutes).
- Some loops additionally self-throttle based on config (for example, SOC access sync ticks often but only *acts* every N minutes).
- If a loop iteration errors, the error is generally swallowed; the next tick still happens.

3) **Setup pacing (`setup_pause`) (rate-limit friendly)**
- Between create/edit operations, Mandy sleeps a small amount to reduce Discord API rate-limit pressure.
- On rate-limit-like errors, setup pacing can adapt (sleep longer) rather than crashing.

Separately, message sending can be delayed:
- A typing-delay patch can add “typing…” time to make UX feel intentional.
- The patch intentionally skips typing delay in operational contexts (Admin Hub channels, log channels, and “command-context” sends), so monitoring surfaces remain fast.

### 2.3 Config “roots” used throughout the code
The following keys in `cfg()` are treated as authoritative:

#### Layout / presentation
- `layout.categories`: dict of `CategoryName -> [channel_name, ...]`.
- `channel_topics`: dict of `channel_name -> topic_string`.
- `pinned_text`: dict of `channel_name -> pinned_message_text`.
- `menu_messages`: dict of `{guild_id: {user_menu: message_id, god_menu: message_id}}`.

#### Command routing (binding, not commands)
- `command_channels.user`: channel name used for general menu panel hosting.
- `command_channels.god`: channel name used for admin menu panel hosting.
- `command_channels.mode`: off/soft/hard behavior for command routing (behavior exists even if you don’t use it).

#### Logging routing
- `logs.system`, `logs.audit`, `logs.debug`, `logs.mirror`, `logs.ai`, `logs.voice`: channel IDs used as sinks for log streams.

#### Mirrors / relays
- `mirror_rules`: dict of mirror rule entries (server/category/channel scopes).
- `mirror_status`: per-satellite last mirror snapshot.
- `mirror_message_map`: mapping used to support reply/reaction bridging for mirrored messages (JSON fallback if MySQL is off).

#### Admin Hub satellites registry
- `admin_servers[guild_id]`: per-satellite admin folder IDs inside the Admin Hub:
  - `category_id` (the category representing the satellite)
  - `mirror_feed` (admin-hub channel id)
  - `server_info` (admin-hub channel id)

#### Gate / quarantine
- `gate_layout`: names for where to place gate channels and how to find quarantine:
  - `category` (category name for private gate channels)
  - `quarantine` (channel name)
  - plus some guest channel names (used by permission logic)
- `gate[user_id]`: per-user gate state:
  - `channel` (the private gate channel id)
  - `tries` (attempt counter)

#### SOC access (visibility + roles)
- `soc_access.sections`: high-level access “sections” and their role names.
- `soc_access.users[user_id]`: per-user SOC overrides including:
  - `sections` on/off overrides
  - `allowed_guilds` allowlist and `denied_guilds` denylist
- `soc_onboarding.tokens[user_id]`: gate tokens for Admin Hub entry.

#### DM bridges
- `dm_bridges[user_id]`: JSON fallback for bridge state (if MySQL off)
- `dm_bridge_controls[user_id]`: pinned controls message tracking
- `dm_ai[user_id]`: per-user “DM AI enabled” metadata

#### Owner onboarding (self-service)
- `owner_onboarding.pending[user_id]`: pending request to link a user to a new satellite after bot invite.
- `owner_onboarding.history[user_id]`: resolved onboarding history.
- `satellite_features[guild_id]`: feature flags selected for a satellite.

#### Optional MySQL mode
- If MySQL is configured and bootstrapped, some tables mirror/extend state (audit logs, watchers, DM bridges, mirror maps).
- If MySQL init fails, the bot falls back to JSON-only behavior without aborting.

---

## 3) SOC v2 Minimal Layout (Admin Hub Blueprint)

This section describes the blueprint the bot uses when building/verifying the Admin Hub layout (unless an AI layout override is enabled).

### 3.1 Categories and channels (top-level)

**WELCOME**
- `rules`
- `announcements`
- `guest-briefing`
- `manual-for-living`

**OPERATIONS**
- `console`
- `requests`
- `reports`

**SATELLITES**
- (no static channels; satellite folders are created dynamically in the Admin Hub as separate categories named under a SATELLITES path)

**GUEST ACCESS**
- `guest-chat`
- `guest-feedback`
- `quarantine`

**ENGINEERING**
- `system-log`
- `audit-log`
- `debug-log`
- `mirror-log`
- `data-lab`
- `dm-bridges` (a “home” channel; DM bridge channels also live under this category)

**GOD CORE**
- `admin-chat`
- `server-management`
- `layout-control`
- `blueprint-export`
- `incident-room`

Important behavior:
- Layout is “ensured”, not blindly recreated. Existing channels/categories are moved/renamed to match the blueprint.
- The bot preserves functionality by mapping old names to new names during `ensure_text_channel()` and old categories to new categories in `ensure_category()`.

### 3.2 Topics
Topics are applied to channels during layout enforcement:
- Topics come from `cfg().channel_topics[channel_name]`.
- If a channel exists with a different topic, it is edited to match.

### 3.3 Pinned panels (pinned_text)
Pinned content is:
- Posted by the bot if missing.
- Updated in-place if a matching pin exists but content differs.

Pin identification uses a hidden signature:
- First line of pinned text is: `<!--PIN:<channel_name>-->`
- The bot searches pinned messages for that exact signature (first line equality) to decide whether to update vs create a new pin.

---

## 4) Boot Sequence (“Who does what when”)

### 4.1 on_ready entrypoint
On ready:
- The store is loaded from disk.
- A typing-delay patch may be installed (affects message sending UX).
- Optional extensions are loaded (AI cog, plugin manager).
- The boot orchestrator is invoked.

### 4.2 Boot orchestrator phases
The orchestrator is a linear set of phases that produce a “setup status panel” snapshot.

Phases include (conceptually):
1) **Plugins**: load tool plugins if configured.
2) **Database init**: attempt MySQL init and bootstrap; fall back to JSON-only if not available.
3) **Mirror sync**: migrate legacy mirror state formats and build mirror indices.
4) **Register controls**: register persistent UI controls (mirror controls, etc.).
5) **Apply status**: apply presence/bot status from config.
6) **Ambient engine**: start ambient presence/typing behavior (if enabled).
7) **Admin roles**: ensure baseline roles exist and gate permissions are correct in the Admin Hub.
8) **Admin channels sync** (only when auto-setup is disabled): for each satellite, ensure admin folder + mirror rule + server-info panel.
9) **Auto setup** (if enabled): queue auto-setup tasks (admin hub + satellites).
10) **Backfill** (if enabled): queue chat stats backfill.
11) **Start loops**: start all maintenance loops (see below) and do an initial SOC access sync.

The “setup status panel” is posted in the diagnostics channel and updated every phase.

### 4.3 Boot timing and concurrency (cause → effect)

The boot path is intentionally split into:
- **blocking** steps that must complete before the bot is considered stable, and
- **queued** steps that are allowed to run concurrently after the orchestrator has progressed.

Cause → effect rules:
- If database initialization fails, Mandy **does not abort**; she flips into JSON-only mode and continues.
- If auto-setup is enabled, “Admin channels sync” is skipped during boot and a full auto-setup sweep is **queued** instead.
- If a step is queued via `spawn_task(...)`, it may still be running while the bot is already responding to events.

---

## 5) Build / Sync: What gets created and what gets “bound”

There are three related but distinct concerns:
- **Build layout**: categories/channels/topics/pins in the Admin Hub.
- **Bind channels**: choose which channels act as log sinks and which host menus.
- **Sync satellites**: ensure each satellite has an Admin Hub folder and a mirror rule into it.

### 5.1 Ensuring the Admin Hub layout
The Admin Hub layout builder:
- Uses `_resolve_setup_layout()` to decide which layout to apply:
  - If AI layout mode is enabled, it uses the AI layout.
  - Else, it uses `ensure_layout_defaults()` which enforces SOC v2 Minimal Layout.
- For each category:
  - `ensure_category()` ensures the category exists; if a legacy category exists, it is renamed in place.
- For each channel in that category:
  - `ensure_text_channel()` ensures the channel exists in the correct category and updates topic.
  - If a legacy channel exists (old name), it is renamed/moved in place.

#### 5.1.1 In-place migration timing (old → new)

When a legacy channel/category is detected, Mandy prefers “rename/move” over “create new”.

Cause → effect:
- If a legacy channel exists:
  - The first matching legacy name is renamed to the new name and moved into the new category.
  - The channel’s topic is then applied/updated.
- If multiple legacy channels map into one new channel (for example multiple sources mapped to `data-lab`):
  - Only the first match is renamed into `data-lab`.
  - The other legacy channels remain until a human consolidates them (this avoids destructive data loss).

### 5.2 Ensuring pinned text
For each entry in `cfg().pinned_text`:
- The corresponding channel is looked up by name.
- `ensure_pinned()` ensures a pinned message exists for that signature and matches content.

#### 5.2.1 Pin update behavior (cause → effect)

Pinned panels are signature-addressed:
- The first line of each pinned panel is `<!--PIN:<channel_name>-->`.
- Mandy searches pinned messages for an exact signature match on the first line.

Cause → effect:
- If a pin exists with the same signature and different content:
  - Mandy edits that pinned message in place.
- If no pin exists with that signature:
  - Mandy posts a new message and pins it.
- Mandy does not unpin or delete “unknown” pins that do not match a signature.

### 5.3 Menu panel binding (button panels)
Menus are “pinned panel messages” posted into:
- `cfg().command_channels.user` (default `requests`)
- `cfg().command_channels.god` (default `admin-chat`)

The bot:
- Posts or edits a panel message with a persistent button view.
- Stores message IDs in `cfg().menu_messages` to find and update them later.

### 5.4 Log routing binding
Log routing binds `cfg().logs.*` to channel IDs.

Default name lookup behavior binds:
- system → `system-log`
- audit → `audit-log`
- debug → `debug-log`
- mirror → `mirror-log`

If an AI layout explicitly defines log channel names, those names override the defaults and are used to resolve IDs.

Once bound, the logging system:
- Formats log lines in a consistent UTC format.
- Deduplicates within a time window to avoid spam bursts.
- Falls back to system/debug channels if a specific sink is missing.

#### 5.4.1 Log timing + dedup (cause → effect)

Cause → effect:
- If many identical log events fire within ~60 seconds:
  - Only the first is emitted; others are deduplicated.
- If a target log channel cannot be resolved:
  - The log line is printed to stdout as a fallback (not lost).

---

## 6) Satellites: Admin Hub folders, mirror rules, and server-info panels

### 6.1 Satellite Admin Hub folder naming
For each satellite guild, the Admin Hub creates/maintains a dedicated category:
- Category name format: `SATELLITES / Active / <Server Name>`
- This category is the “folder” containing the satellite’s feed/info channels.

Legacy migration:
- If an older category exists (historically `"<Server Name> Admin"`), it is renamed into the SATELLITES path.

### 6.2 The two standard channels inside each satellite folder
Within each satellite folder category, the bot ensures:
- `mirror-feed` (text channel): destination for mirrored content.
- `server-info` (text channel): destination for a maintained server status panel.

The Admin Hub stores these IDs under `cfg().admin_servers[guild_id]`.

### 6.3 Mirror rule creation and enforcement
For each satellite guild:
- A server-scope mirror rule is ensured:
  - scope: `server`
  - source_guild/source_id: the satellite guild id
  - target_channel: the Admin Hub `mirror-feed` channel id

If an existing server-scope rule points at a missing or incorrect target, the bot:
- Disables the old rule and creates a new one bound to the correct `mirror-feed`.
- Keeps the mirror rule registry consistent and rebuilds mirror indices as needed.

### 6.4 Server-info embed maintenance
The bot periodically updates a server-info embed per satellite:
- Includes server ID, owner ID, member count.
- Reports whether the server mirror rule is enabled.
- Reports bot permission posture (admin perms yes/no and missing permission summary).
- If possible, includes a permanent invite link (created only if the bot is permitted to create invites).

Message IDs are tracked in `cfg().server_info_messages` so the bot edits the existing message instead of posting duplicates.

---

## 7) Mirror Pipeline (How messages become mirrored feed entries)

### 7.1 Rule selection (fast path)
For each message in a guild:
- The bot uses a cached index of mirror rules keyed by:
  - channel id (channel-scope)
  - category id (category-scope)
  - guild id (server-scope)

This avoids scanning every rule for every message.

### 7.2 Eligibility checks
For each candidate rule:
- It must be enabled.
- The message must match the rule’s scope constraints.
- The target channel must be resolvable and writable.

### 7.3 Delivery and mapping
When a mirror is delivered:
- The bot posts to the Admin Hub target channel.
- It records a mapping between source and mirrored messages (for reply/reaction bridging and traceability).
  - If MySQL is enabled, it persists mapping there.
  - Otherwise, it persists in JSON.
- It updates `cfg().mirror_status[guild_id]` with last-mirror metadata.
- It emits mirror log lines to the mirror log sink.

### 7.4 Failure handling
If sending fails:
- A failure counter is incremented.
- After a threshold, rules can be disabled.
- A periodic integrity loop can purge stale/disabled rules after TTL.

#### 7.5 Mirror timing (cause → effect)

Cause → effect:
- When a message arrives:
  - mirror rule candidates are selected immediately via the cached index.
  - delivery occurs inline for each eligible target.
- When repeated delivery failures happen:
  - failures accumulate on the rule.
  - the rule may become disabled (and later auto-deleted after TTL) depending on the failure reason and age.
- Integrity loop cadence:
  - mirror integrity runs every `INTEGRITY_REFRESH` seconds (default 60s).
  - it inspects only one rule per tick (cursor-based), smoothing load across time.

---

## 8) SOC Access Model (Visibility, Sections, and Per-Satellite Roles)

The Admin Hub is intended to be “quiet by default” for humans:
- General users see WELCOME/OPERATIONS/GUEST ACCESS, plus only the satellite feeds they are entitled to.
- ENGINEERING is hidden behind higher-level access.
- GOD CORE is hidden behind administrator-level access.

### 8.1 Internal SOC section roles (existing model)
SOC section roles are the internal control-plane gating system:
- Examples: docs / guest area / guest write / mirrors / server info.
- These roles are created and maintained by SOC permission application logic.

The SOC sync uses:
- Per-user section overrides (`cfg().soc_access.users[user_id].sections`) to enable/disable section roles.
- Global defaults (`cfg().soc_access.sections`) for baseline behavior.

### 8.2 Per-satellite visibility roles (existing model)
For each satellite guild ID, the Admin Hub creates:
- `SOC:MIRROR:<guild_id>`: grants view access to that satellite’s `mirror-feed`.
- `SOC:INFO:<guild_id>`: grants view access to that satellite’s `server-info`.

The Admin Hub category/channels are permissioned so that:
- `@everyone` cannot view.
- The corresponding SOC role can view/read.

Then `soc_sync_member_access()` assigns those roles dynamically when:
- The user is a member of the satellite guild (membership check),
- and the user has the relevant section enabled,
- and the user’s allow/deny filters permit that guild.

### 8.3 Human-facing access roles (additional layer)
The Admin Hub also creates these roles:
- `ACCESS:Viewer`: treated as a baseline “human viewer” role for WELCOME/GUEST ACCESS visibility alignment.
- `ACCESS:Engineer`: grants visibility to the ENGINEERING category.
- `ACCESS:Admin`: grants visibility to the GOD CORE category.

These roles do not replace SOC section roles; they complement them by applying category-level hides.

### 8.4 Category-level visibility
The bot applies category overwrite posture:
- ENGINEERING:
  - hidden from `@everyone`
  - visible to `ACCESS:Engineer`, `ACCESS:Admin`, and privileged roles
- GOD CORE:
  - hidden from `@everyone`
  - visible to `ACCESS:Admin` and privileged roles

This is implemented by adjusting category permission tables while preserving the internal SOC section role logic.

---

## 9) Gate System (Admin Hub onboarding)

The gate system runs only in the Admin Hub.

### 9.1 Trigger
When a non-bot member joins the Admin Hub:
- The bot starts a gate flow (creates a private channel and records it in `cfg().gate`).

### 9.2 Private gate channel
The bot creates `gate-<username>` in the gate category (`cfg().gate_layout.category`):
- `@everyone` cannot view.
- The member can view/send/read.
- Bot can view/send/manage channels.
- Privileged roles can view/send/read for support.

### 9.3 Acceptance modes
The gate accepts either:
- A one-time onboarding token from `cfg().soc_onboarding.tokens[user_id]` (preferred flow), or
- A static fallback password loaded from secrets (if configured).

### 9.4 Outcomes
Success:
- Gate state is cleared.
- Quarantine role is removed if present.
- Baseline Guest role is ensured.
- Private gate channel is deleted.
- SOC access sync runs to apply section/per-satellite roles.

Failure:
- Attempt is counted and the attempt message is deleted.
- After enough failures, the user is quarantined:
  - Quarantine role applied.
  - Guest role removed.
  - Gate channel deleted.
  - A notification is posted into the quarantine channel.

#### 9.5 Gate timing (cause → effect)

Cause → effect:
- When a user joins the Admin Hub:
  - a private gate channel is created immediately.
  - the user is prompted there.
- When a user posts in that gate channel:
  - the attempt message is deleted immediately (to avoid password/token leakage).
  - the token/password is evaluated.
  - on pass: the gate channel is deleted and roles are adjusted.
  - on fail: tries increment; at 3 tries the user is quarantined and the channel is deleted.

---

## 10) DM Bridges (Admin Hub DM intake and relay)

DM bridges are “per-user channels” inside the Admin Hub that allow staff to see/relay DMs.

### 10.1 Bridge creation and location
When a DM bridge is opened for a user:
- The bot ensures a channel named `dm-<user_id>` exists under the ENGINEERING category.
- It posts an “opened” header and attempts to dump recent DM history into the bridge channel.
- It posts/updates a pinned “DM Bridge Controls” message that tracks:
  - which user it is bound to
  - whether DM AI is enabled for that user
  - metadata timestamps and who enabled it

### 10.2 Active vs archived
When a bridge is closed:
- The channel is renamed to `archived-dm-<user_id>` and remains under ENGINEERING.
- The bridge state is marked inactive in persistence.

### 10.3 Relay behavior
Inbound:
- If a user sends a DM to Mandy and a bridge exists, the bot forwards that DM content into the bridge channel and updates “last activity”.

Outbound:
- If staff sends a message in an active bridge channel, the bot relays it back to the user’s DM.

### 10.4 Auto-archive
A periodic loop archives inactive bridges beyond a time cutoff to reduce clutter.

#### 10.5 DM bridge timing (cause → effect)

Cause → effect:
- When a user DMs Mandy:
  - if a bridge is already active: the message is forwarded to the bridge channel immediately.
  - if a bridge is not active and auto-open is triggered by that path:
    - the bridge channel is created/ensured, state is saved, and a short DM history is optionally synced.
- Auto-archive cadence:
  - the archive loop runs every 12 minutes.
  - bridges with last activity older than the cutoff are archived.

---

## 11) Diagnostics and Status Panels

### 11.1 Diagnostics channel binding
The bot resolves a diagnostics channel by:
- Using configured channel ID if present,
- Else finding a channel by name in the Admin Hub,
- Then storing the resolved ID in config for future boots.

### 11.2 Setup status panel
During boot and long setup operations:
- A status panel message is updated with phase-by-phase outcomes and timestamps.

---

## 12) Manual Auto-Upload

The bot treats `docs/MANDY_MANUAL.md` as a canonical operator manual artifact.

When enabled:
- It hashes the file and posts it to `manual-for-living` if the hash changed.
- It stores last hash, last message id, and last upload time in `cfg().manual`.

This is a “publication” subsystem: it keeps humans in the Admin Hub in sync with doc updates.

---

## 13) Owner Onboarding (Self-service satellite linking)

Owner onboarding exists to link:
- a user (inviter/owner) → the satellite guild they invited the bot into,
and to:
- persist feature selections for that satellite,
- grant “satellite access” roles in the Admin Hub once the guild is known.

High-level flow:
1) A user completes a self-service onboarding UI.
2) The bot records a pending onboarding record keyed by user id (with feature selections and invite link source).
3) When the bot joins a new satellite guild, it attempts to match that join to the pending onboarding record:
   - Prefer audit-log detection of who added the bot (if bot can view audit logs).
   - Fallback to membership/perms heuristics (member with manage/admin capability).
4) If matched:
   - The satellite’s feature flags are persisted under `cfg().satellite_features[guild_id]`.
   - The user’s `allowed_guilds` is updated to include that satellite guild.
   - SOC access sync is invoked to grant per-satellite visibility roles in the Admin Hub.

This flow does not remove or change mirror rules: it extends the access layer so the inviter can see their satellite feeds.

---

## 14) Background Loops (Ongoing “when/where” maintenance)

The bot runs periodic loops to keep the system in homeostasis.

### 14.1 Loop cadence (default timing)

Fast loops (seconds):
- Config reload: every 5s (checks disk mtime and reloads if changed).
- JSON autosave: every 10s (flushes dirty store).
- Mirror integrity: every `INTEGRITY_REFRESH` seconds (default 60s).
- Server-info refresh: every `SERVER_STATUS_REFRESH` seconds (default 60s).
- Presence controller: every 60s (only acts if autopresence enabled and state should change).

Medium loops (minutes):
- Diagnostics snapshot: every 10 minutes.
- DM bridge archive: every 12 minutes.
- Daily reflection: every 20 minutes tick (only posts if “due”).
- Internal monologue: every 20 minutes tick (only posts if “due”).
- Sentience maintenance: every 30 minutes (AI queue pruning).
- Manual upload: every 30 minutes (posts manual file if hash changed).

SOC access loop (hybrid):
- Tick: every 1 minute.
- Action throttle: it only performs a full SOC sync if at least `soc_access.sync_interval_minutes` have elapsed (default 30 minutes).
- Startup delay: before its first run, it sleeps `soc_access.initial_delay_seconds` (default 60 seconds).

### 14.2 Loops as “eventual consistency”

Many systems are designed to reach correctness over time:
- If a server-info embed update fails once, it will be retried on the next refresh tick.
- If a mirror rule target disappears, integrity checks will disable/purge it in the background.
- If SOC roles drift (manual edits), the next SOC access sync will reconcile them.

Each loop is designed to:
- Continue even if one iteration fails.
- Prefer skipping rather than crashing the process.

---

## 15) Failure Posture (What happens when permissions are missing)

Across all builders:
- Channel/category creation and permission edits are attempted and then rate-limited/slept on failures.
- When Mandy lacks permissions for a critical action:
  - She records debug/audit evidence.
  - In some cases she “requests elevation” (a signal to the operator/superuser that permissions need fixing).

The system is intentionally “best-effort”: it tries to keep running and keep syncing partial systems rather than exiting.

---

## 16) Notes on Legacy Name Migration (Old → New)

When building/verifying the Admin Hub layout, the bot migrates legacy names:

Channels:
- `rules-and-guidelines` → `rules`
- `bot-status` → `console`
- `command-requests` → `requests`
- `error-reporting` → `reports`
- `system-logs` → `system-log`
- `audit-logs` → `audit-log`
- `debug-logs` → `debug-log`
- `mirror-logs` → `mirror-log`
- `core-chat`, `algorithm-discussion`, `data-analysis` → `data-lab` (first one found is renamed; others remain unless manually handled)

Categories:
- `Welcome & Information` → `WELCOME`
- `Bot Control & Monitoring` → `OPERATIONS`
- `Guest Access` → `GUEST ACCESS`
- `Engineering Core` / `Research & Development` / `DM Bridges` → `ENGINEERING`
- `Admin Backrooms` → `GOD CORE`

Satellite folders (Admin Hub categories):
- `<Server Name> Admin` → `SATELLITES / Active / <Server Name>`

This migration is “in-place rename/move” oriented to preserve permissions, pins, and message history wherever possible.

---

## 17) Timing Playbooks (Step-by-step cause → effect sequences)

This section is intentionally procedural: “if X happens, then Y happens”.

### 17.1 Bot starts up (typical case; auto-setup disabled)
1) Store loads from `database.json`.
2) Boot orchestrator runs:
   - MySQL init attempted; failure flips into JSON-only.
   - Mirror rules are synced and indexed.
   - Baseline Admin Hub roles/permissions are ensured.
3) For each satellite:
   - Admin Hub satellite folder ensured (`SATELLITES / Active / <name>`).
   - A server-scope mirror rule into that folder’s `mirror-feed` is ensured.
   - The `server-info` embed is updated/created.
4) Maintenance loops start:
   - within ~60 seconds by default, the first SOC access sync is performed (initial delay).
   - thereafter, each subsystem converges on correctness at its cadence.

### 17.2 Bot joins a new satellite guild
1) The join event fires.
2) The bot ensures:
   - Admin Hub folder category for that satellite.
   - `mirror-feed` and `server-info` channels inside that folder.
   - server-scope mirror rule bound to the new `mirror-feed`.
3) A server-info embed update is performed.
4) Owner onboarding matcher runs:
   - tries to identify the inviter via audit logs (if permitted),
   - falls back to membership/perms heuristics,
   - then updates SOC visibility for the matched user.

### 17.3 A user sends a message in a satellite channel
1) Message event arrives.
2) Mirror candidates are looked up (channel/category/server scope).
3) For each eligible target:
   - content is relayed into the Admin Hub feed channel.
   - the source↔destination mapping is stored for reply/reaction tracking.
   - mirror status for that satellite is updated.
4) If delivery fails:
   - the rule’s failure state is updated.
   - integrity loop later disables/purges as appropriate.

### 17.4 A user joins the Admin Hub
1) Member join fires in Admin Hub.
2) Gate starts:
   - private `gate-<name>` channel created under `cfg().gate_layout.category`.
   - state recorded in `cfg().gate[user_id]`.
   - baseline Guest role and SOC permissions are applied.
3) User submits attempts:
   - each attempt is deleted immediately.
   - on pass, gate is removed and SOC roles are synced.
   - on 3 fails, quarantine posture is applied and gate channel is removed.
