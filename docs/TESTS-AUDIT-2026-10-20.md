# 테스트 중복 감사 — 2026-10-20

명세: `docs/HANDOFF-CLAUDE-2026-10-20.md` §1 "테스트 압축". 목표는 개수 감소가 아니라 **실제 중복과
유지보수 부담을 줄이고 독립 보장을 하나도 잃지 않는 것**이다. golden·생성 JSON은 손대지 않았다.

측정 조건: `uv run pytest --durations=0` BelowNormal 우선순위, xdist 없음, OMP/OPENBLAS=2.
다른 에이전트가 같은 시간대에 CPU를 쓰고 있어 벽시계 시간은 ±수 초 흔들린다. 파일별 시간은
pytest가 보고한 setup+call+teardown 합(0.005초 미만 항목은 pytest가 숨기므로 하한값)이다.
이 감사의 범위는 작업 시작 시점 HEAD(`48741892c`)에 있던 테스트 파일이다. 같은 시간대에 다른
에이전트가 추가하는 `test_funded_*`, `test_search_*`, `test_http_*` 등은 범위 밖이며 손대지 않았다.

## 1. 파일별 목록 (변경 전 기준)

| 파일 | 검증 대상 | 막는 실패 위험 | 함수 | 수집 | 시간(s) | 발견한 중복 |
|---|---|---|---:|---:|---:|---|
| `test_api_v2_contract.py` | 공표 `/api/v2` 2,028개 JSON ↔ `openapi.json`, 링크·self 링크, 읽기 전용 operation, 예제 유효성, 날짜 스냅샷=v1 이력, append-only, 리비전 content hash, v1 상세 URL rewrite, `--check` drift/orphan, NaN 입력, 동결 종목 prune 방지, trade-marks 선택성 | 스펙에 없는 필드·깨진 링크·동결 스냅샷 변경·리비전 위조·대량 삭제 | 18 | 2,120 | 27.53 | ① v1과 거의 같은 OpenAPI 검증기 `_errors`·`JSON_TYPES` 복제본 ② 링크 테스트 2개가 2,028개 파일을 각각 다시 파싱 ③ append-only 검사가 파일마다 `git show` 138회 (5.57s) ④ retired 리비전 테스트가 이미 매개변수 테스트가 검증한 `StrategyRevision` 스키마를 113개 리비전에 재검증 ⑤ canonical sha256 직렬화 코드가 `test_strategy_details.py`와 복제 |
| `test_api_contract.py` | 공표 `/api/v1` JSON ↔ v1 `openapi.json`(v1 상세는 v2 리비전 파일을 `StrategyDetail`로), deprecated 표기, 구체 경로 존재, `--check`, 같은 날 재실행 스냅샷 동결 | v1 동결 경로 파괴, 스냅샷 덮어쓰기 | 6 | 173 | 1.18 | ① v2 검증기의 열화 복제본: `const`·`pattern`·`date-time`·`anyOf`·범위를 **검사하지 않음**(v1 스펙에 `const` 1, `pattern` 1, `date-time` 4곳 존재 → 실제 검증 공백) ② `test_manifest_points_at_published_detail_assets`가 v2 파일의 `test_v1_strategy_detail_urls_are_served_from_v2_revisions` + `test_revision_is_content_addressed`와 경로 형식·대상 존재·strategy_key·content_hash 단언이 겹침 |
| `test_publication_safety.py` | 비유한 시가/종가 → 유한 JSON, 가격 없음도 커버리지 유지, health dry-run 무기록, 실패 시 비0 종료, 엄격 JSON 거부, 공표 형태 손상 감지 | NaN·Infinity 공표, 조용한 데이터 부패 | 8 | 26 | 2.17 | 손상 케이스 id가 `<lambda>0..5`라 실패 시 어떤 필드인지 안 보임. 6개 케이스가 매번 `strategy-backtest.json`을 디스크에서 다시 읽음 |
| `test_market_clock.py` | 시장별 일봉 마감 경계(DST), 미래 날짜 상한, GLD 캐시 갱신·빈 응답 거부, US 요청 범위 | 미래 데이터 사용, 4개월 묵은 벤치마크 공표 | 5 | 11 | <0.1 | 오래된 GLD 캐시 준비 4줄이 두 테스트에 복붙 |
| `test_strategy_details.py` | 상세 페이로드 보존, 해시 재사용/변경, 소스 필드 누락 시 KeyError, 원자적 쓰기 | 빈 스텁 공표, 해시 불일치 | 4 | 8 | <0.1 | canonical sha256 코드 복제(위 ⑤) |
| `test_lake.py` | Silver 가격 검증·격리, 원자적 교체, Bronze 레코더 append/덮어쓰기 금지, SIGTERM 플러시, Bronze→Silver 재현, 레거시 경로, 카탈로그 결정성 | Bronze 불변성 위반, 부분 쓰기 | 23 | 28 | 5.60 | 없음(케이스별 고유 데이터). 3.86s는 실제 동시 프로세스 append 검증이라 유지 |
| `test_accounting.py` | 틱 반올림, FX as-of, 달력, 체결·청산, Kelly | 회계 코어 손익 오류(v22 FX급) | 16 | 28 | 0.05 | 없음 |
| `test_financial_oracles.py` + `financial_oracle_vectors.py` | 손계산 오라클: 초기비용, 이자, FX 매도, ATR63, 휴장 큐, 컨센서스·MA·레짐 미래 차단, 납입 전 수익 금지 | 회계 오라클, no look-ahead | 14 | 14 | 0.04 | 없음 |
| `test_shared_execution.py` | 공통 실행 엔진: 시가 예산·US/KR 슬롯·FX, paper=backtest 일치, 공개 전 시가 금지, 동결 정책, 늦은 bar 차단 | 미래 데이터, paper/backtest 괴리 | 12 | 12 | 0.05 | `observation()` 빌더는 `test_paper_portfolio.py`와 이름만 같고 스키마 2.0 vs 1.0으로 다름 → 중복 아님 |
| `test_paper_portfolio.py` | 구 모의계정 replay, 늦은 공표, 텔레그램 분할·영수증 | 미래 종가, 토큰 노출, 중복 발송 | 5 | 5 | <0.1 | 없음 |
| `test_execution_cutoffs.py` | 첫 시가가 무효면 이후 시가로 대체 안 함, 시가 진입 ATR은 당일 극값 무관 | 미래 데이터 | 2 | 2 | <0.1 | `test_publication_safety`의 무효 시가 케이스와 대상 함수는 같지만 단언이 다름(이후 bar 대체 금지) → 독립 |
| `test_golden.py` / `test_golden_gate.py` | 합성 유니버스 3전략 골든 스냅샷, 0거래 퇴화 방지, CI에서 골든 갱신 금지 | 회귀, 골든 우회 | 2 / 1 | 2 / 1 | 0.24 | 없음 |
| `test_chandelier.py`, `test_rebalance_fx.py`, `test_dsr_walkforward.py` | 스탑 규칙, v22 FX 리밸런스 회귀, DSR·워크포워드 | 전략 회귀 | 2/2/4 | 2/2/4 | 0.73 | 없음 |
| `test_schema_metadata.py` | 실제 카탈로그, SQLite 반영, 깨진 관계 거부, FastAPI 304/405/CORS, 503 무자격증명, 스냅샷 drift | 가짜 키, 자격증명 노출 | 6 | 6 | 1.78 | 없음 |
| `test_bronze_backup.py` | 복원·중복제거, 손상/충돌 시 부분복원 금지, 경로 탈출 거부, 증분 | Bronze 백업 손상 | 4 | 8 | 0.18 | 없음 |
| `test_pipeline_extraction.py`, `test_report_extract.py`, `test_transcription.py` | 표지 가격/등급 추출, OCR 대상 선정, 부분 실패 차단, 품질 게이트 | 잘못된 외부 데이터 수용 | 11/5/10 | 11/15/10 | 1.58 | 없음 |
| `test_report_first_seen.py`, `test_report_links.py`, `test_research_review.py` | 최초 수집일(no look-ahead), 링크 이동, 검토 원자성·불량 검토 거부·경로 탈출 | 늦게 수집한 리포트 소급, Silver 오염 | 6/2/3 | 6/2/9 | 0.21 | 없음 |
| `conftest.py` | FX 전역 복원, 합성 OHLCV, 세션 유니버스 | 테스트 간 누수 | – | – | – | 없음 |

