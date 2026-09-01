from wra.analyzer.analyzer import HTMLAnalyzer
from wra.analyzer.html_parser import parse_html


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
