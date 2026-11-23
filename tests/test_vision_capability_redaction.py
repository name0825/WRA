import httpx
import pytest

from wra.llm import vision_capability as vc

BASE_URL = "http://user:secret@host:11434"


class FakeResponse:
    def __init__(self, json_data=None):
        self._json_data = json_data

    def raise_for_status(self):
        return None

    def json(self):
        return self._json_data


@pytest.fixture(autouse=True)
def _reset_probed():
    vc._probed.clear()
    yield
    vc._probed.clear()


def _joined(caplog):
    return "\n".join(record.getMessage() for record in caplog.records) + "\n" + "\n".join(str(record.exc_text) for record in caplog.records)


def test_unreachable_host_logs_never_contain_credentials(monkeypatch, caplog):
    def refuse(url, json=None, timeout=None):
        raise httpx.ConnectError(f"connection refused: {url}")

    monkeypatch.setattr(vc.httpx, "get", refuse)
    monkeypatch.setattr(vc.httpx, "post", refuse)
    with caplog.at_level("DEBUG"):
        vc.warn_if_vision_missing(BASE_URL, "evaluator", ["m"])

    logged = _joined(caplog)
    assert "Host unreachable" in logged
    assert "ConnectError" in logged
    assert "secret" not in logged


def test_api_show_and_probe_failure_logs_never_contain_credentials(monkeypatch, caplog):
    def fake_post(url, json=None, timeout=None):
        raise httpx.ReadTimeout(f"timed out: {url}")

    monkeypatch.setattr(vc.httpx, "get", lambda url, timeout=None: FakeResponse({"data": [{"id": "m"}]}))
    monkeypatch.setattr(vc.httpx, "post", fake_post)
    with caplog.at_level("DEBUG"):
        vc.warn_if_vision_missing(BASE_URL, "evaluator", ["m"])

    logged = _joined(caplog)
    assert "Image-input probe failed" in logged
    assert "secret" not in logged


def test_blind_server_warning_never_contains_credentials(monkeypatch, caplog):
    monkeypatch.setattr(vc.httpx, "get", lambda url, timeout=None: FakeResponse({"data": [{"id": "m", "capabilities": ["text", "vision"]}]}))
    monkeypatch.setattr(vc.httpx, "post", lambda url, json=None, timeout=None: FakeResponse({"prompt_eval_count": 30}))
    with caplog.at_level("WARNING"):
        vc.warn_if_vision_missing(BASE_URL, "evaluator", ["m"])

    logged = _joined(caplog)
    assert "ignores image inputs" in logged
    assert "http://host:11434" in logged
    assert "secret" not in logged
