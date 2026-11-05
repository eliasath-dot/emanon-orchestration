"""Tests for the BFCL experiment harness. These test the measuring
instrument, not the hypothesis. Run: python -m pytest -q experiments/bfcl"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    Case, Condition, StubSampler, agreement_vs_error, binomial_upper_95,
    correct_action_for, is_correct, load_cases, parse_proposal, run_case,
    schema_check, summarise,
)
import random  # noqa: E402

CASE = Case(
    id="t0",
    prompt="area of a triangle base 10 height 5",
    function={"name": "calculate_triangle_area",
              "parameters": {"type": "dict",
                             "properties": {"base": {"type": "integer"}, "height": {"type": "integer"},
                                            "unit": {"type": "string"}},
                             "required": ["base", "height"]}},
    truth={"base": [10], "height": [5], "unit": ["units", ""]},
)


# --- ground-truth scoring ----------------------------------------------------

def test_exact_match_is_correct():
    assert is_correct({"tool": "calculate_triangle_area", "args": {"base": 10, "height": 5}}, CASE)


def test_optional_arg_may_be_omitted_or_given_an_allowed_value():
    assert is_correct({"tool": "calculate_triangle_area", "args": {"base": 10, "height": 5, "unit": "units"}}, CASE)
    assert not is_correct({"tool": "calculate_triangle_area", "args": {"base": 10, "height": 5, "unit": "metres"}}, CASE)


def test_int_and_float_are_equivalent():
    assert is_correct({"tool": "calculate_triangle_area", "args": {"base": 10.0, "height": 5}}, CASE)


def test_wrong_value_wrong_tool_extra_arg_missing_required_are_all_wrong():
    assert not is_correct({"tool": "calculate_triangle_area", "args": {"base": 11, "height": 5}}, CASE)
    assert not is_correct({"tool": "other", "args": {"base": 10, "height": 5}}, CASE)
    assert not is_correct({"tool": "calculate_triangle_area", "args": {"base": 10, "height": 5, "x": 1}}, CASE)
    assert not is_correct({"tool": "calculate_triangle_area", "args": {"base": 10}}, CASE)


def test_string_match_is_case_and_space_insensitive():
    c = Case("t1", "", {"name": "f", "parameters": {"properties": {"city": {}}, "required": ["city"]}}, {"city": ["San Francisco"]})
    assert is_correct({"tool": "f", "args": {"city": " san francisco "}}, c)


# --- the real dataset -------------------------------------------------------

def test_dataset_loads_and_every_ground_truth_scores_correct():
    cases = load_cases()
    assert len(cases) == 400
    rng = random.Random(0)
    for c in cases:
        assert is_correct(correct_action_for(c, rng), c), c.id


def test_limit_gives_a_deterministic_subset():
    a = [c.id for c in load_cases(limit=20, seed=1)]
    b = [c.id for c in load_cases(limit=20, seed=1)]
    assert a == b and len(a) == 20


# --- parsing model output ----------------------------------------------------

def test_parse_plain_and_fenced_json():
    assert parse_proposal('{"tool": "f", "args": {"a": 1}}') == {"tool": "f", "args": {"a": 1}}
    assert parse_proposal('```json\n{"tool": "f", "args": {}}\n```') == {"tool": "f", "args": {}}
    assert parse_proposal('Sure! {"tool": "f", "args": {}} hope that helps') == {"tool": "f", "args": {}}


def test_unparseable_is_none():
    assert parse_proposal("I cannot do that") is None
    assert parse_proposal('{"name": "f"}') is None
    assert parse_proposal('{"tool": "f", "args": [1]}') is None


# --- schema check ------------------------------------------------------------

def test_schema_check_blocks_wrong_tool_unknown_and_missing_params():
    chk = schema_check(CASE)
    assert chk({"tool": "calculate_triangle_area", "args": {"base": 10, "height": 5}}) is None
    assert "not the offered function" in chk({"tool": "x", "args": {}})
    assert "undeclared" in chk({"tool": "calculate_triangle_area", "args": {"base": 10, "height": 5, "zzz": 1}})
    assert "missing required" in chk({"tool": "calculate_triangle_area", "args": {"base": 10}})


def test_schema_check_does_not_know_ground_truth():
    # Right shape, wrong value: the check must pass it. Only the gate's
    # disagreement or the scorer can catch this.
    assert schema_check(CASE)({"tool": "calculate_triangle_area", "args": {"base": 999, "height": 5}}) is None


# --- running a case ----------------------------------------------------------

def test_unanimous_correct_commits_and_scores_correct():
    good = {"tool": "calculate_triangle_area", "args": {"base": 10, "height": 5}}
    rs = run_case(CASE, [good] * 5, [Condition("g", 0.8, False)])
    assert rs[0].committed and rs[0].correct is True and rs[0].agreement == 1.0


def test_unanimous_wrong_commits_and_scores_wrong():
    bad = {"tool": "calculate_triangle_area", "args": {"base": 99, "height": 5}}
    rs = run_case(CASE, [bad] * 5, [Condition("g", 0.8, False)])
    assert rs[0].committed and rs[0].correct is False


def test_split_escalates_and_correct_is_none():
    a = {"tool": "calculate_triangle_area", "args": {"base": 10, "height": 5}}
    b = {"tool": "calculate_triangle_area", "args": {"base": 11, "height": 5}}
    rs = run_case(CASE, [a, a, a, b, b], [Condition("g", 0.8, False)])
    assert not rs[0].committed and rs[0].correct is None and math.isclose(rs[0].agreement, 0.6)


def test_unparseable_samples_count_as_disagreement():
    good = {"tool": "calculate_triangle_area", "args": {"base": 10, "height": 5}}
    rs = run_case(CASE, [good, good, good, None, None], [Condition("g", 0.8, False)])
    assert not rs[0].committed and math.isclose(rs[0].agreement, 0.6) and rs[0].n_valid == 3


def test_all_unparseable_never_commits():
    rs = run_case(CASE, [None] * 5, [Condition("g", 0.51, False)])
    assert not rs[0].committed and rs[0].correct is None


def test_checks_condition_blocks_unanimous_schema_violation():
    bad = {"tool": "calculate_triangle_area", "args": {"base": 10, "height": 5, "bogus": 1}}
    plain, checked = run_case(CASE, [bad] * 5, [Condition("p", 0.8, False), Condition("c", 0.8, True)])
    assert plain.committed and plain.correct is False
    assert not checked.committed and any("undeclared" in r for r in checked.reasons)


# --- statistics --------------------------------------------------------------

def test_binomial_upper_bound_known_values():
    # Rule of three: 0 of n gives ~3/n.
    assert math.isclose(binomial_upper_95(0, 300), 1 - 0.05 ** (1 / 300), rel_tol=1e-6)
    assert 0.0099 < binomial_upper_95(0, 300) < 0.0100
    assert binomial_upper_95(5, 5) == 1.0
    assert binomial_upper_95(0, 0) is None
    b = binomial_upper_95(10, 100)
    assert 0.10 < b < 0.17


def test_summary_arithmetic():
    good = {"tool": "calculate_triangle_area", "args": {"base": 10, "height": 5}}
    bad = {"tool": "calculate_triangle_area", "args": {"base": 99, "height": 5}}
    cond = [Condition("g", 0.8, False)]
    rs = run_case(CASE, [good] * 5, cond) + run_case(CASE, [bad] * 5, cond) + run_case(CASE, [good, good, good, bad, bad], cond)
    s, = summarise(rs)
    assert s.n_cases == 3 and s.commits == 2 and s.wrong_commits == 1 and s.escalations == 1
    assert math.isclose(s.coverage, 2 / 3) and math.isclose(s.selective_risk, 0.5)
    assert s.wrong_rate_unanimous == 0.5 and s.wrong_rate_non_unanimous is None


def test_agreement_buckets():
    good = {"tool": "calculate_triangle_area", "args": {"base": 10, "height": 5}}
    bad = {"tool": "calculate_triangle_area", "args": {"base": 99, "height": 5}}
    cond = [Condition("no_gate", 0.51, False)]
    rs = run_case(CASE, [good] * 5, cond) + run_case(CASE, [bad, bad, bad, good, good], cond)
    rows = agreement_vs_error(rs)
    assert [r["agreement"] for r in rows] == [0.6, 1.0]
    assert rows[0]["wrong"] == 1 and rows[1]["wrong"] == 0


# --- stub sampler --------------------------------------------------------------

def test_stub_is_deterministic_and_has_the_promised_structure():
    s1, s2 = StubSampler(seed=3), StubSampler(seed=3)
    assert s1(CASE, 5) == s2(CASE, 5)
    always_right = StubSampler(p_wrong=0.0, p_noise=0.0)
    assert all(is_correct(p, CASE) for p in always_right(CASE, 5))
    always_correlated = StubSampler(p_wrong=1.0, p_correlated=1.0, seed=1)
    ps = always_correlated(CASE, 5)
    assert all(p == ps[0] for p in ps) and not is_correct(ps[0], CASE)
