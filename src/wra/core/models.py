from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from wra.browser.models import BrowserConfig


class OllamaConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_url: str


class ModelConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    analyzer: list[str] = Field(default_factory=list)
    designer: list[str] = Field(default_factory=list)
    generator: list[str] = Field(default_factory=list)

    @field_validator("analyzer", "designer", "generator", mode="before")
    @classmethod
    def normalize_models(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: list[str] = Field(default_factory=list)

    @field_validator("model", mode="before")
    @classmethod
    def normalize_models(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value


class PipelineTerminationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True


class PipelineOutputConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    save_mode: Literal["all", "best"] = "all"


class PipelineConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_iterations: int = 3
    evaluation_threshold: float = 8.0
    termination: PipelineTerminationConfig = PipelineTerminationConfig()
    output: PipelineOutputConfig = PipelineOutputConfig()


class RecoveryConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_retries: int = 3
    initial_num_ctx: int = 8192
    max_num_ctx: int = 32768
    context_multiplier: float = 2.0


class Framework(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    cdn: str


class FrameworkConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    css: list[Framework] = Field(default_factory=list)
    js: list[Framework] = Field(default_factory=list)

    def find_css(self, name: str) -> Framework | None:
        return next((framework for framework in self.css if framework.name == name), None)

    def find_js(self, name: str) -> Framework | None:
        return next((framework for framework in self.js if framework.name == name), None)


class WRAConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    ollama: OllamaConfig
    models: ModelConfig
    evaluation: EvaluationConfig = EvaluationConfig()
    browser: BrowserConfig = BrowserConfig()
    pipeline: PipelineConfig
    recovery: RecoveryConfig = RecoveryConfig()
    frameworks: FrameworkConfig = FrameworkConfig()


class Metadata(BaseModel):
    title: str | None = None
    description: str | None = None
    keywords: list[str] = Field(default_factory=list)
    language: str | None = None
    canonical: str | None = None
    open_graph: dict[str, str] = Field(default_factory=dict)


class Heading(BaseModel):
    level: int
    text: str


class Link(BaseModel):
    text: str
    href: str | None = None


class Image(BaseModel):
    src: str | None = None
    alt: str | None = None


class Video(BaseModel):
    src: str | None = None
    poster: str | None = None


class Button(BaseModel):
    text: str
    href: str | None = None


class Form(BaseModel):
    action: str | None = None
    method: str | None = None
    fields: list[str] = Field(default_factory=list)


class Section(BaseModel):
    tag: str
    text: str


class Content(BaseModel):
    headings: list[Heading] = Field(default_factory=list)
    paragraphs: list[str] = Field(default_factory=list)
    lists: list[list[str]] = Field(default_factory=list)
    tables: list[list[list[str]]] = Field(default_factory=list)
    buttons: list[Button] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    text: list[str] = Field(default_factory=list)


class Media(BaseModel):
    images: list[Image] = Field(default_factory=list)
    videos: list[Video] = Field(default_factory=list)
    icons: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class Structure(BaseModel):
    header: bool = False
    navigation: bool = False
    main: bool = False
    sections: list[Section] = Field(default_factory=list)
    articles: bool = False
    aside: bool = False
    footer: bool = False
    forms: list[Form] = Field(default_factory=list)


class BusinessInfo(BaseModel):
    company_name: str | None = None
    services: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    contact: list[str] = Field(default_factory=list)
    address: str | None = None
    email: list[str] = Field(default_factory=list)
    phone: list[str] = Field(default_factory=list)
    business_information: list[str] = Field(default_factory=list)
    cta: list[str] = Field(default_factory=list)


class Requirements(BaseModel):
    must_preserve: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)


class Site(BaseModel):
    type: str | None = None
    industry: str | None = None
    brand: dict[str, object] = Field(default_factory=dict)


class FeatureModel(BaseModel):
    site: Site = Field(default_factory=Site)
    metadata: Metadata = Field(default_factory=Metadata)
    content: Content = Field(default_factory=Content)
    structure: Structure = Field(default_factory=Structure)
    media: Media = Field(default_factory=Media)
    business: BusinessInfo | None = None
    navigation: dict[str, object] = Field(default_factory=dict)
    requirements: Requirements = Field(default_factory=Requirements)


class EvaluationDimension(BaseModel):
    score: float = Field(ge=0.0, le=10.0)
    feedback: str = ""


class EvaluationResult(BaseModel):
    design_completeness: EvaluationDimension
    visual_hierarchy: EvaluationDimension
    design_consistency: EvaluationDimension
    content_preservation: EvaluationDimension
    ux: EvaluationDimension
    readability: EvaluationDimension
    responsive_design: EvaluationDimension
    originality: EvaluationDimension
    html_quality: EvaluationDimension
    overall_score: float = Field(ge=0.0, le=10.0)
    summary: str = ""
    improvement_suggestions: list[str] = Field(default_factory=list)
