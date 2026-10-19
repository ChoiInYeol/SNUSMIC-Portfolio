# 데이터의 지도 · 메타데이터 서비스

페이지: `/developers/architecture`. React Flow와 ELK가 FastAPI 응답 하나를 기준으로
노드·그룹·FK·ETL·검색·Sheet·SVG를 만든다. 데모 테이블은 포함하지 않는다.

## 로컬 실행

`uv sync --frozen`, `npm ci` 후 서로 다른 터미널에서 실행한다. 종료는 각 터미널의 Ctrl+C.

```powershell
npm run dev:metadata
```

```powershell
npm run dev:local
```

- 화면: http://127.0.0.1:3100/developers/architecture
- FastAPI Swagger: http://127.0.0.1:8000/docs
- FastAPI OpenAPI: http://127.0.0.1:8000/openapi.json
- 메타데이터: http://127.0.0.1:8000/metadata/schema
- Next 프록시: http://127.0.0.1:3100/api/metadata/schema

`dev:local`은 Webpack을 쓴다. 이 Windows 환경의 Next 16.2.7 Turbopack 개발 모드에서
Noto Serif Google Font 쿼리 오류가 발생했다. 프로덕션 `npm run build`는 기존 설정을 유지한다.
DB 없이 실행하면 파일 레이크의 실제 카탈로그와 파일 필드를 조회한다.
서버를 끈 뒤 **다시 연결**을 누르면 오류가 표시되고 서버를 켠 뒤 다시 시도하면 복구된다.

## 실제 DB 연결

FastAPI 터미널에 환경 변수를 설정하고 서버를 재시작한다. URL은 서버에만 보관한다.

```powershell
$env:METADATA_DATABASE_URL = 'sqlite:///C:/absolute/path/to/existing.sqlite'
$env:METADATA_SCHEMA_LAYERS = '{"main":"silver"}'
npm run dev:metadata
```

SQLAlchemy가 테이블·뷰·컬럼·NULL 허용·복합 PK/FK를 실제 DB에서 조회한다.
데이터 행은 읽지 않는다. SQLite 드라이버는 내장되어 있고 다른 DB는 해당 SQLAlchemy 드라이버가 필요하다.
운영 연결에는 메타데이터 조회 권한만 가진 계정을 사용한다.

| 환경 변수 | 의미 |
|---|---|
| `METADATA_DATABASE_URL` | 없으면 파일 카탈로그, 있으면 DB reflection. 연결 실패 시 503, 파일로 대체하지 않음 |
| `METADATA_SCHEMAS` | 실제 스키마 이름, 쉼표 구분. 기본은 존재하는 bronze/silver/gold, 없으면 DB 기본 스키마 |
| `METADATA_SCHEMA_LAYERS` | 실제 스키마→bronze/silver/gold/unassigned JSON 매핑. 미매핑 스키마는 미분류 |
| `METADATA_LINEAGE_PATH` | DB ETL 의존성 JSON 파일 경로. FK만으로 ETL을 추측하지 않음 |
| `METADATA_CORS_ORIGINS` | 직접 브라우저 연결 허용 출처, 쉼표 구분. Next 프록시는 CORS 설정 불필요 |
| `METADATA_API_URL` | Next 서버의 FastAPI URL. 기본 `http://127.0.0.1:8000/metadata/schema` |
| `NEXT_PUBLIC_METADATA_API_URL` | 선택적 클라이언트 경로. 기본 `/api/metadata/schema`; 비밀값 금지 |

ETL 파일은 관계 객체의 배열이다. 각 항목은 `id`, `source`, `target`, `kind: "etl"`, `label`,
`source_columns`, `target_columns`를 갖는다. source/target은 실제 `schema.table` 식별자다.
없는 테이블이나 잘못된 FK 컬럼은 응답을 실패시킨다. 조회 범위 밖의 FK 대상 스키마도
`METADATA_SCHEMAS`에 포함해야 한다.

## 계약과 데이터 출처

- Python 원천 계약: `scripts/schema_metadata.py` Pydantic `SchemaMetadata` v1.0.
- 프런트 경계: `src/lib/schema-metadata.ts` Zod mirror. 잘못된 응답은 오류로 표시한다.
- `entities`: 스키마·계층·컬럼·PK·위치·생성기·종류(table/view/dataset).
- `relationships`: FK는 참조 테이블→대상, ETL은 입력→산출물. 복합 FK 컬럼 순서를 보존한다.
- `column_status`: DB 선언(`declared`), 파일 필드 관측(`observed`), 미검사(`unavailable`).
  CSV는 문자열, JSON은 최상위 필드의 관측 타입이며 DB 제약이 아니다. 파일의 PK/FK는 추측하지 않는다.
- 계보는 순환을 처리하며 모든 상·하류를 조회한다. Sheet에는 필터로 숨긴 노드의 계보도 표시한다.
- 서버·프록시 오류는 `503 application/problem+json`, `Cache-Control: no-store`.
  오류 때 마지막 성공 그래프를 대신 보여주지 않는다. FastAPI는 ETag·304를 지원한다.

## 정적 배포

FastAPI는 기존 정적 JSON API와 별개의 선택적 서비스다. 운영에서 실시간 조회하려면
FastAPI를 별도 배포하고 Next 서버의 `METADATA_API_URL`을 지정한다. 없으면 연결 오류를 표시한다.

외부 서버 없이 파일 레이크만 공개하려면 빌드 전에
`NEXT_PUBLIC_METADATA_API_URL=/metadata/schema.json`을 지정한다. 자동 fallback은 없다.
스냅샷·JSON Schema·FastAPI OpenAPI는 다음 작성기로만 재생성한다.

```powershell
uv run python scripts/schema_metadata.py
uv run python scripts/build_catalog.py
uv run python scripts/schema_metadata.py --check
```

SVG는 현재 검색·필터와 같은 노드·관계를 같은 레이아웃에서 내보낸다. 외부 폰트나
HTML foreignObject 없이 텍스트·경로를 사용하며 테마 색·방향·실선·점선을 보존한다.
노드는 최대 5개 컬럼을 표시하고 전체 컬럼은 Sheet에서 조회한다.
