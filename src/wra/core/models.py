from typing import Literal
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from wra.browser.models import BrowserConfig


class OllamaConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_url: str


class ModelSpec(BaseModel):
    """Configuration for one selectable Ollama model."""
    model_config = ConfigDict(frozen=True)

    name: str
    think: bool | None = None
    timeout: float | None = Field(default=None, gt=0)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Model name must not be empty")
        return value


class ModelConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    analyzer: list[ModelSpec] = Field(default_factory=list)
    designer: list[ModelSpec] = Field(default_factory=list)
    generator: list[ModelSpec] = Field(default_factory=list)

    @field_validator("analyzer", "designer", "generator", mode="before")
    @classmethod
    def normalize_models(cls, value):
        if value is None:
            return []
        if isinstance(value, (str, dict)):
            value = [value]
        normalized = []
        for item in value:
            if isinstance(item, str):
                normalized.append({"name": item})
            elif isinstance(item, dict):
                normalized.append(item)
            else:
                normalized.append(item)
        return normalized


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: list[ModelSpec] = Field(default_factory=list)

    @field_validator("model", mode="before")
    @classmethod
    def normalize_models(cls, value):
        if value is None:
            return []
        if isinstance(value, (str, dict)):
            value = [value]
        normalized = []
        for item in value:
            if isinstance(item, str):
                normalized.append({"name": item})
            elif isinstance(item, dict):
                normalized.append(item)
            else:
                normalized.append(item)
        return normalized


class DesignConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: str = "auto"
    presets: list[str] = Field(default_factory=lambda: [
        "business",
        "portfolio",
        "blog",
        "landing",
        "ecommerce",
    ])

    @model_validator(mode="after")
    def validate_mode(self):
        if self.mode != "auto" and self.mode not in self.presets:
            raise ValueError(
                f"Invalid design mode '{self.mode}'. "
                f"Mode must be 'auto' or one of the configured presets: {self.presets}"
            )
        return self


ContentFreedom = Literal["low", "medium", "high", "ultra"]

CONTENT_FREEDOM_POLICIES: dict[str, dict[str, str]] = {
    "low": {
        "authoring": "Keep the source content as the Feature Model supplies it. Do not add, remove, merge, split, or reword source strings, links, or media.",
        "planning": "Place the existing content. Do not plan sections that require content the Feature Model does not supply.",
        "evaluation": "Judge content_preservation as strict fidelity: any added, removed, or reworded source content is a defect.",
    },
    "medium": {
        "authoring": "Keep the source content as the substance of the page. Minor additions, deletions, and rewording are allowed where they improve clarity or fit the layout. Do not change the meaning of a source string or drop a link or media reference.",
        "planning": "Place the existing content. Small supporting elements are allowed, but the plan must stay grounded in the supplied content.",
        "evaluation": "Judge content_preservation as meaning-level fidelity: minor edits are acceptable, changed meaning or dropped links and media are defects.",
    },
    "high": {
        "authoring": "Use the source content as the basis of the page. You may rewrite, reorder, drop weak content, and add new content that fits the site's purpose. Keep the site's subject, identity, and factual claims intact, and do not invent external URLs or assets.",
        "planning": "Use the Feature Model as the basis. Not every supplied collection has to be placed, and sections may be added when the site's purpose calls for them.",
        "evaluation": "Judge content_preservation as intent-level fidelity: rewriting, dropping, and adding content are expected; only a lost subject, a contradicted fact, or invented external assets are defects.",
    },
    "ultra": {
        "authoring": "Treat the source content as reference material. Add, remove, and rewrite freely to produce the strongest page for this subject. Do not invent external URLs or assets, and do not contradict facts the Feature Model states.",
        "planning": "Treat the Feature Model as reference material. Structure the page however best serves the subject.",
        "evaluation": "Judge content_preservation as subject-level fidelity only: the page must still be about the same subject and must not contradict stated facts or invent external assets.",
    },
}

_COVERAGE_ENFORCED_LEVELS = frozenset({"low", "medium"})


def content_freedom_policy(level: ContentFreedom) -> dict[str, str]:
    return CONTENT_FREEDOM_POLICIES[level]


def coverage_is_enforced(level: ContentFreedom) -> bool:
    """Whether a DesignSpec must place every populated Feature Model collection."""
    return level in _COVERAGE_ENFORCED_LEVELS


class ContentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    freedom: ContentFreedom = "low"


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


class LoggingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    console_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    directory: str = "log"


class Framework(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    cdn: str
    tag: Literal["link", "script"] | None = None

    def loading_tag(self, default: Literal["link", "script"]) -> str:
        """Resolve the HTML loading tag, falling back to the catalog's kind.

        An unset tag previously defaulted to `link`, which injected JavaScript
        frameworks as stylesheets.
        """
        return self.tag or default


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
    design: DesignConfig = DesignConfig()
    content: ContentConfig = ContentConfig()
    evaluation: EvaluationConfig = EvaluationConfig()
    browser: BrowserConfig = BrowserConfig()
    pipeline: PipelineConfig
    recovery: RecoveryConfig = RecoveryConfig()
    frameworks: FrameworkConfig = FrameworkConfig()
    logging: LoggingConfig = LoggingConfig()


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

    @field_validator("business_information", mode="before")
    @classmethod
    def normalize_business_information(cls, value):
        """Normalize common LLM object output to the canonical list[str] shape."""
        if value is None:
            return []
        if isinstance(value, (str, dict)):
            value = [value]
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str):
                normalized.append(item)
            elif isinstance(item, dict):
                for key, item_value in item.items():
                    if isinstance(item_value, (dict, list)):
                        item_value = json.dumps(item_value, ensure_ascii=False)
                    normalized.append(f"{key}: {item_value}")
            else:
                normalized.append(str(item))
        return normalized


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

    @model_validator(mode="before")
    @classmethod
    def normalize_llm_output(cls, value):
        """Accept both the canonical dimension-object format and flat LLM scores."""
        if not isinstance(value, dict):
            return value

        normalized = dict(value)
        dimensions = (
            "design_completeness",
            "visual_hierarchy",
            "design_consistency",
            "content_preservation",
            "ux",
            "readability",
            "responsive_design",
            "originality",
            "html_quality",
        )

        for name in dimensions:
            dimension = normalized.get(name)
            if isinstance(dimension, (int, float)) and not isinstance(dimension, bool):
                normalized[name] = {"score": dimension}

        feedback = normalized.pop("feedback", None)
        if feedback is not None and not normalized.get("summary"):
            normalized["summary"] = str(feedback)

        return normalized

    @model_validator(mode="after")
    def enforce_overall_score_mean(self):
        """Make overall_score a deterministic function of the nine dimension scores."""
        dimensions = (
            self.design_completeness,
            self.visual_hierarchy,
            self.design_consistency,
            self.content_preservation,
            self.ux,
            self.readability,
            self.responsive_design,
            self.originality,
            self.html_quality,
        )
        self.overall_score = sum(dimension.score for dimension in dimensions) / len(dimensions)
        return self
