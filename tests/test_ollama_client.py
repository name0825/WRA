import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from ollama import ResponseError
from pydantic import ValidationError

from wra.cli import _resolve_seed
from wra.core.models import OllamaConfig
from wra.llm import ollama as ollama_module
from wra.llm.ollama import LLMTransportError, OllamaClient


class FakeUnderlyingClient:
    def __init__(self, host=None, timeout=None) -> None:
        self.host = host
        self.timeout = timeout
        self.calls: list[dict] = []

    def generate(self, **kwargs) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(response="ok", done=True, done_reason="stop")


def test_generate_forwards_format_as_top_level_kwarg(monkeypatch) -> None:
    monkeypatch.setattr(ollama_module, "Client", FakeUnderlyingClient)
    client = OllamaClient(base_url="http://localhost:11434")

    client.generate(prompt="hi", model="test-model", options={"num_ctx": 4096}, format="json")

    fake = client._get_client(None)
    call = fake.calls[0]
    assert call["format"] == "json"
    assert "format" not in call["options"]
    assert call["options"] == {"num_ctx": 4096}


def test_generate_omits_format_kwarg_when_none(monkeypatch) -> None:
    monkeypatch.setattr(ollama_module, "Client", FakeUnderlyingClient)
    client = OllamaClient(base_url="http://localhost:11434")

    client.generate(prompt="hi", model="test-model")

    fake = client._get_client(None)
    call = fake.calls[0]
    assert "format" not in call


class FakeMarkerClient(FakeUnderlyingClient):
    def generate(self, **kwargs) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(response="HRI, 202<0xC2>7", done=True, done_reason="stop")


def test_generate_strips_byte_fallback_markers(monkeypatch, caplog) -> None:
    monkeypatch.setattr(ollama_module, "Client", FakeMarkerClient)
    client = OllamaClient(base_url="http://localhost:11434")

    with caplog.at_level("WARNING"):
        result = client.generate(prompt="hi", model="test-model")

    assert result == "HRI, 2027"
    assert "byte-fallback" in caplog.text
    assert "test-model" in caplog.text


def test_generate_sends_no_sampling_options_by_default(monkeypatch) -> None:
    monkeypatch.setattr(ollama_module, "Client", FakeUnderlyingClient)
    client = OllamaClient(base_url="http://localhost:11434")

    client.generate(prompt="hi", model="test-model")

    fake = client._get_client(None)
    assert fake.calls[0]["options"] == {}


def test_generate_merges_temperature_and_seed_into_options(monkeypatch) -> None:
    monkeypatch.setattr(ollama_module, "Client", FakeUnderlyingClient)
    client = OllamaClient(base_url="http://localhost:11434", temperature=0.7, seed=42)

    client.generate(prompt="hi", model="test-model", options={"num_ctx": 4096})

    fake = client._get_client(None)
    assert fake.calls[0]["options"] == {"temperature": 0.7, "seed": 42, "num_ctx": 4096}


def test_generate_per_call_option_overrides_default(monkeypatch) -> None:
    monkeypatch.setattr(ollama_module, "Client", FakeUnderlyingClient)
    client = OllamaClient(base_url="http://localhost:11434", temperature=0.7, seed=42)

    client.generate(prompt="hi", model="test-model", options={"seed": 7})

    fake = client._get_client(None)
    assert fake.calls[0]["options"] == {"temperature": 0.7, "seed": 7}


def test_resolve_seed_random_returns_int() -> None:
    resolved = _resolve_seed("random")
    assert isinstance(resolved, int)


def test_resolve_seed_passes_through_int_and_none() -> None:
    assert _resolve_seed(5) == 5
    assert _resolve_seed(None) is None


def test_ollama_config_rejects_negative_temperature() -> None:
    with pytest.raises(ValidationError):
        OllamaConfig(base_url="http://localhost:11434", temperature=-0.1)


def test_default_seed_increments_per_request(monkeypatch) -> None:
    monkeypatch.setattr(ollama_module, "Client", FakeUnderlyingClient)
    client = OllamaClient(base_url="http://localhost:11434", seed=100)

    for _ in range(3):
        client.generate(prompt="hi", model="test-model")

    seeds = [call["options"]["seed"] for call in client._get_client(None).calls]
    assert seeds == [100, 101, 102]


def test_explicit_seed_wins_and_does_not_consume_counter(monkeypatch) -> None:
    monkeypatch.setattr(ollama_module, "Client", FakeUnderlyingClient)
    client = OllamaClient(base_url="http://localhost:11434", seed=100)

    client.generate(prompt="hi", model="test-model", options={"seed": 7})
    client.generate(prompt="hi", model="test-model")

    seeds = [call["options"]["seed"] for call in client._get_client(None).calls]
    assert seeds == [7, 100]


