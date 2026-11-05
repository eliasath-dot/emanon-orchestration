"""ActionGate: commit an agent's action only when its own samples agree.

Sample an agent N times for the action it wants to take. Each sample is a
structured tool call: a tool name plus a dict of parameters. ActionGate groups
identical calls, and if the largest group's share of the samples reaches the
threshold, it returns COMMIT with that action. Otherwise it returns ESCALATE
with every candidate, so a human can decide.

Standard library only. If a logger is supplied (for example AuditLogger from
emanon_orchestration_core), every decision is recorded before it is returned.
If recording fails, the gate raises instead of returning a decision, so there
is never an unlogged COMMIT.

Limit, stated plainly: agreement is not correctness. An agent that is
confidently and consistently wrong produces unanimous samples and passes the
agreement test. ActionGate catches an agent that is unsure; on its own it does
not catch one that is wrong.

Two optional layers narrow that gap without another model call:

- checks: deterministic functions run on the winning action before it can
  commit (e.g. "the refund amount matches the order total in the database",
  "the amount is under a limit"). Each returns None to pass or a reason string
  to block. A blocked or crashing check escalates; it never commits.
- threshold_for: a risk policy that sets the agreement required for a given
  action (e.g. 0.8 for a $10 refund, 1.0 for $1,000), or returns None to send
  that action to a human whatever the agreement.

Agreement is necessary, not sufficient: the samples must agree AND the action
must pass every check.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

COMMIT = "COMMIT"
ESCALATE = "ESCALATE"

# A check receives the winning action and returns None (pass) or a reason (block).
Check = Callable[[Dict[str, Any]], Optional[str]]
# A risk policy receives the winning action and returns the required agreement,
# or None meaning "always escalate this action to a human".
ThresholdPolicy = Callable[[Dict[str, Any]], Optional[float]]


def _valid_threshold(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and 0.5 < value <= 1.0
    )


def _check_name(check: Callable[..., Any]) -> str:
    return getattr(check, "__name__", None) or type(check).__name__


class DecisionLogger(Protocol):
    """Anything with AuditLogger's record_handshake signature."""

    def record_handshake(self, agent_a: str, agent_b: str, metadata: Dict[str, Any]) -> Any:
        ...


def canonical_action(proposal: Mapping[str, Any]) -> str:
    """Return a canonical string for one proposed tool call.

    A proposal must be a mapping with exactly two keys: "tool" (a non-empty
    string) and "args" (a dict). Parameter order does not matter, so
    {"a": 1, "b": 2} and {"b": 2, "a": 1} produce the same string. Anything
    malformed raises ValueError rather than being guessed at.
    """
    if not isinstance(proposal, Mapping):
        raise ValueError(f"proposal must be a mapping, got {type(proposal).__name__}")
    if set(proposal.keys()) != {"tool", "args"}:
        raise ValueError(f"proposal must have exactly the keys 'tool' and 'args', got {sorted(proposal.keys())}")
    tool = proposal["tool"]
    args = proposal["args"]
    if not isinstance(tool, str) or not tool.strip():
        raise ValueError("'tool' must be a non-empty string")
    if not isinstance(args, dict):
        raise ValueError(f"'args' must be a dict, got {type(args).__name__}")
    try:
        return json.dumps(
            {"tool": tool, "args": args},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"'args' must be JSON-serializable without NaN/Infinity: {exc}") from exc


@dataclass(frozen=True)
class GateDecision:
    outcome: str                        # COMMIT or ESCALATE
    action: Optional[Dict[str, Any]]    # the committed action, or None on ESCALATE
    agreement: float                    # share of samples in the largest group
    threshold: float
    n_samples: int
    candidates: Tuple[Tuple[Dict[str, Any], int], ...]  # (action, count), largest first
    reasons: Tuple[str, ...] = ()       # why it escalated, if it did; empty on COMMIT

    @property
    def committed(self) -> bool:
        return self.outcome == COMMIT


