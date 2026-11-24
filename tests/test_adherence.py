from wra.core.models import Framework, FrameworkConfig
from wra.designer.schema import (
    ColorSystem,
    Design,
    DesignSpec,
    FrameworkRecommendation,
    FrameworkRecommendations,
    InteractionSpec,
    Motion,
    ResponsiveSpec,
    Typography,
)
from wra.validator.adherence import DIRECTIVE_PREFIX, FRAMEWORK_THEME_PREFIX, find_directive_deviations, find_unapplied_design_tokens

FRAMEWORKS = FrameworkConfig(css=[Framework(name="Tailwind CSS", cdn="https://cdn.example/tailwind.js", tag="script")])


def _spec(css_name: str = "Tailwind CSS", primary: str = "#60A5FA", background: str = "#0F172A") -> DesignSpec:
    return DesignSpec(
        design=Design(
            concept="Test",
            visual_direction="Minimal",
            layout="Single column",
            color_system=ColorSystem(background=background, surface="#1E293B", text="#E2E8F0", muted_text="#94A3B8", primary=primary, accent="#34D399"),
            typography=Typography(heading="sans-serif", body="sans-serif", scale="standard", weight="400"),
            framework=FrameworkRecommendations(
                css=FrameworkRecommendation(name=css_name, reason="test"),
                js=FrameworkRecommendation(name="None", reason="test"),
            ),
            motion=Motion(level="none"),
        ),
        responsive=ResponsiveSpec(strategy="responsive"),
        interaction=InteractionSpec(),
    )


def test_a_framework_page_without_any_designed_colour_is_flagged() -> None:
    html = '<html><head><style>body{color:#111;background:#fff}</style></head><body class="bg-white">x</body></html>'

    findings = find_unapplied_design_tokens(html, _spec(), FRAMEWORKS)

    assert len(findings) == 1 and findings[0].startswith(FRAMEWORK_THEME_PREFIX)


def test_one_designed_colour_in_css_class_or_script_is_enough() -> None:
    in_css = "<style>body{background:#0f172a}</style>"
    in_class = '<div class="bg-[#60a5fa]">x</div>'
    in_rgb = "<style>a{color:rgb(96,165,250)}</style>"

    for html in (in_css, in_class, in_rgb):
        assert find_unapplied_design_tokens(html, _spec(), FRAMEWORKS) == []


def test_a_design_without_a_catalogued_framework_is_not_checked() -> None:
    assert find_unapplied_design_tokens("<p>x</p>", _spec(css_name="Unknown"), FRAMEWORKS) == []


def test_named_colours_are_not_parseable_and_raise_no_finding() -> None:
    spec = _spec(primary="blue", background="navy")
    spec.design.color_system.surface = spec.design.color_system.text = "white"
    spec.design.color_system.muted_text = spec.design.color_system.accent = "gray"

    assert find_unapplied_design_tokens("<p>x</p>", spec, FRAMEWORKS) == []


HTML = """<html><head><link href="https://fonts.googleapis.com/css2?family=Pretendard&family=JetBrains+Mono" rel="stylesheet">
<style>:root{--bg:#0F172A;--fg:#e2e8f0} body{font-family:"Pretendard",sans-serif}</style></head>
<body><img src="assets/logo.svg" alt=""><nav><a>About</a><a>Skills</a></nav></body></html>"""


def test_a_page_that_follows_every_directive_has_no_findings() -> None:
    assert find_directive_deviations(HTML, ["#0f172a", "#E2E8F0"], ["Pretendard", "JetBrains Mono"], "logo.svg", ["About", "skills"]) == []


def test_the_logo_is_checked_by_its_output_path() -> None:
    assert find_directive_deviations(HTML, [], [], "assets/logo.svg", []) == []
    findings = find_directive_deviations(HTML, [], [], "assets/other/logo.svg", [])
    assert len(findings) == 1 and "assets/other/logo.svg" in findings[0]


def test_each_missed_directive_group_yields_one_finding() -> None:
    findings = find_directive_deviations(HTML, ["#0f172a", "#ff0000"], ["Inter"], "brand.svg", ["Contact"])

    assert len(findings) == 4
    assert all(finding.startswith(DIRECTIVE_PREFIX) for finding in findings)
    assert "#ff0000" in findings[0] and "#0f172a" not in findings[0]
    assert "Inter" in findings[1]
    assert "brand.svg" in findings[2]
    assert "Contact" in findings[3]


def test_empty_directives_yield_nothing() -> None:
    assert find_directive_deviations("<p>x</p>", [], [], None, []) == []
