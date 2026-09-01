# WRA (Web Redesigner AI) 프로젝트 계획서

## 1. 프로젝트 개요

### 1.1 프로젝트명

**WRA (Web Redesigner AI)**  
프로젝트 코드명: `wra`

### 1.2 목적

원본 HTML 파일을 입력으로 받아 웹사이트의 **콘텐츠, 구조, 사이트 특성, 기능, 미디어 등의 핵심 특징을 추출**한 뒤, LLM을 활용하여 원본과 다른 새로운 디자인의 HTML을 자동 생성한다.

WRA는 특정 웹사이트 유형이나 비즈니스 웹사이트를 전제로 하지 않으며, 다양한 유형의 웹페이지에 범용적으로 적용할 수 있도록 설계한다.

핵심 원칙은 다음과 같다.

> **원본 HTML의 의미와 핵심 콘텐츠는 보존하고, 디자인과 표현 방식은 새롭게 재구성한다.**

### 1.3 기본 기술 스택

| 영역 | 기술 |
|---|---|
| 개발 언어 | Python |
| LLM Backend | Ollama |
| 고성능 LLM | Design Planning 담당 |
| 저성능 LLM | HTML 구현 담당 |
| HTML Parsing | Python HTML Parser 계열 |
| 데이터 모델 | Pydantic |
| Browser Rendering | Playwright (확장 단계) |
| 테스트 | pytest |
| 설정 파일 | YAML |

프로젝트 설정은 `.env`가 아닌 **YAML 설정 파일**을 사용한다.

기본 설정 파일:

```text
config/
└── config.yaml
```

모델, Ollama endpoint, iteration 정책, validation/evaluation 정책 등 애플리케이션 동작에 필요한 설정을 YAML에서 관리한다.

모델 목록은 환경에 따라 지정할 수 있도록 설정 템플릿에서는 비워둔다.

---

# 2. 핵심 목표

## 2.1 기능적 목표

1. 기존 HTML 파일을 입력받는다.
2. HTML에서 의미 있는 특징을 자동 추출한다.
3. 추출된 정보를 구조화된 Feature Model로 변환한다.
4. 고성능 LLM이 새로운 디자인을 계획한다.
5. 저성능 LLM이 Design Specification을 실제 HTML로 구현한다.
6. 생성된 HTML을 자동 검증한다.
7. 문제가 발생하면 Recovery 과정을 거친다.
8. 필요할 경우 여러 번 반복하여 품질을 개선한다.
9. 최종적으로 새로운 디자인의 HTML 파일을 출력한다.

## 2.2 품질 목표

단순한 HTML 재작성기가 아니라 다음을 목표로 한다.

- 원본 콘텐츠 보존
- 원본 디자인과 차별화
- 다양한 페이지 구조 생성
- 일관된 디자인 시스템
- 정상적인 HTML/CSS/JS
- 반응형 웹 지원
- 디자인 의도와 실제 구현의 일치
- 실패 상황에서도 가능한 한 최종 결과 생성

---

# 3. 핵심 설계 원칙

## 3.1 분석과 생성을 분리

원본 HTML 전체를 LLM에게 전달하여 바로 새 HTML을 생성하는 방식은 사용하지 않는다.

다음과 같은 중간 데이터 구조를 사용한다.

```text
Original HTML
      ↓
HTML Analyzer
      ↓
Feature Model
      ↓
Design Planner
      ↓
Design Specification
      ↓
HTML Generator
      ↓
Generated HTML
```

## 3.2 LLM의 역할을 분리

고성능 모델과 저성능 모델의 역할을 명확하게 분리한다.

### 고성능 모델

**무엇을 만들 것인가?**

- 디자인 컨셉
- 정보 구조
- 레이아웃
- 섹션 구성
- 컴포넌트
- 시각적 계층
- UX
- 색상
- Typography
- Responsive 전략

### 저성능 모델

**결정된 디자인을 어떻게 구현할 것인가?**

- HTML
- CSS
- JavaScript
- Responsive CSS
- Component 구현

즉,

```text
High Performance LLM
        ↓
Design Decisions
        ↓
Low Performance LLM
        ↓
Implementation
```

으로 책임을 분리한다.

