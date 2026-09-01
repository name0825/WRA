import json
from pathlib import Path
from typing import Any

from wra.analyzer.extractor import (
    extract_business_info,
    extract_content,
    extract_media,
    extract_metadata,
    extract_requirements,
    extract_structure,
)
from wra.analyzer.html_parser import parse_html
from wra.core.models import BusinessInfo, FeatureModel, Requirements, Site
from wra.llm.client import LLMClient


class HTMLAnalyzer:
    """Analyze arbitrary web pages with deterministic extraction and optional LLM semantics."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        analyzer_model: str = "",
        prompt_path: str | Path = "prompts/analyzer/system.md",
    ) -> None:
        self._llm_client = llm_client
        self._analyzer_model = analyzer_model
        self._prompt_path = Path(prompt_path)

    def analyze(self, html: str) -> FeatureModel:
        root = parse_html(html)
        content = extract_content(root)
        media = extract_media(root)
        extracted_business = extract_business_info(root, content)
        business = extracted_business if self._has_business_data(extracted_business) else None

        features = FeatureModel(
            metadata=extract_metadata(root),
            content=content,
            structure=extract_structure(root),
            media=media,
            business=business,
            requirements=extract_requirements(content, media),
        )

        if self._llm_client is not None and self._analyzer_model:
            features = self._apply_semantic_analysis(features)

        return features

    def analyze_file(self, path: str | Path) -> FeatureModel:
        file_path = Path(path)
        html = file_path.read_text(encoding="utf-8")
        return self.analyze(html)

    def _apply_semantic_analysis(self, features: FeatureModel) -> FeatureModel:
        prompt_template = self._prompt_path.read_text(encoding="utf-8")
        prompt = (
            f"{prompt_template}\n\n"
            "Deterministic extraction result:\n"
            f"{features.model_dump_json(indent=2)}"
        )
        response = self._llm_client.generate(
            prompt=prompt,
            model=self._analyzer_model,
            options={"format": "json"},
        )
        semantic = self._parse_semantic_response(response)

        if "site" in semantic:
            features.site = Site.model_validate(semantic["site"])

        if "business" in semantic:
            semantic_business = BusinessInfo.model_validate(semantic["business"])
            features.business = self._merge_business_info(
                features.business,
                semantic_business,
            )

        if "navigation" in semantic:
            features.navigation = semantic["navigation"]

        if "requirements" in semantic:
            semantic_requirements = Requirements.model_validate(semantic["requirements"])
            features.requirements = self._merge_requirements(
                features.requirements,
                semantic_requirements,
            )

        return features

    @staticmethod
    def _parse_semantic_response(response: str) -> dict[str, Any]:
        try:
            data = json.loads(response)
        except json.JSONDecodeError as exc:
            raise ValueError("Analyzer LLM returned invalid JSON") from exc

        if not isinstance(data, dict):
            raise ValueError("Analyzer LLM response must be a JSON object")

        return data

    @staticmethod
    def _has_business_data(value: BusinessInfo) -> bool:
        return any(
            [
                value.company_name,
                value.services,
                value.products,
                value.contact,
                value.address,
                value.email,
                value.phone,
                value.business_information,
                value.cta,
            ]
        )

    @staticmethod
    def _merge_business_info(
        base: BusinessInfo | None,
        semantic: BusinessInfo,
    ) -> BusinessInfo:
        data = base.model_dump() if base is not None else BusinessInfo().model_dump()
        semantic_data = semantic.model_dump()

        for key, value in semantic_data.items():
            if isinstance(value, list):
                data[key] = list(dict.fromkeys([*data[key], *value]))
            elif value is not None:
                data[key] = value

        return BusinessInfo.model_validate(data)

    @staticmethod
    def _merge_requirements(base: Requirements, semantic: Requirements) -> Requirements:
        return Requirements(
            must_preserve=list(dict.fromkeys([*base.must_preserve, *semantic.must_preserve])),
            optional=list(dict.fromkeys([*base.optional, *semantic.optional])),
        )
