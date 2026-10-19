# 추출값 검토와 Silver 교정

`/developers/review`는 실제 `parsed.csv`의 파싱 이슈·QA 플래그와 가격/상승여력 불일치를 검토한다.
파싱 이슈 외 QA 플래그도 포함하므로 검토 건수는 기존 이슈 207건보다 많다. 플래그만으로 오답으로 단정하지 않는다.

목록은 작은 Gold 색인, 전사 구간과 근거 후보는 보고서를 선택할 때 별도로 가져온다.
원문 PDF/수집 페이지·전체 전사 링크, 표지 전사, 필드별 기존값과 값 일치 후보를 함께 제공한다.
일치 후보는 단순 문자열 검색 결과이며 추출기의 확정 근거나 신뢰도 점수가 아니다.
숫자 단위·표 위치·발간일을 PDF와 대조하고 교정 사유와 원문 근거를 남긴다.

1. 검색 후 보고서를 열어 필드를 수정한다. 빈 값은 null이다.
2. 검토자·사유·근거를 입력하고 `review-corrections.json`을 내보낸다.
3. 로컬에서 아래 작성기로 검증·반영한다.

```powershell
uv run python scripts/build_research_review.py --apply review-corrections.json
```

작성기는 원문 SHA-256·기존 추출값·기존 교정을 대조한다. 오래된 제안, 중복 보고서,
교정 키 basename 충돌, 허용되지 않은 필드, 비유한/음수 가격·잘못된 날짜·근거 누락은 거부한다.
Silver `corrections.json`에 변경과 `_review_history`를 같은 원자적 쓰기로 저장한다.
원천 PDF·전사는 수정하지 않는다. 제안 다운로드는 반영·승인이 아니며 공개 쓰기 API는 없다.

반영 후 performance → review/search → first-seen/charts → backtest → v1/forward/v2/stats →
health → metadata → catalog를 재생성하고 게시 게이트를 통과시킨다.
정답셋·golden은 교정 UI에서 변경하지 않는다.

서빙 계약: `schema_version=1.0`; 작성기는 `build_research_review.py`,
TS 경계는 `src/lib/research-review.ts`. 목록 `/research/review.json`, 상세 `/research/reviews/{sha256}.json`.
상세 URL의 해시는 보고서 식별자 해시이며 콘텐츠 불변 캐시를 뜻하지 않는다.
