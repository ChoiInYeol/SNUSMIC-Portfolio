# 텔레그램 명령과 공유 방 권한

명령은 기존 Vercel의 `POST /api/telegram/webhook`으로 받는다. 조회는 배포된 최신 SOTA 모델에서 즉시
응답하고, 데이터 재수집·백테스트는 GitHub Actions `refresh-daily`에서 실행한다. 접수가 빨라도 계산 완료는
수 분 이상 걸릴 수 있다. 완료 후 발송되는 성공 메시지는 발행 검사를 통과하고 main에 커밋된 데이터만 사용한다.

## 권한

사용자가 확정한 정책: **초대받은 참여자는 조회만, 갱신 실행은 관리자 본인만**.

| 기능 | 참여자 | 관리자 |
|---|---|---|
| `/status`: SOTA, NAV, 평가일, KOSPI 200MA 레짐 | 허용 | 허용 |
| `/holdings`: 모델 주수, 현재 비중, 평가손익 | 허용 | 허용 |
| `/signals`: 매수 대기, 스탑 근접·청산 대기 | 허용 | 허용 |
| `/trades`: 최근 매수와 매도 체결 | 허용 | 허용 |
| `/help`: 역할에 맞는 명령 목록 | 허용 | 허용 |
| `/refresh`: 리포트·시세 수집, 백테스트, 발행 | 차단 | 허용 |
| `/chatid`: 새 공유 방 ID 확인 | 차단 | 허용 |

명령 응답은 명령을 보낸 방에만 보낸다. 일일 요약과 갱신 완료 알림은 `TELEGRAM_CHAT_ID`로 보낸다.
`/status`, `/holdings`, `/signals`, `/trades`는 기본적으로 표 형식 PNG 운용 원장으로 응답한다.
여러 장은 사진 앨범 하나로 묶고 한 장에 최대 24행을 표시한다. 진입일, 수량, 가격, 비중, 손익을 열로 정렬한다.
흰색 바탕, Pretendard 서체, 이익 빨강과 손실 파랑을 사용한다. 한글 폰트와 색상은 로컬 자산만 사용한다.
`/holdings text` 또는 `/holdings 텍스트`처럼 입력하면 같은 열과 값을 TSV 텍스트로 볼 수 있다(네 조회 명령 모두 지원).
이미지 생성이 실패하면 같은 검증된 데이터를 텍스트로 표시한다. 전송 실패를 성공으로 처리하지 않는다.
이미지는 인증된 요청에서만 생성하며, 외부 이미지 주소나 영구 저장 파일을 만들지 않는다.
동일한 데이터와 명령의 렌더링은 최대 8항목의 프로세스 메모리 캐시에서 재사용한다.
`/refresh` 완료 알림은 짧은 결과와 `/status` 안내만 보낸다.
긴 대시와 가운데점 구분자는 사용하지 않는다.
명령 메뉴도 기본은 조회 전용이며, 관리자 개인 대화와 등록된 그룹의 관리자에게만 운영 명령 메뉴를 등록한다.
명령은 허용 목록의 방과 관리자 개인 대화에서만 받는다. 그룹의 다른 참여자를 관리자로 승격하는 명령,
임의 스크립트 실행, 비밀값 조회, 계좌 개시·취소·설정 변경 명령은 제공하지 않는다.
전달된 메시지·수정된 메시지로 운영 명령을 재실행하지 않는다. 그룹 익명 관리자 발신은 사용자 ID를
검증할 수 없으므로 처리하지 않는다. 그룹에서는 `/status@봇이름`처럼 명령을 보내면 기본 privacy mode에서도 전달된다.

## 최초 연결

