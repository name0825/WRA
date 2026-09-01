# WRA (Web Redesigner AI)

WRA(Web Redesigner AI)는 기존 HTML을 분석하고, 원본의 의미와 핵심 콘텐츠를 보존하면서 새로운 디자인의 HTML을 생성하는 LLM 기반 웹 리디자인 시스템이다.

특정 비즈니스/기업 웹사이트만을 대상으로 하지 않으며, 다양한 유형의 웹페이지를 범용적으로 처리하는 것을 목표로 한다.

## 핵심 구조

```text
Original HTML
     ↓
HTML Analyzer
     ↓
FeatureModel
     ↓
Design Planner (High-performance LLM)
     ↓
DesignSpec
     ↓
HTML Generator (Lower-cost LLM)
     ↓
Generated HTML
     ↓
HTML Validator
     ↓
Browser Evaluation (optional)
     ↓
LLM Evaluator
     ↓
Quality sufficient?
   ├─ YES → Final HTML
   └─ NO  → DesignSpec Revision → next iteration
```

WRA의 핵심 데이터 계약은 다음 세 단계다.

```text
FeatureModel → DesignSpec → HTML
```

- **FeatureModel**: 원본 페이지의 콘텐츠, 구조, 미디어, 요구사항 및 사이트 특성을 표현
- **DesignSpec**: 새로운 디자인의 의사결정 결과
- **HTML**: DesignSpec을 실제 HTML/CSS/JavaScript로 구현한 결과

## 주요 기능

### HTML 분석

Python 기반의 deterministic extraction과 선택적인 LLM semantic analysis를 조합한다.

추출/분석 대상에는 다음이 포함된다.

- 문서 metadata
- headings / paragraphs / lists / tables
- buttons / links / labels
- header / navigation / main / sections / article / aside / footer / forms
- images / videos / icons / media URLs
- 사이트 유형 및 특성
- 비즈니스 정보(존재하는 경우)
- 보존해야 하는 콘텐츠 및 요구사항

LLM 분석은 Python으로 직접 추출할 수 있는 정보를 다시 LLM에 맡기지 않고, 의미적인 판단이 필요한 부분에 사용한다.

### Design Planning

Design Planner는 HTML을 직접 작성하지 않는다. FeatureModel을 바탕으로 다음을 결정한다.

- 디자인 컨셉 및 visual direction
- 전체 layout
- 페이지 section 구조와 순서
- component 및 content source
- color system
- typography
- responsive strategy
- interaction
- CSS framework / JavaScript framework 또는 vanilla 선택
- framework 선택 이유
- motion level

Motion level은 다음 네 가지 중 하나다.

```text
none / low / mid / high
```

페이지 구조는 고정된 `Header → Hero → About → Services → Contact → Footer` 템플릿을 사용하지 않는다.

### HTML Generation

HTML Generator는 DesignSpec과 FeatureModel을 받아 실제 HTML/CSS/JavaScript를 생성한다.

Generator는 DesignSpec을 임의로 재설계하지 않고 지정된 디자인을 구현하는 역할을 담당한다.

CSS/JavaScript framework는 사용자가 YAML에 등록한 CDN URL을 기반으로 삽입한다.

### DesignSpec Validation

LLM이 생성한 DesignSpec은 Pydantic schema와 별도의 deterministic validator를 거친다.

검증 대상에는 다음이 포함된다.

- 중복 section ID
- 비어 있거나 잘못된 `content_source`
- 존재하지 않는 interaction target
- 잘못된 responsive breakpoint
- Pydantic schema 오류

### HTML Validation 및 Recovery

생성된 HTML은 Python validator를 통해 검증한다.

- DOCTYPE
- HTML/DOM 구조
- 필수 콘텐츠
- 필수 링크
- 필수 미디어
- 구조 관련 오류

Generator Recovery는 오류 유형에 따라 전략을 분리한다.

```text
Token limit
    → num_ctx 증가

구조/콘텐츠 오류
    → 이전 응답을 활용한 수정 재시도

기타 오류
    → 재생성
```

