from typing import Literal

from pydantic import BaseModel, Field


MotionLevel = Literal["none", "low", "mid", "high"]


class ColorSystem(BaseModel):
    background: str
    surface: str
    text: str
    muted_text: str
    primary: str
    accent: str


class Typography(BaseModel):
    heading: str
    body: str
    scale: str
    weight: str


class FrameworkRecommendation(BaseModel):
    name: str
    reason: str


class FrameworkRecommendations(BaseModel):
    css: FrameworkRecommendation
    js: FrameworkRecommendation


class Motion(BaseModel):
    level: MotionLevel
    principles: list[str] = Field(default_factory=list)


class Design(BaseModel):
    concept: str
    visual_direction: str
    layout: str
    color_system: ColorSystem
    typography: Typography
    framework: FrameworkRecommendations
    motion: Motion


class ComponentSpec(BaseModel):
    type: str
    purpose: str
    content_source: str


class SectionSpec(BaseModel):
    id: str
    purpose: str
    layout: str
    components: list[ComponentSpec] = Field(default_factory=list)


class ResponsiveBreakpoint(BaseModel):
    name: str
    width: str


class ResponsiveSpec(BaseModel):
    strategy: str
    breakpoints: list[ResponsiveBreakpoint] = Field(default_factory=list)
    behavior: list[str] = Field(default_factory=list)


class InteractionComponent(BaseModel):
    target: str
    behavior: str


class InteractionSpec(BaseModel):
    principles: list[str] = Field(default_factory=list)
    components: list[InteractionComponent] = Field(default_factory=list)


class DesignSpec(BaseModel):
    design: Design
    sections: list[SectionSpec] = Field(default_factory=list)
    responsive: ResponsiveSpec
    interaction: InteractionSpec
