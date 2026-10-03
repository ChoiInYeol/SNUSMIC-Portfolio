

# 판결 아카이브 (Verdict Archive)

> **모든 리포트는, 결국 시장의 판결을 받는다.**

[![Daily Refresh](https://github.com/ChoiInYeol/SNUSMIC-Portfolio/actions/workflows/refresh-daily.yml/badge.svg)](https://github.com/ChoiInYeol/SNUSMIC-Portfolio/actions/workflows/refresh-daily.yml)
[![Next.js](https://img.shields.io/badge/Next.js-App%20Router-black?logo=next.js)](https://nextjs.org)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Deployed on Vercel](https://img.shields.io/badge/Vercel-verdict--archive-black?logo=vercel)](https://verdict-archive.vercel.app)

6개 대학 투자학회의 리서치 리포트를 **point-in-time 시세**로 검증하는 아카이브 & 전략 랩.
리포트 PDF를 전사·파싱해 목표가와 투자의견을 추출하고, 발간 이후의 실제 주가 경로로 점수를 매깁니다.

**Live: [verdict-archive.vercel.app](https://verdict-archive.vercel.app)**

![판결 아카이브 — 모자이크와 핵심 지표](docs/screenshots/home.png)
*홈 — 매수 의견 911건의 성적표. 적중률 60.5%, 더블 이상 320건, 판결까지 중앙값 185일.*

## 무엇인가요

서울대 **SMIC** · 연세대 **YIG** · 성균관대 **STAR** · 고려대 **KUVIC** · 이화여대 **EIA** · 홍익대 **VOERA** —
여섯 학회가 공개한 리포트 PDF **1,569건을 수집, 1,395건을 전사·파싱**했습니다.

핵심은 *사후 판단(hindsight)이 아닌 발간 시점 기준(point-in-time)* 검증입니다.

- 여섯 학회가 나란히 비교되는 **2019년 7월 이후 매수 의견 911건**을 채점하고, 발간 90일이 지나지 않은 신생 건은 판결을 보류합니다.
- 판결은 둘로 나뉩니다 — **현재 판결**(오늘 다시 읽은 같은 보고서의 성적)과 **전성기 판결**(발간 후 24개월 내 최고점). "맞았지만 라운드트립을 탔다"가 한눈에 드러납니다.
- 모든 판결문은 원본 PDF·전사 markdown·출처 게시글로 역추적할 수 있습니다.

## 화면

| | |
|---|---|
| ![증거의 서가](docs/screenshots/evidence-shelf.png) | ![판결문](docs/screenshots/verdict-card.png) |
| **증거의 서가** — 매수 리포트 911건을 발간 순서 그대로 깐 모자이크. 짙은 청색은 급락, 금장은 멀티배거, 다이아몬드 광채는 텐배거. | **판결문** — 그날의 주장(의견·목표가) 대 시간의 판정(현재/전성기), 도장까지. |
| ![오늘의 신호](docs/screenshots/strategy-signals.png) | ![전략 비교](docs/screenshots/strategy-compare.png) |
| **오늘의 신호** — SOTA 전략의 보유 종목·스탑·과열계수, 매도/매수 임박 카운트. | **전략 비교** — 26개 변형을 IS/OOS로 검증, 전 전략 온/오프 오버레이 자산곡선(lightweight-charts)과 거래 로그. |

![종목 페이지 — 삼성전기](docs/screenshots/stock-009150.png)
*종목 페이지 — 캔들 차트 위에 학회별 발간 시점(▲)과 목표가(점선)를 중첩. 3개 학회가 같은 종목을 어떻게 판단했는지 시간 순으로 비교.*

## 주요 기능

- **증거의 서가** — 911건의 매수 의견을 숨김 없이 전부 전시. 급락도 텐배거도 같은 벽에 걸립니다.
- **이중 판결** — 현재 판결 vs 전성기 판결. 전성기 +100% 도달 비율 37.2%(320건), 그러나 그 수익이 현재까지 남았는지는 별개의 질문.
- **전략 랩** — "전성기 수익을 어떻게 수확할 것인가"에 대한 26개 전략 변형 실험. SOTA `U. 샹들리에+과열 스케일아웃` (`U_chandelier_scaleout`)은 **KOSPI 적립식(DCA) 대비 1.12×** (IS Sharpe 1.26, OOS Sharpe 1.70). 판정 칩은 매 실행 수치로 자동 생성.
- **오늘의 신호** — SOTA 전략이 지금 규칙대로 굴러간다면 일어날 매매. 뉴스 피드가 아니라 임박 주문서.
- **통계 페이지** — 배거의 관상(배거 도달 종목 패턴 분석), 전략별 KRW 환산 손익, 누적 수익 곡선.
- **본문 읽기** — 판결문 안에서 전사된 리포트 원문을 즉시 열람. PDF 다운로드 없이 리포트 논거 확인 가능.
- **시그널 API** — 헤드라인 전략의 매매 신호를 정적 JSON으로 서빙. 매일 갱신, CDN 캐시, CORS 허용. 자세한 내용은 [`docs/API.md`](docs/API.md).
- **학회별 성적표** — 학회마다 최신순 장부와 연도별 요약, QA 플래그(파싱 신뢰도) 포함.
- **원문 추적** — 판결문 → 전사 markdown → 원본 PDF → 출처 게시글. 모든 숫자에 출처가 달립니다.

## 아키텍처

```mermaid
flowchart LR
    A["수집<br/>collect_*.py<br/>6개 학회 사이트"] --> B["전사<br/>transcribe_pdfs.py<br/>+ OCR 폴백"]
    B --> C["파싱<br/>목표가·투자의견<br/>티커 매칭"]
    C --> D["시세 결합<br/>build_report_performance.py<br/>KRX/yfinance PIT"]
    D --> E["판결·백테스트<br/>backtest_momentum.py<br/>이중 판결 + 26개 전략"]
    E --> F["SSG 배포<br/>Next.js → Vercel"]
```

데이터 복구 체인: 일반 전사 → 파일명/수집 메타데이터 힌트 → 네이버 자동완성 티커 복구 → Windows OCR 표지 폴백.
사람이 검증한 교정값은 `data/sources/corrections.json`으로 유지됩니다.

## 데이터 소스

| 학회 | 학교 | 수집(PDF) | 출처 |
|---|---|---:|---|
| SMIC | 서울대학교 | 776건 | http://snusmic.com/research/ |
| STAR | 성균관대학교 | 281건 | http://starskku.com/board/board_list?code=research |
| EIA | 이화여자대학교 | 200건 | https://ewhainvest.com/research |
| YIG | 연세대학교 | 104건 | https://yig.yonsei.ac.kr/research |
| KUVIC | 고려대학교 | 104건 | https://www.kuvic.com/research |
| VOERA | 홍익대학교 | 104건 | https://www.voera.co.kr/Research |

**수집 원칙**: 각 PDF는 **1회만 다운로드**하고 SHA256 manifest(`data/sources/manifest.json`)로 관리하며,
요청 간 지연(2.5초)을 두어 원 서버에 부하를 주지 않습니다. 이메일 등 비공개 채널 수집은 하지 않습니다.

## Quick Start

```bash
npm install && npm run dev          # http://localhost:3000

# 1) 수집 (1회만 다운로드, manifest 관리, 요청 간 2.5초 지연)
.venv/Scripts/python scripts/collect_smic.py              # SMIC (증분, WP REST API)
.venv/Scripts/python scripts/collect_smic.py --full       # SMIC 전체 순회
.venv/Scripts/python scripts/collect_reports.py --source all
.venv/Scripts/python scripts/collect_kuvic_browser.py     # KUVIC (Playwright)
.venv/Scripts/python scripts/collect_ewha.py              # EIA (plain requests)
.venv/Scripts/python scripts/collect_voera.py             # VOERA (Playwright)

# 2) 전사 + OCR 폴백 (텍스트 없는 PDF 표지, Windows 전용)
.venv/Scripts/python scripts/transcribe_pdfs.py
.venv/Scripts/python scripts/ocr_fallback.py

# 3) 파싱 + 시세 결합 (.env의 KRX_ID/KRX_PW 사용, data/prices/ 증분 캐시)
.venv/Scripts/python scripts/build_report_performance.py

# 4) 종목 차트 데이터 → public/prices/{slug}.json
.venv/Scripts/python scripts/export_stock_charts.py

# 5) 전략 백테스트 (IS/OOS 분리, 민감도 그리드 포함) → src/data/strategy-backtest.json
.venv/Scripts/python scripts/backtest_momentum.py
```

## 시그널 API

헤드라인 전략의 매매 신호를 매일 정적 JSON으로 서빙합니다. 서버리스 컴퓨팅 없이 Vercel CDN에서 직접 제공되며 CORS가 허용됩니다.

```bash
# 오늘의 신호 (보유 포지션·매수 임박·매도 임박)
curl https://verdict-archive.vercel.app/api/v1/signals/latest.json | python -m json.tool

# 전략 목록 (IS/OOS 지표 포함)
curl https://verdict-archive.vercel.app/api/v1/strategies.json | python -m json.tool
```

엔드포인트 전체 명세: [`docs/API.md`](docs/API.md) · OpenAPI 스펙: [`/api/v1/openapi.json`](https://verdict-archive.vercel.app/api/v1/openapi.json)

## Docker

로컬 재현성 및 셀프호스팅을 위한 멀티스테이지 이미지 — `pipeline`(Python 파이프라인)과 `web`(Next.js 프로덕션).

```bash
# 전체 파이프라인 실행 (백테스트 + API export)
docker compose --profile pipeline run --rm pipeline

# Next.js 프로덕션 서버 (localhost:3000)
docker compose --profile web up web
```

자세한 빌드·플랫폼(Mac M-series, Linux, Windows) 가이드: [`docs/DOCKER.md`](docs/DOCKER.md)

## 자동 갱신

| 워크플로 | 주기 | 내용 |
|---|---|---|
| [`refresh-daily.yml`](.github/workflows/refresh-daily.yml) | 평일 18:30 KST (장 마감 후) | 증분 수집 → 새 PDF만 전사 → 데이터셋·차트·백테스트 재생성 → 커밋 |
| [`refresh-reports.yml`](.github/workflows/refresh-reports.yml) | 수동 실행 전용 | 전체 페이지 스윕(`--full`) — 수집기 복구·과거 누락 의심 시에만 |

레포 Settings → Secrets에 `KRX_ID`, `KRX_PW` 등록이 필요합니다. 갱신 커밋이 푸시되면 Vercel이 자동 재배포합니다.
운영 상세는 [`docs/OPS.md`](docs/OPS.md), 릴리스별 변경 기록은 [`docs/CHANGELOG.md`](docs/CHANGELOG.md) 참고.

## 방법론에 대한 정직한 주석

- 백테스트는 **과거 데이터 기반 시뮬레이션**이며 미래 수익을 보장하지 않습니다. 투자 권유가 아닙니다.
- 과최적화 방지를 위해 파라미터는 문헌 표준값으로 고정하고(200MA, ATR×5 등), 인샘플(2020-01~2023-12)/아웃오브샘플(2024~현재)을 분리해 검증했습니다. 그리드 서치로 고른 전략이 아니라 IS 샤프 최상위가 자동 채택됩니다.
- 목표가·의견 파싱은 자동화되어 있어 오류가 있을 수 있습니다. 신뢰도가 낮은 건은 QA 플래그로 표시하고, 검증된 교정값만 overlay로 반영합니다.
- 2019년 7월 이전 SMIC 단독 수집분 445건은 학회 간 비교가 불공정하므로 채점 없이 아카이브로만 보관합니다.

## Legacy

이전 버전(리서치 워크스테이션, 422 커밋)은 [`legacy` 브랜치](../../tree/legacy)에 보존되어 있습니다.
파서 교정 규칙·시세 웨어하우스 등은 legacy에서 선별 이식했습니다.
