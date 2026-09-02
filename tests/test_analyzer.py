from wra.analyzer.analyzer import HTMLAnalyzer
from wra.analyzer.html_parser import parse_html
from wra.llm.client import LLMClient
from wra.core.models import BusinessInfo


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
    def __init__(self, response: str | list[str]) -> None:
        self.responses = [response] if isinstance(response, str) else list(response)
        self.calls: list[tuple[str, str, dict | None]] = []

    def generate(self, prompt: str, model: str, options: dict | None = None, **kwargs) -> str:
        self.calls.append((prompt, model, options))
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


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
    assert llm.calls[0][2]["format"] == "json"
    assert llm.calls[0][2]["num_ctx"] > 0


def test_analyzer_recovers_from_invalid_semantic_json() -> None:
    llm = FakeLLMClient(["not json", '{"site": {"type": "portfolio"}}'])

    features = HTMLAnalyzer(llm_client=llm, analyzer_model="test-model", semantic_max_retries=1).analyze(HTML)

    assert features.site.type == "portfolio"
    assert len(llm.calls) == 2
    assert "WRA Semantic Analyzer Recovery" in llm.calls[1][0]


def test_business_information_normalizes_object_items() -> None:
    business = BusinessInfo.model_validate({
        "business_information": [
            {"biz_reg_no": "480-88-03967"},
            {"address_type": "headquarters", "status": "active"},
            "Technology company",
        ]
    })

    assert business.business_information == [
        "biz_reg_no: 480-88-03967",
        "address_type: headquarters",
        "status: active",
        "Technology company",
    ]


def test_analyzer_accepts_structured_business_information_from_llm() -> None:
    llm = FakeLLMClient(
        response='''{
          "business": {
            "business_information": [
              {"biz_reg_no": "480-88-03967"}
            ]
          }
        }'''
    )

    features = HTMLAnalyzer(llm_client=llm, analyzer_model="test-model").analyze(HTML)

    assert features.business is not None
    assert "biz_reg_no: 480-88-03967" in features.business.business_information


def test_analyzer_rejects_invalid_llm_json() -> None:
    llm = FakeLLMClient(response="not json")

    try:
        HTMLAnalyzer(llm_client=llm, analyzer_model="test-model", semantic_max_retries=0).analyze(HTML)
    except ValueError as exc:
        assert str(exc) == "Analyzer LLM returned invalid JSON"
    else:
        raise AssertionError("Expected invalid JSON to raise ValueError")


def test_analyzer_accepts_markdown_wrapped_json() -> None:
    llm = FakeLLMClient(response='```json\n{"site": {"type": "portfolio"}}\n```')

    features = HTMLAnalyzer(llm_client=llm, analyzer_model="test-model").analyze(HTML)

    assert features.site.type == "portfolio"


def test_analyzer_accepts_prose_around_json() -> None:
    llm = FakeLLMClient(response='Here is the analysis:\n{"site": {"type": "portfolio"}}\nEnd.')

    features = HTMLAnalyzer(llm_client=llm, analyzer_model="test-model").analyze(HTML)

    assert features.site.type == "portfolio"


def test_analyzer_uses_later_valid_json_after_malformed_attempt() -> None:
    response = '''```json
{
  "site": {"type": "broken"},
  "business": {"phone": ["05</tbody>
```

Wait, I see a slight error in my output stream. Let me provide the clean, correct JSON.

```json
{
  "site": {"type": "corrected"},
  "business": {"business_information": ["Business registration number: 480-88-03967"]}
}
```'''
    llm = FakeLLMClient(response=response)

    features = HTMLAnalyzer(llm_client=llm, analyzer_model="test-model").analyze(HTML)

    assert features.site.type == "corrected"
    assert "Business registration number: 480-88-03967" in features.business.business_information
