"""Does disagreement at the ActionGate predict wrong actions?

This is the experiment the library exists to run. It is NOT part of the
library: nothing in here is imported by action_gate.py or the core.

Hypothesis: when an agent's N samples disagree about a tool call, the
committed action is more likely to be wrong than when they agree.
Null: agreement tells you nothing about correctness. If the null survives,
ActionGate is a cost without a benefit and the README should say so.

Ground truth comes from the Berkeley Function Calling Leaderboard (BFCL v4,
simple_python split, Apache-2.0): 400 prompts, each with one function schema
and the acceptable value(s) for every argument. That is the same
{"tool": ..., "args": ...} shape ActionGate judges, so we never grade ourselves.

Pipeline, per prompt:
  1. sample the agent N times  ->  N proposals
  2. run the proposals through ActionGate under several conditions
  3. if it committed, score the committed action against ground truth
  4. aggregate: wrong commits / commits (selective risk), coverage, and a
     one-sided 95% binomial upper bound on the wrong-commit rate.

Samplers are pluggable. `StubSampler` fabricates proposals with a controlled
error structure so the whole pipeline can be tested for free; `OpenAISampler`
calls a real model through any OpenAI-compatible endpoint, with every raw
response cached on disk so re-runs cost nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

from action_gate import COMMIT, ActionGate  # noqa: E402

DATA = HERE / "data"
CACHE = HERE / "cache"
RESULTS = HERE / "results"

Proposal = Dict[str, Any]
Sampler = Callable[["Case", int], List[Optional[Proposal]]]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Case:
    id: str
    prompt: str
    function: Dict[str, Any]          # the one schema offered to the agent
    truth: Dict[str, List[Any]]       # arg -> acceptable values ("" = may omit)

    @property
    def tool(self) -> str:
        return self.function["name"]


def load_cases(limit: Optional[int] = None, seed: int = 0) -> List[Case]:
    qs = [json.loads(l) for l in (DATA / "questions.jsonl").read_text().splitlines() if l.strip()]
    ans = {a["id"]: a for a in (json.loads(l) for l in (DATA / "answers.jsonl").read_text().splitlines() if l.strip())}
    cases = []
    for q in qs:
        gt = ans[q["id"]]["ground_truth"]
        assert len(q["question"]) == 1 and len(q["question"][0]) == 1, q["id"]
        assert len(q["function"]) == 1 and len(gt) == 1, q["id"]
        (name, args), = gt[0].items()
        assert name == q["function"][0]["name"], q["id"]
        cases.append(Case(q["id"], q["question"][0][0]["content"], q["function"][0], args))
    if limit is not None and limit < len(cases):
        rng = random.Random(seed)
        cases = rng.sample(cases, limit)
        cases.sort(key=lambda c: c.id)
    return cases


# ---------------------------------------------------------------------------
# Scoring against BFCL ground truth
# ---------------------------------------------------------------------------

def _norm(v: Any) -> Any:
    """Normalise so 10 == 10.0 and lists compare order-insensitively where BFCL does."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        return v.strip().lower()
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in sorted(v.items())}
    return v


def is_correct(action: Mapping[str, Any], case: Case) -> bool:
    """True iff the action names the right tool and every argument is acceptable.

    BFCL semantics: each argument lists acceptable values; "" among them
    means the argument may be omitted. An argument not in ground truth at
    all is wrong. A required argument that is missing (and "" not allowed)
    is wrong.
    """
    if action.get("tool") != case.tool:
        return False
    args = action.get("args", {})
    if not isinstance(args, Mapping):
        return False
    for k in args:
        if k not in case.truth:
            return False
    for k, allowed in case.truth.items():
        allowed_n = [_norm(a) for a in allowed]
        if k not in args:
            if "" in allowed:
                continue
            return False
        if _norm(args[k]) not in allowed_n:
            return False
    return True


# ---------------------------------------------------------------------------
# Samplers
# ---------------------------------------------------------------------------

def correct_action_for(case: Case, rng: random.Random) -> Proposal:
    """Build one acceptable action from ground truth (first allowed value, or omit)."""
    args = {}
    for k, allowed in case.truth.items():
        choice = allowed[0]
        if choice == "":
            continue
        args[k] = choice
    return {"tool": case.tool, "args": args}