### LLM Evaluation

평가 모델은 별도로 YAML에서 지정할 수 있다.

현재 평가 항목:

| 항목 | 범위 |
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

각 항목에 대한 feedback과 전체 `overall_score`, summary, improvement suggestions를 생성한다.

`overall_score`는 9개 평가 항목의 산술 평균을 사용한다.

### DesignSpec Revision

평가 결과가 종료 조건을 만족하지 못하면 이전 결과를 처음부터 다시 생성하지 않고, 평가 결과를 이용해 기존 DesignSpec을 수정한다.

```text
Previous FeatureModel
       +
Previous DesignSpec
       +
Previous HTML
       +
EvaluationResult
       ↓
DesignSpec Revision
       ↓
HTML Generation
```

### Browser Evaluation

선택적으로 Playwright/Chromium을 이용해 실제 브라우저에서 생성된 HTML을 렌더링한다.

기본 viewport:

- Desktop: 1440×900
- Tablet: 768×1024
- Mobile: 390×844

수집하는 browser evidence:

- screenshot
- horizontal overflow
- scroll width
- viewport width
- page height
- console errors
- JavaScript page errors

생성된 screenshot과 browser 결과는 LLM Evaluator에 전달할 수 있다.

## Iteration

생성 과정은 반복 실행을 지원한다.

```text
Design
 ↓
Generate
 ↓
Validate
 ↓
Browser Evaluation (optional)
 ↓
Evaluate
 ↓
Threshold / iteration limit 확인
 ├─ 종료
 └─ Revision → 다음 iteration
```

`pipeline.termination.enabled`가 `true`이면 평가 threshold 또는 최대 iteration에 도달했을 때 종료한다.

`false`이면 종료 조건을 적용하지 않고 계속 생성하며, 실행 중 `Ctrl+C`로 중단할 수 있다.

## 결과 저장

출력 저장 정책은 `all`과 `best`를 지원한다.

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

각 iteration의 결과를 메모리에서 비교하고 가장 높은 평가 점수의 HTML을 최종 결과로 저장한다.

```text
output/
└── run_YYYYMMDD_HHMMSS/
    ├── original.html
    ├── features.json
    ├── final.html
    ├── result.json
    └── browser/              # best 결과의 browser artifact가 있는 경우
```

`result.json`에는 저장 모드, 최고 iteration, 최고 점수를 기록한다.

## 프로젝트 구조

```text
WRA/
├── README.md
├── project_plan.md
├── pyproject.toml
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
│   ├── evaluator/
│   │   ├── llm.md
│   │   └── system.md
│   └── generator/
│       ├── system.md
│       └── recovery.md
│
├── src/wra/
│   ├── cli.py
│   ├── analyzer/
│   │   ├── html_parser.py
│   │   ├── extractor.py
│   │   └── analyzer.py
│   ├── browser/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   └── evaluator.py
│   ├── core/
│   │   ├── models.py
│   │   ├── pipeline.py
│   │   ├── iteration.py
│   │   └── output.py
│   ├── designer/
│   │   ├── schema.py
│   │   ├── planner.py
│   │   └── recovery.py
│   ├── evaluator/
│   │   ├── evaluator.py
│   │   └── llm.py
│   ├── generator/
│   │   ├── generator.py
│   │   └── recovery.py
│   ├── llm/
│   │   ├── client.py
│   │   ├── models.py
│   │   └── ollama.py
│   ├── utils/
│   │   ├── files.py
│   │   └── logging.py
│   └── validator/
│       ├── html.py
│       ├── content.py
│       ├── structure.py
│       └── design.py
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

LLM Backend는 `LLMClient` 추상화 뒤에 위치하며 현재 구현은 Ollama 공식 Python client를 사용한다.

## 설정

실제 설정 파일은 다음 위치에 둔다.

```text
config/config.yaml
```

템플릿:

```bash
cp config/config.yaml.sample config/config.yaml
```

모델 목록은 환경별로 지정할 수 있도록 sample에서는 비워 둔다.

```yaml
ollama:
  base_url: "http://localhost:11434"