## 3.3 고정된 페이지 구조를 사용하지 않는다

모든 웹사이트를 다음과 같이 생성하는 방식은 지양한다.

```text
Header
Hero
About
Services
Contact
Footer
```

Design Planner가 원본 콘텐츠와 사이트 특성을 분석하여 페이지 구조 자체를 결정하도록 한다.

예:

```text
Header
Hero
Featured Content
Editorial Story
Statistics
Related Content
CTA
Footer
```

또는:

```text
Navigation
Split Hero
Main Content
Timeline
Technical Information
Related Content
Footer
```

등 다양한 구조를 허용한다.

---

# 4. 전체 시스템 아키텍처

```text
                    Original HTML
                         │
                         ▼
                 ┌──────────────────┐
                 │  HTML Analyzer   │
                 │      Python      │
                 └────────┬─────────┘
                          │
                          ▼
                    Feature Model
                          │
                          ▼
              ┌────────────────────────────┐
              │    HIGH PERFORMANCE LLM   │
              │                            │
              │      Design Planner       │
              └─────────────┬──────────────┘
                            │
                            ▼
                    ┌──────────────────┐
                    │   DesignSpec      │
                    └────────┬─────────┘
                             │
                             ▼
              ┌────────────────────────────┐
              │     LOW PERFORMANCE LLM   │
              │                            │
              │      HTML Generator       │
              └─────────────┬──────────────┘
                            │
                            ▼
                    ┌──────────────────┐
                    │  Generated HTML  │
                    └────────┬─────────┘
                             │
                    ┌────────┴─────────┐
                    ▼                   ▼
             ┌──────────────┐  ┌──────────────┐
             │   Validator  │  │   Browser    │
             │    Python    │  │  Playwright  │
             └──────┬───────┘  └──────┬───────┘
                    │                  │
                    └────────┬─────────┘
                             ▼
                    ┌──────────────────┐
                    │    Evaluator     │
                    └────────┬─────────┘
                             │
                       ┌─────┴─────┐
                       │           │
                     FAIL        PASS
                       │           │
                       ▼           ▼
                    Recovery   Final HTML
                       │
                       └──→ LOOP
```

---

# 5. 데이터 파이프라인

각 단계의 데이터 계약을 명확하게 유지한다.

```text
original.html
      │
      ▼
HTMLDocument
      │
      ▼
FeatureModel
      │
      ▼
DesignSpec
      │
      ▼
GeneratedHTML
      │
      ▼
ValidationResult
      │
      ▼
EvaluationResult
```

각 모델은 Pydantic 기반으로 정의하는 것을 기본으로 한다.

---

# 6. HTML Analyzer

## 6.1 역할

원본 HTML에서 LLM에 전달할 필요가 없는 정보는 Python 코드로 직접 추출하고, 의미적인 분석이 필요한 부분만 LLM을 사용한다.

이를 통해 불필요한 Context 사용을 줄인다.

Analyzer는 특정 사이트 유형을 전제로 하지 않고 범용 웹페이지의 특성과 콘텐츠를 분석한다.

## 6.2 추출 정보

### 문서 정보

- `<title>`
- meta description
- keywords
- language
- canonical
- Open Graph 관련 정보

### 콘텐츠

- headings
- paragraphs
- lists
- tables
- buttons
- links
- labels
- 주요 텍스트

### 구조

- header
- navigation
- main
- sections
- article
- aside
- footer
- forms

### 미디어

- images
- image alt
- videos
- icons
- media URL

### 사이트/비즈니스 정보

가능한 경우 다음 정보를 추출한다.

- 사이트 유형 및 특성
- 회사명
- 서비스명
- 제품
- 연락처
- 주소
- 이메일
- 전화번호
- 사업 관련 정보
- CTA

비즈니스 관련 정보는 선택적인 정보로 취급하며, 비즈니스 웹사이트가 아닌 경우 해당 정보가 없어도 정상적인 Feature Model을 생성할 수 있어야 한다.

---

# 7. Feature Model

Analyzer의 결과는 자유로운 텍스트가 아니라 구조화된 JSON 모델로 관리한다.

예시:

