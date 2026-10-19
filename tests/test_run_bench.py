import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_bench.py"
if not SCRIPT.exists():
    pytest.skip("scripts/run_bench.py is local-only (gitignored)", allow_module_level=True)
spec = importlib.util.spec_from_file_location("run_bench", SCRIPT)
run_bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_bench)


class FakeClient:
    def __init__(self, failures=0):
        self.failures = failures
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            raise ConnectionError("down")


def write_models(path, enabled):
    path.write_text(
        f'ollama:\n  base_url: "http://x"\nmodels:\n  single:\n    enabled: {enabled}\n    name: "m"\n',
        encoding="utf-8",
    )


def test_wait_for_model_retries_until_probe_succeeds():
    client = FakeClient(failures=2)
    received = {}

    def factory(**kwargs):
        received.update(kwargs)
        return client

    sleeps = []
    failed, waited = run_bench.wait_for_model("http://x", "m", client_factory=factory, sleep=sleeps.append)

    assert failed == 2
    assert waited >= 0
    assert received == {"host": "http://x", "timeout": run_bench.PROBE_TIMEOUT}
    assert sleeps == [run_bench.PROBE_INTERVAL] * 2
    assert len(client.calls) == 3
    for call in client.calls:
        assert call == {"model": "m", "prompt": "Reply with the single word: pong", "options": {"num_predict": 8}}


def test_wait_for_model_returns_at_once_when_probe_succeeds():
    client = FakeClient()
    sleeps = []

    failed, _ = run_bench.wait_for_model("http://x", "m", client_factory=lambda **kwargs: client, sleep=sleeps.append)

    assert failed == 0
    assert sleeps == []
    assert len(client.calls) == 1


def test_probe_target_follows_single_enabled_flag(tmp_path, monkeypatch):
    models = tmp_path / "models.yaml"
    monkeypatch.setattr(run_bench, "MODELS", models)

    write_models(models, "true")
    assert run_bench.probe_target() == ("http://x", "m")

    write_models(models, "false")
    assert run_bench.probe_target() is None


class ErrorClient:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        raise self.error


class StatusError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.parametrize(
    "error",
    [
        StatusError("unauthorized", 401),
        StatusError("forbidden", 403),
        StatusError('model "probe-model" not found, try pulling it first', 404),
        StatusError("model 'probe-model' not found", 404),
    ],
)
def test_wait_for_model_fails_fast_on_permanent_errors(error):
    client = ErrorClient(error)
    sleeps = []

    with pytest.raises(SystemExit) as raised:
        run_bench.wait_for_model("http://x", "probe-model", client_factory=lambda **kwargs: client, sleep=sleeps.append)

    assert raised.value.code not in (0, None)
    assert "probe-model" in str(raised.value.code)
    assert client.calls == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "error",
    [StatusError("No instance found for the request", 404), StatusError("No instance found for model x: route not found", 404), StatusError("not found", 404), StatusError("bad gateway", 502), ConnectionError("down")],
)
def test_wait_for_model_keeps_retrying_transient_errors(error):
    class FlakyClient(FakeClient):
        def generate(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) <= 2:
                raise error

    client = FlakyClient()
    sleeps = []

    failed, _ = run_bench.wait_for_model("http://x", "m", client_factory=lambda **kwargs: client, sleep=sleeps.append)

    assert failed == 2
    assert len(sleeps) == 2


def test_wait_for_model_gives_up_after_the_overall_timeout():
    client = ErrorClient(ConnectionError("down"))
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    with pytest.raises(SystemExit) as raised:
        run_bench.wait_for_model("http://x", "m", client_factory=lambda **kwargs: client, sleep=sleep, clock=lambda: now[0])

    assert raised.value.code not in (0, None)
    assert now[0] >= run_bench.PROBE_MAX_WAIT
    assert client.calls <= run_bench.PROBE_MAX_WAIT / run_bench.PROBE_INTERVAL + 2
