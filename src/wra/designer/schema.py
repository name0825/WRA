from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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

    @model_validator(mode="before")
    @classmethod
    def normalize_weight(cls, value: Any) -> Any:
        """Accept concise structured weight output from LLMs and normalize it.

        The public DesignSpec contract uses a string here, but models sometimes
        naturally emit a mapping such as {"heading": 600, "body": 400}.
        Converting that representation keeps the schema strict downstream
        without forcing the planner to retry a semantically valid response.
        """
        if isinstance(value, dict):
            parts = [f"{key}: {item}" for key, item in value.items()]
            value = dict(value)
            value["weight"] = ", ".join(parts)
            return value
        return value


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

    @model_validator(mode="before")
    @classmethod
    def normalize_behavior(cls, value: Any) -> Any:
        """Normalize richer breakpoint behavior objects to schema strings.

        Some LLMs emit [{"breakpoint": "mobile", "changes": [...]}]
        even though the DesignSpec intentionally keeps behavior as strings.
        Preserve all information in a compact, deterministic representation.
        """
        if not isinstance(value, dict):
            return value

        behavior = value.get("behavior")
        if not isinstance(behavior, list):
            return value

        normalized: list[str] = []
        for item in behavior:
            if isinstance(item, str):
                normalized.append(item)
                continue
            if isinstance(item, dict):
                breakpoint = item.get("breakpoint") or item.get("name")
                changes = item.get("changes")
                if isinstance(changes, list):
                    change_text = "; ".join(str(change) for change in changes)
                elif changes is None:
                    change_text = ""
                else:
                    change_text = str(changes)
                if breakpoint and change_text:
                    normalized.append(f"{breakpoint}: {change_text}")
                elif breakpoint:
                    normalized.append(str(breakpoint))
                else:
                    normalized.append(str(item))
                continue
            normalized.append(str(item))

        result = dict(value)
        result["behavior"] = normalized
        return result


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
