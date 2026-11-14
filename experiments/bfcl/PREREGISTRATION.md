# Pre-registration: ActionGate × BFCL, run 1

Frozen 2026-11-04 (Toronto), before any real-model sample was taken.
Committed to `main` so the timestamp is independent of the result.

## Fixed in advance

| item | value | why |
|---|---|---|
| dataset | BFCL v4 `simple_python`, all 400 prompts | the full split; no subset selection after the fact |
| model | `gemini-3.5-flash-lite` via the Gemini API's OpenAI-compatible endpoint (`https://generativelanguage.googleapis.com/v1beta/openai/`), free tier | small and cheap, so weak enough to make mistakes; a model that is never wrong cannot test the hypothesis. Chosen after the OpenAI account was unavailable; changed before any sample was taken |
| temperature | 0.7 | enough variation for samples to disagree; the stock default for sampling |
| samples per prompt (N) | 5 | the library's documented example; 4-of-5 is the 0.8 threshold |
| system prompt | `SYSTEM_PROMPT` in `harness.py` as merged with this file | unchanged after this freeze |
| conditions | the six in `DEFAULT_CONDITIONS` as merged with this file | fixed; no new thresholds added after seeing results |
| scorer | `is_correct` as merged with this file | fixed |
| seed | 0 | only affects stub runs and `--limit` subsetting; irrelevant to the full run |
| command | `python experiments/bfcl/harness.py --sampler openai --base-url https://generativelanguage.googleapis.com/v1beta/openai/ --api-key-env GEMINI_API_KEY --model gemini-3.5-flash-lite --temperature 0.7 --n 5 --sleep 6 --out run1_gemini35flashlite_n5` | exactly this. The free tier's daily cap means the 2,000 calls span about two days; the same command is re-run and resumes from the cache. `--sleep` only paces requests and does not affect the samples |

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

The full console output and `results/run1_gemini35flashlite_n5.summary.json` are
pasted into the results section below, verbatim, whichever way they come out.
The library README is then updated to state the outcome in one sentence.

## Results

