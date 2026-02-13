# Mandy Prompts

Single source of truth for prompt text lives in `src/mandy_v1/prompts.py`.

## What Each Prompt Is For

- `BASE_PERSONA`
  Shared persona layer reused across interactive chat surfaces.

- `CHAT_SYSTEM_PROMPT`
  Used for public server replies (chat mode). Called by `AiService.generate_chat_reply()`.

- `DM_SYSTEM_PROMPT`
  Used for direct messages. Called by `AiService.generate_dm_reply()`.

- `HIVE_COORDINATOR_SYSTEM_PROMPT`
  Used for periodic coordination notes across DM + shadow activity. Must return strict JSON.
  Called by `AiService.generate_hive_note()`.

- `SHADOW_PLANNER_SYSTEM_PROMPT`
  Used for periodic shadow planning. Must return strict JSON with allowed actions only.
  Called by `AiService.generate_shadow_plan()`.

- `ROAST_SYSTEM_PROMPT`
  Used when roast mode is enabled and a negative mention triggers a roast reply.
  Called by `AiService.generate_roast_reply()`.

- `HEALTHCHECK_SYSTEM_PROMPT`
  Used only for API connectivity testing. Called by `AiService.test_api()`.

