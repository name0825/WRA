import hashlib
import importlib.util
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "replay_eval_experiment.py"
if not SCRIPT.exists():
    pytest.skip("scripts/replay_eval_experiment.py is missing", allow_module_level=True)
spec = importlib.util.spec_from_file_location("replay_eval_experiment", SCRIPT)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


class DownClient:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        raise self.exc


class ModelNotFound(Exception):
    status_code = 404


def make_items(count=2):
    return [{"sample": "s", "bench_dir": "b", "run": f"r{i}", "iter": 1, "page": "index", "px": 1000 + i} for i in range(count)]


@pytest.fixture
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(replay.time, "sleep", sleeps.append)
    return sleeps


def test_wait_until_responsive_is_bounded(no_sleep):
    client = DownClient(ConnectionError("down"))
    assert replay.wait_until_responsive(client, "m") is None
    assert client.calls == replay.RECOVERY_MAX_PROBES
    assert len(no_sleep) == replay.RECOVERY_MAX_PROBES - 1


def test_wait_until_responsive_stops_at_permanent_failure(no_sleep):
    client = DownClient(ModelNotFound("model 'm' not found"))
    assert replay.wait_until_responsive(client, "m") is None
    assert client.calls == 1
    assert no_sleep == []


def test_instance_loss_404_is_not_permanent():
    assert not replay.is_permanent_failure(type("E", (Exception,), {"status_code": 404})("No instance found"))


