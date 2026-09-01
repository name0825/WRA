import json
import logging
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
from wra.core.models import BusinessInfo, FeatureModel, ModelSpec, Requirements, Site
from wra.llm.client import LLMClient
from wra.llm.response import normalize_json_response

logger = logging.getLogger(__name__)


class HTMLAnalyzer:
    """Analyze arbitrary web pages with deterministic extraction and optional LLM semantics."""

    def __init__(self, llm_client: LLMClient | None = None, analyzer_model: str = "", analyzer_think: bool | None = None, analyzer_timeout: float | None = None, prompt_path: str | Path = "prompts/analyzer/system.md") -> None:
        self._llm_client = llm_client
        self._analyzer_model = analyzer_model
        self._analyzer_think = analyzer_think
        self._analyzer_timeout = analyzer_timeout
        self._prompt_path = Path(prompt_path)

    def analyze(self, html: str) -> FeatureModel:
        logger.info("Analyzer started: html_chars=%d model=%s", len(html), self._analyzer_model or "none")
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
        logger.debug("Deterministic extraction completed: headings=%d paragraphs=%d links=%d images=%d", len(content.headings), len(content.paragraphs), len(content.links), len(media.images))

        if self._llm_client is not None and self._analyzer_model:
            logger.info("Analyzer semantic LLM phase started: model=%s", self._analyzer_model)
            features = self._apply_semantic_analysis(features)

        logger.info("Analyzer completed")
        return features

    def analyze_file(self, path: str | Path) -> FeatureModel:
        file_path = Path(path)
        logger.info("Reading input HTML: %s", file_path)
        html = file_path.read_text(encoding="utf-8")
        return self.analyze(html)

    def _apply_semantic_analysis(self, features: FeatureModel) -> FeatureModel:
        prompt_template = self._prompt_path.read_text(encoding="utf-8")
        prompt = f"{prompt_template}\n\nDeterministic extraction result:\n{features.model_dump_json(indent=2)}"
        response = self._llm_client.generate(prompt=prompt, model=self._analyzer_model, options={"format": "json"}, think=self._analyzer_think, timeout=self._analyzer_timeout)
        semantic = self._parse_semantic_response(response)
        logger.debug("Analyzer semantic response parsed: keys=%s", sorted(semantic.keys()))

        if "site" in semantic:
            features.site = Site.model_validate(semantic["site"])
        if "business" in semantic:
            semantic_business = BusinessInfo.model_validate(semantic["business"])
            features.business = self._merge_business_info(features.business, semantic_business)
        if "navigation" in semantic:
            features.navigation = semantic["navigation"]
        if "requirements" in semantic:
            semantic_requirements = Requirements.model_validate(semantic["requirements"])
            features.requirements = self._merge_requirements(features.requirements, semantic_requirements, features)
        return features

    @staticmethod
    def _parse_semantic_response(response: str) -> dict[str, Any]:
        normalized_response = normalize_json_response(response)
        try:
            data = json.loads(normalized_response)
        except json.JSONDecodeError as exc:
            logger.error("Analyzer JSON parsing failed: response_chars=%d normalized_chars=%d", len(response), len(normalized_response))
            logger.debug("Analyzer invalid raw response: %s", response)
            raise ValueError("Analyzer LLM returned invalid JSON") from exc
        if not isinstance(data, dict):
            logger.error("Analyzer response is not a JSON object")
            raise ValueError("Analyzer LLM response must be a JSON object")
        return data

    @staticmethod
    def _has_business_data(value: BusinessInfo) -> bool:
        return any([value.company_name, value.services, value.products, value.contact, value.address, value.email, value.phone, value.business_information, value.cta])

    @staticmethod
    def _merge_business_info(base: BusinessInfo | None, semantic: BusinessInfo) -> BusinessInfo:
        data = base.model_dump() if base is not None else BusinessInfo().model_dump()
        semantic_data = semantic.model_dump()
        for key, value in semantic_data.items():
            if isinstance(value, list):
                data[key] = list(dict.fromkeys([*data[key], *value]))
            elif value is not None:
                data[key] = value
        return BusinessInfo.model_validate(data)

    @staticmethod
    def _collect_feature_strings(value: Any) -> set[str]:
        """Collect strings from deterministic features for verbatim requirement checks."""
        if isinstance(value, str):
            return {value}
        if isinstance(value, dict):
            result: set[str] = set()
            for item in value.values():
                result.update(HTMLAnalyzer._collect_feature_strings(item))
            return result
        if isinstance(value, (list, tuple, set)):
            result: set[str] = set()
            for item in value:
                result.update(HTMLAnalyzer._collect_feature_strings(item))
            return result
        return set()

    @staticmethod
    def _merge_requirements(base: Requirements, semantic: Requirements, features: FeatureModel) -> Requirements:
        """Keep only verbatim semantic requirements as hard requirements.

        LLM semantic analysis can describe a requirement such as "Educational
        background and awards" even when the source page never contains that
        exact phrase. Such descriptions are useful hints, but treating them as
        mandatory visible text makes HTML validation reject otherwise correct
        redesigns. Only strings that actually occur as deterministic source
        content remain in ``must_preserve``; the rest are retained as optional
        semantic hints.
        """
        deterministic_strings = {
            " ".join(value.split()).casefold()
            for value in HTMLAnalyzer._collect_feature_strings(features.model_dump())
            if value.strip()
        }

        must_preserve = list(base.must_preserve)
        optional = list(base.optional)
        for value in semantic.must_preserve:
            normalised = " ".join(value.split()).casefold()
            if normalised and normalised in deterministic_strings:
                if value not in must_preserve:
                    must_preserve.append(value)
            elif value not in optional:
                optional.append(value)

        optional.extend(value for value in semantic.optional if value not in optional)
        return Requirements(must_preserve=must_preserve, optional=list(dict.fromkeys(optional)))
