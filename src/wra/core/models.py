from typing import Literal
import json
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from wra.browser.models import BrowserConfig


class OllamaConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_url: str
    # Ollama unloads an idle model after 5 minutes by default, and a browser
    # evaluation or a save step easily runs longer than that between two LLM
    # calls. Holding the runner resident is what makes single-model mode pay off.
    keep_alive: str | float | None = "30m"


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


SINGLE_MODEL_ROLES = ("analyzer", "designer", "generator", "evaluator", "vision", "creator", "refiner")


class SingleModelConfig(BaseModel):
    """One model, one context window, for the whole run."""
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    name: str = ""
    think: bool | None = None
    timeout: float | None = Field(default=None, gt=0)
    num_ctx: int | None = Field(default=32768, gt=0)

    @model_validator(mode="after")
    def validate_name(self):
        if self.enabled and not self.name.strip():
            raise ValueError("models.single.name must not be empty when models.single.enabled is true")
        return self


class ModelConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    single: SingleModelConfig = SingleModelConfig()
    analyzer: list[ModelSpec] = Field(default_factory=list)
    designer: list[ModelSpec] = Field(default_factory=list)
    generator: list[ModelSpec] = Field(default_factory=list)
    evaluator: list[ModelSpec] = Field(default_factory=list)
    vision: list[ModelSpec] = Field(default_factory=list)
    creator: list[ModelSpec] = Field(default_factory=list)
    refiner: list[ModelSpec] = Field(default_factory=list)

    @field_validator("analyzer", "designer", "generator", "evaluator", "vision", "creator", "refiner", mode="before")
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


ContentFreedom = Literal["low", "medium", "high", "ultra", "madmax"]

# Applies at every freedom level, including madmax. Freedom governs how much
# content may be rewritten or invented, never whether a stated fact may be
# restated incorrectly.
FACTUAL_ACCURACY_RULE = (
    "Every date, number, proper noun, and the pairing between them must match the Feature Model "
    "wherever it appears. Freedom to rewrite is never freedom to restate a fact incorrectly."
)
# Freedom to write new content is not freedom to switch the page's language:
# an audience that reads the source reads the replacement.
SOURCE_LANGUAGE_RULE = (
    "Write in the language the Feature Model's content uses, and where it mixes languages, keep each part in "
    "the language the source used rather than unifying them. Content you add or rewrite must be in the language "
    "of the content it accompanies, and source strings keep their original language character for character. "
    "No freedom level permits translating a source string."
)
FACTUAL_ACCURACY_CAP = (
    "Cap this dimension at 4 when a date, number, proper noun, or the pairing between them "
    "differs from the Feature Model, at any freedom level."
)

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
    "madmax": {
        "authoring": "Write this page as an author, not as a transcriber. Rewrite every heading and every line of prose in your own words, cut whatever is weak, and write substantial copy the source never contained: an opening statement, section introductions, explanatory text that makes the subject legible to a first-time reader, and calls to action the source lacks. Reproducing the source's wording or echoing its section order is a failure at this level. At this level only, these defaults are lifted: `requirements.must_preserve` is advisory rather than required, source strings need not be reproduced, and writing content the Feature Model does not contain is expected rather than fabrication. Rewriting is never translating: a string the Feature Model gives in one language must not reappear in another, and where the source mixes languages, keep each part in the language the source used and write your new copy in the language of the content it accompanies. The fixed points are the subject's identity and its factual record: dates, numbers, proper names, contact values, and URLs must appear exactly as the Feature Model states them wherever you use them, and no external URL or asset may be invented.",
        "planning": "Plan the page an author would want for this subject, not a rearrangement of the Feature Model. Plan sections the source does not contain, and do not mirror its section order. The Feature Model is one input among your own ideas, and `requirements.must_preserve` does not bind the plan.",
        "evaluation": "Judge content_preservation as identity-level fidelity only: invented sections and wholly new copy are the point and are not defects, and closely reproduced source wording earns no credit here. Only a lost subject identity, a misstated fact, or an invented external asset is a defect.",
    },
}

_COVERAGE_ENFORCED_LEVELS = frozenset({"low", "medium"})