def test_no_seed_sent_when_client_seed_is_none(monkeypatch) -> None:
    monkeypatch.setattr(ollama_module, "Client", FakeUnderlyingClient)
    client = OllamaClient(base_url="http://localhost:11434")

    client.generate(prompt="hi", model="test-model")
    client.generate(prompt="hi", model="test-model")

    for call in client._get_client(None).calls:
        assert "seed" not in call["options"]


def _scripted_client(monkeypatch, sends, probes=()):
    sends = list(sends)
    probes = list(probes)
    record = SimpleNamespace(sends=[], probes=[], sleeps=[], clock=[0.0])

    class ScriptedClient:
        def __init__(self, host=None, timeout=None) -> None:
            pass

        def generate(self, **kwargs):
            is_probe = kwargs["prompt"] == "Reply with the single word: pong"
            (record.probes if is_probe else record.sends).append(copy.deepcopy(kwargs))
            item = (probes if is_probe else sends).pop(0)
            if callable(item):
                item = item()
            if isinstance(item, Exception):
                raise item
            return item

    def fake_sleep(seconds):
        record.sleeps.append(seconds)
        record.clock[0] += seconds

    monkeypatch.setattr(ollama_module, "Client", ScriptedClient)
    monkeypatch.setattr(ollama_module, "sleep", fake_sleep)
    monkeypatch.setattr(ollama_module, "monotonic", lambda: record.clock[0])
    return OllamaClient(base_url="http://localhost:11434"), record


def _reply(text="ok", **fields):
    return SimpleNamespace(response=text, done=True, done_reason="stop", **fields)


def _empty_body():
    return json.JSONDecodeError("Expecting value", "", 0)


def _no_instance():
    return ResponseError("No instance found for model test-model", 404)


def test_empty_body_after_a_response_probes_then_resends_the_same_request(monkeypatch) -> None:
    client, record = _scripted_client(monkeypatch, [_reply("first"), _empty_body(), _reply("second")], [_reply("pong")])

    client.generate(prompt="hi", model="test-model", options={"num_ctx": 4096}, images=["a.png"], format="json")
    result = client.generate(prompt="hi", model="test-model", options={"num_ctx": 4096}, images=["a.png"], format="json")

    assert result == "second"
    assert len(record.probes) == 1
    assert "think" not in record.probes[0]
    assert record.probes[0]["options"] == {"num_predict": 8}
    assert "images" not in record.probes[0]
    assert record.sends[2] == record.sends[1]


def test_no_instance_waits_through_failed_probes(monkeypatch) -> None:
    client, record = _scripted_client(
        monkeypatch,
        [_reply("first"), _no_instance(), _reply("second")],
        [_no_instance(), _no_instance(), _reply("pong")],
    )

    client.generate(prompt="hi", model="test-model")
    result = client.generate(prompt="hi", model="test-model")

    assert result == "second"
    assert len(record.probes) == 3
    assert record.sleeps == [ollama_module._PROBE_INTERVAL] * 2


def test_model_that_never_answered_fails_fast_with_the_original_error(monkeypatch) -> None:
    error = _no_instance()
    client, record = _scripted_client(monkeypatch, [error])

    with pytest.raises(ResponseError) as raised:
        client.generate(prompt="hi", model="test-model")

    assert raised.value is error
    assert record.probes == []


def test_recovery_wait_cap_raises_then_later_failures_fail_fast(monkeypatch) -> None:
    probes = [_no_instance() for _ in range(100)]
    later_error = _empty_body()
    client, record = _scripted_client(monkeypatch, [_reply("first"), _empty_body(), later_error], probes)

    client.generate(prompt="hi", model="test-model")
    with pytest.raises(LLMTransportError):
        client.generate(prompt="hi", model="test-model")
    probes_after_cap = len(record.probes)
    assert probes_after_cap > 1
    assert record.clock[0] >= ollama_module._RECOVERY_WAIT_CAP

    with pytest.raises(json.JSONDecodeError) as raised:
        client.generate(prompt="hi", model="test-model")

    assert raised.value is later_error
    assert len(record.probes) == probes_after_cap


def test_every_send_empty_with_working_probes_raises_after_four_sends(monkeypatch) -> None:
    client, record = _scripted_client(
        monkeypatch,
        [_reply("first")] + [_empty_body() for _ in range(4)],
        [_reply("pong") for _ in range(3)],
    )

    client.generate(prompt="hi", model="test-model")
    with pytest.raises(LLMTransportError):
        client.generate(prompt="hi", model="test-model")

    assert len(record.sends) == 5
    assert len(record.probes) == 3


def test_non_empty_invalid_body_is_reraised_unchanged(monkeypatch, caplog) -> None:
    error = json.JSONDecodeError("Expecting value", "<html>bad gateway</html>", 0)
    client, record = _scripted_client(monkeypatch, [_reply("first"), error])

    client.generate(prompt="hi", model="test-model")
    with caplog.at_level("WARNING"):
        with pytest.raises(json.JSONDecodeError) as raised:
            client.generate(prompt="hi", model="test-model")

    assert raised.value is error
    assert record.probes == []
    assert len(record.sends) == 2
    assert "body_chars=24" in caplog.text


