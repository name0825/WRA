from types import SimpleNamespace

from wra.llm import ollama as ollama_module
from wra.llm.ollama import OllamaClient


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
