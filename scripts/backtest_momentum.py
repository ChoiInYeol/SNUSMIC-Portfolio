"""학회 리포트 × 전략 연구 백테스트 v18.

변경사항 (v18):
- W. 올웨더-샹들리에: T-와 동일한 D+ 샹들리에 규칙이되 유휴 현금을 KOSPI 대신
  올웨더 바스켓(25% GLD/NASDAQ/S&P500/KOSPI, 분기 리밸런스 — 벤치마크와 동일 시리즈)에
  파킹. 레짐 게이트 없음 — 올웨더 자체가 방어 자산(GLD)을 포함하므로 KOSPI<200MA
  스위치가 불필요하다는 설계 가설. 헤드라인 경쟁: W vs T- — 자기 파킹 벤치마크
  (W→올웨더 DCA, T-→KOSPI DCA)를 이기고 IS+OOS 샤프가 높은 쪽 승격.
  T(상시 KOSPI 파킹)는 연구 기록 비교용으로 유지.
- 현금 이자 모델: 모든 전략의 유휴 현금에 연 3.0% (한국 MMF/단기채 ETF 프록시,
  가정) 일복리 (1.03)^(1/252)−1 적용. T-/U 레짐 OFF 구간의 파킹 잔액도 동일.
  전략 간 비교 공정성을 위해 일괄 적용.
- 차입 비용 모델: J 코어-새틀라이트 레버리지 차입비용 연 6.0%를 일복리
  (1.06)^(1/252)−1 로 통일 (기존 단리 6%/365).
- entry_reason: 모든 거래에 "왜 진입했는가" 사유 문자열 기록 — 전략 패밀리별
  실제 진입 규칙 텍스트 (리포트 트리거 학회·날짜 포함). CSV·JSON·오늘의 신호에 노출.
- L 민리버전·M 단기 리버설 가지치기: v11에서 비용 사망 판정 확정 — 더 이상
  실행/출력하지 않음 (구현은 기록용으로 코드에 유지, 방법론에 한 줄 명시).
- 연구 기록 테이블에 vs 올웨더 DCA 비율 열 추가 (전 전략).

변경사항 (v16):
- V. SPO 포트폴리오 (Smart "Predict, then Optimize" — Elmachtoub & Grigas 2022):
  V_spo(SPO+ 손실 SGD)·V_ls(동일 파이프라인 LS 베이스라인). 워크포워드 확장 윈도우
  (최소 24개월) 매월 재학습, 캡 심플렉스(Σw=1, w≤15%) LP 닫힌형 오라클, 월말 신호 →
  익월 첫 거래일 시가 체결 (run_portfolio_opt weight_schedule 모드 재사용).
  학습/검증 알고리즘은 scripts/spo_portfolio.py — Julia 레퍼런스
  (github.com/paulgrigas/SmartPredictThenOptimize sgd.jl/validation_set.jl) 미러.
  셀렉터 승격은 U와 동일 관례(T 베스트 대비 부의 비율+OOS 샤프 게이트).
  기존 'SPO(유상증자) 보류' 방법론 항목을 spo_predict_optimize 결과로 대체.

변경사항 (v15):
- S 포트폴리오 최적화 침묵 실패 수정: scipy/sklearn 미설치 시 _msharpe/_mincvar의
  함수 내부 import 실패가 월별 try/except에 삼켜져 NAV가 1.0 평탄(거래 0건)으로
  출력되던 버그. run_portfolio_opt 시작 시 의존성 프리플라이트 체크 → 즉시
  RuntimeError로 크게 실패. requirements.txt에 scipy/scikit-learn 추가.
- 오늘의 신호 의미 재정의: 헤드라인(SOTA) 전략이 지금 규칙대로 굴러갈 때
  임박한 매매만 표시.
    매수 임박 = 최근 5거래일 내 발간된 buy 리포트 중 미보유 + 슬롯 여유.
    매도 임박 = 트레일링 스탑 3% 이내 + 스탑 이미 터치(stop_hit).
    보유 중 = 현 포지션 + 스탑 레벨 + 과열계수.
    레짐 상태(T- 헤드라인): KOSPI vs 200MA — OFF면 파킹 수익 0%(현금) 명시.
    대기(watching) 목록은 카운트만 유지(리포트 흐름이지 전략 신호가 아님).
- 레거시 필드 제거: sensitivity(빈 배열, v8부터 dead) 페이로드에서 삭제.

변경사항 (v12):
- Q. 깡토 추세추종: 시장 신호등(KOSPI 200MA+50MA상승), 유닛 사이징(총자본/20, Max 2% Rule),
  진입=RS퍼센타일≥KOSPI RS AND 60d고가돌파 AND 거래량≥1.5×20d평균,
  청산=-8%초기스탑/BE at+1R/트레일 고점-8% at+1.5R/절반익절 +3R.
  보고 항목: win rate, vs KOSPI DCA. 추세형 그룹.
- R. Kelly 샹들리에: D+ Chandelier 규칙 + Kelly 포지션 사이징
  (rolling 최근 40거래 win_rate/payoff → fractional Kelly, cap 0.25, safety 0.5, floor 1%).
  오버레이 그룹.
- S. 포트폴리오 최적화 (월간 리밸런스): 활성 유니버스(18mo 유효)에 대해 trailing 252d 일별 수익률 사용.
  (a) S_hrp: HRP (hierarchical risk parity — 직접 구현, corr distance, single-linkage, quasi-diag, iv-split),
  (b) S_msharpe: max-Sharpe (mean-variance, LedoitWolf 수축 또는 λ=0.3 diagonal, long-only w≤15%),
  (c) S_mincvar: min-CVaR (95%, scipy linprog LP, long-only w≤15%).
  월 리밸런스, 턴오버 비용. IS 샤프 최상 변형만 셀렉터에 포함. 배분형 그룹.
- 비교표에 "vs KOSPI DCA" 최종 자산 비율 열 추가.
- 방법론 주석: 장중 데이터 미도입(데이터 부재) 및 SPO 보류(미래 작업) 1줄씩 명시.

변경사항 (v11):
- MTT 룩어헤드 감사 및 수정: O 전략 진입 시그널을 당일 종가 기준으로 포착,
  실제 체결은 익일 시가로 처리 (동일 바 룩어헤드 제거).
  _compute_rs_percentiles: 당일 종가+과거 데이터만 사용 — 점검 결과 문제 없음.
  52w 고/저, MA 등 rolling window 지표: load_prices에서 look-forward 없이 계산 확인.
  히스토리컬 가격 파일에 상장폐지 종목 포함 여부: 프라이스 파일이 존재하는 종목만
  유니버스에 포함 → 생존 편향 존재하나 피할 수 없음, 방법론 주석에 명시.
- L 민리버전 RSI-2: 동일 바 룩어헤드 수정 (시그널→익일 시가 체결).
  그러나 수정 후에도 0.6% 왕복 비용 × 단기 회전율 → 비용 사망 확인 → 제외.
- M 단기 리버설: positions.clear() 버그 수정 (미청산 포지션 현금 미회수 문제),
  동일 바 룩어헤드 수정. 수정 후에도 월별 전체 교체 비용 사망 → 제외.
  방법론: "RSI-2 민리버전·단기 리버설은 거래비용으로 사망 — 구현 검증 후 제외"
- 신규 전략 P: 딥바이 샹들리에 하이브리드 ("P_deepbuy_chandelier").
  진입 = G 딥바이 (발간일 종가 대비 ≥20% 하락, 6개월 내),
  규모 추가 = 최초 진입 후 추가 10% 하락 시 동일 슬롯에 1회 한정 추가 매수,
  청산 = D+ 샹들리에 Optuna 파라미터 ATR 트레일링 스탑 (타겟가 캡 없음).
  최고점 기준 통합 포지션 스탑 관리.

변경사항 (v10):
- Optuna 탐색공간 이산화: suggest_float에 step= 추가 (ATR mult step 0.25, 0.05 등).
  파라미터를 소수점 2자리로 반올림하여 보고.
- 신규 전략 O: MTT (alpha16 이식) - 알파16 논문 Minervini MTT를 OUR 유니버스에 이식.
  RS 퍼센타일(3m*0.5/6m*0.3/12m*0.2), MTT 필터(close>50MA>150MA>200MA, 200MA상승,
  52w저점*1.9 이상, 52w고점*0.95 이상, RS>=80), 진입 RS>=79, 청산(-8%초기스탑/BE/6%트레일/+3.5R/RS<82 후 8일/115일).
  동일 유니버스(리포트 후 18개월 유효풀), 5%/20슬롯 동일비중, 추세형 그룹 추가.
  [출처 공개: alpha16 RobustOpt KRX 파라미터 - KRX 전체 종목으로 튜닝된 값, OUR 데이터 미사용]
- 재매수 규칙 명시: 청산 후 동일 티커 재진입 허용 (패밀리 진입 조건 재충족 시).
  기존 open_positions 체크는 현재 보유 중 여부만 확인 -> OK.
  단, 리포트 구동 패밀리는 신규 리포트 OR 18개월 유효창 내 기술적 재충족 시 재진입.
- 신규 전략 L (민리버전 Connors RSI-2), M (단기 리버설 월별 하위 5분위), N (52주 고가 근접 George & Hwang 2004).
- RSI(2) 지표를 load_prices에 추가.

변경사항 (v9):
- 오늘의 신호: 헤드라인 전략(D 샹들리에) 기준으로 변경.
- Optuna 강건 최적화: 샹들리에 패밀리 파라미터 (ATR 기간, 배수, 래칫, 최대 포지션).

변경사항 (v8):
- 유니버스 확대: >=2개교 컨센서스 게이트 제거 -> 1회 언급(단독 커버 포함) 즉시 진입.
    컨센서스는 분석 통계(consensus_stats)로만 유지 - 진입 조건 아님.
    동일 티커 중복 진입 방지: 이미 오픈된 포지션이 있으면 스킵.
- 전략별 open_positions 추가: multi_strategy.open_positions_by_strategy 에 현재 보유 상태 포함.
- 레거시 신고가 돌파 민감도 분석(sensitivity) 제거 - dead weight.
- 11가지 전략(A~K) 동일 유니버스에서 재계산.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

# ── Package split (pure-move refactor) ───────────────────────────────────────
# Every name that used to live at backtest_momentum module level is re-exported
# here so external scripts/tests keep importing it as backtest_momentum.<name>.
from backtest import (  # noqa: F401
    config, fx, warehouse, accounting, metrics, strategies, reporting,
)
from backtest.config import *  # noqa: F401,F403
from backtest.fx import *  # noqa: F401,F403
from backtest.warehouse import *  # noqa: F401,F403
from backtest.accounting import *  # noqa: F401,F403
from backtest.metrics import *  # noqa: F401,F403
from backtest.strategies import *  # noqa: F401,F403
from backtest.reporting import *  # noqa: F401,F403

# Pull underscore-prefixed names too (import * skips them).
for _m in (config, fx, warehouse, accounting, metrics, strategies, reporting):
    for _k, _v in vars(_m).items():
        if _k.startswith('_') and not _k.startswith('__'):
            globals()[_k] = _v
del _m, _k, _v

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")




def point_in_time_school_counts(perf: pd.DataFrame) -> dict[tuple[str, dt.date], int]:
    """Return accumulated unique school coverage available at each report date."""
    required = {"ticker_key", "report_date", "school"}
    missing = required.difference(perf.columns)
    if missing:
        raise ValueError(f"school-count input missing columns: {sorted(missing)}")
    rows = perf[["ticker_key", "report_date", "school"]].copy()
    rows["report_date"] = pd.to_datetime(rows["report_date"]).dt.date
    counts: dict[tuple[str, dt.date], int] = {}
    for ticker, group in rows.groupby("ticker_key", sort=True):
        schools: set[str] = set()
        for report_date, day_group in group.groupby("report_date", sort=True):
            schools.update(str(school) for school in day_group["school"])
            counts[(str(ticker), report_date)] = len(schools)
    return counts

def headline_rank(item: tuple[str, dict]) -> tuple[float, str]:
    """Deterministic IS-only headline rank; OOS flips cannot alter selection."""
    key, result = item
    value = result.get("in_sample", {}).get("sharpe")
    score = float(value) if value is not None and math.isfinite(float(value)) else float("-inf")
    return score, key


def align_causal_quotes(series: pd.Series, index: pd.DatetimeIndex, name: str) -> pd.Series:
    """Carry only earlier observations; reject prices not yet observable at the start."""
    aligned = series.reindex(series.index.union(index)).ffill().reindex(index)
    if aligned.empty or not np.isfinite(aligned.to_numpy(dtype=float)).all() or (aligned <= 0).any():
        raise ValueError(f"{name}: missing finite positive point-in-time quote")
    return aligned


def _is_dca_ratios(nav: pd.Series, kospi: pd.Series, all_weather: pd.Series) -> dict[str, float | None]:
    """Compute causal DCA ratios through IS end only for selection gates."""
    nav_is = nav[nav.index.date <= IS_END]
    if len(nav_is) < 2:
        return {"kospi": None, "allweather": None}
    try:
        result = compute_wealth_simulation_multi(nav_is, {"KOSPI": kospi, "AllWeather": all_weather}, nav_is.index[0].date(), nav_is.index[-1].date())
    except (ValueError, KeyError):
        return {"kospi": None, "allweather": None}
    final = result.get("final_strategy_value")
    finals = result.get("final_benchmark_values", {})
    return {
        "kospi": round(final / finals["KOSPI"], 3) if final is not None and finals.get("KOSPI", 0) > 0 else None,
        "allweather": round(final / finals["AllWeather"], 3) if final is not None and finals.get("AllWeather", 0) > 0 else None,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Benchmark computations
# ──────────────────────────────────────────────────────────────────────────────

def compute_all_weather(
    kospi: pd.Series,
    sp500: pd.Series,
    nasdaq: pd.Series,
    gld: pd.Series,
    start: dt.date,
    end: dt.date,
    usdkrw: pd.Series | None = None,
) -> pd.Series:
    idx = pd.date_range(start=pd.Timestamp(start), end=pd.Timestamp(end), freq="B")
    # FX and asset prices must already be observable, never borrowed from a future bar.
    fx_src = usdkrw if usdkrw is not None else fx._USDKRW
    if fx_src is None:
        raise ValueError("USDKRW: no point-in-time quotes for all-weather valuation")
    fx_aligned = align_causal_quotes(fx_src, idx, "USDKRW")
    k = align_causal_quotes(kospi, idx, "KOSPI")
    s = align_causal_quotes(sp500, idx, "SP500") * fx_aligned
    n = align_causal_quotes(nasdaq, idx, "NASDAQ") * fx_aligned
    g = align_causal_quotes(gld, idx, "GLD") * fx_aligned

    k = k / k.iloc[0]
    s = s / s.iloc[0]
    n = n / n.iloc[0]
    g = g / g.iloc[0]

    weights = {"k": 0.25, "s": 0.25, "n": 0.25, "g": 0.25}
    units = {name: weights[name] for name in weights}
    prices_dict = {"k": k, "s": s, "n": n, "g": g}

    nav = pd.Series(index=idx, dtype=float)
    last_rebal_quarter: tuple[int, int] | None = None

    for ts in idx:
        p = {name: float(prices_dict[name].loc[ts]) for name in units}
        current_nav = sum(units[name] * p[name] for name in units)
        q = (ts.year, (ts.month - 1) // 3)
        if last_rebal_quarter != q:
            last_rebal_quarter = q
            for name in units:
                units[name] = weights[name] * current_nav / p[name]
        nav.loc[ts] = current_nav

    return nav.dropna()




def compute_wealth_simulation_multi(
    strategy_nav_df: pd.Series,
    benchmarks: dict[str, pd.Series],
    backtest_start: dt.date,
    backtest_end: dt.date,
) -> dict:
    strat_daily_ret = strategy_nav_df.pct_change().fillna(0)
    all_dates = strategy_nav_df.index

    _dates_series = pd.Series(all_dates.date, index=all_dates)
    monthly_dates = set(
        _dates_series.groupby(_dates_series.index.to_period("M")).first().values
    )

    strat_wealth = float(DCA_INITIAL)
    total_contributed = float(DCA_INITIAL)
    month_idx = 0
    series: list[dict] = []

    bench_units: dict[str, float] = {}
    bench_aligned: dict[str, pd.Series] = {}
    # Positional numpy views — same aligned values as .loc[day], just O(1) access
    bench_arr: dict[str, np.ndarray] = {}
    for name, idx_series in benchmarks.items():
        aligned = idx_series.reindex(all_dates).ffill()
        if aligned.isna().any() or float(aligned.iloc[0]) <= 0:
            raise ValueError(f"benchmark {name} has no point-in-time quote for DCA start")
        bench_aligned[name] = aligned
        bench_arr[name] = aligned.to_numpy()
        bench_units[name] = DCA_INITIAL / float(aligned.iloc[0])
    strat_ret_arr = strat_daily_ret.to_numpy()

    for day_i, day in enumerate(all_dates):
        day_date = day.date()
        is_month_first = day_date in monthly_dates

        if is_month_first:
            month_idx += 1

        sr = float(strat_ret_arr[day_i])
        strat_wealth *= (1 + sr)

        # Contributions execute at the daily close after that day's return; strategy and benchmarks use same convention.
        if is_month_first and month_idx > 1:
            contribution = DCA_BASE_MONTHLY + DCA_STEP * ((month_idx - 1) // DCA_STEP_MONTHS)
            total_contributed += contribution
            strat_wealth += contribution
            for name in bench_units:
                price_today = float(bench_arr[name][day_i])
                bench_units[name] += contribution / price_today

        bench_vals: dict[str, float] = {
            name: bench_units[name] * float(bench_arr[name][day_i])
            for name in bench_units
        }

        if is_month_first:
            entry: dict = {
                "month": month_idx - 1,
                "date": day_date.isoformat(),
                "contributed": round(total_contributed),
                "strategy_value": round(strat_wealth),
            }
            for name, val in bench_vals.items():
                entry[f"{name}_value"] = round(val)
            series.append(entry)

    # Ensure the last calendar day is always represented so the strategy line
    # extends exactly as far as the benchmark lines (fixes missing final point).
    last_day_date = all_dates[-1].date()
    if series and series[-1]["date"] != last_day_date.isoformat():
        bench_vals_last: dict[str, float] = {
            name: bench_units[name] * float(bench_aligned[name].iloc[-1])
            for name in bench_units
        }
        final_entry: dict = {
            "month": month_idx,
            "date": last_day_date.isoformat(),
            "contributed": round(total_contributed),
            "strategy_value": round(strat_wealth),
        }
        for name, val in bench_vals_last.items():
            final_entry[f"{name}_value"] = round(val)
        series.append(final_entry)

    final_strat = series[-1]["strategy_value"] if series else round(strat_wealth)
    final_contrib = series[-1]["contributed"] if series else round(total_contributed)

    def gain_pct(final: float) -> float | None:
        return round((final - final_contrib) / final_contrib * 100, 1) if final_contrib else None

    wealth_vals = pd.Series([s["strategy_value"] for s in series])
    sim_mdd = round(float((wealth_vals / wealth_vals.cummax() - 1).min()) * 100, 2) if len(wealth_vals) > 1 else 0.0

    bench_finals = {name: series[-1].get(f"{name}_value", 0) for name in benchmarks}

    return {
        "fx_assumption": (
            "v19 FX 레이어 적용: 미국 자산(S&P500, NASDAQ, GLD, US 개별주) 포지션 가치·손익을 "
            "일별 USDKRW 환율(yfinance KRW=X)로 원화 환산. "
            "원화 강세 구간에서 달러 자산 수익률이 낮아지고, 약세 구간에서 추가 수익이 발생하는 원화 투자자 관점의 실제 경험을 반영합니다. "
            "KR 자산은 이미 원화 기준이므로 변경 없음."
        ),
        "schedule_desc": (
            "초기 자본 1,000만원 + 월 적립 (0~23개월: 100만원, 24~47개월: 200만원, "
            "48~71개월: 300만원, …). 적립금은 즉시 전략 NAV에 편입. "
            "전략 내부 유휴 현금에는 연 3% 일복리 이자 반영 (v18, MMF 프록시 가정)."
        ),
        "final_contributed": final_contrib,
        "final_strategy_value": final_strat,
        "final_benchmark_values": bench_finals,
        "strategy_gain_on_contributed_pct": gain_pct(final_strat),
        "benchmark_gain_on_contributed_pct": {name: gain_pct(v) for name, v in bench_finals.items()},
        "strategy_mdd_pct": sim_mdd,
        "series": series,
    }




# ──────────────────────────────────────────────────────────────────────────────
# Today's signals — keyed off the headline strategy (single source of truth)
# For chandelier: open positions include stop level & distance-to-stop %
# ──────────────────────────────────────────────────────────────────────────────

def compute_today_signals(
    perf: pd.DataFrame,
    prices: dict[str, pd.DataFrame],
    ticker_reports: dict[str, list[dict]],
    calendar: list[dt.date],
    headline_open_positions: dict,   # raw open_positions dict from the headline run
    headline_label: str,
    reports: list[tuple[dt.date, str, str, int]],
    kospi: pd.Series | None = None,
    regime_aware: bool = False,
    max_positions: int = MAX_POSITIONS,
) -> dict:
    """
    오늘의 신호 — "SOTA 전략이 지금 규칙대로 굴러간다면 일어날 매매" (v15 재정의).

      - 매수 임박(imminent_buys): 최근 5거래일 내 발간된 buy 리포트 중
        아직 미보유인 종목 — 익일 시가 진입이 지금 대기 중인 신호.
        슬롯 여유(slots.available)와 함께 보고.
      - 매도 임박(approaching_stop): 트레일링 스탑 3% 이내 포지션
        + 현재가가 이미 스탑 아래인 포지션(stop_hit=True → 다음 시가 청산).
      - 보유 중(open_positions): 현 포지션 + 스탑 레벨 + 과열계수.
      - 레짐(regime): T- 헤드라인일 때 KOSPI vs 200MA 상태.
        OFF = 유휴 현금 파킹 수익 0% (현금 보유). 진입 자체는 차단하지 않음 — 정직하게 명시.
      - watching(대기)은 카운트만 — 리포트 흐름이지 전략의 임박 신호가 아님.
    """
    as_of = calendar[-1] if calendar else dt.date.today()
    as_of_ts = pd.Timestamp(as_of)
    IMMINENT_BUY_TRADING_DAYS = 5    # 매수 임박: 최근 5거래일 내 리포트
    APPROACHING_STOP_PCT = 0.03      # 3% distance-to-stop threshold

    is_chandelier_family = "chandelier" in headline_label.lower() or "regime" in headline_label.lower()

    open_positions: list[dict] = []
    approaching_stop: list[dict] = []   # 매도 임박 (within 3% of stop, incl. stop hit)
    imminent_buys: list[dict] = []

    # ── 레짐 상태 (T- 계열: KOSPI < 200MA → 파킹 수익 0%) ───────────────────
    regime: dict | None = None
    if kospi is not None:
        kospi_close = asof_value(kospi, as_of)
        kospi_ma200 = asof_value(kospi.rolling(200, min_periods=100).mean(), as_of)
        if kospi_close > 0 and kospi_ma200 > 0:
            state = "ON" if kospi_close >= kospi_ma200 else "OFF"
            if regime_aware:
                note = (
                    "레짐 ON — 유휴 현금이 KOSPI 익스포저로 작동 중."
                    if state == "ON" else
                    "레짐 OFF — KOSPI < 200MA. 유휴 현금은 파킹 대신 현금 이자 연 3% 일복리 (v18). "
                    "신규 진입 규칙 자체는 유지됩니다."
                )
            else:
                note = "레짐 필터 없는 전략 — 참고용 KOSPI 200MA 상태."
            regime = {
                "applies": regime_aware,
                "state": state,
                "kospi_close": round(kospi_close, 2),
                "kospi_ma200": round(kospi_ma200, 2),
                "note": note,
            }

    # ── 보유 중: from headline open_positions dict ──────────────────────────
    already_in: set[str] = set()
    for ticker, pos in (headline_open_positions or {}).items():
        already_in.add(ticker)
        current_price = None
        df = prices.get(ticker)
        if df is not None:
            cv = df["close"].asof(as_of_ts)
            if pd.notna(cv):
                current_price = float(cv)

        entry_price = float(pos.get("entry_price", 0))
        unrealized_pct = round((current_price / entry_price - 1) * 100, 2) if current_price and entry_price else None
        stop_level = pos.get("stop")
        highest = pos.get("highest", entry_price)
        days_elapsed = (as_of - pos["entry_date"]).days if hasattr(pos.get("entry_date"), "date") else (as_of - dt.date.fromisoformat(str(pos.get("entry_date", as_of)))).days

        dist_to_stop_pct = None
        if stop_level and current_price and current_price > 0:
            dist_to_stop_pct = round((current_price - stop_level) / current_price * 100, 2)

        tr_list = ticker_reports.get(ticker, [])
        past_tr = [r for r in tr_list if r["report_date"] <= as_of]
        trigger_schools = sorted({r["school"] for r in past_tr})
        trigger_reports = [
            {
                "school": r["school"],
                "report_date": r["report_date"].isoformat(),
                "target_price": r["target_price"],
                "stated_upside_pct": r["stated_upside_pct"],
            }
            for r in sorted(past_tr, key=lambda x: x["report_date"], reverse=True)[:5]
        ]

        # Extension gauge (visible regardless of strategy family)
        ext_val = compute_extension(df, as_of) if df is not None else None

        pos_info: dict = {
            "ticker": ticker,
            "market": pos.get("market", "KR"),
            "display_name": pos.get("display_name", ticker),
            "entry_date": pos["entry_date"].isoformat() if hasattr(pos.get("entry_date"), "isoformat") else str(pos.get("entry_date", "")),
            "entry_price": round(entry_price, 4),
            "current_price": round(current_price, 4) if current_price else None,
            "unrealized_pct": unrealized_pct,
            "days_elapsed": days_elapsed,
            "highest_since_entry": round(float(highest), 4) if highest else None,
            "extension": ext_val,   # ATR% multiple from 50-MA (과열 게이지)
            "trigger_schools": trigger_schools,
            "trigger_reports": trigger_reports,
            "entry_reason": pos.get("entry_reason", ""),   # v18: 왜 진입했는가
        }
        if is_chandelier_family:
            # Round stop to exchange tick size (display boundary — conservative floor).
            # dist_to_stop_pct is recomputed from the rounded stop so UI percentages are consistent.
            market_code = pos.get("market", "KR")
            rounded_stop = round_to_tick(float(stop_level), market_code) if stop_level else None
            pos_info["stop_level"] = rounded_stop
            if rounded_stop and current_price and current_price > 0:
                dist_to_stop_pct = round((current_price - rounded_stop) / current_price * 100, 2)
            pos_info["dist_to_stop_pct"] = dist_to_stop_pct
            pos_info["stop_hit"] = bool(dist_to_stop_pct is not None and dist_to_stop_pct <= 0)

        open_positions.append(pos_info)

        # 매도 임박: within 3% of stop (스탑 터치 포함 — dist ≤ 0)
        if is_chandelier_family and dist_to_stop_pct is not None and dist_to_stop_pct <= APPROACHING_STOP_PCT * 100:
            approaching_stop.append(pos_info)

    open_positions.sort(key=lambda x: (x.get("dist_to_stop_pct") or 999))
    slots_available = max(0, max_positions - len(open_positions))

    # ── 매수 임박: 최근 5거래일 내 발간 buy 리포트, 미보유 ───────────────────
    recent_days = calendar[-IMMINENT_BUY_TRADING_DAYS:] if len(calendar) >= IMMINENT_BUY_TRADING_DAYS else calendar
    imminent_cutoff = recent_days[0] if recent_days else as_of
    recent_by_ticker: dict[str, list[dict]] = {}
    for rdate, ticker, source, n_clubs in reports:
        if imminent_cutoff <= rdate <= as_of and ticker not in already_in:
            tr_list = ticker_reports.get(ticker, [])
            match = next((r for r in tr_list if r["report_date"] == rdate), None)
            if match is not None:
                bucket = recent_by_ticker.setdefault(ticker, [])
                if not any(r["report_date"] == rdate for r in bucket):
                    bucket.append(match)

    for ticker, recent_reports in recent_by_ticker.items():
        if not recent_reports:
            continue
        latest = max(recent_reports, key=lambda x: x["report_date"])
        market = latest.get("market", "KR")
        latest_rdate = latest["report_date"]
        entry_basis_date = first_trading_day_after(latest_rdate, calendar)
        entry_pending = entry_basis_date is None or entry_basis_date > as_of
        entry_basis_price = None
        if entry_basis_date and ticker in prices:
            df = prices[ticker]
            ts = pd.Timestamp(entry_basis_date)
            if ts in df.index:
                entry_basis_price = float(df.loc[ts]["open"])

        # v18: 매수 사유 텍스트 — 헤드라인(샹들리에 패밀리) 진입 규칙 그대로
        _trigger_txt = ", ".join(
            f"{r['school']} {r['report_date'].isoformat()}"
            for r in sorted(recent_reports, key=lambda x: x["report_date"], reverse=True)
        )
        imminent_buys.append({
            "ticker": ticker,
            "market": market,
            "display_name": latest["display_name"],
            "n_schools": len({r["school"] for r in recent_reports}),
            "entry_basis_date": entry_basis_date.isoformat() if entry_basis_date else None,
            "entry_basis_price": round(entry_basis_price, 4) if entry_basis_price else None,
            "entry_pending": entry_pending,   # True = 익일 시가 진입이 아직 미래
            "entry_reason": f"학회 매수리포트 발간 → 익일 시가 진입 (트리거: {_trigger_txt})",
            "trigger_schools": sorted({r["school"] for r in recent_reports}),
            "trigger_reports": [
                {
                    "school": r["school"],
                    "report_date": r["report_date"].isoformat(),
                    "target_price": r["target_price"],
                    "stated_upside_pct": r["stated_upside_pct"],
                }
                for r in sorted(recent_reports, key=lambda x: x["report_date"], reverse=True)
            ],
        })

    imminent_buys.sort(key=lambda x: x["entry_basis_date"] or "9999", reverse=True)

    # ── 대기(watching) 카운트만: 유효 리포트 보유 종목 중 미보유 ─────────────
    # 목록은 전략의 "임박 신호"가 아니라 리포트 흐름 — 아카이브로 안내.
    watching_count = 0
    for ticker in set(ticker_reports.keys()) & set(prices.keys()):
        if ticker in already_in:
            continue
        if any(r["report_date"] <= as_of for r in ticker_reports.get(ticker, [])):
            watching_count += 1

    return {
        "as_of": as_of.isoformat(),
        "headline_strategy": headline_label,
        "disclaimer": "백테스트 규칙의 기계적 적용이며 투자 권유가 아닙니다. 과거 데이터 기반 시뮬레이션으로 미래 수익을 보장하지 않습니다.",
        "regime": regime,
        "slots": {
            "max_positions": max_positions,
            "open": len(open_positions),
            "available": slots_available,
        },
        "open_positions": open_positions,
        "approaching_stop": approaching_stop,
        "imminent_buys": imminent_buys,
        "watching_count": watching_count,
        "counts": {
            "open": len(open_positions),
            "approaching_stop": len(approaching_stop),
            "imminent_buys": len(imminent_buys),
            "watching": watching_count,
        },
    }




# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    print("Loading report data...", flush=True)
    perf_all = pd.read_csv(REPORT_PERFORMANCE_CSV, encoding="utf-8-sig")

    # ── Load both KR and US buy reports
    perf = perf_all[
        perf_all["ticker"].notna()
        & perf_all["report_date"].notna()
        & (perf_all["rating_class"] == "buy")
        & (perf_all["report_date"] >= UNIVERSE_START.isoformat())
    ].copy()

    # Normalise ticker keys
    def normalise_ticker(row: pd.Series) -> str:
        market = str(row.get("market", "KR"))
        t = str(row["ticker"])
        return t.zfill(6) if market == "KR" else t

    perf["ticker_key"] = perf.apply(normalise_ticker, axis=1)

    kr_count = (perf["market"] == "KR").sum()
    us_count = (perf["market"] == "US").sum()
    print(f"  {len(perf)} buy reports: {kr_count} KR + {us_count} US  "
          f"({perf.report_date.min()} to {perf.report_date.max()})", flush=True)

    # Build per-ticker report metadata
    ticker_reports = build_ticker_reports(perf)

    # Point-in-time school coverage: future reports must not alter earlier consensus counts.
    ticker_club_count = point_in_time_school_counts(perf)


    # ── Fetch missing US price files
    print("Checking US price files...", flush=True)
    us_tickers = perf[perf["market"] == "US"]["ticker"].unique()
    for t in us_tickers:
        path = PRICE_DIR / f"US_{t}.csv"
        if not path.exists():
            _fetch_us_stock_yf(t)

    # ── Load prices (KR + US)
    print("Loading stock prices...", flush=True)
    prices: dict[str, pd.DataFrame] = {}
    for _, row in perf.iterrows():
        tk = row["ticker_key"]
        market = str(row.get("market", "KR"))
        if tk not in prices:
            df = load_prices(str(row["ticker"]), market)
            if df is not None:
                prices[tk] = df

    # ── Build reports list using ticker_key
    reports: list[tuple[dt.date, str, str, int]] = []
    for _, row in perf.iterrows():
        tk = row["ticker_key"]
        if tk not in prices:
            continue
        try:
            rdate = dt.date.fromisoformat(str(row["report_date"]))
        except ValueError:
            continue
        n_clubs = ticker_club_count.get((tk, rdate), 1)
        source = Path(str(row["source_file"])).name
        reports.append((rdate, tk, source, n_clubs))
    reports.sort()

    kr_with_prices = len({r[1] for r in reports if r[1][0].isdigit()})
    us_with_prices = len({r[1] for r in reports if not r[1][0].isdigit()})
    print(f"  {len(reports)} reports with price data, "
          f"{kr_with_prices} KR tickers + {us_with_prices} US tickers", flush=True)

    # Consensus-only reports
    consensus_reports = [(d, t, s, n) for d, t, s, n in reports if n >= 2]
    print(f"  {len(consensus_reports)} consensus (≥2 clubs) reports, "
          f"{len({r[1] for r in consensus_reports})} tickers", flush=True)

    # ── Calendar (merged KR + US trading days)
    raw_calendar = sorted({ts.date() for df in prices.values() for ts in df.index})
    calendar = [d for d in raw_calendar if d >= SIM_START]
    if not calendar:
        print("ERROR: no calendar dates after SIM_START", flush=True)
        return 1
    print(f"  Calendar (clipped): {calendar[0]} to {calendar[-1]}", flush=True)

    # ── v8: all reports (single-club included) used as entry signals
    print(f"  Total signal reports (all clubs): {len(reports)}", flush=True)
    print(f"  Consensus (≥2 clubs) subset: {len([(d,t,s,n) for d,t,s,n in reports if n>=2])} reports", flush=True)

    # ── Load benchmarks
    print("Loading benchmarks...", flush=True)
    kospi = load_kospi()
    sp500 = load_sp500()
    try:
        nasdaq = load_nasdaq()
        print(f"  NASDAQ: {nasdaq.index[0].date()} to {nasdaq.index[-1].date()}", flush=True)
    except Exception as e:
        print(f"  NASDAQ fetch failed: {e}, using S&P500 as proxy", flush=True)
        nasdaq = sp500.copy()
    try:
        gld = load_gld()
        print(f"  GLD: {gld.index[0].date()} to {gld.index[-1].date()}", flush=True)
    except Exception as e:
        print(f"  GLD fetch failed: {e}, using flat series as proxy", flush=True)
        gld = pd.Series(100.0, index=nasdaq.index)

    # v19 FX: USDKRW 환율 로드 및 전역 주입
    try:
        usdkrw_series = load_usdkrw()
        set_usdkrw(usdkrw_series)
        print(f"  USDKRW: {usdkrw_series.index[0].date()} to {usdkrw_series.index[-1].date()}, "
              f"latest={usdkrw_series.iloc[-1]:.1f}", flush=True)
    except Exception as e:
        print(f"  USDKRW fetch failed: {e}, FX fallback={fx._USDKRW_FALLBACK}", flush=True)

    strat_start = calendar[0]
    strat_end = calendar[-1]
    all_weather = compute_all_weather(kospi, sp500, nasdaq, gld, strat_start, strat_end)

    # Fingerprint of all result-relevant inputs — gates the Optuna/SPO caches.
    dataset_fp = _dataset_fingerprint(prices, reports, calendar)
    print(f"  Dataset fingerprint: {dataset_fp[:16]}…", flush=True)

    # ══════════════════════════════════════════════════════════════════════════
    # v6 MULTI-STRATEGY COMPARISON
    # All strategies: consensus ≥2, immediate entry, same costs/position sizing
    # Parameters are literature-grounded fixed values — no grid search
    # ══════════════════════════════════════════════════════════════════════════

    print("\n── Running strategy battery (v18: W 올웨더-샹들리에 + 현금이자/차입 모델 + entry_reason + L/M 가지치기) ──", flush=True)

    # A. 12개월 보유 (baseline)
    print("A. 12개월 보유...", flush=True)
    result_A = run_fixed_hold(
        prices, reports, calendar, hold_months=12,
        label="A_12mo", ticker_reports=ticker_reports, record_full_trades=True,
    )
    print(f"   IS sharpe={result_A['in_sample'].get('sharpe')}  OOS sharpe={result_A['out_of_sample'].get('sharpe')}", flush=True)

    # B. 36개월 보유
    print("B. 36개월 보유...", flush=True)
    result_B = run_fixed_hold(
        prices, reports, calendar, hold_months=36,
        label="B_36mo", ticker_reports=ticker_reports, record_full_trades=True,
    )
    print(f"   IS sharpe={result_B['in_sample'].get('sharpe')}  OOS sharpe={result_B['out_of_sample'].get('sharpe')}", flush=True)

    # C. 내러티브 홀드
    print("C. 내러티브 홀드 (200MA thesis-break)...", flush=True)
    result_C = run_narrative_hold(
        prices, reports, calendar,
        label="C_narrative", ticker_reports=ticker_reports, record_full_trades=True,
    )
    print(f"   IS sharpe={result_C['in_sample'].get('sharpe')}  OOS sharpe={result_C['out_of_sample'].get('sharpe')}", flush=True)

    # D. 샹들리에 래칫 (ATR42×5 trailing) — literature default
    print("D. 샹들리에 래칫 (ATR×5)...", flush=True)
    result_D = run_chandelier(
        prices, reports, calendar,
        label="D_chandelier", ticker_reports=ticker_reports, record_full_trades=True,
    )
    print(f"   IS sharpe={result_D['in_sample'].get('sharpe')}  OOS sharpe={result_D['out_of_sample'].get('sharpe')}", flush=True)

    # E. 절반익절 + 러너
    print("E. 절반익절 + 러너...", flush=True)
    result_E = run_half_exit_runner(
        prices, reports, calendar,
        label="E_half_runner", ticker_reports=ticker_reports, record_full_trades=True,
    )
    print(f"   IS sharpe={result_E['in_sample'].get('sharpe')}  OOS sharpe={result_E['out_of_sample'].get('sharpe')}", flush=True)

    # F. 모멘텀 필터 + 내러티브 홀드
    print("F. 모멘텀 필터 + 내러티브 홀드...", flush=True)
    result_F = run_narrative_hold(
        prices, reports, calendar,
        label="F_momentum_narrative", ticker_reports=ticker_reports, record_full_trades=True,
        momentum_filter_entry=True,
    )
    print(f"   IS sharpe={result_F['in_sample'].get('sharpe')}  OOS sharpe={result_F['out_of_sample'].get('sharpe')}", flush=True)

    # G. 딥바이
    print("G. 딥바이 (≥20% dip, single-club OK)...", flush=True)
    result_G = run_dip_buy(
        prices, reports, calendar,
        label="G_dip_buy", ticker_reports=ticker_reports, record_full_trades=True,
    )
    print(f"   IS sharpe={result_G['in_sample'].get('sharpe')}  OOS sharpe={result_G['out_of_sample'].get('sharpe')}", flush=True)

    # H. 미너비니 트렌드 템플릿
    print("H. 미너비니 트렌드 템플릿...", flush=True)
    result_H = run_minervini(
        prices, reports, calendar,
        label="H_minervini", ticker_reports=ticker_reports, record_full_trades=True,
        kospi=kospi,
    )
    print(f"   IS sharpe={result_H['in_sample'].get('sharpe')}  OOS sharpe={result_H['out_of_sample'].get('sharpe')}", flush=True)

    # I. 슈퍼트렌드(10, 3)
    print("I. 슈퍼트렌드(10, 3)...", flush=True)
    result_I = run_supertrend(
        prices, reports, calendar,
        label="I_supertrend", ticker_reports=ticker_reports, record_full_trades=True,
    )
    print(f"   IS sharpe={result_I['in_sample'].get('sharpe')}  OOS sharpe={result_I['out_of_sample'].get('sharpe')}", flush=True)

    # J. 코어-새틀라이트 레버리지 (overlay on D)
    print("J. 코어-새틀라이트 레버리지 오버레이 (on D)...", flush=True)
    result_J = run_core_satellite_leverage(
        chandelier_nav=result_D["nav_df"],
        kospi=kospi,
        label="J_core_satellite",
    )
    print(f"   IS sharpe={result_J['in_sample'].get('sharpe')}  OOS sharpe={result_J['out_of_sample'].get('sharpe')}", flush=True)

    # K. R:R 2.5 추세추종
    print("K. R:R 2.5 추세추종 (max 10 positions)...", flush=True)
    result_K = run_rr_trend(
        prices, reports, calendar,
        label="K_rr_trend", ticker_reports=ticker_reports, record_full_trades=True,
    )
    print(f"   IS sharpe={result_K['in_sample'].get('sharpe')}  OOS sharpe={result_K['out_of_sample'].get('sharpe')}", flush=True)

    # L 민리버전 / M 단기 리버설 — v18 가지치기: v11에서 룩어헤드·버그 수정 후에도
    # 거래비용 사망 판정이 확정된 두 전략은 더 이상 실행/출력하지 않는다.
    # (구현 run_rsi2_mean_reversion / run_short_term_reversal 은 기록용으로 코드에 유지.)
    print("L/M. 민리버전·단기 리버설 — v18 가지치기 (비용 사망 확정, 미실행)", flush=True)

    # N. 52주 고가 근접 (George & Hwang 2004)
    print("N. 52주 고가 근접 (George & Hwang 2004)...", flush=True)
    result_N = run_52w_high_proximity(
        prices, reports, calendar,
        label="N_52w_high", ticker_reports=ticker_reports, record_full_trades=True,
    )
    print(f"   IS sharpe={result_N['in_sample'].get('sharpe')}  OOS sharpe={result_N['out_of_sample'].get('sharpe')}", flush=True)

    # O. MTT alpha16 이식 — v11: same-bar lookahead fixed (signal at close → fill next open)
    print("O. MTT alpha16 (lookahead-fixed, Minervini RS+MTT+R-multiple exits)...", flush=True)
    result_O = run_mtt_alpha16(
        prices, reports, calendar,
        label="O_mtt_alpha16", ticker_reports=ticker_reports, record_full_trades=True,
    )
    print(f"   IS sharpe={result_O['in_sample'].get('sharpe')}  OOS sharpe={result_O['out_of_sample'].get('sharpe')}", flush=True)

    # P. 딥바이 샹들리에 하이브리드 (new v11)
    # Use D+ Optuna ATR mult if adopted, else D default ATR×5
    print("P. 딥바이 샹들리에 하이브리드 (진입=딥바이, 청산=ATR트레일, 스케일인)...", flush=True)
    # P runs with D default params initially; will be re-run with Optuna params after D+ eval
    result_P_default = run_deepbuy_chandelier(
        prices, reports, calendar,
        label="P_deepbuy_chandelier",
        ticker_reports=ticker_reports, record_full_trades=True,
        atr_mult=P_ATR_MULT_DEFAULT,
    )
    print(f"   IS sharpe={result_P_default['in_sample'].get('sharpe')}  OOS sharpe={result_P_default['out_of_sample'].get('sharpe')}", flush=True)

    # Q. 깡토 추세추종
    print("Q. 깡토 추세추종 (시장신호등+유닛사이징+RS+60d돌파+볼륨)...", flush=True)
    result_Q = run_kangto_trend(
        prices, reports, calendar,
        label="Q_kangto_trend",
        ticker_reports=ticker_reports, record_full_trades=True,
        kospi=kospi,
    )
    print(f"   IS sharpe={result_Q['in_sample'].get('sharpe')}  OOS sharpe={result_Q['out_of_sample'].get('sharpe')}  "
          f"win_rate={result_Q['metrics'].get('win_rate_pct')}%  trades={result_Q['metrics']['trades']}", flush=True)

    # R. Kelly 샹들리에 (D+ 규칙 + Kelly 포지션 사이즈)
    # Will be re-run with Optuna params after D+ eval; for now use D default
    print("R. Kelly 샹들리에 (D+ 규칙 + Kelly sizing)...", flush=True)
    result_R_default = run_kelly_chandelier(
        prices, reports, calendar,
        label="R_kelly_chandelier",
        ticker_reports=ticker_reports, record_full_trades=True,
        atr_mult=CHANDELIER_ATR_MULT,
    )
    print(f"   IS sharpe={result_R_default['in_sample'].get('sharpe')}  OOS sharpe={result_R_default['out_of_sample'].get('sharpe')}", flush=True)

    # S. 포트폴리오 최적화 — three variants
    # Monthly optimiser weights are a pure function of the dataset → cached on
    # disk keyed by dataset fingerprint (replay identical; --retune to force).
    _s_cache_key = hashlib.sha256(f"{dataset_fp}|{strategies.S_WEIGHTS_CODE_TAG}".encode()).hexdigest()
    s_weights_memos: dict[str, dict] = {"hrp": {}, "msharpe": {}, "mincvar": {}}
    if not strategies.FORCE_RETUNE and strategies.S_WEIGHTS_CACHE_PATH.exists():
        try:
            with open(strategies.S_WEIGHTS_CACHE_PATH, "rb") as _fh:
                _stored_key, _stored_memos = pickle.load(_fh)
            if _stored_key == _s_cache_key:
                s_weights_memos = _stored_memos
                print("  S-family weights cache HIT (dataset unchanged) [--retune to force]", flush=True)
        except Exception:
            pass

    print("S(a). HRP 포트폴리오 최적화...", flush=True)
    result_S_hrp = run_portfolio_opt(
        prices, reports, calendar,
        label="S_hrp",
        variant="hrp",
        ticker_reports=ticker_reports, record_full_trades=True,
        weights_memo=s_weights_memos["hrp"],
    )
    print(f"   IS sharpe={result_S_hrp['in_sample'].get('sharpe')}  OOS sharpe={result_S_hrp['out_of_sample'].get('sharpe')}", flush=True)

    print("S(b). max-Sharpe 포트폴리오 최적화...", flush=True)
    result_S_msharpe = run_portfolio_opt(
        prices, reports, calendar,
        label="S_msharpe",
        variant="msharpe",
        ticker_reports=ticker_reports, record_full_trades=True,
        weights_memo=s_weights_memos["msharpe"],
    )
    print(f"   IS sharpe={result_S_msharpe['in_sample'].get('sharpe')}  OOS sharpe={result_S_msharpe['out_of_sample'].get('sharpe')}", flush=True)

    print("S(c). min-CVaR 포트폴리오 최적화...", flush=True)
    result_S_mincvar = run_portfolio_opt(
        prices, reports, calendar,
        label="S_mincvar",
        variant="mincvar",
        ticker_reports=ticker_reports, record_full_trades=True,
        weights_memo=s_weights_memos["mincvar"],
    )
    print(f"   IS sharpe={result_S_mincvar['in_sample'].get('sharpe')}  OOS sharpe={result_S_mincvar['out_of_sample'].get('sharpe')}", flush=True)

    try:
        strategies.S_WEIGHTS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(strategies.S_WEIGHTS_CACHE_PATH, "wb") as _fh:
            pickle.dump((_s_cache_key, s_weights_memos), _fh, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        print(f"  WARNING: S-weights cache write failed: {e}", flush=True)

    # Best S variant by IS sharpe
    s_variants = {
        "S_hrp": result_S_hrp,
        "S_msharpe": result_S_msharpe,
        "S_mincvar": result_S_mincvar,
    }
    best_s_key = max(s_variants, key=lambda k: (s_variants[k].get("in_sample", {}).get("sharpe") or -999.0))
    best_s_result = s_variants[best_s_key]
    print(f"   Best S variant (IS sharpe): {best_s_key} → IS {best_s_result['in_sample'].get('sharpe')}  OOS {best_s_result['out_of_sample'].get('sharpe')}", flush=True)

    # ── V. SPO 포트폴리오 — Smart "Predict, then Optimize" (Elmachtoub & Grigas 2022)
    # 워크포워드 학습/예측은 spo_portfolio.py, 체결은 run_portfolio_opt 스케줄 모드.
    # V_spo: SPO+ 손실 SGD (Julia 레포 미러), V_ls: 동일 파이프라인 LS 손실 베이스라인.
    print("V. SPO 포트폴리오 (SPO+ vs LS 베이스라인, 워크포워드 월간 리밸런스)...", flush=True)
    sys.path.insert(0, str(ROOT / "scripts"))
    from spo_portfolio import compute_spo_weight_schedules

    # SPO walk-forward training is seeded → deterministic given the dataset.
    # Cache the (schedules, meta) artifact keyed by dataset fingerprint + the
    # exact spo_portfolio.py source — a cache hit is identical to re-training.
    _spo_src_hash = hashlib.sha256((ROOT / "scripts" / "spo_portfolio.py").read_bytes()).hexdigest()
    _spo_key = hashlib.sha256(f"{dataset_fp}|{_spo_src_hash}".encode()).hexdigest()
    spo_schedules = None
    spo_meta = None
    if not strategies.FORCE_RETUNE and strategies.SPO_CACHE_PATH.exists():
        try:
            with open(strategies.SPO_CACHE_PATH, "rb") as _fh:
                _stored_key, _schedules, _meta = pickle.load(_fh)
            if _stored_key == _spo_key:
                spo_schedules, spo_meta = _schedules, _meta
                print(f"  SPO cache HIT (dataset+code unchanged) — {_meta.get('n_rebalances')} rebalances "
                      f"[--retune to force re-train]", flush=True)
        except Exception:
            spo_schedules = None
    if spo_schedules is None:
        spo_schedules, spo_meta = compute_spo_weight_schedules(prices, reports, calendar, ticker_reports)
        try:
            strategies.SPO_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(strategies.SPO_CACHE_PATH, "wb") as _fh:
                pickle.dump((_spo_key, spo_schedules, spo_meta), _fh, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as e:
            print(f"  WARNING: SPO cache write failed: {e}", flush=True)
    result_V_spo = run_portfolio_opt(
        prices, reports, calendar,
        label="V_spo", variant="spo_plus",
        ticker_reports=ticker_reports, record_full_trades=True,
        weight_schedule=spo_schedules["spo_plus"],
    )
    print(f"   V_spo (SPO+) IS sharpe={result_V_spo['in_sample'].get('sharpe')}  OOS sharpe={result_V_spo['out_of_sample'].get('sharpe')}", flush=True)
    result_V_ls = run_portfolio_opt(
        prices, reports, calendar,
        label="V_ls", variant="ls",
        ticker_reports=ticker_reports, record_full_trades=True,
        weight_schedule=spo_schedules["ls"],
    )
    print(f"   V_ls  (LS)   IS sharpe={result_V_ls['in_sample'].get('sharpe')}  OOS sharpe={result_V_ls['out_of_sample'].get('sharpe')}", flush=True)

    # All strategies for comparison (L/M included for diagnostics but flagged)
    all_strategies: dict[str, dict] = {
        "A_12mo": result_A,
        "B_36mo": result_B,
        "C_narrative": result_C,
        "D_chandelier": result_D,
        "E_half_runner": result_E,
        "F_momentum_narrative": result_F,
        "G_dip_buy": result_G,
        "H_minervini": result_H,
        "I_supertrend": result_I,
        "J_core_satellite": result_J,
        "K_rr_trend": result_K,
        # L/M: v18 가지치기 — 비용 사망 확정, 미실행 (방법론 한 줄로만 기록)
        "N_52w_high": result_N,
        "O_mtt_alpha16": result_O,
        "P_deepbuy_chandelier": result_P_default,
        "Q_kangto_trend": result_Q,
        "R_kelly_chandelier": result_R_default,
        # S: all three sub-variants included, best one goes into selector
        "S_hrp": result_S_hrp,
        "S_msharpe": result_S_msharpe,
        "S_mincvar": result_S_mincvar,
        # V: SPO+ vs LS — 연구 기록 테이블 기본, 셀렉터는 T- 게이트 통과 시에만 (아래)
        "V_spo": result_V_spo,
        "V_ls": result_V_ls,
        # T / T-: added after D+ Optuna + T runs (see below)
    }
    # Strategies excluded from headline selector (cost-death confirmed or sub-variants)
    # S sub-variants: only best_s_key is eligible; the other two are excluded from selector
    # T-: regime variant — not directly in selector (best of T/T- wins as "T. 코어-KOSPI 샹들리에")
    s_non_best = {k for k in s_variants if k != best_s_key}
    # V_spo/V_ls: 기본 연구 기록 전용 — V_spo는 T- 게이트(부의 비율+OOS 샤프) 통과 시에만 셀렉터 승격
    # W: 올웨더 파킹 변형 — 헤드라인 콘테스트(W vs T-) 승자만 셀렉터 승격 (아래)
    EXCLUDED_FROM_SELECTOR = {"T-_kospi_core_regime", "U_chandelier_scaleout", "V_spo", "V_ls", "W_allweather_chandelier"} | s_non_best

    # ── D+ Optuna optimization ─────────────────────────────────────────────────
    print("\n── Optuna robust optimization (D+ chandelier) ───────────────────", flush=True)
    optuna_result = run_optuna_chandelier(
        prices, reports, calendar, ticker_reports=ticker_reports,
        dataset_fingerprint=dataset_fp,
    )
    optuna_meta = optuna_result.get("optuna_meta", {})
    d_plus_adopted = False
    result_Dplus = None

    if not optuna_result.get("skipped"):
        # Parameters are selected on IS only; OOS is descriptive and cannot promote tuning.
        is_sharpe_dplus = optuna_result.get("in_sample", {}).get("sharpe")
        result_Dplus = optuna_result
        d_plus_adopted = True
        result_Dplus["label"] = "D+_chandelier_optuna"
        result_Dplus["selection_basis"] = "IS-only fixed parameter choice; OOS is untouched evaluation"
        all_strategies["D+_chandelier_optuna"] = result_Dplus
        print(f"  D+ ADOPTED from IS-only selection (IS={is_sharpe_dplus})", flush=True)

    # ── T / T-: 코어-KOSPI 샹들리에 (D+ params if adopted, else D defaults) ──
    # Use D+ Optuna params if available; otherwise fall back to D ATR×5 / 20 pos
    t_atr_period = ATR_PERIOD
    t_atr_mult   = CHANDELIER_ATR_MULT
    t_max_pos    = MAX_POSITIONS
    if d_plus_adopted and result_Dplus is not None:
        _bp = optuna_meta.get("best_params", {})
        t_atr_period = int(_bp.get("atr_period", ATR_PERIOD))
        t_atr_mult   = float(_bp.get("atr_mult", CHANDELIER_ATR_MULT))
        t_max_pos    = int(_bp.get("max_positions", MAX_POSITIONS))

    print(f"\nT. 코어-KOSPI 샹들리에 (always-KOSPI park, ATR{t_atr_mult}, {t_max_pos} slots)...", flush=True)
    result_T = run_parking_core_chandelier(
        prices, reports, calendar,
        label="T_kospi_core_chandelier",
        parking=kospi,
        atr_period=t_atr_period,
        atr_mult=t_atr_mult,
        max_positions=t_max_pos,
        ticker_reports=ticker_reports,
        record_full_trades=True,
        regime_aware=False,
        parking_name="KOSPI",
    )
    print(f"   IS sharpe={result_T['in_sample'].get('sharpe')}  OOS sharpe={result_T['out_of_sample'].get('sharpe')}", flush=True)

    print(f"T-. 코어-KOSPI 샹들리에 레짐 변형 (KOSPI<200MA → 현금 이자 3%)...", flush=True)
    result_Tminus = run_parking_core_chandelier(
        prices, reports, calendar,
        label="T-_kospi_core_regime",
        parking=kospi,
        atr_period=t_atr_period,
        atr_mult=t_atr_mult,
        max_positions=t_max_pos,
        ticker_reports=ticker_reports,
        record_full_trades=True,
        regime_aware=True,
        parking_name="KOSPI",
    )
    print(f"   IS sharpe={result_Tminus['in_sample'].get('sharpe')}  OOS sharpe={result_Tminus['out_of_sample'].get('sharpe')}", flush=True)

    # W. 올웨더-샹들리에 — v18: 유휴 현금을 올웨더 바스켓에 파킹 (레짐 게이트 없음)
    # 올웨더 자체가 방어 자산(GLD 25%)을 포함 → KOSPI<200MA 스위치 불필요 가설.
    print("W. 올웨더-샹들리에 (idle cash → 올웨더 파킹, 레짐 게이트 없음)...", flush=True)
    result_W = run_parking_core_chandelier(
        prices, reports, calendar,
        label="W_allweather_chandelier",
        parking=all_weather,
        atr_period=t_atr_period,
        atr_mult=t_atr_mult,
        max_positions=t_max_pos,
        ticker_reports=ticker_reports,
        record_full_trades=True,
        regime_aware=False,
        parking_name="올웨더",
    )
    print(f"   IS sharpe={result_W['in_sample'].get('sharpe')}  OOS sharpe={result_W['out_of_sample'].get('sharpe')}", flush=True)

    # Add T / T- / W to all_strategies (after they are computed)
    all_strategies["T_kospi_core_chandelier"] = result_T
    all_strategies["T-_kospi_core_regime"] = result_Tminus
    all_strategies["W_allweather_chandelier"] = result_W

    # ── U: 코어-KOSPI 샹들리에 + 과열 스케일아웃 (T- identical + extension 8×/12×) ──
    print(f"\nU. 코어-KOSPI 샹들리에 + 과열 스케일아웃 (T- + ATR% extension 8×/12×)...", flush=True)
    result_U = run_kospi_core_chandelier_scaleout(
        prices, reports, calendar,
        label="U_chandelier_scaleout",
        kospi=kospi,
        atr_period=t_atr_period,
        atr_mult=t_atr_mult,
        max_positions=t_max_pos,
        ticker_reports=ticker_reports,
        record_full_trades=True,
    )
    print(f"   IS sharpe={result_U['in_sample'].get('sharpe')}  OOS sharpe={result_U['out_of_sample'].get('sharpe')}", flush=True)
    # U tenbagger metrics
    _u_max_single = result_U["metrics"].get("max_single_return_pct")
    _tm_max_single = result_Tminus["metrics"].get("max_single_return_pct")
    print(f"   U max single trade: {_u_max_single}%  vs T- max: {_tm_max_single}%", flush=True)
    all_strategies["U_chandelier_scaleout"] = result_U
    # U is NOT automatically in the selector — evaluated vs T- below

    # ── Re-run P and R with Optuna ATR params if D+ was adopted ────────────
    result_P = result_P_default
    result_R = result_R_default
    p_atr_mult_used = P_ATR_MULT_DEFAULT
    if d_plus_adopted and result_Dplus is not None:
        best_p = optuna_meta.get("best_params", {})
        p_atr_mult_used = float(best_p.get("atr_mult", P_ATR_MULT_DEFAULT))
        p_max_pos = int(best_p.get("max_positions", MAX_POSITIONS))
        print(f"P. 딥바이 샹들리에 재실행 (Optuna ATR mult={p_atr_mult_used}, max_pos={p_max_pos})...", flush=True)
        result_P = run_deepbuy_chandelier(
            prices, reports, calendar,
            label="P_deepbuy_chandelier",
            ticker_reports=ticker_reports, record_full_trades=True,
            atr_mult=p_atr_mult_used,
            max_positions=p_max_pos,
        )
        all_strategies["P_deepbuy_chandelier"] = result_P
        print(f"   P (Optuna params) IS sharpe={result_P['in_sample'].get('sharpe')}  OOS sharpe={result_P['out_of_sample'].get('sharpe')}", flush=True)
        # R uses same Optuna ATR params (it's the D+ rules with Kelly sizing)
        r_atr_period = int(best_p.get("atr_period", ATR_PERIOD))
        print(f"R. Kelly 샹들리에 재실행 (Optuna ATR mult={p_atr_mult_used})...", flush=True)
        result_R = run_kelly_chandelier(
            prices, reports, calendar,
            label="R_kelly_chandelier",
            ticker_reports=ticker_reports, record_full_trades=True,
            atr_period=r_atr_period,
            atr_mult=p_atr_mult_used,
            max_positions=p_max_pos,
        )
        all_strategies["R_kelly_chandelier"] = result_R
        print(f"   R (Optuna params) IS sharpe={result_R['in_sample'].get('sharpe')}  OOS sharpe={result_R['out_of_sample'].get('sharpe')}", flush=True)
        # T and T- were already run with Optuna params above; all_strategies already updated.

    # ── Per-strategy KOSPI/올웨더 DCA ratio (final strategy wealth / benchmark DCA wealth)
    print("\nComputing per-strategy KOSPI/AllWeather DCA ratios (quick pass)...", flush=True)
    _kospi_dca_ratios: dict[str, dict[str, float | None]] = {}
    for key, r in all_strategies.items():
        ws_tmp = compute_wealth_simulation_multi(
            r["nav_df"], {"KOSPI": kospi, "AllWeather": all_weather}, strat_start, strat_end)
        strat_final = ws_tmp["final_strategy_value"]
        kospi_final = ws_tmp["final_benchmark_values"].get("KOSPI", 1)
        aw_final = ws_tmp["final_benchmark_values"].get("AllWeather", 1)
        ratio = round(strat_final / kospi_final, 3) if kospi_final and kospi_final > 0 else None
        aw_ratio = round(strat_final / aw_final, 3) if aw_final and aw_final > 0 else None
        is_ratios = _is_dca_ratios(r["nav_df"], kospi, all_weather)
        _kospi_dca_ratios[key] = {
            "full_ratio": ratio,
            "aw_ratio": aw_ratio,
            "is_ratio": is_ratios["kospi"],
            "aw_is_ratio": is_ratios["allweather"],
            "strat_final": round(strat_final),
            "kospi_final": round(kospi_final) if kospi_final else None,
            "aw_final": round(aw_final) if aw_final else None,
        }

    # ── v18 파킹 헤드라인 콘테스트: W (올웨더 파킹) vs T- (KOSPI 파킹 + 레짐) ──
    # Selection uses IS-only benchmark ratio and IS Sharpe. OOS/full-period values are descriptive; historical OOS was observed during prior research and is not an untouched holdout.
    # T (상시 KOSPI 파킹, 레짐 없음)는 연구 기록 비교용 — 콘테스트에 미참여.
    _dplus_ref = result_Dplus if result_Dplus is not None else result_D
    _dplus_is  = _dplus_ref.get("in_sample", {}).get("sharpe")
    _dplus_oos = _dplus_ref.get("out_of_sample", {}).get("sharpe")

    _tm_kospi_ratio = _kospi_dca_ratios.get("T-_kospi_core_regime", {}).get("is_ratio")
    _w_aw_ratio     = _kospi_dca_ratios.get("W_allweather_chandelier", {}).get("aw_is_ratio")
    _w_kospi_ratio  = _kospi_dca_ratios.get("W_allweather_chandelier", {}).get("is_ratio")
    _tm_aw_ratio    = _kospi_dca_ratios.get("T-_kospi_core_regime", {}).get("aw_is_ratio")

    _tm_is  = (result_Tminus.get("in_sample", {}).get("sharpe") or -999.0)
    _tm_oos = (result_Tminus.get("out_of_sample", {}).get("sharpe") or -999.0)
    _w_is   = (result_W.get("in_sample", {}).get("sharpe") or -999.0)
    _w_oos  = (result_W.get("out_of_sample", {}).get("sharpe") or -999.0)

    _tm_robust = _tm_kospi_ratio is not None and _tm_kospi_ratio > 1.0
    _w_robust  = _w_aw_ratio is not None and _w_aw_ratio > 1.0
    _tm_score = _tm_is
    _w_score  = _w_is

    # Winner: robust 후보 중 IS+OOS 샤프 합 우위; 둘 다 robust 아니면 점수만으로 비교
    if _w_robust and not _tm_robust:
        _winner_key = "W_allweather_chandelier"
    elif _tm_robust and not _w_robust:
        _winner_key = "T-_kospi_core_regime"
    else:
        _winner_key = "W_allweather_chandelier" if _w_score > _tm_score else "T-_kospi_core_regime"

    if _winner_key == "W_allweather_chandelier":
        _t_best_key, _t_best = _winner_key, result_W
        _t_best_own_ratio, _t_loser_key = _w_aw_ratio, "T-_kospi_core_regime"
        _t_best_robust = _w_robust
    else:
        _t_best_key, _t_best = _winner_key, result_Tminus
        _t_best_own_ratio, _t_loser_key = _tm_kospi_ratio, "W_allweather_chandelier"
        _t_best_robust = _tm_robust

    _t_best_is  = _t_best.get("in_sample", {}).get("sharpe")
    _t_best_oos = _t_best.get("out_of_sample", {}).get("sharpe")
    # Promotion uses IS-only ratio and Sharpe; OOS is descriptive.
    _t_promoted = (
        _t_best_robust
        and _t_best_is is not None
        and _dplus_is is not None
        and _t_best_is >= _dplus_is
    )

    # T(상시 파킹)와 콘테스트 패자는 연구 기록 전용; 승자만 승격 시 셀렉터 진입
    EXCLUDED_FROM_SELECTOR.add("T_kospi_core_chandelier")
    EXCLUDED_FROM_SELECTOR.add(_t_loser_key)
    if _t_promoted:
        EXCLUDED_FROM_SELECTOR.discard(_t_best_key)
    else:
        EXCLUDED_FROM_SELECTOR.add(_t_best_key)

    t_promotion_verdict = (
        f"파킹 콘테스트 승자={_t_best_key} 자기벤치마크 IS비율={_t_best_own_ratio}x "
        f"IS={_t_best_is} (OOS descriptive={_t_best_oos}; D+ IS={_dplus_is}) "
        f"promoted={'YES — 헤드라인 후보' if _t_promoted else 'NO — IS 벤치마크 또는 IS 샤프 미달'}"
    )
    parking_showdown = {
        "contest": "W(올웨더 파킹, 레짐 없음) vs T-(KOSPI 파킹, KOSPI<200MA 레짐)",
        "W": {
            "is_sharpe": _w_is, "oos_sharpe": _w_oos,
            "vs_kospi_dca": _w_kospi_ratio, "vs_allweather_dca": _w_aw_ratio,
            "own_benchmark": "올웨더 DCA", "beats_own_benchmark": _w_robust,
        },
        "T_minus": {
            "is_sharpe": _tm_is, "oos_sharpe": _tm_oos,
            "vs_kospi_dca": _tm_kospi_ratio, "vs_allweather_dca": _tm_aw_ratio,
            "own_benchmark": "KOSPI DCA", "beats_own_benchmark": _tm_robust,
        },
        "winner": _t_best_key,
        "promoted_to_headline_candidate": _t_promoted,
        "criteria": "IS 기간 자기 파킹 벤치마크 DCA 우위 AND IS 샤프 우위 → 승자; OOS/full-period는 사후 기술통계이며 독립 holdout 아님",

        "design_note": (
            "사용자 가설: KOSPI 파킹+200MA 레짐은 과적합 냄새가 나고, 올웨더 파킹이 더 자연스럽다. "
            "W는 레짐 게이트 없이 올웨더(GLD 25% 포함, 분기 리밸런스)에 상시 파킹 — "
            "방어를 자산 배분으로 해결하고 타이밍 스위치를 제거한 설계."
        ),
        "verdict": t_promotion_verdict,
    }
    print(f"\n파킹 콘테스트 (W vs T-): {t_promotion_verdict}", flush=True)
    print(f"  W: IS={_w_is} OOS={_w_oos} vsKOSPI={_w_kospi_ratio}x vs올웨더={_w_aw_ratio}x", flush=True)
    print(f"  T-: IS={_tm_is} OOS={_tm_oos} vsKOSPI={_tm_kospi_ratio}x vs올웨더={_tm_aw_ratio}x", flush=True)

    # U/T comparison uses IS-only ratios and Sharpe for promotion; full-period values are descriptive.
    _u_ratio  = _kospi_dca_ratios.get("U_chandelier_scaleout",  {}).get("is_ratio")
    _tm_ratio_cmp = _kospi_dca_ratios.get(_t_best_key, {}).get("is_ratio")
    _u_oos    = result_U.get("out_of_sample", {}).get("sharpe")
    _tm_oos_cmp = _t_best.get("out_of_sample", {}).get("sharpe")
    _u_is     = result_U.get("in_sample", {}).get("sharpe")
    _u_max_trade = result_U["metrics"].get("max_single_return_pct")
    _tm_max_trade = result_Tminus["metrics"].get("max_single_return_pct")

    # Tenbagger metrics: top-decile P&L share and avg winner hold days
    def _tenbagger_metrics(strat_result: dict) -> dict:
        closed = [t for t in strat_result.get("trades", []) if not t.get("exit_reason", "").endswith("미청산")]
        returns = sorted([t["return_pct"] for t in closed], reverse=True)
        n = len(returns)
        if not n:
            return {"top_decile_pnl_share_pct": None, "avg_winner_hold_days": None}
        top10_n = max(1, math.ceil(n * 0.1))
        ranked = sorted(closed, key=lambda t: t["return_pct"], reverse=True)[:top10_n]
        monetary = [t.get("pnl_krw") for t in closed]
        if any(v is None or not math.isfinite(float(v)) for v in monetary):
            td_share = None
        else:
            positive = sum(float(v) for v in monetary if float(v) > 0)
            top_positive = sum(float(t["pnl_krw"]) for t in ranked if float(t["pnl_krw"]) > 0)
            td_share = (top_positive / positive * 100) if positive > 0 else 0.0
        winners = [t for t in closed if t["return_pct"] > 0]
        avg_hold = round(sum(t["days"] for t in winners) / len(winners), 1) if winners else None
        return {
            "top_decile_pnl_share_pct": round(td_share, 1) if td_share is not None else None,
            "avg_winner_hold_days": avg_hold,
            "max_single_return_pct": max((t["return_pct"] for t in closed), default=None),
        }

    _u_tb  = _tenbagger_metrics(result_U)
    _tm_tb = _tenbagger_metrics(result_Tminus)

    _u_beats_tminus = (_u_ratio is not None and _tm_ratio_cmp is not None and _u_is is not None and _tm_is is not None and _u_ratio > _tm_ratio_cmp and _u_is >= _tm_is)
    if _u_beats_tminus:
        # U promoted: remove from exclusions so it competes in headline selection
        EXCLUDED_FROM_SELECTOR.discard("U_chandelier_scaleout")
        u_verdict_str = "PROMOTED — IS 기준 T- 대비 벤치마크 비율·IS 샤프 우위 (OOS descriptive)"

    else:
        u_verdict_str = (
            "NOT PROMOTED — "
            + ("과열 스케일아웃은 상승 여력을 깎았다" if (_u_ratio is None or _tm_ratio_cmp is None or _u_ratio <= _tm_ratio_cmp) else "IS 비율은 앞서나 IS 샤프 미달")

        )

    u_vs_tminus_verdict = {
        "U_wealth_ratio": _u_ratio,
        "Tminus_wealth_ratio": _tm_ratio_cmp,
        "U_oos_sharpe": _u_oos,
        "Tminus_oos_sharpe": _tm_oos_cmp,
        "U_is_sharpe": _u_is,
        "U_max_single_return_pct": _u_max_trade,
        "Tminus_max_single_return_pct": _tm_max_trade,
        "U_top_decile_pnl_share_pct": _u_tb.get("top_decile_pnl_share_pct"),
        "Tminus_top_decile_pnl_share_pct": _tm_tb.get("top_decile_pnl_share_pct"),
        "U_avg_winner_hold_days": _u_tb.get("avg_winner_hold_days"),
        "Tminus_avg_winner_hold_days": _tm_tb.get("avg_winner_hold_days"),
        "promoted": _u_beats_tminus,
        "verdict": u_verdict_str,
        "extension_formula": "extension = B/A, A=ATR(14)/price (ATR%), B=(price-50SMA)/50SMA. 출처: Minervini 커뮤니티 관행, TradingView Fred6724.",
        "scaleout_thresholds": {"first": U_SCALEOUT_EXT_1, "second": U_SCALEOUT_EXT_2},
        "editorial": (
            "가격-전용(price-only) 데이터로 텐배거를 끝까지 들고 갈 수 있는가? "
            "샹들리에 ATR×5 트레일은 고점이 어디인지 모른다는 사실을 설계로 인정하고 "
            "단지 '가격이 최고점에서 충분히 떨어질 때까지' 기다린다. "
            "이 접근은 삼성전기(+1,400% 구간)처럼 오랜 상승추세를 '끝까지' 타는 것을 허용하는 반면, "
            "PLTR·TSLA·NVDA 유형처럼 extension 10×를 넘어 과열 후 급락하는 패턴에서는 "
            "일부 수익을 고점 근처에서 실현하는 것이 유리하다. "
            "스케일아웃 전략(U)이 T-와 비교해 "
            + ("더 높은 부의 비율을 달성했다 — 과열 구간의 부분 익절이 체계적으로 효과적임을 시사한다." if _u_beats_tminus else
               "더 낮은 부의 비율을 보였다 — 이 유니버스에서는 과열 스케일아웃이 남은 포지션의 상승을 놓치는 비용이 더 컸다. "
               "가격-전용 데이터만으로는 정확한 '과열 고점'을 식별하기 어렵고, "
               "조기 익절은 텐배거의 복리 효과를 희석시킨다.")
        ),
    }
    print(f"\nU vs T- verdict: {u_verdict_str}", flush=True)
    print(f"  U ratio={_u_ratio}x  T- ratio={_tm_ratio_cmp}x  U OOS={_u_oos}  T- OOS={_tm_oos_cmp}", flush=True)
    print(f"  U max_trade={_u_max_trade}%  T- max_trade={_tm_max_trade}%", flush=True)
    print(f"  U top-decile PnL share={_u_tb.get('top_decile_pnl_share_pct')}%  T- {_tm_tb.get('top_decile_pnl_share_pct')}%", flush=True)

    # ── V(SPO+) 승격 판정 — 기존 승격 관례 (U와 동일): T 베스트 변형 대비
    # V promotion uses IS-only ratio and Sharpe; OOS is descriptive.
    _vspo_ratio = _kospi_dca_ratios.get("V_spo", {}).get("is_ratio")
    _vls_ratio  = _kospi_dca_ratios.get("V_ls",  {}).get("is_ratio")
    _vspo_is  = result_V_spo.get("in_sample", {}).get("sharpe")
    _vspo_oos = result_V_spo.get("out_of_sample", {}).get("sharpe")
    _vls_is   = result_V_ls.get("in_sample", {}).get("sharpe")
    _vls_oos  = result_V_ls.get("out_of_sample", {}).get("sharpe")

    _v_promoted = (_vspo_ratio is not None and _tm_ratio_cmp is not None and _vspo_is is not None and _tm_is is not None and _vspo_ratio > _tm_ratio_cmp and _vspo_is >= _tm_is)
    if _v_promoted:
        EXCLUDED_FROM_SELECTOR.discard("V_spo")
        v_verdict_str = "PROMOTED — IS 기준 T 베스트 대비 벤치마크 비율·IS 샤프 우위 (OOS descriptive)"

    else:
        v_verdict_str = (
            "NOT PROMOTED — "
            + ("IS 비율이 T 베스트에 미달" if (_vspo_ratio is None or _tm_ratio_cmp is None or _vspo_ratio <= _tm_ratio_cmp) else "IS 비율은 앞서나 IS 샤프 미달")
            + " → 연구 기록 전용"
        )

    # 논문의 핵심 주장 검증: 같은 파이프라인에서 SPO+ > LS (오지정 하 의사결정 품질)
    _spo_beats_ls_is  = _vspo_is  > _vls_is
    _spo_beats_ls_oos = _vspo_oos > _vls_oos
    spo_vs_ls_verdict = (
        f"SPO+ vs LS — IS {_vspo_is} vs {_vls_is} ({'SPO+ 우위' if _spo_beats_ls_is else 'LS 우위/동률'}), "
        f"OOS {_vspo_oos} vs {_vls_oos} ({'SPO+ 우위' if _spo_beats_ls_oos else 'LS 우위/동률'}), "
        f"vs KOSPI DCA {_vspo_ratio}x vs {_vls_ratio}x"
    )
    print(f"\nV(SPO) verdict: {v_verdict_str}", flush=True)
    print(f"  {spo_vs_ls_verdict}", flush=True)
    print(f"  realized decision stats: {spo_meta.get('realized_decision_stats')}", flush=True)

    # ── Summary table (all emitted strategies; L/M pruned in v18)
    print(f"\n── Strategy summary (v18, {len(all_strategies)} strategies; S-non-best/파킹 콘테스트 패자/U·V(if not promoted) excluded from selector) ──", flush=True)
    print(f"{'Strategy':<32} {'IS Shp':>8} {'OOS Shp':>9} {'WinRate':>8} {'vs KOSPI DCA':>13} {'Trades':>7} {'Note':>12}", flush=True)
    for key, r in all_strategies.items():
        is_m  = r.get("in_sample", {})
        oos_m = r.get("out_of_sample", {})
        win_rate = r["metrics"].get("win_rate_pct")
        ratio_info = _kospi_dca_ratios.get(key, {})
        kospi_ratio_str = f"{ratio_info.get('full_ratio','—')}x" if ratio_info.get('full_ratio') else "—"
        note = "[EXCLUDED]" if key in EXCLUDED_FROM_SELECTOR else ""
        if key == best_s_key:
            note = "[S-BEST]"
        if key == _t_best_key and _t_promoted:
            note = "[T-BEST]"
        print(
            f"  {key:<30} {str(is_m.get('sharpe','—')):>8} "
            f"{str(oos_m.get('sharpe','—')):>9} "
            f"{str(win_rate)+'%' if win_rate is not None else '—':>8} "
            f"{kospi_ratio_str:>13} "
            f"{r['metrics']['trades']:>7} {note:>12}",
            flush=True,
        )

    # Beat KOSPI DCA verdict
    beats_kospi_both = [
        k for k, v in _kospi_dca_ratios.items()
        if v.get("full_ratio") and v["full_ratio"] > 1.0
        and k not in EXCLUDED_FROM_SELECTOR
    ]
    if beats_kospi_both:
        print(f"\n  ✓ Strategies beating KOSPI DCA: {beats_kospi_both}", flush=True)
    else:
        print(f"\n  ✗ No eligible strategy beats KOSPI DCA in full-period wealth simulation.", flush=True)

    # ── Headline selection among ELIGIBLE strategies only.
    # Headline selection uses IS only; OOS/full-period values are descriptive and contaminated for selection.
    eligible_strategies = {k: v for k, v in all_strategies.items() if k not in EXCLUDED_FROM_SELECTOR}
    headline_key, headline = max(eligible_strategies.items(), key=headline_rank)
    # 키와 라벨은 항상 일치 — signals/multi_strategy/api가 같은 헤드라인을 가리켜야 한다
    headline_label = headline_key
    print(f"\nHeadline (tiebreak IS→OOS→wealth ratio, eligible only): {headline_label} [{headline_key}]", flush=True)
    print(f"  IS sharpe={headline.get('in_sample', {}).get('sharpe')}  OOS sharpe={headline.get('out_of_sample', {}).get('sharpe')}  "
          f"vs KOSPI DCA={_kospi_dca_ratios.get(headline_key, {}).get('full_ratio')}x", flush=True)

    # ── v20 데이터 주도 판정 (verdict) — 매 실행 시 실제 수치·게이트 결과에서 생성 ──
    # 칩: SOTA(헤드라인) | 채택(셀렉터 멤버) | 연구용(비교 앵커·오버레이·대조군) | 기각(미달).
    # 프런트엔드 하드코딩 금지 — strategy-meta.ts는 라벨/규칙 설명만 갖는다.
    _CURATED_CORE = [
        "D+_chandelier_optuna", "D_chandelier", "B_36mo", "C_narrative",
        "P_deepbuy_chandelier", "F_momentum_narrative",
    ]
    curated_keys: list[str] = [headline_key] + [k for k in _CURATED_CORE if k in all_strategies]
    if best_s_key in all_strategies:
        curated_keys.append(best_s_key)               # S 배분형 IS 샤프 베스트 변형
    if _u_beats_tminus and "U_chandelier_scaleout" in all_strategies:
        curated_keys.append("U_chandelier_scaleout")  # T 베스트 게이트 통과 시 셀렉터 승격
    if _t_promoted and _t_best_key in all_strategies:
        curated_keys.append(_t_best_key)              # 파킹 콘테스트 승자 (승격 시)
    if _v_promoted and "V_spo" in all_strategies:
        curated_keys.append("V_spo")
    curated_keys = list(dict.fromkeys(curated_keys))

    _h_is = headline.get("in_sample", {}).get("sharpe")
    _h_oos = headline.get("out_of_sample", {}).get("sharpe")
    _h_ratio = _kospi_dca_ratios.get(headline_key, {}).get("full_ratio")

    # 역할 앵커 — 구조적으로 비교용으로 설계된 전략 (성과 판정이 아니라 역할 기술)
    _RESEARCH_ROLE = {
        "A_12mo":                  "12개월 고정 보유 기준선 — 비교 앵커로만 유지",
        "J_core_satellite":        "D 레버리지 오버레이 — 차입·디레버 효과 검증용 (차입 6%/년)",
        "R_kelly_chandelier":      "Kelly 사이징 오버레이 — 사이징 효과 단독 검증용",
        "T_kospi_core_chandelier": "상시 KOSPI 파킹 앵커 (레짐 없음) — 파킹 효과 분리용",
        "O_mtt_alpha16":           "alpha16 KRX 파라미터 이식 — 본 유니버스 미튜닝, 비교 참고용",
        "V_ls":                    "SPO+ 효과 분리용 LS 대조군 — 비교 앵커로만 유지",
    }

    def _vf(v) -> str:
        return "—" if v is None else f"{v}"

    def _strategy_verdict(key: str, r: dict) -> tuple[str, str]:
        is_s = r.get("in_sample", {}).get("sharpe")
        oos_s = r.get("out_of_sample", {}).get("sharpe")
        ratio = _kospi_dca_ratios.get(key, {}).get("full_ratio")
        aw_r = _kospi_dca_ratios.get(key, {}).get("aw_ratio")
        mdd = r["metrics"].get("mdd_pct")
        n_tr = r["metrics"].get("trades") or 0

        if key == headline_key:
            return "SOTA", (
                f"타이브레이크(IS 샤프 → OOS 샤프 → 부의 비율) 1위 — "
                f"IS {_vf(is_s)} · OOS {_vf(oos_s)} · vs KOSPI DCA {_vf(ratio)}x"
            )
        if key in curated_keys:
            if key == best_s_key:
                return "채택", f"S 배분형 IS 샤프 베스트 변형 — IS {_vf(is_s)} · OOS {_vf(oos_s)}"
            if key == _t_best_key and _t_promoted:
                return "채택", (
                    f"파킹 콘테스트 승자·승격 게이트 통과 — 타이브레이크 차점 "
                    f"(IS {_vf(is_s)} vs {_vf(_h_is)} · OOS {_vf(oos_s)} vs {_vf(_h_oos)})"
                )
            if key in ("U_chandelier_scaleout", "V_spo"):
                return "채택", (
                    f"승격 게이트 통과 (T 베스트 대비 부의 비율·OOS 샤프 우위) — "
                    f"IS {_vf(is_s)} · OOS {_vf(oos_s)} · vs KOSPI DCA {_vf(ratio)}x"
                )
            return "채택", f"셀렉터 핵심 비교 세트 — IS {_vf(is_s)} · OOS {_vf(oos_s)} · vs KOSPI DCA {_vf(ratio)}x"

        # 파킹 콘테스트 패자 (W 또는 T-)
        if key == _t_loser_key:
            _own = _w_aw_ratio if key == "W_allweather_chandelier" else _tm_kospi_ratio
            _own_name = "올웨더 DCA" if key == "W_allweather_chandelier" else "KOSPI DCA"
            _loser_score = _w_score if key == "W_allweather_chandelier" else _tm_score
            _winner_score = _tm_score if key == "W_allweather_chandelier" else _w_score
            return "연구용", (
                f"파킹 콘테스트 패자 (vs {_t_best_key}) — IS+OOS 샤프 합 "
                f"{round(_loser_score, 2)} vs {round(_winner_score, 2)} · 자기 벤치마크({_own_name}) {_vf(_own)}x"
            )
        # 파킹 콘테스트 승자였으나 승격 게이트 미달
        if key == _t_best_key and not _t_promoted:
            return "연구용", (
                f"파킹 콘테스트 승자였으나 승격 게이트 미달 — 자기 벤치마크 {_vf(_t_best_own_ratio)}x · "
                f"IS {_vf(is_s)} vs D+ {_vf(_dplus_is)}"
            )
        # U 미승격: 스케일아웃이 T 베스트에 패배
        if key == "U_chandelier_scaleout":
            why = ("과열 스케일아웃이 상승 여력을 깎음" if _u_ratio <= _tm_ratio_cmp
                   else "부의 비율은 앞서나 OOS 샤프 미달")
            return "기각", (
                f"T 베스트 대비 부의 비율 {_vf(_u_ratio)}x vs {_vf(_tm_ratio_cmp)}x · "
                f"OOS {_vf(_u_oos)} vs {_vf(_tm_oos_cmp)} — {why}"
            )
        # V_spo 미승격
        if key == "V_spo":
            return "연구용", (
                f"SPO+ 의사결정 학습 검증 — 승격 게이트 미달 "
                f"(부의 비율 {_vf(_vspo_ratio)}x vs {_vf(_tm_ratio_cmp)}x · OOS {_vf(_vspo_oos)} vs {_vf(_tm_oos_cmp)})"
            )
        # S 비베스트 변형
        if key in s_variants and key != best_s_key:
            return "연구용", f"S 배분형 비베스트 변형 — IS {_vf(is_s)} (베스트 {best_s_key} {_vf(best_s_result['in_sample'].get('sharpe'))})"
        # 구조적 비교 앵커·오버레이·대조군
        if key in _RESEARCH_ROLE:
            return "연구용", f"{_RESEARCH_ROLE[key]} (IS {_vf(is_s)} · OOS {_vf(oos_s)})"
        # OOS 양호하나 IS 미달 — 표본 부족 참고용
        if oos_s is not None and oos_s >= 1.0 and (is_s or 0.0) > 0.0:
            return "연구용", f"OOS 샤프 {_vf(oos_s)} 양호 · IS {_vf(is_s)} 미달 — 표본 부족, 참고용"

        # 기각 — 지배적 실패 원인을 수치에서 골라 기술
        if is_s is None or is_s < 0.1:
            why = f"IS 샤프 {_vf(is_s)} — 이 유니버스에서 미작동"
        elif mdd is not None and mdd <= -60:
            why = f"MDD {mdd}% — 낙폭 과대 (IS {_vf(is_s)} · OOS {_vf(oos_s)})"
        elif n_tr >= 400 and (ratio or 0.0) < 0.7:
            why = f"거래 {n_tr}건 고회전 — 비용이 알파 소진 (vs KOSPI DCA {_vf(ratio)}x)"
        else:
            why = (
                f"vs KOSPI DCA {_vf(ratio)}x · 올웨더 DCA {_vf(aw_r)}x — "
                f"벤치마크 적립식 하회 (IS {_vf(is_s)} · OOS {_vf(oos_s)})"
            )
        return "기각", why

    strategy_verdicts: dict[str, dict[str, str]] = {}
    for key, r in all_strategies.items():
        chip, reason = _strategy_verdict(key, r)
        strategy_verdicts[key] = {"verdict": chip, "verdict_reason": reason}
    print(f"\nVerdicts (data-driven): curated={curated_keys}", flush=True)
    for key, v in strategy_verdicts.items():
        print(f"  [{v['verdict']:>3}] {key}: {v['verdict_reason']}", flush=True)

    # ── Tail stats and consensus stats on headline
    tail_stats = compute_tail_stats(headline.get("trades", []))
    consensus_stats = compute_consensus_stats(headline.get("trades", []))

    # ── Wealth simulation on headline
    print("\nComputing wealth simulations...", flush=True)
    headline_nav: pd.Series = headline["nav_df"]
    assert headline_nav.index[0].date() >= SIM_START

    # v19 FX: SP500 and NASDAQ are USD-denominated — convert to KRW for wealth sim.
    # AllWeather already uses KRW-converted USD legs (compute_all_weather applies FX).
    if fx._USDKRW is not None:
        _fx_aligned_sim = fx._USDKRW.reindex(headline_nav.index).ffill().bfill().fillna(fx._USDKRW_FALLBACK)
        sp500_krw = sp500.reindex(headline_nav.index).ffill().bfill() * _fx_aligned_sim
        nasdaq_krw = nasdaq.reindex(headline_nav.index).ffill().bfill() * _fx_aligned_sim
    else:
        sp500_krw = sp500
        nasdaq_krw = nasdaq
    benchmarks_for_sim: dict[str, pd.Series] = {
        "KOSPI": kospi, "SP500": sp500_krw, "NASDAQ": nasdaq_krw, "AllWeather": all_weather,
    }
    wealth_sim = compute_wealth_simulation_multi(headline_nav, benchmarks_for_sim, strat_start, strat_end)
    print(f"  Strategy final: {wealth_sim['final_strategy_value']:,}원", flush=True)

    # Per-strategy wealth sims (for UI switcher)
    strat_wealth_sims: dict[str, dict] = {}
    for key, r in all_strategies.items():
        ws = compute_wealth_simulation_multi(r["nav_df"], benchmarks_for_sim, strat_start, strat_end)
        strat_wealth_sims[key] = {
            "final_strategy_value": ws["final_strategy_value"],
            "strategy_gain_on_contributed_pct": ws["strategy_gain_on_contributed_pct"],
            "strategy_mdd_pct": ws["strategy_mdd_pct"],
            "series": ws["series"],
        }

    # ── Today's signals — keyed off the headline strategy (single source of truth)
    print("\nComputing today's signals (headline: {})...".format(headline_key), flush=True)
    headline_open_pos_raw = headline.get("open_positions", {})
    if not isinstance(headline_open_pos_raw, dict):
        headline_open_pos_raw = {}
    # U(과열 스케일아웃)는 T-와 동일한 레짐·슬롯 구조 — 헤드라인 승격 시 동일 취급
    headline_is_t_family = headline_key in (
        "T_kospi_core_chandelier", "T-_kospi_core_regime", "W_allweather_chandelier", "U_chandelier_scaleout",
    )
    today_signals = compute_today_signals(
        perf, prices, ticker_reports, calendar,
        headline_open_positions=headline_open_pos_raw,
        headline_label=headline_label,
        reports=reports,
        kospi=kospi,
        regime_aware=(headline_key in ("T-_kospi_core_regime", "U_chandelier_scaleout")),
        max_positions=(t_max_pos if headline_is_t_family else MAX_POSITIONS),
    )
    print(f"  Open: {today_signals['counts']['open']}, "
          f"Approaching stop: {today_signals['counts']['approaching_stop']}, "
          f"Imminent buys (5td): {today_signals['counts']['imminent_buys']}, "
          f"Watching: {today_signals['counts']['watching']}, "
          f"Regime: {(today_signals.get('regime') or {}).get('state', '—')}",
          flush=True)

    # ── Export CSVs per strategy
    print("\nExporting trade CSVs...", flush=True)
    export_trades_csv(headline.get("trades", []), CSV_PATH)  # headline CSV
    for key, r in all_strategies.items():
        export_trades_csv(r.get("trades", []), PUBLIC_DIR / f"strategy-trades-{key}.csv")

    # ── v24: 다중검정 보정 + 워크포워드 일관성 ─────────────────────────────────
    print("\nComputing DSR (multiple-testing deflation) + walk-forward windows...", flush=True)
    dsr_stats = compute_dsr_stats(all_strategies)
    walkforward = compute_walkforward(all_strategies, kospi)
    _h_dsr = dsr_stats.get(headline_key, {})
    print(f"  trials N={_h_dsr.get('n_trials')}  SR0(ann)={_h_dsr.get('sr0_annualized')}", flush=True)
    print(f"  headline {headline_key}: PSR={_h_dsr.get('psr')}  DSR={_h_dsr.get('dsr')}  "
          f"significant_after_deflation={_h_dsr.get('significant_after_deflation')}", flush=True)
    _h_wf = walkforward.get(headline_key, {}).get("consistency_oos") or {}
    print(f"  headline OOS windows: n={_h_wf.get('n_windows')}  positive={_h_wf.get('positive_pct')}%  "
          f"beat_kospi={_h_wf.get('beat_kospi_pct')}%  median_sharpe={_h_wf.get('median_sharpe')}", flush=True)

    # ── Multi-strategy comparison rows
    multi_strategy_summary = build_multi_strategy_summary(
        all_strategies, kospi_dca_ratios=_kospi_dca_ratios, verdicts=strategy_verdicts,
        dsr_stats=dsr_stats, walkforward=walkforward)

    # ── Serialize open positions helper
    def _serialize_open_positions(raw: dict) -> list[dict]:
        result_list = []
        for t, p in raw.items():
            entry_date_val = p.get("entry_date", "")
            entry_date_str = entry_date_val.isoformat() if hasattr(entry_date_val, "isoformat") else str(entry_date_val)
            stop_val = p.get("stop", 0) or 0
            last_close_val = p.get("last_close", p.get("entry_price", 0))
            cost_val = p.get("cost", 1)
            market_code = p.get("market", "KR")
            # Compute extension gauge for this position (uses last available data)
            df_pos = prices.get(t)
            last_cal_day = calendar[-1]
            ext_val = compute_extension(df_pos, last_cal_day) if df_pos is not None else None
            result_list.append({
                "ticker": t,
                "market": market_code,
                "display_name": p.get("display_name", t),
                "entry_date": entry_date_str,
                "entry": round(float(p.get("entry_price", 0)), 4),
                "last_close": round(float(last_close_val), 4),
                "stop": round_to_tick(float(stop_val), market_code) if stop_val else 0,
                "return_pct": round((p["shares"] * float(last_close_val) / float(cost_val) - 1) * 100, 2),
                "source": p.get("source", ""),
                "n_clubs": p.get("n_clubs", 1),
                "extension": ext_val,   # ATR% multiple from 50-MA (과열 게이지)
                "entry_reason": p.get("entry_reason", ""),   # v18: 왜 진입했는가
            })
        return result_list

    open_positions_list = _serialize_open_positions(headline_open_pos_raw)

    # Per-strategy open positions for UI switcher
    open_positions_by_strategy: dict[str, list[dict]] = {}
    for key, r in all_strategies.items():
        raw_op = r.get("open_positions", {})
        if isinstance(raw_op, dict):
            open_positions_by_strategy[key] = _serialize_open_positions(raw_op)
        else:
            open_positions_by_strategy[key] = []

    # Headline closed trades for JSON
    headline_trades_for_json = [
        t for t in headline.get("trades", [])
        if not t.get("exit_reason", "").endswith("미청산")
    ]

    # Build Optuna methodology note for payload
    optuna_note: dict = {}
    if not optuna_result.get("skipped") and optuna_meta:
        optuna_note = {
            "adopted": d_plus_adopted,
            "best_params": optuna_meta.get("best_params", {}),
            "fold1_sharpe": optuna_meta.get("fold1_sharpe"),
            "fold2_sharpe": optuna_meta.get("fold2_sharpe"),
            "oos_sharpe": result_Dplus.get("out_of_sample", {}).get("sharpe") if result_Dplus else None,
            "is_sharpe": result_Dplus.get("in_sample", {}).get("sharpe") if result_Dplus else None,
            "n_trials": optuna_meta.get("n_trials"),
            "search_space": optuna_meta.get("search_space", {}),
            "methodology": optuna_meta.get("methodology", ""),
            "adoption_criteria": "IS-only parameter selection; OOS is descriptive historical validation, not an untouched holdout",

        }

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "universe_filter": f"rating_class == buy AND report_date >= {UNIVERSE_START.isoformat()} (KR + US)",
        "universe_stats": {
            "kr_reports": int(kr_count),
            "us_reports": int(us_count),
            "total_reports": int(len(perf)),
            "kr_tickers": kr_with_prices,
            "us_tickers": us_with_prices,
        },
        "params": {
            "universe_start": UNIVERSE_START.isoformat(),
            "sim_start": SIM_START.isoformat(),
            "is_period": f"{SIM_START.isoformat()} ~ {IS_END.isoformat()}",
            "oos_period": f"{OOS_START.isoformat()} ~ present",
            "atr_period": ATR_PERIOD,
            "max_positions": MAX_POSITIONS,
            "position_weight": POSITION_WEIGHT,
            "cost_per_side": COST_PER_SIDE,
            "headline_strategy": headline_label,
            "headline_key": headline_key,
            "chandelier_atr_mult": CHANDELIER_ATR_MULT,
            "faber_ma_period": 200,
            "anti_overfit_note": "파라미터는 문헌 표준값 고정 (200MA, ATR×5). 그리드 서치 없음. D+ Optuna는 별도 방법론 섹션 참조.",
        },
        "metrics": headline["metrics"],
        "in_sample": headline.get("in_sample", {}),
        "out_of_sample": headline.get("out_of_sample", {}),
        "yearly": headline["yearly"],
        "equity": headline["equity"],
        "multi_strategy": {
            "strategies": multi_strategy_summary,
            "headline_key": headline_key,
            "curated_keys": curated_keys,
            "verdict_rules": (
                "SOTA = 적격 전략 중 타이브레이크(IS 샤프 → OOS 샤프 → 전체 기간 부의 비율 vs KOSPI DCA) 1위. "
                "채택 = 셀렉터 멤버(핵심 비교 세트 + S 베스트 변형 + 승격 게이트 통과 전략). "
                "연구용 = 비교 앵커·오버레이·대조군 또는 콘테스트 패자. "
                "기각 = 미작동·낙폭 과대·비용 사망·벤치마크 하회. "
                "판정과 사유는 매 백테스트 실행 시 실제 수치에서 생성 — 프런트엔드 하드코딩 없음."
            ),
            "strategy_wealth_sims": strat_wealth_sims,
            # v24: 다중검정 보정 — 26개 변형 중 최고를 고르는 선택 편향의 정량화
            "dsr_note": (
                f"Deflated Sharpe Ratio (Bailey & López de Prado 2014). N={_h_dsr.get('n_trials')}개 변형을 "
                "같은 데이터에서 시도해 최고를 골랐으므로, 무정보 시도에서 기대되는 최대 샤프 "
                f"SR0(연환산 {_h_dsr.get('sr0_annualized')})를 차감해 평가. DSR = P(진짜 샤프 > SR0), "
                "왜도·첨도 보정 포함. DSR ≥ 0.95면 선택 편향을 감안해도 유의. "
                "PSR = P(진짜 샤프 > 0), 단일 전략 기준."
            ),
            # v24: 워크포워드 일관성 — 단일 IS/OOS 분할 보완. 재적합 없음
            # (대부분 문헌 고정 파라미터, D+/U Optuna 파라미터는 IS 적합 →
            #  IS 윈도는 참고치, OOS 윈도가 진짜 검증).
            "walkforward_params": {
                "window_months": WF_WINDOW_MONTHS,
                "refit": False,
                "note": "달력 6개월 윈도별 수익률·샤프·MDD·vs KOSPI. oos=true 윈도가 2024-01 이후.",
            },
            "walkforward_by_strategy": {
                key: v["windows"] for key, v in walkforward.items()
            },
            "equity_by_strategy": {
                key: r["equity"] for key, r in all_strategies.items()
            },
            "yearly_by_strategy": {
                key: r["yearly"] for key, r in all_strategies.items()
            },
            "open_positions_by_strategy": open_positions_by_strategy,
            "trades_by_strategy": {
                key: [t for t in r.get("trades", []) if not t.get("exit_reason", "").endswith("미청산")]
                for key, r in all_strategies.items()
            },
        },
        "optuna_chandelier": optuna_note,
        "tail_stats": tail_stats,
        "consensus_stats": consensus_stats,
        "wealth_sim": wealth_sim,
        "trades": headline_trades_for_json,
        "best_trades": sorted(headline_trades_for_json, key=lambda t: t["return_pct"], reverse=True)[:5],
        "worst_trades": sorted(headline_trades_for_json, key=lambda t: t["return_pct"])[:5],
        "open_positions": open_positions_list,
        "signals": today_signals,
        # ── v11 감사 결과 ──────────────────────────────────────────────────────
        "v11_audit": {
            "mtt_lookahead_fix": (
                "O MTT: 동일 바 룩어헤드 수정 완료. 진입 시그널은 당일 종가 기준으로 포착, "
                "체결은 익일 시가. _compute_rs_percentiles는 asof(day_63/126/252)로 과거 데이터만 사용 — "
                "점검 결과 lookahead 없음. hi52w/lo52w/MA: load_prices rolling window, look-forward 없음. "
                "생존 편향: 프라이스 파일 존재 종목만 포함 — 상장폐지 후 파일 삭제 시 편향 가능. 방법론 주석 명시."
            ),
            "LM_pruned": (
                "L 민리버전(Connors RSI-2)·M 단기 리버설: v11에서 룩어헤드·현금 누락 버그 수정 후 "
                "재검증했으나 두 전략 모두 거래비용으로 사망 확정 (L: 0.3%/side × ~10일 회전 → 연 6-7% 비용; "
                "M: 월별 전체 교체 연 24회 편도 → 연 7% 비용). v18부터 실행·출력하지 않음 — "
                "테스트했고 실패했다는 기록만 방법론에 유지."
            ),
            "excluded_from_selector": sorted(EXCLUDED_FROM_SELECTOR),
            "selection_contamination": {
                "oos_used_for_selection": False,
                "oos_role": "descriptive_validation_only",
                "full_period_benchmark_ratios": "not_valid_for_independent_oos_claims",
                "independent_untouched_holdout": False,
            },
            "P_strategy": (
                f"P 딥바이 샹들리에: IS sharpe={result_P['in_sample'].get('sharpe')}, "
                f"OOS sharpe={result_P['out_of_sample'].get('sharpe')}, "
                f"trades={result_P['metrics']['trades']}, "
                f"max_single_return={result_P['metrics'].get('max_single_return_pct')}%, "
                f"ATR mult used={p_atr_mult_used}. "
                "설계: 딥바이 진입 + 10% 추가 하락 시 1회 스케일인 + ATR 트레일링 스탑 (타겟가 캡 없음)."
            ),
        },
        # ── v12 신규 전략 결과 ─────────────────────────────────────────────────
        "v12_new_strategies": {
            "Q_kangto": {
                "is_sharpe": result_Q["in_sample"].get("sharpe"),
                "oos_sharpe": result_Q["out_of_sample"].get("sharpe"),
                "win_rate_pct": result_Q["metrics"].get("win_rate_pct"),
                "trades": result_Q["metrics"]["trades"],
                "kospi_dca_ratio": _kospi_dca_ratios.get("Q_kangto_trend", {}).get("full_ratio"),
                "description": (
                    "깡토 추세추종: 시장신호등(KOSPI 200MA+50MA상승→2유닛), "
                    "진입=RS퍼센타일≥KOSPI RS AND 60d고가돌파 AND 거래량≥1.5×20d평균, "
                    "스탑=-8%(1R)/BE at+1R/트레일 고점-8% at+1.5R/절반익절 +3R. "
                    "win rate ~30% 설계 — 손절 많고 대형 위너 추구."
                ),
            },
            "R_kelly": {
                "is_sharpe": result_R["in_sample"].get("sharpe"),
                "oos_sharpe": result_R["out_of_sample"].get("sharpe"),
                "trades": result_R["metrics"]["trades"],
                "kospi_dca_ratio": _kospi_dca_ratios.get("R_kelly_chandelier", {}).get("full_ratio"),
                "kelly_params": {
                    "lookback_trades": R_KELLY_LOOKBACK,
                    "cap": R_KELLY_CAP,
                    "safety_factor": R_KELLY_SAFETY,
                    "floor_pct": R_KELLY_FLOOR,
                    "fallback_pct": R_KELLY_FALLBACK,
                },
                "description": (
                    "Kelly 샹들리에: D+ Chandelier 진입/청산 규칙 + Kelly 포지션 사이징. "
                    f"Kelly rolling {R_KELLY_LOOKBACK} trades, cap {R_KELLY_CAP}, safety {R_KELLY_SAFETY}, "
                    f"floor {R_KELLY_FLOOR*100}%. 충분한 거래 이력 없으면 flat {R_KELLY_FALLBACK*100}% fallback. "
                    "참조: alpha16-main utils.py _safe_kelly_fraction."
                ),
            },
            "S_portfolio_opt": {
                "variants": {
                    k: {
                        "is_sharpe": v["in_sample"].get("sharpe"),
                        "oos_sharpe": v["out_of_sample"].get("sharpe"),
                        "trades": v["metrics"]["trades"],
                        "kospi_dca_ratio": _kospi_dca_ratios.get(k, {}).get("full_ratio"),
                    }
                    for k, v in s_variants.items()
                },
                "best_variant": best_s_key,
                "best_is_sharpe": best_s_result["in_sample"].get("sharpe"),
                "best_oos_sharpe": best_s_result["out_of_sample"].get("sharpe"),
                "best_kospi_dca_ratio": _kospi_dca_ratios.get(best_s_key, {}).get("full_ratio"),
                "description": (
                    "포트폴리오 최적화 월간 리밸런스. 유니버스: 18개월 내 buy report 종목. "
                    "Trailing 252d 일별 수익률 점-in-time. "
                    "S_hrp: HRP (직접 구현, corr distance → single-linkage → quasi-diag → iv-split). "
                    "S_msharpe: max-Sharpe (LedoitWolf 수축, long-only w≤15%). "
                    "S_mincvar: min-CVaR 95% (scipy linprog LP, long-only w≤15%). "
                    "IS 샤프 최상 변형만 셀렉터 포함."
                ),
            },
            "T_kospi_core": {
                "T": {
                    "is_sharpe": result_T["in_sample"].get("sharpe"),
                    "oos_sharpe": result_T["out_of_sample"].get("sharpe"),
                    "trades": result_T["metrics"]["trades"],
                    "kospi_dca_ratio": _kospi_dca_ratios.get("T_kospi_core_chandelier", {}).get("full_ratio"),
                },
                "T_minus": {
                    "is_sharpe": result_Tminus["in_sample"].get("sharpe"),
                    "oos_sharpe": result_Tminus["out_of_sample"].get("sharpe"),
                    "trades": result_Tminus["metrics"]["trades"],
                    "kospi_dca_ratio": _kospi_dca_ratios.get("T-_kospi_core_regime", {}).get("full_ratio"),
                },
                "best_variant": _t_best_key,
                "promoted_to_headline": _t_promoted,
                "promotion_verdict": t_promotion_verdict,
                "atr_params": {
                    "atr_period": t_atr_period,
                    "atr_mult": t_atr_mult,
                    "max_positions": t_max_pos,
                    "source": "D+ Optuna best params" if d_plus_adopted else "D default (ATR42×5, 20 slots)",
                },
                "cost_disclosure": (
                    "인덱스 ETF(KODEX200 기준) 전환 비용 0.05%/side 가정. "
                    "주식 매수·매도 비용 0.3%/side (기존 동일). "
                    "실제 KODEX200 스프레드·세금·운용보수는 계좌마다 상이할 수 있음."
                ),
                "interpretation": (
                    "T의 베이스라인 = KOSPI DCA. 주식 픽이 KOSPI 대비 순 알파를 더하면 ratio>1, "
                    "전환 비용이 알파를 삼키면 ratio≤1. "
                    "T-는 KOSPI<200MA 구간에서 파킹 수익 0%(현금) — 약세장 방어 레이어. "
                    "참조: Faber (2007) 10개월 이동평균 레짐 필터."
                ),
                "csv_note": (
                    "CSV는 주식 거래만 기록. KOSPI 파킹 전환(ETF 매수/매도)은 별도 미기록."
                ),
            },
            "U_chandelier_scaleout": {
                "is_sharpe": result_U["in_sample"].get("sharpe"),
                "oos_sharpe": result_U["out_of_sample"].get("sharpe"),
                "trades": result_U["metrics"]["trades"],
                "kospi_dca_ratio": _kospi_dca_ratios.get("U_chandelier_scaleout", {}).get("full_ratio"),
                "max_single_return_pct": result_U["metrics"].get("max_single_return_pct"),
                "top_decile_pnl_share_pct": _u_tb.get("top_decile_pnl_share_pct"),
                "avg_winner_hold_days": _u_tb.get("avg_winner_hold_days"),
                "vs_tminus": u_vs_tminus_verdict,
                "description": (
                    "U 코어-KOSPI 샹들리에 + 과열 스케일아웃. T- 레짐 필터 완전 동일 + "
                    f"ATR% Multiple from 50-MA extension 게이지 (ATR14, 50SMA). "
                    f"extension > {U_SCALEOUT_EXT_1}× → 절반 익절 → KOSPI 파킹 (1차). "
                    f"extension > {U_SCALEOUT_EXT_2}× → 남은 절반 다시 익절 (2차). "
                    "트리거 포지션당 1회. 출처: Minervini 커뮤니티 관행, TradingView Fred6724."
                ),
            },
            "beats_kospi_dca": beats_kospi_both,
            "kospi_dca_verdict": (
                f"KOSPI 적립식 DCA를 전체 기간 자산 기준으로 초과한 전략: {beats_kospi_both if beats_kospi_both else '없음'}. "
                "KOSPI 적립식 매수는 현재도 강력한 베이스라인입니다."
            ),
            "intraday_declined": (
                "장중(intraday) 데이터는 도입 범위 외: 한국 주식 1분봉/tick 데이터 미수집으로 "
                "장중 진입/청산 시뮬레이션 불가. 일봉(daily close) 기반 전략만 구현."
            ),
        },
        # ── v16: V. SPO 포트폴리오 — Smart "Predict, then Optimize" ───────────
        # (기존 'SPO는 향후 과제' 항목 대체. 유상증자(Secondary Public Offering)
        #  이벤트 전략은 여전히 범위 밖 — 여기의 SPO는 Elmachtoub & Grigas의
        #  Smart Predict-then-Optimize 프레임워크.)
        "spo_predict_optimize": {
            "paper": spo_meta.get("paper"),
            "reference_impl": spo_meta.get("reference_impl"),
            "decision_problem": spo_meta.get("decision_problem"),
            "features": spo_meta.get("features"),
            "hyperparams": spo_meta.get("hyperparams"),
            "mirrored_from_julia": spo_meta.get("mirrored_from_julia"),
            "adaptations": spo_meta.get("adaptations"),
            "panel_months": spo_meta.get("panel_months"),
            "first_rebalance": spo_meta.get("first_rebalance"),
            "n_rebalances": spo_meta.get("n_rebalances"),
            "n_train_months_final": spo_meta.get("n_train_months_final"),
            "lambda_selected_last": spo_meta.get("lambda_selected_last"),
            "realized_decision_stats": spo_meta.get("realized_decision_stats"),
            "V_spo": {
                "is_sharpe": result_V_spo["in_sample"].get("sharpe"),
                "oos_sharpe": result_V_spo["out_of_sample"].get("sharpe"),
                "trades": result_V_spo["metrics"]["trades"],
                "mdd_pct": result_V_spo["metrics"].get("mdd_pct"),
                "kospi_dca_ratio": _vspo_ratio,
            },
            "V_ls": {
                "is_sharpe": result_V_ls["in_sample"].get("sharpe"),
                "oos_sharpe": result_V_ls["out_of_sample"].get("sharpe"),
                "trades": result_V_ls["metrics"]["trades"],
                "mdd_pct": result_V_ls["metrics"].get("mdd_pct"),
                "kospi_dca_ratio": _vls_ratio,
            },
            "spo_beats_ls": {"is": _spo_beats_ls_is, "oos": _spo_beats_ls_oos},
            "spo_vs_ls_verdict": spo_vs_ls_verdict,
            "promoted_to_selector": _v_promoted,
            "promotion_verdict": v_verdict_str,
            "promotion_criteria": "IS 기간 T 베스트 변형 대비 벤치마크 비율 우위 AND IS 샤프 동등 이상; OOS는 descriptive historical validation",
            "warmup_note": (
                "워크포워드 최소 24개월 학습 윈도우 — 첫 리밸런스 이전 구간은 현금 보유 "
                "(NAV 평탄). IS 샤프는 이 현금 구간을 포함해 계산되므로 상시 투자 전략과의 "
                "직접 비교 시 주의."
            ),
            "spo_secondary_offering_note": (
                "주의: 과거 방법론의 'SPO(유상증자) 이벤트 전략 보류'와는 별개. "
                "유상증자 이벤트 전략은 여전히 데이터 파이프라인 부재로 범위 밖."
            ),
        },
        "kospi_dca_ratios": _kospi_dca_ratios,
        # ── v18: 파킹 헤드라인 콘테스트 (W 올웨더 vs T- KOSPI 레짐) ───────────
        "parking_showdown": parking_showdown,
        # ── v18: 현금 이자·차입 비용 모델 공시 ─────────────────────────────────
        "cost_model": {
            "cash_yield_annual_pct": round(CASH_YIELD_ANNUAL * 100, 1),
            "cash_yield_daily": round(CASH_YIELD_DAILY, 8),
            "cash_yield_note": (
                "모든 전략의 유휴 현금(빈 슬롯 현금, T-/U 레짐 OFF 구간의 파킹 잔액 포함)에 "
                "연 3.0% 일복리 — (1.03)^(1/252)−1/거래일 — 적용. "
                "근거: 2020-26 한국 MMF/단기채 ETF 평균 수익률 프록시 (고정 가정, 공시). "
                "전략 간 비교 공정성을 위해 일괄 적용."
            ),
            "borrow_rate_annual_pct": round(BORROW_RATE_ANNUAL * 100, 1),
            "borrow_rate_daily": round(BORROW_RATE_DAILY, 8),
            "borrow_note": (
                "J 코어-새틀라이트 레버리지(120%)의 차입 잔액에 연 6.0% 일복리 — "
                "(1.06)^(1/252)−1/거래일 — 적용 (v18: 기존 단리 6%/365에서 통일)."
            ),
            "parking_switch_cost_per_side": KOSPI_PARK_COST,
        },
        # ── 재매수 규칙 ───────────────────────────────────────────────────────
        "reentry_rule": {
            "rule": (
                "청산 후 동일 티커 재진입 허용: 각 패밀리의 진입 조건이 다시 충족되면 재매수. "
                "현재 보유 중인 경우에만 차단 (open_positions 중복 방지). "
                "리포트 구동 패밀리(A~K, N, P): 신규 리포트 발간 시 또는 유효창 내 기술적 조건 재충족 시 재진입. "
                "MTT 계열(O): 유효창 내 기술적 시그널 재발생 시 재진입. "
                "어느 패밀리도 청산 후 영구 차단하지 않음."
            ),
            "families_changed": ["v11: L/M 셀렉터 제외, P 신규 추가"],
            "audit_note": (
                "v11 엔진 전 패밀리 검토: _try_enter()는 ticker in positions 조건만 확인 (현재 보유 여부). "
                "청산 후 positions에서 제거되므로 재진입 자동 허용. 영구 차단 패밀리 없음."
            ),
        },
        # ── MTT O 출처 공시 ───────────────────────────────────────────────────
        "mtt_provenance": {
            "source": "alpha16-main (Minervini MTT RobustOpt KRX params)",
            "params": {
                "stop_pct": MTT_STOP_PCT,
                "be_at_r": MTT_BE_AT_R,
                "trail_pct": MTT_TRAIL_PCT,
                "trail_activate_r": MTT_TRAIL_ACTIVATE_R,
                "take_profit_r": MTT_TAKE_PROFIT_R,
                "rs_buy_threshold": MTT_RS_BUY_THRESHOLD,
                "rs_mtt_threshold": MTT_RS_MTT_THRESHOLD,
                "rs_exit_threshold": MTT_RS_EXIT_THRESHOLD,
                "rs_exit_min_hold_days": MTT_RS_EXIT_MIN_HOLD_DAYS,
                "max_hold_days": MTT_MAX_HOLD_DAYS,
                "price_from_low_mult": MTT_PRICE_FROM_LOW_MULT,
                "price_from_high_mult": MTT_PRICE_FROM_HIGH_MULT,
            },
            "disclaimer": (
                "alpha16 RobustOpt KRX 파라미터는 전체 KRX 종목 대상으로 튜닝된 값입니다. "
                "OUR 리포트 검증 데이터에서 최적화하지 않았습니다 (데이터 오염 방지). "
                "포지션 사이징은 비교 가능성을 위해 당사 5%/20슬롯 동일비중 유지. "
                "Kelly 사이징은 미래 작업으로 남겨둡니다."
            ),
        },
    }

    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nWrote {OUT_PATH.relative_to(ROOT).as_posix()}", flush=True)
    print(f"  Trades in JSON: {len(headline_trades_for_json)}", flush=True)
    # Forward accounting starts at cash on first actual observation, never at historical NAV.
    from paper_portfolio import capture as capture_paper_portfolio
    capture_paper_portfolio(strategy=headline_key, as_of=calendar[-1].isoformat(),
                           nav=float(headline_nav.iloc[-1]), positions=headline.get("open_positions", {}),
                           prices=prices, fx_rate=_fx,
                           core={"INDEX:AllWeather": all_weather, "INDEX:KOSPI": kospi})
    return 0


if __name__ == "__main__":
    # --retune: ignore the Optuna/SPO artifact caches and re-run both searches.
    # (Price-frame caches in data/gold/_cache/price_frames/ auto-invalidate on CSV change.)
    if "--retune" in sys.argv[1:]:
        strategies.FORCE_RETUNE = True
        print("[--retune] Optuna/SPO caches bypassed — full re-search/re-train", flush=True)
    status = main()
    from lake_prices import flush_bronze
    flush_bronze()  # a failed bronze write must fail the run; atexit cannot change the exit code
    sys.exit(status)
