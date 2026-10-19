# kordoc 파이프라인 연결·전체 재전사 기록

브랜치: `feat/api-v2-medallion`. 원천은 보존하고 Silver 전사·파싱과 Gold 서빙 산출물을 재생성한다.
성과 기준일은 마지막 완료 거래일인 `2026-10-19`다. 검증·재계산 상태는 아래 기록을 따른다.

## 전사와 추출기

- PDF 경로 1,588건의 전사를 완료하고 최신 kordoc 헤더·비어 있지 않은 본문을 검증했다.
  총 Markdown은 1,649건이며, 로컬 PDF가 없는 기존 61건은 이전 커밋과 비교해 변경 없이 보존했다.
- OCR 적용은 186건, 3,918쪽이다. 엔진은 kordoc **4.22.13**, 기존 내장 모델·해상도를 유지했다.
- EIA의 빈 파일 별칭 1건은 같은 수집 항목의 정상 원본을 찾아 매니페스트 SHA-256을 검증해 재사용했다.
  빈 파일과 정상 원본 모두 수정하지 않았다. 별칭의 first-seen도 최초 수집일로 연결한다.
- 텍스트층 단계는 빈 모델 캐시를 사용한다. 필요한 페이지 범위를 지정해 `--ocr-force`로 인식한 후,
  원래 페이지 순서와 나머지 본문을 보존하며 병합한다. 숫자가 한자로 매핑되는 글꼴 10건과
  종목코드가 겹쳐 나오는 오래된 PDF 17건도 해당 페이지를 OCR했다. 숫자 치환표를 추측해서 적용하지 않는다.
- `--no-images`에서 실제 파일 없이 생성된 `image_001.png` 형태의 자리표시자를 작성기에서 제거했다.
  기존 전사본은 `--normalize-current`로 정규화했다. 실제 외부 이미지 링크는 유지한다.
- 완료 문서별 원자적 저장, 일부 실패 시 nonzero 종료, ONNX intra-op 2/inter-op 1 및 이미지 처리 1스레드 제한.
  전체 실행은 4 jobs 이하·8개 논리 CPU affinity·BelowNormal 우선순위를 유지했다. Docker에도 설정 모듈을 포함한다.
- 운영 파서는 후보-채점 가격 추출기, 값이 없을 때의 등급 정규식 fallback, 명시적 N/R 보호를 사용한다.
  GFM 셀·줄바꿈·수집 제목의 종목코드를 읽으며 산업 보고서의 커버리지 목록을 단일 회사로 오인하지 않는다.

## 정확도 — PDF에서 읽은 company 정답 224건

5필드, 총 1,120개 값을 평가했다. 오류는 wrong+missed+spurious, 틀린 값은 wrong+spurious다.
평가는 `parse_report`와 수집 힌트·QA를 포함하며, 시세·수동 교정 후 전체 파이프라인의 모든 값을 보증하는 수치는 아니다.

| 필드 | 기존 오류 / 틀린 값 | 운영 파서 + kordoc 전체본 오류 / 틀린 값 |
|---|---|---|
| 등급 | 4.5% / 2.7% | 3.1% / 1.3% |
| 목표가 | 17.4% / 8.0% | 11.6% / 4.9% |
| 발간가 | 29.9% / 7.1% | 17.0% / 1.3% |
| 상승여력 | 35.3% / 1.8% | 13.4% / 3.6% |
| 티커 | 9.8% / 0.9% | 9.8% / 0.9% |
| **전체** | **19.4% / 4.1%** | **11.0% / 2.4%** |

오류 217→123개, 틀린 값 46→27개다. 상승여력의 틀린 값은 여전히 개선 대상이다.
회귀 게이트는 동일한 정답셋 전체를 사용해 전체 오류 ≤15%, 틀린 값 ≤3%를 검사한다.
정답 라벨·golden은 수정하지 않았다. 기존 정규식은 평가용 `extractor="legacy"`에만 남겼다.

## 전체 파싱 비교

