# -*- coding: utf-8 -*-
"""배거의 관상 — 발간 시점 공통 요인 통계 빌더.

modern 매수 리포트(src/data/report-performance.json) 각각에 대해 가격 창고
(data/silver/prices/{KR,US}_*.csv)에서 *발간일 기준 point-in-time* 피처를 계산하고,
bucket_peak 등급(Tenbagger/Multibagger/Double/Winner/Positive)별 분포·사분위
리프트 테이블·로지스틱 회귀(참고용)·현재 후보 스크리닝을 src/data/stats.json으로 내보낸다.

정직성 원칙:
- 피처는 발간일 이전 데이터만 사용 (look-ahead 금지)
- 가격 창고는 각 종목 '첫 리포트 직전'부터 시작하므로(중앙값 리드 15일),
  과거 이력 피처는 재커버·다학회 종목에서만 계산 가능 — 피처별 n을 그대로 공개
- 리프트는 단순 3분위 조건부 확률 / 베이스레이트 — ML 아님
- 로지스틱 회귀는 in-sample 상관 요약일 뿐, 인과·예측력 주장 아님
- 후보 스크리닝은 '현재 시점' 피처로 계산 (발간 시점과 다름을 명시)
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PERF_PATH = ROOT / "src" / "data" / "report-performance.json"
sys.path.insert(0, str(ROOT / "scripts"))
from lake import SILVER_PRICES as PRICES_DIR  # noqa: E402
OUT_PATH = ROOT / "src" / "data" / "stats.json"

SUCCESS_TIERS = {"Double", "Multibagger", "Tenbagger"}
TIER_ORDER = ["Tenbagger", "Multibagger", "Double", "Winner", "Positive"]

# 피처 메타: key -> (한글 라벨, 단위, 설명)
FEATURE_META: dict[str, tuple[str, str, str]] = {
    "ret_1m": ("직전 1개월 수익률", "%", "발간일 기준 21거래일 수익률"),
    "ret_3m": ("직전 3개월 수익률", "%", "발간일 기준 63거래일 수익률"),
    "ret_6m": ("직전 6개월 수익률", "%", "발간일 기준 126거래일 수익률"),
    "ret_12m": ("직전 12개월 수익률", "%", "발간일 기준 252거래일 수익률"),
    "prox_52w_high": ("52주 고가 근접도", "%", "발간일 종가 ÷ 직전 52주 최고가 × 100 — 100%면 신고가"),
    "mult_52w_low": ("52주 저가 배수", "배", "발간일 종가 ÷ 직전 52주 최저가 — 바닥에서 얼마나 올라왔나"),
    "vol_60d": ("변동성 (60일)", "%", "일간 수익률 60일 표준편차 연율화"),
    "vol_trend": ("거래량 추세", "배", "20일 평균 거래량 ÷ 120일 평균 거래량 — 1보다 크면 거래 증가"),
    "vs_ma200": ("200일선 이격", "%", "발간일 종가 ÷ 200일 이동평균 − 1"),
    "rs_6m": ("시장 대비 상대강도 6개월", "%p", "종목 6개월 수익률 − 지수(KOSPI/NASDAQ) 6개월 수익률"),
    "stated_upside_pct": ("리포트 제시 상승여력", "%", "목표가 ÷ 발간 시점 가격 − 1 (리포트 기재)"),
    "n_clubs_18m": ("18개월 내 커버 학회 수", "개", "발간일 기준 직전 18개월 내 같은 종목을 다룬 학회 수 (본인 포함)"),
}
FEATURE_KEYS = list(FEATURE_META.keys())


def load_prices(market: str, ticker: str, cache: dict[str, pd.DataFrame | None]) -> pd.DataFrame | None:
    key = f"{market}_{ticker}"
    if key in cache:
        return cache[key]
    path = PRICES_DIR / f"{key}.csv"
    if not path.exists():
        cache[key] = None
        return None
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df = df[~df.index.isna()].sort_index()
        df = df[df["close"] > 0]
        cache[key] = df
    except Exception:
        cache[key] = None
    return cache[key]


def load_index(name: str) -> pd.Series:
    path = PRICES_DIR / f"IDX_{name}.csv"
    df = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    return df["close"].astype(float)


def trailing_return(close: pd.Series, k: int) -> float:
    """k거래일 수익률(%). 데이터가 k의 90% 미만이면 NaN."""
    if len(close) < int(k * 0.9) + 1:
        return float("nan")
    base = close.iloc[max(0, len(close) - 1 - k)]
    if base <= 0:
        return float("nan")
    return (close.iloc[-1] / base - 1.0) * 100.0


def compute_features(
    prices: pd.DataFrame,
    asof: pd.Timestamp,
    index_close: pd.Series | None,
) -> dict[str, float]:
    """asof일(포함) 이전 데이터만으로 피처 계산. 부족하면 NaN."""
    hist = prices[prices.index <= asof]
    out = {k: float("nan") for k in FEATURE_KEYS if k not in ("stated_upside_pct", "n_clubs_18m")}
    if len(hist) < 22:
        return out
    close = hist["close"].astype(float)
    out["ret_1m"] = trailing_return(close, 21)
    out["ret_3m"] = trailing_return(close, 63)
    out["ret_6m"] = trailing_return(close, 126)
    out["ret_12m"] = trailing_return(close, 252)

    # 52주(252거래일) 창 — 최소 126거래일 있어야 계산
    w52 = hist.tail(252)
    if len(w52) >= 126:
        hi = w52["high"].astype(float).max() if "high" in w52 else w52["close"].astype(float).max()
        lo = w52["low"].astype(float).min() if "low" in w52 else w52["close"].astype(float).min()
        if hi > 0:
            out["prox_52w_high"] = close.iloc[-1] / hi * 100.0
        if lo > 0:
            out["mult_52w_low"] = close.iloc[-1] / lo

    # 변동성 60일 (연율화 %)
    rets = close.pct_change().dropna()
    if len(rets) >= 50:
        out["vol_60d"] = float(rets.tail(60).std() * math.sqrt(252) * 100.0)

    # 거래량 추세 20d/120d
    if "volume" in hist:
        vol = hist["volume"].astype(float)
        v20 = vol.tail(20).mean()
        v120 = vol.tail(120).mean()
        if len(vol) >= 100 and v120 and v120 > 0:
            out["vol_trend"] = float(v20 / v120)

    # 200일선 이격
    if len(close) >= 180:
        ma200 = close.tail(200).mean()
        if ma200 > 0:
            out["vs_ma200"] = (close.iloc[-1] / ma200 - 1.0) * 100.0

    # 시장 대비 상대강도 6개월
    if index_close is not None and not math.isnan(out["ret_6m"]):
        idx_hist = index_close[index_close.index <= asof]
        idx_ret = trailing_return(idx_hist, 126)
        if not math.isnan(idx_ret):
            out["rs_6m"] = out["ret_6m"] - idx_ret
    return out


def quantile(vals: list[float], q: float) -> float | None:
    arr = np.array([v for v in vals if v is not None and not math.isnan(v)])
    if len(arr) == 0:
        return None
    return float(np.quantile(arr, q))


def round_or_none(v: float | None, nd: int = 2) -> float | None:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return round(float(v), nd)


def main() -> None:
    perf = json.loads(PERF_PATH.read_text(encoding="utf-8"))
    records = perf["records"]
    as_of = perf.get("as_of") or dt.date.today().isoformat()

    idx_kospi = load_index("KOSPI")
    idx_nasdaq = load_index("NASDAQ")

    # ── 학회 커버리지 맵 (n_clubs_18m): 전 시대 매수 리포트 (ticker -> [(date, school)])
    coverage: dict[str, list[tuple[pd.Timestamp, str]]] = {}
    for r in records:
        if r.get("rating_class") not in ("buy", "soft_buy"):
            continue
        if not r.get("ticker") or not r.get("report_date"):
            continue
        if r.get("target_seq", 1) != 1:
            continue
        coverage.setdefault(r["ticker"], []).append((pd.Timestamp(r["report_date"]), r["school"]))

    def n_clubs_18m(ticker: str, asof: pd.Timestamp) -> float:
        rows = coverage.get(ticker, [])
        lo = asof - pd.Timedelta(days=547)
        schools = {s for (d, s) in rows if lo <= d <= asof}
        return float(len(schools)) if schools else float("nan")

    # ── 분석 유니버스: modern 매수 리포트, target_seq==1로 중복 제거
    universe = [
        r for r in records
        if r.get("era") == "modern"
        and r.get("rating_class") in ("buy", "soft_buy")
        and r.get("target_seq", 1) == 1
        and r.get("ticker")
        and r.get("report_date")
        and not r.get("data_issue")
        and r.get("bucket_peak") and r["bucket_peak"] != "No quote"
    ]

    price_cache: dict[str, pd.DataFrame | None] = {}
    rows: list[dict] = []
    skipped_no_price = 0
    for r in universe:
        prices = load_prices(r["market"], r["ticker"], price_cache)
        if prices is None or prices.empty:
            skipped_no_price += 1
            continue
        asof = pd.Timestamp(r["report_date"])
        idx = idx_nasdaq if r["market"] == "US" else idx_kospi
        feats = compute_features(prices, asof, idx)
        feats["stated_upside_pct"] = float(r["stated_upside_pct"]) if r.get("stated_upside_pct") is not None else float("nan")
        feats["n_clubs_18m"] = n_clubs_18m(r["ticker"], asof)
        rows.append({
            "ticker": r["ticker"], "market": r["market"], "school": r["school"],
            "report_date": r["report_date"], "tier": r["bucket_peak"],
            "success": r["bucket_peak"] in SUCCESS_TIERS,
            **feats,
        })

    df = pd.DataFrame(rows)
    n_total = len(df)
    base_rate = float(df["success"].mean())
    print(f"universe: {n_total} reports (skipped no-price: {skipped_no_price}), "
          f"base rate P(>=Double on peak) = {base_rate:.3f}")

    # ── 1) 등급별 피처 분포 (median / IQR / p10-p90)
    tier_stats: dict[str, dict[str, dict]] = {}
    n_by_tier = {t: int((df["tier"] == t).sum()) for t in TIER_ORDER}
    for key in FEATURE_KEYS:
        tier_stats[key] = {}
        for tier in TIER_ORDER:
            vals = df.loc[df["tier"] == tier, key].dropna().tolist()
            tier_stats[key][tier] = {
                "n": len(vals),
                "median": round_or_none(quantile(vals, 0.5)),
                "q1": round_or_none(quantile(vals, 0.25)),
                "q3": round_or_none(quantile(vals, 0.75)),
                "p10": round_or_none(quantile(vals, 0.10)),
                "p90": round_or_none(quantile(vals, 0.90)),
            }

    # ── 2) 3분위 리프트: P(tier>=Double | 피처 3분위) / base rate
    # 사분위는 구간당 n이 ~90으로 표준오차 ±5%p — 3분위로 노이즈를 줄인다.
    lift_tables: dict[str, dict] = {}
    feature_summary: list[dict] = []
    for key in FEATURE_KEYS:
        sub = df[[key, "success"]].dropna()
        if len(sub) < 80:
            continue
        if key == "n_clubs_18m":
            # 이산값 — 1개 학회 vs 2개+ 학회로 명시적 이분
            groups = [("1개 학회", sub[key] <= 1), ("2개+ 학회", sub[key] >= 2)]
            cats = None
        else:
            try:
                binned = pd.qcut(sub[key], 3, duplicates="drop")
            except ValueError:
                continue
            cats = binned.cat.categories
            if cats.size < 2:
                continue
            labels = ["하위 ⅓", "중위 ⅓", "상위 ⅓"][: cats.size] if cats.size == 3 else [f"구간{i + 1}" for i in range(cats.size)]
            groups = [(labels[i], binned == cat) for i, cat in enumerate(cats)]
        bin_rows = []
        for i, (label, mask) in enumerate(groups):
            n = int(mask.sum())
            n_succ = int(sub.loc[mask, "success"].sum())
            p = n_succ / n if n else 0.0
            lo = round_or_none(float(cats[i].left), 2) if cats is not None else None
            hi = round_or_none(float(cats[i].right), 2) if cats is not None else None
            bin_rows.append({
                "label": label,
                "lo": lo,
                "hi": hi,
                "n": n,
                "success": n_succ,
                "p_pct": round_or_none(p * 100, 1),
                "lift": round_or_none(p / base_rate, 2) if base_rate else None,
            })
        ps = [b["p_pct"] for b in bin_rows]
        # 단조성: 인접 구간이 같은 방향으로 (2%p 허용오차) 움직이는가
        diffs = [ps[i + 1] - ps[i] for i in range(len(ps) - 1)]
        tol = 2.0
        monotone_up = all(d >= -tol for d in diffs) and ps[-1] > ps[0]
        monotone_down = all(d <= tol for d in diffs) and ps[-1] < ps[0]
        spread = (bin_rows[-1]["lift"] or 0) - (bin_rows[0]["lift"] or 0)
        lift_tables[key] = {"n": int(len(sub)), "bins": bin_rows}
        feature_summary.append({
            "key": key,
            "label": FEATURE_META[key][0],
            "unit": FEATURE_META[key][1],
            "desc": FEATURE_META[key][2],
            "n": int(len(sub)),
            "monotone": bool(monotone_up or monotone_down),
            "direction": 1 if spread > 0 else -1,
            "lift_spread": round_or_none(abs(spread), 2),
            "best_bin_lift": round_or_none(max(b["lift"] or 0 for b in bin_rows), 2),
        })

    # ── 3) 상위 판별 요인: 단조 우선 + 스프레드 큰 순 (최대 5개, 스프레드 0.15 이상)
    eligible = [f for f in feature_summary if (f["lift_spread"] or 0) >= 0.15]
    eligible.sort(key=lambda f: (not f["monotone"], -(f["lift_spread"] or 0)))
    top_factors_meta = eligible[:5]
    for f in feature_summary:
        f["is_top"] = f["key"] in {t["key"] for t in top_factors_meta}

    # 후보 체크용 임계값: 유리한 쪽 3분위 경계 (direction>0이면 상위 ⅓ 경계, 아니면 하위 ⅓)
    top_factors: list[dict] = []
    for f in top_factors_meta:
        key = f["key"]
        vals = df[key].dropna()
        if key == "n_clubs_18m":
            thr, cond = 2.0, ">="
        elif f["direction"] > 0:
            thr, cond = float(np.quantile(vals, 2 / 3)), ">="
        else:
            thr, cond = float(np.quantile(vals, 1 / 3)), "<="
        top_factors.append({
            "key": key,
            "label": f["label"],
            "unit": f["unit"],
            "direction": f["direction"],
            "monotone": f["monotone"],
            "threshold": round_or_none(thr, 2),
            "cond": cond,
            "lift_spread": f["lift_spread"],
            "best_bin_lift": f["best_bin_lift"],
        })

    # ── 4) 로지스틱 회귀 (참고용, L2, in-sample) — 상관 요약이지 인과 아님
    logit_out: dict | None = None
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import roc_auc_score

        feat_cols = [k for k in FEATURE_KEYS if k in lift_tables]
        comp = df[feat_cols + ["success"]].dropna()
        if len(comp) >= 200:
            X = comp[feat_cols].to_numpy(dtype=float)
            mu, sd = X.mean(axis=0), X.std(axis=0)
            sd[sd == 0] = 1.0
            Xz = (X - mu) / sd
            y = comp["success"].astype(int).to_numpy()
            model = LogisticRegression(C=1.0, max_iter=2000)  # 기본 L2
            model.fit(Xz, y)
            auc = float(roc_auc_score(y, model.predict_proba(Xz)[:, 1]))
            coefs = sorted(
                [
                    {"key": k, "label": FEATURE_META[k][0], "coef": round_or_none(float(c), 3)}
                    for k, c in zip(feat_cols, model.coef_[0])
                ],
                key=lambda d: -abs(d["coef"] or 0),
            )
            logit_out = {
                "n": int(len(comp)),
                "auc_in_sample": round_or_none(auc, 3),
                "coefs": coefs,
                "note": "L2 로지스틱 회귀, 피처 표준화(1SD당 로그오즈). in-sample 상관 요약이며 인과·예측력 주장이 아님. 결측 없는 표본만 사용.",
            }
            print(f"logit: n={len(comp)} auc(in-sample)={auc:.3f}")
    except Exception as exc:  # sklearn 없거나 수렴 실패 시 생략
        print(f"logit skipped: {exc}")

    # ── 5) 현재 후보 스크리닝: 발간 180일 이내 & 아직 피크 기준 Double 미만
    cand_src = [
        r for r in records
        if r.get("era") == "modern"
        and r.get("rating_class") in ("buy", "soft_buy")
        and r.get("target_seq", 1) == 1
        and r.get("ticker") and r.get("report_date")
        and not r.get("data_issue")
        and (r.get("age_days") is not None and r["age_days"] <= 180)
        and r.get("bucket_peak") not in SUCCESS_TIERS
    ]
    # 종목별 최신 리포트 1건 + 커버 학회 수집
    by_ticker: dict[str, dict] = {}
    schools_by_ticker: dict[str, set[str]] = {}
    for r in cand_src:
        schools_by_ticker.setdefault(r["ticker"], set()).add(r["school"])
        prev = by_ticker.get(r["ticker"])
        if prev is None or r["report_date"] > prev["report_date"]:
            by_ticker[r["ticker"]] = r

    candidates: list[dict] = []
    stale_cut = pd.Timestamp(as_of) - pd.Timedelta(days=14)
    for ticker, r in by_ticker.items():
        prices = load_prices(r["market"], ticker, price_cache)
        if prices is None or prices.empty:
            continue
        last_date = prices.index[-1]
        idx = idx_nasdaq if r["market"] == "US" else idx_kospi
        feats = compute_features(prices, last_date, idx)
        feats["stated_upside_pct"] = float(r["stated_upside_pct"]) if r.get("stated_upside_pct") is not None else float("nan")
        feats["n_clubs_18m"] = n_clubs_18m(ticker, pd.Timestamp(as_of))
        checks = []
        score = 0
        for tf in top_factors:
            v = feats.get(tf["key"], float("nan"))
            ok: bool | None
            if v is None or math.isnan(v):
                ok = None
            elif tf["cond"] == ">=":
                ok = bool(v >= tf["threshold"])
            else:
                ok = bool(v <= tf["threshold"])
            if ok:
                score += 1
            checks.append({"key": tf["key"], "value": round_or_none(v, 2), "pass": ok})
        candidates.append({
            "ticker": ticker,
            "market": r["market"],
            "slug": f"{r['market']}-{ticker}".lower(),
            "name": r.get("display_name") or r.get("company") or ticker,
            "school": r["school"],
            "schools": sorted(schools_by_ticker.get(ticker, set())),
            "report_date": r["report_date"],
            "age_days": r["age_days"],
            "tier_now": r.get("bucket_peak"),
            "stated_upside_pct": round_or_none(r.get("stated_upside_pct"), 1),
            "return_since_pct": round_or_none(r.get("return_latest_pct"), 1),
            "score": score,
            "checks": checks,
            "price_as_of": str(last_date.date()),
            "stale_price": bool(last_date < stale_cut),
        })
    candidates.sort(key=lambda c: (-c["score"], c["report_date"]))

    # ── 6) 학회 합의(consensus) — 같은 종목을 여럿이 보면 성과가 다른가 (v25)
    # 리포트 단위 분석: 각 매수 리포트에 대해 발간일 직전 90일 내 같은 종목을
    # 커버한 학회 수(본인 포함)를 세고, 그룹(1 / 2 / 3+)별 결과를 비교한다.
    # 주의: 리포트가 행 단위이므로 합의 에피소드의 리포트들은 각자 한 행 —
    # 윈도가 겹치는 동시 커버는 그룹에 중복 기여한다 (에피소드 중복 제거 아님).
    def n_schools_90d(ticker: str, asof: pd.Timestamp) -> int:
        rows_cov = coverage.get(ticker, [])
        lo = asof - pd.Timedelta(days=90)
        return len({s for (d, s) in rows_cov if lo <= d <= asof})

    cons_universe = [
        r for r in records
        if r.get("era") == "modern"
        and r.get("rating_class") in ("buy", "soft_buy")
        and r.get("target_seq", 1) == 1
        and r.get("ticker") and r.get("report_date")
        and not r.get("data_issue")
        and r.get("bucket_peak") and r["bucket_peak"] != "No quote"
    ]
    cons_rows = []
    for r in cons_universe:
        n_sch = n_schools_90d(r["ticker"], pd.Timestamp(r["report_date"]))
        cons_rows.append({
            "ticker": r["ticker"], "market": r["market"], "school": r["school"],
            "display_name": r.get("display_name") or r.get("company") or r["ticker"],
            "report_date": r["report_date"],
            "n_schools": max(n_sch, 1),
            "success": r["bucket_peak"] in SUCCESS_TIERS,
            "tier": r["bucket_peak"],
            "peak_pct": r.get("peak_return_24m_pct"),
            "latest_pct": r.get("return_latest_pct"),
        })
    cons_df = pd.DataFrame(cons_rows)
    cons_base = float(cons_df["success"].mean()) if len(cons_df) else 0.0

    def _cons_group(mask: pd.Series, label: str) -> dict | None:
        sub = cons_df[mask]
        if len(sub) < 10:
            return None
        return {
            "label": label,
            "n": int(len(sub)),
            "success_pct": round_or_none(float(sub["success"].mean()) * 100, 1),
            "lift": round_or_none(float(sub["success"].mean()) / cons_base, 2) if cons_base else None,
            "median_peak_pct": round_or_none(quantile(sub["peak_pct"].dropna().tolist(), 0.5), 1),
            "median_latest_pct": round_or_none(quantile(sub["latest_pct"].dropna().tolist(), 0.5), 1),
        }

    consensus_groups = [
        g for g in (
            _cons_group(cons_df["n_schools"] == 1, "단독 커버"),
            _cons_group(cons_df["n_schools"] == 2, "2개 학회"),
            _cons_group(cons_df["n_schools"] >= 3, "3개+ 학회"),
        ) if g
    ] if len(cons_df) else []

    # 합의 에피소드 명부: 90일 내 2개+ 학회가 본 리포트, 최신순 상위 30건
    episodes = sorted(
        (r for r in cons_rows if r["n_schools"] >= 2),
        key=lambda r: (r["report_date"], r["n_schools"]),
        reverse=True,
    )[:30]
    consensus_out = {
        "window_days": 90,
        "base_rate_pct": round_or_none(cons_base * 100, 1),
        "n_reports": int(len(cons_df)),
        "groups": consensus_groups,
        "episodes": [
            {
                "ticker": e["ticker"],
                "slug": f"{e['market']}-{e['ticker']}".lower(),
                "name": e["display_name"],
                "report_date": e["report_date"],
                "school": e["school"],
                "n_schools": e["n_schools"],
                "tier": e["tier"],
                "peak_pct": round_or_none(e["peak_pct"], 1),
                "latest_pct": round_or_none(e["latest_pct"], 1),
            }
            for e in episodes
        ],
        "note": (
            "합의 = 발간일 직전 90일 내 같은 종목을 커버한 학회 수(본인 포함). "
            "리포트 단위 집계라 동시 커버 에피소드는 그룹에 중복 기여. "
            "성공 = 발간 후 24개월 피크 +100% 이상(Double+)."
        ),
    }
    if consensus_groups:
        print("consensus:", [(g["label"], g["n"], g["success_pct"], g["lift"]) for g in consensus_groups])

    out = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "as_of": as_of,
        "universe": {
            "n_reports": n_total,
            "n_by_tier": n_by_tier,
            "base_rate_pct": round_or_none(base_rate * 100, 1),
            "success_def": "bucket_peak ≥ Double — 발간 후 24개월 내 최고가 기준 +100% 이상",
            "criteria": "modern 시대(2019.07~) 매수·소프트매수 의견, 리포트당 1행(첫 목표가), 시세 확보분",
            "skipped_no_price": skipped_no_price,
            "coverage_caveat": "가격 창고가 각 종목 첫 리포트 직전부터 시작하므로, 과거 이력이 필요한 시세 피처는 재커버 종목(이전에 다른 리포트가 있던 종목)에서만 계산됩니다. 피처별 n이 전체보다 작은 이유입니다.",
        },
        "features": feature_summary,
        "tier_order": TIER_ORDER,
        "tier_stats": tier_stats,
        "lift": lift_tables,
        "top_factors": top_factors,
        "logit": logit_out,
        "candidates": candidates,
        "consensus": consensus_out,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"top factors: {[t['key'] for t in top_factors]}")
    print(f"candidates: {len(candidates)} (score>=3: {sum(1 for c in candidates if c['score'] >= 3)})")
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
