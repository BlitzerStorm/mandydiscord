# Mandy Autonomy, Agency, and Safety

This document describes the current autonomy system. It replaces the older one-off implementation note with the behavior that is active in the repo now.

## Boundaries

Mandy does not have literal free will. The software gives her a decision loop:

1. Observe Discord context.
2. Score attention and relevance.
3. Check policy, permissions, cooldowns, and approvals.
4. Choose from allowed actions.
5. Execute or store a proposal.
6. Log the decision and outcome.
7. Learn from stored outcomes and memory summaries.

## Main Components

- `AutonomyEngine` in `src/mandy_v1/services/autonomy_engine.py`
  Runs continuous background behavior selection and outcome tracking.

- `BehaviorLibrary` in `src/mandy_v1/services/behavior_library.py`
  Produces candidate autonomous behaviors such as callbacks, curiosity bursts, relationship maintenance, expansion processing, and nickname updates.

- `AIService` in `src/mandy_v1/services/ai_service.py`
  Handles attention scoring, ambient agency decisions, memory, telemetry, privacy, prompt context, and AI completions.

- `AgentCoreService` in `src/mandy_v1/services/agent_core_service.py`
  Applies a final directive layer before risky proposed actions can proceed.

- `PermissionIntelligenceService` in `src/mandy_v1/services/permission_intelligence_service.py`
  Scans what Mandy can do, maps abilities to Discord permissions, identifies authority candidates, and writes permission request plans.

- `ServerControlService` in `src/mandy_v1/services/server_control_service.py`
  Executes Discord mutations through guarded wrappers.

- `IntelligenceControlsCog` in `src/mandy_v1/cogs/intelligence_controls.py`
  Exposes privacy, telemetry, reflection compaction, agency, and wake-broadcast commands.

## Agency Decisions

Agency mode controls ambient public-message behavior.

Policy state lives under `ai.agency`:

```python
{
    "enabled": True,
    "ambient_min_score": 0.55,
    "reply_min_score": 0.78,
    "decision_log": []
}
```

When a message is not directly addressed to Mandy:

- If agency is off, Mandy ignores it.
- If ambient chat is disabled by permission voice policy, Mandy ignores it.
- If the attention score is below `ambient_min_score`, Mandy ignores it.
- If the score meets `ambient_min_score`, Mandy can react.
- If the score meets `reply_min_score`, Mandy can choose a reply.
- Every decision is logged with guild, channel, user, action, reason, score, and whether Mandy was addressed.

Discord controls:

- `!agency show`
- `!agency on`
- `!agency off`
- `!agency thresholds <ambient_min> <reply_min>`
- `!ambient show|on|off`

## Autonomy Policy

Policy state lives under `autonomy_policy`.

Key concepts:

- Mode controls the baseline action set.
- Extra allow-list entries can be added or removed.
- External-contact and high-risk actions require approval.
- Rate limits prevent repeated autonomous mutations.
- Proposals preserve the original payload for later review.

Discord controls:

- `!autonomymode show|assist|trusted|wild|off`
- `!autonomyapproval show|on|off`
- `!autonomyallow`
- `!autonomyallow add <action>`
- `!autonomyallow remove <action>`
- `!autonomydash`
- `!autonomyapprove <proposal_id>`

Approval flow:

1. Mandy proposes an action.
2. The policy layer checks mode, allow-list, permissions, risk, and approval requirements.
3. If approval is required, the payload is stored as a pending proposal.
4. An authorized user reviews it in Discord.
5. `!autonomyapprove <proposal_id>` executes the stored payload through `ServerControlService`.
6. The proposal is marked executed or failed and logged.

## Permission Intelligence

Permission intelligence helps Mandy know when she is blocked and who to ask.

It tracks:

- Required permissions for known capabilities.
- Current bot permissions by guild.
- Missing permissions.
- Server owner and authority candidates.
- Voice policy for story mode and ambient chat.
- Permission request logs.

Discord controls:

- `!permscan [guild_id]`
- `!authority [guild_id]`
- `!permask <guild_id> <capability> [reason]`
- `!storymode show|on|off`
- `!ambient show|on|off`

If Mandy lacks a capability, `!permask` can identify the best authority target, usually the server owner first, and record a request plan.

## Reflection Compaction

Mandy collects noisy interaction data, then compacts it into cleaner summaries:

- stable user traits
- preferences
- ongoing storylines
- unresolved social threads
- server-level patterns

Controls:

- Scheduled loop in `bot.py`.
- Manual command: `!compactreflections [guild_id]`.
- Summary command: `!reflect [guild_id]`.

## Privacy

Privacy controls are Discord-native.

Commands:

- `!privacy status [user_id]`
- `!privacy pause [user_id] [reason]`
- `!privacy resume [user_id] [reason]`
- `!privacy export [user_id]`
- `!privacy forget [user_id] [reason]`
- `!privacyaudit`

Self-service is allowed for the calling user. Cross-user controls require high authorization.

## Telemetry and Caching

AI telemetry tracks:

- calls
- successes and failures
- fallback count
- models used
- cache hits
- in-flight joins
- persistent cache rows
- budget throttles
- cooldown remaining
- estimated tokens
- estimated cost

Command:

- `!telemetry`

The AI path uses request caching, persistent completion cache rows, in-flight request coalescing, cooldown backoff, and a configurable per-minute API budget.

## Controlled Wake Broadcast

Wake broadcast is not automatic on deploy. It is an explicit SOC-gated command that only targets known DM contacts and has cooldown protection.

Commands:

- `!wakebroadcast preview [limit]`
- `!wakebroadcast send <limit> [message]`

The command records sent/failed counts and a short audit log.

## Safety Notes

- Keep `!autonomyapproval on` for risky deployments.
- Use `!autonomydash` before widening action allow-lists.
- Use `!permscan` after inviting Mandy to a new guild.
- Use `!agency thresholds` carefully; low thresholds make ambient reactions more common.
- Do not treat generated decisions as proof of consciousness. They are logged software decisions from policy and context.
