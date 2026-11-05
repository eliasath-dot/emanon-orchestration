# Experiment: does disagreement at the gate predict wrong actions?

This folder is **not part of the library**. Nothing in `action_gate.py` or the
core imports it. It exists to answer the one question the library's README
leaves open, and to answer it in a way that can come out *against* ActionGate.

## The question, stated so it can fail

**Hypothesis.** When an agent's N samples disagree about a tool call, the
plurality action is more likely to be wrong than when they agree.

**Null.** Agreement level tells you nothing about correctness.

If the null survives, ActionGate is a cost (N model calls instead of one) with
no safety benefit, and the library's README should say so.

## Ground truth: borrowed, not self-graded

The **Berkeley Function Calling Leaderboard** (BFCL v4, `simple_python` split,
[Apache-2.0](data/LICENSE-BFCL)) supplies 400 prompts. Each comes with one
function schema and the acceptable value(s) for every argument — the same
`{"tool": ..., "args": ...}` shape ActionGate judges. Scoring is exact match
after light normalisation (`10 == 10.0`, case- and space-insensitive strings).
An optional argument may be omitted only where BFCL lists `""` as acceptable.

Using a public benchmark means we never grade our own labels. It also means
the result is about *this kind of task*: single-turn, single-function,
schema-known. Refunds and customer messages are a different distribution; see
Limits.

## Procedure

For each prompt:

1. Sample the agent N times (default 5) → N proposals. Unparseable replies are
   kept and count as disagreement; they can never commit.
2. Run the proposals through ActionGate under each condition below.
3. If it committed, score the committed action against ground truth.

Conditions (all use the same N samples, so the comparison is paired):

| condition | threshold | checks | what it tells you |
|---|---|---|---|
| `no_gate` | plurality (>0.5) | no | the baseline: act on whatever most samples said |
| `gate_0.6` / `gate_0.8` / `gate_1.0` | 0.6 / 0.8 / 1.0 | no | does raising the bar reduce wrong commits, and at what cost in coverage? |
| `checks_only` | plurality | yes | what a deterministic schema check catches on its own |
| `gate_0.8+checks` | 0.8 | yes | the configuration the library recommends |

The **schema check** is one any deployment could write without seeing ground
truth: the action must name the offered function, use only declared
parameters, and supply every required one. It knows nothing about which
*values* are right.

## Metrics

- **coverage** = commits / prompts. What share of work the gate lets through.
- **selective risk** = wrong commits / commits. The rate that matters: of the
  actions that *would have executed*, how many were wrong.
- **risk ≤ 95%** = one-sided 95% Clopper–Pearson upper bound on selective
  risk. Zero wrong in 300 commits gives ≈1.0%; a 0.1% claim needs ≈3,000.
- **wrong/case** = wrong commits / prompts. Harm per unit of work.
- **unan.wrong / split.wrong** = wrong rate among commits that were unanimous
  vs. not. If these are the same, agreement is not informative.
- **Agreement vs. wrong rate** (the table at the bottom): every prompt bucketed
  by agreement level, with the plurality action's wrong rate. **This is the
  hypothesis test.** A monotone decrease supports the hypothesis; a flat line
  is the null.

## Running it

Dry run with the stub agent (free, tests the pipeline, says nothing about any
model):

```bash
python experiments/bfcl/harness.py --sampler stub --n 5
```

The stub's error structure is controllable: `--stub-p-wrong` (share of prompts
the agent gets wrong), `--stub-p-correlated` (of those, share where every
sample makes the *same* mistake — the case agreement cannot catch), and
`--stub-p-noise` (scattered per-sample slips).

Real model, through any OpenAI-compatible endpoint:

```bash
pip install openai
export OPENAI_API_KEY=...            # read from the environment, never stored
python experiments/bfcl/harness.py --sampler openai --model gpt-4o-mini --n 5 --out gpt4omini_n5
```

Every raw model reply is cached under `cache/<model>/`, so re-running the
analysis is free and the whole result can be regenerated from the cache.
`--out` writes per-case results (JSONL) and the summary (JSON) under
`results/`. Both folders are git-ignored; commit a results file deliberately
when it is worth keeping.

Budget: 400 prompts × 5 samples = 2,000 short completions. With a small
model that is cents to a few dollars. Start with `--limit 50` to check the
plumbing before spending the rest.

## Freeze before you believe

Decide the model, temperature, N, prompt, and conditions **before** the final
run, and don't change them after seeing results. If you tune a threshold on
these 400 prompts, the reported risk for that threshold is optimistic; hold
out a split or use a second dataset (BFCL has `multiple`, `parallel`, and
`live` splits) for the number you quote.

## Limits, honestly

- One benchmark, one task family. Not refunds, not customer messages, not
  multi-step plans.
- Exact-match scoring is strict: a semantically fine answer phrased
  differently counts as wrong. That inflates *all* wrong rates equally, so
  comparisons between conditions survive it, but absolute numbers are
  pessimistic.
- 400 prompts bounds selective risk at roughly ≥1% even with zero errors.
  This can show whether agreement is informative; it cannot certify a
  production error rate.
- The schema check catches *shape* errors. Value errors (right parameter,
  wrong number) are exactly what it cannot see, and what the hypothesis is
  about.

## Testing the instrument

```bash
python -m pytest -q experiments/bfcl
```

These tests cover the scorer (every BFCL ground truth scores correct against
itself), the parser, the schema check, the pipeline's handling of unanimity,
splits and unparseable output, the binomial bound, and the stub's promised
error structure. They test the measuring instrument, not the hypothesis.
