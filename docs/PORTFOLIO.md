# 전략 모의 포트폴리오와 텔레그램

> 초기 5천만원·체증식 월 납입·정수 주식·익일 시가 주문 계획 계정(`funded-w50-v1`)은 별도 계정이다.
> [FUNDED-ACCOUNT.md](FUNDED-ACCOUNT.md) 참조. 아래 내용은 1억원 `execution-v2` 계정에 대한 것이다.

## 계산 의미

`/portfolio`와 `public/research/portfolio.json`은 **기록 시작 이후의 전진 모의 평가**다.
과거 백테스트 누적 수익률을 live 수익률로 사용하지 않는다. 초기 자금은 1억원이며 첫 관측은 현금 100%다.
보유 비중과 별도로 다음 체결 목표 비중을 표시한다. 실제 증권계좌나 실시간 호가를 연결하지 않는다.

현재 계약은 `schema_version=2.0`, 엔진은 `parking-open-v2`다. W 백테스트와 전진 계정이
`scripts/backtest/parking_engine.py`를 함께 호출한다. 최초 관측에서 W의 ATR 기간·배수·슬롯 수를
동결하며 이후 헤드라인 변경이나 재최적화가 기존 계정의 정책을 바꾸지 않는다.
현재 정책은 ATR 42 × 5.75, 최대 30종목, 신규 진입 기본 NAV 5%다.

원천 입력은 `lake.BRONZE_PAPER_EXECUTION`에 관측한 KST 날짜별로 추가한다.
실제 관측 시각, 최초 공개 보유 후보, 새 리포트 이벤트, 가격·ATR·환율·올웨더 지수를 보존한다.
초기 후보는 과거 백테스트 수량을 승계하지 않는다. 리포트 날짜 이후이면서 관측 시각보다 늦은
해당 시장의 다음 가용 시가에 진입하고, 종가 스탑 신호는 다음 가용 시가에 청산한다.
최초 기록은 2026-10-20 20:14 KST이며 과거 10월 19일 수익을 얻지 않는다.

KR 시가를 US 시가보다 먼저 처리한다. 전일 ATR·환율·평가잔고를 사용하며 오늘 파킹 수익을
시가 매수 예산에 미리 넣지 않는다. 주식 편도 0.3%, 파킹 전환 0.05% 비용을 반영한다.
올웨더는 거래 가능한 시가가 없는 합성지수 프록시다. 당일 매수에 쓰지 않은 전일 잔액에만
당일 지수 수익을 적용하며 당일 매도 유입금은 다음 날부터 반영하는 보수적 대체 모형이다.

KR·US·FX가 모두 완료된 일봉 날짜까지만 평가하므로 국내 평가일보다 하루 이상 늦을 수 있다.
각 원천의 수집 시각 이후 시세, 미래 완료일, 정책 변경은 거부한다. 이미 봉인한 날짜의 늦게
도착한 행은 과거 체결에 소급 삽입하지 않고 `data_issues`에 남긴다. 최초 관측된 행의 수정도
기존 체결을 다시 쓰지 않는다. 초기 현금에는 이자를 붙이지 않는다.

Silver 원장은 동결 입력을 공통 엔진으로 재생한다. Gold는 NAV·비중·손익·실제 모의 체결 목록,
다음 시가 후보를 제공한다. 후보별 5%는 기본 진입 규모이며 동시 보유 목표 비중 합계가 아니다.
이전 `lake.BRONZE_PAPER_TARGETS`의 v1 종가 추종 관측은 보존하지만 v2 시가 체결로 재해석하지 않는다.
경계 계약은 `src/lib/paper-portfolio.ts`이며 재생 검사는 `paper_portfolio.py --check`다.

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
