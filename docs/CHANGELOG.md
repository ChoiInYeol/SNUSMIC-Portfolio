# Changelog

판결 아카이브의 릴리스 기록. 최신이 위. 내부 작업 버전(v23 등)은 커밋 메시지의
버전 번호를, 릴리스 버전(v1.3.0 등)은 GitHub Release 태그를 가리킨다.

---

## Unreleased — REST API v2 · Medallion · 스키마·계보 지도 (2026-10-19)

- `feat/api-v2-medallion`은 kordoc 위에 쌓인 로컬 작업 브랜치. main 병합·푸시 없음.
- 리소스 중심 URI, OpenAPI 3.1·Zod 계약, 개발자 레퍼런스·요청 콘솔·Swagger UI.
  정적 파일보다 앞에서 메서드를 처리해 OPTIONS=204, 쓰기=405(problem+json)로 통일.
- Bronze/Silver/Gold 물리 재배치, 단일 경로 모듈, 시세 원천 기록·검증·격리·재생성,
  카탈로그 계보와 CI 커밋 정합 검증. Bronze 로그 재시도 후 파일 참조 보존 회귀 수정.
- 날짜별 판결일보·축쇄판·스냅샷 비교, 화면별 API 출처 링크, 전략 공유 URL.
  과거 지면은 first-seen 기준으로 리포트를 제한하고 부고는 당시 스냅샷으로 계산.
- 이동 후 깨질 1,462건의 전사본 링크를 `refresh_report_links.py`로 갱신하고 v2 재생성.
  수익률·파싱·source_file 등 연구 결과와 원천 식별자는 유지.
- `/developers/architecture`: React Flow·ELK, 계층 그룹·스키마 노드·FK/ETL 구분,
  검색·필터·확대축소·키보드 Sheet·상하류 계보·다크모드·벡터 SVG 내보내기.
- 선택적 FastAPI 메타데이터 서비스와 Next 프록시. 실제 파일 카탈로그 또는 SQLAlchemy DB
  reflection, 복합 PK/FK·뷰 지원. 서버 중단·재시작·실제 SQLite 통신 검증.
  실행과 배포 설정: `docs/METADATA.md`. 기본 파이프라인에 런타임 DB를 도입하지 않음.

---

## Unreleased — 2026-10 감사 P0 · 에이전트 규칙 · kordoc 엔진 (2026-10-19)

인수인계: `docs/HANDOFF-2026-10-19.md`. 감사 근거: `docs/AUDIT-2026-10-19.md`.

### 감사 P0 (`8987a7af9`, 브랜치 `audit/2026-10-p0`)

- **백테스트 시점 정합**: T-·U 파킹 레짐 게이트가 당일 종가·당일 200MA로 당일 파킹 수익을 정하던
  look-ahead 제거(`_regime_risk_on`, 전일 기준). Q 시가 진입 유닛도 전일 종가. 통합·월요일 오라클 테스트.
  **공표 성과 숫자는 아직 수정 전 코드 결과** — 재계산·헤드라인 재선정 필요.
- **전략 페이지**: 자산곡선 납입금선·벤치마크 4종 소실 회귀 복구, 캔들/전략 상세 오류 상태 + 재시도
  (shadcn Alert/Skeleton), aria-live, `error.tsx`/`not-found.tsx`, 375px 내비 넘침 수정.
- **스키마·API 계약**: `src/lib/api-schemas.ts`(zod) 단일 계약, openapi가 모든 게시 경로·필드 문서화,
  계약 테스트(pytest + node), `signal_date` null 작성기 버그, 날짜 스냅샷·해시 자산 캐시 헤더.
- **릴리스 게이트**: `export_signals_api.py --check`(재생성 대신 검증), refresh 워크플로가 봇 커밋 전
  계약·pytest·`next build` 실행.

### AI 에이전트 규칙 (`5a8c380fc`)

- `AGENTS.md` 단일 원천(데이터 계약 · shadcn 우선 UI 정책 · 등락 색 토큰 · 검증 명령),
  `CLAUDE.md`·`.cursor/rules` 는 import. 공식 shadcn Skill(`.agents/skills`), shadcn MCP(Claude Code·Cursor).

### 파싱 엔진 kordoc 교체 (진행 중, 브랜치 `feat/kordoc-parsing`)

- 전사 엔진 opendataloader(Java) + winocr(Windows) → **kordoc 4.22.13**(`tools/kordoc` 고정), 내장 PP-OCRv5 한국어 OCR.
  CI(Linux)에서도 OCR 동작. `ocr_fallback.py`·관련 의존성 삭제.
