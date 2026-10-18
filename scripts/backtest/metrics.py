# -*- coding: utf-8 -*-
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import IS_END, OOS_START
from .warehouse import _fast_asof_raw, asof_value



# ──────────────────────────────────────────────────────────────────────────────
# Shared result computation
# ──────────────────────────────────────────────────────────────────────────────

def _compute_result(
    nav_series: list[tuple[str, float]],
    trades: list[dict],
    start_capital: float,
    label: str,
    open_positions: dict | None = None,
) -> dict:
    nav_df = pd.Series({pd.Timestamp(d): v for d, v in nav_series}).sort_index()
    daily_ret = nav_df.pct_change().dropna()
    _tr_raw = nav_df.iloc[-1] / start_capital - 1
    total_return = float(_tr_raw) if not math.isnan(_tr_raw) else 0.0
    years = (nav_df.index[-1] - nav_df.index[0]).days / 365.25
    _cagr_raw = (nav_df.iloc[-1] / start_capital) ** (1 / years) - 1 if years > 0 else None
    cagr = _cagr_raw if (_cagr_raw is not None and not math.isnan(_cagr_raw) and not math.isinf(_cagr_raw)) else None
    _sharpe_raw = float(daily_ret.mean() / daily_ret.std() * math.sqrt(252)) if daily_ret.std() else None
    sharpe = _sharpe_raw if (_sharpe_raw is not None and not math.isnan(_sharpe_raw)) else None
    # Drawdown starts at the supplied capital, before any first-day fees/trades.
    _mdd_path = pd.concat([
        pd.Series([float(start_capital)], index=[nav_df.index[0] - pd.Timedelta(nanoseconds=1)]),
        nav_df,
    ])
    _mdd_raw = float((_mdd_path / _mdd_path.cummax() - 1).min())
    mdd = _mdd_raw if not math.isnan(_mdd_raw) else 0.0

    closed_trades = [t for t in trades if not t.get("exit_reason", "").endswith("미청산")]
    wins = [t for t in closed_trades if t["return_pct"] > 0]

    is_mask = nav_df.index.date <= IS_END
    oos_mask = nav_df.index.date >= OOS_START

    def period_metrics(mask: pd.Series) -> dict:
        sub = nav_df[mask]
        if len(sub) < 2:
            return {}
        ret = sub.pct_change().dropna()
        period_start = float(start_capital) if sub.index[0] == nav_df.index[0] else float(sub.iloc[0])
        _total = sub.iloc[-1] / period_start - 1
        _years = (sub.index[-1] - sub.index[0]).days / 365.25
        _cagr_raw = (sub.iloc[-1] / period_start) ** (1 / _years) - 1 if _years > 0 else None
        _cagr = _cagr_raw if (_cagr_raw is not None and not math.isnan(_cagr_raw) and not math.isinf(_cagr_raw)) else None
        _sharpe_raw = float(ret.mean() / ret.std() * math.sqrt(252)) if ret.std() else None
        _sharpe = _sharpe_raw if (_sharpe_raw is not None and not math.isnan(_sharpe_raw)) else None
        _period_path = pd.concat([
            pd.Series([period_start], index=[sub.index[0] - pd.Timedelta(nanoseconds=1)]),
            sub,
        ])
        _mdd_raw = float((_period_path / _period_path.cummax() - 1).min())
        _mdd = _mdd_raw if not math.isnan(_mdd_raw) else 0.0
        return {
            "start": sub.index[0].date().isoformat(),
            "end": sub.index[-1].date().isoformat(),
            "total_return_pct": round(float(_total) * 100, 2) if not math.isnan(_total) else None,
            "cagr_pct": round(_cagr * 100, 2) if _cagr is not None else None,
            "sharpe": round(_sharpe, 2) if _sharpe is not None else None,
            "mdd_pct": round(_mdd * 100, 2),
        }

    year_last = nav_df.resample("YE").last().dropna()
    yearly = year_last.pct_change()
    if len(year_last):
        # First calendar year's return is measured against initial capital, not first recorded NAV.
        yearly.iloc[0] = year_last.iloc[0] / start_capital - 1
    yearly = (yearly * 100).round(2)

    equity_weekly = [
        {"date": ts.date().isoformat(), "nav": round(v / start_capital, 4)}
        for ts, v in nav_df.resample("W-FRI").last().dropna().items()
    ]

    # Max single trade return
    max_trade = max((t["return_pct"] for t in closed_trades), default=None)
    max_trade_info = max(closed_trades, key=lambda t: t["return_pct"], default=None)

    result: dict = {
        "label": label,
        "metrics": {
            "start": nav_series[0][0],
            "end": nav_series[-1][0],
            "initial_capital_krw": round(float(start_capital), 2),
            "final_nav_krw": round(float(nav_df.iloc[-1]), 2),
            "pnl_krw": round(float(nav_df.iloc[-1] - start_capital), 2),
            "total_return_pct": round(total_return * 100, 2),
            "cagr_pct": round(cagr * 100, 2) if cagr is not None else None,
            "sharpe": round(sharpe, 2) if sharpe is not None else None,
            "sharpe_definition": "raw daily NAV return, annualized sqrt(252); no risk-free excess subtraction",
            "mdd_pct": round(mdd * 100, 2),
            "trades": len(closed_trades),
            "win_rate_pct": round(len(wins) / len(closed_trades) * 100, 1) if closed_trades else None,
            "avg_hold_days": round(sum(t["days"] for t in closed_trades) / len(closed_trades), 1) if closed_trades else None,
            "max_single_return_pct": round(max_trade, 2) if max_trade is not None else None,
            "best_trade_ticker": max_trade_info.get("display_name", max_trade_info.get("ticker")) if max_trade_info else None,
        },
        "in_sample": period_metrics(is_mask),
        "out_of_sample": period_metrics(oos_mask),
        "yearly": [{"year": ts.year, "return_pct": float(v) if not math.isnan(v) else None} for ts, v in yearly.items()],
        "equity": equity_weekly,
        "trades": trades,
        "nav_df": nav_df,
    }
    if open_positions is not None:
        result["open_positions"] = open_positions
    return result