def content_freedom_policy(level: ContentFreedom) -> dict[str, str]:
    return CONTENT_FREEDOM_POLICIES[level]


def coverage_is_enforced(level: ContentFreedom) -> bool:
    """Whether a DesignSpec must place every populated Feature Model collection."""
    return level in _COVERAGE_ENFORCED_LEVELS


# At these levels the rubric has narrowed `content_preservation` to identity,
# factual accuracy, and invented assets. That is a constraint, not a quality
# axis: averaging it in hands a compliant page a free top mark it did not earn
# through design, and reduces a misstated fact to a ninth of the total.
_CONTENT_GATE_LEVELS: set[str] = {"ultra", "madmax"}

# Below this the evaluator has reported one of those defects. The rubric leaves
# no other way for the score to fall this far at a gated level.
CONTENT_GATE_THRESHOLD = 7.0


def content_preservation_is_a_gate(level: ContentFreedom) -> bool:
    """Whether content_preservation caps the total instead of averaging into it."""
    return level in _CONTENT_GATE_LEVELS


class ContentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    freedom: ContentFreedom = "low"


class PipelineTerminationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    # Stop a page after this many iterations that fail to beat its best score.
    # 0 leaves the run bounded only by max_iterations. Ignored when `enabled`
    # is false, which asks for an unbounded run on purpose.
    patience: int = Field(default=0, ge=0)


class PipelineOutputConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    save_mode: Literal["all", "best"] = "all"


class RefineConfig(BaseModel):
    """Improving the best page in place instead of redesigning it."""
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    # Consecutive refinements that fail to improve before one full redesign is
    # tried instead. Refining exploits what already works; a redesign explores.
    # 0 never falls back, and a stuck page stays stuck.
    redesign_after_stalls: int = Field(default=2, ge=0)
    # How many of the lowest-scoring dimensions a refinement pass aims at.
    weakest_dimensions: int = Field(default=3, ge=1, le=9)


class PipelineConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_iterations: int = 3
    evaluation_threshold: float = 8.0
    termination: PipelineTerminationConfig = PipelineTerminationConfig()
    refine: RefineConfig = RefineConfig()
    output: PipelineOutputConfig = PipelineOutputConfig()


class RecoveryConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_retries: int = 3
    initial_num_ctx: int = 8192
    max_num_ctx: int = 32768
    initial_ceiling_num_ctx: int = 32768
    growth_power: float = 2.0
    designer_output_headroom_tokens: int = 2048
    generator_output_headroom_tokens: int = 2048


class LoggingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    console_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    directory: str = "log"


class ViewerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str = "127.0.0.1"
    port: int = 5002
    debug: bool = False
    output_root: str = "output"
    score_high_threshold: float = 8.0
    score_mid_threshold: float = 6.0


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


# A model writes one framework's name several ways -- "Tailwind", "Tailwind CSS",
# "tailwindcss" -- and an exact-match lookup rejected all but the catalogue's own
# spelling. The mismatch is unrecoverable: the designer is told the name is not
# configured, repeats it, and the run dies after its retries.
_FRAMEWORK_NAME_SUFFIXES = ("javascript", "css", "js")


def normalize_framework_name(name: str) -> str:
    """A catalogue key for a framework name, ignoring case, spacing and a trailing kind."""
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    for suffix in _FRAMEWORK_NAME_SUFFIXES:
        if len(key) > len(suffix) and key.endswith(suffix):
            return key[: -len(suffix)]
    return key


class FrameworkConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    css: list[Framework] = Field(default_factory=list)
    js: list[Framework] = Field(default_factory=list)

    @staticmethod
    def _find(catalog: list[Framework], name: str) -> Framework | None:
        """The catalogue entry for a name: its own spelling first, then a normalized one."""
        exact = next((framework for framework in catalog if framework.name == name), None)
        if exact is not None:
            return exact
        key = normalize_framework_name(name)
        if not key:
            return None
        return next((framework for framework in catalog if normalize_framework_name(framework.name) == key), None)

    def find_css(self, name: str) -> Framework | None:
        return self._find(self.css, name)

    def find_js(self, name: str) -> Framework | None:
        return self._find(self.js, name)


