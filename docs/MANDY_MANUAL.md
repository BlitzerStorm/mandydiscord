# Mandy AI — Complete Manual

**Status:** ✅ Integrated & Production Ready
**Generated:** 2026-01-13

---

## Overview ✨
Mandy AI is a Discord bot integration that provides natural-language command processing and safe tool execution. It combines a robust intelligent command processor (intent recognition, fuzzy argument extraction, context memory, clarifications) with Mandy's existing tool ecosystem (send_dm, watchers, mirrors, stats, queue). This single manual merges the quick reference, integration notes, and system summary and adds configuration, extension developer guidance, and troubleshooting.

---

## Quick Start 🚀
- Start the bot: `python main.py`
- Typical user examples (no syntax knowledge required):
  - `dm @john hello`
  - `watch john after 5 messages say hey`
  - `mirror #general to #backup`
  - `show stats`
  - `health` or `queue`
- Verify integration (optional): `python verify_integration.py`

---

## Architecture & Key Files 🔧
- `intelligent_command_processor.py` — core engine (IntentRecognizer, ArgumentExtractor, ContextMemory, ClarificationHandler, ConfirmationFormatter, IntelligentCommandProcessor)
- `cogs/mandy_ai.py` — Mandy integration and executor bridge (`_ManydAICommandExecutor`) and routing logic
- `capability_registry.py` — tool registry
- `tool_plugin_manager.py` — plugin loader for extension builds
- `extensions/` — extension tools and validators
- `main.py` — default config and startup
- `verify_integration.py` — checks integration points

---

## What the Intelligent Processor Does ✅
- Detects intent from natural text across 8 major types: **send_dm, add_watcher, remove_watcher, list_watchers, create_mirror, show_stats, show_health, show_queue**.
- Uses fuzzy name matching, pronoun resolution (last targets), context memory (last ~20 actions per user), and clarifications when ambiguous.
- If the processor handles the request, it executes via `_ManydAICommandExecutor` which calls `mandy._execute_actions()` and returns human-friendly confirmations.
- If the processor fails or throws, Mandy falls back to the traditional parsing system automatically.

---

## Configuration (per-guild & global) ⚙️
Config is exposed via Mandy's AI config (see `main.py` defaults). Important keys:
- `cooldown_seconds`: default 5 (command cooldown). Use `mandy_limits` command to view current value.
- `limits`: per-model limits; each model can have `rpm` (requests per minute), `tpm` (tokens per minute), `rpd` (requests per day). Default values are in `DEFAULT_AI_LIMITS` in `main.py`.
- `installed_extensions`: list of extension module names (default `[]`).
- `tts_model`, `default_model`, `router_model` etc. — used for routing and TTS.

Tip: Expose safe commands or admin UI to view/update per-guild config. Add documentation entry: `mandy_config get|set <key> <value>` (planned).

---

## Limits & Rate Handling ⚠️
- Local limits are enforced using per-model `rpm`, `tpm`, `rpd`. When thresholds are hit, Mandy will either return a friendly message, raise `LocalRateLimitError`, or enqueue the job with a backoff.
- Backoff schedule default (seconds): `[10, 30, 60, 120, 240, 480, 600]` (configurable in future).
- Helpful messages should explain why the user is rate-limited and suggest fixes (reduce message length, switch to cheaper model, retry later).

---

## Commands & Quick Reference 📝
- `mandy` — primary command to invoke the AI (in code as `mandy_cmd`)
- `mandy_model <name>` — set or list model
- `mandy_queue` — show queued jobs
- `mandy_cancel <job_id>` — cancel queued job
- `mandy_limits` — show cooldown and configured limits

User-focused examples (natural language):
- `dm @alice "Hey — can you review this?"`
- `watch bob after 10 messages say "please be nice"`
- `mirror #announcements to #backup`
- `show stats weekly`
- `queue` or `health`

---

## Extension Development — Build & Safety Rules 🔐
When building or accepting tool extensions, the system enforces strict validation and safe defaults to avoid executing arbitrary or malicious code. Key rules:

