# 인수인계 — API v2 · Bronze/Silver/Gold · UI (Claude Code 세션, 2026-10-19)

> 2026-10-20 상태 보충: API v2·Medallion·메타데이터 UI는 `6353f6597`에 로컬 커밋됐다.
> 아래는 초기 Claude 세션의 작업 기록이다. 이후 kordoc 연결·1,588건 재전사와 재계산 현황은
> [전체 전사 기록](KORDOC-ROLLOUT-2026-10-20.md)을 따른다. main 병합·푸시는 하지 않았다.

Codex 가 이어받기 위한 문서다. 설계 근거는 `docs/adr/0001-api-v2-and-medallion.md`, 데이터 계층은 `docs/DATA-LAYERS.md`,
API 는 `docs/API.md`. 기존 kordoc 인수인계(`docs/HANDOFF-2026-10-19.md`)의 제약은 그대로 유효하다
(kordoc 고정, CPU 제한 ≤4 jobs·BelowNormal·8코어 affinity, **`main` 푸시 금지 = 프로덕션 배포**).

## 0. 브랜치 / 커밋 상태

| 브랜치 | 상태 |
|---|---|
| `main` | 로컬을 `origin/main`(10-16 daily)으로 fast-forward만 함. 푸시 없음 |
| `audit/2026-10-p0` | 변경 없음 (푸시됨, 미머지) |
| `feat/kordoc-parsing` | 변경 없음 (로컬, 미푸시) |
| `feat/api-v2-medallion` | **현재 브랜치.** `feat/kordoc-parsing` 위에 스택. **아무것도 커밋되지 않음** — 전부 작업 트리/인덱스에 있음 |

- 인덱스(staged): Claude 세션 작업 대부분 (`git add -A` 1회, 리네임 1685건 포함).
- 미스테이징(unstaged)/미추적: 그 이후의 리뷰 수정분 **+ Codex 가 이미 시작한 작업**
  (`docs/METADATA.md`, `scripts/schema_metadata.py`, `scripts/market_clock.py`, `src/app/developers/architecture/**`,
  `src/components/architecture/**`, `tools/kordoc/cpu-limits.mjs`, `tests/test_market_clock.py`,
  `tests/test_pipeline_extraction.py`, `tests/test_transcription.py`, `data/silver/reports/markdown/**` 재전사 약 1,590건).
  → 커밋할 때 Claude 세션 작업과 Codex 작업을 **별도 커밋**으로 나눌 것.

## 1. 완료된 것 (Claude 세션)

### 1-1. REST API v2 (`/api/v2`, v1 은 동결·deprecated)
- 리소스: `/api/v2`, `/signals`, `/signals/latest`, `/signals/{date}`, `/strategies`, `/strategies/{id}`,
  `/strategies/{id}/trades`, `/strategies/{id}/revisions/{sha256}`, `/track-record`, `/instruments`, `/instruments/{id}`,
  `/instruments/{id}/prices`, `/instruments/{id}/trade-marks`, `/clubs`, `/clubs/{id}`, `/clubs/{id}/reports`, `/openapi.json`.
- 파일 `public/api/v2/<uri>.json`, `next.config.ts` 가 확장자 없는 URI 로 rewrite. 작성자 `scripts/export_api_v2.py`(+`--check`),
  가격은 `scripts/export_stock_charts.py`. `/prices/*`, `/strategy-marks/*` 는 308 리다이렉트(구 디렉터리 삭제).
- v1: 모든 operation `deprecated: true`, 응답에 `Deprecation: @1792368000` + `Link: </api/v2>; rel="successor-version"`.
  v1 strategy-details URL 은 v2 revision 파일(바이트 동일)로 rewrite → 11MB 중복 없음.
- 404/405 는 `src/app/api/v2/[...path]/route.ts` 가 RFC 9457 problem+json. **한계**: 존재하는 정적 리소스에 POST/PUT/DELETE/OPTIONS 는
  Next 정적 계층이 500(+`Allow`)으로 응답 — 설정으로 못 고침, 문서에 "플랫폼 정의"로 기재. Vercel 실제 동작은 프리뷰에서 확인 필요.
