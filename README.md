# WRA (Web Redesigner AI)

WRA(Web Redesigner AI)는 기존 HTML을 분석하고, 원본의 의미와 핵심 콘텐츠를 보존하면서 새로운 디자인의 HTML을 생성하는 LLM 기반 웹 리디자인 시스템이다.

특정 비즈니스/기업 웹사이트에 한정하지 않고 다양한 유형의 웹페이지를 범용적으로 처리하는 것을 목표로 한다.

## 핵심 구조

```text
input/ (폴더 전체)
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
  ├─ 종료 → <slug>.html
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

Design Planner와 HTML Generator는 오류 유형에 따라 복구 전략을 구분한다. Analyzer semantic analysis와 LLM Evaluator도 JSON/스키마 적용 실패 시 설정된 retry 횟수만큼 compact repair prompt로 재시도한다.

첫 시도의 `num_ctx`는 조립된 프롬프트 길이에서 산출해 `initial_num_ctx`와 `max_num_ctx`(첫 시도는 32768 상한) 사이로 정한다. Ollama는 `num_ctx`를 넘는 프롬프트의 **앞부분**을 버리므로, 모든 단계의 프롬프트는 `데이터 → 지시/출력 계약` 순서로 조립해 role·schema·output contract가 잘려나가지 않게 한다.

```text
Token limit
    → num_ctx 증가
    → 이전 응답을 활용한 재시도

JSON / 구조 / 콘텐츠 오류
    → 이전 응답을 활용한 수정 재시도

Analyzer / Evaluator JSON 오류
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

각 항목에 점수와 feedback을 생성하며 `overall_score`는 `EvaluationResult` 모델이 dimension 점수에서
결정론적으로 다시 계산한다.

### content_preservation의 두 가지 역할

`low`~`high`에서는 9개 항목의 단순 평균이다.

`ultra`와 `madmax`에서는 다르다. 이 단계에서 루브릭은 이미 이 항목을 "주제 정체성이 유지되는가,
사실을 틀리게 말하지 않는가, 없는 에셋을 지어내지 않는가"로 좁혀놓았다. 그것은 품질의 축이 아니라
**제약**이므로, 평균에 섞으면 두 방향으로 왜곡된다.

- 준수한 페이지는 디자인과 무관한 만점을 공짜로 받아 총점이 올라간다.
- 사실을 틀린 페이지는 9분의 1만 잃어서, 시각적으로 뛰어나면 임계값을 통과해 버린다.

그래서 이 두 단계에서는 **평균에서 빼고 상한으로 쓴다.** 나머지 8개 항목의 평균이 총점이 되고,
`content_preservation`이 `CONTENT_GATE_THRESHOLD`(7.0) 미만이면 총점을 그 값으로 제한한다.
준수한 페이지는 자기 디자인 실력만으로 채점되고, 사실을 틀린 페이지는 결정적으로 떨어진다.

실제 평가 기록에 적용해 보면 사실 오류가 확인된 4건 중 3건이 기존 규칙에서는 임계값 8.0을 넘고
있었다(8.11 / 8.11 / 8.22). 게이트 적용 후에는 4.00 / 4.00 / 5.00이 된다.

평가 결과에는 어느 규칙으로 채점됐는지 `content_freedom` 필드로 남는다.

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
  evaluator:
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
    ├── index.html              최종 결과: 페이지별 최고 점수 iteration
    ├── about.html
    ├── assets/                 모든 페이지가 공유하는 미디어·CSS·JS
    ├── data/                   페이지별 원본 자료
    │   ├── index/
    │   │   ├── features.json
    │   │   ├── original.html
    │   │   ├── source.png
    │   │   └── browser/        최고 결과의 브라우저 산출물
    │   └── about/
    │       ├── features.json
    │       ├── original.html
    │       ├── original_history.html   (병합된 경우)
    │       ├── sources.json            (재구성된 경우)
    │       └── source.png
    ├── iterations/             회차별 결과
    │   ├── iter1/
    │   │   ├── index.html      그 회차의 사이트 전체를 그대로 열어볼 수 있다
    │   │   ├── about.html
    │   │   └── data/
    │   │       ├── index/{design,validation,evaluation,browser}.json
    │   │       └── about/...
    │   └── iter2/
    └── result.json             페이지별 최고 회차와 점수
