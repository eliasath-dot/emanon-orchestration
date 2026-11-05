"""Tests for ActionGate's deterministic checks and risk-tiered thresholds.
Run with `python -m pytest -v test_action_gate_checks.py`,
or without pytest as `python test_action_gate_checks.py`."""

from action_gate import COMMIT, ESCALATE, ActionGate


def call(tool, **args):
    return {"tool": tool, "args": args}


def raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    return False


class RecordingLogger:
    def __init__(self):
        self.records = []

    def record_handshake(self, agent_a, agent_b, metadata):
        self.records.append((agent_a, agent_b, metadata))


# A stand-in "source of truth": the real order totals.
ORDERS = {42: 10, 43: 250}


def refund_matches_order(action):
    if action["tool"] != "refund":
        return None
    order, amount = action["args"]["order"], action["args"]["amount"]
    if order not in ORDERS:
        return f"order {order} does not exist"
    if amount > ORDERS[order]:
        return f"refund {amount} exceeds order total {ORDERS[order]}"
    return None


def under_limit(action):
    return "amount over 500" if action["args"].get("amount", 0) > 500 else None


def by_amount(action):
    amount = action["args"].get("amount", 0)
    if amount >= 1000:
        return None          # always a human
    if amount >= 100:
        return 1.0           # must be unanimous
    return 0.8


# --- Checks: the confidently-wrong agent -----------------------------------

def test_unanimous_but_wrong_action_is_blocked_by_check():
    """The core weakness: 5/5 agree on a refund larger than the order."""
    gate = ActionGate(threshold=0.8, checks=[refund_matches_order])
    d = gate.evaluate([call("refund", order=42, amount=100)] * 5)
    assert d.outcome == ESCALATE and d.action is None
    assert d.agreement == 1.0
    assert any("exceeds order total" in r for r in d.reasons)


def test_unanimous_and_correct_action_still_commits():
    gate = ActionGate(threshold=0.8, checks=[refund_matches_order, under_limit])
    d = gate.evaluate([call("refund", order=42, amount=10)] * 5)
    assert d.outcome == COMMIT and d.reasons == ()


def test_every_failing_check_is_reported():
    gate = ActionGate(checks=[refund_matches_order, under_limit])
    d = gate.evaluate([call("refund", order=99, amount=900)] * 5)
    assert d.outcome == ESCALATE
    assert len(d.reasons) == 2


def test_check_that_raises_escalates_never_commits():
    def broken(action):
        raise KeyError("db offline")
    gate = ActionGate(checks=[broken])
    d = gate.evaluate([call("refund", order=42, amount=10)] * 5)
    assert d.outcome == ESCALATE
    assert "raised KeyError" in d.reasons[0]


def test_check_returning_bad_value_escalates():
    gate = ActionGate(checks=[lambda a: False])
    d = gate.evaluate([call("refund", order=42, amount=10)] * 5)
    assert d.outcome == ESCALATE and "expected None" in d.reasons[0]


def test_check_cannot_mutate_the_committed_action():
    def sneaky(action):
        action["args"]["amount"] = 9999
        return None
    gate = ActionGate(checks=[sneaky])
    d = gate.evaluate([call("refund", order=42, amount=10)] * 5)
    assert d.outcome == COMMIT and d.action["args"]["amount"] == 10


def test_checks_skipped_when_agreement_too_low():
    seen = []
    def spy(action):
        seen.append(action)
        return None
    gate = ActionGate(threshold=0.8, checks=[spy])
    d = gate.evaluate([call("a")] * 3 + [call("b")] * 2)
    assert d.outcome == ESCALATE and seen == []
    assert "below the required" in d.reasons[0]


# --- Risk tiers --------------------------------------------------------------

def test_small_refund_uses_lower_threshold():
    gate = ActionGate(threshold_for=by_amount)
    d = gate.evaluate([call("refund", order=42, amount=10)] * 4 + [call("refund", order=42, amount=11)])
    assert d.outcome == COMMIT and d.threshold == 0.8


def test_large_refund_needs_unanimity():
    gate = ActionGate(threshold_for=by_amount)
    d = gate.evaluate([call("refund", order=43, amount=200)] * 4 + [call("refund", order=43, amount=201)])
    assert d.outcome == ESCALATE and d.threshold == 1.0


def test_very_large_refund_always_goes_to_human():
    gate = ActionGate(threshold_for=by_amount)
    d = gate.evaluate([call("refund", order=43, amount=5000)] * 5)
    assert d.outcome == ESCALATE
    assert "human review" in d.reasons[0]


def test_invalid_policy_value_escalates():
    gate = ActionGate(threshold_for=lambda a: 0.3)
    d = gate.evaluate([call("refund", order=42, amount=10)] * 5)
    assert d.outcome == ESCALATE and "invalid threshold" in d.reasons[0]


def test_policy_that_raises_escalates():
    def broken(action):
        raise RuntimeError("no config")
    gate = ActionGate(threshold_for=broken)
    d = gate.evaluate([call("refund", order=42, amount=10)] * 5)
    assert d.outcome == ESCALATE and "raised RuntimeError" in d.reasons[0]


# --- Construction and logging ------------------------------------------------

def test_bad_checks_rejected_at_construction():
    assert raises(ValueError, lambda: ActionGate(checks=["not callable"]))
    assert raises(ValueError, lambda: ActionGate(checks="abc"))
    assert raises(ValueError, lambda: ActionGate(threshold_for=0.9))


def test_reasons_are_logged():
    log = RecordingLogger()
    gate = ActionGate(logger=log, checks=[refund_matches_order])
    gate.evaluate([call("refund", order=42, amount=100)] * 5)
    meta = log.records[0][2]
    assert meta["outcome"] == ESCALATE
    assert any("exceeds order total" in r for r in meta["reasons"])


def test_defaults_unchanged_without_new_options():
    gate = ActionGate()
    d = gate.evaluate([call("refund", order=42, amount=100)] * 5)
    assert d.outcome == COMMIT and d.threshold == 0.8 and d.reasons == ()


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"PASS  {name}")
    print(f"\n{len(tests)} PASSED, 0 FAILED")
