"""I10: INVARIANTS.md may only cite tests that exist, and every component the
README calls implemented must have at least one test naming it."""

import re
from pathlib import Path

HERE = Path(__file__).parent
INVARIANTS = (HERE / "INVARIANTS.md").read_text(encoding="utf-8")
README = (HERE / "README.md").read_text(encoding="utf-8")


def _test_functions(path: Path):
    return set(re.findall(r"^def (test_\w+)\(", path.read_text(encoding="utf-8"), flags=re.M))


def test_every_cited_test_exists():
    cited = re.findall(r"`(test_\w+\.py)::(test_\w+)`", INVARIANTS)
    assert cited, "INVARIANTS.md cites no tests"
    missing = [f"{f}::{t}" for f, t in cited
               if not (HERE / f).exists() or t not in _test_functions(HERE / f)]
    assert not missing, f"INVARIANTS.md cites tests that do not exist: {missing}"


def test_every_enforced_invariant_cites_a_test_or_construction():
    # Each "[x]" line must name a test, or state that it holds by construction / by a stated doc.
    for line in INVARIANTS.splitlines():
        if line.startswith("- [x]"):
            assert re.search(r"test_\w+\.py::test_\w+|by construction|stated in|README", line), line


def test_every_documented_component_has_a_test():
    components = re.findall(r"^### `(\w+)`", README, flags=re.M)
    assert components, "README lists no components"
    all_tests = set()
    for f in HERE.glob("test_*.py"):
        all_tests |= _test_functions(f)
    slug = {
        "AuditLogger": "audit_logger",
        "ThresholdOptimizer": "threshold_optimizer",
        "EmanonMetaController": "meta_controller",
        "InteractionPolicy": "interaction_policy",
        "ActionGate": "",  # the whole of test_action_gate.py is about it
    }
    untested = []
    for name in components:
        key = slug.get(name, re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower())
        if key == "":
            ok = (HERE / "test_action_gate.py").exists()
        else:
            ok = any(key in t for t in all_tests)
        if not ok:
            untested.append(name)
    assert not untested, f"README documents components with no test: {untested}"
