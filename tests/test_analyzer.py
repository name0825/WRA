from wra.analyzer.analyzer import HTMLAnalyzer
from wra.analyzer.html_parser import parse_html
from wra.llm.client import LLMClient


HTML = """
<!doctype html>
<html lang="ko">
<head>
  <title>Example</title>
  <meta name="description" content="Example site">
  <meta name="keywords" content="web, ai">
  <link rel="canonical" href="https://example.com/">
  <meta property="og:title" content="Example">
</head>
<body>
  <header><nav><a href="/about">About</a></nav></header>
  <main>
    <section>
      <h1>Example Company</h1>
      <p>Welcome to our service.</p>
      <ul><li>Service A</li><li>Service B</li></ul>
      <img src="hero.jpg" alt="Example product">
      <a class="button" href="/contact">Contact us</a>
    </section>
    <form action="/contact" method="post">
      <label for="email">Email</label>
      <input id="email" name="email">
    </form>
  </main>
  <footer>contact@example.com</footer>
</body>
</html>
"""


class FakeLLMClient(LLMClient):
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict | None]] = []

    def generate(self, prompt: str, model: str, options: dict | None = None) -> str:
        self.calls.append((prompt, model, options))
        return self.response


def test_parser_builds_tree() -> None:
    root = parse_html(HTML)
    html = root.children[0]
    assert html.tag == "html"
    assert html.attrs["lang"] == "ko"


def test_analyzer_extracts_features() -> None:
    features = HTMLAnalyzer().analyze(HTML)

    assert features.metadata.title == "Example"
    assert features.metadata.description == "Example site"
    assert features.metadata.keywords == ["web", "ai"]
    assert features.metadata.language == "ko"
    assert features.metadata.canonical == "https://example.com/"
    assert features.metadata.open_graph["og:title"] == "Example"

    assert features.content.headings[0].text == "Example Company"
    assert features.content.paragraphs == ["Welcome to our service."]
    assert features.content.lists == [["Service A", "Service B"]]
    assert features.content.links[0].href == "/about"
    assert any(button.text == "Contact us" for button in features.content.buttons)

    assert features.structure.header is True
    assert features.structure.navigation is True
    assert features.structure.main is True
    assert len(features.structure.sections) == 1
    assert features.structure.footer is True
    assert features.structure.forms[0].fields == ["email"]

    assert features.media.images[0].src == "hero.jpg"
    assert features.media.images[0].alt == "Example product"
    assert "contact@example.com" in features.business.email
    assert "Example Company" in features.requirements.must_preserve


def test_analyzer_applies_llm_semantic_analysis() -> None:
    llm = FakeLLMClient(
        response='''{
          "site": {
            "type": "corporate",
            "industry": "technology",
            "brand": {"name": "Example Company", "tone": ["professional", "modern"]}
          },
          "business": {
            "services": ["AI web service"],
            "products": [],
            "contact": [],
            "address": null,
            "email": [],
            "phone": [],
            "business_information": ["Technology company"],
            "cta": ["Contact us"]
          },
          "navigation": {"primary_items": ["About", "Contact"]},
          "requirements": {
            "must_preserve": ["Example Company", "AI web service", "Educational background and awards"],
            "optional": ["Technology company"]
          }
        }'''
    )

    features = HTMLAnalyzer(llm_client=llm, analyzer_model="test-model").analyze(HTML)

    assert features.site.type == "corporate"
    assert features.site.industry == "technology"
    assert features.site.brand["name"] == "Example Company"
    assert "AI web service" in features.business.services
    assert "Technology company" in features.business.business_information
    assert "Example Company" in features.requirements.must_preserve
    assert "AI web service" in features.requirements.optional
    assert "Educational background and awards" in features.requirements.optional
    assert "AI web service" not in features.requirements.must_preserve
    assert "Educational background and awards" not in features.requirements.must_preserve
    assert features.navigation["primary_items"] == ["About", "Contact"]
    assert llm.calls[0][1] == "test-model"
    assert llm.calls[0][2] == {"format": "json"}


def test_analyzer_rejects_invalid_llm_json() -> None:
    llm = FakeLLMClient(response="not json")

    try:
        HTMLAnalyzer(llm_client=llm, analyzer_model="test-model").analyze(HTML)
    except ValueError as exc:
        assert str(exc) == "Analyzer LLM returned invalid JSON"
    else:
        raise AssertionError("Expected invalid JSON to raise ValueError")