```json
{
  "site": {
    "type": "portfolio",
    "industry": null,
    "brand": {
      "name": "Example",
      "tone": ["modern", "minimal"]
    }
  },
  "content": {
    "blocks": []
  },
  "navigation": {},
  "media": {},
  "business": null,
  "requirements": {
    "must_preserve": [],
    "optional": []
  }
}
```

`site.type`은 특정 유형의 목록에 고정하지 않으며, 원본 페이지의 실제 특성에 따라 결정한다. 유형을 명확하게 판단하기 어려운 경우에도 정상적인 Feature Model을 생성할 수 있어야 한다.

`business`는 선택적인 Feature로 취급한다.

## 7.1 설계 원칙

Feature Model에는 다음 두 종류의 정보가 명확하게 구분되어야 한다.

### 보존해야 하는 정보

예:

- 회사명
- 제품명
- 서비스명
- 연락처
- 핵심 설명
- 주요 링크
- 필수 이미지
- 법적/필수 정보
- 원본 페이지의 핵심 콘텐츠 및 기능

### 재구성 가능한 정보

예:

- 섹션 순서
- 레이아웃
- 카드 형태
- 배치 방식
- 색상
- Typography
- Navigation 형태
- Hero 표현 방식

---

# 8. Design Planner

## 8.1 역할

Design Planner는 고성능 LLM을 사용한다.

핵심 역할은 HTML을 작성하는 것이 아니라 **새로운 디자인의 설계안을 만드는 것**이다.

Design Planner는 특정 웹사이트 유형을 기본값으로 가정하지 않고 Feature Model에 포함된 콘텐츠와 사이트 특성에 따라 디자인 방향과 페이지 구조를 결정한다.

## 8.2 입력

```text
Feature Model
+
Design Constraints
+
Generation Rules
```

## 8.3 출력

구조화된 `DesignSpec`을 생성한다.

예:

```json
{
  "design": {
    "concept": "Editorial Industrial",
    "visual_direction": "...",
    "layout": "asymmetric",
    "color_system": {},
    "typography": {}
  },
  "sections": [
    {
      "id": "hero",
      "purpose": "...",
      "layout": "...",
      "components": []
    },
    {
      "id": "content",
      "purpose": "...",
      "layout": "...",
      "components": []
    }
  ],
  "responsive": {},
  "interaction": {}
}
```

## 8.4 DesignSpec이 결정해야 하는 항목

- 전체 디자인 컨셉
- 페이지 구조
- 섹션 수
- 섹션 순서
- 각 섹션의 목적
- 레이아웃
- 컴포넌트
- 색상 체계
- Typography
- spacing
- visual hierarchy
- interaction
- responsive behavior
- 콘텐츠 배치 방식

---

# 9. HTML Generator

## 9.1 역할

HTML Generator는 상대적으로 저성능인 모델을 사용한다.

Design Planner가 결정한 DesignSpec을 실제 웹페이지로 구현한다.

## 9.2 입력

```text
Feature Model
+
DesignSpec
+
HTML/CSS/JS Constraints
```

## 9.3 출력

완전한 HTML 문서를 생성한다.

```html
<!DOCTYPE html>
<html>
<head>
    ...
</head>
<body>
    ...
</body>
</html>
```

## 9.4 구현 원칙

HTML Generator가 임의로 디자인을 변경하지 않도록 한다.

즉:

```text
DesignSpec
    ↓
Implementation
```

이어야 한다.

DesignSpec 자체를 변경해야 하는 경우에는 Design Planner 단계로 되돌아간다.

---

# 10. Validation

Validator는 가능한 한 deterministic한 Python 코드로 구현한다.

## 10.1 HTML 검증

- 완전한 HTML 문서인지 확인
- parser 오류
- 중복 ID
- 잘못된 DOM 구조
- 필수 태그
- malformed HTML

## 10.2 콘텐츠 검증

Feature Model에 존재하는 필수 콘텐츠가 생성 결과에 존재하는지 확인한다.

예:

```text
원본:
핵심 콘텐츠 = Example

생성:
핵심 콘텐츠 없음

→ validation failure
```

## 10.3 링크 검증

- 필수 링크 존재 여부
- href 누락
- 잘못된 내부 링크
- CTA 링크 누락

