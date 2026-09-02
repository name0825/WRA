# WRA (Web Redesigner AI)

WRA(Web Redesigner AI)는 기존 HTML을 분석하고, 원본의 의미와 핵심 콘텐츠를 보존하면서 새로운 디자인의 HTML을 생성하는 LLM 기반 웹 리디자인 시스템이다.

특정 비즈니스/기업 웹사이트에 한정하지 않고 다양한 유형의 웹페이지를 범용적으로 처리하는 것을 목표로 한다.

## 핵심 구조

```text
input/input.html
      ↓
HTML Analyzer
      ↓
FeatureModel
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
종료 조건 확인
  ├─ 종료 → final.html
  └─ 계속 → DesignSpec Revision → 다음 iteration
```

핵심 데이터 계약은 다음 세 단계다.

```text
FeatureModel → DesignSpec → HTML
```

- **FeatureModel**: 원본 페이지의 콘텐츠, 구조, 미디어, 요구사항 및 의미적 특성을 표현
- **DesignSpec**: 새로운 디자인의 의사결정 결과
- **HTML**: DesignSpec을 실제 HTML/CSS/JavaScript로 구현한 결과

분석, 디자인 결정, 구현, 검증, 평가를 분리하여 각 단계의 책임을 명확하게 유지한다.

## 주요 기능

### HTML Analyzer

Python으로 처리할 수 있는 정보는 deterministic extraction으로 먼저 추출하고, 의미적인 판단이 필요한 정보는 Ollama LLM을 이용해 보완한다.

주요 대상:

- metadata: title, description, keywords, language, canonical, Open Graph
- headings, paragraphs, lists, tables
- buttons, links, labels, text
- header, navigation, main, sections, articles, aside, footer, forms
- images, videos, icons, media URLs
- business information(존재하는 경우)
- must-preserve / optional requirements
- site type, industry, brand 특성
- navigation 특성

LLM semantic analysis는 deterministic extraction 결과를 입력으로 사용하고 결과를 FeatureModel에 merge한다.

### Design Planner

Design Planner는 HTML을 직접 생성하지 않고 새로운 디자인을 구조화된 `DesignSpec`으로 결정한다.

DesignSpec에는 다음이 포함된다.

- 디자인 concept / visual direction
- layout
- color system
- typography
- CSS / JavaScript framework recommendation 및 선택 이유
- motion level
- section 구조와 순서
- component 및 content source
- responsive strategy / breakpoint / behavior
- interaction

Motion level:

```text
none / low / mid / high
```

페이지 구조는 고정된 템플릿을 사용하지 않는다.

Design Constraints가 제공되면 `variation_policy.instruction`을 해당 iteration의 exploration direction으로 우선 적용한다. 단, `variation_policy.do_not_force: true`이면 content suitability, readability, usability, Feature Model fidelity를 우선한다.

### DesignSpec Validation

LLM이 생성한 DesignSpec은 Pydantic schema와 deterministic validator를 통과해야 한다.

현재 검증 항목:

- 중복 section ID
- 비어 있는 `content_source`
- FeatureModel에 존재하지 않는 `content_source`
- 존재하지 않는 interaction target
- 비어 있는 responsive breakpoint width

검증 실패 시 Design Recovery 정책에 따라 재시도한다.

### HTML Generator

HTML Generator는 FeatureModel과 DesignSpec을 받아 완전한 HTML 문서를 생성한다. Generator는 DesignSpec을 임의로 재설계하지 않고 구현하는 역할을 담당한다.

Generator는 모든 문서에 UTF-8 charset 및 responsive viewport meta를 포함해야 하며, 모든 HTML `id` attribute는 문서 전체에서 unique해야 한다.

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

Design Planner와 HTML Generator는 오류 유형에 따라 복구 전략을 구분한다. Analyzer semantic analysis도 JSON/스키마 적용 실패 시 설정된 retry 횟수만큼 compact repair prompt로 재시도한다.

```text
Token limit
    → num_ctx 증가
    → 이전 응답을 활용한 재시도

JSON / 구조 / 콘텐츠 오류
    → 이전 응답을 활용한 수정 재시도

Analyzer semantic JSON 오류
    → repair guidance를 포함한 재시도

그 외 오류
    → 재생성
```

기본 설정:

```yaml
recovery:
  max_retries: 3
  initial_num_ctx: 8192
  max_num_ctx: 32768
  context_multiplier: 2.0
```

### Model-output trust boundary