Node(`node --test tests/*.test.cjs`, 55개, 1.9s, 파일 병렬):

| 파일 | 검증 대상(TS zod/런타임 경계) | 테스트 | 발견한 중복 |
|---|---|---:|---|
| `api-v2-schemas.test.cjs` | 공표 v2 문서 전체 ↔ `src/lib/api-v2.ts` zod, 목록 누락, 불량 캔들·외부 링크·schema_version 거부 | 4 | TS 트랜스파일·평가 로더 복제 |
| `api-schemas.test.cjs` | v1 latest·상세 ↔ `api-schemas.ts` zod, 불량 trade 거부 | 28 | 로더 복제 |
| `canonical-json.test.cjs` | 브라우저 canonical hash ↔ Python 해시, 숫자 원문 보존, 위조 감지 | 3 | 로더 복제 |
| `api-http.test.cjs` | `src/proxy.ts` GET/HEAD 통과, OPTIONS 204, 쓰기 405+Allow+problem JSON | 4 | 로더 복제 |
| `report-model.test.cjs` | 리포트 레코드 격리(대체값 금지), 봉투 거부, NaN 격리 | 8 | 로더를 재귀 `moduleRequire`로 두 번 복제 |
| `dashboard-transport.test.cjs` | 홈 압축 투영 무손실, review/paper/robustness zod | 3 | 로더 복제 + **cwd 의존 상대 경로**(저장소 루트 밖에서 실행하면 실패) |
| `schema-metadata.test.cjs` | 메타데이터 focus/filter/lineage, ELK 레이아웃, SVG 이스케이프 | 5 | 로더 복제 |