- 후보-채점 추출기 `scripts/report_extract.py`, 평가 하네스 `scripts/eval_extraction.py`,
  PDF 표지를 직접 읽은 정답셋 240건(`tests/fixtures/parse_gold`).
- 정답셋(company 224건) 기준: 기존 방식 전체 오류 19.4% / 틀린 값 4.1% → **kordoc + 새 추출기 13.2% / 2.1%**.
- kordoc 성능 함정 수정: OCR 모델이 캐시돼 있으면 `--ocr` 없이도 이미지를 자동 OCR(40배 느림) →
  텍스트층 단계는 빈 `KORDOC_MODEL_CACHE` 사용.
- 파이프라인 적용·전체 재전사·재파싱은 미완 — 인수인계 §3.

---

## v1.3.0 — 회귀 게이트 · DSR · 전진 기록 · CI 소생 (2026-06-13)

새 전략 없음. 백테스트를 "믿을 수 있게" 만드는 검증 인프라 릴리스.
내부 버전 v23~v27 + CI 수정 2건 (`e03f3e0a` ~ `0d2b01b2`).

### v23 — 회귀 테스트 게이트 (`e03f3e0a`)

- **`tests/` 신설 (38개 테스트)**: 실데이터가 아닌 **고정 시드 합성 유니버스**에서
  실행 — 일일 데이터 갱신과 무관하게 코드 변경만 잡아낸다.
  - 회계 코어 단위 테스트: KRX 호가단위 라운딩(절대 위로 안 올림), FX 환산
    (`_fx`/`set_usdkrw`), asof 패스트패스 ≡ pandas 시맨틱, `_close_trade`/
    `_try_enter` KRW 손익, Kelly 사이징 경계.
  - **v22 FX 버그 회귀 테스트**: 가격·환율 상수인 US 포지션이 월간 리밸런스를
    거쳐도 NAV가 평탄해야 함 — 버그 재발 시 NAV 붕괴로 즉시 실패. zero-NAV
    가드 계약(−98%는 회계 버그 신호)도 고정.
  - 샹들리에 행동 테스트: ATR을 손계산할 수 있는 경로에서 "스탑 이탈 → 익일
    시가 체결" 검증.
  - **골든 스냅샷**: 대표 전략 3종(고정보유/샹들리에/HRP)의 지표·거래 로그를
    `tests/golden/strategies.json`과 비교. 의도된 변경은
    `UPDATE_GOLDEN=1 uv run pytest` 로 재생성 후 diff 확인하고 커밋.
- **`tests.yml` CI**: `scripts/**`/`tests/**`를 건드리는 모든 push/PR에서 실행
  (~30초, KRX/yfinance 불필요).
- **`pipeline_health.py`**: daily CI 마지막에 산출물 카운트(수집 PDF·전사·파싱·
  성과 행), 파싱 이슈 증가량(+15 임계), 신호 신선도(as_of ≤ 4일)를 직전 실행
  (`data/health.json`)과 비교 — 이상 시에만 Telegram 운영 알림.
- 신호 스냅샷 아카이빙은 v21부터 이미 구현돼 있었음을 확인
  (`signals/{YYYY-MM-DD}.json` append-only) — v25 채점기의 데이터 소스.

### v24 — 다중검정 보정 + 워크포워드 (`76aa6b5b`)

- **Deflated Sharpe Ratio** (Bailey & López de Prado 2014): N=26개 변형을 같은
  데이터에서 시도해 최고를 고르는 선택 편향을 정량화. SR0 = 무정보 N회 시도의
  기대 최대 샤프(시도 간 샤프 분산 기반), 왜도·첨도 보정 포함.
  - **정직성 공시**: 헤드라인 U 전략은 PSR 0.9998이지만 **DSR 0.87 < 0.95** —
    26개 중 골랐다는 사실을 감안하면 유의성 보류. /strategy 히어로에 그대로 게시.
- **워크포워드 일관성**: NAV를 달력 6개월 윈도로 잘라 윈도별 수익률·샤프·MDD·
  vs KOSPI 비교. 재적합 없음(파라미터 대부분 문헌 고정; D+/U Optuna 파라미터는
  IS 적합이므로 OOS 윈도가 진짜 검증 — 별도 집계).
- 연구 테이블에 DSR·WF OOS 컬럼(유의성 색상, 툴팁 상세), `strategies.json` API에
  `dsr`/`walkforward`/`walkforward_oos` 필드 추가.