```

`iterations/iter1/index.html`을 그대로 열면 그 회차의 사이트가 링크까지 살아 있는 채로 보인다.
이미지 참조는 저장 시점에 `../../assets/`로 재지정되므로, 에셋을 회차마다 복제하지 않는다.
최종 결과는 `assets/` 바로 옆에 있으므로 참조가 그대로 유효하다.

### best

iteration별 결과를 `iterations/`에 저장하지 않고, 평가 점수를 비교하여 최고 결과만 `<slug>.html`로 저장한다. Browser Evaluation의 screenshot artifact가 있는 경우 최고 결과의 browser artifact를 `data/<slug>/browser/`에 저장한다.

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
cp config/models.yaml.sample config/models.yaml
```

실제 사용할 모델은 `config/models.yaml`에 등록한다. `models.yaml.sample`의 모델 목록은 비어 있다.

```yaml
ollama:
  base_url: "http://localhost:11434"
  keep_alive: "30m"

models:
  analyzer:
    - "qwen3:27b"
  designer:
    - "qwen3:27b"
  generator:
    - "gemma3:12b"
  evaluator:
    - "qwen3:27b"
```

모델명은 `ollama list`에 표시되는 이름과 일치해야 한다.

`ollama.keep_alive`(기본 `"30m"`)는 Ollama가 유휴 모델을 내리기까지의 시간이다. Ollama 기본값은 5분인데,
두 LLM 호출 사이에 browser evaluation이나 저장 단계가 끼면 그보다 쉽게 넘겨 모델이 다시 로드된다.
`null`로 두면 Ollama 자체 기본값을 그대로 따른다.

### 단일 모델 모드

Ollama는 모델이나 `num_ctx`가 바뀔 때마다 러너를 다시 만든다. 파이프라인은 역할별 모델 풀과
프롬프트별 컨텍스트 크기 산정을 쓰므로, 사실상 단계마다 모델이 언로드·재로드된다.
`config/models.yaml`의 `models.single.enabled: true`로 이를 피할 수 있다.

```yaml
models:
  single:
    enabled: false
    name: "gemma4:26b"
    think: false
    timeout: 900
    num_ctx: 32768
```

켜면:

- analyzer, designer, generator, evaluator, vision, creator, refiner의 모든 역할 풀이 이 모델
  하나로 대체되어, 실행 내내 Ollama에는 이 모델 하나만 요청된다.
- `num_ctx`가 `recovery.initial_num_ctx`, `recovery.initial_ceiling_num_ctx`,
  `recovery.max_num_ctx`를 전부 이 값으로 고정해, 모든 요청이 같은 창을 쓰고 러너 하나가
  실행 내내 상주한다.

대가:

- recovery가 응답 잘림에 대해 더 이상 컨텍스트 창을 키울 수 없다. 대신 간결하게 다시 쓰라는
  지시만으로 재시도한다. Generator의 전체 문서 출력이 들어갈 만큼 `num_ctx`를 크게 잡거나,
  `num_ctx: null`로 두어 모델만 고정하고 프롬프트별 크기 산정은 그대로 둔다.
- 고정된 모델이 vision 모델도 겸하므로, `input.vision.enabled`가 켜져 있다면 멀티모달 모델이어야
  한다(아니면 CLI가 경고를 남긴다).
- iteration마다 모델이 바뀌며 생기던 디자인 다양성의 원천 하나를 잃는다.

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

**이름 대조는 표기 차이를 무시한다.** 대소문자·공백·구두점과 뒤에 붙은 종류 표기(`CSS`, `JS`,
`JavaScript`)를 떼고 비교하므로 `TailwindCSS` 하나만 등록해도 모델이 쓰는 `Tailwind`,
`tailwind css`, `TAILWINDCSS`가 모두 같은 항목으로 붙는다. 정확 일치를 먼저 시도하므로
등록된 이름 그대로 쓴 경우가 항상 우선한다.

