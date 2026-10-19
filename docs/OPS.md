# 운영 가이드 (OPS)

판결 아카이브의 자동화 파이프라인, 수동 재실행, 배포, 보안 절차를 정리한 문서입니다.

## 자동화 워크플로

두 개의 GitHub Actions 워크플로가 동일한 파이프라인(수집 → 전사 → 파싱·시세 결합 → 차트 export → 백테스트 → 커밋)을 주기만 달리해 실행합니다. 같은 concurrency group(`verdict-archive-refresh`)을 공유하므로 둘이 겹쳐 돌지 않습니다.

| 워크플로 | 파일 | 스케줄 (UTC / KST) | 모드 |
|---|---|---|---|
| `refresh-daily` | `.github/workflows/refresh-daily.yml` | `30 9 * * 1-5` (평일 18:30 KST, 장 마감 후) | 증분 — 새 게시글이 없으면 수집기가 조기 종료. 시세·백테스트는 항상 재생성 |
| `full-sweep-manual` | `.github/workflows/refresh-reports.yml` | 스케줄 없음 (workflow_dispatch 전용) | 전체 스윕 — 평소엔 일간 증분이 전부 커버, 복구용으로만 수동 실행 |

공통 사항:

- 커밋 주체는 `verdict-archive-bot`이며 `src/data/*.json`, `public/api/`, `data/` 의 추적 산출물(bronze manifest·reference, silver 전사·파싱, gold 성과·health, `data/catalog.json`)만 스테이징합니다. 변경이 없으면 커밋 없이 종료합니다. 레이어 구조는 [`DATA-LAYERS.md`](DATA-LAYERS.md).
- 시세 레이크(`data/silver/prices/`, `data/bronze/prices/`, `data/gold/_cache/price_frames/`)는 `actions/cache`로 복원되어 증분 조회만 발생합니다. 구 `data/prices` 캐시는 첫 실행에서 한 번 silver로 옮겨지고 bronze `legacy_import` 스냅샷이 만들어집니다.
- **2026-06-12 CI 소생 인시던트**: refresh-daily가 출범 후 한 번도 완주하지 못했던 사실이 발견됨 (yig/star 수집기 전수 재열거 36분 → 45분 타임아웃 + gitignore된 `data/pdfs`를 add하는 커밋 스텝). 증분 단락 도입 후 30분대 완주, 사상 첫 봇 커밋. 상세는 [`CHANGELOG.md`](CHANGELOG.md)의 "CI 소생" 절.
- 전사는 kordoc(`tools/kordoc`, 버전 고정) 단일 엔진입니다. 텍스트층으로 먼저 전사하고, kordoc 페이지 품질 판정상 표지나 문서 대부분에 텍스트층이 없으면 내장 PP-OCRv5 한국어 OCR로 다시 전사합니다. CI(Linux)에서도 동일하게 동작하므로 Windows 전용 OCR 단계는 없습니다. 엔진 버전을 올리면 헤더 버전이 달라진 파일만 재전사됩니다.

### 필요한 Secrets

레포 Settings → Secrets and variables → Actions:

| Secret | 용도 |
|---|---|
| `KRX_ID` | KRX 데이터 포털 로그인 ID (`build_report_performance.py` 시세 조회) |
| `KRX_PW` | KRX 데이터 포털 비밀번호 |

### 수동 재실행

두 워크플로 모두 `workflow_dispatch`를 지원합니다.

```bash
gh workflow run refresh-daily.yml --repo ChoiInYeol/SNUSMIC-Portfolio
gh workflow run refresh-reports.yml --repo ChoiInYeol/SNUSMIC-Portfolio

# 실행 상태 확인
gh run list --repo ChoiInYeol/SNUSMIC-Portfolio --workflow refresh-daily.yml --limit 5
gh run watch --repo ChoiInYeol/SNUSMIC-Portfolio
```

또는 GitHub UI: Actions 탭 → 워크플로 선택 → "Run workflow".

## 회귀 테스트 (v23)

