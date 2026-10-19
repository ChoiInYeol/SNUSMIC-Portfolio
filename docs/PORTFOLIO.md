# 전략 모의 포트폴리오와 텔레그램

## 계산 의미

`/portfolio`와 `public/research/portfolio.json`은 **기록 시작 이후의 전진 모의 평가**다.
과거 백테스트 누적 수익률을 live 수익률로 사용하지 않는다. 초기 자금은 1억원이며 첫 관측은 현금 100%다.
보유 비중과 별도로 다음 체결 목표 비중을 표시한다. 실제 증권계좌나 실시간 호가를 연결하지 않는다.

백테스트 작성기가 현재 헤드라인의 실제 수량·환율·NAV에서 목표 비중을 계산해
`lake.BRONZE_PAPER_TARGETS`에 최초 관측 시각과 함께 날짜별 동결한다. 재계산으로 기존 관측을 덮지 않는다.
과거 날짜의 백테스트 재실행으로 전진 관측을 소급 추가하지 않는다.

다음 관측에서 해당 시장의 종가 시각이 이전 관측 시각보다 늦은 시세가 모두 있을 때,
이전 목표 비중으로 종가 리밸런싱한다. 당일 수익을 먼저 얻고 체결하는 처리는 없다.
모든 자산은 원화로 평가하며 주식 편도 0.3%, 합성지수 편도 0.05% 비용을 반영한다.
올웨더·KOSPI는 비매매 합성지수 프록시다. 이 모의계정의 다음 종가 체결·일별 비중 추종은
백테스트 엔진의 다음 시가 진입·스탑 매도와 다른 명시적 추종 규칙이다. 미체결 현금에는 이자를 붙이지 않는다.
일봉 기준 날짜가 섞일 수 있어 종목별 시세일을 함께 표시한다.

Silver 원장은 관측을 시간순으로 재생하며 현금·보유수량·비용·평가액을 보존한다.
Gold 서빙은 비중·평가손익·전 평가 대비 수익률·기록 이후 수익률을 제공한다.
`schema_version=1.0`, 경계 계약은 `src/lib/paper-portfolio.ts`다.

## 알림 설정

GitHub 저장소 **Settings → Secrets and variables → Actions**에 다음 Secrets를 등록한다.

- `TELEGRAM_BOT_TOKEN`: BotFather에서 받은 봇 토큰.
- `TELEGRAM_CHAT_ID`: 수신 채팅 ID. 사용자가 먼저 봇과 대화를 시작해야 한다.

비밀값은 소스·브라우저 환경 변수·채팅에 넣지 않는다. 구현 당시 로컬·GitHub 모두 미설정이었다.
등록 이후 `portfolio-daily`를 수동 실행해 수신을 확인한다. 매일 20:00 KST 예약은 main에 반영된
워크플로에서 작동하며 GitHub 스케줄은 정시 도착을 보장하지 않는다.

휴장일에는 최근 평가 기준일을 명시해 매일 알린다. 실시간 장중 수익률을 주장하지 않는다.
기존 신호 변화 알림은 `TELEGRAM_SIGNALS_ENABLED=true`인 경우만 별도로 실행한다.
일일 포트폴리오는 변동이 없어도 전송한다.

```powershell
uv run python scripts/send_telegram_portfolio.py --dry-run
# 환경 변수 설정 후 실제 전송. --force는 같은 날 재전송할 때만 사용.
uv run python scripts/send_telegram_portfolio.py
```

전송 성공 후 수신자/KST 날짜의 해시를 영수증에 저장한다. 워크플로는 이 파일을 Actions cache에 보관한다.
비밀값·원래 채팅 ID는 영수증에 쓰지 않는다. API 오류·미설정은 실패 처리하며 토큰 포함 URL은 로그에 쓰지 않는다.
캐시 삭제, 일부 메시지 전송 뒤 실패, 서버 수신 후 연결 단절의 경우 완전한 exactly-once 보장은 없다.
API 사용: [Telegram sendMessage](https://core.telegram.org/bots/api#sendmessage).