다만 **칸을 옮겨주지는 않는다.** CSS 프레임워크를 `framework.js`에 적으면 여전히 오류다. 대신
오류 메시지가 어디에 속하는지와 그 칸에서 쓸 수 있는 이름을 알려준다.

```text
JavaScript framework is not configured: Tailwind; TailwindCSS is a configured CSS
framework, so it belongs in the CSS slot; configured JavaScript frameworks are:
GSAP, Svelte, React, ReactDOM, Vue, Angular
```

문제만 말하고 대안을 말하지 않으면 디자이너는 같은 답을 반복하고 재시도를 모두 소진한 채
실행이 죽는다. 실제로 이 실패가 2026-09-05와 09-06에 run을 하나씩 죽였다.

## 입력 및 실행

WRA는 CLI argument를 받지 않고 `input/` 폴더 전체를 입력으로 사용한다. HTML, 이미지, CSS,
JS를 원본 디렉터리 구조 그대로 넣으면 된다.

```text
input/
├── index.html
├── about.html
├── css/
│   └── site.css
├── js/
│   └── site.js
└── src/img/
    ├── logo.png
    └── hero.jpg
```

`_` 또는 `.`으로 시작하는 파일과 폴더는 무시된다.

### creator 모드 — 최소한의 정보로 새로 만들기

`input/`에 **HTML이 없고 브리프만 있으면** 리디자인 대신 생성 모드로 동작한다. 브리프는
`.md`·`.txt`(자유 서술)나 `.yaml`(구조화)이며, 둘 다 있으면 자유 서술을 먼저 읽고 YAML이
모호한 부분을 확정한다.

```text
input/
├── brief.md          몇 줄이면 충분하다
├── logo.png          함께 넣은 이미지는 생성된 페이지가 실제로 사용한다
└── hero.jpg
```

```markdown
대구 달성군에서 25년째 정밀 절삭공구를 유통하는 회사입니다.
엔드밀, 드릴, 홀더, 인서트를 취급하고 재고를 상시 보유합니다.
가장 큰 강점은 당일 출고입니다.
목표는 신규 거래처의 견적 문의를 받는 것입니다.
```

creator가 이 브리프에서 **페이지 구성과 내용**을 정한다 — 어떤 페이지가 필요한지, 각 페이지가
무슨 말을 하는지, 방문자가 무엇을 하게 할지. 그 뒤로는 디자이너·생성기·평가자 루프가
리디자인과 **완전히 동일하게** 돈다. creator가 쓴 것이 그 루프의 "원본"이 된다.

**지어내도 되는 것과 안 되는 것을 가른다.** 섹션 주제, 보조 문구, 버튼 문구는 자유롭게 만든다.
전화번호·이메일·주소·설립연도·거래처명·인증·가격·통계처럼 **확인 가능한 사실은 브리프에
없으면 만들지 않는다.** 여기서 지어낸 숫자는 완성된 페이지가 사실로 진술하게 되기 때문이다.

`input/`에 넣은 이미지는 에셋 목록과 비전 판독을 거쳐 creator에게 "무엇이 찍힌 사진인지"와
함께 제시되고, creator는 그중에서만 고른다. 없는 파일을 참조하는 계획은 거부된다.

페이지 수는 `creator.max_pages`(기본 4)로 제한한다. 채울 내용이 없는 페이지는 거부되므로
브리프가 짧으면 한 장으로 나온다.

```yaml
creator:
  max_pages: 4

models:
  creator:            # 비우면 designer 모델을 쓴다
    - name: "gemma4:26b"
```

creator 모드에서는 `content.freedom`이 **madmax로 고정된다.** 지킬 원본이 없는데 보존을
요구하는 것은 의미가 없고, 낮은 단계는 브리프가 채우지도 않은 컬렉션의 배치를 강제해
불필요한 재시도를 만든다. 정체성 게이트는 그대로 적용된다 — creator가 정한 것을 생성기가
뒤집으면 총점이 제한된다.

