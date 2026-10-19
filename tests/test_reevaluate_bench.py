import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reevaluate_bench.py"
if not SCRIPT.exists():
    pytest.skip("scripts/reevaluate_bench.py is missing", allow_module_level=True)
spec = importlib.util.spec_from_file_location("reevaluate_bench", SCRIPT)
reevaluate_bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reevaluate_bench)


def evaluation(overall):
    data = {"overall_score": overall}
    for dim in reevaluate_bench.EVALUATION_DIMENSIONS:
        data[dim] = {"score": overall}
    return data


def item(run_name, page, iteration=1, sample="s"):
    return {"sample": sample, "run_name": run_name, "page": page, "iteration": iteration, "html": "<p>x</p>", "old_evaluation": evaluation(1.0)}


def save(path, overall):
    path.write_text(json.dumps({"evaluation": evaluation(overall), "inspection_findings": []}), encoding="utf-8")


def test_result_paths_are_distinct_per_run_and_page(tmp_path):
    items = [item("r1", "index"), item("r1", "about"), item("r2", "index"), item("r2", "about")]
    paths = {reevaluate_bench.result_path(tmp_path, entry) for entry in items}
    assert len(paths) == 4


def test_table_aggregates_each_page_own_result(tmp_path):
    first, second = item("r1", "index"), item("r1", "about")
    save(reevaluate_bench.result_path(tmp_path, first), 2.0)
    save(reevaluate_bench.result_path(tmp_path, second), 4.0)
    lines = reevaluate_bench.build_table(tmp_path, [first, second], tmp_path)
    assert len(lines) == 4
    assert lines[1].split("\t")[:6] == ["s", "r1", "index", "1", "1", "2"]
    assert lines[2].split("\t")[:6] == ["s", "r1", "about", "1", "1", "4"]
    assert lines[3].split("\t")[:6] == ["MEAN", "", "", "", "1.000", "3.000"]


def test_old_format_result_is_not_picked_up(tmp_path):
    save(tmp_path / "s_i1.json", 3.0)
    entry = item("r1", "index")
    assert not reevaluate_bench.result_path(tmp_path, entry).exists()
    lines = reevaluate_bench.build_table(tmp_path, [entry], tmp_path)
    assert len(lines) == 1


def test_resume_is_refused_when_fingerprint_differs(tmp_path):
    fingerprint = {"evaluator_model": "m", "content_freedom": "low"}
    reevaluate_bench.check_fingerprint(tmp_path, fingerprint, False)
    save(tmp_path / "s_r1_index_i1.json", 2.0)
    with pytest.raises(SystemExit) as exc:
        reevaluate_bench.check_fingerprint(tmp_path, {**fingerprint, "content_freedom": "high"}, False)
    assert exc.value.code != 0
    reevaluate_bench.check_fingerprint(tmp_path, {**fingerprint, "content_freedom": "high"}, True)
    assert json.loads((tmp_path / "fingerprint.json").read_text(encoding="utf-8"))["content_freedom"] == "high"


def test_resume_proceeds_when_fingerprint_is_identical(tmp_path):
    fingerprint = {"evaluator_model": "m", "content_freedom": "low"}
    reevaluate_bench.check_fingerprint(tmp_path, fingerprint, False)
    save(tmp_path / "s_r1_index_i1.json", 2.0)
    reevaluate_bench.check_fingerprint(tmp_path, dict(fingerprint), False)


def test_existing_results_without_fingerprint_are_refused(tmp_path):
    save(tmp_path / "s_r1_index_i1.json", 2.0)
    with pytest.raises(SystemExit):
        reevaluate_bench.check_fingerprint(tmp_path, {"evaluator_model": "m"}, False)


