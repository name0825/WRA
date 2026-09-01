from pathlib import Path

from wra.analyzer.extractor import (
    extract_business_info,
    extract_content,
    extract_media,
    extract_metadata,
    extract_requirements,
    extract_structure,
)
from wra.analyzer.html_parser import parse_html
from wra.core.models import FeatureModel


class HTMLAnalyzer:
    def analyze(self, html: str) -> FeatureModel:
        root = parse_html(html)
        content = extract_content(root)
        media = extract_media(root)
        return FeatureModel(
            metadata=extract_metadata(root),
            content=content,
            structure=extract_structure(root),
            media=media,
            business=extract_business_info(root, content),
            requirements=extract_requirements(content, media),
        )

    def analyze_file(self, path: str | Path) -> FeatureModel:
        file_path = Path(path)
        html = file_path.read_text(encoding="utf-8")
        return self.analyze(html)
