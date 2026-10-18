# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import math
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from .config import PRICE_DIR, ATR_PERIOD



# ──────────────────────────────────────────────────────────────────────────────
# Data loading helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_index_yf(ticker: str, cache_name: str) -> pd.Series:
    """yfinance로 지수 종가 다운로드, data/prices에 캐시."""
    cache_path = PRICE_DIR / f"IDX_{cache_name}.csv"
    import yfinance as yf  # type: ignore

    today = dt.date.today()
    if cache_path.exists():
        df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
        if not df.empty:
            last_date = df.index[-1].date()
            if last_date >= today - dt.timedelta(days=5):
                return df["close"].sort_index()
            start_upd = last_date + dt.timedelta(days=1)
            raw = yf.download(ticker, start=start_upd.isoformat(), progress=False, auto_adjust=True, threads=False)
            if not raw.empty:
                raw.index = pd.to_datetime(raw.index)
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = [c[0].lower() for c in raw.columns]
                else:
                    raw.columns = [c.lower() for c in raw.columns]
                if "close" in raw.columns:
                    new_rows = raw[["close"]].copy()
                    new_rows.index.name = "Date"
                    combined = pd.concat([df, new_rows])
                    combined = combined[~combined.index.duplicated(keep="last")]
                    combined.to_csv(cache_path)
                    return combined["close"].sort_index()
            return df["close"].sort_index()

    raw = yf.download(ticker, start="2007-01-01", progress=False, auto_adjust=True, threads=False)
    if raw.empty:
        raise RuntimeError(f"yfinance returned empty data for {ticker}")
    raw.index = pd.to_datetime(raw.index)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0].lower() for c in raw.columns]
    else:
        raw.columns = [c.lower() for c in raw.columns]
    out = raw[["close"]].copy()
    out.index.name = "Date"
    out.to_csv(cache_path)
    return out["close"].sort_index()


def _fetch_us_stock_yf(ticker: str) -> bool:
    """US 주식 가격 yfinance로 다운로드 → data/prices/US_{ticker}.csv 저장."""
    import yfinance as yf  # type: ignore
    cache_path = PRICE_DIR / f"US_{ticker}.csv"
    print(f"  Fetching US/{ticker} via yfinance...", flush=True)
    try:
        raw = yf.download(ticker, start="2007-01-01", progress=False, auto_adjust=True, threads=False)
        if raw.empty:
            print(f"    WARNING: empty data for {ticker}", flush=True)
            return False
        raw.index = pd.to_datetime(raw.index)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [c[0].lower() for c in raw.columns]
        else:
            raw.columns = [c.lower() for c in raw.columns]
        needed = [c for c in ["open", "high", "low", "close", "volume"] if c in raw.columns]
        out = raw[needed].copy()
        out.index.name = "Date"
        out.to_csv(cache_path)
        print(f"    Saved {cache_path.name} ({len(out)} rows)", flush=True)
        return True
    except Exception as e:
        print(f"    ERROR fetching {ticker}: {e}", flush=True)
        return False


def load_kospi() -> pd.Series:
    # CI 콜드스타트(빈 캐시)에서도 동작하도록 파일이 없으면 yfinance로 받아 캐시한다
    path = PRICE_DIR / "IDX_KOSPI.csv"
    if not path.exists():
        return _fetch_index_yf("^KS11", "KOSPI")
    df = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    return df["close"]


def load_sp500() -> pd.Series:
    path = PRICE_DIR / "IDX_US.csv"
    if not path.exists():
        return _fetch_index_yf("^GSPC", "US")
    df = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    return df["close"]


def load_nasdaq() -> pd.Series:
    path = PRICE_DIR / "IDX_NASDAQ.csv"
    if path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
        if not df.empty and "close" in df.columns:
            return df["close"]
    return _fetch_index_yf("^IXIC", "NASDAQ")


def load_gld() -> pd.Series:
    path = PRICE_DIR / "IDX_GLD.csv"
    if path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
        if not df.empty and "close" in df.columns:
            return df["close"]
    return _fetch_index_yf("GLD", "GLD")