models: {}

evaluation:
  model: ""

browser:
  enabled: false
  timeout_ms: 10000
  screenshot: true
  viewports:
    - name: desktop
      width: 1440
      height: 900
    - name: tablet
      width: 768
      height: 1024
    - name: mobile
      width: 390
      height: 844

frameworks:
  css: []
  js: []

pipeline:
  max_iterations: 3
  evaluation_threshold: 8.0

  termination:
    enabled: true

  output:
    save_mode: "all"
```

Framework를 사용하려면 사용자가 이름과 CDN URL을 YAML에 등록한다.

```yaml
frameworks:
  css:
    - name: "Bootstrap"
      cdn: "https://..."
  js:
    - name: "GSAP"
      cdn: "https://..."
```

Design Planner가 해당 framework를 선택하면 Generator가 등록된 CDN URL을 HTML에 삽입한다.

## 설치

기본 의존성:

```bash
pip install -e .
```

테스트 의존성:

```bash
pip install -e ".[test]"
```

Browser Evaluation 사용 시:

```bash
pip install -e ".[browser]"
playwright install chromium
```

Ollama가 실행 중이어야 하며, 사용할 모델은 `config/config.yaml`에서 지정한다.

## 실행

WRA는 `input/input.html`을 입력 파일로 사용한다.

```text
input/
└── input.html
```

CLI 실행:

```bash
redesign
```

또는:

```bash
python -m wra.cli
```

실행 시 결과는 `output/run_YYYYMMDD_HHMMSS/` 아래에 저장된다.

## 테스트

```bash
pytest
```

테스트는 LLM 응답 자체를 동일하게 만드는 것이 아니라 각 단계의 schema, 계약, 불변 조건 및 deterministic 동작을 검증하는 방향으로 구성한다.

## 현재 구현 상태

현재 저장소에는 다음 핵심 기능이 구현되어 있다.

- [x] input/input.html 기반 입력
- [x] YAML 설정
- [x] Ollama 공식 Python client 연동
- [x] deterministic HTML 분석
- [x] LLM semantic analysis
- [x] FeatureModel
- [x] DesignSpec schema
- [x] DesignSpec validation
- [x] Design Planner
- [x] DesignSpec revision
- [x] Design recovery
- [x] HTML Generator
- [x] framework CDN injection
- [x] HTML validation
- [x] HTML recovery
- [x] LLM Evaluation
- [x] Evaluation 기반 iteration
- [x] `all` / `best` output policy
- [x] optional Playwright browser evaluation
- [x] viewport screenshots 및 browser evidence
- [x] screenshot 기반 LLM evaluation 전달

일부 모듈과 테스트 파일은 아직 확장 단계에 있으며, 특히 `evaluator/evaluator.py`, `llm/models.py`, `utils/files.py`, `tests/test_validator.py`는 현재 저장소에서 실질적인 구현이 비어 있거나 제한적이다.

## 설계 원칙

WRA는 다음 원칙을 유지한다.

1. **분석과 생성을 분리한다.**
2. **Design과 Implementation을 분리한다.**
3. **원본 콘텐츠와 의미를 보존한다.**
4. **페이지 구조를 고정하지 않는다.**
5. **Python으로 처리할 수 있는 작업은 가능한 한 LLM에 맡기지 않는다.**
6. **LLM 출력은 schema와 deterministic validation으로 검증한다.**
7. **실패 유형에 따라 recovery 전략을 구분한다.**
8. **평가 결과를 다음 DesignSpec revision에 반영한다.**
9. **Browser evaluation은 실제 렌더링 결과를 품질 평가에 활용한다.**
10. **LLM 및 framework 설정은 코드와 분리한다.**

자세한 설계 및 개발 계획은 [`project_plan.md`](project_plan.md)를 참고한다.