**Python과 Node는 서로 대체하지 않는다.** Python은 작성기 산출물 ↔ OpenAPI 계약을, Node는 같은
산출물 ↔ 웹이 실제로 쓰는 TS zod 경계를 검증한다. 같은 JSON을 두 번 읽지만 서로 다른 계약이다.
`api-v2-schemas`의 전수 검사는 실패 목록에 파일과 스키마 이름을 남기므로 그대로 두었다.

## 2. 변경 내용

1. **`tests/_contract.py` (신규, 수집 안 됨)** — 공용 오라클.
   - `OpenApi`/`openapi(path)`: v1·v2가 같은 검증기를 쓴다(스펙은 세션당 1회 로드).
     v1은 그동안 빠져 있던 `const`·`pattern`·`date-time`·`anyOf`·범위 검사를 얻었고, 현재 공표물은 모두 통과했다.
   - `canonical_sha256`: 작성기와 독립된 테스트 측 content-hash 오라클.
   - `committed_blobs`/`changed_since_head`: `git ls-tree` 1회로 HEAD blob id를 얻고 작업 파일의 git blob
     sha1과 비교한다. `git show HEAD:path` 바이트 비교와 같은 의미이며 실제 저장소 138개 동결 파일에서 두
     방식의 결과가 동일함을 교차 확인했다. 검사 대상이 0개면 실패하도록 비공허 단언을 추가했다.
2. **`tests/test_contract_oracles.py` (신규)** — 공용 오라클의 음성 케이스. 기존에는 검증기 자체가
   아무것도 거부하지 않아도 ~2,300개 계약 테스트가 초록이 되는 구조였다. 미문서 필드(최상위·`$ref` 내부·
   `additionalProperties:false`), 필수 누락, 타입, bool≠integer, 범위, enum, const, pattern, date, date-time,
   anyOf, 배열 원소, additionalProperties 스키마를 매개변수 id로 하나씩 검증한다. 미문서 필드 규칙을 지우는
   변이를 넣으면 3개 케이스가 실패함을 확인했다. append-only 오라클의 "수정 감지·미커밋 제외" 테스트도 여기 둔다.
