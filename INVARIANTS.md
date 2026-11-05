# Invariants

Rules this library must never break. Each one names the failure it prevents and
the test that proves it holds. A change that breaks an invariant is rejected
regardless of what else it improves.

Status: `[x]` enforced by a passing test (named) · `[ ]` stated, not yet enforced.
An invariant moves to `[x]` only when its test exists and passes.
`test_invariants.py` checks that every test cited here exists.

---

## I1. No unlogged commit

**Rule.** When a logger is attached, `ActionGate.evaluate` records the decision
before returning it. If the record cannot be written, the gate raises; it never
returns a `COMMIT` that is absent from the audit chain.

**Prevents.** An irreversible action with no trace of who approved it.

- [x] `test_action_gate.py::test_every_decision_is_logged`
- [x] `test_action_gate.py::test_logging_failure_returns_no_decision`

---

## I2. A tie can never commit

**Rule.** `threshold` must be strictly above 0.5. With `n` samples, at most one
action can hold more than half of them, so a split vote always escalates.

**Prevents.** Two equally popular candidates and one of them going through.

- [x] `test_action_gate.py::test_even_split_escalates_even_at_lowest_threshold`
- [x] `test_action_gate.py::test_bad_configuration_rejected`

---

## I3. Escalation hands the human everything

**Rule.** On `ESCALATE`, the decision carries every distinct candidate with its
count, largest first, in deterministic order. The gate decides nothing on the
human's behalf.

**Prevents.** A reviewer approving the top candidate without seeing the others.

- [x] `test_action_gate.py::test_escalation_lists_all_candidates_largest_first`

---

## I4. Malformed input is an error, not a guess

**Rule.** A proposal that is not `{"tool": str, "args": dict}`, args that are not
JSON-serializable or contain `NaN`/`Infinity`, fewer samples than
`min_samples`, or a bad threshold all raise `ValueError`. Nothing is coerced.

**Prevents.** A silent default turning garbage into a confident decision.

- [x] `test_action_gate.py::test_malformed_proposals_rejected`
- [x] `test_action_gate.py::test_empty_and_too_few_rejected`
- [x] `test_emanon_core.py::test_audit_logger_nan_metadata_rejected`
- [x] `test_emanon_core.py::test_threshold_optimizer_invalid_step_inputs`

---

## I5. The audit chain is tamper-evident

**Rule.** Every `AuditLogEntry` is HMAC-SHA256 signed over a deterministic
serialization that includes the previous entry's signature. Changing, reordering
or forging any entry makes `verify_chain()` return `False`. Signatures are
compared with `hmac.compare_digest`.

**Prevents.** A log that reads cleanly after someone edited it.

- [x] `test_emanon_core.py::test_audit_logger_tampered_metadata_fails_verification`
- [x] `test_emanon_core.py::test_audit_logger_tampered_signature_fails_verification`
- [x] `test_emanon_core.py::test_audit_logger_prev_signature_tamper_fails_verification`
- [x] `test_action_gate.py::test_tampered_decision_log_fails_verification`

**Truncation.** `verify_chain()` alone cannot see a cut tail; with a sealed
`head()` stored separately it can. `load_jsonl` refuses a file that fails
either check.

- [x] `test_emanon_core.py::test_audit_logger_truncation_detected_with_head`
- [x] `test_emanon_core.py::test_audit_logger_extension_after_seal_detected`
- [x] `test_emanon_core.py::test_audit_logger_tampered_head_rejected`
- [x] `test_emanon_core.py::test_audit_logger_jsonl_truncated_file_rejected`
- [x] `test_emanon_core.py::test_audit_logger_jsonl_edited_file_rejected`

---

## I6. Blocking is a human decision

**Rule.** The library never performs the action. `COMMIT` and `ESCALATE` are
verdicts returned to the caller; `Drifting (Circuit Breaker Triggered)` and
`LEVY_FLIGHT` are signals. What happens next is the orchestrator's and the
human's choice.

**Prevents.** A gate that acts unilaterally and gets bypassed or disabled.

- [x] by construction: no component has side effects beyond the audit log
  (see `action_gate.py`, `emanon_orchestration_core.py`); add a test if a
  component ever gains an executor.

---

## I7. Thresholds are tunable defaults, not derived constants

**Rule.** `threshold=0.8`, `min_samples=3`, `drift_threshold`, `waste_weight=1.2`
and `waste_exponent=1.05` are parameters chosen by the operator. They are
validated at construction, documented as tunable, and never presented as
calibrated.

**Prevents.** A magic number being mistaken for a measured result.

- [x] validated: `test_action_gate.py::test_bad_configuration_rejected`,
  `test_emanon_core.py::test_threshold_optimizer_invalid_init_values`,
  `test_emanon_core.py::test_meta_controller_invalid_drift_threshold_raises`
- [x] stated in a comment beside each default in `action_gate.py` and
  `emanon_orchestration_core.py`

---

## I8. Agreement is not correctness

**Rule.** `ActionGate` reports how consistently an agent proposes an action,
not whether the action is right. An agent that is consistently wrong passes
this gate. The docstring, the README and this file all say so, and no output
of the gate may be labelled "verified" or "correct".

**Prevents.** Model consistency being sold as validation.

- [x] stated in `action_gate.py` module docstring and README "Scope and limits"
- [ ] roadmap item 1: an experiment on real agent tasks measuring whether
  disagreement predicts error, with results labelled **observed**

---

## I9. Examples are either observed or illustrative

**Rule.** Every code example in the README runs as written and produces the
output shown. Any walkthrough that was authored rather than executed is
labelled *illustrative*.

**Prevents.** A reviewer asking "did this actually happen?" and the answer being no.

- [x] `test_readme_examples.py::test_readme_block_runs` (every ```python block)
- [x] `test_readme_examples.py::test_readme_meta_controller_claims` (and the
  other `*_claims` tests check the values the README shows)
- [x] `test_readme_examples.py::test_worked_example_runs`
  (`example_agent_loop.py` is labelled illustrative in its own docstring)

---

## I10. Implemented and planned are never mixed

**Rule.** The README lists what runs today with its tests, and separately what
is planned. A feature without a passing test is "planned". Persistence, key
rotation, truncation detection and packaging are planned.

**Prevents.** Documentation theatre: a polished spec for code that does not exist.

- [x] README "Status and roadmap" keeps the two lists apart
- [x] `test_invariants.py::test_every_documented_component_has_a_test`
- [x] `test_invariants.py::test_every_cited_test_exists` (this file may not
  cite a test that does not exist)

---

## Working rule

Reviews of this repository, by a person or a model, should start from this file
and ask what breaks an invariant, not what reads well.