class VisionConfig(BaseModel):
    """Vision-model analysis of the source media, run once per input."""
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    max_images: int = 20
    min_bytes: int = 4096
    min_dimension: int = 64
    screenshot: bool = True
    # Scroll the source page before capturing it, then reveal whatever its own
    # animations left transparent. Without this, scroll-reveal pages screenshot
    # as mostly blank.
    reveal_animations: bool = True
    cache_directory: str = ".cache/vision"
    # Images analysed at once. Worth raising only when the Ollama server has
    # more than one parallel slot (OLLAMA_NUM_PARALLEL); otherwise the requests
    # simply queue.
    concurrency: int = Field(default=1, ge=1, le=16)


class CreatorConfig(BaseModel):
    """Building a site from a brief instead of redesigning one."""
    model_config = ConfigDict(frozen=True)

    max_pages: int = Field(default=4, ge=1, le=12)


class InputConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    directory: str = "input"
    # entry: redesign the entry page only.
    # pages: redesign every page independently.
    # site:  redesign every page, sharing one site context between them.
    mode: Literal["entry", "pages", "site"] = "entry"
    entry: str | None = None
    asset_directory: str = "assets"
    max_css_bytes: int = 512000
    max_js_bytes: int = 512000
    vision: VisionConfig = VisionConfig()


class WRAConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    ollama: OllamaConfig
    models: ModelConfig
    input: InputConfig = InputConfig()
    creator: CreatorConfig = CreatorConfig()
    design: DesignConfig = DesignConfig()
    content: ContentConfig = ContentConfig()
    browser: BrowserConfig = BrowserConfig()
    pipeline: PipelineConfig
    recovery: RecoveryConfig = RecoveryConfig()
    frameworks: FrameworkConfig = FrameworkConfig()
    logging: LoggingConfig = LoggingConfig()
    viewer: ViewerConfig = ViewerConfig()

    # Single-model mode is a load-time rewrite rather than a runtime branch:
    # every role pool is replaced by the one pinned model and every recovery
    # window is pinned to one size, so Ollama keeps a single runner resident for
    # the whole run instead of rebuilding it whenever the model or num_ctx
    # changes. The cost is that recovery can no longer grow the window on a
    # truncated response; it retries with concision guidance on the same window.
    @model_validator(mode="before")
    @classmethod
    def apply_single_model(cls, data):
        if not isinstance(data, dict):
            return data
        models = data.get("models")
        if not isinstance(models, dict):
            return data
        single = models.get("single")
        if not isinstance(single, dict) or not single.get("enabled"):
            return data

        spec = {"name": single.get("name", "")}
        if single.get("think") is not None:
            spec["think"] = single["think"]
        if single.get("timeout") is not None:
            spec["timeout"] = single["timeout"]
        models = {**models, **{role: [dict(spec)] for role in SINGLE_MODEL_ROLES}}

        num_ctx = single.get("num_ctx")
        recovery = data.get("recovery")
        recovery = dict(recovery) if isinstance(recovery, dict) else {}
        if num_ctx is not None:
            recovery.update({"initial_num_ctx": num_ctx, "initial_ceiling_num_ctx": num_ctx, "max_num_ctx": num_ctx})
        return {**data, "models": models, "recovery": recovery}


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


class VisionInsight(BaseModel):
    """What a vision model saw in one source image."""

    subject: str = ""
    mood: list[str] = Field(default_factory=list)
    role: str = ""
    has_text: bool = False
    text: str | None = None


class AssetInfo(BaseModel):
    """One file discovered in the input directory, described deterministically."""

    path: str
    kind: Literal["image", "video", "css", "js", "font", "other"] = "other"
    bytes: int = 0
    width: int | None = None
    height: int | None = None
    aspect: float | None = None
    usable_as: list[str] = Field(default_factory=list)
    vision: VisionInsight | None = None