def test_success_line_logs_token_counts(monkeypatch, caplog) -> None:
    client, _ = _scripted_client(monkeypatch, [_reply(prompt_eval_count=123, eval_count=45)])

    with caplog.at_level("INFO"):
        client.generate(prompt="hi", model="test-model")

    assert "prompt_tokens=123 eval_tokens=45" in caplog.text


def test_warns_when_reported_tokens_exceed_num_ctx(monkeypatch, caplog) -> None:
    client, _ = _scripted_client(monkeypatch, [_reply(prompt_eval_count=33000, eval_count=800)])

    with caplog.at_level("WARNING"):
        client.generate(prompt="hi", model="test-model", options={"num_ctx": 32768})

    assert "LLM context window exceeded: model=test-model prompt_tokens=33000 eval_tokens=800 num_ctx=32768" in caplog.text


def test_no_context_warning_at_or_under_num_ctx(monkeypatch, caplog) -> None:
    client, _ = _scripted_client(monkeypatch, [_reply(prompt_eval_count=32000, eval_count=768)])

    with caplog.at_level("WARNING"):
        client.generate(prompt="hi", model="test-model", options={"num_ctx": 32768})

    assert "LLM context window exceeded" not in caplog.text


def test_no_context_warning_without_token_counts(monkeypatch, caplog) -> None:
    client, _ = _scripted_client(monkeypatch, [_reply()])

    with caplog.at_level("WARNING"):
        result = client.generate(prompt="hi", model="test-model", options={"num_ctx": 32768})

    assert result == "ok"
    assert "LLM context window exceeded" not in caplog.text


def test_timeout_is_not_a_transport_failure(monkeypatch) -> None:
    error = httpx.ReadTimeout("timed out")
    client, record = _scripted_client(monkeypatch, [_reply("first"), error])

    client.generate(prompt="hi", model="test-model")
    with pytest.raises(httpx.ReadTimeout) as raised:
        client.generate(prompt="hi", model="test-model")

    assert raised.value is error
    assert record.probes == []
    assert len(record.sends) == 2


def test_resend_after_empty_body_keeps_the_same_seed(monkeypatch) -> None:
    client, record = _scripted_client(monkeypatch, [_reply("first"), _empty_body(), _reply("second")], [_reply("pong")])
    client._seed = 100

    client.generate(prompt="hi", model="test-model")
    client.generate(prompt="hi", model="test-model")

    assert record.sends[1]["options"]["seed"] == 101
    assert record.sends[2]["options"]["seed"] == 101


def test_recovery_waits_share_one_deadline_per_call(monkeypatch) -> None:
    probes = [_no_instance() for _ in range(55)] + [_reply("pong")] + [_no_instance() for _ in range(100)]
    client, record = _scripted_client(monkeypatch, [_reply("first"), _empty_body(), _empty_body(), _empty_body()], probes)

    client.generate(prompt="hi", model="test-model")
    with pytest.raises(LLMTransportError):
        client.generate(prompt="hi", model="test-model")
    probes_after_cap = len(record.probes)
    assert len(record.sends) == 3
    assert record.clock[0] == ollama_module._RECOVERY_WAIT_CAP

    with pytest.raises(json.JSONDecodeError):
        client.generate(prompt="hi", model="test-model")

    assert len(record.probes) == probes_after_cap


def test_time_spent_inside_a_resend_is_not_charged_to_the_recovery_budget(monkeypatch) -> None:
    def slow_empty_body():
        record.clock[0] += ollama_module._RECOVERY_WAIT_CAP + 1
        return _empty_body()

    client, record = _scripted_client(
        monkeypatch,
        [_reply("first"), _empty_body(), slow_empty_body, _reply("third")],
        [_no_instance(), _no_instance(), _reply("pong"), _reply("pong")],
    )

    client.generate(prompt="hi", model="test-model")
    result = client.generate(prompt="hi", model="test-model")

    assert result == "third"
    assert len(record.sends) == 4
    assert len(record.probes) == 4


def test_last_sleep_before_expiry_is_the_clamped_remainder(monkeypatch) -> None:
    def slow_failed_probe():
        record.clock[0] += ollama_module._RECOVERY_WAIT_CAP - 5
        return _no_instance()

    client, record = _scripted_client(monkeypatch, [_reply("first"), _empty_body()], [slow_failed_probe])

    client.generate(prompt="hi", model="test-model")
    with pytest.raises(LLMTransportError):
        client.generate(prompt="hi", model="test-model")

    assert record.sleeps == [5]
    assert len(record.probes) == 1