### v25 — 전진 트랙 레코드 + 합의 팩터 (`9cd853f0`)

- **`/track` 페이지 + `build_forward_record.py` + `/api/v1/forward.json`**:
  커밋 시점에 박제된 일별 신호 스냅샷의 매수 신호를 이후 실현 시세로 채점.
  구조적으로 out-of-sample — 사후 수정 불가. 채점 규칙: 진입 = 신호일 익일
  시가, 로컬 통화 종가 수익률, 동일 티커 7일 내 중복 제외.
- **학회 합의 팩터** (통계 ⑤): 발간 직전 90일 내 같은 종목을 커버한 학회 수별
  성과 — 단독 738건 더블 도달률 34.0% vs **2개 학회 78건 43.6% (리프트 1.25×)**.
  합의 에피소드 명부 포함.
- **배거 스크리너 상시화**: `build_stats.py`가 daily CI에 편입 — v20 이후
  일회성으로 동결돼 있던 /stats 후보 테이블이 매일 갱신.

### v26 — 백테스트 모놀리스 분할 (`f0fb9da3`)

- 7,392줄 `backtest_momentum.py` → `scripts/backtest/` 패키지 7모듈 + 얇은 shim:
  `config`(경로·파라미터) / `fx`(USDKRW 상태) / `warehouse`(로더·지표·고속 조회) /
  `accounting`(진입·청산·틱·달력) / `metrics`(결과·DSR·워크포워드) /
  `strategies`(전체 run_* + Optuna) / `reporting`(CSV·요약).
- 순수 이동(pure move) — 골든 byte-identical + 전체 백테스트 재실행 deep-diff
  **IDENTICAL**(generated_at 제외)로 검증. 외부 호출부(spo_portfolio, 테스트,
  CI 명령) 변경 없음.
- 가변 전역 처리: FX 상태는 `backtest.fx`에 단일 거주(테스트는 live attribute를
  저장/복원), `FORCE_RETUNE`은 모듈 attribute로 설정.

### v27 — 내실 다지기 (4트랙 감사) (`e4ba1245`)

파이프라인/엔진/프런트엔드/보안 병렬 감사. 크리티컬 0건. 신규 기능 없이 수정만.

**버그 수정**
- `build_forward_record`: 채점 실패(가격 파일 부재)한 티커도 dedup 마킹되어
  이후 7일의 정상 신호를 침묵 삭제하던 결함 → 기록된 신호만 마킹.
- `collect_reports`: SHA256 중복제거 누락(smic/ewha/voera에는 있었음) —
  같은 PDF가 URL만 다르면 중복 저장되던 문제. manifest 기반 sha 인덱스 추가.
- `export_signals_api`: strategy-marks를 wipe-then-write → **write-then-prune**
  (중간에 죽어도 서빙 디렉터리가 비지 않음). OpenAPI base URL
  `smic-easy.vercel.app` → `verdict-archive.vercel.app` (layout.tsx와 통일,
  docs/API.md·README 동일 수정).
- /track 링크 텍스트·href 불일치, README·API.md의 "25개 전략"(실제 26개) 정정.

**엔진 경화 (출력 중립 — deep-diff IDENTICAL 재검증)**
- Optuna 캐시 키에 `OPTUNA_CODE_TAG` 추가 — 지표 로직 수정 시 스테일 튜닝
  캐시가 조용히 재생되는 구멍 차단 (S-weights에는 이미 있던 가드).
- 샹들리에 fold 샤프: 수치적 0 표준편차(1e-18)가 Inf 목적함수를 만드는 경로 차단.
- HRP 재귀 이분: NaN 클러스터 분산이 `<= 0` 가드를 통과하던 구멍 차단.

**죽은 코드 제거**
- `react-bits-lite.tsx`(임포터 0) 삭제, framer-motion·recharts 의존성 제거
  (recharts는 v22에서 UI 제거 후 의존성만 잔존).
- 대시보드 훅의 미소비 `kpis`/`chartPoints` 연산, `LEVERAGE_BORROW_RATE`
  고아 상수, HRP 미사용 지역변수, 무효한 `bt._USDKRW` 정리 라인.

**위생**
- `package.json`의 `"latest"` 8종 → 락파일 버전 핀 고정 (Python은 이미
  `uv sync --frozen`; JS가 보안 리뷰에서 지적된 구멍).
