# Mandy v1 (Discord Control Plane)

Lean v1 implementation aligned to `d:\docs\MANDY_MANUAL.md`.

## Features

- MessagePack-only persistence (`data/mandy_v1.msgpack`)
- Global watcher triggers (`!watchers ...`)
- Server-scope mirrors auto-created on guild join
- Mirror action buttons:
  - Direct Reply
  - DM User
  - Add to Watch List
  - Ignore
- Reaction propagation from mirror message to source message (in-memory map)
- SOC numeric tier checks with GOD bypass
- Onboarding invite command (`!onboarding`)
- DM bridges (open/relay only)
- Minimal health and structured logging

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Copy `passwords.example.txt` to `passwords.txt` and set:
- `DISCORD_TOKEN`
- `ADMIN_GUILD_ID`
- `GOD_USER_ID` (default already set to `741470965359443970`)

3. Run:

```bash
python run_bot.py
```

## Commands

- `!health`
- `!watchers`
- `!watchers add <user_id> <threshold> <response_text>`
- `!watchers remove <user_id>`
- `!watchers reset <user_id>`
- `!socset <user_id> <tier>`
- `!onboarding [user_id]`
- `!syncaccess`
- `!setguestpass <password>`
- `!guestpass <password>`

## SOC Tiers (default)

- `1` guest
- `10` member
- `50` staff
- `70` admin
- `90` soc-admin
- `100` GOD bypass (`741470965359443970`)
