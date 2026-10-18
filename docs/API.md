# Signal API — Reference

> **DISCLAIMER / 투자 경고**
>
> All data served by this API is derived from **backtested simulations** of
> momentum strategies applied to student investment-club reports.
> It is provided for **research and educational purposes only**.
> It does **NOT** constitute investment advice or a solicitation to buy or sell
> any security.  
> Past backtest performance does not guarantee future results.  
> **All trading decisions and their financial consequences are solely your
> responsibility (실매매로 인한 모든 손익의 책임은 전적으로 사용자에게 있습니다).**

---

## Overview

The API is a set of **static JSON files** regenerated once daily by the CI
pipeline (`scripts/backtest_momentum.py` → `scripts/export_signals_api.py`) and
deployed as public assets on Vercel.  There is no server-side compute at request
time — Vercel serves the files directly from CDN.

Base URL: `https://verdict-archive.vercel.app/api/v1`

An OpenAPI 3.1 machine-readable spec is available at
[`/api/v1/openapi.json`](https://verdict-archive.vercel.app/api/v1/openapi.json).

---

## Endpoints

### `GET /api/v1/signals/latest.json`

The **headline strategy's current signals** — refreshed each trading day.

| Field | Type | Description |
|---|---|---|
| `schema_version` | string | API schema version (`"1.0"`) |
| `as_of` | date | Date the signals were computed |
| `generated_at` | datetime | ISO-8601 UTC timestamp of generation |
| `headline_strategy` | string | Strategy key (e.g. `W_allweather_chandelier`) |
| `disclaimer` | string | English disclaimer text |
| `disclaimer_ko` | string | Korean disclaimer text |
| `regime` | object | Market regime state (see below) |
| `slots` | object | `{max_positions, open, available}` |
| `open_positions` | array | Currently held positions |
| `buy_signals` | array | Imminent buy candidates (last 5 trading days) |
| `sell_signals` | array | Positions approaching or hitting stop |
| `counts` | object | Summary counts |

**`regime` object**

| Field | Type | Description |
|---|---|---|
| `kospi_above_200ma` | bool | KOSPI close > 200-day MA |
| `state` | `"ON"` \| `"OFF"` | Regime gate state |
| `kospi_close` | number | Last KOSPI close |
| `kospi_ma200` | number | KOSPI 200-day MA value |
| `parking` | `"allweather"` \| `"kospi"` \| `"cash"` | Idle-cash parking vehicle |
| `applies` | bool | Whether the regime filter is active for this strategy |

**`open_positions[]` item**

| Field | Type | Description |
|---|---|---|
| `ticker` | string | KR 6-digit code or US ticker |
| `market` | `"KR"` \| `"US"` | Market |
| `name` | string | Company display name |
| `entry_date` | date | Position open date |
| `entry_price` | number | Entry price |
| `current_price` | number | Last close price |
| `stop_level` | number | Chandelier trailing stop level |
| `unrealized_pct` | number | Unrealised return % |
| `days_held` | integer | Calendar days since entry |
| `entry_reason` | string | Human-readable entry rationale |
| `trigger_reports` | array | Club reports that triggered entry |

**`buy_signals[]` item**

| Field | Type | Description |
|---|---|---|
| `ticker` | string | |
| `market` | string | |
| `name` | string | |
| `signal_date` | date | Date signal was generated |
| `entry_basis_price` | number\|null | Suggested entry reference price |
| `entry_reason` | string | |
| `trigger_reports` | array | |

**`sell_signals[]` item**

| Field | Type | Description |
|---|---|---|
| `ticker` | string | |
| `market` | string | |
| `name` | string | |
| `reason` | `"approaching_stop"` \| `"stop_hit"` | Why it's flagged |
| `stop_level` | number | Stop price |
| `dist_to_stop_pct` | number | Distance to stop as % of current price |

---

### `GET /api/v1/signals/{YYYY-MM-DD}.json`

Immutable daily snapshot — same shape as `latest.json`.  
The CI pipeline appends a new file each day but never overwrites historical ones,
so consumers can diff day-over-day.

Example: `/api/v1/signals/2026-06-11.json`

---

### `GET /api/v1/forward.json`

Snapshot scoring is not automatically independent out-of-sample evidence. First-publication provenance is explicitly unknown unless it is independently verified; `as_of` and Git commit timestamps alone do not establish the time data became publicly available.

| Field | Description |
|---|---|
| `method` | Scoring rules (entry = next-day open after signal, local-currency close returns, 7-day per-ticker dedup) |
| `summary` | `n_signals`, `n_tracking`, `avg_return_pct`, `win_rate_pct`, `best_pct`, `worst_pct`, `first_snapshot`, `n_snapshots` |
| `entries[]` | Per-signal: `signal_date`, `ticker`, `market`, `name`, `entry_date`, `entry_price`, `current_price`, `return_pct`, `peak_pct`, `days`, `status`, `trigger_schools` |
| `coverage` / provenance fields | Missing prices remain in the signal denominator with `unavailable`/`pending` status and null returns; unverified first-publication evidence is disclosed, not fabricated. |

Rendered at [/track](https://verdict-archive.vercel.app/track). Record starts 2026-06-11.

---

### `GET /api/v1/strategies.json`

All 26 strategies with full IS/OOS metrics.

| Field | Description |
|---|---|
| `strategies[].key` | Internal strategy key |
| `strategies[].is_headline` | `true` for the current headline strategy |
| `strategies[].metrics` | Full-period stats: `total_return_pct`, `cagr_pct`, `sharpe`, `mdd_pct`, `win_rate_pct`, `avg_hold_days` |
| `strategies[].in_sample` | IS period stats |
| `strategies[].out_of_sample` | OOS period stats |
| `strategies[].kospi_dca_ratio` | Final value vs KOSPI buy-and-hold (>1 = beats market) |
| `strategies[].aw_dca_ratio` | Final value vs All-Weather DCA |
| `strategies[].dsr` | v24 — Deflated Sharpe Ratio block: `psr`, `dsr`, `sr0_annualized`, `n_trials`, `significant_after_deflation` (Bailey & López de Prado 2014; DSR ≥ 0.95 = significant after multiple-testing correction) |
| `strategies[].walkforward` / `walkforward_oos` | v24 — 6-month rolling-window consistency: `n_windows`, `positive_pct`, `beat_kospi_pct`, `median_sharpe`, `worst_window_return_pct` (no refit; `_oos` = windows from 2024-01) |

---

### `GET /api/v1/trades/{strategy_key}.json`

Full closed-trade log for one strategy.  Open (未청산) positions are excluded.

`strategy_key` values: `A_12mo`, `B_36mo`, `C_narrative`, `D_chandelier`,
`E_half_runner`, `F_momentum_narrative`, `G_dip_buy`, `H_minervini`,
`I_supertrend`, `J_core_satellite`, `K_rr_trend`, `N_52w_high`,
`O_mtt_alpha16`, `P_deepbuy_chandelier`, `Q_kangto_trend`,
`R_kelly_chandelier`, `S_hrp`, `S_msharpe`, `S_mincvar`, `V_spo`, `V_ls`,
`T_kospi_core_chandelier`, `T-_kospi_core_regime`, `W_allweather_chandelier`,
`U_chandelier_scaleout`

**`trades[]` item**

| Field | Description |
|---|---|
| `ticker` | KR 6-digit or US ticker |
| `market` | `"KR"` or `"US"` |
| `name` | Company name |
| `entry_date` / `exit_date` | ISO dates |
| `entry_price` / `exit_price` | Prices |
| `return_pct` | Trade return % (net of transaction cost) |
| `days` | Holding period in calendar days |
| `exit_reason` | e.g. `"chandelier_stop"`, `"time_exit"` |
| `entry_reason` | Human-readable entry rationale |
| `trigger_reports` | Club reports that triggered entry |

---

### `GET /strategy-marks/{slug}.json`

Per-ticker **backtest trade marks of the headline strategy** — used by the
stock pages to overlay buy/sell points on the candle chart.  
`slug` matches the price-chart files: `"{market}-{ticker}".lower()`
(e.g. `kr-278470`, `us-fix`). One file exists for every ticker the headline
strategy ever traded (plus current open positions).

| Field | Description |
|---|---|
| `strategy_key` | Headline strategy key |
| `ticker` / `market` / `name` | Instrument identity |
| `marks[]` | `{date, side: "buy"\|"sell"\|"stop", price, reason}` — entry/exit points |
| `open_stop` | `{stop_level, entry_date, as_of}` for currently held tickers, else `null` |

Example: `/strategy-marks/kr-278470.json`

---

### `GET /api/v1/openapi.json`

OpenAPI 3.1 spec — use with any OpenAPI client generator to get typed
access in your language.

---

## Worked Examples

### curl

```bash
# Latest signals
curl https://verdict-archive.vercel.app/api/v1/signals/latest.json | python -m json.tool

# Headline strategy trades
curl https://verdict-archive.vercel.app/api/v1/trades/W_allweather_chandelier.json | python -m json.tool
```

### Python — print today's buy and sell signals

```python
import urllib.request
import json

url = "https://verdict-archive.vercel.app/api/v1/signals/latest.json"
with urllib.request.urlopen(url) as resp:
    data = json.load(resp)

print(f"Signals as of {data['as_of']}  (strategy: {data['headline_strategy']})")
print(f"Regime: {data['regime']['state']}  parking: {data['regime']['parking']}")
print()

buys = data["buy_signals"]
if buys:
    print(f"=== BUY signals ({len(buys)}) ===")
    for b in buys:
        print(f"  {b['ticker']} ({b['market']}) — {b['name']}")
        print(f"    signal_date: {b['signal_date']}  basis: {b['entry_basis_price']}")
        print(f"    reason: {b['entry_reason']}")
else:
    print("No buy signals today.")

print()
sells = data["sell_signals"]
if sells:
    print(f"=== SELL alerts ({len(sells)}) ===")
    for s in sells:
        print(f"  {s['ticker']} — {s['name']}  [{s['reason']}]")
        print(f"    stop: {s['stop_level']}  dist: {s['dist_to_stop_pct']:.1f}%")
else:
    print("No sell alerts today.")
```

### Python — diff yesterday vs today

```python
import urllib.request, json, datetime

base = "https://verdict-archive.vercel.app/api/v1/signals"
today = datetime.date.today().isoformat()
yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

def fetch(date):
    try:
        with urllib.request.urlopen(f"{base}/{date}.json") as r:
            return json.load(r)
    except Exception:
        return None

t = fetch(today)
y = fetch(yesterday)
if t and y:
    t_tickers = {p["ticker"] for p in t["open_positions"]}
    y_tickers = {p["ticker"] for p in y["open_positions"]}
    print("Entered:", t_tickers - y_tickers)
    print("Exited: ", y_tickers - t_tickers)
```

---

## Cache-Control & CORS

The `.json` assets are served directly; the no-extension alias remains a Next handler for GET/OPTIONS. All API responses allow `Access-Control-Allow-Origin: *`.

- `signals/latest.json`, `/signals/latest` and `strategy-details/manifest.json`: `public, max-age=300, stale-while-revalidate=60`.
- Dated `signals/YYYY-MM-DD.json`: `public, max-age=3600, s-maxage=31536000`. Snapshots are append-only, but a header rule also
  matches the 404 of a date that is not published yet, so browsers revalidate hourly; the CDN copy lives until the next deploy.
- `strategy-details/<key>/<sha256>.json`: `public, max-age=31536000, immutable` (content-addressed — new bytes, new URL).
- Other API files use the platform default cache policy; there is no blanket 12-hour API cache.
- Invalid input to the generated alias fails the build instead of publishing a cached static 503.

## Contract & versioning

- `openapi.json` documents every published file and field. `tests/test_api_contract.py` validates every published payload
  against it (undocumented fields fail), and `tests/api-schemas.test.cjs` validates them against the web app's zod schemas
  (`src/lib/api-schemas.ts`).
- Additive fields keep `schema_version` `1.x`; clients must ignore unknown fields. Removing/renaming a field or changing its
  type is breaking and ships under `/api/v2/`.
- Errors: missing files are plain static 404s (no JSON body).

`strategy-details/manifest.json` maps strategy keys to `path` and `content_hash`. The page binds these references to the source signal date and loads `/api/v1/strategy-details/<key>/<hash>.json` on demand. Missing/mismatched assets are explicit errors, with no silent fallback to latest. `export_signals_api.py --details-only` prepares these assets without refreshing previously published signals. Old hash assets are currently retained; storage growth needs measurement before a Hobby suitability claim.

---

## Pipeline integration — regenerating the API

The export step runs automatically after `backtest_momentum.py`:

```bash
# Full pipeline
uv run python scripts/backtest_momentum.py
uv run python scripts/export_signals_api.py

# Or just re-export from an existing strategy-backtest.json
uv run python scripts/export_signals_api.py --source src/data/strategy-backtest.json

# Verify committed artifacts equal a fresh export (writes nothing; exit 1 on drift)
uv run python scripts/export_signals_api.py --check
```

`tests.yml` builds from the committed artifacts and runs `--check` instead of regenerating them, so a missing or stale
`public/api` file fails CI rather than the Vercel build. The refresh workflows run the same gate (contracts, pytest,
`npm run build`) before the bot commit, because `GITHUB_TOKEN` pushes do not trigger `tests.yml`.

---

## 텔레그램 일일 신호 봇

`scripts/send_telegram_signals.py`가 매 거래일 CI(`refresh-daily.yml`)에서
오늘의 신호를 한국어 다이제스트로 텔레그램에 보냅니다. 전일 스냅샷과
비교해 **신규 진입 / 청산 / 매수 신호 / 매도 임박**의 변화가 있을 때만
전송합니다 (변화 없으면 침묵 — 스팸 없음).

### 1. 봇 만들기 (BotFather)

1. 텔레그램에서 [@BotFather](https://t.me/BotFather) 검색 → 대화 시작
2. `/newbot` 입력 → 봇 이름과 username(예: `verdict_signal_bot`) 지정
3. BotFather가 주는 **HTTP API 토큰**(`123456:ABC-DEF...` 형태)을 복사
   — 이것이 `TELEGRAM_BOT_TOKEN`

### 2. chat_id 얻기

1. 방금 만든 봇과 대화를 시작하고 아무 메시지나 보냅니다
   (그룹에 봇을 초대해 그룹으로 받아도 됩니다)
2. 브라우저에서 다음 URL 열기:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. 응답 JSON의 `result[].message.chat.id` 값이 `TELEGRAM_CHAT_ID`
   (그룹이면 음수일 수 있습니다 — 그대로 사용)

### 3. GitHub 저장소 시크릿 설정

저장소 → Settings → Secrets and variables → Actions → New repository secret:

| Secret 이름 | 값 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather가 준 API 토큰 |
| `TELEGRAM_CHAT_ID` | 위에서 얻은 chat id |

시크릿을 설정하기 전에도 CI는 실패하지 않습니다 — 환경 변수가 없으면
스크립트가 조용히 종료(exit 0)합니다.

### 4. 로컬 테스트

```bash
# 다이제스트 내용만 출력 (전송 없음, 토큰 불필요)
uv run python scripts/send_telegram_signals.py --dry-run

# 실제 전송 (변화 없어도 강제 전송)
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
  uv run python scripts/send_telegram_signals.py --force
```
