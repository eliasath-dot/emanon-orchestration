"""Worked example: the four primitives wired into one agent loop.

Run: python example_agent_loop.py

The "agent" here is a stub that returns canned proposals; swap `sample_agent`
for real model calls. Everything else is what a real loop looks like:

  1. The drift router checks the agent is still on its original objective.
  2. The agent is sampled several times for the action it wants to take.
  3. ActionGate commits only if the samples agree; otherwise it escalates.
  4. The reward tracker scores value against compute and trips a breaker.
  5. Every decision lands in the tamper-evident audit chain, which is
     persisted to JSONL with a sealed head stored separately.

This is an illustrative walkthrough with a stub agent, not an observed result
on real tasks. Whether disagreement predicts error is still an open question
(see README, roadmap item 1).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from action_gate import ActionGate
from emanon_orchestration_core import AuditLogger, EmanonMetaController, ThresholdOptimizer

SECRET = "replace-with-a-real-secret-0123456789abcdef"


def sample_agent(task: str, n: int) -> list[dict]:
    """Stub: a confident agent for one task, an unsure one for the other."""
    if task == "issue a refund":
        return [{"tool": "refund", "args": {"order": 42, "amount": 10}}] * n
    half = n // 2
    return ([{"tool": "email", "args": {"to": "customer", "body": "apology"}}] * half
            + [{"tool": "email", "args": {"to": "manager", "body": "escalation"}}] * (n - half))


def main() -> None:
    workdir = Path(tempfile.mkdtemp())
    log_path, head_path = workdir / "audit.jsonl", workdir / "audit.head.json"

    log = AuditLogger(secret_key=SECRET)
    gate = ActionGate(threshold=0.8, min_samples=3, logger=log, gate_id="support_gate")
    router = EmanonMetaController(root_vector=[1.0, 0.0, 0.0], drift_threshold=0.75)
    reward = ThresholdOptimizer()

    tasks = [
        ("issue a refund", [0.95, 0.05, 0.0], 1.0),       # on objective, agent agrees: COMMIT
        ("reply to complaint", [0.9, 0.3, 0.1], 4.0),     # on objective, agent split: ESCALATE
        ("rewrite the pricing page", [0.3, 0.9, 0.2], 2.0),  # off objective: router stops it
    ]

    for task, state_embedding, compute in tasks:
        print(f"\n=== {task} ===")
        route, sim = router.evaluate_trajectory(state_embedding)
        print(f"drift router : {route} (cos={sim:.2f})")
        if route != "PATH_INTEGRATION":
            print("               agent has drifted from its objective; resetting before acting")
            log.record_handshake("router", "agent", {"event": "drift_reset", "task": task, "cos": sim})
            continue

        decision = gate.evaluate(sample_agent(task, n=5), agent="support_agent")
        print(f"action gate  : {decision.outcome} (agreement={decision.agreement:.2f})")
        if decision.committed:
            print(f"               executing {decision.action}")
        else:
            for action, count in decision.candidates:
                print(f"               candidate x{count}: {action}")
            print("               handed to a human")

        score = reward.step(clarity=1.0, quality=2.0, v_recycle=0.5, raw_compute_cost=compute)
        print(f"reward       : {score['net_reward']:+.2f}  {score['status']}")
        log.record_handshake("reward", "agent", {"task": task, **{k: v for k, v in score.items() if k != 'status'}})

    log.save_jsonl(log_path)
    head_path.write_text(json.dumps(log.head()))
    print(f"\naudit chain  : {len(log.chain)} entries -> {log_path}")
    print(f"sealed head  : {head_path}")

    restored = AuditLogger.load_jsonl(SECRET, log_path, head=json.loads(head_path.read_text()))
    print(f"verification : chain restored and verified against head ({len(restored.chain)} entries)")


if __name__ == "__main__":
    main()
