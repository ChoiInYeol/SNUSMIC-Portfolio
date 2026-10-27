# 데이터 레이어 (Bronze / Silver / Gold)

일일 수집은 새 게시물 목록을 확인하되, manifest에 체크섬이 기록되고 현재 kordoc 버전의
비어 있지 않은 전사본이 있는 보고서는 PDF 캐시가 없는 Actions 러너에서도 재다운로드하지 않는다.
버전이 다르거나 전사본이 없으면 복구 다운로드를 수행한다. KUVIC 목록 수집은 다운로드보다 먼저 실행하며,
완료된 첫 페이지에서 종료한다. 주간 `--full`은 모든 목록 페이지를 확인하고 기존 Bronze 항목을 보존한다.

스키마·계보 탐색: `/developers/architecture` — [FastAPI 메타데이터 서비스와 로컬 실행](METADATA.md).

결정 근거: [ADR 0001](adr/0001-api-v2-and-medallion.md) Decision 2. 모든 경로는 `scripts/lake.py` 한 곳에서 정의하고,
스크립트는 `data/...` 문자열 대신 그 상수를 import 한다.

## 세 레이어

| 레이어 | 정의 | 규칙 |
|---|---|---|
| **Bronze** | API·DB·파일 원천 데이터를 가공 없이 수집·저장. 수집 시각·출처·버전 정보를 보존하고 재처리 가능 | append-only. 덮어쓰기·제자리 수정 금지. 교정은 silver에서(`corrections.json`) |
| **Silver** | Bronze를 검증·정제·중복 제거·스키마 표준화해 정합성·일관성 보장 | 쓰기 전에 검증, 위반 시 실패 또는 격리(조용히 버리지 않음) |
| **Gold** | Silver를 비즈니스 목적에 맞게 집계·가공해 REST API·대시보드·분석·트레이딩 알고리즘에서 즉시 활용 | silver만 읽는다. 웹이 읽는 산출물은 `schema_version` 을 가진다 |

## 디렉터리

```
data/
  bronze/                                   원천, append-only
    reports/pdfs/{school}/*.pdf             수집 PDF (git 제외, manifest로 재다운로드 가능)
    reports/manifest.json                   다운로드 1건당 1행: url, sha256, size, downloaded_at, error
    reports/kuvic_items.json                KUVIC 목록 원본
    prices/{vendor}/ingest_date=YYYY-MM-DD/{run_id}-{seq}.csv.gz   시세 원본 응답 (git 제외)
    prices/_ingest.jsonl                    조회 1건당 1줄 (git 제외)
    reference/krx_names.json                기존 pykrx 공식 종목명 스냅샷 (보존)
    reference/krx_name_observations.jsonl    새 공식명 조회: 수집 시각·벤더 버전 포함, append-only
    _runs/source-status/, _runs/quarantine/ CI 수집기 상태 (git 제외)
    portfolio/funded-w50-v1/                납입식 모의 계정: genesis·납입·신호·세션·동결 주문 계획 (docs/FUNDED-ACCOUNT.md)
  silver/                                   검증·표준화
    reports/markdown/{school}/*.md          kordoc 전사본
    reports/parsed.csv, parse_issues.csv    리포트 1건당 1행 / 파싱 이슈
    reference/kr_name_ticker.json           이름→티커 (네이버 자동완성 캐시)
    reference/corrections.json              사람이 검증한 교정값
    reference/search_synonyms.json          본문 검색 동의어·종목 별칭 (사람이 관리, docs/SEARCH.md)
    prices/{KR|US|IDX}_{symbol}.csv         정제 OHLCV (git 제외, bronze로 재생성 가능)
    _rejects/{symbol}.jsonl                 검증에 걸린 시세 행 + 사유 (git 제외)
  gold/                                     목적별 집계
    reports/performance.csv                 리포트 성과
    reports/performance_summary.csv         (현재 생성 스크립트 없음 — 과거 산출물)
    ops/health.json                         파이프라인 헬스 기준값
    _cache/                                 백테스트 단계 캐시·시세 프레임 캐시 (git 제외)
  catalog.json                              데이터셋 계보 (아래)
src/data/*.json, public/api/**, public/strategy-trades*.csv   gold 서빙 산출물 (Next.js·공개 API)
```

## 생산자와 소비자

