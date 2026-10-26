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
| `/trades`: 최근 수익/손실 청산 | 허용 | 허용 |
| `/help`: 역할에 맞는 명령 목록 | 허용 | 허용 |
| `/refresh`: 리포트·시세 수집, 백테스트, 발행 | 차단 | 허용 |
| `/chatid`: 새 공유 방 ID 확인 | 차단 | 허용 |

명령 응답은 명령을 보낸 방에만 보낸다. 일일 요약과 갱신 완료 알림은 `TELEGRAM_CHAT_ID`로 보낸다.
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
2. 관리자가 그룹에서 `/chatid@봇이름`을 보낸다. 이 명령만 미등록 방에서도 관리자에게 허용한다.
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
- SOTA 데이터는 Python 작성기와 zod 독자가 평가액·수량·비중·손익을 검증한다. 잘못된 생성물은 빌드를 실패시킨다.

Python: `configure_telegram_webhook.py`, `notify_telegram_refresh.py`.
웹: `src/app/api/telegram/webhook/route.ts`, `src/lib/telegram-bot.ts`, `src/lib/sota-portfolio.ts`.
공유 데이터·일일 발송의 의미는 [PORTFOLIO.md](PORTFOLIO.md)를 따른다.