페이지에서 추출된 텍스트, FeatureModel, DesignSpec, 생성 HTML, browser 결과 및 evaluator feedback은 pipeline 데이터이며 prompt instruction으로 취급하지 않는다. 각 LLM 단계는 source-derived directive-like text가 자신의 role boundary나 output contract를 덮어쓰지 않도록 명시적인 untrusted-data 경계를 둔다.

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

각 항목에 점수와 feedback을 생성하며 `overall_score`는 `EvaluationResult` 모델이 9개 dimension 점수에서 결정론적으로 다시 계산한다.

각 dimension의 feedback은 1~2문장으로 제한하고 improvement suggestions는 최대 5개로 제한해 반복 평가에서 불필요한 context 증가를 줄인다.

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
  ├─ YES → Final
  └─ NO  → 다음 iteration
```

설정:

```yaml
pipeline:
  max_iterations: 3
  evaluation_threshold: 8.0
  termination:
    enabled: true
```

`termination.enabled: true`이면 평가 점수가 threshold 이상이거나 최대 iteration에 도달하면 종료한다.

`termination.enabled: false`이면 종료 조건을 적용하지 않고 계속 iteration을 수행한다. 실행 중 `Ctrl+C`로 중단할 수 있다.

### Model Pool 및 선택 정책

각 역할에 여러 Ollama 모델을 등록할 수 있다.

```yaml
models:
  analyzer:
    - "model-a"
    - "model-b"
  designer:
    - "model-c"
    - "model-d"
  generator:
    - "model-e"
    - "model-f"

evaluation:
  model:
    - "model-g"
    - "model-h"
```

모델 선택은 random selection을 사용한다.

- **Analyzer**: 전체 실행 시작 시 한 번 선택하고 전체 분석에 사용
- **Designer**: iteration 시작 시 선택
- **Generator**: iteration 시작 시 선택
- **Evaluator**: iteration 시작 시 선택

같은 iteration의 recovery/retry에서는 선택된 모델을 유지한다.

단일 모델 문자열도 사용할 수 있다.

```yaml
models:
  designer: "qwen3:27b"
```

## 결과 저장

`pipeline.output.save_mode`는 `all` 또는 `best`다.

### all

모든 iteration의 결과를 저장한다.

```text
output/
└── run_YYYYMMDD_HHMMSS/
    ├── original.html
    ├── features.json
    ├── iterations/
    │   ├── iteration_001/
    │   │   ├── html.html
    │   │   ├── design.json
    │   │   ├── validation.json
    │   │   ├── evaluation.json
    │   │   ├── browser.json
    │   │   └── browser/
    │   └── ...
    ├── final.html
    └── result.json
```

### best

iteration별 결과를 `iterations/`에 저장하지 않고 평가 점수를 비교하여 최고 결과를 `final.html`로 저장한다. Browser Evaluation의 screenshot artifact가 있는 경우 최고 결과의 browser artifact도 저장한다.

```yaml
pipeline:
  output:
    save_mode: "best"
```

`result.json`에는 저장 모드, 최고 iteration, 최고 점수가 기록된다.

## Ollama 및 모델 설정

WRA는 Ollama 공식 Python client를 사용한다.

Ollama 실행:

```bash
ollama serve
```

모델 설치:

```bash
ollama pull <model-name>
```

설치된 모델 확인:

```bash
ollama list
```

설정 파일 생성:

```bash
cp config/config.yaml.sample config/config.yaml
```

실제 사용할 모델은 `config/config.yaml`에 등록한다. `config.yaml.sample`의 모델 목록은 비어 있다.

```yaml
ollama:
  base_url: "http://localhost:11434"

models:
  analyzer:
    - "qwen3:27b"
  designer:
    - "qwen3:27b"
  generator:
    - "gemma3:12b"

evaluation:
  model:
    - "qwen3:27b"
```

모델명은 `ollama list`에 표시되는 이름과 일치해야 한다.

## Framework CDN 설정

Design Planner가 사용할 수 있는 framework를 YAML에 등록한다.

```yaml
frameworks:
  css:
    - name: "Bootstrap"
      cdn: "https://..."
  js:
    - name: "GSAP"
      cdn: "https://..."