def test_fingerprint_changes_with_prompt_and_settings():
    from support import sample_config_data
    from wra.core.models import WRAConfig

    config = WRAConfig.model_validate(sample_config_data())
    model = SimpleNamespace(name="m", think=None, timeout=None)
    base = reevaluate_bench.build_fingerprint(config, model)
    assert set(base["prompts"]) == {"llm.md", "visual_check.md"}
    assert "single_num_ctx" not in base
    for field, value in (("name", "other"), ("think", True), ("timeout", 99.0)):
        assert reevaluate_bench.build_fingerprint(config, SimpleNamespace(**{**vars(model), field: value})) != base
    changed = config.model_copy(update={"browser": config.browser.model_copy(update={"evaluation_max_pixels": 12345})})
    assert reevaluate_bench.build_fingerprint(changed, model) != base
    for field, value in (("temperature", 0.123), ("seed", 987654)):
        changed = config.model_copy(update={"ollama": config.ollama.model_copy(update={field: value})})
        assert reevaluate_bench.build_fingerprint(changed, model) != base


def fake_git(monkeypatch, tmp_path, diff=b"", untracked=()):
    outputs = {"rev-parse": b"a" * 40 + b"\n", "diff": diff, "ls-files": "".join(f"{name}\n" for name in untracked).encode()}
    monkeypatch.setattr(reevaluate_bench, "ROOT", tmp_path)
    monkeypatch.setattr(reevaluate_bench, "git_output", lambda *args: outputs[args[0]])


def test_code_version_reports_commit_and_dirty_hash_or_none_without_git(monkeypatch):
    version = reevaluate_bench.code_version()
    assert version is None or (len(version["commit"]) == 40 and set(version) == {"commit", "dirty_hash"})

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(reevaluate_bench.subprocess, "run", no_git)
    assert reevaluate_bench.code_version() is None


def test_code_version_is_none_when_git_times_out(monkeypatch):
    def slow_git(*args, **kwargs):
        assert kwargs["timeout"] == 10
        raise reevaluate_bench.subprocess.TimeoutExpired("git", 10)

    monkeypatch.setattr(reevaluate_bench.subprocess, "run", slow_git)
    assert reevaluate_bench.code_version() is None


def test_dirty_hash_distinguishes_different_uncommitted_edits(monkeypatch, tmp_path):
    fake_git(monkeypatch, tmp_path)
    assert reevaluate_bench.code_version()["dirty_hash"] is None
    fake_git(monkeypatch, tmp_path, diff=b"edit one")
    first = reevaluate_bench.code_version()["dirty_hash"]
    fake_git(monkeypatch, tmp_path, diff=b"edit two")
    second = reevaluate_bench.code_version()["dirty_hash"]
    assert first and second and first != second


def test_dirty_hash_covers_untracked_file_content(monkeypatch, tmp_path):
    (tmp_path / "new.py").write_text("one", encoding="utf-8")
    fake_git(monkeypatch, tmp_path, untracked=["new.py"])
    first = reevaluate_bench.code_version()["dirty_hash"]
    (tmp_path / "new.py").write_text("two", encoding="utf-8")
    assert reevaluate_bench.code_version()["dirty_hash"] != first


def test_forced_resume_is_recorded_and_refused_without_the_flag(tmp_path):
    fingerprint = {"evaluator_model": "m", "content_freedom": "low"}
    reevaluate_bench.check_fingerprint(tmp_path, fingerprint, False)
    save(tmp_path / "s_r1_index_i1.json", 2.0)
    changed = {**fingerprint, "content_freedom": "high"}
    reevaluate_bench.check_fingerprint(tmp_path, changed, True)
    assert json.loads((tmp_path / "fingerprint.json").read_text(encoding="utf-8"))["forced"] is True
    with pytest.raises(SystemExit) as exc:
        reevaluate_bench.check_fingerprint(tmp_path, changed, False)
    assert "force-resumed" in str(exc.value)
    reevaluate_bench.check_fingerprint(tmp_path, changed, True)


def test_fingerprint_is_written_atomically(tmp_path):
    reevaluate_bench.check_fingerprint(tmp_path, {"evaluator_model": "m"}, False)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["fingerprint.json"]
