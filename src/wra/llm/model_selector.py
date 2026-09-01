import random
from collections.abc import Sequence


class ModelSelector:
    """Select one configured model for each pipeline iteration."""

    @staticmethod
    def select(models: str | Sequence[str], role: str) -> str:
        if isinstance(models, str):
            candidates = [models.strip()] if models.strip() else []
        else:
            candidates = [model.strip() for model in models if isinstance(model, str) and model.strip()]

        if not candidates:
            raise ValueError(f"No LLM model is configured for role: {role}")

        return random.choice(candidates)
