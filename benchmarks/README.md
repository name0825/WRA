# 벤치마크 입력 샘플

리디자인 품질(시각 디자인/UI 참신성 우선, 다음 UX)을 측정하기 위한 고정 소스 사이트 세트입니다.
각 샘플은 의도적으로 2008년 무렵의 표/폰트 기반 구식 레이아웃이지만, 사실 관계는 풍부하고 일관성 있게 구성했습니다.

| 샘플 | 언어 | 주요 스트레스 포인트 |
|---|---|---|
| `bakery` | 한국어 | 숫자 사실(가격/영업시간), 날짜-항목 짝, 이미지 처리 |
| `research-lab` | 영어+한국어 혼용 | 텍스트 밀도, 긴 목록, 날짜/학회/연도 짝, 혼용 언어 보존 |
| `saas-product` | 영어 | 내비게이션/앵커, CTA, 인터랙션(FAQ 토글), 전환 UX |
| `reference-polished` | 한국어 | 이미 완성도 높은 디자인 — 평가 상한 기준, 리디자인이 품질을 떨어뜨리지 않는지 |

## 실행 방법

`config/config.yaml`에서 `input.directory`를 원하는 샘플로 지정한 뒤 `redesign`을 실행합니다.

```yaml
input:
  directory: "benchmarks/bakery"
```

기존 포트폴리오 샘플(`input/input.html`)은 그대로 유지되며(gitignore 대상), 이 폴더로 복사되지 않았습니다.

## 디자인 프롬프트 A/B 비교

`design_plan.md`의 비교는 기존 프롬프트 A와 `benchmarks/design_prompt_comparison/candidate/prompts/`의 후보 B를 사용합니다. 두 묶음은 제목·목록 수에 따른 콘텐츠 증식 강제를 제거한 공통 Creator 런타임으로 실행합니다. 따라서 A는 기존 프롬프트 기준이며 변경 전 파이프라인 전체의 재현은 아닙니다. 기본 프롬프트에 후보를 적용하는 판단은 별도 비교 결과에 따릅니다.

2026-11-15 v6 실행(evaluator 역할만 `think: false`, 그 밖에는 같은 모델·설정)에서 6개 사례 × A/B 12건이 모두 유효하게 완료됐습니다(36/36 iteration). 블라인드 화면 판정 결과는 B 선호 3건(saas-product, research-lab, portfolio), A 선호 2건(bakery, brief), 동점 1건(reference-polished)입니다. 채택 기준인 최소 4건 B 선호에 미달하므로 후보의 기본 적용은 보류합니다. 그 이전 v3·v5 실행은 시각 평가 출력 한도 초과와 외부 종료로 무효 처리했습니다. 자세한 결과는 [계획 §9](../design_plan.md)와 [로컬 실행 기록](../bench/design_prompt_comparison/pilot_report_20261115.md)을 참고하세요. 둘 다 gitignore 대상입니다.

새 디렉터리를 준비해도 과거에 소비한 예산은 환급되거나 초기화되지 않습니다. 추가 비교나 모델·추론 설정을 바꾼 실행은 별도 조건과 예산을 정한 뒤 진행합니다.

준비에는 `bench/design_prompt_comparison/`에 고정된 로컬 `manifest.json`, `baseline/` 및 6개 `inputs/`가 필요합니다. 이 디렉터리는 gitignore 대상이므로 새 체크아웃에 자동으로 포함되지 않습니다. 준비는 원본 입력과 프롬프트·설정을 검증해 묶음과 사례별 작업 디렉터리를 분리하며, 모델이나 브라우저를 실행하지 않습니다. 대상은 아직 존재하지 않는 경로를 지정합니다.

```powershell
$wraPython = 'C:\Users\null\anaconda3\envs\WRA\python.exe'
$experiment = Read-Host '새 실험 디렉터리 경로'
& $wraPython scripts/design_prompt_bench.py prepare $experiment
& $wraPython -c 'import sys; from pathlib import Path; from scripts.design_prompt_bench import verify; verify(Path(sys.argv[1]).resolve()); print("verified")' $experiment
```

준비된 입력·설정·프롬프트와 공통 런타임은 고정되며 실행 전에 다시 검증됩니다. 준비 결과를 검토한 후 초기 두 사례의 A/B 비교를 실행합니다. `pilot`은 `bakery`와 `reference-polished`의 네 작업이며 최대 12 iteration입니다. 나머지 네 사례까지 포함한 전체 상한은 36 iteration입니다.

```powershell
& $wraPython scripts/design_prompt_bench.py run $experiment --phase pilot
```

각 작업은 시작 전에 3 iteration을 예약하며 실패·중단·포기해도 반환하지 않습니다. 기존 예약이 있으면 `--resume`으로 이미 소비된 작업을 건너뛰고 이어갑니다. 중단된 작업에 완료 기록이 없으면 작업 프로세스 종료와 결과를 확인한 후 해당 작업을 `abandon`으로 명시적으로 포기할 수 있습니다. 해당 작업은 자동 재실행되지 않습니다.

```powershell
& $wraPython scripts/design_prompt_bench.py abandon $experiment --bundle A --case bakery
& $wraPython scripts/design_prompt_bench.py run $experiment --phase pilot --resume
```

나머지 사례는 초기 네 작업이 모두 성공하고 결과를 검토한 뒤에만 실행합니다. 기존 예약이 있으므로 `--resume`과 검토 확인용 `--pilot-reviewed`를 함께 지정합니다.

```powershell
& $wraPython scripts/design_prompt_bench.py run $experiment --phase remaining --resume --pilot-reviewed
```
