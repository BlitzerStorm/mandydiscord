from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class AgentVerdict:
    allowed: bool
    reason: str
    risk: str
    requires_approval: bool


class AgentCoreService:
    """Control shell around Mandy's model-driven plans.

    The model can suggest intent, but this service records the decision boundary:
    what action was considered, what checks ran, whether approval is required,
    and why it was allowed or blocked.
    """

    def __init__(self, store: Any) -> None:
        self.store = store

    def root(self) -> dict[str, Any]:
        data = self.store.data.setdefault("agent_core", {})
        data.setdefault("enabled", True)
        data.setdefault("default_posture", "guarded")
        data.setdefault("directive", "Think with the model; act only through typed Discord tools and policy gates.")
        data.setdefault("audit_log", [])
        data.setdefault("last_verdict", {})
        data.setdefault("risk_counts", {"low": 0, "medium": 0, "high": 0, "blocked": 0})
        return data

    def status_lines(self) -> list[str]:
        root = self.root()
        counts = root.get("risk_counts", {})
        if not isinstance(counts, dict):
            counts = {}
        last = root.get("last_verdict", {})
        if not isinstance(last, dict):
            last = {}
        return [
            f"enabled={bool(root.get('enabled', True))} posture={root.get('default_posture', 'guarded')}",
            f"directive={str(root.get('directive', ''))[:180]}",
            "risk_counts="
            f"low:{int(counts.get('low', 0) or 0)} "
            f"medium:{int(counts.get('medium', 0) or 0)} "
            f"high:{int(counts.get('high', 0) or 0)} "
            f"blocked:{int(counts.get('blocked', 0) or 0)}",
            f"last={last.get('action', 'none')} allowed={last.get('allowed', '')} reason={last.get('reason', '')}",
        ]

    def prompt_block(self) -> str:
        root = self.root()
        return (
            "[AGENT CONTROL SHELL]\n"
            f"Enabled: {bool(root.get('enabled', True))}\n"
            f"Posture: {root.get('default_posture', 'guarded')}\n"
            f"Directive: {root.get('directive', '')}\n"
            "Rule: propose intent freely, but execute only through allowed typed tools after policy checks."
        )

    def evaluate_action(
        self,
        *,
        guild_id: int,
        payload: dict[str, Any],
        base_allowed: bool,
        base_reason: str,
        approval_required: bool,
        destructive_actions: set[str],
        external_actions: set[str],
    ) -> AgentVerdict:
        action = str(payload.get("action", "")).strip()
        risk = "low"
        if action in external_actions:
            risk = "medium"
        if action in destructive_actions:
            risk = "high"
        allowed = bool(base_allowed)
        reason = str(base_reason or "ok")
        requires_approval = bool(approval_required)
        if not bool(self.root().get("enabled", True)):
            allowed = False
            reason = "agent_core_disabled"
        if risk == "high" and not requires_approval:
            allowed = False
            reason = "high_risk_requires_approval"
        if risk == "medium" and action in external_actions and not requires_approval:
            allowed = False
            reason = "external_contact_requires_approval"
        verdict = AgentVerdict(allowed=allowed, reason=reason, risk=risk if allowed else "blocked", requires_approval=requires_approval)
        self.record_verdict(guild_id=guild_id, payload=payload, verdict=verdict)
        return verdict

    def record_verdict(self, *, guild_id: int, payload: dict[str, Any], verdict: AgentVerdict) -> None:
        root = self.root()
        row = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "guild_id": int(guild_id),
            "action": str(payload.get("action", ""))[:80],
            "allowed": bool(verdict.allowed),
            "reason": verdict.reason[:120],
            "risk": verdict.risk,
            "requires_approval": bool(verdict.requires_approval),
        }
        root["last_verdict"] = row
        audit = root.setdefault("audit_log", [])
        if isinstance(audit, list):
            audit.append(row)
            if len(audit) > 500:
                del audit[: len(audit) - 500]
        counts = root.setdefault("risk_counts", {"low": 0, "medium": 0, "high": 0, "blocked": 0})
        if isinstance(counts, dict):
            key = verdict.risk if verdict.risk in {"low", "medium", "high", "blocked"} else "blocked"
            counts[key] = int(counts.get(key, 0) or 0) + 1
        if hasattr(self.store, "touch"):
            self.store.touch()