def load_usdkrw() -> pd.Series:
    """USDKRW 일별 환율 로드. KRW=X yfinance 심볼, data/prices/IDX_USDKRW.csv 캐시."""
    path = PRICE_DIR / "IDX_USDKRW.csv"
    if path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
        if not df.empty and "close" in df.columns:
            last_date = df.index[-1].date()
            today = dt.date.today()
            if last_date >= today - dt.timedelta(days=5):
                return df["close"]
    return _fetch_index_yf("KRW=X", "USDKRW")


# ── Price warehouse cache (perf only — results identical) ─────────────────────
# Parsed CSV + indicator columns are pickled to data/prices/.cache/, keyed by the
# source file's (mtime_ns, size) plus a code version tag. Pickle round-trips
# float64 bit-exactly, so cached frames are identical to freshly computed ones.
_PRICE_CACHE_DIR = PRICE_DIR / ".cache"
# Bump when load_prices indicator logic changes (invalidates all cached frames)
_PRICE_CACHE_VERSION = "v2"



def _load_prices_uncached(path: Path) -> pd.DataFrame | None:
    from .strategies import Q_BREAKOUT_DAYS
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    except Exception:
        return None
    if df.empty or "close" not in df:
        return None
    # KR CSVs may use Korean 날짜 header — already handled by index_col=0
    df = df[~df.index.duplicated(keep="last")]

    # Ensure required columns exist
    for col in ["open", "high", "low", "close"]:
        if col not in df.columns:
            return None

    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD, min_periods=ATR_PERIOD // 2).mean()
    # ATR(20) for K R:R strategy
    df["atr20"] = tr.rolling(20, min_periods=10).mean()
    # ATR(63) for strategies that explicitly select the long lookback.
    df["atr63"] = tr.rolling(63, min_periods=63).mean()
    # ATR(14) for extension gauge (U 과열 스케일아웃)
    df["atr14"] = tr.rolling(14, min_periods=7).mean()
    ret = df["close"].pct_change()
    df["sharpe90"] = ret.rolling(90, min_periods=45).mean() / ret.rolling(90, min_periods=45).std() * math.sqrt(252)
    # Moving averages for various strategies
    df["ma50"]  = df["close"].rolling(50,  min_periods=25).mean()
    df["ma150"] = df["close"].rolling(150, min_periods=75).mean()
    df["ma200"] = df["close"].rolling(200, min_periods=100).mean()
    # 52-week high/low
    df["hi52w"] = df["high"].rolling(252, min_periods=126).max()
    df["lo52w"] = df["low"].rolling(252, min_periods=126).min()
    # Q 깡토: 60d-high breakout / 20d avg volume — precomputed once here instead of
    # rebuilding the full rolling series per ticker per day inside run_kangto_trend.
    # Same rolling ops on the same data → identical values.
    df["hi60"] = df["close"].rolling(Q_BREAKOUT_DAYS, min_periods=30).max()
    if "volume" in df.columns:
        df["vol20avg"] = df["volume"].rolling(20, min_periods=10).mean()
    # Supertrend(10, 3): standard Supertrend indicator
    #   basic upper/lower bands = hl2 ± multiplier * ATR(period)
    _atr10 = tr.rolling(10, min_periods=5).mean()
    _hl2 = (df["high"] + df["low"]) / 2
    _upper = _hl2 + 3.0 * _atr10
    _lower = _hl2 - 3.0 * _atr10
    # Supertrend state: True=bullish, False=bearish
    # Recurrence identical to the original .iloc loop, run on numpy arrays
    # (same float64 values, same min/max/comparison semantics — just faster).
    up = _upper.to_numpy()
    lo = _lower.to_numpy()
    cl_arr = df["close"].to_numpy()
    fu = up.copy()
    fl = lo.copy()
    trend = np.ones(len(df), dtype=bool)
    for i in range(1, len(df)):
        prev_fu = fu[i - 1]
        prev_fl = fl[i - 1]
        cu = up[i]
        cl = lo[i]
        pc = cl_arr[i - 1]
        # Adjust bands
        fu[i] = min(cu, prev_fu) if pc <= prev_fu else cu
        fl[i] = max(cl, prev_fl) if pc >= prev_fl else cl
        # Determine trend
        close_now = cl_arr[i]
        if trend[i - 1]:
            trend[i] = close_now >= fl[i]
        else:
            trend[i] = close_now > fu[i]
    df["supertrend_bull"] = pd.Series(trend, index=df.index)
    df["supertrend_upper"] = pd.Series(fu, index=df.index)
    df["supertrend_lower"] = pd.Series(fl, index=df.index)
    # RSI(2) for Connors mean-reversion (L strategy)
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=1, adjust=False).mean()   # Wilder EMA with α=1/2
    avg_loss = loss.ewm(com=1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    df["rsi2"] = 100 - 100 / (1 + rs)
    df["rsi2"] = df["rsi2"].fillna(50.0)
    return df


def load_prices(ticker: str, market: str = "KR") -> pd.DataFrame | None:
    """주가 데이터 로드. market='KR' → KR_{ticker}.csv, market='US' → US_{ticker}.csv"""
    if market == "US":
        path = PRICE_DIR / f"US_{ticker}.csv"
    else:
        path = PRICE_DIR / f"KR_{ticker}.csv"

    if not path.exists():
        return None

    st = path.stat()
    cache_key = (_PRICE_CACHE_VERSION, pd.__version__, st.st_mtime_ns, st.st_size)
    cache_file = _PRICE_CACHE_DIR / f"{path.stem}.pkl"
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as fh:
                stored_key, df = pickle.load(fh)
            if stored_key == cache_key:
                return df
        except Exception:
            pass  # corrupt/stale cache — recompute below

    df = _load_prices_uncached(path)
    if df is None:
        return None
    try:
        _PRICE_CACHE_DIR.mkdir(exist_ok=True)
        tmp = cache_file.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump((cache_key, df), fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(cache_file)
    except Exception:
        pass  # cache write failure is non-fatal
    return df


# ── Fast point lookups (perf only — results identical) ────────────────────────
# pd.Series.asof / df.loc[ts] dominate the per-day simulation loops. These
# helpers do the same lookups via numpy searchsorted / a per-frame position map,
# returning the exact same float64 values.

def _fast_asof_raw(series: pd.Series, ts: pd.Timestamp) -> float:
    """Exact mirror of float(series.asof(ts)) for a sorted DatetimeIndex.

    Series.asof returns the last non-NaN value at or before ts (NaN if none).
    """
    idx = series.index.values.view("i8")
    pos = int(idx.searchsorted(ts.value, side="right")) - 1
    vals = series.to_numpy()
    if vals.dtype.kind == "f":
        while pos >= 0 and np.isnan(vals[pos]):
            pos -= 1
    return float(vals[pos]) if pos >= 0 else float("nan")


def asof_value(series: pd.Series, day: dt.date) -> float:
    value = _fast_asof_raw(series, pd.Timestamp(day))
    return value if not math.isnan(value) else 0.0


class _FastFrame:
    """Per-DataFrame numpy views: {timestamp→row position} + column arrays."""
    __slots__ = ("df", "pos", "cols")

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df
        self.pos = {int(v): i for i, v in enumerate(df.index.values.view("i8"))}
        self.cols: dict[str, np.ndarray] = {}


_FAST_FRAMES: dict[int, _FastFrame] = {}


def _fast_frame(df: pd.DataFrame) -> _FastFrame:
    ff = _FAST_FRAMES.get(id(df))
    if ff is None or ff.df is not df:
        ff = _FastFrame(df)
        _FAST_FRAMES[id(df)] = ff
    return ff


def _px(df: pd.DataFrame, day_ts: pd.Timestamp, col: str) -> float | None:
    """float(df.loc[day_ts][col]) if day_ts in df.index else None."""
    ff = _fast_frame(df)
    i = ff.pos.get(day_ts.value)
    if i is None:
        return None
    arr = ff.cols.get(col)
    if arr is None:
        arr = ff.cols[col] = df[col].to_numpy()
    return float(arr[i])


def _has_day(df: pd.DataFrame, day_ts: pd.Timestamp) -> bool:
    """Equivalent to `day_ts in df.index` (unique sorted index)."""
    return day_ts.value in _fast_frame(df).pos