3. `test_api_v2_contract.py` — 검증기 복제본 삭제, 링크/self 링크 테스트는 모듈 fixture 1회 파싱을 공유(두 테스트
   이름과 단언은 유지, 실패 메시지는 파일별 목록), append-only는 git 1회 호출, retired 리비전 테스트의 스키마
   재검증 제거, v1 매니페스트의 `as_of` 단언을 흡수.
4. `test_api_contract.py` — 검증기 복제본 삭제 → 공용 검증기, 중복 매니페스트 테스트 제거(아래 대응표).
5. `test_publication_safety.py` — 손상 케이스·엄격 JSON 케이스에 필드명 id
   (`latest.counts.open_positions=bool` 등), 원본 텍스트는 모듈 fixture로 1회 읽고 케이스마다 새로 파싱.
6. `test_market_clock.py` — `stale_gld_cache` fixture로 복붙 준비 코드 통합.
7. `test_strategy_details.py` — 공용 `canonical_sha256` 사용.
8. **`tests/_ts-module.cjs` (신규, `*.test.cjs`가 아니라 단독 실행 안 됨)** — 7개 Node 파일에 복제된
   TS 트랜스파일·평가 로더와 `readJson`을 하나로. 경로는 저장소 루트 기준으로 해석해
   `dashboard-transport.test.cjs`의 cwd 의존을 없앴다(다른 디렉터리에서 실행해 3/3 통과 확인).

변경하지 않은 것: `conftest.py`, golden, 생성 JSON, 회계/미래차단/Bronze/백업/추출 테스트(실제 중복 없음).

## 3. 제거·병합 대응표

| 제거/병합된 검사 | 이제 같은 보장을 담당하는 테스트 |
|---|---|
| `test_api_contract.py::test_manifest_points_at_published_detail_assets` — `manifest.as_of == latest.as_of` | `test_api_v2_contract.py::test_v1_strategy_detail_urls_are_served_from_v2_revisions` (단언 그대로 이동) |
| 〃 — `asset.path == strategy-details/{key}/{hash}.json`, rewrite 대상 파일 존재 | 〃 (기존에 동일 단언 보유) |
| 〃 — `detail.strategy_key == key`, `detail.content_hash == asset.content_hash` | `test_api_v2_contract.py::test_revision_is_content_addressed[...]` — 대상 파일명은 `{key}/revisions/{hash}.json`이고 이 테스트가 `content_hash == 파일명`, `strategy_key == 상위 디렉터리`를 113개 리비전 전부에 단언 |
| `test_api_v2_contract.py::test_revisions_of_retired_strategies_stay_valid_without_a_parent`의 `StrategyRevision` 스키마 재검증 | `test_api_v2_contract.py::test_published_document_matches_openapi[strategies/*/revisions/*.json]` — 모든 리비전이 openapi 경로 `/api/v2/strategies/{strategy_id}/revisions/{sha256}`의 200 스키마 `StrategyRevision`으로 매개변수 검증됨(retired 포함). 부모 부재 허용·prune 금지 단언은 원래 테스트에 남음 |
| `test_every_link_resolves...`의 prices.json 별도 루프(`instrument` 링크 존재, `self == URI`) | 같은 테스트(prices의 모든 링크 값 존재 — 기존보다 넓음) + `test_self_links_match_their_uri`(self == URI) |
| `test_api_contract.py`의 v1 `_errors` 검증기 | `_contract.OpenApi.errors` (상위집합) + `test_contract_oracles.py` 음성 케이스 |
| `test_api_v2_contract.py`의 v2 `_errors`/`_resolve` | 동일 코드가 `_contract.OpenApi`로 이동, 모듈 내 이름(`_errors`, `_resolve`, `SPEC`)은 유지 |
| `_committed()` + 파일별 `git show` | `_contract.changed_since_head` + `test_contract_oracles.py::test_append_only_oracle_flags_edited_bytes_and_skips_uncommitted` + 비공허 단언 |