## 10.4 미디어 검증

- 필수 이미지 존재 여부
- alt 정보
- 이미지 참조 오류

---

# 11. Evaluator

Validator와 Evaluator는 분리한다.

## Validator

```text
정상인가?
```

## Evaluator

```text
얼마나 좋은가?
```

## 11.1 평가 항목

| 평가 항목 | 점수 |
|---|---:|
| 디자인 완성도 | 0~10 |
| 시각적 계층 | 0~10 |
| 디자인 일관성 | 0~10 |
| 콘텐츠 보존 | 0~10 |
| UX | 0~10 |
| 반응형 설계 | 0~10 |
| 독창성 | 0~10 |
| HTML 품질 | 0~10 |

최종적으로 가중 평균 또는 별도의 scoring policy를 적용한다.

---

# 12. LOOP 및 Iteration

전체 생성은 단일 실행이 아니라 반복 개선을 지원한다.

```text
Design
  ↓
Generate HTML
  ↓
Validate
  ↓
Evaluate
  ↓
Quality sufficient?
  ├─ YES → Final
  └─ NO
       ↓
   Revision
       ↓
      LOOP
```

초기 기본값은 최대 3회 반복을 권장한다.

예:

```python
for attempt in range(max_iterations):
    design = design_planner(...)
    html = html_generator(...)
    validation = validator(html)
    if not validation.valid:
        continue
    evaluation = evaluator(html)
    if evaluation.score >= threshold:
        break
```

단, 실제 구현에서는 이전 결과와 실패 원인을 다음 iteration에 전달하여 단순 재생성이 아니라 **개선 생성**이 되도록 한다.

---

# 13. Recovery

LLM은 항상 정상적인 결과를 생성한다고 가정하지 않는다.

## 13.1 주요 실패 유형

```text
INVALID_HTML
MISSING_CONTENT
MISSING_SECTION
BROKEN_STRUCTURE
CSS_ERROR
JAVASCRIPT_ERROR
DESIGN_MISMATCH
INVALID_DESIGN_SPEC
```

## 13.2 Recovery 전략

### HTML 오류

```text
Generated HTML
      ↓
Validation Failure
      ↓
Recovery Prompt
      ↓
HTML Generator
```

### Design 오류

DesignSpec 자체가 잘못된 경우에는 HTML Generator가 임의로 해결하지 않고 Design Planner 단계로 되돌린다.

```text
DesignSpec
    ↓
Validation
    ↓
Invalid
    ↓
Design Revision
```

---

# 14. Ollama 추상화

Ollama API 호출을 프로젝트 전체에 직접 작성하지 않는다.

권장 구조:

```text
llm/
├── client.py
├── ollama.py
└── models.py
```

인터페이스 예시:

```python
class LLMClient:
    def generate(
        self,
        prompt: str,
        model: str,
        options: dict | None = None
    ) -> str:
        ...
```

이를 통해 향후 Ollama 외의 LLM Backend로 교체할 수 있도록 한다.

---

# 15. 모델 설정

모델명은 코드에 하드코딩하지 않는다.

모델 목록은 YAML 설정 파일에서 역할별로 지정할 수 있도록 한다.

예:

```yaml
models:
  analyzer:
  designer:
  generator:
  evaluator:
```

또는 역할 기반 설정:

```yaml
models:
  high_performance:
  low_performance:
```

모델은 환경에 맞게 교체할 수 있어야 한다.

---

# 16. Prompt 관리

Prompt를 Python 코드에 직접 작성하지 않고 별도의 파일로 관리한다.

```text
prompts/
├── analyzer/
│   └── system.md
├── designer/
│   ├── system.md
│   └── revision.md
├── generator/
│   ├── system.md
│   └── recovery.md
└── evaluator/
    └── system.md
```

Prompt 변경이 코드 변경과 독립적으로 가능하도록 한다.

---

# 17. 권장 프로젝트 구조

