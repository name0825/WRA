import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


MotionLevel = Literal["none", "low", "mid", "high"]


def _as_text(value: Any) -> str:
    """Flatten a structure an LLM sent where the schema asks for one string."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return ", ".join(f"{key}: {_as_text(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(_as_text(item) for item in value)
    return str(value)


def _breakpoint_name(width: str) -> str:
    """Name a breakpoint an LLM gave as a bare width."""
    digits = re.match(r"\s*(\d+)", width)
    if not digits:
        return width.strip() or "breakpoint"
    pixels = int(digits.group(1))
    if pixels < 640:
        return "mobile"
    return "tablet" if pixels < 1024 else "desktop"



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
    def normalize_scale(cls, value: Any) -> Any:
        """Accept a type scale sent as a list of sizes or a map of roles to sizes."""
        if isinstance(value, dict) and not isinstance(value.get("scale", ""), str):
            result = dict(value)
            result["scale"] = _as_text(value["scale"])
            return result
        return value

    @model_validator(mode="before")
    @classmethod
    def normalize_weight(cls, value: Any) -> Any:
        """Accept concise structured weight output from LLMs and normalize it.

        The public DesignSpec contract uses a string here, but models sometimes
        naturally emit a mapping such as {"heading": 600, "body": 400}.
        Converting that representation keeps the schema strict downstream
        without forcing the planner to retry a semantically valid response.

        Only the weight itself is rewritten: joining the whole typography object
        into this field turned every weight token into a description of the type
        family and scale, which is not a weight at all.
        """
        if isinstance(value, dict) and isinstance(value.get("weight"), dict):
            result = dict(value)
            result["weight"] = ", ".join(f"{key}: {item}" for key, item in value["weight"].items())
            return result
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

    @model_validator(mode="before")
    @classmethod
    def normalize_principles(cls, value: Any) -> Any:
        """Normalize a single LLM-generated principle into a one-item list."""
        if isinstance(value, dict):
            principles = value.get("principles")
            if isinstance(principles, str):
                result = dict(value)
                result["principles"] = [principles]
                return result
        return value


class Design(BaseModel):
    concept: str
    visual_direction: str
    layout: str
    color_system: ColorSystem
    typography: Typography
    framework: FrameworkRecommendations
    motion: Motion

    @model_validator(mode="before")
    @classmethod
    def normalize_layout(cls, value: Any) -> Any:
        """Accept a layout described as a list of notes or a map of regions."""
        if isinstance(value, dict) and "layout" in value and not isinstance(value["layout"], str):
            result = dict(value)
            result["layout"] = _as_text(value["layout"])
            return result
        return value


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
    def normalize_breakpoints(cls, value: Any) -> Any:
        """Normalize the shapes LLMs reach for instead of [{name, width}].

        Seen in practice: a bare width ("768px"), a numeric width, a single
        {name: width} pair per entry, and the whole set as one
        {"mobile": "0px", "tablet": "768px"} map. Each carries the same
        information the schema asks for, just arranged differently.
        """
        if not isinstance(value, dict) or "breakpoints" not in value:
            return value
        breakpoints = value["breakpoints"]

        if isinstance(breakpoints, dict):
            normalized = [{"name": str(name), "width": _as_text(width)} for name, width in breakpoints.items()]
        elif isinstance(breakpoints, list):
            normalized = []
            for item in breakpoints:
                if isinstance(item, dict) and "width" in item:
                    entry = dict(item)
                    entry["width"] = _as_text(entry["width"])
                    if "name" in entry:
                        entry["name"] = _as_text(entry["name"])
                    normalized.append(entry)
                elif isinstance(item, dict) and len(item) == 1:
                    name, width = next(iter(item.items()))
                    normalized.append({"name": str(name), "width": _as_text(width)})
                elif isinstance(item, (str, int, float)):
                    width = _as_text(item)
                    normalized.append({"name": _breakpoint_name(width), "width": width})
                else:
                    normalized.append(item)
        else:
            return value

        result = dict(value)
        result["breakpoints"] = normalized
        return result

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

    @model_validator(mode="before")
    @classmethod
    def normalize_behavior(cls, value: Any) -> Any:
        if isinstance(value, dict) and "behavior" in value and not isinstance(value["behavior"], str):
            result = dict(value)
            result["behavior"] = _as_text(value["behavior"])
            return result
        return value


class InteractionSpec(BaseModel):
    principles: list[str] = Field(default_factory=list)
    components: list[InteractionComponent] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_principles(cls, value: Any) -> Any:
        """Normalize a single LLM-generated principle into a one-item list."""
        if isinstance(value, dict):
            principles = value.get("principles")
            if isinstance(principles, str):
                result = dict(value)
                result["principles"] = [principles]
                return result
        return value


class DesignSpec(BaseModel):
    design: Design
    sections: list[SectionSpec] = Field(default_factory=list)
    responsive: ResponsiveSpec
    interaction: InteractionSpec
