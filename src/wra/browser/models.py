from pydantic import BaseModel, Field


class BrowserViewport(BaseModel):
    name: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class BrowserConfig(BaseModel):
    enabled: bool = False
    timeout_ms: int = Field(default=10000, gt=0)
    viewports: list[BrowserViewport] = Field(default_factory=lambda: [
        BrowserViewport(name="desktop", width=1440, height=900),
        BrowserViewport(name="tablet", width=768, height=1024),
        BrowserViewport(name="mobile", width=390, height=844),
    ])
    screenshot: bool = True
    localize_web_fonts: bool = True
    visual_inspection: bool = True
    evaluation_max_pixels: int | None = Field(default=None, gt=0)


class ContrastFailure(BaseModel):
    text: str
    ratio: float
    foreground: str
    background: str


class OverflowElement(BaseModel):
    selector: str
    right: int


class WrappedTextElement(BaseModel):
    selector: str
    text: str
    line_count: int
    char_count: int


class VerticalGapBand(BaseModel):
    top: int
    height: int


class HeaderLogoElement(BaseModel):
    selector: str
    width: int
    height: int


class EdgeHuggingTextElement(BaseModel):
    selector: str
    section: str
    left: int
    right: int
    text: str


class HeaderOverlapPair(BaseModel):
    a: str
    b: str
    ratio: float


class OccludedTextElement(BaseModel):
    selector: str
    text: str
    occluder: str


class BrowserViewportResult(BaseModel):
    name: str
    width: int
    height: int
    horizontal_overflow: bool = False
    scroll_width: int = 0
    viewport_width: int = 0
    page_height: int = 0
    console_errors: list[str] = Field(default_factory=list)
    page_errors: list[str] = Field(default_factory=list)
    screenshot_path: str | None = None
    stylesheet_rule_count: int | None = None
    contrast_failures: list[ContrastFailure] = Field(default_factory=list)
    broken_images: list[str] = Field(default_factory=list)
    overflow_elements: list[OverflowElement] = Field(default_factory=list)
    first_screen_max_right: int | None = None
    wrapped_text_elements: list[WrappedTextElement] = Field(default_factory=list)
    vertical_gap_bands: list[VerticalGapBand] = Field(default_factory=list)
    header_logo_elements: list[HeaderLogoElement] = Field(default_factory=list)
    edge_hugging_text_elements: list[EdgeHuggingTextElement] = Field(default_factory=list)
    header_overlap_pairs: list[HeaderOverlapPair] = Field(default_factory=list)
    occluded_text_elements: list[OccludedTextElement] = Field(default_factory=list)


class BrowserEvaluationResult(BaseModel):
    enabled: bool
    viewports: list[BrowserViewportResult] = Field(default_factory=list)
    failed_viewports: list[str] = Field(default_factory=list)
    blocked_external_resources: list[str] = Field(default_factory=list)
    wide_text_width_median: int | None = None