```text
wra/
│
├── pyproject.toml
├── README.md
├── project_plan.md
│
├── config/
│   └── config.yaml.sample
│
├── input/
│   └── input.html
│
├── prompts/
│   ├── analyzer/
│   │   └── system.md
│   ├── designer/
│   │   ├── system.md
│   │   └── revision.md
│   ├── generator/
│   │   ├── system.md
│   │   └── recovery.md
│   └── evaluator/
│       └── system.md
│
├── src/
│   └── wra/
│       ├── cli.py
│       │
│       ├── core/
│       │   ├── pipeline.py
│       │   ├── iteration.py
│       │   └── models.py
│       │
│       ├── analyzer/
│       │   ├── html_parser.py
│       │   ├── extractor.py
│       │   └── analyzer.py
│       │
│       ├── designer/
│       │   ├── planner.py
│       │   └── schema.py
│       │
│       ├── generator/
│       │   ├── generator.py
│       │   └── recovery.py
│       │
│       ├── validator/
│       │   ├── html.py
│       │   ├── content.py
│       │   └── structure.py
│       │
│       ├── evaluator/
│       │   └── evaluator.py
│       │
│       ├── llm/
│       │   ├── client.py
│       │   ├── ollama.py
│       │   └── models.py
│       │
│       └── utils/
│           ├── logging.py
│           └── files.py
│
├── tests/
│   ├── test_analyzer.py
│   ├── test_designer.py
│   ├── test_generator.py
│   ├── test_validator.py
│   └── test_pipeline.py
│
└── output/
```

---

# 18. 실행 방식

입력 HTML은 CLI argument로 전달하지 않고 `input/input.html`을 사용한다.

기본 실행:

```bash
redesign
```

Python module 방식:

```bash
python -m wra
```

출력 경로 및 반복 횟수 등 애플리케이션 동작에 필요한 설정은 YAML 설정 파일을 기본으로 사용한다.

---

# 19. 실행 결과 저장

단순히 최종 HTML만 저장하지 않고 전체 실행 결과를 보존한다.

```text
output/
└── run_20260901_012700/
    ├── original.html
    ├── features.json
    │
    ├── design/
    │   ├── iteration_1.json
    │   ├── iteration_2.json
    │   └── iteration_3.json
    │
    ├── html/
    │   ├── iteration_1.html
    │   ├── iteration_2.html
    │   └── iteration_3.html
    │
    ├── validation/
    │   ├── iteration_1.json
    │   └── iteration_2.json
    │
    ├── evaluation/
    │   └── final.json
    │
    └── final.html
```

이를 통해 다음을 추적할 수 있어야 한다.

- 어떤 Feature Model이 생성되었는가
- 어떤 DesignSpec이 생성되었는가
- 어떤 HTML이 생성되었는가
- 어떤 validation 오류가 발생했는가
- 어떤 iteration에서 품질이 개선되었는가

---

# 20. Browser Evaluation

확장 단계에서는 Playwright를 이용해 실제 브라우저에서 HTML을 렌더링한다.

```text
Generated HTML
      ↓
Chromium
      ↓
Rendered Page
      ↓
Screenshot
      ↓
Visual Evaluation
```

이를 통해 단순한 HTML 문법 검사를 넘어 다음을 평가할 수 있다.

- 실제 레이아웃
- overflow
- 요소 겹침
- 반응형 문제
- 빈 공간
- 시각적 균형
- 화면 크기별 UI 상태

이 단계는 최종 디자인 품질 향상을 위한 핵심 확장 기능으로 취급한다.

---

# 21. 개발 단계

## Phase 1 — 프로젝트 기반

목표:

- Python 프로젝트 생성
- 패키지 구조 구축
- Pydantic 모델
- 설정 시스템
- Logging
- CLI
- Ollama Client

완료 조건:

```text
Python → Ollama → 응답
```

까지 정상 동작해야 한다.

---

## Phase 2 — HTML Analyzer

구현:

- HTML parser
- metadata extraction
- heading extraction
- content extraction
- links
- images
- forms
- navigation
- Feature Model
- LLM 기반 semantic analysis

완료 조건:

```text
input/input.html
    ↓
features.json
```

이 생성되어야 한다.

---

## Phase 3 — Design Planner

구현:

- Design prompt
- DesignSpec schema
- Ollama 연동
- JSON validation
- Design recovery

완료 조건:

```text
features.json
    ↓
DesignSpec
```

이 정상적으로 생성되어야 한다.