- Extensions must define `TOOL_EXPORTS` as a dict literal mapping `"tool_name"` → dict with `description`, `args_schema`, `side_effect` (`read`|`write`), `cost` (`cheap|normal|expensive`), and `handler` async callable.
- Files must live under `extensions/` and end with `.py`.
- Files must be <= 200KB (configurable restriction).
- **Allowed imports:** `discord`, `discord.ext.commands`, `typing`, `datetime`, `json`, `re`, `asyncio`, `math`, `random`.
- **Denied imports:** `os`, `sys`, `subprocess`, `socket`, `requests`, `aiohttp`, `httpx`, `pathlib`, `shutil`, `aiomysql`, `sqlite3`, `pickle`.
- No `eval`, `exec`, `open` calls, or other un-sandboxed file/network operations.

Validation flow:
1. `validate_extension_path(path)` ensures correct location/filename.
2. `validate_extension_source(slug, content)` analyzes source for disallowed patterns and returns specific errors (line-oriented messages when possible).
3. Build process writes the file, validates `TOOL_EXPORTS` present, runs `validate_extension_source` and only then loads the module using `tool_plugin_manager`.

Developer friendliness:
- Use provided extension templates (scaffold generator) or `validator_unrestricted.py` in a local dev-only mode to speed prototyping.
- The production validator will reject dangerous imports and open/calls. Errors include reason strings — improve UX by exposing line snippets and exact fix recommendations.

---

## BUILD TOOL (BUILD_TOOL intent) 🛠️
- Users (or the router) can return a `build` intent containing `slug` and `files` to create a new extension. Mandy writes files and attempts to load the new module, adding it to `installed_extensions` on success.
- Build validation checks slug format (`[a-z0-9_]{3,32}`) and ensures `TOOL_EXPORTS` exists, that files pass path/source validation, and performs an import test.
- Post-build, Mandy notifies user with reload/unload instructions and a DM summary.

---

## Developer & Admin UX recommendations (beginners-first) 💡
To make the system easier for beginners, adopt these improvements (we can implement them):
- Add a `beginner_mode` opt-in to relax non-security-critical constraints and show inline tips.
- Provide `mandy scaffold-extension <name>` to generate a correct template with `TOOL_EXPORTS` and tests.
- Improve validator error messages with line numbers and suggested fixes.
- Add an interactive `mandy build --dry-run` that validates and reports problems without writing files.
- Add `mandy_help` and `mandy_getting_started` commands in Discord to walk admins through common tasks.

---

## Troubleshooting & Diagnostics 🛟
- If intelligent processor fails: check console logs for `Intelligent processor error (falling back): {e}` — the system will fallback to classic parsing.
- If extensions fail to load: check validator errors and build diagnostics; ensure `TOOL_EXPORTS` is present and imports are allowed.
- If you see rate-limit messages: run `mandy_limits` to inspect `rpm`/`tpm`/`rpd` and reduce prompt size or swap to a different model.
- Run `python verify_integration.py` to check all integration points.

---

## Security Considerations & Tradeoffs ⚠️
- Validator rules are intentionally strict to prevent code that can access the file system, spawn processes, or make uncontrolled network requests.
- Developer mode (unrestricted validator) should only be used in safe, local environments.
- Any relaxation of import restrictions should be opt-in, gated to server administrators, and logged.

---

## Example: Creating a Safe Extension (snippet)
```python
# extensions/hello_tool.py
TOOL_EXPORTS = {
    "hello": {
        "description": "Say hello to a user",
        "args_schema": {"user_id": {"type": "int"}, "text": {"type": "str"}},
        "side_effect": "write",
        "cost": "cheap",
        "handler": async def handler(ctx, user_id: int, text: str):
            return f"Hello <@{user_id}>: {text}"
    }
}
```
Run build flow or load plugin via `tool_plugin_manager` to register.

---

## Changelog & Notes 📝
- 2026-01-13: Merged docs and integrated the Intelligent Command Processor into Mandy; manual created.
- Default config from `main.py`: `cooldown_seconds` default 5; `limits` seeded from `DEFAULT_AI_LIMITS`.

---

## Where to go next ☑️
- Want me to implement the suggested beginner-mode changes, add scaffold generator, and improve validator error formatting? Reply: **Start quick wins** and I will implement the first set of changes (config options, better rate limit messaging, `mandy_help`).

---

If you want a plain `.txt` copy or changes in tone/length, tell me which sections to expand or condense.
