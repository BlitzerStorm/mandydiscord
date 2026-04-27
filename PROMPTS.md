# Mandy Prompts

Prompt constants live in `src/mandy_v1/prompts.py`. Runtime prompt assembly happens mostly in `AIService.build_contextual_system_prompt()`.

## Static Prompt Constants

- `BASE_PERSONA`
  Shared persona layer reused across interactive chat surfaces.

- `CHAT_SYSTEM_PROMPT`
  Public server replies. Used by chat reply generation.

- `DM_SYSTEM_PROMPT`
  Direct-message replies.

- `HIVE_COORDINATOR_SYSTEM_PROMPT`
  Cross-DM/shadow coordination. Must return strict JSON.

- `SHADOW_PLANNER_SYSTEM_PROMPT`
  Shadow planning. Must return strict JSON with allowed actions only.

- `ROAST_SYSTEM_PROMPT`
  Roast mode replies when roast mode is enabled and a negative Mandy mention triggers it.

- `HEALTHCHECK_SYSTEM_PROMPT`
  API connectivity testing.

## Context Blocks

Mandy's contextual system prompt can include:

1. Base persona.
2. Mood tag from `EmotionService`.
3. Identity and opinions.
4. Server culture.
5. User persona and relationship profile.
6. Reflection summary.
7. Fun mode.
8. Curiosity planner.
9. Agency policy and last agency choice.
10. Episodic memory.
11. Runtime coordinator context.
12. Agent core directive.
13. Permission intelligence.
14. Guild prompt override.
15. Global prompt override.
16. Extra instruction for the active mode.

Each block is clamped before assembly so prompts stay bounded.

## Prompt Overrides

Discord controls:

- `!setprompt <global|guild_id> <off|light|full> <prompt...>`
- `!showprompt [global|guild_id]`

Learning modes:

- `off` disables learning for that scope.
- `light` keeps lighter learning behavior.
- `full` allows normal profile, memory, and style updates.

Global and guild prompt overrides are treated as high-priority behavior instructions, but they do not override platform safety, Discord permission checks, autonomy approvals, or hard policy gates.

## Injection Resistance

The prompt path combines instructions with runtime policy. Dangerous Discord mutations still flow through:

- autonomy mode
- action allow-list
- approval requirement
- permission intelligence
- agent core
- Discord API permission checks
- audit logging

This means a prompt can influence style and intent, but it should not bypass execution policy.

## Prompt-Related Commands

- `!funmode this|<guild_id> show|balanced|chaotic|cozy|serious|roast|lore|helper`
- `!reflect [guild_id]`
- `!compactreflections [guild_id]`
- `!agency show|on|off|thresholds <ambient_min> <reply_min>`
- `!agentcore show|on|off|directive <text>`
- `!telemetry`
