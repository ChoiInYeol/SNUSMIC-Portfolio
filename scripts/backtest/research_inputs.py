"""Read the exact report universe and cached price inputs without fetching or tuning anything."""
import datetime as dt
import hashlib
import json
from pathlib import Path

import pandas as pd

import lake
from . import fx
from .accounting import build_ticker_reports
from .config import SIM_START, UNIVERSE_START
from .warehouse import load_prices


def load(as_of, reference=None):
    import backtest_momentum as bt
    source = reference or lake.WEB_DATA / "strategy-backtest.json"
    model = json.loads(Path(source).read_text(encoding="utf-8"))
    perf = pd.read_csv(lake.GOLD_REPORT_PERFORMANCE, encoding="utf-8-sig")
    perf = perf[perf.ticker.notna() & perf.report_date.notna() & (perf.rating_class == "buy") &
                (perf.report_date >= UNIVERSE_START.isoformat()) & (perf.report_date <= as_of)].copy()
    perf["ticker_key"] = perf.apply(lambda r: str(r.ticker).zfill(6) if r.market == "KR" else str(r.ticker), axis=1)
    metadata = build_ticker_reports(perf)
    counts = bt.point_in_time_school_counts(perf)
    prices = {}
    for _, row in perf.iterrows():
        if row.ticker_key not in prices:
            frame = load_prices(str(row.ticker), str(row.market))
            if frame is not None:
                prices[row.ticker_key] = frame.loc[:pd.Timestamp(as_of)].copy()
    prices = {t: f for t, f in prices.items() if not f.empty}
    reports = sorted((dt.date.fromisoformat(str(r.report_date)), r.ticker_key, Path(str(r.source_file)).name,
                      counts.get((r.ticker_key, dt.date.fromisoformat(str(r.report_date))), 1))
                     for _, r in perf.iterrows() if r.ticker_key in prices)
    calendar = sorted({i.date() for f in prices.values() for i in f.index if SIM_START <= i.date() <= dt.date.fromisoformat(as_of)})
    def index(name):
        path = lake.SILVER_PRICES / f"IDX_{name}.csv"
        frame = pd.read_csv(path, index_col=0, parse_dates=True).loc[:pd.Timestamp(as_of)]
        result = pd.to_numeric(frame["close"], errors="raise")
        if result.empty or result.isna().any() or (result <= 0).any():
            raise ValueError(f"invalid cached benchmark: {name}")
        return result
    rates = index("USDKRW")
    fx.set_usdkrw(rates)
    kospi, us, nasdaq, gold = (index(n) for n in ("KOSPI", "US", "NASDAQ", "GLD"))
    parking = bt.compute_all_weather(kospi, us, nasdaq, gold, calendar[0], calendar[-1], usdkrw=rates)
    selected = model.get("optuna_chandelier", {})
    params = selected["best_params"] if selected.get("adopted") else {"atr_period": 42, "atr_mult": 5.0, "max_positions": 20}
    h = hashlib.sha256(bt._dataset_fingerprint(prices, reports, calendar).encode())
    for series in (kospi, us, nasdaq, gold, rates):
        h.update(series.to_csv(lineterminator="\n").encode())
    h.update(json.dumps(params, sort_keys=True).encode())
    return {"as_of": as_of, "prices": prices, "reports": reports, "calendar": calendar,
            "metadata": metadata, "parking": parking, "kospi": kospi, "fx": rates, "policy": params,
            "fingerprint": h.hexdigest(), "reference": model,
            "benchmarks": {"kospi": kospi, "sp500": us, "nasdaq": nasdaq, "gld": gold, "usdkrw": rates}}
