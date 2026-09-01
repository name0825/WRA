from typing import Any

from ollama import Client

from .client import LLMClient


class OllamaClient(LLMClient):
    """Ollama implementation backed by the official Python client."""

    def __init__(self, base_url: str) -> None:
        self._client = Client(host=base_url)

    def generate(
        self,
        prompt: str,
        model: str,
        options: dict[str, Any] | None = None,
    ) -> str:
        response = self._client.generate(
            model=model,
            prompt=prompt,
            options=options,
        )
        return response.response