def compute_tail_stats(trades: list[dict]) -> dict:
    closed = [t for t in trades if not t.get("exit_reason", "").endswith("미청산")]
    if not closed:
        return {}
    returns = sorted([t["return_pct"] for t in closed], reverse=True)
    n = len(returns)
    top10_n = max(1, math.ceil(n * 0.1))
    top_decile = returns[:top10_n]
    monetary_pnl = [t.get("pnl_krw") for t in closed]
    if any(v is None or not math.isfinite(float(v)) for v in monetary_pnl):
        top_decile_pnl_share = None
    else:
        pnl_by_return = [t.get("pnl_krw") for t in sorted(closed, key=lambda t: t["return_pct"], reverse=True)[:top10_n]]
        total_positive_pnl = sum(float(v) for v in monetary_pnl if float(v) > 0)
        top_positive_pnl = sum(float(v) for v in pnl_by_return if float(v) > 0)
        top_decile_pnl_share = (top_positive_pnl / total_positive_pnl * 100) if total_positive_pnl > 0 else 0.0
    doublers = [t for t in closed if t["return_pct"] >= 100]
    top_trades = sorted(closed, key=lambda t: t["return_pct"], reverse=True)[:top10_n]
    avg_hold_top = round(sum(t["days"] for t in top_trades) / len(top_trades), 1) if top_trades else None
    return {
        "total_trades": n,
        "top_decile_n": top10_n,
        "top_decile_pnl_share_pct": round(top_decile_pnl_share, 1) if top_decile_pnl_share is not None else None,
        "top_decile_avg_return_pct": round(sum(top_decile) / len(top_decile), 1) if top_decile else None,
        "multibagger_count": len([t for t in closed if t["return_pct"] >= 400]),
        "doubler_count": len(doublers),
        "top_decile_avg_hold_days": avg_hold_top,
        "top10_trades": [
            {
                "ticker": t["ticker"],
                "market": t.get("market", "KR"),
                "display_name": t.get("display_name", t["ticker"]),
                "return_pct": t["return_pct"],
                "days": t["days"],
                "n_clubs": t.get("n_clubs", 1),
            }
            for t in sorted(closed, key=lambda t: t["return_pct"], reverse=True)[:10]
        ],
    }