class StubSampler:
    """Fabricates proposals with a controllable error structure. Free.

    p_wrong:       probability a prompt is one the agent gets wrong.
    p_correlated:  given the prompt is wrong, probability the error is the
                   SAME across all samples (the confidently-wrong case that
                   agreement cannot catch). Otherwise each wrong sample
                   makes its own independent mistake.
    p_noise:       per-sample probability of a scattered mistake even on
                   prompts the agent basically gets right.
    """

    name = "stub"

    def __init__(self, p_wrong=0.3, p_correlated=0.5, p_noise=0.1, seed=0):
        self.p_wrong, self.p_correlated, self.p_noise = p_wrong, p_correlated, p_noise
        self.rng = random.Random(seed)

    def _mutate(self, action: Proposal, case: Case, rng: random.Random) -> Proposal:
        a = json.loads(json.dumps(action))
        keys = list(case.truth.keys())
        mode = rng.choice(["value", "value", "drop", "extra", "tool"])
        if mode == "value" and a["args"]:
            k = rng.choice(list(a["args"].keys()))
            v = a["args"][k]
            a["args"][k] = (v + 1) if isinstance(v, (int, float)) and not isinstance(v, bool) else f"{v}_x"
        elif mode == "drop" and a["args"]:
            a["args"].pop(rng.choice(list(a["args"].keys())))
        elif mode == "extra":
            a["args"]["unexpected"] = 1
        elif mode == "tool":
            a["tool"] = case.tool + "_v2"
        else:
            a["args"]["unexpected"] = 1
        return a

    def __call__(self, case: Case, n: int) -> List[Optional[Proposal]]:
        rng = random.Random(f"{self.rng.random()}:{case.id}")
        correct = correct_action_for(case, rng)
        if rng.random() < self.p_wrong:
            if rng.random() < self.p_correlated:
                wrong = self._mutate(correct, case, rng)
                return [json.loads(json.dumps(wrong)) for _ in range(n)]
            return [self._mutate(correct, case, rng) for _ in range(n)]
        return [self._mutate(correct, case, rng) if rng.random() < self.p_noise else json.loads(json.dumps(correct))
                for _ in range(n)]


SYSTEM_PROMPT = (
    "You are a function-calling agent. You will be given one function schema and a user request. "
    "Respond with ONLY a JSON object of the form {\"tool\": <function name>, \"args\": {<argument>: <value>, ...}} "
    "and nothing else. Use the exact function name. Include only the arguments the request supports; "
    "omit optional arguments the request does not mention."
)


class OpenAISampler:
    """Samples a real model through any OpenAI-compatible chat endpoint.

    The API key is read from the environment (OPENAI_API_KEY) and is never
    written anywhere. Every raw response is cached under cache/<model>/ keyed
    by (case id, sample index, temperature), so re-running the analysis is
    free and the experiment is reproducible from the cache alone.
    """

    def __init__(self, model: str, temperature: float = 0.7, base_url: Optional[str] = None,
                 api_key_env: str = "OPENAI_API_KEY", sleep: float = 0.0):
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise SystemExit("pip install openai  (only needed for real-model runs)") from exc
        key = os.environ.get(api_key_env)
        if not key:
            raise SystemExit(f"set {api_key_env} in the environment; the key is never stored by this script")
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model, self.temperature, self.sleep = model, temperature, sleep
        self.name = model.replace("/", "_")
        self.cache_dir = CACHE / self.name
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, case: Case, i: int) -> Path:
        h = hashlib.sha256(f"{case.id}|{i}|{self.temperature}|{SYSTEM_PROMPT}".encode()).hexdigest()[:16]
        return self.cache_dir / f"{case.id}_{i}_{h}.json"

    def _raw(self, case: Case, i: int) -> str:
        p = self._cache_path(case, i)
        if p.exists():
            return json.loads(p.read_text())["content"]
        user = f"Function schema:\n{json.dumps(case.function, indent=1)}\n\nRequest: {case.prompt}"
        resp = self.client.chat.completions.create(
            model=self.model, temperature=self.temperature,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
        )
        content = resp.choices[0].message.content or ""
        p.write_text(json.dumps({"case": case.id, "i": i, "model": self.model,
                                 "temperature": self.temperature, "content": content}))
        if self.sleep:
            time.sleep(self.sleep)
        return content

    def __call__(self, case: Case, n: int) -> List[Optional[Proposal]]:
        return [parse_proposal(self._raw(case, i)) for i in range(n)]


def parse_proposal(text: str) -> Optional[Proposal]:
    """Parse a model reply into {"tool", "args"}; None if it is not one.

    Unparseable replies are kept as None so they count as disagreement at
    the gate (they cannot match anything), which is the honest treatment.
    """
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end < 0:
        return None
    try:
        obj = json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "tool" not in obj:
        return None
    args = obj.get("args", {})
    if not isinstance(args, dict):
        return None
    return {"tool": obj["tool"], "args": args}


# ---------------------------------------------------------------------------
# Conditions: what sits in front of the action
# ---------------------------------------------------------------------------

