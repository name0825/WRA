import json

from wra.core.models import FeatureModel
from wra.generator.script_repair import repair_script_mixing
from wra.llm.client import LLMClient


class FakeLLMClient(LLMClient):
    def __init__(self, response: str | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def generate(self, prompt: str, model: str, options=None, **kwargs) -> str:
        self.calls.append({"prompt": prompt, "model": model})
        if self.error is not None:
            raise self.error
        return self.response


def test_repair_replaces_token_in_text_node() -> None:
    html = '<!DOCTYPE html><html><body><p title="빵집幸福빵집입니다.">빵집幸福빵집입니다.</p><script>var t = "빵집幸福빵집입니다.";</script></body></html>'
    features = FeatureModel(content={"text": ["빵집입니다."]})
    client = FakeLLMClient(json.dumps({"빵집幸福빵집입니다.": "빵집입니다."}, ensure_ascii=False))

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert "<p title=\"빵집幸福빵집입니다.\">빵집입니다.</p>" in repaired
    assert 'var t = "빵집幸福빵집입니다.";' in repaired
    assert rewrites == ["'빵집幸福빵집입니다.' -> '빵집입니다.'"]
    assert len(client.calls) == 1


def test_repair_rejects_replacement_still_containing_han() -> None:
    html = "<!DOCTYPE html><html><body><p>빵집幸福빵집입니다.</p></body></html>"
    features = FeatureModel(content={"text": ["빵집입니다."]})
    client = FakeLLMClient(json.dumps({"빵집幸福빵집입니다.": "빵집幸福당신."}, ensure_ascii=False))

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == html
    assert rewrites == []


def test_repair_leaves_html_unchanged_on_llm_error() -> None:
    html = "<!DOCTYPE html><html><body><p>빵집幸福빵집입니다.</p></body></html>"
    features = FeatureModel(content={"text": ["빵집입니다."]})
    client = FakeLLMClient(error=RuntimeError("model unreachable"))

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == html
    assert rewrites == []


def test_repair_skips_llm_call_when_no_tokens_leaked() -> None:
    html = "<!DOCTYPE html><html><body><p>빵집입니다.</p></body></html>"
    features = FeatureModel(content={"text": ["빵집입니다."]})
    client = FakeLLMClient(json.dumps({}))

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == html
    assert rewrites == []
    assert client.calls == []


def test_repair_preserves_markup_outside_the_rewritten_token() -> None:
    html = (
        "<!DOCTYPE html><html><body><!-- 안내 --><DIV class='x'>&nbsp;&amp;"
        "<svg><![CDATA[ raw <data> ]]></svg>빵집幸福빵집입니다.</DIV></body></html>"
    )
    features = FeatureModel(content={"text": ["빵집입니다."]})
    client = FakeLLMClient(json.dumps({"빵집幸福빵집입니다.": "빵집입니다."}, ensure_ascii=False))

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == html.replace("빵집幸福빵집입니다.", "빵집입니다.")
    assert rewrites == ["'빵집幸福빵집입니다.' -> '빵집입니다.'"]


def test_repair_rejects_replacement_containing_markup() -> None:
    html = "<!DOCTYPE html><html><body><p>빵집幸福빵집입니다.</p></body></html>"
    features = FeatureModel(content={"text": ["빵집입니다."]})
    client = FakeLLMClient(
        json.dumps({"빵집幸福빵집입니다.": "<img src=x onerror=alert(1)>"}, ensure_ascii=False)
    )

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == html
    assert rewrites == []


def test_repair_matches_response_key_with_different_punctuation() -> None:
    html = "<!DOCTYPE html><html><body><p>방법을研究中입니다.</p></body></html>"
    features = FeatureModel(content={"text": ["방법을 연구중입니다."]})
    client = FakeLLMClient(
        json.dumps({"방법을研究中입니다。": "방법을 연구중입니다."}, ensure_ascii=False)
    )

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == "<!DOCTYPE html><html><body><p>방법을 연구중입니다.</p></body></html>"
    assert rewrites == ["'방법을研究中입니다.' -> '방법을 연구중입니다.'"]


def test_repair_matches_response_key_with_extra_whitespace() -> None:
    html = "<!DOCTYPE html><html><body><p>방법을研究中입니다.</p></body></html>"
    features = FeatureModel(content={"text": ["방법을 연구중입니다."]})
    client = FakeLLMClient(
        json.dumps({" 방법을 研究中 입니다. ": "방법을 연구중입니다."}, ensure_ascii=False)
    )

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == "<!DOCTYPE html><html><body><p>방법을 연구중입니다.</p></body></html>"
    assert rewrites == ["'방법을研究中입니다.' -> '방법을 연구중입니다.'"]


def test_repair_skips_ambiguous_normalized_key_match() -> None:
    html = "<!DOCTYPE html><html><body><p>방법을研究中입니다.</p></body></html>"
    features = FeatureModel(content={"text": ["방법을 연구중입니다."]})
    client = FakeLLMClient(
        json.dumps(
            {
                "방법을研究中입니다。": "방법을 연구중입니다.",
                "방법을研究中입니다！": "방법을 연구합니다.",
            },
            ensure_ascii=False,
        )
    )

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == html
    assert rewrites == []


def test_repair_falls_back_to_learned_foreign_run_mapping() -> None:
    html = "<!DOCTYPE html><html><body><p>시작,当天 판매분을当天 판매분을當天</p></body></html>"
    features = FeatureModel(content={"text": ["시작,당일 판매분을당일"]})
    client = FakeLLMClient(
        json.dumps(
            {
                "시작,当天": "시작,당일",
                "판매분당일": "판매분당일",
                "판매분當天": "판매분당일",
            },
            ensure_ascii=False,
        )
    )

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == "<!DOCTYPE html><html><body><p>시작,당일 판매분을당일 판매분을당일</p></body></html>"
    assert set(rewrites) == {
        "'시작,当天' -> '시작,당일'",
        "'판매분을当天' -> '판매분을당일'",
        "'판매분을當天' -> '판매분을당일'",
    }


def test_repair_does_not_learn_run_when_value_still_has_foreign_script() -> None:
    html = "<!DOCTYPE html><html><body><p>판매분을当天</p></body></html>"
    features = FeatureModel(content={"text": ["판매분을당일"]})
    client = FakeLLMClient(
        json.dumps({"当天所": "当天당일"}, ensure_ascii=False)
    )

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == html
    assert rewrites == []


def test_repair_leaves_token_unresolved_on_conflicting_learned_mapping() -> None:
    html = "<!DOCTYPE html><html><body><p>판매분을当天</p></body></html>"
    features = FeatureModel(content={"text": ["판매분을당일"]})
    client = FakeLLMClient(
        json.dumps({"A当天": "A당일", "B当天": "B당신"}, ensure_ascii=False)
    )

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == html
    assert rewrites == []


def test_repair_falls_back_to_learned_run_deletion() -> None:
    html = "<!DOCTYPE html><html><body><p>당일 판매분을当天的 구워냅니다.</p></body></html>"
    features = FeatureModel(content={"text": ["당일 판매분을 구워냅니다."]})
    client = FakeLLMClient(json.dumps({"판매분의当天的": "판매분의"}, ensure_ascii=False))

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == "<!DOCTYPE html><html><body><p>당일 판매분을 구워냅니다.</p></body></html>"
    assert rewrites == ["'판매분을当天的' -> '판매분을'"]


def test_repair_normalizes_cjk_punctuation_not_in_token() -> None:
    html = "<!DOCTYPE html><html><body><p>방법을研究中입니다.</p></body></html>"
    features = FeatureModel(content={"text": ["방법을 연구중입니다."]})
    client = FakeLLMClient(
        json.dumps({"방법을研究中입니다。": "방법을 연구중입니다。"}, ensure_ascii=False)
    )

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == "<!DOCTYPE html><html><body><p>방법을 연구중입니다.</p></body></html>"
    assert rewrites == ["'방법을研究中입니다.' -> '방법을 연구중입니다.'"]


def test_repair_falls_back_to_source_present_tail_when_llm_rewrites_key() -> None:
    html = "<!DOCTYPE html><html><body><p>당일 판매분을当天 구워냅니다.</p></body></html>"
    features = FeatureModel(content={"text": ["당일 판매분을 당일 구워냅니다."]})
    client = FakeLLMClient(json.dumps({"판매분당일": "판매분당일"}, ensure_ascii=False))

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == "<!DOCTYPE html><html><body><p>당일 판매분을 당일 구워냅니다.</p></body></html>"
    assert rewrites == ["'판매분을当天' -> '판매분을 당일'"]


def test_repair_leaves_token_unresolved_when_no_candidate_is_source_present() -> None:
    html = "<!DOCTYPE html><html><body><p>당일 판매분을当天 구워냅니다.</p></body></html>"
    features = FeatureModel(content={"text": ["당일 판매분을 당일 구워냅니다."]})
    client = FakeLLMClient(json.dumps({"판매분당일": "판매분오늘"}, ensure_ascii=False))

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == html
    assert rewrites == []


def test_repair_leaves_token_unresolved_on_conflicting_source_present_candidates() -> None:
    html = "<!DOCTYPE html><html><body><p>당일 판매분을当天 구워냅니다. 판매분을 새벽 굽습니다.</p></body></html>"
    features = FeatureModel(
        content={"text": ["당일 판매분을 당일 구워냅니다. 판매분을 새벽 굽습니다."]}
    )
    client = FakeLLMClient(
        json.dumps(
            {"판매분당일": "판매분당일", "판매분새벽X": "판매분새벽"},
            ensure_ascii=False,
        )
    )

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == html
    assert rewrites == []


def test_repair_leaves_middle_run_and_multi_run_tokens_unresolved() -> None:
    html = (
        "<!DOCTYPE html><html><body><p>당일 판매분当天을 구워냅니다. "
        "시작,当天 판매분을当天 구워냅니다.</p></body></html>"
    )
    features = FeatureModel(
        content={"text": ["당일 판매분을 구워냅니다. 시작,당일 판매분을 구워냅니다."]}
    )
    client = FakeLLMClient(
        json.dumps({"판매분당일을": "판매분당일을", "판매분당일": "판매분당일"}, ensure_ascii=False)
    )

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == html
    assert rewrites == []


def test_repair_does_not_rewrite_token_embedded_in_a_longer_word() -> None:
    html = "<!DOCTYPE html><html><body><p>빵집幸福 x빵집福气y</p></body></html>"
    features = FeatureModel(content={"text": ["빵집입니다."]})
    client = FakeLLMClient(json.dumps({"빵집幸福": "빵집"}, ensure_ascii=False))

    repaired, rewrites = repair_script_mixing(html, features, client, "generator-model")

    assert repaired == "<!DOCTYPE html><html><body><p>빵집 x빵집福气y</p></body></html>"
    assert rewrites == ["'빵집幸福' -> '빵집'"]
