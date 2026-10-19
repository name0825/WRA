import httpx
import pytest

from wra.llm import vision_capability as vc


class FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_data


def test_exo_entry_with_empty_capabilities_warns(monkeypatch, caplog):
    def fake_get(url, timeout=None):
        assert url == "http://192.168.0.164:52415/v1/models"
        return FakeResponse({"data": [{"id": "Edge0/Edge0-35B-A3B-preview", "capabilities": []}]})

    monkeypatch.setattr(vc.httpx, "get", fake_get)
    with caplog.at_level("WARNING"):
        vc.warn_if_vision_missing("http://192.168.0.164:52415/ollama", "evaluator", ["Edge0/Edge0-35B-A3B-preview"])

    assert any("Edge0/Edge0-35B-A3B-preview" in record.message for record in caplog.records)


def test_exo_entry_with_vision_does_not_warn(monkeypatch, caplog):
    def fake_get(url, timeout=None):
        return FakeResponse({"data": [{"id": "mlx-community/gemma-4-26b-a4b-it-6bit", "capabilities": ["text", "vision"]}]})

    monkeypatch.setattr(vc.httpx, "get", fake_get)
    monkeypatch.setattr(vc.httpx, "post", lambda url, json=None, timeout=None: FakeResponse({"prompt_eval_count": 300 if "images" in json else 30}))
    with caplog.at_level("WARNING"):
        vc.warn_if_vision_missing("http://192.168.0.164:52415/ollama", "evaluator", ["mlx-community/gemma-4-26b-a4b-it-6bit"])

    assert not any(record.levelname == "WARNING" for record in caplog.records)


def test_v1_models_404_falls_back_to_api_show(monkeypatch, caplog):
    def fake_get(url, timeout=None):
        return FakeResponse(status_code=404)

    def fake_post(url, json=None, timeout=None):
        assert url == "http://localhost:11434/api/show"
        assert json == {"model": "gemma4:26b"}
        return FakeResponse({"capabilities": ["completion"]})

    monkeypatch.setattr(vc.httpx, "get", fake_get)
    monkeypatch.setattr(vc.httpx, "post", fake_post)
    with caplog.at_level("WARNING"):
        vc.warn_if_vision_missing("http://localhost:11434", "evaluator", ["gemma4:26b"])

    assert any("gemma4:26b" in record.message for record in caplog.records)


def test_v1_models_missing_capabilities_key_falls_back_to_api_show(monkeypatch, caplog):
    def fake_get(url, timeout=None):
        return FakeResponse({"data": [{"id": "gemma4:26b"}]})

    def fake_post(url, json=None, timeout=None):
        assert json == {"model": "gemma4:26b"}
        return FakeResponse({"capabilities": ["completion", "vision"]})

    monkeypatch.setattr(vc.httpx, "get", fake_get)
    monkeypatch.setattr(vc.httpx, "post", fake_post)
    with caplog.at_level("WARNING"):
        vc.warn_if_vision_missing("http://localhost:11434", "evaluator", ["gemma4:26b"])

    assert not any(record.levelname == "WARNING" for record in caplog.records)


def test_network_error_does_not_warn_or_raise(monkeypatch, caplog):
    post_calls = []

    def fake_get(url, timeout=None):
        raise httpx.ConnectError("connection refused")

    def fake_post(url, json=None, timeout=None):
        post_calls.append(json)
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(vc.httpx, "get", fake_get)
    monkeypatch.setattr(vc.httpx, "post", fake_post)
    with caplog.at_level("DEBUG"):
        vc.warn_if_vision_missing("http://192.168.0.164:52415/ollama", "evaluator", ["some-model"])

    assert not any(record.levelname == "WARNING" for record in caplog.records)
    assert post_calls == []


@pytest.fixture(autouse=True)
def _reset_probed():
    vc._probed.clear()
    yield
    vc._probed.clear()


def _unknown_metadata(monkeypatch):
    monkeypatch.setattr(vc.httpx, "get", lambda url, timeout=None: FakeResponse({"data": [{"id": "m"}]}))


