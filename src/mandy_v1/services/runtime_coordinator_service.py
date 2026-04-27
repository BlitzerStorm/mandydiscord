from __future__ import annotations

import time
from pathlib import Path
from typing import Any


WORKSPACE_SCAN_TTL_SEC = 60
WORKSPACE_SCAN_LIMIT = 4000
WORKSPACE_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "venv", "node_modules"}


class RuntimeCoordinatorService:
    """Unifies runtime state into compact, reusable context blocks."""

    def __init__(
        self,
        *,
        storage: Any,
        emotion_service: Any | None = None,
        identity_service: Any | None = None,
        episodic_memory_service: Any | None = None,
        persona_service: Any | None = None,
        culture_service: Any | None = None,
        expansion_service: Any | None = None,
        autonomy_engine: Any | None = None,
        self_model_service: Any | None = None,
        agent_core_service: Any | None = None,
    ) -> None:
        self.storage = storage
        self.emotion = emotion_service
        self.identity = identity_service
        self.episodic = episodic_memory_service
        self.personas = persona_service
        self.culture = culture_service
        self.expansion = expansion_service
        self.autonomy = autonomy_engine
        self.self_model = self_model_service
        self.agent_core = agent_core_service
        self._workspace_cache: dict[str, Any] = {"root": "", "ts": 0.0, "snapshot": {}}

    def workspace_snapshot(self, workspace_root: Path) -> dict[str, Any]:
        root = Path(workspace_root).resolve()
        now = time.time()
        cached_root = str(self._workspace_cache.get("root", ""))
        cached_ts = float(self._workspace_cache.get("ts", 0.0) or 0.0)
        cached_snapshot = self._workspace_cache.get("snapshot", {})
        if cached_root == str(root) and (now - cached_ts) <= WORKSPACE_SCAN_TTL_SEC and isinstance(cached_snapshot, dict):
            return dict(cached_snapshot)

        counts = {"total": 0, "py": 0, "tests": 0, "docs": 0}
        recent_files: list[tuple[float, str]] = []
        scanned = 0
        for path in root.rglob("*"):
            if scanned >= WORKSPACE_SCAN_LIMIT:
                break
            if any(part in WORKSPACE_SKIP_DIRS for part in path.parts):
                continue
            if not path.is_file():
                continue
            scanned += 1
            counts["total"] += 1
            suffix = path.suffix.casefold()
            if suffix == ".py":
                counts["py"] += 1
            if "tests" in path.parts:
                counts["tests"] += 1
            if suffix in {".md", ".txt", ".rst"}:
                counts["docs"] += 1
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            rel = str(path.relative_to(root))
            recent_files.append((mtime, rel))

        recent_files.sort(key=lambda item: item[0], reverse=True)
        snapshot = {
            "root": str(root),
            "scanned": scanned,
            "files": counts["total"],
            "python_files": counts["py"],
            "test_files": counts["tests"],
            "doc_files": counts["docs"],
            "recent_files": [name for _mtime, name in recent_files[:5]],
        }
        self._workspace_cache = {"root": str(root), "ts": now, "snapshot": dict(snapshot)}
        return snapshot

    def summarize_selfcheck(self, report: dict[str, list[str]] | None) -> str:
        if not isinstance(report, dict):
            return "Self-check unavailable."
        passed = len(report.get("pass", []))
        warnings = len(report.get("warn", []))
        failures = len(report.get("fail", []))
        top_issue = ""
        if failures:
            top_issue = str(report["fail"][0])[:140]
        elif warnings:
            top_issue = str(report["warn"][0])[:140]
        if top_issue:
            return f"Self-check p={passed} w={warnings} f={failures} top={top_issue}"
        return f"Self-check p={passed} w={warnings} f={failures}"

    def build_prompt_context(
        self,
        *,
        guild_id: int,
        user_id: int,
        topic: str,
        user_name: str = "",
        workspace_root: Path | None = None,
        selfcheck_report: dict[str, list[str]] | None = None,
    ) -> str:
        lines: list[str] = []

        if self.emotion is not None and hasattr(self.emotion, "summary"):
            lines.append(f"Mood: {self.emotion.summary()}")

        if self.personas is not None and hasattr(self.personas, "get_profile"):
            try:
                profile = self.personas.get_profile(user_id)
            except Exception:  # noqa: BLE001
                profile = {}
            if isinstance(profile, dict):
                depth = float(profile.get("relationship_depth", 0.0) or 0.0)
                arc = str(profile.get("arc", "new"))
                style = str(profile.get("communication_style", ""))
                alias = str(user_name or profile.get("preferred_name", "") or profile.get("primary_alias", "")).strip()
                fragment = f"User: depth={depth:.2f} arc={arc}"
                if style:
                    fragment = f"{fragment} style={style}"
                if alias:
                    fragment = f"{fragment} alias={alias[:24]}"
                lines.append(fragment)

        if guild_id > 0 and self.culture is not None and hasattr(self.culture, "get_server_readiness"):
            readiness = self.culture.get_server_readiness(guild_id)
            if isinstance(readiness, dict):
                lines.append(
                    "Guild: "
                    f"active={bool(readiness.get('active', False))} "
                    f"calibrated={bool(readiness.get('calibrated', False))} "
                    f"obs={int(readiness.get('observation_count', 0) or 0)}"
                )

        if guild_id > 0 and self.episodic is not None and hasattr(self.episodic, "format_memory_block"):
            try:
                memory_block, _summaries = self.episodic.format_memory_block(guild_id, topic, limit=1, char_limit=180)
            except Exception:  # noqa: BLE001
                memory_block = ""
            memory_line = " ".join(str(memory_block or "").split())
            if memory_line:
                lines.append(f"Memory: {memory_line[:180]}")

        if self.autonomy is not None and hasattr(self.autonomy, "get_autonomy_status"):
            try:
                status = self.autonomy.get_autonomy_status()
            except Exception:  # noqa: BLE001
                status = {}
            if isinstance(status, dict):
                lines.append(
                    "Autonomy: "
                    f"decisions={int(status.get('decision_count', 0) or 0)} "
                    f"success={float(status.get('recent_success_rate', 0.0) or 0.0):.2f}"
                )

        if self.agent_core is not None and hasattr(self.agent_core, "status_lines"):
            try:
                agent_lines = self.agent_core.status_lines()
            except Exception:  # noqa: BLE001
                agent_lines = []
            if agent_lines:
                lines.append("AgentCore: " + " | ".join(str(line) for line in agent_lines[:2])[:220])

        if self.self_model is not None and hasattr(self.self_model, "snapshot"):
            try:
                snapshot = self.self_model.snapshot(
                    guild_id=guild_id,
                    channel_id=0,
                    user_id=user_id,
                    topic=topic,
                    user_name=user_name,
                    recent_lines=[],
                    facts=[],
                )
            except Exception:  # noqa: BLE001
                snapshot = {}
            if isinstance(snapshot, dict) and snapshot:
                lines.append(
                    "SelfModel: "
                    f"focus={str(snapshot.get('current_focus', 'stay present'))[:40]} "
                    f"goal={str(snapshot.get('social_goal', 'read the room'))[:40]}"
                )

        if workspace_root is not None:
            snapshot = self.workspace_snapshot(workspace_root)
            recent = ", ".join(str(item) for item in snapshot.get("recent_files", [])[:3])
            workspace_line = (
                f"Workspace: files={int(snapshot.get('files', 0) or 0)} "
                f"py={int(snapshot.get('python_files', 0) or 0)} "
                f"tests={int(snapshot.get('test_files', 0) or 0)}"
            )
            if recent:
                workspace_line = f"{workspace_line} recent={recent[:180]}"
            lines.append(workspace_line)

        if selfcheck_report:
            lines.append(self.summarize_selfcheck(selfcheck_report))

        if not lines:
            return ""
        return "[RUNTIME STATE]\n" + "\n".join(lines[:6])
