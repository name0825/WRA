from pydantic import BaseModel, ConfigDict


class OllamaConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_url: str


class ModelConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    analyzer: str = ""
    designer: str = ""
    generator: str = ""
    evaluator: str = ""


class PipelineConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_iterations: int = 3
    evaluation_threshold: float = 8.0


class WRAConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    ollama: OllamaConfig
    models: ModelConfig
    pipeline: PipelineConfig
