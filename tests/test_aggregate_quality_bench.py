import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("aggregate_quality_bench", ROOT / "scripts" / "aggregate_quality_bench.py")
aggregate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aggregate)


def item(path, score, iteration=1, page="index", refine_failed=None):
    return {"page": page, "iteration": iteration, "path": path, "overall_score": score, "refine_failed": refine_failed}


def test_path_summary_counts_means_and_refine_failures():
    runs = [{"run": "run_1", "iterations": [item("initial", 6.0), item("refine", 8.0, 2), item("redesign", 5.0, 3, refine_failed=True), item("unrecorded", 4.0, 4)]}]

    summary = aggregate.path_summary(runs)

    assert summary["initial"] == {"count": 1, "mean_overall": 6.0, "refine_failed_count": 0}
    assert summary["refine"]["mean_overall"] == 8.0
    assert summary["redesign"]["refine_failed_count"] == 1
    assert summary["unrecorded"]["count"] == 1


def test_path_summary_does_not_count_the_same_run_iteration_twice():
    runs = [{"run": "run_1", "iterations": [item("initial", 6.0)]}, {"run": "run_1", "iterations": [item("initial", 6.0)]}]

    assert aggregate.path_summary(runs)["initial"]["count"] == 1


def test_path_summary_is_per_mode_so_initial_results_are_not_merged():
    rewrite = [{"run": "run_a", "iterations": [item("initial", 6.0)]}]
    refine = [{"run": "run_b", "iterations": [item("initial", 8.0)]}]

    assert aggregate.path_summary(rewrite)["initial"]["mean_overall"] == 6.0
    assert aggregate.path_summary(refine)["initial"]["mean_overall"] == 8.0
