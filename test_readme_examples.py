"""I9: every Python example in README.md runs as written and produces what it
claims. If you change an example, this test is what breaks."""

import re
from pathlib import Path

import pytest

README = Path(__file__).with_name("README.md").read_text(encoding="utf-8")
BLOCKS = re.findall(r"```python\n(.*?)```", README, flags=re.S)


def test_readme_has_python_examples():
    assert len(BLOCKS) >= 4


@pytest.mark.parametrize("block", BLOCKS, ids=[b.strip().splitlines()[0][:40] for b in BLOCKS])
def test_readme_block_runs(block):
    exec(compile(block, "<README>", "exec"), {})


def test_readme_audit_logger_claims():
    from emanon_orchestration_core import AuditLogger
    log = AuditLogger(secret_key="your-secret-key")
    log.record_handshake("planner", "executor", {"task": "sync"})
    log.record_handshake("executor", "reviewer", {"task": "route"})
    assert log.verify_chain() is True
    log.chain[0].metadata["task"] = "tampered"
    assert log.verify_chain() is False


def test_readme_threshold_optimizer_claims():
    from emanon_orchestration_core import ThresholdOptimizer
    out = ThresholdOptimizer().step(clarity=2.0, quality=3.0, v_recycle=1.0, raw_compute_cost=1.0)
    assert out["net_reward"] == pytest.approx(5.8)
    assert out["status"] == "System Aligned: Sustainable Innovation"


def test_readme_meta_controller_claims():
    from emanon_orchestration_core import EmanonMetaController
    c = EmanonMetaController(root_vector=[1.0, 0.0], drift_threshold=0.75)
    action, sim = c.evaluate_trajectory([0.9, 0.1])
    assert action == "PATH_INTEGRATION" and round(sim, 3) == 0.994
    action, sim = c.evaluate_trajectory([0.0, 1.0])
    assert action == "LEVY_FLIGHT" and sim == 0.0


def test_readme_action_gate_claims():
    from action_gate import ActionGate
    from emanon_orchestration_core import AuditLogger
    log = AuditLogger(secret_key="your-secret-key")
    gate = ActionGate(threshold=0.8, logger=log)
    proposals = [
        {"tool": "refund", "args": {"order": 42, "amount": 10}},
        {"tool": "refund", "args": {"amount": 10, "order": 42}},
        {"tool": "refund", "args": {"order": 42, "amount": 10}},
        {"tool": "refund", "args": {"order": 42, "amount": 10}},
        {"tool": "refund", "args": {"order": 42, "amount": 100}},
    ]
    decision = gate.evaluate(proposals, agent="support_agent")
    assert decision.outcome == "COMMIT"
    assert decision.action == {"args": {"amount": 10, "order": 42}, "tool": "refund"}
    assert log.verify_chain() is True


def test_worked_example_runs(capsys):
    import example_agent_loop
    example_agent_loop.main()
    out = capsys.readouterr().out
    assert "COMMIT" in out and "ESCALATE" in out and "LEVY_FLIGHT" in out
    assert "verified against head" in out


def test_readme_sealed_head_claims():
    import tempfile
    from emanon_orchestration_core import AuditLogger
    log = AuditLogger(secret_key="your-secret-key")
    for step in range(3):
        log.record_handshake("planner", "executor", {"step": step})
    path = tempfile.mktemp(suffix=".jsonl")
    log.save_jsonl(path)
    head = log.head()
    log.chain.pop()
    log.last_signature = log.chain[-1].signature
    assert log.verify_chain() is True
    assert log.verify_chain(head=head) is False
    assert len(AuditLogger.load_jsonl("your-secret-key", path, head=head).chain) == 3