브리프와 페이지별 결정은 `run_*/data/brief.json`과 `run_*/data/<slug>/created.json`에 남는다.

### 진입 페이지와 모드

진입 HTML은 `input.entry` → `index.html` → `input.html` → 가장 큰 HTML 순으로 선택된다.
`input.mode`가 몇 장을 리디자인할지 결정한다.

| mode | 동작 | 출력 |
| --- | --- | --- |
| `entry` | 진입 페이지 1장만 | `run_*/<slug>.html` |
| `pages` | 모든 HTML을 각각 독립적으로 | 페이지마다 `run_*/<slug>.html` |
| `site` | 모든 HTML을 하나의 사이트 컨텍스트를 공유하며 | 위와 같고, 내비게이션·시각 언어가 일관됨 |

### 반복은 언덕 오르기다

루프는 생성 → 채점 → 보완 → 채점이지만, 매 걸음을 그대로 받아들이면 랜덤워크가 된다. 두 가지
규율이 붙는다.

**후퇴한 시도는 기반이 되지 않는다.** 다음 수정은 언제나 **그 페이지의 최고 결과**에서 출발한다.
직전 결과에서 출발하면 한 번 무너진 회차가 그 뒤 모든 회차의 토대가 되고, 페이지는 다시 올라오지
못한다. 실제로 8.25에서 4.00으로 떨어진 실행이 그대로 4.00에 갇힌 적이 있다.

버려진 시도도 `iterations/`에는 그대로 남는다. 무엇을 시도했는지는 기록이고, 무엇 위에 쌓을지는
별개의 판단이다. 실패한 이유는 다음 수정 프롬프트에 `rejected_attempt`로 전달된다 — 어떤 항목이
몇 점에서 몇 점으로 떨어졌는지까지.

```json
"rejected_attempt": {
  "iteration": 2, "score": 4.0, "best_score": 8.25,
  "regressed": ["content_preservation: 10 -> 4", "ux: 8 -> 6"],
  "instruction": "An attempt from this same starting point scored lower and was discarded..."
}
```

**정체하면 멈춘다.** `pipeline.termination.patience`회차 동안 최고 점수가 갱신되지 않으면 그
페이지를 마친다. 9회차를 돌면서 2회차 이후 진전이 없던 실행이 있었다 — 회차당 수 분이므로 그대로
낭비다. `0`이면 `max_iterations`만으로 제한하고, `termination.enabled: false`는 무한 실행을
의도한 설정이므로 patience를 무시한다.

임계값 판정도 **최고 점수**로 한다. 이미 기준을 넘긴 페이지는 한 번 미끄러졌다고 계속 돌지 않는다.

### refine 모드 — 다시 만들지 않고 고치기

기본 동작은 회차마다 DesignSpec을 새로 세우고 **HTML을 처음부터 다시 생성**한다. 잘 나온 부분까지
버리고 다시 만드니 걸음이 크고, 그만큼 후퇴도 잦다.

`pipeline.refine.enabled: true`면 2회차부터 디자이너와 생성기를 건너뛰고, **최고 결과를 직접
편집**한다. 리파이너가 받는 것은 그 페이지의 HTML과 평가, 그리고 그 페이지를 만든 DesignSpec이다.

- `Current DesignSpec` — 색·서체·간격·프레임워크가 무엇으로 정해졌는지. 바꿀 대상이 아니라
  **무엇이 이미 확정됐는지 판단하는 근거**다. 이것을 주지 않으면 리파이너는 HTML에서
  역추론해야 하고, 의도된 값과 실수를 구분하지 못한다.
- `weakest_dimensions` — 가장 낮은 항목 N개와 평가자의 근거
- `improvement_suggestions` — 평가자가 제안한 것
- `content_repairs` — 결정론적으로 확인된 오염을 **위치까지 지목한 치환 지시**

프롬프트는 바꾸지 말아야 할 것을 명시한다: 섹션 구조·색상·서체·간격·프레임워크는 이미 정해졌고,
지적되지 않은 문구는 손대지 않는다. 모든 편집은 지목된 약점으로 추적되어야 한다.

