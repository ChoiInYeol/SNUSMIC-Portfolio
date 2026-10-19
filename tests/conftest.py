"""공용 픽스처 — 합성 가격 유니버스 위에서 backtest_momentum의 실제 엔진을 구동한다.

설계 원칙:
- 실데이터(data/silver/prices)는 매일 갱신되므로 회귀 게이트로 쓸 수 없다.
  고정 시드 합성 데이터 → 코드 변경만이 결과를 바꾼다.
- 가격 로딩은 실제 _load_prices_uncached를 통과시켜 지표 파이프라인(ATR, MA,
  supertrend, RSI2)까지 함께 검증한다.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import backtest_momentum as bt  # noqa: E402
import backtest.fx as fx_state  # noqa: E402

GOLDEN_DIR = Path(__file__).parent / "golden"


@pytest.fixture(autouse=True)
def _restore_fx_state():
    """_USDKRW 전역을 테스트마다 원복 — 테스트 간 FX 누수 방지.

    패키지 분할 후 가변 전역은 backtest.fx 모듈에 산다. bt._USDKRW(재노출 복사본)이
    아니라 fx_state._USDKRW(엔진이 실제로 읽는 attribute)를 저장/복원해야 한다.
    """
    saved = fx_state._USDKRW
    yield
    fx_state._USDKRW = saved


# ── 합성 가격 생성 ────────────────────────────────────────────────────────────

def synth_ohlcv(
    seed: int,
    n_days: int,
    start: dt.date = dt.date(2019, 1, 2),
    start_price: float = 10_000.0,
    drift: float = 0.0006,
    vol: float = 0.02,
) -> pd.DataFrame:
    """고정 시드 기하 랜덤워크 OHLCV. default_rng 스트림은 플랫폼 불변."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n_days)
    close = start_price * np.exp(np.cumsum(rng.normal(drift, vol, n_days)))
    open_ = close * (1 + rng.normal(0, 0.003, n_days))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n_days)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n_days)))
    volume = rng.integers(100_000, 1_000_000, n_days).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def flat_ohlcv(
    n_days: int,
    price: float = 10_000.0,
    band: float = 10.0,
    start: dt.date = dt.date(2019, 1, 2),
) -> pd.DataFrame:
    """상수 가격 ± band — ATR을 손으로 계산할 수 있는 결정적 경로."""
    idx = pd.bdate_range(start, periods=n_days)
    return pd.DataFrame(
        {
            "open": np.full(n_days, price),
            "high": np.full(n_days, price + band),
            "low": np.full(n_days, price - band),
            "close": np.full(n_days, price),
            "volume": np.full(n_days, 500_000.0),
        },
        index=idx,
    )


def load_via_pipeline(df: pd.DataFrame, tmp_dir: Path, name: str) -> pd.DataFrame:
    """실제 CSV 라운드트립 + 지표 계산 경로(_load_prices_uncached)를 통과시킨다."""
    path = tmp_dir / f"{name}.csv"
    df.to_csv(path)
    out = bt._load_prices_uncached(path)
    assert out is not None, f"synthetic CSV {name} failed to load"
    return out


def make_report_entry(
    rdate: dt.date,
    display_name: str,
    market: str = "KR",
    target_price: float | None = None,
) -> dict:
    """ticker_reports 항목 — 엔진이 읽는 모든 키 포함."""
    return {
        "report_date": rdate,
        "display_name": display_name,
        "target_price": target_price,
        "market": market,
        "school": "TEST",
        "source_file": "synthetic.pdf",
        "stated_upside_pct": None,
    }


# ── 합성 유니버스 (세션 스코프 — 골든/리그레션 공용) ──────────────────────────

@pytest.fixture(scope="session")
def universe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """KR 5종 + US 1종, 2019-01~2021-09 (700 영업일), 월별 스태거 리포트."""
    tmp = tmp_path_factory.mktemp("prices")
    n = 700
    prices: dict[str, pd.DataFrame] = {}
    ticker_reports: dict[str, list[dict]] = {}
    reports: list[tuple[dt.date, str, str, int]] = []

    kr_tickers = [f"KR000{i}" for i in range(1, 6)]
    for i, tk in enumerate(kr_tickers):
        prices[tk] = load_via_pipeline(synth_ohlcv(seed=10 + i, n_days=n), tmp, tk)
    prices["US0001"] = load_via_pipeline(
        synth_ohlcv(seed=77, n_days=n, start_price=100.0, vol=0.018), tmp, "US0001"
    )

    # 캘린더: 워밍업 후 2019-07-01부터
    full_idx = prices["KR0001"].index
    calendar = [ts.date() for ts in full_idx if ts.date() >= dt.date(2019, 7, 1)]

    # 리포트: 2019-08부터 6주 간격으로 종목 순환 발간 (단독 커버, n_clubs=1)
    all_tickers = kr_tickers + ["US0001"]
    rdates = [calendar[20 + 30 * k] for k in range(12)]
    for k, rdate in enumerate(rdates):
        tk = all_tickers[k % len(all_tickers)]
        market = "US" if tk.startswith("US") else "KR"
        reports.append((rdate, tk, "synthetic", 1))
        ticker_reports.setdefault(tk, []).append(
            make_report_entry(rdate, f"{tk}-주식회사", market=market)
        )

    # USDKRW: 1300 + 80·sin — 결정적, FX 경로 상시 활성
    fx_vals = 1300.0 + 80.0 * np.sin(np.arange(len(full_idx)) / 40.0)
    usdkrw = pd.Series(fx_vals, index=full_idx)

    return {
        "prices": prices,
        "reports": reports,
        "ticker_reports": ticker_reports,
        "calendar": calendar,
        "usdkrw": usdkrw,
    }