`tests/`는 합성 가격·손계산 금융 원장과 회귀 스냅샷을 함께 검증합니다. CI는 모든 main push/PR에서 `UPDATE_GOLDEN=0`을 강제하고, 프론트엔드 격리 테스트·lint·build도 실행합니다. CI에서 골든 갱신·skip 시도는 실패하며 수익률 상승은 정확성 기준이 아닙니다.

- **단위 테스트** — KRX 호가단위 라운딩, FX 환산(`_fx`/`set_usdkrw`), asof 패스트패스 ≡ pandas, `_close_trade`/`_try_enter` KRW 손익, Kelly 경계.
- **회귀 테스트** — v22 FX 리밸런스 버그(보유 US 포지션 무환율 평가 → NAV 0 수렴) 재발 시 즉시 실패. zero-NAV 가드 계약 고정.
- **행동 테스트** — 샹들리에 스탑: ATR 손계산 가능한 경로에서 "스탑 이탈 → 익일 시가 체결" 검증.
- **골든 스냅샷** — 대표 전략 3종(고정보유/샹들리에/HRP)의 지표·거래 로그를 `tests/golden/strategies.json`과 비교. 의도된 로직 변경 시:

```bash
uv run pytest tests/test_financial_oracles.py tests/test_publication_safety.py  # 독립 oracle 먼저
UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py   # 로컬에서만 의도된 변경 재생성
UPDATE_GOLDEN=0 uv run pytest                       # 갱신 후 skip 없는 전체 회귀
git diff tests/golden/                                # 변경이 의도와 일치하는지 확인 후 커밋
```

## 파이프라인 헬스 체크 (v23)

수집기별 종료 상태를 `data/bronze/_runs/source-status/`에 기록하며, 하나라도 실패하면 재생성·커밋 전에 중단하고 상태를 quarantine artifact로 남깁니다. 새 산출물은 strict JSON/유한 수치·분류·시점/헤드라인·카운트/슬롯 일관성·자료 감소 검증 후에만 커밋하고 Telegram 다이제스트를 보냅니다. 품질 실패는 Telegram 설정과 무관하게 nonzero입니다. 신호의 달력상 경과일을 검사하지만 거래소 휴일 여부를 추정하여 최신성을 보장하지는 않습니다.

```bash
uv run python scripts/pipeline_health.py --dry-run   # 전송·health 상태 변경 없는 진단; 이상이면 nonzero
```

## Vercel 배포

| 항목 | 값 |
|---|---|
| 프로젝트 | `verdict-archive` (`prj_LPAuSB8gjKIdw6OZ3yIhuMiu63ae`) |
| 팀 | `team_bhIzHPiilnrcujrv6YdR8Isr` |
| 프로덕션 URL | https://verdict-archive.vercel.app |
| 트리거 | `main` 브랜치 push 시 자동 빌드·배포 (Next.js 자동 감지, 추가 설정 없음) |

페이지와 `.json` API는 정적 제공이 중심입니다. `/api/v1/signals/latest` 별칭은 CORS/OPTIONS를 제공하는 Next 요청 핸들러가 남아 있으므로 함수 사용량이 0이라고 주장하지 않습니다. 자격증명은 브라우저 번들에 넣지 않습니다. KRX 자격증명은 수집 파이프라인에서만 사용합니다.
자동 갱신 워크플로의 봇 커밋이 push될 때마다 재배포가 일어나므로, GitHub deployments 기록은 주기적으로 쌓입니다(2026-06 기준 최신 3건만 남기고 정리함).

빌드 전에 `uv run python scripts/export_signals_api.py --details-only`로 기존 공표본의 상세 manifest와 해시 URL 자산을 준비합니다. 이 명령은 신호의 `generated_at`이나 dated snapshot을 갱신하지 않습니다. frontend는 summary만 먼저 받고 선택/표시한 전략 상세를 같은 해시로 가져옵니다. 참조가 없거나 generation이 다르면 빌드/화면에서 명시적으로 실패합니다. 해시 자산은 이전 참조 보존을 위해 현재 자동 삭제하지 않으므로 누적 저장량을 측정해야 합니다.