def schema_check(case: Case) -> Callable[[Dict[str, Any]], Optional[str]]:
    """A deterministic check any deployment could write: the action must name
    the offered tool, use only declared parameters, and supply every required
    one. Knows nothing about the ground truth."""
    props = case.function["parameters"].get("properties", {})
    required = set(case.function["parameters"].get("required", []))

    def check(action: Dict[str, Any]) -> Optional[str]:
        if action["tool"] != case.tool:
            return f"tool {action['tool']!r} is not the offered function {case.tool!r}"
        unknown = set(action["args"]) - set(props)
        if unknown:
            return f"undeclared parameter(s): {sorted(unknown)}"
        missing = required - set(action["args"])
        if missing:
            return f"missing required parameter(s): {sorted(missing)}"
        return None

    return check


@dataclass
class Condition:
    name: str
    threshold: float
    use_checks: bool

    def gate(self, case: Case) -> ActionGate:
        checks = [schema_check(case)] if self.use_checks else []
        return ActionGate(threshold=self.threshold, min_samples=2, checks=checks)


DEFAULT_CONDITIONS = [
    Condition("no_gate", 0.51, False),      # commit whatever the plurality says: the baseline an agent without ActionGate approximates
    Condition("gate_0.6", 0.6, False),
    Condition("gate_0.8", 0.8, False),
    Condition("gate_1.0", 1.0, False),
    Condition("gate_0.8+checks", 0.8, True),
    Condition("checks_only", 0.51, True),
]


# ---------------------------------------------------------------------------
# Running and scoring
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    case_id: str
    condition: str
    n_valid: int
    agreement: float
    committed: bool
    correct: Optional[bool]        # None when escalated
    reasons: List[str] = field(default_factory=list)


def run_case(case: Case, proposals: Sequence[Optional[Proposal]], conditions: Sequence[Condition]) -> List[CaseResult]:
    valid = [p for p in proposals if p is not None]
    # Anything unparseable still counts toward n: it is a sample that failed to
    # agree. ActionGate needs real proposals, so we pad disagreement by giving
    # each None a unique placeholder tool that can never win or pass a check.
    padded: List[Proposal] = list(valid) + [{"tool": f"__unparseable_{i}", "args": {}} for i, p in enumerate(proposals) if p is None]
    out = []
    for cond in conditions:
        if len(padded) < 2:
            out.append(CaseResult(case.id, cond.name, len(valid), 0.0, False, None, ["fewer than 2 samples"]))
            continue
        d = cond.gate(case).evaluate(padded, agent="bfcl_agent")
        if d.outcome == COMMIT and d.action["tool"].startswith("__unparseable_"):
            out.append(CaseResult(case.id, cond.name, len(valid), d.agreement, False, None, ["plurality was unparseable"]))
            continue
        committed = d.outcome == COMMIT
        out.append(CaseResult(case.id, cond.name, len(valid), d.agreement, committed,
                              is_correct(d.action, case) if committed else None, list(d.reasons)))
    return out


def binomial_upper_95(k: int, n: int) -> Optional[float]:
    """One-sided 95% Clopper-Pearson upper bound on a rate with k events in n trials."""
    if n == 0:
        return None
    if k >= n:
        return 1.0
    # Exact via bisection on the binomial CDF (no scipy dependency).
    def cdf(p: float) -> float:
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if cdf(mid) > 0.05:
            lo = mid
        else:
            hi = mid
    return hi


@dataclass
class Summary:
    condition: str
    n_cases: int
    commits: int
    wrong_commits: int
    coverage: float                # commits / cases
    selective_risk: Optional[float]  # wrong / commits
    risk_upper_95: Optional[float]
    wrong_per_case: float          # wrong / cases
    escalations: int
    # the diagnostic the hypothesis lives or dies on:
    wrong_rate_unanimous: Optional[float]      # among commits with agreement == 1.0
    wrong_rate_non_unanimous: Optional[float]  # among commits with agreement < 1.0


def summarise(results: Iterable[CaseResult]) -> List[Summary]:
    by: Dict[str, List[CaseResult]] = {}
    for r in results:
        by.setdefault(r.condition, []).append(r)
    out = []
    for cond, rs in by.items():
        commits = [r for r in rs if r.committed]
        wrong = [r for r in commits if r.correct is False]
        unan = [r for r in commits if r.agreement >= 1.0]
        non = [r for r in commits if r.agreement < 1.0]
        rate = lambda xs: (sum(1 for r in xs if r.correct is False) / len(xs)) if xs else None
        out.append(Summary(
            cond, len(rs), len(commits), len(wrong),
            len(commits) / len(rs) if rs else 0.0,
            len(wrong) / len(commits) if commits else None,
            binomial_upper_95(len(wrong), len(commits)),
            len(wrong) / len(rs) if rs else 0.0,
            len(rs) - len(commits),
            rate(unan), rate(non),
        ))
    return out