class ActionGate:
    """Agreement gate in front of an irreversible action.

    threshold must be above 0.5 and at most 1.0. Above 0.5 guarantees that at
    most one action can ever pass, so a tie can never commit.
    min_samples is the fewest proposals the gate will judge; fewer is an error,
    because two matching samples out of two says very little.
    checks are run, in order, on the winning action only after it reaches the
    required agreement. threshold_for, if given, replaces threshold for each
    action; it must return a valid threshold or None (always escalate).
    """

    def __init__(
        self,
        threshold: float = 0.8,
        min_samples: int = 3,
        logger: Optional[DecisionLogger] = None,
        gate_id: str = "action_gate",
        checks: Sequence[Check] = (),
        threshold_for: Optional[ThresholdPolicy] = None,
    ) -> None:
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ValueError("threshold must be a number")
        if not _valid_threshold(threshold):
            raise ValueError("threshold must be above 0.5 and at most 1.0")
        if isinstance(checks, (str, bytes)) or not isinstance(checks, Sequence):
            raise ValueError("checks must be a list of callables")
        if not all(callable(c) for c in checks):
            raise ValueError("every check must be callable")
        if threshold_for is not None and not callable(threshold_for):
            raise ValueError("threshold_for must be callable")
        if isinstance(min_samples, bool) or not isinstance(min_samples, int) or min_samples < 2:
            raise ValueError("min_samples must be an integer of at least 2")
        if not isinstance(gate_id, str) or not gate_id.strip():
            raise ValueError("gate_id must be a non-empty string")
        self.threshold = float(threshold)
        self.min_samples = min_samples
        self.logger = logger
        self.gate_id = gate_id
        self.checks = tuple(checks)
        self.threshold_for = threshold_for

    def evaluate(self, proposals: Sequence[Mapping[str, Any]], agent: str = "agent") -> GateDecision:
        if isinstance(proposals, (str, bytes)) or not isinstance(proposals, Sequence):
            raise ValueError("proposals must be a list of tool-call proposals")
        if len(proposals) < self.min_samples:
            raise ValueError(f"need at least {self.min_samples} proposals, got {len(proposals)}")
        if not isinstance(agent, str) or not agent.strip():
            raise ValueError("agent must be a non-empty string")

        keys = [canonical_action(p) for p in proposals]
        counts = Counter(keys)
        n = len(keys)
        # Largest group first; ties broken by the canonical string so output is deterministic.
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        top_key, top_count = ranked[0]
        agreement = top_count / n

        candidates = tuple((json.loads(k), c) for k, c in ranked)
        top_action = json.loads(top_key)

        required, reasons = self._required_threshold(top_action)
        if not reasons and agreement < required:
            reasons = [f"agreement {agreement:.3f} is below the required {required:.3f}"]
        if not reasons:
            reasons = self._run_checks(top_action)

        if reasons:
            decision = GateDecision(ESCALATE, None, agreement, required, n, candidates, tuple(reasons))
        else:
            decision = GateDecision(COMMIT, top_action, agreement, required, n, candidates)

        if self.logger is not None:
            # Fail closed: if the record can't be written, no decision is returned.
            self.logger.record_handshake(agent, self.gate_id, self._log_record(decision))
        return decision

    def _required_threshold(self, action: Dict[str, Any]) -> Tuple[float, List[str]]:
        """Agreement required for this action. Fails closed: a policy that
        raises, or returns something invalid, escalates rather than commits."""
        if self.threshold_for is None:
            return self.threshold, []
        try:
            value = self.threshold_for(json.loads(json.dumps(action)))
        except Exception as exc:  # noqa: BLE001 - any policy failure must escalate
            return 1.0, [f"risk policy raised {type(exc).__name__}: {exc}"]
        if value is None:
            return 1.0, ["risk policy requires human review for this action"]
        if not _valid_threshold(value):
            return 1.0, [f"risk policy returned an invalid threshold: {value!r}"]
        return float(value), []

    def _run_checks(self, action: Dict[str, Any]) -> List[str]:
        """Run every check on a copy of the action; collect every failure.
        A check that raises counts as a failure, so it can never let an
        action through."""
        reasons: List[str] = []
        for check in self.checks:
            name = _check_name(check)
            try:
                result = check(json.loads(json.dumps(action)))
            except Exception as exc:  # noqa: BLE001 - any check failure must escalate
                reasons.append(f"check {name} raised {type(exc).__name__}: {exc}")
                continue
            if result is None:
                continue
            if isinstance(result, str) and result.strip():
                reasons.append(f"check {name} blocked: {result}")
            else:
                reasons.append(f"check {name} returned {result!r}; expected None or a reason string")
        return reasons

    @staticmethod
    def _log_record(decision: GateDecision) -> Dict[str, Any]:
        return {
            "event": "action_gate_decision",
            "outcome": decision.outcome,
            "action": decision.action,
            "agreement": decision.agreement,
            "threshold": decision.threshold,
            "n_samples": decision.n_samples,
            "candidates": [{"action": a, "count": c} for a, c in decision.candidates],
            "reasons": list(decision.reasons),
        }
