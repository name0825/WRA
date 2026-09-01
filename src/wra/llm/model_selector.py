import random
from collections.abc import Sequence

from wra.core.models import ModelSpec


class ModelSelector:
    """Select one configured model for each pipeline iteration."""

    @staticmethod
    def select(models: Sequence[ModelSpec], role: str) -> ModelSpec:
        candidates = [model for model in models if model.name.strip()]
        if not candidates:
            raise ValueError(f"No LLM model is configured for role: {role}")
        return random.choice(candidates)
