from wra.designer.schema import DesignSpec

_FIELDS = (
    ("--color-bg", "color_system", "background"),
    ("--color-surface", "color_system", "surface"),
    ("--color-text", "color_system", "text"),
    ("--color-muted", "color_system", "muted_text"),
    ("--color-primary", "color_system", "primary"),
    ("--color-accent", "color_system", "accent"),
    ("--font-heading", "typography", "heading"),
    ("--font-body", "typography", "body"),
)


def root_block(design_spec: DesignSpec) -> str:
    declarations = []
    for token, group, field in _FIELDS:
        value = str(getattr(getattr(design_spec.design, group), field, "") or "").strip().rstrip(";").strip()
        if value:
            declarations.append(f"{token}:{value}")
    if not declarations:
        return ""
    return ":root{" + ";".join(declarations) + ";}"