def compute_consensus_stats(trades: list[dict]) -> dict:
    closed = [t for t in trades if not t.get("exit_reason", "").endswith("미청산")]
    if not closed:
        return {}
    single = [t for t in closed if t.get("n_clubs", 1) == 1]
    multi = [t for t in closed if t.get("n_clubs", 1) >= 2]

    def stats(group: list[dict]) -> dict:
        if not group:
            return {"count": 0, "avg_return_pct": None, "win_rate_pct": None, "median_return_pct": None}
        returns = [t["return_pct"] for t in group]
        wins = [r for r in returns if r > 0]
        rs = sorted(returns)
        n = len(rs)
        median = rs[n // 2] if n % 2 == 1 else (rs[n // 2 - 1] + rs[n // 2]) / 2
        return {
            "count": len(group),
            "avg_return_pct": round(sum(returns) / len(returns), 2),
            "win_rate_pct": round(len(wins) / len(returns) * 100, 1),
            "median_return_pct": round(median, 2),
        }

    s = stats(single)
    m = stats(multi)
    return {
        "single_club": s,
        "multi_club": m,
        "alpha_multi_vs_single": round((m["avg_return_pct"] or 0) - (s["avg_return_pct"] or 0), 2) if single and multi else None,
        "note": "≥2개 학회가 동시에 Buy 의견을 낸 종목이 단독 커버 종목 대비 더 높은 수익률을 보이는지 검증합니다.",
    }


# ──────────────────────────────────────────────────────────────────────────────
# v24: Deflated Sharpe Ratio + 워크포워드 일관성
# ──────────────────────────────────────────────────────────────────────────────

def compute_dsr_stats(all_strategies: dict[str, dict]) -> dict[str, dict]:
    """Deflated Sharpe Ratio (Bailey & López de Prado 2014) — 다중검정 보정.

    N개 변형을 같은 데이터에서 시도해 최고를 고르는 선택 과정 자체가 헤드라인
    샤프를 부풀린다. SR0 = N번의 무정보 시도에서 기대되는 최대 샤프(시도 간
    샤프 분산 기반, E[max] of N gaussians). DSR = P(진짜 SR > SR0) — 비정규성
    (왜도·첨도) 보정 포함. PSR = P(진짜 SR > 0), 단일 전략 기준.

    DSR ≥ 0.95: 26개를 시도해 골랐다는 사실을 감안해도 스킬이 유의.
    """
    from scipy.stats import norm

    sr_daily: dict[str, float] = {}
    moments: dict[str, tuple[int, float, float]] = {}
    for key, r in all_strategies.items():
        nav = r.get("nav_df")
        if nav is None or len(nav) < 30:
            continue
        ret = nav.pct_change().dropna()
        sd = float(ret.std())
        if not sd or math.isnan(sd):
            continue
        sr_daily[key] = float(ret.mean()) / sd
        # pandas kurt()는 excess → γ4 = kurt + 3
        moments[key] = (len(ret), float(ret.skew()), float(ret.kurt()) + 3.0)

    n_trials = len(sr_daily)
    if n_trials < 2:
        return {}

    var_sr = float(np.var(list(sr_daily.values()), ddof=1))
    emc = 0.5772156649015329  # Euler–Mascheroni
    sr0 = (
        math.sqrt(var_sr)
        * ((1 - emc) * norm.ppf(1 - 1 / n_trials) + emc * norm.ppf(1 - 1 / (n_trials * math.e)))
        if var_sr > 0
        else 0.0
    )

    out: dict[str, dict] = {}
    for key, sr in sr_daily.items():
        t, skew, kurt = moments[key]
        denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
        if denom <= 0 or t < 2:
            continue
        scale = math.sqrt(t - 1) / math.sqrt(denom)
        out[key] = {
            "psr": round(float(norm.cdf(sr * scale)), 4),
            "dsr": round(float(norm.cdf((sr - sr0) * scale)), 4),
            "sr0_annualized": round(sr0 * math.sqrt(252), 3),
            "n_trials": n_trials,
            "significant_after_deflation": bool(norm.cdf((sr - sr0) * scale) >= 0.95),
        }
    return out


WF_WINDOW_MONTHS = 6   # 워크포워드 윈도 길이
WF_MIN_OBS = 40        # 이보다 짧은 꼬리 윈도는 통계에서 제외


def compute_walkforward(
    all_strategies: dict[str, dict],
    kospi: pd.Series,
    window_months: int = WF_WINDOW_MONTHS,
) -> dict[str, dict]:
    """롤링 윈도 일관성 — 단일 IS/OOS 분할의 보완.

    NAV를 달력 기준 6개월 윈도로 잘라 윈도별 수익률·샤프·MDD와 같은 구간
    KOSPI 수익률을 비교한다. 파라미터 재적합은 하지 않는다 — 전략 대부분이
    문헌 고정 파라미터이고, D+/U의 Optuna 파라미터는 IS에서 적합되었으므로
    IS 구간 윈도는 참고치, OOS(2024-01 이후) 윈도가 진짜 검증이다.
    """
    out: dict[str, dict] = {}
    for key, r in all_strategies.items():
        nav = r.get("nav_df")
        if nav is None or len(nav) < WF_MIN_OBS:
            continue
        grp = (nav.index.year * 12 + (nav.index.month - 1)) // window_months
        windows: list[dict] = []
        for _, sub in nav.groupby(grp):
            if len(sub) < WF_MIN_OBS:
                continue
            ret = sub.pct_change().dropna()
            sd = float(ret.std())
            sharpe = round(float(ret.mean()) / sd * math.sqrt(252), 2) if sd else None
            w_ret = float(sub.iloc[-1] / sub.iloc[0] - 1)
            mdd = float((sub / sub.cummax() - 1).min())
            k0 = _fast_asof_raw(kospi, sub.index[0])
            k1 = _fast_asof_raw(kospi, sub.index[-1])
            k_ret = (k1 / k0 - 1) if (not math.isnan(k0) and not math.isnan(k1) and k0 > 0) else None
            windows.append({
                "start": sub.index[0].date().isoformat(),
                "end": sub.index[-1].date().isoformat(),
                "return_pct": round(w_ret * 100, 2),
                "sharpe": sharpe,
                "mdd_pct": round(mdd * 100, 2),
                "kospi_return_pct": round(k_ret * 100, 2) if k_ret is not None else None,
                "beat_kospi": (w_ret > k_ret) if k_ret is not None else None,
                "oos": sub.index[0].date() >= OOS_START,
            })

        if not windows:
            continue

        def _consistency(ws: list[dict]) -> dict | None:
            if not ws:
                return None
            sharpes = sorted(w["sharpe"] for w in ws if w["sharpe"] is not None)
            beats = [w["beat_kospi"] for w in ws if w["beat_kospi"] is not None]
            return {
                "n_windows": len(ws),
                "positive_pct": round(sum(1 for w in ws if w["return_pct"] > 0) / len(ws) * 100, 1),
                "beat_kospi_pct": round(sum(beats) / len(beats) * 100, 1) if beats else None,
                "median_sharpe": (
                    round(sharpes[len(sharpes) // 2], 2) if len(sharpes) % 2 == 1
                    else round((sharpes[len(sharpes) // 2 - 1] + sharpes[len(sharpes) // 2]) / 2, 2)
                ) if sharpes else None,
                "worst_sharpe": min(sharpes) if sharpes else None,
                "worst_window_return_pct": min(w["return_pct"] for w in ws),
            }

        out[key] = {
            "windows": windows,
            "consistency": _consistency(windows),
            "consistency_oos": _consistency([w for w in windows if w["oos"]]),
        }
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Extension gauge helper
# ATR% Multiple from 50-MA (Minervini 커뮤니티 관행, TradingView Fred6724)
#   A = ATR(14) / price   (ATR%)
#   B = (price - 50SMA) / 50SMA  (% gain from 50-SMA)
#   extension = B / A
# Returns None if insufficient data.
# ──────────────────────────────────────────────────────────────────────────────

def compute_extension(df: pd.DataFrame, day: dt.date) -> float | None:
    """
    과열 게이지: ATR% Multiple from 50-MA.
    A = ATR(14)/price,  B = (price-50SMA)/50SMA
    extension = B / A.
    양수 = 50SMA 위 과열; 음수 = 50SMA 아래.
    None = 데이터 불충분.
    """
    price = asof_value(df["close"], day)
    if price <= 0:
        return None
    atr14 = asof_value(df["atr14"], day)
    if not atr14 or atr14 <= 0:
        return None
    ma50 = asof_value(df["ma50"], day)
    if not ma50 or ma50 <= 0:
        return None
    A = atr14 / price          # ATR%
    B = (price - ma50) / ma50  # % from 50SMA
    return round(B / A, 2)