기존 GitHub Actions secrets `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `VERCEL_TOKEN`,
`VERCEL_PROJECT_ID`, `VERCEL_ORG_ID`를 사용한다. 여기에 저장소 한정 fine-grained 토큰을
`TELEGRAM_ACTIONS_TOKEN`으로 등록한다. 권한은 이 저장소의 **Actions: Read and write**다.
개인 gh CLI 로그인 토큰을 복사해서 서버에 설치하지 않는다.

```powershell
gh workflow run telegram-webhook-setup.yml --ref main
```

설정 workflow는 입력을 검사하고 Vercel production 환경에 Secret을 등록한다. main을 새로 배포하고
보호된 엔드포인트가 동작하는 것을 확인한 뒤 Telegram `setWebhook`·`setMyCommands`를 설정한다.
토큰·웹훅 서명 값은 로그에 쓰지 않는다. 설치 시 실제 포트폴리오 메시지는 전송하지 않는다.
조회만 먼저 연결하려면 `-f read_only=true`를 지정한다.

## 공유 그룹 연결

1. 그룹을 만들거나 기존 그룹에 봇을 추가한다.
2. 관리자가 그룹에서 `/chatid@kor_univ_portfolio_report_bot`을 보낸다. 이 명령만 미등록 방에서도 관리자에게 허용한다.
3. 반환된 그룹 ID를 GitHub Actions secret `TELEGRAM_ALLOWED_CHAT_IDS`에 추가한다(여러 방은 쉼표로 구분).
4. setup workflow를 다시 실행한다. 연결 전에는 그 그룹의 일반 참여자에게 모델 데이터를 응답하지 않는다.

현재 `TELEGRAM_CHAT_ID`가 관리자 개인 대화면 관리자 ID는 자동으로 확정한다. 기본 알림 목적지를 그룹으로
바꿀 때는 `TELEGRAM_CHAT_ID`를 그룹 ID로, `TELEGRAM_ALLOWED_USER_ID`를 관리자 **사용자** ID로 등록한다.
그룹 ID 자체를 관리자 ID로 사용하지 않는다. 기본 목적지 그룹은 별도의 허용 목록 없이 조회가 허용된다.
관리자 개인 대화는 그룹 연결 후에도 운영 명령을 사용할 수 있다.

## 처리·실패 기준

- 웹훅 secret 헤더를 상수 시간 비교로 검증하고, 발신 사용자·방을 검사한 뒤에만 외부 요청을 한다.
- `/refresh`는 main의 고정 workflow만 실행한다. 입력 문자열을 셸 명령으로 실행하지 않는다.
- 이미 진행 중인 갱신이 있으면 실행 링크를 돌려준다. 같은 Telegram update ID의 재전송도 기존 실행을 찾는다.
- 10분 넘게 지연된 갱신 명령은 실행하지 않는다. Telegram 연결은 `max_connections=1`로 등록한다.
  네트워크 타임아웃은 dispatch가 도착했는지 불명확할 수 있어 자동 재시도하지 않고 실행 목록 확인을 안내한다.
- 조회는 최신 완료 일봉의 저장 결과다. 평가일이 3일 넘게 뒤처지면 날짜와 지연 가능성을 표시한다.
- SMIC가 HTML·빈 목록·잘못된 JSON을 반환하면 수집기는 최대 3회 재시도한다. 끝내 실패하거나 PDF를
  내려받지 못하면 종료 코드 1로 발행을 중단한다. 기존 보고서가 있다는 이유로 수집 실패를 성공으로 표시하지 않는다.
- SOTA 데이터는 Python 작성기와 zod 독자가 평가액·수량·비중·손익을 검증한다. 잘못된 생성물은 빌드를 실패시킨다.

원문 파싱을 고친 뒤 이미 확보한 시세로 재현할 때는
`uv run python scripts/build_report_performance.py --cached-prices-only --as-of YYYY-MM-DD`를 사용할 수 있다.
이 모드는 시세를 새로 조회하지 않으며 손상된 캐시는 실패시킨다. 없는 시세는 명시적인 `No quote`로 남긴다.
이후 백테스트·API·색인·메타데이터도 기존 작성기로 재생성하고 전체 발행 검사를 실행한다.

Python: `configure_telegram_webhook.py`, `notify_telegram_refresh.py`.
웹: `src/app/api/telegram/webhook/route.ts`, `src/lib/telegram-bot.ts`, `src/lib/sota-portfolio.ts`.
이미지 양식: `src/lib/telegram-image.tsx`. `node scripts/render_telegram_ledger.cjs <SOTA JSON 경로>`로
실제 전송 없이 `artifacts/telegram/ledger/`에 모든 명령의 미리보기를 렌더링할 수 있다.
공유 데이터·일일 발송의 의미는 [PORTFOLIO.md](PORTFOLIO.md)를 따른다.

## 공유 원장과 URI

`src/lib/portfolio-data.ts`는 `/api/v2/portfolio` 공표물을 zod로 검증한다. 웹과 봇은 같은
`portfolio-ledger.ts`의 열 정의와 행을 사용한다. 별도 카드용 계산과 종목별 텍스트 포맷터는 없다.

- `/portfolio#positions`: 보유 진입일, 수량, 현재 비중, 평가손익.
- `/portfolio#orders`: 매수 대기와 스탑 근접, 청산 대기. 후보를 체결로 표시하지 않는다.
- `/portfolio/trades?from=2026-10-01&to=2026-10-26&side=buy&q=NBIS`: 기간, 매매 구분, 종목 필터.
  주소를 공유하거나 다시 열어도 같은 조회 조건을 유지한다. 모든 실제 매수와 매도 체결을 조회할 수 있다.
- 이미지 캡션은 해당 원장 URI를 제공한다. 이미지는 표의 요약이고 전체 기간 조회는 웹에서 한다.

매도 행의 손익은 매수와 매도의 비용 및 환율을 반영한다. 매수 행의 실현손익은 공란이다.
일일 알림은 평가액과 수익률, 원장 링크만 보낸다.