**고칠 수 없는 약점은 겨냥하지 않는다.** 평가 항목은 두 갈래다.

| 갈래 | 항목 | 어디서 정해지는가 |
|---|---|---|
| 구조 | `design_completeness`, `visual_hierarchy`, `responsive_design`, `originality` | DesignSpec |
| 국소 | `design_consistency`, `content_preservation`, `ux`, `readability`, `html_quality` | HTML |

리파이너는 섹션 구조도 디자인 시스템도 바꿀 수 없으므로 구조 항목을 올릴 방법이 없다.
그래서 `weakest_dimensions`는 **국소 항목만** 담고, 페이지의 최저점이 구조 항목이면 그 회차는
아예 재설계로 간다. 이 구분이 없으면 리파이너는 자기 계약이 금지한 편집을 요구받고,
아무것도 바꾸지 않거나 건드려도 되는 쪽을 망가뜨린다.

**막히면 탐색으로 돌아간다.** `redesign_after_stalls`회 연속으로 국소 수정이 개선에 실패하면 그
회차는 전체 재설계로 간다. 작은 편집이 몇 번 움직이지 못했다면 문제는 디자인 쪽이라는 뜻이다.
편집이 유효한 HTML로 나오지 않을 때도 그 회차를 버리지 않고 재설계로 넘어간다.

```yaml
pipeline:
  refine:
    enabled: false
    redesign_after_stalls: 2
    weakest_dimensions: 3

models:
  refiner:            # 비우면 generator 모델을 쓴다
    - name: "gemma4:26b"
```

리파이너는 **완성된 원문을 편집**한다. generator 풀의 작은 모델을 그대로 쓰면 디자인을
개선하는 만큼 원문을 흘린다(`과학기술정보통신부장관상` → `과학기술정보통신부장상` 한 글자
누락으로 madmax 게이트가 총점을 4.0으로 눌렀다). designer 급 모델을 지정하는 편이 안전하다.

#### 위치를 지목하는 것의 차이

같은 페이지·같은 오염(`취급품목`이 `취업품목`으로)에 대해 실측한 결과다.

| 리파이너에게 준 것 | 변경된 줄 | 오염 잔존 |
| --- | --- | --- |
| 검출 문구만 (`'취급품목'이 '취업품목'으로`) | 26 / 788 | **2회, 미수정** |
| **위치까지** (`<title>`, `og:title` 인용) | **4 / 788** | **0회** |

첫 번째 경우 모델은 788줄에서 그 단어를 직접 찾아야 했고, `<head>` 안의 `<title>`을 놓쳤다.
위치를 주면 수정 범위가 오히려 좁아지면서(26줄→4줄) 오염은 완전히 사라진다.

### 회차 안의 일관성

페이지가 여럿이면 한 회차 안에서 디자인이 갈라지지 않도록 두 가지를 고정한다.

- **모델은 회차당 한 번 뽑는다.** 페이지마다 뽑으면 같은 회차의 두 페이지가 서로 다른 디자이너·
  생성기 모델로 만들어져, 애초에 같은 사이트로 보일 수 없다. 회차가 바뀌면 다시 뽑으므로
  탐색의 다양성은 그대로다.
- **진입 페이지가 디자인 시스템을 정한다.** 진입 페이지가 회차마다 먼저 설계되고, 그 결과의
  `color_system`·`typography`·`framework`·`motion`·브레이크포인트가 나머지 페이지에 그대로
  고정된다. 섹션 구성과 레이아웃은 페이지마다 자유롭게 정해지므로, 형제 페이지가 복제본이 되지는
  않는다. 바뀌지 않는 것은 사이트를 이루는 재료다.

페이지가 하나뿐이면 둘 다 해당되지 않는다.

반복은 페이지 바깥에서 돈다. 모든 페이지가 iteration 1을 마친 뒤에야 어떤 페이지든 iteration 2로
넘어간다. 종료 판정은 페이지별이라, 먼저 기준에 도달한 페이지는 빠지고 나머지가 계속 반복된다.
`pipeline.termination.enabled: false`면 모든 페이지가 중단할 때까지 함께 개선된다.