---

## Phase 4 — HTML Generator

구현:

- DesignSpec → HTML
- CSS
- JavaScript
- HTML validation
- Generator recovery

완료 조건:

```text
DesignSpec
    ↓
valid HTML
```

---

## Phase 5 — Pipeline Loop

구현:

```text
Analyze
  ↓
Design
  ↓
Generate
  ↓
Validate
  ↓
Retry / Revision
```

완료 조건:

하나의 CLI 명령으로 전체 파이프라인이 실행되어야 한다.

---

## Phase 6 — Evaluator

구현:

- 디자인 평가
- 콘텐츠 보존 평가
- UX 평가
- HTML 품질 평가
- score 계산
- best iteration 선택

완료 조건:

반복 결과 중 가장 높은 품질의 결과를 최종 출력할 수 있어야 한다.

---

## Phase 7 — Browser Evaluation

구현:

- Playwright
- Chromium rendering
- screenshot
- viewport별 테스트
- visual evaluation

완료 조건:

실제 브라우저 렌더링 결과를 품질 평가 과정에 반영할 수 있어야 한다.

---

# 22. 테스트 전략

LLM 결과 자체를 완전히 동일하게 만드는 방식의 테스트는 사용하지 않는다.

대신 **계약과 불변 조건**을 테스트한다.

## Analyzer

- HTML 파싱
- 필수 콘텐츠 추출
- 이미지 추출
- 링크 추출
- Feature Model schema
- LLM semantic analysis

## Design Planner

- 유효한 DesignSpec 생성
- 필수 필드
- 잘못된 JSON recovery
- 다양한 section 구조 허용

## HTML Generator

- 완전한 HTML 생성
- 필수 콘텐츠 보존
- DesignSpec의 section 구현
- malformed HTML recovery

## Validator

- 정상 HTML
- 잘못된 HTML
- 콘텐츠 누락
- 링크 누락
- 중복 ID

## Pipeline

- 정상 실행
- validation failure
- retry
- maximum iteration
- 최종 결과 선택

---

# 23. 성능 및 비용 최적화

LLM 호출 횟수와 Context 크기를 최소화한다.

## 원칙

### 1. HTML 전체를 매번 전달하지 않는다

```text
Original HTML
     ↓
Feature Model
     ↓
LLM
```

### 2. Python으로 처리 가능한 것은 Python으로 처리한다

HTML parsing, validation 등은 LLM을 사용하지 않는다.

### 3. 고성능 모델 호출을 최소화한다

고성능 모델은 디자인 결정과 필요한 revision에 집중한다.

### 4. 저성능 모델은 구현에 사용한다

HTML/CSS/JS 생성은 저성능 모델이 담당한다.

### 5. 반복 과정에서는 실패 원인을 전달한다

단순히 동일한 Prompt를 반복하지 않는다.

```text
Iteration 1
   ↓
Failure:
MISSING_CONTENT
CSS_OVERFLOW
   ↓
Iteration 2
   ↓
Correction
```

### 6. 캐싱 고려

동일한 원본 HTML과 동일한 분석 조건에 대해서는 Feature Model을 재사용할 수 있도록 한다.

---

# 24. 향후 확장

## 24.1 Multi-page 지원

현재는 단일 HTML을 기준으로 개발하지만 이후 다음 구조를 지원한다.

```text
Website
├── index.html
├── about.html
├── services.html
└── contact.html
```

페이지 간 Design System을 공유한다.

## 24.2 Design System 공유

여러 페이지가 동일한 다음 요소를 공유하도록 한다.

- color tokens
- typography
- spacing
- components
- navigation
- footer

## 24.3 이미지 분석

이미지 자체를 분석하여 다음 정보를 활용한다.

- 이미지 유형
- 주요 색상
- 이미지 분위기
- 제품/인물/건축물 등 분류

## 24.4 사용자 디자인 조건

CLI에서 사용자가 디자인 방향을 지정할 수 있도록 한다.

예:

```bash
redesign \
    --style "minimal editorial" \
    --theme "dark" \
    --layout "asymmetric"
```

## 24.5 여러 Design 후보 생성