Run completed 2026-11-14 (Toronto). 400 of 400 prompts, 2,000 samples, with
the frozen command. The free tier's daily cap spread the sampling over several
evenings; the cache carried it across runs. Nothing in the table above was
changed. The one code change during the run (PR #5) made the harness wait out
rate limits instead of crashing; it does not touch sampling, conditions, or
scoring.

### Verdict: inconclusive

Applying the decision rule as frozen:

| bucket | commits | wrong | wrong rate |
|---|---|---|---|
| agreement = 1.0 | 378 | 15 | 4.0% |
| agreement ≤ 0.6 | 8 | 2 | 25.0% |

The unanimous wrong rate (4.0%) is less than half the ≤0.6 wrong rate (25.0%),
and the 1.0 bucket has well over 100 commits, so the "supported" comparison
points the right way. But the ≤0.6 bucket has 8 commits, and the rule says the
result is inconclusive if any compared bucket has fewer than 20. So the result
is **inconclusive**, and it is not rounded up: the direction matches the
hypothesis, and the run was too small in the disagreement bucket to decide.

### Secondary read-outs (reported, not used to decide)

- The model rarely disagreed with itself: 378 of 400 prompts were unanimous.
- Unanimous-but-wrong under `no_gate`: 15 of the 21 wrong actions. No
  agreement threshold can catch these.
- Schema check: `checks_only` is identical to `no_gate`. It caught 0 of the 21.
- `gate_0.8` withheld 8 prompts and removed 2 wrong commits (selective risk
  5.2% → 4.8%). `gate_1.0` withheld 22 and removed 6 (5.2% → 4.0%).

### Exploratory, chosen after seeing the data (not a result)

Pooling both non-unanimous levels gives 6 wrong of 22 (27.3%) against 15 of
378 (4.0%); a one-sided Fisher exact test gives p ≈ 0.0004. This comparison
was not pre-registered, so it is a lead for a second run, not evidence. A
second run needs its own pre-registration, a setup that produces more
disagreement, and this pooled comparison declared in advance.

### Console output, verbatim

```
ActionGate × BFCL simple_python   sampler: gemini-3.5-flash-lite @ T=0.7   prompts: 400   samples/prompt: 5

condition          cases commits wrong  coverage  sel.risk  risk≤95% wrong/case unan.wrong split.wrong
------------------------------------------------------------------------------------------------------
no_gate              400     400    21    100.0%      5.2%      7.5%       5.2%       4.0%       27.3%
gate_0.6             400     400    21    100.0%      5.2%      7.5%       5.2%       4.0%       27.3%
gate_0.8             400     392    19     98.0%      4.8%      7.0%       4.8%       4.0%       28.6%
gate_1.0             400     378    15     94.5%      4.0%      6.0%       3.8%       4.0%         -  
gate_0.8+checks      400     392    19     98.0%      4.8%      7.0%       4.8%       4.0%       28.6%
checks_only          400     400    21    100.0%      5.2%      7.5%       5.2%       4.0%       27.3%

Columns: coverage = commits/cases; sel.risk = wrong/commits; risk≤95% = one-sided 95% upper bound on sel.risk;
unan.wrong / split.wrong = wrong rate among commits that were unanimous / not unanimous.

Agreement level vs. wrong rate of the plurality action (no_gate condition; this is the hypothesis test):

 agreement     n  wrong  wrong_rate
-----------------------------------
      0.60     8      2       25.0%
      0.80    14      4       28.6%
      1.00   378     15        4.0%

wrote results/run1_gemini35flashlite_n5.cases.jsonl and results/run1_gemini35flashlite_n5.summary.json
```

### `results/run1_gemini35flashlite_n5.summary.json`, verbatim

```json
{
 "sampler": "gemini-3.5-flash-lite @ T=0.7",
 "n_samples": 5,
 "prompts": 400,
 "seed": 0,
 "summaries": [
  {
   "condition": "no_gate",
   "n_cases": 400,
   "commits": 400,
   "wrong_commits": 21,
   "coverage": 1.0,
   "selective_risk": 0.0525,
   "risk_upper_95": 0.07472379735376607,
   "wrong_per_case": 0.0525,
   "escalations": 0,
   "wrong_rate_unanimous": 0.03968253968253968,
   "wrong_rate_non_unanimous": 0.2727272727272727
  },
  {
   "condition": "gate_0.6",
   "n_cases": 400,
   "commits": 400,
   "wrong_commits": 21,
   "coverage": 1.0,
   "selective_risk": 0.0525,
   "risk_upper_95": 0.07472379735376607,
   "wrong_per_case": 0.0525,
   "escalations": 0,
   "wrong_rate_unanimous": 0.03968253968253968,
   "wrong_rate_non_unanimous": 0.2727272727272727
  },
  {
   "condition": "gate_0.8",
   "n_cases": 400,
   "commits": 392,
   "wrong_commits": 19,
   "coverage": 0.98,
   "selective_risk": 0.04846938775510204,
   "risk_upper_95": 0.07031195756234404,
   "wrong_per_case": 0.0475,
   "escalations": 8,
   "wrong_rate_unanimous": 0.03968253968253968,
   "wrong_rate_non_unanimous": 0.2857142857142857
  },
  {
   "condition": "gate_1.0",
   "n_cases": 400,
   "commits": 378,
   "wrong_commits": 15,
   "coverage": 0.945,
   "selective_risk": 0.03968253968253968,
   "risk_upper_95": 0.06044772077754052,
   "wrong_per_case": 0.0375,
   "escalations": 22,
   "wrong_rate_unanimous": 0.03968253968253968,
   "wrong_rate_non_unanimous": null
  },
  {
   "condition": "gate_0.8+checks",
   "n_cases": 400,
   "commits": 392,
   "wrong_commits": 19,
   "coverage": 0.98,
   "selective_risk": 0.04846938775510204,
   "risk_upper_95": 0.07031195756234404,
   "wrong_per_case": 0.0475,
   "escalations": 8,
   "wrong_rate_unanimous": 0.03968253968253968,
   "wrong_rate_non_unanimous": 0.2857142857142857
  },
  {
   "condition": "checks_only",
   "n_cases": 400,
   "commits": 400,
   "wrong_commits": 21,
   "coverage": 1.0,
   "selective_risk": 0.0525,
   "risk_upper_95": 0.07472379735376607,
   "wrong_per_case": 0.0525,
   "escalations": 0,
   "wrong_rate_unanimous": 0.03968253968253968,
   "wrong_rate_non_unanimous": 0.2727272727272727
  }
 ],
 "agreement_vs_error": [
  {
   "agreement": 0.6,
   "n": 8,
   "wrong": 2,
   "wrong_rate": 0.25
  },
  {
   "agreement": 0.8,
   "n": 14,
   "wrong": 4,
   "wrong_rate": 0.2857142857142857
  },
  {
   "agreement": 1.0,
   "n": 378,
   "wrong": 15,
   "wrong_rate": 0.03968253968253968
  }
 ]
}
```
