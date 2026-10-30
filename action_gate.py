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
confidently and consistently wrong produces unanimous samples and passes this
gate. ActionGate catches an agent that is unsure; it does not catch one that
is wrong.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

COMMIT = "COMMIT"
ESCALATE = "ESCALATE"


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

    @property
    def committed(self) -> bool:
        return self.outcome == COMMIT


class ActionGate:
    """Agreement gate in front of an irreversible action.

    threshold must be above 0.5 and at most 1.0. Above 0.5 guarantees that at
    most one action can ever pass, so a tie can never commit.
    min_samples is the fewest proposals the gate will judge; fewer is an error,
    because two matching samples out of two says very little.
    """

    def __init__(
        self,
        threshold: float = 0.8,
        min_samples: int = 3,
        logger: Optional[DecisionLogger] = None,
        gate_id: str = "action_gate",
    ) -> None:
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ValueError("threshold must be a number")
        if not math.isfinite(threshold) or not (0.5 < threshold <= 1.0):
            raise ValueError("threshold must be above 0.5 and at most 1.0")
        if isinstance(min_samples, bool) or not isinstance(min_samples, int) or min_samples < 2:
            raise ValueError("min_samples must be an integer of at least 2")
        if not isinstance(gate_id, str) or not gate_id.strip():
            raise ValueError("gate_id must be a non-empty string")
        self.threshold = float(threshold)
        self.min_samples = min_samples
        self.logger = logger
        self.gate_id = gate_id

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
        if agreement >= self.threshold:
            decision = GateDecision(COMMIT, json.loads(top_key), agreement, self.threshold, n, candidates)
        else:
            decision = GateDecision(ESCALATE, None, agreement, self.threshold, n, candidates)

        if self.logger is not None:
            # Fail closed: if the record can't be written, no decision is returned.
            self.logger.record_handshake(agent, self.gate_id, self._log_record(decision))
        return decision

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
        }
