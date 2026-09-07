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


class BrowserEvaluationResult(BaseModel):
    enabled: bool
    viewports: list[BrowserViewportResult] = Field(default_factory=list)