| 항목 | 이전 | 이후 |
|---|---:|---:|
| 파싱 행 | 1,647 | 1,647 |
| 티커 있음 | 1,466 | 1,475 |
| 발간일 있음 | 1,622 | 1,619 |
| 등급 있음 | 1,586 | 1,587 |
| 목표가 있음 | 1,504 | 1,498 |
| 발간가 있음 | 1,285 | 1,332 |
| 상승여력 있음 | 1,013 | 1,375 |
| 파싱 이슈 행 | 238 | 207 |

회사 보고서 안에서는 목표가 1,394→1,423개, 발간가 1,205→1,272개, 상승여력 944→1,302개로 늘었다.
성과 대상 회사 보고서는 1,471건이며 1,444건에 시세가 연결됐다. 산업 보고서는 성과 채점에서 제외한다.

## 시세·계보

- 국내 시세 캐시 712종목을 사전 갱신했다. 전체 성과 생성은 790개 종목 그룹을 조회한다.
- 6월에 멈춘 지수·환율 6개를 갱신했다. KOSPI·KOSDAQ은 10월 19일,
  S&P500·NASDAQ·GLD·USDKRW는 당시 완료 일봉인 10월 16일까지 확보했다.
  정규장·DST를 고려한 일봉 상한과 오래된 벤치마크 거부 동작은 [데이터 레이어](DATA-LAYERS.md)에 설명했다.
- 새 공식 종목명 조회는 수집 시각·벤더 버전과 함께 Bronze 관측 로그에 추가한다.
  기존 `krx_names.json` 스냅샷을 덮어쓰지 않는다.

## 재생성·검증

재계산: performance → first-seen → charts → backtest → v1 → forward-record → v2 → stats → health → metadata → catalog.
과거 dated 스냅샷과 콘텐츠 해시 revision은 보존한다. API 작성기·카탈로그의 `--check`로 결과를 확인한다.

최종 검증 (2026-10-20):

- `npx tsc --noEmit`, `npm run lint`: 통과.
- Node 테스트 51건, `uv run pytest` 2,441건 통과. FastAPI 테스트 의존성의 Starlette deprecation 경고 1건.
- v1·v2 작성기, first-seen, 메타데이터, 카탈로그의 `--check` 모두 통과.
- 파이프라인 health 검사 통과. Telegram 환경을 비우고 baseline만 갱신했으며 알림을 보내지 않았다.
- `npm run build`: 889개 정적 페이지 생성 성공.
- 실제 production 서버에서 1440px·375px의 개발자 문서·신문·축쇄판·종목·전략 화면,
  Swagger 로딩, 키보드 요청·탭·지난 호 이동 검증. 가로 넘침·브라우저 오류 없음.
- 기존 회계 작성기가 출력하는 revision 거래의 `pnl_krw`를 OpenAPI·Zod에 명시했다.
  기존 revision 바이트·버전은 보존하며 새 필드를 강제하지 않는다.
- health 작성 후 metadata, 마지막에 catalog를 생성하도록 두 refresh 워크플로 순서를 수정했다.
- 원천 Bronze 파일과 `tests/golden` 수정 없음. 현재 브랜치에만 커밋하며 main 병합·푸시 없음.

UI는 Bronze 구리색·Silver 파랑·Gold 황금색, FK 보라·ETL 청록으로 구분하고 블루·화이트 바탕과 산세리프 제목을 쓴다.
기존 shadcn Button·Input·Select·ToggleGroup·Sheet·Table·Alert·Skeleton·Empty를 재사용했다.
새 UI primitive 설치 없이 기존 스키마 노드와 SVG 작성기의 색상을 조정했다.
27개 노드·44개 관계에서 검색·필터·키보드 Sheet·SVG·다크모드·375px 검증을 통과했다.

재현 명령:

```powershell
uv run python scripts/transcribe_pdfs.py --jobs 4
uv run python scripts/transcribe_pdfs.py --normalize-current
uv run python scripts/eval_extraction.py --baseline-dir cache/baseline/data/markdown --kordoc-dir data/silver/reports/markdown --json cache/kordoc-rollout-full-eval.json
uv run python scripts/build_report_performance.py --as-of 2026-10-19 --sleep 0.3
```

실행 전 CPU 제한은 [인수인계](HANDOFF-2026-10-19.md)의 설정을 적용한다.