def test_probe_warns_when_image_does_not_raise_prompt_tokens(monkeypatch, caplog):
    _unknown_metadata(monkeypatch)
    calls = []

    def fake_post(url, json=None, timeout=None):
        if url.endswith("/api/show"):
            return FakeResponse({})
        calls.append(json)
        return FakeResponse({"prompt_eval_count": 30})

    monkeypatch.setattr(vc.httpx, "post", fake_post)
    with caplog.at_level("WARNING"):
        vc.warn_if_vision_missing("http://host:1/ollama", "evaluator", ["m"])

    assert [("images" in call) for call in calls] == [False, True]
    assert all(call["stream"] is False and call["options"] == {"num_predict": 1} for call in calls)
    assert any("ignores image inputs" in r.message and "http://host:1/ollama" in r.message and "'m'" in r.message and "'evaluator'" in r.message for r in caplog.records)


def test_probe_silent_when_image_raises_prompt_tokens(monkeypatch, caplog):
    _unknown_metadata(monkeypatch)

    def fake_post(url, json=None, timeout=None):
        if url.endswith("/api/show"):
            return FakeResponse({})
        return FakeResponse({"prompt_eval_count": 300 if "images" in json else 30})

    monkeypatch.setattr(vc.httpx, "post", fake_post)
    with caplog.at_level("INFO"):
        vc.warn_if_vision_missing("http://host:1/ollama", "evaluator", ["m"])

    assert not any(r.levelname == "WARNING" for r in caplog.records)
    assert any(r.levelname == "INFO" and "'evaluator'" in r.message and "'m'" in r.message and "300 with image vs 30 without" in r.message for r in caplog.records)


def test_probe_request_error_does_not_warn_or_raise(monkeypatch, caplog):
    _unknown_metadata(monkeypatch)

    def fake_post(url, json=None, timeout=None):
        if url.endswith("/api/show"):
            return FakeResponse({})
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(vc.httpx, "post", fake_post)
    with caplog.at_level("DEBUG"):
        vc.warn_if_vision_missing("http://host:1/ollama", "evaluator", ["m"])

    assert not any(r.levelname == "WARNING" for r in caplog.records)


def test_probe_runs_once_per_model_across_roles(monkeypatch):
    _unknown_metadata(monkeypatch)
    generate_calls = []

    def fake_post(url, json=None, timeout=None):
        if url.endswith("/api/show"):
            return FakeResponse({})
        generate_calls.append(json)
        return FakeResponse({"prompt_eval_count": 30})

    monkeypatch.setattr(vc.httpx, "post", fake_post)
    vc.warn_if_vision_missing("http://host:1/ollama", "evaluator", ["m"])
    vc.warn_if_vision_missing("http://host:1/ollama", "analyzer", ["m"])

    assert len(generate_calls) == 2


def test_probe_warns_when_metadata_claims_vision_but_counts_equal(monkeypatch, caplog):
    monkeypatch.setattr(vc.httpx, "get", lambda url, timeout=None: FakeResponse({"data": [{"id": "m", "capabilities": ["text", "vision"]}]}))
    monkeypatch.setattr(vc.httpx, "post", lambda url, json=None, timeout=None: FakeResponse({"prompt_eval_count": 30}))
    with caplog.at_level("WARNING"):
        vc.warn_if_vision_missing("http://host:1/ollama", "evaluator", ["m"])

    assert any("ignores image inputs" in r.message for r in caplog.records)


def test_probe_passes_keep_alive_and_longer_timeout_for_first_request(monkeypatch):
    monkeypatch.setattr(vc.httpx, "get", lambda url, timeout=None: FakeResponse({"data": [{"id": "m", "capabilities": ["text", "vision"]}]}))
    seen = []

    def fake_post(url, json=None, timeout=None):
        seen.append((json.get("keep_alive"), timeout))
        return FakeResponse({"prompt_eval_count": 30})

    monkeypatch.setattr(vc.httpx, "post", fake_post)
    vc.warn_if_vision_missing("http://host:1/ollama", "evaluator", ["m"], keep_alive="30m")

    assert seen == [("30m", vc._PROBE_COLD_LOAD_TIMEOUT), ("30m", vc._PROBE_TIMEOUT)]
