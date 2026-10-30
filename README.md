# Emanon Orchestration Core

A small Python library for keeping multi-agent systems honest. It provides four
independent primitives that an orchestrator can use to record what agents did,
decide whether the work was worth its compute, detect when an agent has
drifted away from its original objective, and hold back an irreversible action
when the agent's own samples disagree about it.

Version 0.1.0 — working prototype, fully tested, not yet production-hardened.
Built by Helixis AI Systems Inc. (Toronto, Ontario).

## Why

Agent frameworks make it easy to run many agents and hard to answer three
questions afterwards: *What actually happened? Was it worth the compute? Is the
agent still doing what I asked?* This library answers each one with a small,
checkable mechanism rather than another model call.

## Components

### `AuditLogger` — tamper-evident interaction log

Records a handshake between two agents as an HMAC-SHA256 signed entry that
includes the previous entry's signature, forming a hash chain. Modifying,
reordering or removing any entry breaks verification.

```python
from emanon_orchestration_core import AuditLogger

log = AuditLogger(secret_key="your-secret-key")
log.record_handshake("planner", "executor", {"task": "sync"})
log.record_handshake("executor", "reviewer", {"task": "route"})

log.verify_chain()           # True
log.chain[0].metadata["task"] = "tampered"
log.verify_chain()           # False
```

Signing payloads are serialized with `sort_keys=True`, compact separators and
`allow_nan=False`, so the bytes being signed are deterministic. Signature
comparison uses `hmac.compare_digest` to avoid timing leaks. Metadata that is
not a dict, not JSON-serializable, or contains `NaN`/`Infinity` is rejected at
record time rather than silently signed.

**Scope and limits.** The chain is tamper-*evident*, not tamper-*proof*. It
lives in memory, anyone holding the secret key can re-sign a forged chain, and
nothing records the expected chain length, so truncating the tail is not
detected. Treat it as an integrity check against accidental or downstream
corruption, not as a security boundary.

### `ThresholdOptimizer` — value-vs-compute reward

Scores a unit of work against the compute it consumed:

```
reward = (clarity × quality) + recycled_value − waste_weight × compute^waste_exponent
```

With the defaults (`waste_weight=1.2`, `waste_exponent=1.05`), compute is
penalized slightly superlinearly, so doubling spend must more than double value
to stay positive. The returned status is one of *Aligned*, *Balanced*, or
*Drifting (Circuit Breaker Triggered)* — the last is the signal to stop a loop
that is burning compute without producing value.

```python
from emanon_orchestration_core import ThresholdOptimizer

ThresholdOptimizer().step(clarity=2.0, quality=3.0, v_recycle=1.0, raw_compute_cost=1.0)
# {'net_reward': 5.8, ..., 'status': 'System Aligned: Sustainable Innovation'}
```

The weights are tunable parameters, not derived constants — pick them to match
what compute actually costs you.

### `EmanonMetaController` — drift router

Holds a normalized "root" embedding representing the original objective and
compares each agent state against it by cosine similarity. At or above the
threshold the agent is on course (`PATH_INTEGRATION`); below it, it has drifted
and should explore or be reset (`LEVY_FLIGHT`).

```python
from emanon_orchestration_core import EmanonMetaController

c = EmanonMetaController(root_vector=[1.0, 0.0], drift_threshold=0.75)
c.evaluate_trajectory([0.9, 0.1])   # ('PATH_INTEGRATION', 0.993)
c.evaluate_trajectory([0.0, 1.0])   # ('LEVY_FLIGHT', 0.0)
```

### `InteractionPolicy` — relation weights

Maps relation types to scalar weights for graph-based scoring: *Causally
Drives* 3.0, *Resolves* 2.5, *Enhances* 1.5, *Mentions* 0.5, *Conflicts* −1.0.

### `ActionGate` — commit only when the agent agrees with itself

Before an agent does something irreversible (a refund, a database write, a
message to a customer), sample it several times for the action it wants to
take. `ActionGate` groups identical proposals and commits only if one action
wins a large enough share. Otherwise it escalates, with every candidate, to a
human.

```python
from action_gate import ActionGate
from emanon_orchestration_core import AuditLogger

log = AuditLogger(secret_key="your-secret-key")
gate = ActionGate(threshold=0.8, logger=log)

proposals = [
    {"tool": "refund", "args": {"order": 42, "amount": 10}},
    {"tool": "refund", "args": {"amount": 10, "order": 42}},  # same action, reordered
    {"tool": "refund", "args": {"order": 42, "amount": 10}},
    {"tool": "refund", "args": {"order": 42, "amount": 10}},
    {"tool": "refund", "args": {"order": 42, "amount": 100}},
]
decision = gate.evaluate(proposals, agent="support_agent")
decision.outcome    # "COMMIT" (4 of 5 agree = 0.8)
decision.action     # {"args": {"amount": 10, "order": 42}, "tool": "refund"}
log.verify_chain()  # True: the decision is in the tamper-evident log
```

Proposals must be structured tool calls, `{"tool": str, "args": dict}`, so two
proposals are "the same" by exact match after parameter order is normalized.
No embedding model or extra LLM call is involved. Malformed proposals, too few
samples, or a bad threshold raise an error rather than being guessed at. The
threshold must be above 0.5, so a tie can never commit. If the logger fails,
the gate raises instead of returning a decision, so there is no unlogged
commit. Standard library only.

**Scope and limits.** Agreement is not correctness. An agent that is
consistently wrong produces unanimous samples and passes this gate:
`ActionGate` catches an agent that is unsure, not one that is wrong. It also
assumes the samples are independent; if they share a context that steers them
the same way, agreement overstates confidence. Exact matching treats `10` and
`10.0`, or `"Apple"` and `"AAPL"`, as different actions, which errs toward
escalating. Whether disagreement actually predicts errors on real agent tasks
is the open question this gate exists to test.

## Install and test

Requires Python 3.9+ and `numpy`.

```bash
pip install -r requirements.txt
python -m pytest -v
```

24 tests for the core, all passing, plus 13 for `ActionGate`, whose module
needs only the standard library. Coverage is weighted toward negative cases: tampered
metadata, forged signatures, rewritten `prev_signature`, malformed entries,
weak secret keys, non-serializable and `NaN` metadata, non-finite reward
inputs, zero and mismatched vectors.

## Status and roadmap

This is a prototype. It is tested and the behavior is verified, but it has no
persistence, no published package, and no production users.

Next, in order:

1. An experiment on real agent tasks: does disagreement at the `ActionGate`
actually predict the actions that would have gone wrong?
2. Append-only JSONL persistence via `pathlib`, with chain length recorded so
   truncation is detectable.
3. Key rotation and a documented threat model for the audit chain.
4. A worked end-to-end example wiring the primitives into one agent loop.
5. Packaging for PyPI.

## License

Apache License 2.0. See `LICENSE`.
