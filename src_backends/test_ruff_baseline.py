"""scripts/ruff_baseline.py（ruff指摘のベースライン検査）のテスト"""
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ruff_baseline.py"
_spec = importlib.util.spec_from_file_location("ruff_baseline", _SCRIPT)
ruff_baseline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ruff_baseline)


class TestFindRegressions:
    def test_no_change_is_ok(self):
        base = {"a.py::F401": 2}
        assert ruff_baseline.find_regressions(dict(base), base) == []

    def test_increase_in_existing_file_detected(self):
        assert ruff_baseline.find_regressions({"a.py::F401": 3}, {"a.py::F401": 2}) == [("a.py::F401", 2, 3)]

    def test_new_file_or_new_rule_starts_from_zero(self):
        found = ruff_baseline.find_regressions({"new.py::E501": 1, "a.py::F841": 1}, {"a.py::F401": 2})
        assert sorted(found) == [("a.py::F841", 0, 1), ("new.py::E501", 0, 1)]

    def test_decrease_is_not_a_regression(self):
        assert ruff_baseline.find_regressions({"a.py::F401": 1}, {"a.py::F401": 2}) == []


class TestFindImprovements:
    def test_decrease_and_disappearance_reported(self):
        found = ruff_baseline.find_improvements({"a.py::F401": 1}, {"a.py::F401": 2, "b.py::E501": 1})
        assert sorted(found) == [("a.py::F401", 2, 1), ("b.py::E501", 1, 0)]


def test_committed_baseline_passes_for_current_tree():
    """コミット済みのベースラインに対し、現在のツリーが超過していないこと（CIのLintと同じ条件）"""
    import json
    baseline = json.loads(ruff_baseline.BASELINE_FILE.read_text(encoding="utf-8"))
    assert ruff_baseline.find_regressions(ruff_baseline.current_counts(), baseline) == []