def agreement_vs_error(results: Iterable[CaseResult], condition: str = "no_gate") -> List[Dict[str, Any]]:
    """The core diagnostic, independent of any threshold: bucket every case
    by its agreement level and report how often the plurality action was wrong."""
    buckets: Dict[float, List[CaseResult]] = {}
    for r in results:
        if r.condition == condition and r.committed:
            buckets.setdefault(round(r.agreement, 2), []).append(r)
    rows = []
    for a in sorted(buckets):
        rs = buckets[a]
        w = sum(1 for r in rs if r.correct is False)
        rows.append({"agreement": a, "n": len(rs), "wrong": w, "wrong_rate": w / len(rs)})
    return rows


def format_table(summaries: Sequence[Summary]) -> str:
    def f(x, pct=True):
        if x is None:
            return "   -  "
        return f"{100 * x:5.1f}%" if pct else f"{x}"
    lines = [
        f"{'condition':<18}{'cases':>6}{'commits':>8}{'wrong':>6}{'coverage':>10}{'sel.risk':>10}{'risk≤95%':>10}{'wrong/case':>11}{'unan.wrong':>11}{'split.wrong':>12}",
        "-" * 102,
    ]
    for s in summaries:
        lines.append(
            f"{s.condition:<18}{s.n_cases:>6}{s.commits:>8}{s.wrong_commits:>6}"
            f"{f(s.coverage):>10}{f(s.selective_risk):>10}{f(s.risk_upper_95):>10}{f(s.wrong_per_case):>11}"
            f"{f(s.wrong_rate_unanimous):>11}{f(s.wrong_rate_non_unanimous):>12}"
        )
    return "\n".join(lines)


def format_agreement(rows: Sequence[Dict[str, Any]]) -> str:
    lines = [f"{'agreement':>10}{'n':>6}{'wrong':>7}{'wrong_rate':>12}", "-" * 35]
    for r in rows:
        lines.append(f"{r['agreement']:>10.2f}{r['n']:>6}{r['wrong']:>7}{100 * r['wrong_rate']:>11.1f}%")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sampler", choices=["stub", "openai"], default="stub")
    ap.add_argument("--model", default="gpt-4o-mini", help="model name for --sampler openai")
    ap.add_argument("--base-url", default=None, help="OpenAI-compatible endpoint (optional)")
    ap.add_argument("--n", type=int, default=5, help="samples per prompt")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--limit", type=int, default=None, help="use a random subset of this many prompts")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--stub-p-wrong", type=float, default=0.3)
    ap.add_argument("--stub-p-correlated", type=float, default=0.5)
    ap.add_argument("--stub-p-noise", type=float, default=0.1)
    ap.add_argument("--out", default=None, help="write per-case results (JSONL) and summary (JSON) under results/ with this stem")
    args = ap.parse_args(argv)

    cases = load_cases(args.limit, args.seed)
    if args.sampler == "stub":
        sampler: Sampler = StubSampler(args.stub_p_wrong, args.stub_p_correlated, args.stub_p_noise, args.seed)
        label = f"stub(p_wrong={args.stub_p_wrong}, p_corr={args.stub_p_correlated}, p_noise={args.stub_p_noise})"
    else:
        sampler = OpenAISampler(args.model, args.temperature, args.base_url)
        label = f"{args.model} @ T={args.temperature}"

    results: List[CaseResult] = []
    for i, case in enumerate(cases, 1):
        proposals = sampler(case, args.n)
        results.extend(run_case(case, proposals, DEFAULT_CONDITIONS))
        if args.sampler == "openai" and i % 25 == 0:
            print(f"  {i}/{len(cases)} prompts sampled", file=sys.stderr)

    summaries = summarise(results)
    rows = agreement_vs_error(results)

    print(f"\nActionGate × BFCL simple_python   sampler: {label}   prompts: {len(cases)}   samples/prompt: {args.n}\n")
    print(format_table(summaries))
    print("\nColumns: coverage = commits/cases; sel.risk = wrong/commits; risk≤95% = one-sided 95% upper bound on sel.risk;")
    print("unan.wrong / split.wrong = wrong rate among commits that were unanimous / not unanimous.\n")
    print("Agreement level vs. wrong rate of the plurality action (no_gate condition; this is the hypothesis test):\n")
    print(format_agreement(rows))
    print()
    if args.sampler == "stub":
        print("STUB DATA: these numbers test the pipeline, not the hypothesis. Nothing here is evidence about any model.")

    if args.out:
        RESULTS.mkdir(exist_ok=True)
        (RESULTS / f"{args.out}.cases.jsonl").write_text("\n".join(json.dumps(asdict(r)) for r in results) + "\n")
        (RESULTS / f"{args.out}.summary.json").write_text(json.dumps({
            "sampler": label, "n_samples": args.n, "prompts": len(cases), "seed": args.seed,
            "summaries": [asdict(s) for s in summaries], "agreement_vs_error": rows,
        }, indent=1))
        print(f"wrote results/{args.out}.cases.jsonl and results/{args.out}.summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
