"""Tests for ActionGate. Run with `python -m pytest -v test_action_gate.py`,
or without pytest as `python test_action_gate.py`."""

import math

from action_gate import COMMIT, ESCALATE, ActionGate, canonical_action


def call(tool, **args):
    return {"tool": tool, "args": args}


def raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    return False


class RecordingLogger:
    """Stands in for AuditLogger: same record_handshake signature."""

    def __init__(self):
        self.records = []

    def record_handshake(self, agent_a, agent_b, metadata):
        self.records.append((agent_a, agent_b, metadata))


class BrokenLogger:
    def record_handshake(self, agent_a, agent_b, metadata):
        raise IOError("disk full")


# --- Criterion: unanimous agreement commits ---------------------------------

def test_unanimous_commits():
    gate = ActionGate(threshold=0.8)
    d = gate.evaluate([call("refund", order=42, amount=10)] * 5)
    assert d.outcome == COMMIT and d.committed
    assert d.action == call("refund", order=42, amount=10)
    assert d.agreement == 1.0 and d.n_samples == 5


def test_four_of_five_meets_threshold_exactly():
    gate = ActionGate(threshold=0.8)
    d = gate.evaluate([call("refund", order=42)] * 4 + [call("refund", order=43)])
    assert d.outcome == COMMIT and d.agreement == 0.8


def test_three_of_five_escalates():
    gate = ActionGate(threshold=0.8)
    d = gate.evaluate([call("refund", order=42)] * 3 + [call("cancel", order=42)] * 2)
    assert d.outcome == ESCALATE and d.action is None
    assert math.isclose(d.agreement, 0.6)


# --- Criterion: a 50/50 split escalates -------------------------------------

def test_even_split_escalates_even_at_lowest_threshold():
    gate = ActionGate(threshold=0.51)
    d = gate.evaluate([call("buy", ticker="AAPL")] * 2 + [call("sell", ticker="AAPL")] * 2)
    assert d.outcome == ESCALATE
    assert [c for _, c in d.candidates] == [2, 2]


def test_escalation_lists_all_candidates_largest_first():
    gate = ActionGate(threshold=0.8)
    d = gate.evaluate([call("a")] * 1 + [call("b")] * 3 + [call("c")] * 2)
    assert [(a["tool"], c) for a, c in d.candidates] == [("b", 3), ("c", 2), ("a", 1)]


# --- Criterion: reordered parameters are the same action --------------------

def test_parameter_order_does_not_matter():
    a = {"tool": "transfer", "args": {"to": "acct-1", "amount": 100}}
    b = {"tool": "transfer", "args": {"amount": 100, "to": "acct-1"}}
    assert canonical_action(a) == canonical_action(b)
    d = ActionGate().evaluate([a, b, a])
    assert d.outcome == COMMIT and d.agreement == 1.0


def test_different_values_are_different_actions():
    assert canonical_action(call("transfer", amount=100)) != canonical_action(call("transfer", amount=1000))
    assert canonical_action(call("transfer", amount=100)) != canonical_action(call("transfer", amount="100"))


# --- Criterion: empty or malformed input is rejected, never passed ----------

def test_empty_and_too_few_rejected():
    gate = ActionGate(min_samples=3)
    assert raises(ValueError, lambda: gate.evaluate([]))
    assert raises(ValueError, lambda: gate.evaluate([call("x")] * 2))


def test_malformed_proposals_rejected():
    gate = ActionGate()
    good = call("x")
    bad_inputs = [
        "not a list",
        [good, good, "free text instead of a tool call"],
        [good, good, {"tool": "x"}],                         # missing args
        [good, good, {"tool": "x", "args": {}, "extra": 1}],  # unexpected key
        [good, good, {"tool": "", "args": {}}],               # empty tool name
        [good, good, {"tool": "x", "args": [1, 2]}],          # args not a dict
        [good, good, {"tool": "x", "args": {"v": float("nan")}}],
        [good, good, {"tool": "x", "args": {"v": object()}}],
    ]
    for bad in bad_inputs:
        assert raises(ValueError, lambda b=bad: gate.evaluate(b)), bad


def test_bad_configuration_rejected():
    for t in [0.5, 0.3, 0, 1.01, float("nan"), float("inf"), True, "0.8"]:
        assert raises(ValueError, lambda t=t: ActionGate(threshold=t)), t
    for m in [1, 0, -3, 2.5, True]:
        assert raises(ValueError, lambda m=m: ActionGate(min_samples=m)), m


# --- Criterion: every decision is logged, and logging failure fails closed --

def test_every_decision_is_logged():
    log = RecordingLogger()
    gate = ActionGate(logger=log, gate_id="refund_gate")
    gate.evaluate([call("refund", order=1)] * 5, agent="support_agent")
    gate.evaluate([call("a"), call("b"), call("c")], agent="support_agent")
    assert [r[2]["outcome"] for r in log.records] == [COMMIT, ESCALATE]
    agent, gate_name, meta = log.records[1]
    assert (agent, gate_name) == ("support_agent", "refund_gate")
    assert meta["n_samples"] == 3 and meta["threshold"] == 0.8
    assert len(meta["candidates"]) == 3


def test_logging_failure_returns_no_decision():
    gate = ActionGate(logger=BrokenLogger())
    assert raises(IOError, lambda: gate.evaluate([call("refund", order=1)] * 5))


# --- Criterion: a tampered decision log fails verification ------------------
# Runs against the real AuditLogger when emanon_orchestration_core is importable
# (i.e. inside the emanon-orchestration repo); skipped otherwise.

def test_tampered_decision_log_fails_verification():
    try:
        from emanon_orchestration_core import AuditLogger
    except ImportError:
        if __name__ != "__main__" and _STANDALONE is False:
            import pytest
            pytest.skip("emanon_orchestration_core not available here")
        print("SKIP  test_tampered_decision_log_fails_verification (emanon_orchestration_core not available here)")
        return "skipped"
    log = AuditLogger(secret_key="test-key-for-action-gate-0123456789abcdef")
    gate = ActionGate(logger=log)
    gate.evaluate([call("a"), call("b"), call("a")], agent="planner")
    gate.evaluate([call("refund", order=7)] * 5, agent="planner")
    assert log.verify_chain()
    log.chain[0].metadata["outcome"] = COMMIT  # rewrite an escalation as a commit
    assert not log.verify_chain()


_STANDALONE = False

if __name__ == "__main__":
    _STANDALONE = True
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed = skipped = 0
    for name, fn in tests:
        if fn() == "skipped":
            skipped += 1
        else:
            passed += 1
            print(f"PASS  {name}")
    print(f"\n{passed} PASSED, {skipped} SKIPPED, 0 FAILED")
