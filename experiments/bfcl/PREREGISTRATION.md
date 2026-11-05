# Pre-registration: ActionGate × BFCL, run 1

Frozen 2026-11-04 (Toronto), before any real-model sample was taken.
Committed to `main` so the timestamp is independent of the result.

## Fixed in advance

| item | value | why |
|---|---|---|
| dataset | BFCL v4 `simple_python`, all 400 prompts | the full split; no subset selection after the fact |
| model | `gpt-4o-mini` via OpenAI | cheap, widely available, and weak enough to make mistakes; a model that is never wrong cannot test the hypothesis |
| temperature | 0.7 | enough variation for samples to disagree; the stock default for sampling |
| samples per prompt (N) | 5 | the library's documented example; 4-of-5 is the 0.8 threshold |
| system prompt | `SYSTEM_PROMPT` in `harness.py` at commit `087285a` | unchanged after this freeze |
| conditions | the six in `DEFAULT_CONDITIONS` at that commit | fixed; no new thresholds added after seeing results |
| scorer | `is_correct` at that commit | fixed |
| seed | 0 | only affects stub runs and `--limit` subsetting; irrelevant to the full run |
| command | `python experiments/bfcl/harness.py --sampler openai --model gpt-4o-mini --temperature 0.7 --n 5 --out run1_gpt4omini_n5` | exactly this |

## The question and what counts as an answer

**Hypothesis.** Among prompts where the plurality action was committed under
`no_gate`, the wrong rate decreases as the agreement level rises.

**Primary read-out.** The "agreement level vs. wrong rate" table. We will
compare the wrong rate at agreement = 1.0 (unanimous) against the wrong rate
at agreement ≤ 0.6 (closest to a split).

**Decision rule, fixed now:**

- **Supported** if the unanimous wrong rate is less than half the ≤0.6 wrong
  rate, and the 1.0 bucket has at least 100 commits.
- **Not supported** if the unanimous wrong rate is at least 80% of the ≤0.6
  wrong rate (agreement is not usefully informative), regardless of what
  thresholds do to coverage.
- **Inconclusive** otherwise, or if any bucket we compare has fewer than 20
  commits. An inconclusive result is reported as such, not rounded up.

**Secondary read-outs** (reported, not used to decide):
- selective risk and coverage for each condition;
- the unanimous-but-wrong count under `no_gate`: how many confidently wrong
  actions exist that no threshold could catch;
- how many of those the schema check catches (`checks_only` vs `no_gate`).

## What we will not do

- Change the model, temperature, N, prompt, conditions, or scorer after
  seeing results. If something is genuinely broken (e.g. the model returns
  unparseable output on most prompts), the run is voided and a new
  pre-registration is written; it is not patched mid-run.
- Pick a threshold based on these 400 prompts and then quote its risk from
  the same 400. Any threshold chosen here is quoted only after a fresh run on
  a different split.
- Present the result as a production error rate. 400 prompts bounds
  selective risk at about 1% at best; this run can say whether agreement is
  informative, not certify a deployment.

## Reporting

The full console output and `results/run1_gpt4omini_n5.summary.json` are
pasted into the results section below, verbatim, whichever way they come out.
The library README is then updated to state the outcome in one sentence.

## Results

_(empty until the run is complete)_