def test_failing_trial_records_a_failure_row(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("bad response")

    monkeypatch.setattr(replay, "run_trial", boom)
    entry = replay.build_schedule(make_items(1))[0]
    row = replay.execute_trial(entry, None, None, None, None)
    assert row["trial"] == entry["trial"]
    assert row["condition"] == entry["condition"]
    assert row["eval"]["ok"] is False
    assert "bad response" in row["eval"]["error"]
    json.dumps(row)


SETTINGS = {"think": False, "content_freedom": "madmax", "temperature": 0.2, "seed": 7, "growth_power": 2.0, "base_url": "http://x", "seed_scheme": "trial_stride_1000", "conditions": {"A": [[32768, None]]}, "prompt_sha256:llm.md": "a", "prompt_sha256:visual_check.md": "b"}


def test_resume_with_identical_schedule_skips_done(tmp_path):
    schedule = replay.build_schedule(make_items(), "AB")
    assert replay.load_done(tmp_path, schedule, "m", False, SETTINGS) == set()
    (tmp_path / "results.jsonl").write_text(json.dumps({"trial": 1}) + "\n", encoding="utf-8")
    assert replay.load_done(tmp_path, schedule, "m", False, SETTINGS) == {1}


def test_resume_with_different_schedule_is_rejected(tmp_path):
    replay.load_done(tmp_path, replay.build_schedule(make_items(2), "AB"), "m", False, SETTINGS)
    (tmp_path / "results.jsonl").write_text(json.dumps({"trial": 1}) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        replay.load_done(tmp_path, replay.build_schedule(make_items(3), "AB"), "m", False, SETTINGS)
    with pytest.raises(SystemExit):
        replay.load_done(tmp_path, replay.build_schedule(make_items(2), "BA"), "m", False, SETTINGS)


def test_resume_with_different_model_or_visual_option_is_rejected(tmp_path):
    schedule = replay.build_schedule(make_items(2), "AB")
    replay.load_done(tmp_path, schedule, "m", False, SETTINGS)
    (tmp_path / "results.jsonl").write_text(json.dumps({"trial": 1}) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        replay.load_done(tmp_path, schedule, "other", False, SETTINGS)
    with pytest.raises(SystemExit):
        replay.load_done(tmp_path, schedule, "m", True, SETTINGS)
    assert replay.load_done(tmp_path, schedule, "m", False, SETTINGS) == {1}


@pytest.mark.parametrize("key,value", [("content_freedom", "low"), ("temperature", 0.9), ("seed", 8), ("think", True), ("growth_power", 3.0), ("base_url", "http://y"), ("seed_scheme", "counter"),("conditions", {"A": [[65536, None]]}), ("prompt_sha256:llm.md", "c"), ("prompt_sha256:visual_check.md", "c")])
def test_resume_with_different_evaluation_settings_is_rejected_naming_the_key(tmp_path, key, value):
    schedule = replay.build_schedule(make_items(2), "AB")
    replay.load_done(tmp_path, schedule, "m", False, SETTINGS)
    (tmp_path / "results.jsonl").write_text(json.dumps({"trial": 1}) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        replay.load_done(tmp_path, schedule, "m", False, {**SETTINGS, key: value})
    assert key in str(raised.value.code)


def test_resume_of_results_recorded_without_settings_is_rejected(tmp_path):
    schedule = replay.build_schedule(make_items(2), "AB")
    legacy = {key: value for key, value in replay.schedule_signature(schedule, "m", False, SETTINGS).items() if key != "settings"}
    (tmp_path / "schedule.json").write_text(json.dumps(legacy), encoding="utf-8")
    (tmp_path / "results.jsonl").write_text(json.dumps({"trial": 1}) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        replay.load_done(tmp_path, schedule, "m", False, SETTINGS)
    assert "fresh" in str(raised.value.code)


def test_evaluation_settings_come_from_config_and_model():
    config = type("C", (), {"content": type("Ct", (), {"freedom": "madmax"})(), "ollama": type("O", (), {"temperature": 0.3, "seed": "random", "base_url": "http://x"})(), "recovery": type("R", (), {"growth_power": 2.0})()})()
    model = type("M", (), {"name": "m", "think": True})()
    schedule = replay.build_schedule(make_items(2), "AD")
    settings = replay.evaluation_settings(config, model, schedule)
    assert {key: settings[key] for key in ("think", "content_freedom", "temperature", "seed", "growth_power", "base_url")} == {"think": True, "content_freedom": "madmax", "temperature": 0.3, "seed": "random", "growth_power": 2.0, "base_url": "http://x"}
    assert settings["conditions"] == {"A": [[32768, None]], "D": [[32768, 4000000]]}
    for name in ("llm.md", "visual_check.md"):
        assert settings[f"prompt_sha256:{name}"] == hashlib.sha256((ROOT / "prompts" / "evaluator" / name).read_bytes()).hexdigest()
    assert json.loads(json.dumps(settings)) == settings
    assert settings["seed_scheme"] == replay.SEED_SCHEME


class SeedRecorder:
    def __init__(self):
        self.seeds = []

    def generate(self, **kwargs):
        self.seeds.append(kwargs["options"].get("seed"))
        return SimpleNamespace(response="ok", done_reason="stop")


def seeded_client(seed, cls=None):
    client = (cls or replay.OllamaClient)("http://x", seed=seed)
    recorder = SeedRecorder()
    client._get_client = lambda timeout: recorder
    return client, recorder


def run_trials(client, recorder, trials, calls_per_trial=3, begin=True):
    seeds = {}
    for trial in trials:
        if begin:
            client.begin_trial(trial)
        recorder.seeds = []
        for _ in range(calls_per_trial):
            client.generate(prompt="p", model="m")
        seeds[trial] = list(recorder.seeds)
    return seeds


def test_plain_client_seeds_depend_on_how_many_trials_ran_in_the_process():
    full_client, full_recorder = seeded_client(7)
    resumed_client, resumed_recorder = seeded_client(7)
    full = run_trials(full_client, full_recorder, [1, 2], begin=False)
    resumed = run_trials(resumed_client, resumed_recorder, [2], begin=False)
    assert full[2] != resumed[2]
    assert resumed[2] == full[1]


def test_resumed_trial_gets_the_seeds_of_an_uninterrupted_run():
    full_client, full_recorder = seeded_client(7, replay.TrialSeededClient)
    resumed_client, resumed_recorder = seeded_client(7, replay.TrialSeededClient)
    full = run_trials(full_client, full_recorder, [1, 2, 3])
    resumed = run_trials(resumed_client, resumed_recorder, [3])
    assert resumed[3] == full[3]
    assert resumed[3] == [7 + 3 * replay.SEED_STRIDE + n for n in range(3)]


def test_trials_of_the_same_item_and_condition_never_share_seeds():
    client, recorder = seeded_client(7, replay.TrialSeededClient)
    seeds = run_trials(client, recorder, range(1, 7), calls_per_trial=replay.SEED_STRIDE)
    flat = [seed for trial in seeds.values() for seed in trial]
    assert len(flat) == len(set(flat))


def test_probe_and_wait_calls_before_the_first_trial_do_not_overlap_trial_one():
    client, recorder = seeded_client(7, replay.TrialSeededClient)
    probe = run_trials(client, recorder, [0], calls_per_trial=6)[0]
    first = run_trials(client, recorder, [1], calls_per_trial=1)[1]
    assert first[0] not in probe


def test_random_seed_runs_send_no_seed_option():
    client, recorder = seeded_client(None, replay.TrialSeededClient)
    seeds = run_trials(client, recorder, [1, 2])
    assert seeds == {1: [None] * 3, 2: [None] * 3}


def write_page(root, run, iteration, page):
    iteration_dir = root / "output" / run / "iterations" / f"iter{iteration}"
    data_dir = iteration_dir / "data" / page
    (data_dir / "browser").mkdir(parents=True)
    (root / "output" / run / "data" / page).mkdir(parents=True, exist_ok=True)
    (root / "output" / run / "data" / page / "features.json").write_text("{}", encoding="utf-8")
    (iteration_dir / f"{page}.html").write_text("<html></html>", encoding="utf-8")
    for name in ("design.json", "browser.json"):
        (data_dir / name).write_text("{}", encoding="utf-8")
    for name in replay.VIEWPORTS:
        (data_dir / "browser" / f"{name}.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 10, 10))


def test_discover_items_keeps_every_page_of_an_iteration(tmp_path, monkeypatch):
    log = tmp_path / "bench" / "quality_baseline_x"
    log.mkdir(parents=True)
    (log / "bench.log").write_text("reference-polished\texit=0\t1s\trun_a\n", encoding="utf-8")
    for page in ("index", "about"):
        write_page(tmp_path, "run_a", 1, page)
    monkeypatch.setattr(replay, "ROOT", tmp_path)
    items = replay.discover_items()
    assert sorted(item["page"] for item in items) == ["about", "index"]


def test_half_cache_does_not_collide_across_pages(tmp_path, monkeypatch):
    sizer = replay.HalfSizer(tmp_path / "half")
    monkeypatch.setattr(sizer, "_shoot", lambda source, target, width, height: target.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", width, height)))
    items = []
    for page in ("index", "about"):
        write_page(tmp_path, "run_a", 1, page)
        data_dir = tmp_path / "output" / "run_a" / "iterations" / "iter1" / "data" / page
        items.append({"run": "run_a", "iter": 1, "page": page, "data_dir": data_dir})
    assert sizer.half(items[0], "desktop") != sizer.half(items[1], "desktop")


def test_signature_mismatch_without_results_overwrites_the_schedule(tmp_path):
    schedule = replay.build_schedule(make_items(2), "AB")
    replay.load_done(tmp_path, schedule, "m", False, SETTINGS)
    assert replay.load_done(tmp_path, schedule, "other", True, SETTINGS) == set()
    assert json.loads((tmp_path / "schedule.json").read_text(encoding="utf-8")) == replay.schedule_signature(schedule, "other", True, SETTINGS)
    (tmp_path / "results.jsonl").write_text(json.dumps({"trial": 1}) + "\n", encoding="utf-8")
    assert replay.load_done(tmp_path, schedule, "other", True, SETTINGS) == {1}


def test_resume_without_persisted_schedule_is_rejected(tmp_path):
    (tmp_path / "results.jsonl").write_text(json.dumps({"trial": 1}) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        replay.load_done(tmp_path, replay.build_schedule(make_items(), "AB"), "m", False, SETTINGS)