완성된 각 페이지는 `run_*/<slug>.html`에 놓여 공유 `assets/` 옆에서 페이지 간 링크가 그대로
동작한다. 저장되는 것은 마지막 iteration이 아니라 **최고 점수 iteration**이다. `site`는 여기에 더해 형제 페이지 목록을 디자인
제약으로 주입해 내비게이션과 시각 언어를 일관되게 유지한다.

### 사이트 재구성 (madmax 전용)

`content.freedom: madmax`이고 `input.mode`가 `pages` 또는 `site`이면, 리디자인은 페이지 구성
자체를 바꿀 수 있다. 소스 분석이 끝난 뒤 사이트 플래너가 각 페이지의 실제 내용(제목, 헤딩,
문단·목록·표·이미지 개수, 본문 분량)을 보고 출력 페이지 집합을 정한다.

- **유지** — 이미 역할이 분명한 페이지는 그대로 둔다.
- **병합** — 얇거나 겹치거나 이유 없이 쪼개진 페이지들을 하나로 합친다.
- **분할** — 서로 무관한 여러 역할을 한 페이지가 떠안고 있고 각각을 채울 내용이 충분하면 나눈다.

지켜지는 제약:

- **모든 소스 페이지는 최소 한 출력 페이지의 `sources`에 나타나야 한다.** 재구성은 내용을
  재배치하는 것이지 버리는 것이 아니며, 빠진 페이지가 있으면 계획이 거부되고 재시도한다.
- 존재하지 않는 소스를 참조하거나, 슬러그가 중복되거나, 진입 페이지가 정확히 하나가 아니면
  거부된다.
- 계획이 끝내 실패하면 **원본 페이지 구성 그대로 진행한다.** 재구성은 개선이지 필수가 아니다.

한 소스가 여러 출력 페이지를 먹이는 경우(분할), 각 페이지는 `content_focus`를 디자인 제약으로
받는다. 이것이 없으면 두 페이지가 같은 내용을 반복한다. 병합된 페이지는 소스들의 FeatureModel을
합쳐서 받고, 원본은 `original.html`·`original_<slug>.html`로, 구성 근거는 `sources.json`으로
출력 디렉터리에 남는다.

페이지가 병합되어 사라지면 **그 페이지를 가리키던 링크는 흡수한 페이지로 자동 재지정된다.**
`about.html`이 `company.html`로 병합되면 FeatureModel의 `about.html` 링크가 `company.html`로
바뀌며, 앵커(`#awards`)는 유지되고 외부 URL은 건드리지 않는다. 이것이 없으면 생성기가
FeatureModel의 링크를 그대로 옮겨 적어 존재하지 않는 파일로 가는 링크가 남는다.

`input.mode: entry`는 진입 페이지 한 장만 다루는 모드이므로 재구성하지 않는다.

### 에셋 처리

원본이 `/src/img/logo.png` 같은 절대경로를 쓰더라도 로컬에 그 경로가 없을 수 있으므로,
참조는 **파일명 기준**으로 실제 파일과 연결된다. 모든 미디어는 `run_*/assets/`로 복사되고
HTML의 `src`·`srcset`·`url()`·favicon 경로가 `assets/<파일명>`으로 재작성된다. 따라서
최종 결과와 브라우저 스크린샷 양쪽에서 이미지가 정상 표시된다.

### 소스 에셋 분석

CSS·JS·미디어는 **추출 단계에서 1회** 분석되어 `FeatureModel.source_assets`에 압축 저장되고,
이후 모든 iteration이 이를 재사용한다. 원본 CSS와 JS 텍스트는 어떤 프롬프트에도 들어가지 않는다.

- `styles` — 소스의 디자인 토큰. `var()`로 정의된 커스텀 프로퍼티를 해석해
  `--navy: #2B4884`처럼 작성자의 이름 그대로 팔레트·폰트·타입스케일·간격·라디우스·
  브레이크포인트를 뽑는다.
