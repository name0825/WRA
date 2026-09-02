# WRA

WRA (Web Redesigner AI)는 기존 HTML을 분석하고, 구조화된 Feature Model을 기반으로 새로운 DesignSpec을 설계한 뒤, HTML을 생성·검증·평가하는 웹 리디자인 파이프라인이다.

## Pipeline

```text
HTML Analyzer
    ↓
Feature Model
    ↓
Design Planner
    ↓
DesignSpec
    ↓
HTML Generator
    ↓
HTML Validator
    ↓
Browser Evaluation (optional)
    ↓
LLM Evaluator
    ↓
Iteration / Revision
```

## Core Contracts

### Feature Model

HTML Analyzer는 결정론적 추출 결과를 바탕으로 의미 정보만 보완한다. 원본 user-visible content는 source language와 wording을 유지하며, `requirements.must_preserve`에는 실제 추출 결과에 존재하는 verbatim 문자열만 허용한다.

### DesignSpec

Design Planner는 구현 코드가 아닌 구조화된 디자인 명세만 생성한다.

- `design`
- `sections`
- `responsive`
- `interaction`

`content_source`는 Feature Model의 실제 reference path를 사용해야 하며, populated content/media/structure collection은 최소 하나의 DesignSpec component에서 참조되어야 한다. `structure.sections`와 `structure.forms`도 동일한 coverage 규칙을 적용한다.

Design Constraints가 제공되면 `variation_policy.instruction`이 해당 iteration의 exploration direction으로 사용되며, `do_not_force=true`인 경우 content suitability, readability, usability, Feature Model fidelity가 우선한다.

### HTML Generator

HTML Generator는 FeatureModel과 DesignSpec을 받아 완전한 HTML 문서를 생성한다. Generator는 DesignSpec을 임의로 재설계하지 않고 구현하는 역할을 담당한다.

Generator는 모든 문서에 UTF-8 charset 및 responsive viewport meta를 포함해야 한다. 모든 HTML `id` attribute는 문서 전체에서 unique해야 한다.

Feature Model에 존재하지 않는 장식용 이미지는 외부 URL을 임의로 생성하지 않고 CSS 또는 inline SVG 등으로 구현한다.

DesignSpec에서 선택한 CSS/JavaScript framework가 YAML에 등록되어 있으면 등록된 CDN URL을 HTML에 삽입한다.

```yaml
frameworks:
  css:
    - name: "Bootstrap"
      cdn: "https://..."
  js:
    - name: "GSAP"
      cdn: "https://..."
```

### HTML Validation

생성된 HTML은 Python validator로 검증한다.

- HTML 비어 있음
- DOCTYPE
- DOM/구조
- 콘텐츠 보존
- 링크
- 미디어
- 중복 `id`

### Recovery

Design Planner와 HTML Generator는 오류 유형에 따라 복구 전략을 구분한다.

```text
Token limit
    → num_ctx 증가
    → 이전 응답을 재사용하지 않고 전체 문서 재생성

JSON / 구조 / 콘텐츠 오류
    → 이전 응답을 활용한 수정 재시도

그 외 오류
    → 재생성
```

Token limit recovery에서는 이전 응답이 잘린 HTML일 수 있으므로 recovery prompt에 이전 응답을 넣지 않는다.

기본 설정:

```yaml
recovery:
  max_retries: 3
  initial_num_ctx: 8192
  max_num_ctx: 32768
  context_multiplier: 2.0
```

## LLM Evaluation

생성 결과는 별도의 Evaluation LLM으로 평가한다.

| 평가 항목 | 범위 |
|---|---:|
| 디자인 완성도 | 0~10 |
| 시각적 계층 | 0~10 |
| 디자인 일관성 | 0~10 |
| 콘텐츠 보존 | 0~10 |
| UX | 0~10 |
| 가독성 | 0~10 |
| 반응형 설계 | 0~10 |
| 독창성 | 0~10 |
| HTML 품질 | 0~10 |

각 항목에 점수와 feedback을 생성하며 `overall_score`는 9개 점수의 산술 평균이다. 이 값은 LLM 응답을 그대로 신뢰하지 않고 `EvaluationResult` 모델이 9개 dimension 점수에서 결정론적으로 다시 계산한다. 평가 모델은 0~2, 5~6, 8~10 등의 명시적인 score anchor를 사용해 점수 인플레이션을 방지한다.

각 dimension의 feedback은 1~2문장으로 제한해 반복 평가에서 불필요한 context 증가를 줄인다.

`content_preservation`은 deterministic validator의 exact-match 검사만 반복하는 것이 아니라 의미, 중요도, 맥락, 링크/미디어 및 필수 정보의 보존 정도를 평가한다.

Browser Evaluation이 활성화되어 있으면 screenshot, overflow, console/page error 등의 browser evidence도 평가 모델에 전달한다.

## Browser Evaluation

선택적으로 Playwright/Chromium을 사용해 생성된 HTML을 실제 브라우저에서 렌더링한다.

기본 viewport:

- Desktop: 1440×900
- Tablet: 768×1024
- Mobile: 390×844

수집 정보:

- viewport별 screenshot
- horizontal overflow
- scroll width / viewport width
- page height
- console errors
- JavaScript page errors

## Iteration

```text
Design / Revision
      ↓
Generate
      ↓
Validate
      ↓
Browser Evaluation (optional)
      ↓
Evaluate
      ↓
종료 조건
```

종료 조건은 pipeline 설정의 evaluation threshold와 max iterations를 따른다.
