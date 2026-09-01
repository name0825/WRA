from pydantic import BaseModel, ConfigDict, Field


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
    business: BusinessInfo = Field(default_factory=BusinessInfo)
    navigation: dict[str, object] = Field(default_factory=dict)
    requirements: Requirements = Field(default_factory=Requirements)