- `scripts` — 원본 JS가 *무엇을 했는지*(라이브러리, 인터랙션 종류). 코드는 담지 않는다.
- `assets` — 실제 픽셀 크기까지 읽은 미디어 목록. 이미지 헤더를 직접 파싱하므로 별도
  의존성이 없다. `usable_as`가 업스케일 없이 쓸 수 있는 자리를 알려준다.
- `impression` — 원본 페이지를 렌더링해 찍은 스크린샷을 비전 모델이 읽은 총평.
  `weaknesses`는 이번 리디자인이 고쳐야 할 것, `strengths`는 버리면 안 되는 것이다.

비전 분석은 `input.vision`으로 제어하며 `models.vision`이 비어 있으면 건너뛴다. 결과는
파일 내용 해시로 캐시되므로 같은 입력을 다시 돌릴 때 비전 호출 비용이 들지 않는다.

이미지는 한 장에 한 번씩 호출한다. 여러 장을 한 프롬프트에 묶지 않는 이유는 설명이 어느
파일 것인지 뒤섞이고, 파일 단위 캐시와 장별 실패 격리가 함께 무너지기 때문이다. 대신
`input.vision.concurrency`로 동시 호출 수를 올릴 수 있다. 다만 실제 이득은 Ollama 서버의
`OLLAMA_NUM_PARALLEL` 슬롯 수에 달려 있어, 슬롯이 하나면 요청이 큐잉될 뿐이므로 기본값은 1이다.

원본 페이지 스크린샷은 그냥 찍으면 안 된다. 스크롤 리빌 애니메이션은 대상을 `opacity: 0`으로
시작시키므로, 손대지 않은 전체 페이지 캡처는 대부분 빈 화면이 된다. 그래서 캡처 전에 두 단계를
거친다.

1. **스크롤 통과** — 방문자가 하듯 페이지를 한 화면씩 내렸다 올린다. 사이트 자신의 리빌 로직
   (IntersectionObserver, 스크롤 핸들러)이 실행되어 원본이 의도한 모습 그대로 드러난다.
2. **남은 것 강제 표시** — 그래도 투명한 채로 남은 요소(리빌 스크립트가 아예 돌지 않은 경우)를
   직접 보이게 하고, 트랜지션·애니메이션을 정지시켜 캡처를 안정시킨다.

`display: none`이나 `visibility: hidden`인 요소는 건드리지 않는다. 그것들은 의도적으로 감춘
것(드롭다운, 다이얼로그)이며 억지로 열면 원본을 드러내는 게 아니라 왜곡하게 된다.

`input.vision.reveal_animations: false`로 끌 수 있다.

`.ico`와 `.svg`는 비전 분석에서 제외된다. Ollama가 디코드하지 못해 파일마다 400만 돌려받는다.
두 형식 모두 에셋 목록에는 그대로 남고, 크기 정보도 헤더에서 읽어 채운다.

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
config/models.yaml
config/frameworks.yaml
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
│   ├── config.yaml.sample
│   ├── models.yaml.sample
│   └── frameworks.yaml
├── input/
│   └── (html, css, js, images)
├── prompts/
│   ├── refiner/
│   │   └── system.md
│   ├── creator/
│   │   └── system.md
│   ├── analyzer/
│   │   ├── system.md
│   │   ├── vision_image.md
│   │   └── vision_page.md
│   ├── designer/
│   │   ├── site_plan.md
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

- [x] `input/` 폴더 기반 입력 (html, css, js, 미디어)
- [x] 소스 에셋 다이제스트 (CSS 디자인 토큰, JS 행위, 이미지 실측 크기)
- [x] 미디어·원본 페이지 비전 분석 (캐시 포함)
- [x] 다중 페이지 모드 (`entry` / `pages` / `site`)
- [x] madmax 사이트 재구성 (페이지 병합·분할)
- [x] creator 모드 (브리프에서 사이트 생성)
- [x] 언덕 오르기 반복 (후퇴 거부, 정체 중단)
- [x] refine 모드 (국소 수정, 막히면 재설계로 복귀)
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
- [x] ultra/madmax에서 content_preservation을 제약 게이트로 처리
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