| 데이터셋 | 생산 | 소비 |
|---|---|---|
| bronze PDF · manifest | `collect_smic/ewha/voera/reports.py`, `collect_kuvic_browser.py`(kuvic_items) | `transcribe_pdfs.py`, `build_report_performance.py`(힌트), `pipeline_health.py` |
| bronze 시세 | `lake_prices.record_raw_prices` (← `build_report_performance.py` pykrx·yfinance, `backtest/warehouse.py` yfinance) | `rebuild_silver_prices.py` |
| bronze krx_names | `build_report_performance.py` | `build_report_performance.py` |
| silver markdown | `transcribe_pdfs.py` | `build_report_performance.py`, `eval_extraction.py`, `build_fulltext_index.py`, 웹 원문 뷰어(GitHub 링크) |
| 본문 검색 색인 `public/research/fulltext/` | `build_fulltext_index.py` (silver markdown·parsed·synonyms + 서빙 report-performance·first-seen) | `/search`, 커맨드 팔레트 링크 — [SEARCH.md](SEARCH.md) |
| silver parsed · issues | `build_report_performance.py` | `pipeline_health.py` |
| silver reference | `build_report_performance.py`(name_ticker), 사람(corrections) | `build_report_performance.py` |
| silver 시세 | `lake_prices.write_silver_prices` (← 위 두 수집 경로, `rebuild_silver_prices.py`) | `build_report_performance.py`, 백테스트, `build_stats.py`, `build_forward_record.py`, `export_stock_charts.py` |
| gold performance | `build_report_performance.py` | `backtest_momentum.py`, `spo_portfolio.py` |
| gold health | `pipeline_health.py` | `pipeline_health.py`(직전 실행 비교) |
| 서빙 | `build_report_performance.py`, `backtest_momentum.py`, `export_signals_api.py`, `export_api_v2.py`, `export_stock_charts.py`, `build_stats.py`, `build_forward_record.py` | Next.js 빌드, `/api/v1`, `/api/v2` |

## 시세: bronze 기록 → silver 표준화