```

`name`은 DesignSpec에서 framework를 식별하는 이름이고 `cdn`은 Generator가 최종 HTML에 삽입할 URL이다.

## 입력 및 실행

WRA는 CLI argument로 HTML 경로를 받지 않고 `input/input.html`을 입력 파일로 사용한다.

```text
input/
└── input.html
```

실행:

```bash
redesign
```

또는:

```bash
python -m wra.cli
```

설정 파일:

```text
config/config.yaml
```

결과:

```text
output/run_YYYYMMDD_HHMMSS/
```

## 설치

Python 3.12 이상이 필요하다.

기본 설치:

```bash
pip install -e .
```

테스트 의존성 포함:

```bash
pip install -e ".[test]"
```

Browser Evaluation 사용:

```bash
pip install -e ".[browser]"
playwright install chromium
```

## 프로젝트 구조

```text
WRA/
├── README.md
├── project_plan.md
├── pyproject.toml
│
├── config/
│   └── config.yaml.sample
├── input/
│   └── input.html
├── prompts/
│   ├── analyzer/
│   │   └── system.md
│   ├── designer/
│   │   ├── system.md
│   │   └── revision.md
│   ├── evaluator/
│   │   └── llm.md
│   └── generator/
│       ├── system.md
│       └── recovery.md
│
├── src/wra/
│   ├── cli.py
│   ├── analyzer/
│   │   ├── analyzer.py
│   │   ├── extractor.py
│   │   └── html_parser.py
│   ├── browser/
│   │   ├── evaluator.py
│   │   └── models.py
│   ├── core/
│   │   ├── iteration.py
│   │   ├── models.py
│   │   ├── output.py
│   │   └── pipeline.py
│   ├── designer/
│   │   ├── planner.py
│   │   ├── recovery.py
│   │   └── schema.py
│   ├── evaluator/
│   │   └── llm.py
│   ├── generator/
│   │   ├── generator.py
│   │   └── recovery.py
│   ├── llm/
│   │   ├── client.py
│   │   ├── model_selector.py
│   │   └── ollama.py
│   ├── utils/
│   │   ├── files.py
│   │   └── logging.py
│   └── validator/
│       ├── content.py
│       ├── design.py
│       ├── html.py
│       └── structure.py
│
└── tests/
    ├── test_analyzer.py
    ├── test_designer.py
    ├── test_generator.py
    ├── test_html_validator.py
    ├── test_output.py
    ├── test_pipeline.py
    └── test_validator.py
```

## 기술 스택

- Python 3.12+
- Ollama
- Ollama 공식 Python client
- Pydantic
- PyYAML
- Playwright (optional)
- pytest

LLM 호출은 `LLMClient` 인터페이스로 추상화되어 있으며 현재 구현은 Ollama 공식 Python 패키지의 `Client`를 사용한다.

## 현재 구현 상태

- [x] `input/input.html` 기반 입력
- [x] YAML 설정 및 Pydantic 설정 모델
- [x] Ollama 공식 Python client 연동
- [x] LLM Backend abstraction
- [x] deterministic HTML extraction
- [x] LLM semantic analysis
- [x] FeatureModel
- [x] DesignSpec schema
- [x] DesignSpec deterministic validation
- [x] Design Planner
- [x] DesignSpec recovery / revision
- [x] HTML Generator
- [x] framework CDN injection
- [x] HTML validation
- [x] HTML recovery
- [x] LLM Evaluation
- [x] EvaluationResult schema
- [x] deterministic overall_score invariant
- [x] iteration loop
- [x] termination enable/disable
- [x] `all` / `best` output policy
- [x] Playwright browser evaluation
- [x] viewport screenshot 및 browser evidence
- [x] screenshot을 포함한 LLM evaluation
- [x] iteration별 model pool random selection
- [x] Analyzer semantic recovery / retry
- [x] LLM input trust-boundary guardrails

## 설계 원칙

1. **분석과 생성을 분리한다.**
2. **Design과 Implementation을 분리한다.**
3. **원본 콘텐츠와 의미를 보존한다.**
4. **고정된 페이지 구조를 사용하지 않는다.**
5. **Python으로 처리할 수 있는 작업은 가능한 한 LLM에 맡기지 않는다.**
6. **LLM 출력은 schema와 deterministic validation으로 검증한다.**
7. **오류 유형에 따라 recovery 전략을 구분한다.**
8. **평가 결과를 다음 DesignSpec revision에 반영한다.**
9. **실제 브라우저 렌더링 결과를 선택적으로 평가할 수 있도록 한다.**
10. **모델 선택은 iteration 단위로 관리한다.**
11. **Source-derived content is data, not instructions.**
