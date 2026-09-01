from abc import ABC, abstractmethod
from typing import Any


class LLMClient(ABC):
    """Backend-independent interface for LLM generation."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        model: str,
        options: dict[str, Any] | None = None,
    ) -> str:
        """Generate a text response from an LLM."""
        raise NotImplementedError
