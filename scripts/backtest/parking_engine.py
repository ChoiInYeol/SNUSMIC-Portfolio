"""The shared T/T-/W daily execution engine (backtests and forward paper accounts).

Opening decisions use prior marks/FX/ATR. KR openings precede US openings. Only overnight
parking capital left after opening purchases earns the day's composite-index return; new sale
proceeds start earning the next day. The composite has no executable opening quote: this is an
explicit conservative index proxy, not an ETF intraday fill model.
"""
from __future__ import annotations

import datetime as dt
import math
from zoneinfo import ZoneInfo

import pandas as pd

from .accounting import build_pending_entries, _close_trade, _report_trigger_reason
from .config import CASH_YIELD_DAILY, COST_PER_SIDE, KOSPI_PARK_COST, POSITION_WEIGHT
from .fx import _fx
from .metrics import _compute_result
from .warehouse import _has_day, _px, asof_value

ENGINE_VERSION = "parking-open-v2"


def market_open(day: dt.date, market: str) -> dt.datetime:
    local = dt.datetime.combine(day, dt.time(9, 0) if market == "KR" else dt.time(9, 30),
                                ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York"))
    return local.astimezone(dt.timezone.utc)


def simulate(prices, reports, calendar, label, parking, atr_period, atr_mult, max_positions,
             ticker_reports=None, record_full_trades=False, regime_aware=False, regime_index=None,
             parking_name="KOSPI", *, entry_schedule=None, initial_capital=100_000_000.0,
             collect_history=False):
    if not calendar:
        raise ValueError("execution calendar is empty")
    if atr_period not in (20, 42, 63) or atr_mult <= 0 or max_positions < 1 or initial_capital <= 0:
        raise ValueError("invalid execution policy")
    atr_column = {20: "atr20", 42: "atr", 63: "atr63"}[atr_period]
    pending = entry_schedule if entry_schedule is not None else build_pending_entries(reports, calendar, False, prices)
    positions = {}; exits = set(); trades = []; fills = []; navs = []; snapshots = []
    parked = initial_capital * (1 - KOSPI_PARK_COST)
    fees = initial_capital - parked
    previous_park = None
    regime_source = regime_index if regime_index is not None else parking
    ma = regime_source.rolling(200, min_periods=100).mean() if regime_aware else None

    def metadata(ticker, day):
        items = (ticker_reports or {}).get(ticker, [])
        market = items[0].get("market", "KR") if items else ("KR" if ticker.isdigit() else "US")
        past = [r for r in items if r["report_date"] < day and
                (not r.get("available_at") or dt.datetime.fromisoformat(r["available_at"]) < market_open(day, market))]
        latest = max(past, key=lambda r: r["report_date"]) if past else {}
        targets = [r["target_price"] for r in past if r.get("target_price")]
        return market, latest.get("display_name", ticker), max(targets) if targets else None, past

    for day in calendar:
        timestamp = pd.Timestamp(day)
        prior = day - dt.timedelta(days=1)
        # Keep sale proceeds separate: money received this morning cannot earn last night's return.
        overnight = parked
        newly_parked = 0.0
        candidates = list({t: (t, s, n) for t, s, n in pending.get(day, [])}.values())
        for market in ("KR", "US"):
            for ticker in sorted(exits.copy()):
                if ticker not in positions or positions[ticker]["market"] != market:
                    continue
                df = prices.get(ticker)
                price = _px(df, timestamp, "open") if df is not None else None
                if price is None or not math.isfinite(price) or price <= 0:
                    continue  # A missing open is pending, never substituted by the close.
                pos = positions[ticker]
                rate = _fx(market, prior)
                gross = pos["shares"] * price * rate
                stock_net = gross * (1 - COST_PER_SIDE)
                net = stock_net * (1 - KOSPI_PARK_COST)
                fees += gross - net
                newly_parked += net
                trade = _close_trade(ticker, pos, day, price, f"t_chandelier_ATR{atr_mult}",
                                     ticker_reports, record_full_trades, None, fx_rate=rate)
                # _close_trade only knows stock commission; this strategy also repurchases parking.
                trade["pnl_krw"] = round(net - pos["cost"], 2)
                trade["return_pct"] = round((net / pos["cost"] - 1) * 100, 2)
                trades.append(trade)
                fills.append({"date": day.isoformat(), "ticker": ticker, "market": market, "side": "sell",
                              "price": price, "fx": rate, "shares": pos["shares"], "gross_krw": gross,
                              "cash_flow_krw": net, "fees_krw": gross - net, "reason": "prior_close_stop"})
                del positions[ticker]; exits.remove(ticker)

            # No same-day parking close or future market's sale proceeds enter this budget.
            opening_nav = overnight + newly_parked + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            for ticker, source, clubs in candidates:
                if ticker in positions or len(positions) >= max_positions:
                    continue
                ticker_market, name, target, visible = metadata(ticker, day)
                if ticker_market != market:
                    continue
                df = prices.get(ticker)
                price = _px(df, timestamp, "open") if df is not None else None
                if price is None or not math.isfinite(price) or price <= 0:
                    continue
                budget = min(opening_nav * POSITION_WEIGHT, overnight + newly_parked)
                if budget < opening_nav * POSITION_WEIGHT * 0.5:
                    continue
                # Purchases consume the overnight balance first (conservative parking attribution).
                used = min(overnight, budget); overnight -= used; newly_parked -= budget - used
                stock_budget = budget * (1 - KOSPI_PARK_COST)
                rate = _fx(market, prior)
                shares = stock_budget * (1 - COST_PER_SIDE) / (price * rate)
                fee = budget - shares * price * rate
                fees += fee
                if atr_column not in df.columns:
                    raise ValueError(f"missing ATR({atr_period}) for {ticker}")
                atr = asof_value(df[atr_column], prior)
                stop = price - atr_mult * atr if atr > 0 else price * 0.75
                positions[ticker] = {"shares": shares, "entry_price": price, "entry_date": day,
                                     "cost": budget, "last_close": price, "highest": price, "stop": stop,
                                     "source": source, "n_clubs": clubs, "display_name": name, "market": market,
                                     "fx": rate, "target_price": target,
                                     "entry_reason": _report_trigger_reason(ticker, day, {ticker: visible},
                                         prefix=f"학회 매수리포트 → 익일 시가 ({parking_name} 파킹 전환)")}
                fills.append({"date": day.isoformat(), "ticker": ticker, "market": market, "side": "buy",
                              "price": price, "fx": rate, "shares": shares, "gross_krw": shares * price * rate,
                              "cash_flow_krw": -budget, "fees_krw": fee, "reason": source})

        park = asof_value(parking, day)
        if park > 0 and previous_park is not None and previous_park > 0:
            use_return = not regime_aware or asof_value(ma, prior) <= 0 or asof_value(regime_source, prior) >= asof_value(ma, prior)
            overnight *= park / previous_park if use_return else 1 + CASH_YIELD_DAILY
        if park > 0:
            previous_park = park
        parked = overnight + newly_parked
        for ticker, pos in positions.items():
            df = prices[ticker]
            if not _has_day(df, timestamp):
                continue
            close = _px(df, timestamp, "close")
            if close is None or not math.isfinite(close) or close <= 0:
                raise ValueError("invalid closing mark")
            pos["last_close"] = close; pos["fx"] = _fx(pos["market"], day)
            pos["highest"] = max(pos["highest"], close)
            atr = asof_value(df[atr_column], day)
            if atr > 0:
                pos["stop"] = max(pos["stop"], pos["highest"] - atr_mult * atr)
            if close < pos["stop"]:
                exits.add(ticker)
        nav = parked + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        navs.append((day.isoformat(), nav))
        if collect_history:
            snapshots.append({"date": day.isoformat(), "nav_krw": nav, "parked_krw": parked, "fees_krw": fees,
                              "positions": {k: dict(v) for k, v in positions.items()}, "pending_exits": sorted(exits)})
    for ticker, pos in positions.items():
        trades.append(_close_trade(ticker, pos, calendar[-1], pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))
    result = _compute_result(navs, trades, initial_capital, label, open_positions=positions)
    result["execution"] = {"engine_version": ENGINE_VERSION, "parked_krw": parked, "cash_krw": 0.0,
                           "fees_krw": fees, "fills": fills, "snapshots": snapshots, "pending_exits": sorted(exits)}
    result["kospi_parking_note"] = (f"{parking_name} 합성지수 파킹. 전일 잔고·ATR·환율로 시가 체결, KR→US 순서. "
        "당일 매수로 인출하지 않은 전일 파킹 잔액만 당일 지수 수익을 받으며 당일 매도 유입금은 다음 날부터 반영. "
        "주식 편도 0.3%·파킹 전환 0.05%. 지수 시가가 없는 보수적 프록시이며 실제 ETF 체결과 다름.")
    return result