class StyleDigest(BaseModel):
    """Design tokens distilled from the source CSS. Never the CSS itself."""

    palette: list[str] = Field(default_factory=list)
    fonts: list[str] = Field(default_factory=list)
    type_scale: list[str] = Field(default_factory=list)
    spacing: list[str] = Field(default_factory=list)
    radii: list[str] = Field(default_factory=list)
    shadows: list[str] = Field(default_factory=list)
    breakpoints: list[str] = Field(default_factory=list)
    layout_signals: dict[str, int] = Field(default_factory=dict)


class ScriptDigest(BaseModel):
    """What the source JavaScript does, not how it does it."""

    libraries: list[str] = Field(default_factory=list)
    interactions: list[str] = Field(default_factory=list)


class PageImpression(BaseModel):
    """A vision model's read of the rendered source page."""

    mood: list[str] = Field(default_factory=list)
    density: str = ""
    layout: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)


class SourceAssets(BaseModel):
    """Everything derived once from the input folder and reused every iteration.

    This travels inside the Feature Model, so it is billed to the designer and
    evaluator prompt on every iteration. Keep it distilled: raw CSS, raw
    JavaScript, and image bytes must never reach this structure.
    """

    styles: StyleDigest = Field(default_factory=StyleDigest)
    scripts: ScriptDigest = Field(default_factory=ScriptDigest)
    assets: list[AssetInfo] = Field(default_factory=list)
    impression: PageImpression | None = None


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


class SemanticAnalysis(BaseModel):
    # The analyzer's semantic response shape. Every field is required on
    # purpose: an optional field lets the schema be satisfied by `{}`, which is
    # exactly the empty-object collapse this schema exists to prevent. The
    # merge stays key-by-key in the analyzer -- this model is used only for its
    # JSON schema.
    site: Site
    business: BusinessInfo
    navigation: dict[str, object]
    requirements: Requirements


class FeatureModel(BaseModel):
    site: Site = Field(default_factory=Site)
    metadata: Metadata = Field(default_factory=Metadata)
    content: Content = Field(default_factory=Content)
    structure: Structure = Field(default_factory=Structure)
    media: Media = Field(default_factory=Media)
    source_assets: SourceAssets = Field(default_factory=SourceAssets)
    business: BusinessInfo | None = None
    navigation: dict[str, object] = Field(default_factory=dict)
    requirements: Requirements = Field(default_factory=Requirements)


EVALUATION_DIMENSIONS = (
    "design_completeness", "visual_hierarchy", "design_consistency", "content_preservation",
    "ux", "readability", "responsive_design", "originality", "html_quality",
)

# Dimensions that live in the DesignSpec: section composition, hierarchy, the
# palette and type system, the responsive model. A refinement is forbidden from
# touching any of those, so aiming one at these dimensions asks for an edit its
# own contract disallows -- the model either returns the page unchanged or
# damages something it was allowed to touch.
STRUCTURAL_DIMENSIONS = ("design_completeness", "visual_hierarchy", "responsive_design", "originality")
LOCAL_DIMENSIONS = tuple(name for name in EVALUATION_DIMENSIONS if name not in STRUCTURAL_DIMENSIONS)


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
    # The level this page was authored at, which decides how the total is formed.
    # Supplied by the evaluator, never by the model being scored.
    content_freedom: ContentFreedom = "low"

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
        """Make overall_score a deterministic function of the dimension scores.

        Ordinarily the mean of all nine. At `ultra` and `madmax`, where
        content_preservation has become a constraint rather than a measure of
        craft, it leaves the mean and caps the result instead: a compliant page
        is scored on its design alone, and a page that loses the subject or
        misstates a fact cannot pass on strong visuals.
        """
        graded = (
            self.design_completeness,
            self.visual_hierarchy,
            self.design_consistency,
            self.ux,
            self.readability,
            self.responsive_design,
            self.originality,
            self.html_quality,
        )
        if not content_preservation_is_a_gate(self.content_freedom):
            graded = (*graded, self.content_preservation)
            self.overall_score = sum(dimension.score for dimension in graded) / len(graded)
            return self

        score = sum(dimension.score for dimension in graded) / len(graded)
        if self.content_preservation.score < CONTENT_GATE_THRESHOLD:
            score = min(score, self.content_preservation.score)
        self.overall_score = score
        return self