고성능 LLM이 하나의 DesignSpec만 생성하는 대신 여러 후보를 만들고 Evaluator가 선택하도록 확장할 수 있다.

```text
Feature Model
      ↓
 ┌────┼────┐
 ▼    ▼    ▼
D1   D2   D3
 └────┼────┘
      ▼
  Evaluator
      ↓
  Best Design
```

---

# 25. 핵심 성공 기준

프로젝트의 성공 여부는 단순히 HTML이 생성되는지가 아니라 다음 기준으로 판단한다.

### 필수

- [ ] 원본 HTML을 분석할 수 있다.
- [ ] Feature Model을 생성할 수 있다.
- [ ] DesignSpec을 생성할 수 있다.
- [ ] DesignSpec 기반 HTML을 생성할 수 있다.
- [ ] 생성 결과를 자동 검증할 수 있다.
- [ ] 오류 발생 시 recovery할 수 있다.
- [ ] iteration을 수행할 수 있다.
- [ ] 최종 HTML을 안정적으로 출력할 수 있다.

### 품질

- [ ] 원본 핵심 콘텐츠가 보존된다.
- [ ] 원본과 다른 디자인을 생성한다.
- [ ] 페이지 구조가 고정되지 않는다.
- [ ] DesignSpec과 HTML 구현이 일치한다.
- [ ] HTML/CSS/JS 오류가 최소화된다.
- [ ] 반응형 레이아웃을 지원한다.
- [ ] 반복할수록 품질이 개선될 수 있다.

### 확장성

- [ ] Ollama 모델 교체가 쉽다.
- [ ] LLM Backend 교체가 가능하다.
- [ ] Prompt를 독립적으로 수정할 수 있다.
- [ ] Multi-page 지원으로 확장 가능하다.
- [ ] Browser/Visual evaluation을 추가할 수 있다.

---

# 26. 최종 목표 아키텍처

최종적으로 다음 구조를 프로젝트의 핵심 아키텍처로 유지한다.

```text
                    Original HTML
                         │
                         ▼
                ┌───────────────┐
                │ HTML Analyzer │
                │    Python     │
                └───────┬───────┘
                        │
                        ▼
                  Feature Model
                        │
                        ▼
             ┌─────────────────────┐
             │  High Performance   │
             │        LLM          │
             │                     │
             │   Design Planner    │
             └──────────┬──────────┘
                        │
                        ▼
                    DesignSpec
                        │
                        ▼
             ┌─────────────────────┐
             │   Low Performance   │
             │        LLM          │
             │                     │
             │   HTML Generator    │
             └──────────┬──────────┘
                        │
                        ▼
                  Generated HTML
                        │
               ┌────────┴─────────┐
               ▼                  ▼
          Validator           Browser
               │              Rendering
               └────────┬─────────┘
                        ▼
                    Evaluator
                        │
                 ┌──────┴──────┐
                 │             │
                FAIL          PASS
                 │             │
                 ▼             ▼
             Recovery      final.html
                 │
                 └──────→ LOOP
```

---

# 27. 결론

이 프로젝트의 핵심은 **LLM에게 HTML을 직접 재디자인시키는 것이 아니라, 디자인 의사결정과 실제 구현을 분리하는 것**이다.

WRA는 특정 비즈니스 웹사이트에 한정하지 않고 다양한 웹페이지를 대상으로 동작하는 범용 Web Redesigner를 목표로 한다.

핵심 데이터 계약은 다음 세 단계로 유지한다.

```text
FeatureModel
     ↓
DesignSpec
     ↓
HTML
```

각 단계의 책임은 다음과 같다.

```text
FeatureModel
→ 원본의 의미와 요구사항을 표현

DesignSpec
→ 고성능 LLM이 새로운 디자인을 결정

HTML
→ 저성능 LLM이 디자인을 실제 코드로 구현
```

그리고 마지막에:

```text
Validation
+
Evaluation
+
Recovery
+
Iteration
```

을 결합하여 생성 품질을 높인다.

이 구조를 기반으로 구현하면 향후 모델 교체, 병렬 디자인 후보 생성, Browser 기반 시각 평가, Multi-page 웹사이트 생성, Design System 공유 등으로 자연스럽게 확장할 수 있다.