- 계약: `public/api/v2/openapi.json`(openapi-spec-validator OK) ↔ `tests/test_api_v2_contract.py` ↔ zod `src/lib/api-v2.ts`
  ↔ `tests/api-v2-schemas.test.cjs`.
- 가격: 775 종목 전부 HEAD 의 구 `public/prices` 와 종료일 일치(728개 2026-10-16, 47개는 상장폐지/정지로 HEAD 와 동일한 과거일).

### 1-2. Bronze / Silver / Gold
- 경로 단일 출처 `scripts/lake.py`. `data/bronze|silver|gold` 로 물리 이동(트래킹 파일은 `git mv`).
- Bronze 시세 원천 보존 `scripts/lake_prices.py` (`record_raw_prices` → `data/bronze/prices/{vendor}/ingest_date=…/{run}.csv.gz`
  + `_ingest.jsonl`), Silver 단일 작성자 `write_silver_prices`(구조 위반=실패, 행 단위 이상=`data/silver/_rejects/` 격리).
- `scripts/rebuild_silver_prices.py`(bronze→silver 재처리, `--bootstrap-from-silver` 1회 실행됨: 19.4MB legacy_import),
  `scripts/build_catalog.py` → `data/catalog.json`(lineage, `--check`, `--tracked-pathspecs`).
- CI 워크플로 3개 경로·단계·`git add` 목록 갱신, 최초 1회 캐시 마이그레이션 스텝 포함(**Actions 에서 미실행**).

### 1-3. UI
- 전 소비자 v2 로 이전 (`src/lib/api-v2-server.ts` 경유 zod 검증).
- `/today/[date]` 축쇄판(75호, 전일 대비, 이전/다음), `/today/archive`. look-ahead 차단: 리포트는
  `report_date ≤ 호 날짜 AND first_seen ≤ 호 날짜`(`src/data/report-first-seen.json`, `scripts/build_report_first_seen.py`),
  부고는 박제본 간 사라진 포지션으로 계산. 통권 = 박제본 순번.
- `/developers` (openapi 기반 레퍼런스 + Try it + 코드 샘플 + v1→v2 표), `/developers/swagger` (swagger-ui 5.17.14 cdnjs + SRI).
- API 리소스 칩(각 데이터 화면), `/strategy?s=<id>&on=a,b` 딥링크, 리비전 해시를 `crypto.subtle` 로 재계산 검증.
- shadcn 추가: tabs, table, tooltip, select, scroll-area, input (Windows `"cn"` 버그 수정 완료, 남은 package.json 변경은 `radix-ui`).

## 2. 독립 리뷰(21건) 반영 현황

| # | 내용 | 상태 |
|---|---|---|
| H1 | CI 가 catalog 와 다른 파일 집합을 커밋 | 반영 (strategy-trades CSV·kuvic_items 추가, `git add` 후 `--tracked-pathspecs` 로 검사) |
| H2 | 박제 스냅샷 링크 대상 종목 prune 시 파이프라인 영구 정지 | 반영 (`frozen_instrument_refs`, 유실 시 명시적 실패) — 시뮬레이션 테스트 존재 여부 확인 필요 |
| H3 | 과거호에 나중 수집 리포트 노출 | 반영 (first_seen) |
| H4 | 은퇴 전략 revision 이 node 테스트 실패 유발 | 반영 (revisions 전수 walk) — `docs/API.md` 문구 확인 필요 |
| M5 | 과거호 부고가 현재 백테스트 기반 | 반영 |
| M6 | bronze 가 atexit 에만 flush | 반영 (silver 쓰기 전 flush, SIGTERM, 잠금 O_APPEND, 원자적 쓰기) |
| M7 | cold cache 시 가격 대량 삭제 | 반영 (`MAX_PRUNE_FRACTION = 0.05`) |
| M8 | close ≤ 0 이 전체 실패 | 반영 (행 격리로 이동) — ADR/DATA-LAYERS 문구 일치 여부 확인 |
| M9 | trade-marks 예시 KeyError | 반영 |
| L10–21 | 과거호 후속 카운트, shadcn, 리플레이 순서, 원자적 쓰기, halt 캔들, openapi.json 경로, regex 등 | 반영 |