- `build_stats` base_rate 0 가드, `transcribe --shard` 입력 검증, 스테일
  docstring(--school 목록, 상장폐지 티커 출처) 정정.

**의도적으로 고치지 않은 것** (기록 차원)
- `rank_pct` min-rank 동점 처리: 리뷰어가 지적했으나 추적 결과 동점 0.0
  순위는 소비되지 않고 실수 수익률 동점은 사실상 발생하지 않음 — 변경 시
  O/Q 전략 결과만 의미 없이 변동.
- 수집기 4벌 중복 코드: 통합은 수집기 테스트가 먼저 필요한 큰 리팩터링 — 보류.
- 레코드별 manifest 저장(O(n²) I/O): 크래시 안전성을 위한 의도적 설계 — 유지.
- CORS 와일드카드: 공개 읽기 전용 API 설계 — 유지.
- L/M/E 계열 미사용 전략 함수: "시험했고 실패했다" 기록용 의도적 보존 — 유지.

### CI 소생 (`0d2b01b2`)

푸시 직후 발견: **refresh-daily는 역사상 2회 실행, 둘 다 45분 타임아웃 — 봇
커밋 0건.** 일일 자동 갱신 루프가 한 번도 작동한 적이 없었다. 독립 원인 2개:

1. **수집기 전수 재열거**: collect_yig가 매일 게시글 130개 전부, collect_star가
   목록 전 페이지 + 게시글 281개 전부를 2.5초 간격으로 재방문 (스텝 36분).
   → **증분 단락**: "manifest에 파일 AND 전사 markdown 존재"인 게시글은 요청
   생략 (PDF는 gitignore라 러너에 없음 — markdown까지 확인해야 전사 안 된
   게시글이 영구 누락되지 않는다). star는 목록 페이지 전체가 기수집분이면
   조기 종료. 측정: yig 0건, star 목록 1페이지. `--full` 플래그가 기존 전수
   순회를 복원하며 수동 full-sweep 워크플로에 명시 연결.
2. **커밋 스텝의 사산(死産) 버그**: `git add data/pdfs`는 처음부터 작동 불능 —
   `data/pdfs/`는 레포 첫 커밋부터 gitignore(추적 PDF 0개)이고, 전부 무시된
   pathspec에 대한 git add는 exit 1로 스텝을 죽인다. 양쪽 워크플로에서 제거.
   PDF는 설계상 로컬/일회성(재다운로드 가능, manifest에 URL 보존), 전사본만 커밋.

결과: **사상 첫 완주(30분 50초) + 사상 첫 봇 커밋**(`20fa4763`), 신호 as_of
2026-06-12 정상화, 타임아웃에 막혀 있던 신규 STAR 리포트 13건 수집·전사,
전진 기록 첫 9건 박제.

---

## v1.2.0 — 비교 랩 개편 · 회계 버그 수정 (2026-06-11, `ca8ffdd3`)

- S/V 패밀리 FX 회계 버그 수정 (보유 US 포지션 무환율 평가 → MDD −100% 유령의
  근본 원인). zero-NAV 가드 추가.
- 판정 칩을 실행 시점 수치에서 자동 생성 (하드코딩 제거).
- 비교 랩: 전 전략 26개 온/오프 토글, lightweight-charts v5 멀티 오버레이.
- 헤드라인 타이브레이크(IS 샤프 → OOS 샤프 → 부의 비율) 도입 — SOTA는
  U 샹들리에+과열 스케일아웃.

## v1.1.0 — 시그널 API · 통계 · Docker (2026-06-11, `80985561`)

- 일일 트레이딩 시그널 정적 JSON API (+ OpenAPI 스펙), 크로스 플랫폼 Docker.
- KRW 환산(USDKRW), 호가단위 라운딩 스탑, 본문 읽기(전사 마크다운 뷰어).
- /stats "배거의 관상" — 발간 시점 공통 요인 통계.
- 25x 빠른 백테스트(가격 창고 캐시·벡터화), Telegram 일일 다이제스트,
  종목 차트 SOTA 거래 마크, Verdict Daily 1면.

## v1.0.0 — 6개교 이중 판결 + 전략 랩 (2026-06-11, `7dd005d7`)

- 6개 대학 투자학회 리포트 1,569건 수집·전사·파싱, point-in-time 검증 아카이브.
- 이중 판결(현재/전성기), 증거의 서가, 전략 랩 초기 구축.
- 보안: KRX 자격증명 히스토리 세척 (상세는 [OPS.md](OPS.md)).