Hobby는 개인 비상업 용도인지 먼저 확인해야 합니다. [공식 기준](https://vercel.com/docs/plans/hobby)의 월 CDN 전송·origin 전송·요청·CPU·메모리 한도를 실제 30일 usage와 대조한 뒤에만 적합성을 판단합니다. [100MB CLI source upload 제한](https://vercel.com/docs/limits)은 Git 연동 배포의 정적 파일 총량 한도와 다릅니다. 이번 감사에서 Vercel token이 유효하지 않아 usage·배포 확인은 미완료이며, 임의 플랜 전환/production 배포는 수행하지 않았습니다.

**2026-10-19 레짐 게이트 look-ahead 수정 (미재계산)**: T-·U의 KOSPI 파킹 레짐 게이트가 당일 종가와 당일 200MA로 당일 파킹 수익 여부를 결정하고 있었습니다(파킹 잔액 단독 2019-07~: 당일 판정 +647% vs 전일 판정 +264%, 단순 보유 +263%). 이제 `_regime_risk_on()`이 전일 종가·전일 200MA만 읽고(`tests/test_financial_oracles.py`), Q 전략의 시가 진입 유닛도 전일 종가 기준입니다. 헤드라인(`U_chandelier_scaleout`)은 이 편향 아래에서 선택됐으므로, 공표 숫자를 재계산하고 헤드라인 선택을 다시 수행하기 전까지 기존 성과를 유효한 것으로 표시하지 않습니다.

파킹 지수 캐시 `data/silver/prices/IDX_KOSPI.csv`는 종가만 포함합니다. 종가 수익을 당일 시가 예산에 섞는 M3 수정은 실제 시가 자료 확보 또는 명시적 close-transfer 연구 정책의 결정 전까지 완료 처리하지 않습니다. 기존 공표 금융 JSON을 새 계산 결과처럼 재라벨링하지 않습니다.

## SECURITY — KRX 자격증명 노출 [DONE 2026-06-11]

**`.env`(KRX_ID/KRX_PW)가 public 히스토리 `51a789f`~`af2cb8b` 구간(23개 커밋)에 노출되었습니다.**
`af2cb8b`에서 untrack 처리했지만, 그 이전 커밋들의 트리에는 파일이 그대로 남아 있어 public 레포에서 누구나 열람 가능했습니다.

**완료된 조치 (2026-06-11):**

1. **KRX 비밀번호 교체** — http://data.krx.co.kr 비밀번호 변경 + 레포 Secrets 갱신 완료.
2. **git filter-repo 히스토리 세척** — 전체 히스토리에서 `.env` 제거 후 강제 푸시 완료. 모든 커밋 SHA 재작성됨.
3. **잔여 (선택)** — GitHub Support에 GC(가비지 컬렉션) 요청 (dangling object 완전 제거용). 비밀번호를 이미 교체했으므로 필수는 아님.

### 참고 — 세척 과정에서 발생한 영향

- **모든 커밋 SHA가 바뀌었습니다.** 릴리스 노트(v1.0.0 등)에 박힌 구 SHA 링크가 끊어졌습니다.
- **협업자(있을 경우)는 re-clone이 필요합니다.**
- GitHub 측 캐시(PR, 포크, API의 dangling commit)에는 구 객체가 한동안 남을 수 있습니다.

### 세척에 사용한 절차 (기록용)

```bash
# filter-repo는 fresh clone에서만 동작
pip install git-filter-repo
git clone https://github.com/ChoiInYeol/SNUSMIC-Portfolio.git smic-scrub
cd smic-scrub

# 전체 히스토리(전 브랜치·태그)에서 .env 제거
git filter-repo --invert-paths --path .env

# 리모트 재등록 후 강제 푸시
git remote add origin https://github.com/ChoiInYeol/SNUSMIC-Portfolio.git
git push origin --force --all
git push origin --force --tags
```

## 릴리스 / 태그 정책

- 구 422-커밋 시대의 v0.x~v1.0.5 태그·릴리스(121개 태그, 11개 릴리스)는 2026-06-11에 일괄 삭제했습니다. 현재 main에서 도달 불가능한 고아 히스토리를 가리키고 있었기 때문입니다.
- 현행 릴리스는 `v1.0.0`(판결 아카이브 v1.0)부터 시작합니다. 새 릴리스는 `gh release create vX.Y.Z --target <sha>`로 생성하세요.
