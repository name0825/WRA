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


def test_scores_text_marks_kept_iteration_and_higher_reverted_one():
    iterations = [
        {**item("initial", 7.25, 1), "selected": False},
        {**item("redesign", 7.625, 2), "selected": True},
        {**item("redesign", 8.25, 3), "selected": False},
    ]
    assert aggregate.scores_text(iterations) == "i1=7.25, i2=7.625*, i3=8.25 (not kept)"


def test_scores_text_without_recorded_selection_is_unmarked():
    assert aggregate.scores_text([item("initial", 7.0, 1), item("refine", 8.0, 2)]) == "i1=7, i2=8"


def test_scores_text_compares_against_the_kept_iteration_of_the_same_page():
    iterations = [
        {**item("initial", 6.0, 1, page="a"), "selected": True},
        {**item("redesign", 6.5, 2, page="a"), "selected": False},
        {**item("initial", 8.0, 1, page="b"), "selected": True},
        {**item("redesign", 7.0, 2, page="b"), "selected": False},
    ]
    assert aggregate.scores_text(iterations) == "a:i1=6*, a:i2=6.5 (not kept), b:i1=8*, b:i2=7"