- **완료 일봉**: `market_clock.closed_bar_end`가 정규장 종료 후 15분(KR 15:45 KST,
  US 16:15 America/New_York; DST 반영)을 기준으로 요청 상한을 정한다. FX는 UTC 하루가
  지난 뒤 사용하며 주말은 제외한다. 조기 폐장일은 정규장 기준 시각까지 보수적으로 대기하고,
  공휴일에는 벤더가 반환하는 실제 거래일을 따른다. 역사적 `as_of`보다 뒤의 캐시는 읽지 않는다.
  정규장 근거: [KRX](https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T1.jsp),
  [NYSE](https://www.nyse.com/trade/trading-information).
- **벤치마크 갱신**: KOSPI·S&P500·NASDAQ·GLD·USDKRW는 파일 존재 여부만으로 캐시를 신뢰하지 않고
  누락된 꼬리를 조회한다. 갱신 후에도 마지막 거래일이 요청 상한보다 10일 넘게 오래되면 백테스트를
  실패시킨다(긴 연휴 여유 포함). 응답이 비어도 수개월 전 지수로 성과를 게시하지 않는다.
- **bronze**: 벤더 응답 DataFrame을 정제 전 그대로 기록해 `{vendor}/ingest_date=…/{run_id}-{seq:05d}.csv.gz`
  (열: `symbol`, 벤더 원본 열, `fetched_at`)로 쓰고, `_ingest.jsonl` 에 조회마다 `{vendor, symbol,
  request{ticker,start,end,adjusted}, fetched_at(UTC, μs), rows, sha256(원본 csv 바이트), library, library_version, run_id, file}`
  를 append 한다. 추가 네트워크 호출은 없다. vendor: `pykrx_stock`, `pykrx_index`, `yfinance`, `legacy_import`.
  - **bronze 는 silver 보다 뒤처지지 않는다**: `write_silver_prices` 가 쓰기 전에 기록기를 flush 한다(그래서 조회한 종목마다
    작은 gz 파일 하나가 생긴다). SIGTERM 은 실행 중인 코드를 풀고 종료하면서 flush 하고(종료 코드 143), 생산 스크립트
    (`build_report_performance.py`, `backtest_momentum.py`)는 끝에서 `flush_bronze()` 를 직접 불러 flush 실패가 nonzero 종료가
    되게 한다. atexit flush 는 마지막 안전망일 뿐이다.
  - **원자성·동시성**: gz 는 같은 폴더의 임시 파일에 쓴 뒤 하드링크로 게시한다(완전하거나 없거나, 기존 파일은 절대 덮지 않음).
    flush 가 실패하면 다음 flush 는 새 번호를 쓴다. `_ingest.jsonl`·rejects 는 블록 단위 1회 쓰기를 파일 잠금(POSIX `flock`,
    Windows `msvcrt.locking`) 아래 O_APPEND 로 해서 여러 프로세스가 동시에 써도 줄이 섞이지 않는다.
- **silver** (`write_silver_prices`): 인덱스 헤더 `date`(구 파일의 `날짜`/`Date` 도 모든 리더가 `index_col=0` 으로 동일하게 읽음),
  소문자 OHLCV 열 순서, 날짜 중복 제거(마지막 값 유지), 정렬, 검증 후 원자적 쓰기(같은 폴더의 고유 임시 파일 → `os.replace`).

### 검증 규칙

| 규칙 | 위반 시 |
|---|---|
| 날짜 유일·증가, `close` 열 존재 | `SilverValidationError` — 쓰기 중단, 실행 실패 |
| `close` NaN, `close ≤ 0` | 해당 행을 `silver/_rejects/{symbol}.jsonl` 로 격리(`{date, reason, rejected_at, row}`, 같은 날짜·사유는 한 번만) + 경고. 나머지 행으로 갱신은 계속 |
| `high ≥ max(open, close)`, `low ≤ min(open, close)` (허용오차 0.5%, `open/high/low` 가 0인 거래정지 행은 비교 제외) | 같음 (행 격리) |

허용오차 근거 — 2026-10-19 기존 silver 771개 파일 스캔: 허용오차 없이 228개 파일이 OHLC 위반(수정주가 반올림으로 고가가
종가보다 1원 낮은 식, 거래정지일 OHLC=0). 0.01% 99개, 0.1% 19개, 0.5% 2개 파일. 0.5% 기준 남는 위반은 4개 파일 4행
(`IDX_GLD`·`IDX_NASDAQ` 의 2026-06-09 NaN 종가, `US_TOT`·`US_UA` 각 1행)뿐이고 구조적 위반(중복·역순·0 이하 종가)은 0건이라,
행 단위 격리를 택했다. 전체 실패로 두면 이 4개 파일을 다시 쓰는 순간 매일 실행이 멈춘다.

## 재처리

```bash
# bronze 원본을 재생해 silver 시세 재생성 (전체 / 일부 / 다른 폴더에 만들어 비교)
uv run python scripts/rebuild_silver_prices.py
uv run python scripts/rebuild_silver_prices.py --symbols KR_005930 IDX_KOSPI
uv run python scripts/rebuild_silver_prices.py --out-dir cache/silver-rebuild

# 1회성: bronze 이력이 없는 기존 silver 시세를 bronze 에 legacy_import 로 스냅샷 (이미 있으면 거부)
uv run python scripts/rebuild_silver_prices.py --bootstrap-from-silver

# 리포트: bronze PDF → silver 전사 → silver/gold 파싱·성과
uv run python scripts/transcribe_pdfs.py --jobs 4
uv run python scripts/build_report_performance.py
```

재생은 종목별로 `fetched_at` 순서(안정 정렬)로 하고 같은 날짜는 나중 조회가 이긴다. 파일 이름은 순서에 쓰지 않는다. 셀은 bronze 에 저장된 텍스트
그대로 써서 숫자 자릿수가 바뀌지 않는다. 2026-10-19 로컬 bootstrap(771개 파일, 19.4 MB gz)을 재생하면 위 4행을 뺀 모든 파일이
기존 silver 와 본문이 바이트 단위로 같았다. `backtest/warehouse.py` 가 종가만 쓰던 yfinance 지수 파일은 재생 시 벤더가 준 OHLCV
전체가 들어간다(리더는 열 이름으로 고르므로 영향 없음).

## 경로 호환

bronze 는 다시 쓰지 않으므로 manifest 의 `file` 은 구 경로(`data/pdfs/...`)를 그대로 가진다. 정답셋 픽스처의 `source_pdf`,
이전에 생성된 `source_file`(`data/markdown/...`)도 같다. 읽는 쪽은 `lake.current_rel()` / `lake.resolve_data_path()` 로
구 경로와 새 경로를 모두 받는다. 새로 쓰는 행은 새 경로(`data/bronze/reports/pdfs/...`, `data/silver/reports/markdown/...`)다.

## 카탈로그 (`data/catalog.json`)

`scripts/build_catalog.py` 가 데이터셋마다 `{id, layer, path(glob), format, producer, inputs, schema_version, rows, files,
bytes, updated_at, tracked_in_git}` 를 쓴다. `inputs` 를 따라가면 gold·서빙 파일을 silver·bronze 원천까지 추적할 수 있다.

- 결정적 출력: 정렬된 키·항목, 텍스트 파일 `bytes` 는 LF 기준, `updated_at` 은 파일 안의 `generated_at`/`as_of`/`checked_at`
  (manifest 는 최신 `downloaded_at`)만 쓰고 mtime 은 쓰지 않는다.
- git 제외 데이터셋(PDF, 시세, 캐시)은 기기마다 달라 통계가 `null` 이다. PDF 수는 `bronze.report_manifest.rows` 로 본다.
- CI: refresh-daily 는 헬스 체크 뒤, refresh-reports 는 재생성 끝에 생성하고 커밋한다. `tests.yml` 과 daily 게시 검증이
  `--check` 로 낡은 카탈로그를 막는다. 산출물을 손으로 재생성했다면 `uv run python scripts/build_catalog.py` 도 함께 돌린다.