**주의**: apiv2·medallion 에이전트가 사용량 한도로 **최종 게이트 실행 전에 중단**됐다. 특히 medallion 은 `tests/test_lake.py` 를
재작성하던 중이었다. 2026-10-19 23:3x 기준 `uv run pytest` 결과: 실패는
`tests/test_schema_metadata.py::test_metadata_snapshot_and_openapi_match_sources` **1건뿐이며 이는 Codex 의 신규 작업**이다.

## 3. 남은 일 (순서대로)

1. Codex 자체 작업(`schema_metadata` 등) 마무리 → 위 실패 해소 (`uv run python scripts/schema_metadata.py`).
2. 전체 게이트 (CPU 제한 셸에서, 하나씩):
   ```powershell
   $me = Get-Process -Id $PID; $me.ProcessorAffinity = [IntPtr]0xFF; $me.PriorityClass = 'BelowNormal'
   npx tsc --noEmit; npm run lint
   node --test tests/report-model.test.cjs tests/api-schemas.test.cjs tests/api-v2-schemas.test.cjs tests/canonical-json.test.cjs
   uv run pytest
   uv run python scripts/export_signals_api.py --check
   uv run python scripts/export_api_v2.py --check
   uv run python scripts/build_report_first_seen.py --check
   uv run python scripts/build_catalog.py --check
   npm run build
   ```
   재전사(`data/silver/reports/markdown`)가 끝났다면 `build_report_performance.py` 재실행 후 export 체인
   (`export_stock_charts → backtest_momentum → export_signals_api → build_forward_record → export_api_v2 → build_report_first_seen → build_catalog`)을 다시 돌려야 `--check` 가 통과한다.
3. 리뷰 표의 "확인 필요" 3건 점검 (H2 시뮬레이션 테스트, H4 문서, M8 문서).
4. 브라우저 미검증: 375px 폭, `/developers`·Try it 키보드 내비게이션, `/today/[date]` "지난 호" 도장 겹침.
5. 커밋: Claude 세션 작업 / 리뷰 수정 / Codex 작업을 나눠서. 생성 산출물 변경이 섞이므로 메시지에 재생성 사유 기재.
6. `feat/api-v2-medallion` 푸시 → Vercel 프리뷰에서 확인: `curl -I` 로 v2 헤더·ETag/304·404 problem+json·`%2B` 키,
   비-GET 메서드 응답, 미래 날짜 404 의 캐시 헤더.
7. 머지 순서: `audit/2026-10-p0` → `feat/kordoc-parsing` → `feat/api-v2-medallion`. 머지 시 main 의 daily 봇이 커밋한
   `data/markdown` 과 충돌 → 브랜치 레이아웃을 택하고 전사 재실행. **main 푸시는 사용자 승인 후에만.**

## 4. 알려진 리스크

- 생성 산출물(`parsed.csv`, `performance.csv`, `report-performance.json`)의 `source_file`·`source_md_url` 은 다음 파이프라인 실행 전까지
  `data/markdown/...` 구 경로. 리더는 양쪽 허용(`lake.current_rel`). 머지 후 재생성 전까지 GitHub 마크다운 링크 404.
- `data/bronze/prices/_ingest.jsonl` 은 CI 캐시에만 존재(git 제외) → 캐시 축출 시 원천 이력 유실. 커밋하면 연 ~80MB.
- `data/gold/reports/performance_summary.csv` 는 작성 스크립트가 없음(카탈로그에 producer 공란).
- v2 dated 스냅샷은 append-only — 한 번 커밋되면 수정 불가. 커밋 전에 링크 완전성 재확인.