보존 확인(삭제·약화 없음): 회계 오라클(`test_financial_oracles`, `test_accounting`), 미래 데이터 차단
(`test_shared_execution`, `test_execution_cutoffs`, `test_market_clock`, `test_report_first_seen`), Bronze 불변성
(`test_lake`, `test_bronze_backup`), 동결 스냅샷·append-only(`test_dated_snapshots_mirror...`,
`test_append_only_files_never_change_once_committed`, `test_check_freezes_dated_snapshot_on_same_day_rerun`),
publication safety, 404/405·캐시(`api-http.test.cjs`, `test_schema_metadata`), 잘못된 외부 데이터 거부,
golden gate(`test_golden`, `test_golden_gate`).

## 4. 전후 비교

| 항목 | 전 | 후 | 비고 |
|---|---:|---:|---|
| Python 테스트 코드(기존 26파일, 줄) | 2,934 | 2,831 | −103 (검증기 복제·중복 테스트·복붙 준비 코드) |
| 신규 공용 모듈 `_contract.py` | – | 119 | 두 검증기 + hash + git 오라클을 1곳에 |
| 신규 오라클 음성 테스트 `test_contract_oracles.py` | – | 87 | **새 보장**(검증기 자체의 거부 능력). 이전에는 없던 검사 |
| Python 합계 | 2,934 | 3,037 | 순증은 전부 새 음성 검사. 중복 코드는 줄었다 |
| Node 테스트 코드(7파일 + 로더, 줄) | 381 | 343 | −38, 로더 7벌 → 1벌 |
| Python 수집 수 | 2,505 | 2,522 | −1(중복 매니페스트), +18(오라클 음성 케이스) |
| Python 테스트 함수 | 171 | 174 | −1 매니페스트, +4 오라클 함수 |
| Node 테스트 | 55 | 55 | 동일 |
| `test_api_v2_contract.py` 단독(A/B 연속 실행) | 27.53s | 21.00s | −24% |
| └ `test_append_only_files_never_change_once_committed` | 4.09s | <0.14s | `git show` 138회 → `git ls-tree` 1회 |
| └ 링크 + self 링크 2개 테스트 | 3.66s | 1.88s | 2,028개 파일 2회 파싱 → 1회(공유 fixture) |
| └ retired 리비전 테스트 | 1.33s | <0.14s | 중복 스키마 재검증 제거 |
| 변경 4파일 묶음(v2·v1·publication·oracles) A/B 2회 | 29.3s / 39.5s (구 publication 6케이스는 위치 의존으로 즉시 에러 → 실제보다 짧게 잡힘) | 26.1s / 29.6s | 동시 부하로 변동 큼 |
| 전체 `uv run pytest` | 2,505 passed (기준 실행, `-qq`로 벽시계 미표시) | 2,522 passed, 47.82s (다른 에이전트 부하 중) | 무변경 파일도 40~60% 느려진 부하 상태라 전체 시간 비교는 하지 않는다 |
| Node `node --test tests/*.test.cjs` | 55 pass, 1.92s | 55 pass, 1.84s | 병렬 실행이라 차이 미미 |

시간 이득의 대부분은 API v2 계약 파일에 있다(전체 실행 시간의 절반 이상). 나머지 파일은 함수별로 고유한
데이터와 단언을 갖고 있어 병합하면 실패 위치만 흐려지므로 유지했다. 공표 v2 JSON 2,028개를 파일별로
검증하는 매개변수 테스트(2,028건)는 실패한 파일을 바로 알려 주는 의도된 설계라 줄이지 않았다.

## 5. 남은 제안 (이번 변경 범위 밖)

- `test_api_v2_contract.py::test_published_document_matches_openapi` 2,028건이 전체 시간의 대부분이다. 큰 비용은
  `instruments/*/prices.json`의 JSON 파싱이다. 더 줄이려면 가격 시계열 문서를 별도 마커로 분리해 개발 중 선택
  실행하고 최종 게이트에서만 전수 실행하는 방법이 있다. 최종 게이트 규칙(AGENTS.md)을 바꾸는 일이라 제안만 한다.
- 엄격 JSON 거부는 `pipeline_health._load_strict`(None 반환), `export_api_v2._load_json`(예외),
  `export_signals_api._write_json`(쓰기 거부) 세 경로에서 따로 검사한다. 함수와 실패 계약이 달라 중복이 아니다.
