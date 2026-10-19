"""Smart "Predict, then Optimize" (SPO+) 포트폴리오 모듈 — 전략 패밀리 V.

논문:  Elmachtoub & Grigas, "Smart 'Predict, then Optimize'",
       Management Science 68(1), 2022 (arXiv 2017).
참조 구현:  github.com/paulgrigas/SmartPredictThenOptimize (Julia)
       - solver/sgd.jl          : SPO+ 확률적 서브그래디언트 (spoPlus_sgd)
       - solver/validation_set.jl: 검증셋 람다 선택 (validation_set_alg)

문제 설정 (논문의 선형 목적식-다면체 설정 그대로):
    의사결정: 월간, 롱온리 비중 w — max  r·w  s.t.  Σw = 1, 0 ≤ w ≤ 0.15.
    내부적으로 비용 최소화 관례 사용: c = -(다음달 수익률), min c·w.
    캡 심플렉스 LP는 닫힌형(계수 오름차순 그리디 충전)으로 풀린다 → 오라클 저비용.

예측 모델: 선형 c_hat = X·b (자산별 피처 x → 자산별 비용; 계수 b는 자산 간 공유 —
    유니버스가 매월 변하는 컨텍스추얼 포트폴리오 적응).

SPO+ 손실 (논문 식, sgd.jl과 동일한 서브그래디언트):
    ℓ_SPO+(ĉ, c) = max_w {(c − 2ĉ)·w} + 2ĉ·w*(c) − z*(c)
    ∂ℓ/∂b = 2 Xᵀ (w*(c) − w_oracle(2ĉ − c))

Julia 레포에서 그대로 미러링한 것:
    - 서브그래디언트 식 (sgd.jl :stochastic), batchsize=10, numiter=1000
    - 스텝 사이즈: λ=0 → :long_dynamic = 0.1/sqrt(iter+1) (sgd.jl 기본),
                  λ>0 → :short = 2/(λ(iter+2)) (spoPlus_sgd_path 기본)
    - 지연 가중 평균 이터레이트 (B_avg 업데이트 식 그대로)
    - 릿지 정규화 경로: λ_max=(d/n)·‖X‖²_F, 로그등간 그리드, 워름스타트
    - 검증셋 20% 무작위, 검증 SPO 손실(의사결정 리그렛) argmin으로 모델 선택
      (validation_set_alg 기본 손실 — SPO+/LS 양쪽 모두 동일 기준)

우리의 적응 (정직 공개; 방법론 노트에 동일 기재):
    - LS 베이스라인은 SGD 대신 동일 릿지 목적식의 닫힌형 정규방정식으로 정확히 풂
      (레포의 :stochastic_LS와 같은 목적식 — 수치 최적화 오차 제거, 베이스라인에 유리)
    - num_lambda 100→10, lambda_min_ratio 1e-4→1e-6 (파이썬 연산량 절충 + 저정규화 영역 확장),
      그리드에 λ=0(무정규화) 후보 추가
    - 경로 반복수는 레포의 정확도 기반 공식(2M/(λε)) 대신 고정 numiter=1000
    - 워크포워드 확장 윈도우 (최소 24개월), 매월 재학습 — 레포는 단일 학습/검증 실험
    - 피처 횡단면 표준화 + 절편(표준화 제외) — 시점 정보만 사용 (look-ahead 없음)
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

# ── 하이퍼파라미터 (sgd.jl/validation_set.jl 기본값 미러; 적응값은 주석) ─────
SPO_NUMITER          = 1000    # sgd.jl sgd_parms.numiter
SPO_BATCHSIZE        = 10      # sgd.jl sgd_parms.batchsize
SPO_LONG_FACTOR      = 0.1     # sgd.jl long_factor (:long_dynamic)
SPO_NUM_LAMBDA       = 10      # 적응: 레포 100 → 10
SPO_LAMBDA_MIN_RATIO = 1e-6    # 적응: 레포 1e-4 → 1e-6 (저정규화 영역 포함)
SPO_VALIDATION_PCT   = 0.2     # validation_set.jl validation_set_percent
SPO_WEIGHT_CAP       = 0.15    # 개별 비중 상한 (S 패밀리와 동일)
SPO_MIN_TRAIN_MONTHS = 24      # 워크포워드 최소 학습 월수
SPO_MIN_STOCKS       = 5       # 횡단면 최소 종목수 (표준화 가능 최소)
SPO_UNIVERSE_MONTHS  = 18      # buy 리포트 유효창 (S 패밀리와 동일)
SPO_MIN_HISTORY_DAYS = 252     # 최소 가격 이력
SPO_SEED             = 20170422  # E&G arXiv v1 게재일 — 재현성 시드 베이스

FEATURE_NAMES = [
    "ret_1m", "ret_3m", "ret_6m", "ret_12m", "vol_60d", "rs_pct",
    "prox_52w_high", "days_since_report", "stated_upside_pct", "n_clubs_18m",
]


# ──────────────────────────────────────────────────────────────────────────────
# LP 오라클 — 캡 심플렉스 위 선형 최소화 (닫힌형 그리디)
# ──────────────────────────────────────────────────────────────────────────────

def capped_simplex_argmin(c: np.ndarray, cap: float = SPO_WEIGHT_CAP) -> tuple[float, np.ndarray]:
    """min c·w  s.t.  Σw = 1, 0 ≤ w ≤ cap_eff.

    cap_eff = max(cap, 1/d): 종목 수가 1/cap 미만이면 캡을 1/d로 완화
    (d·cap < 1 이면 불능 — 이때 유일 가능해는 동일비중).
    닫힌형: 비용 오름차순으로 캡까지 충전.
    """
    d = len(c)
    cap_eff = max(cap, 1.0 / d)
    order = np.argsort(c, kind="stable")
    w = np.zeros(d)
    budget = 1.0
    for i in order:
        a = min(cap_eff, budget)
        w[i] = a
        budget -= a
        if budget <= 1e-12:
            break
    return float(c @ w), w


def _spo_regret(b: np.ndarray, samples: list[dict]) -> float:
    """평균 SPO 손실(의사결정 리그렛): mean_m [ c_m·w(ĉ_m) − z*(c_m) ].

    레포 spo_loss와 동일 정의 — validation_set_alg의 모델 선택 기준.
    """
    total = 0.0
    for s in samples:
        c_hat = s["X"] @ b
        _, w_hat = capped_simplex_argmin(c_hat)
        total += float(s["c"] @ w_hat) - s["z_star"]
    return total / max(len(samples), 1)


# ──────────────────────────────────────────────────────────────────────────────
# SPO+ 확률적 서브그래디언트 (sgd.jl spoPlus_sgd :stochastic 미러)
# ──────────────────────────────────────────────────────────────────────────────

def spo_plus_sgd(
    samples: list[dict],
    p: int,
    lam: float,
    rng: np.random.Generator,
    b_init: np.ndarray | None = None,
    numiter: int = SPO_NUMITER,
    batchsize: int = SPO_BATCHSIZE,
) -> np.ndarray:
    """sgd.jl spoPlus_sgd 미러 (grad_type=:stochastic).

    서브그래디언트:  G = (1/batch) Σ 2 Xᵀ(w*(c) − w_oracle(2ĉ − c)) + λ·b
    평균 이터레이트: sgd.jl과 동일한 지연 스텝가중 평균.
    스텝 사이즈: λ=0 → :long_dynamic (sgd.jl 기본, 0.1/√(t+1)),
                λ>0 → :short (경로 알고리즘 spoPlus_sgd_path 기본, 2/(λ(t+2)) —
                Lacoste-Julien et al.; long 스텝은 강볼록 항에서 발산).
    """
    n = len(samples)
    b_iter = np.zeros(p) if b_init is None else b_init.copy()
    b_avg = b_iter.copy()
    step_sum = 0.0
    for it in range(numiter):
        g = np.zeros(p)
        for _ in range(batchsize):
            s = samples[int(rng.integers(n))]
            c_hat = s["X"] @ b_iter
            spoplus_vec = 2.0 * c_hat - s["c"]
            _, w_oracle = capped_simplex_argmin(spoplus_vec)
            g += 2.0 * (s["X"].T @ (s["w_star"] - w_oracle))
        g = g / batchsize + lam * b_iter
        if lam > 0:
            step = 2.0 / (lam * (it + 2))            # sgd.jl :short
        else:
            step = SPO_LONG_FACTOR / math.sqrt(it + 1)  # sgd.jl :long_dynamic
        # 평균 이터레이트 업데이트 (sgd.jl 그대로 — "Note the lag")
        step_sum += step
        step_avg = step / step_sum
        b_avg = (1.0 - step_avg) * b_avg + step_avg * b_iter
        b_iter = b_iter - step * g
        if not np.isfinite(b_iter).all():
            # 발산 가드: 비유한 이터레이트는 0으로 리셋 (해당 후보는 검증에서 탈락)
            b_iter = np.zeros(p)
    return b_avg if np.isfinite(b_avg).all() else np.zeros(p)


def ls_ridge_closed_form(samples: list[dict], p: int, lam: float) -> np.ndarray:
    """릿지 LS 닫힌형 — sgd.jl :stochastic_LS와 동일 목적식의 정확해.

    (1/n) Σ_m Xᵀ(Xb − c) + λb = 0  →  (Σ XᵀX + nλI) b = Σ Xᵀc
    """
    n = len(samples)
    xtx = np.zeros((p, p))
    xtc = np.zeros(p)
    for s in samples:
        xtx += s["X"].T @ s["X"]
        xtc += s["X"].T @ s["c"]
    a = xtx + max(lam, 1e-10) * n * np.eye(p)
    try:
        return np.linalg.solve(a, xtc)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(a, xtc, rcond=None)[0]


def _lambda_grid(samples: list[dict], p: int) -> list[float]:
    """릿지 경로 그리드 — 레포 spoPlus_sgd_path: λ_max = (d/n)·‖X‖²_F.

    유니버스 크기가 월별로 변하므로 d = 평균 자산수, ‖X‖²_F = Σ_m ‖X_m‖²_F.
    로그등간 num_lambda개 + λ=0(무정규화) 후보 추가 (적응).
    """
    n = len(samples)
    d_avg = float(np.mean([len(s["c"]) for s in samples]))
    norm_sq = float(sum(np.sum(s["X"] ** 2) for s in samples))
    lambda_max = (d_avg / n) * norm_sq
    if lambda_max <= 0:
        return [0.0]
    lambda_min = lambda_max * SPO_LAMBDA_MIN_RATIO
    grid = np.exp(np.linspace(math.log(lambda_min), math.log(lambda_max), SPO_NUM_LAMBDA))
    return [0.0] + [float(v) for v in grid]


def _validation_split(
    n: int, rng: np.random.Generator
) -> tuple[list[int], list[int]]:
    """validation_set.jl: 무작위 20%를 검증셋으로 (최소 2개월 보장)."""
    n_val = max(2, int(round(SPO_VALIDATION_PCT * n)))
    val_idx = set(rng.choice(n, size=n_val, replace=False).tolist())
    train = [i for i in range(n) if i not in val_idx]
    val = sorted(val_idx)
    return train, val


def train_with_validation(
    samples: list[dict], p: int, rng: np.random.Generator, method: str,
) -> tuple[np.ndarray, float]:
    """validation_set_alg 미러: 릿지 경로 학습 → 검증 SPO 손실 argmin 선택.

    method: "spo_plus" (SGD) | "ls" (닫힌형).
    반환: (선택된 b, 선택된 λ).
    """
    train_idx, val_idx = _validation_split(len(samples), rng)
    train_s = [samples[i] for i in train_idx]
    val_s = [samples[i] for i in val_idx]
    lambdas = _lambda_grid(train_s, p)

    best_b, best_lam, best_loss = None, 0.0, float("inf")
    b_warm = np.zeros(p)
    for lam in lambdas:
        if method == "spo_plus":
            b = spo_plus_sgd(train_s, p, lam, rng, b_init=b_warm)
            if np.isfinite(b).all():
                b_warm = b  # 경로 워름스타트 (spoPlus_sgd_path 미러)
        else:
            b = ls_ridge_closed_form(train_s, p, lam)
        if not np.isfinite(b).all():
            continue
        loss = _spo_regret(b, val_s)
        if loss < best_loss:
            best_loss, best_b, best_lam = loss, b, lam
    assert best_b is not None
    return best_b, best_lam


# ──────────────────────────────────────────────────────────────────────────────
# 피처/레이블 패널 (point-in-time)
# ──────────────────────────────────────────────────────────────────────────────

def _asof_close(df: pd.DataFrame, day: dt.date) -> float | None:
    sub = df["close"].loc[: pd.Timestamp(day)]
    if sub.empty:
        return None
    v = float(sub.iloc[-1])
    return v if v > 0 else None


def _open_on_or_after(df: pd.DataFrame, day: dt.date, limit_days: int = 7) -> float | None:
    """day 이후(포함) limit_days 내 첫 시가 — 월초 체결가/레이블용."""
    ts = pd.Timestamp(day)
    sub = df.loc[(df.index >= ts) & (df.index <= ts + pd.Timedelta(days=limit_days))]
    if sub.empty:
        return None
    v = float(sub.iloc[0]["open"])
    return v if v > 0 else None


def build_panel(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    ticker_reports: dict[str, list[dict]],
) -> list[dict]:
    """월말 횡단면 패널 구축.

    각 월말 m에 대해:
      universe — 직전 18개월 내 buy 리포트 + m까지 ≥252영업일 이력 + 종가 존재
      X        — 표준화 피처 (횡단면 z-score) + 절편
      c        — -(다음달 시가→다다음달 시가 수익률); 미완결이면 None
    """
    cal_s = pd.Series(calendar)
    keys = cal_s.apply(lambda d: (d.year, d.month))
    month_ends = list(cal_s.groupby(keys).last())
    month_firsts = list(cal_s.groupby(keys).first())

    # 티커별 리포트 유효창
    valid_ranges: dict[str, list[tuple[dt.date, dt.date]]] = {}
    for rdate, ticker, _src, _n in reports:
        expire = rdate + dt.timedelta(days=int(SPO_UNIVERSE_MONTHS * 30.44))
        valid_ranges.setdefault(ticker, []).append((rdate, expire))

    panel: list[dict] = []
    for k, m in enumerate(month_ends):
        m_ts = pd.Timestamp(m)
        rows: list[tuple[str, list[float]]] = []
        for ticker, ranges in valid_ranges.items():
            if not any(start <= m <= end for start, end in ranges):
                continue
            df = prices.get(ticker)
            if df is None:
                continue
            hist = df.loc[df.index <= m_ts]
            if len(hist) < SPO_MIN_HISTORY_DAYS:
                continue
            close_now = float(hist["close"].iloc[-1])
            if close_now <= 0:
                continue

            # 트레일링 수익률 (영업일 근사: 21/63/126/252)
            closes = hist["close"]
            c1 = float(closes.iloc[-22]) if len(closes) >= 22 else None
            c3 = float(closes.iloc[-64]) if len(closes) >= 64 else None
            c6 = float(closes.iloc[-127]) if len(closes) >= 127 else None
            c12 = float(closes.iloc[-253]) if len(closes) >= 253 else None
            if not all(v and v > 0 for v in (c1, c3, c6, c12)):
                continue
            ret_1m = close_now / c1 - 1
            ret_3m = close_now / c3 - 1
            ret_6m = close_now / c6 - 1
            ret_12m = close_now / c12 - 1

            # 60일 변동성 (연율화)
            r60 = closes.iloc[-61:].pct_change().dropna()
            if len(r60) < 30:
                continue
            vol_60d = float(r60.std()) * math.sqrt(252)

            # 52주 고가 근접도
            hi52 = float(hist["high"].iloc[-252:].max())
            prox_52w = close_now / hi52 if hi52 > 0 else 1.0

            # 리포트 피처 (m 이전 발간분만 — point-in-time)
            tr_list = [r for r in ticker_reports.get(ticker, []) if r["report_date"] <= m]
            in_window = [
                r for r in tr_list
                if (m - r["report_date"]).days <= int(SPO_UNIVERSE_MONTHS * 30.44)
            ]
            if not in_window:
                continue
            latest = in_window[-1]
            days_since = float((m - latest["report_date"]).days)
            upside = latest.get("stated_upside_pct")
            n_clubs = float(len({r.get("school", "") for r in in_window}))

            rows.append((ticker, [
                ret_1m, ret_3m, ret_6m, ret_12m, vol_60d,
                0.0,  # rs_pct — 아래 횡단면 패스에서 채움
                prox_52w, days_since,
                float(upside) if upside is not None else math.nan,
                n_clubs,
            ]))

        if len(rows) < SPO_MIN_STOCKS:
            continue

        tickers = [t for t, _ in rows]
        feat = np.array([f for _, f in rows], dtype=float)

        # RS 퍼센타일 (MTT 가중: 3m×0.5 + 6m×0.3 + 12m×0.2 의 횡단면 랭크)
        rs_raw = feat[:, 1] * 0.5 + feat[:, 2] * 0.3 + feat[:, 3] * 0.2
        rs_rank = rs_raw.argsort().argsort().astype(float)
        feat[:, 5] = rs_rank / max(len(rs_rank) - 1, 1) * 99.0

        # stated_upside 결측 → 횡단면 중앙값
        ups = feat[:, 8]
        if np.isnan(ups).any():
            med = float(np.nanmedian(ups)) if not np.isnan(ups).all() else 0.0
            feat[:, 8] = np.where(np.isnan(ups), med, ups)

        # 횡단면 표준화 + 절편
        mu = feat.mean(axis=0)
        sd = feat.std(axis=0)
        sd[sd < 1e-12] = 1.0
        x = (feat - mu) / sd
        x = np.hstack([x, np.ones((len(x), 1))])

        # 레이블: 다음달 시가 → 다다음달 시가 (월초 체결과 정합)
        c_vec: np.ndarray | None = None
        label_tickers: list[str] = []
        if k + 2 < len(month_firsts):
            entry_day = month_firsts[k + 1]
            exit_day = month_firsts[k + 2]
            c_list, lt = [], []
            for i, ticker in enumerate(tickers):
                df = prices[ticker]
                o1 = _open_on_or_after(df, entry_day)
                o2 = _open_on_or_after(df, exit_day)
                if o1 is None or o2 is None:
                    continue
                c_list.append(-(o2 / o1 - 1.0))  # 비용 = -수익률
                lt.append(i)
            if len(lt) >= SPO_MIN_STOCKS:
                c_vec = np.array(c_list, dtype=float)
                label_tickers = lt

        sample: dict = {
            "month_end": m,
            "tickers": tickers,
            "X_full": x,
        }
        if c_vec is not None:
            xl = x[label_tickers]
            z_star, w_star = capped_simplex_argmin(c_vec)
            sample.update({
                "X": xl, "c": c_vec, "z_star": z_star, "w_star": w_star,
                # 레이블 완결 시점 = 레이블 종료 시가일 (다다음달 첫 거래일)
                "label_end": month_firsts[k + 2],
            })
        panel.append(sample)
    return panel


# ──────────────────────────────────────────────────────────────────────────────
# 워크포워드 스케줄 계산 (메인 API)
# ──────────────────────────────────────────────────────────────────────────────

def compute_spo_weight_schedules(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    ticker_reports: dict[str, list[dict]],
    verbose: bool = True,
) -> tuple[dict[str, dict[dt.date, dict[str, float]]], dict]:
    """SPO+/LS 양쪽의 {월말일 → {티커: 비중}} 스케줄과 메타데이터 반환.

    워크포워드: 각 월말 m에서 레이블이 완결된(다다음달 시가 ≤ m) 과거 횡단면만으로
    학습 (확장 윈도우, 최소 SPO_MIN_TRAIN_MONTHS) → m 시점 피처로 예측 →
    캡 심플렉스 오라클로 목표 비중. 체결은 다음달 첫 거래일 시가 (실행기 담당).
    """
    panel = build_panel(prices, reports, calendar, ticker_reports)
    p = len(FEATURE_NAMES) + 1  # +절편
    schedules: dict[str, dict[dt.date, dict[str, float]]] = {"spo_plus": {}, "ls": {}}
    lam_history: dict[str, list[float]] = {"spo_plus": [], "ls": []}
    realized: dict[str, list[tuple[dt.date, float, float]]] = {"spo_plus": [], "ls": []}
    first_rebalance: dt.date | None = None
    n_train_final = 0

    if verbose:
        print(f"  SPO panel: {len(panel)} monthly cross-sections "
              f"({panel[0]['month_end']} ~ {panel[-1]['month_end']})" if panel else
              "  SPO panel: EMPTY", flush=True)

    for k, sample in enumerate(panel):
        m = sample["month_end"]
        # 레이블 완결: 레이블 종료 시가일 ≤ m (point-in-time — look-ahead 차단)
        train = [s for s in panel[:k] if "c" in s and s["label_end"] <= m]
        if len(train) < SPO_MIN_TRAIN_MONTHS:
            continue
        if first_rebalance is None:
            first_rebalance = m
        n_train_final = len(train)

        for method in ("spo_plus", "ls"):
            rng = np.random.default_rng(SPO_SEED + k)  # 메서드 동일 시드 — 동일 검증 분할
            b, lam = train_with_validation(train, p, rng, method)
            lam_history[method].append(lam)
            c_hat = sample["X_full"] @ b
            _, w = capped_simplex_argmin(c_hat)
            schedules[method][m] = {
                sample["tickers"][i]: round(float(w[i]), 6)
                for i in range(len(w)) if w[i] > 1e-4
            }
            # 사후 실현 통계 (레이블 가용 시): 실현 월수익률·리그렛 — 리포트용 근사
            # X/c는 레이블 가능 티커 부분집합 — 해당 부분집합으로 의사결정 재평가
            if "c" in sample:
                c_hat_l = sample["X"] @ b
                _, w_l = capped_simplex_argmin(c_hat_l)
                ret_m = -float(sample["c"] @ w_l)
                regret_m = float(sample["c"] @ w_l) - sample["z_star"]
                realized[method].append((m, ret_m, regret_m))

        if verbose and (len(lam_history["spo_plus"]) % 12 == 1):
            print(f"  SPO walk-forward {m}: train={len(train)}mo "
                  f"λ_spo={lam_history['spo_plus'][-1]:.4g} λ_ls={lam_history['ls'][-1]:.4g}",
                  flush=True)

    def _realized_summary(rows: list[tuple[dt.date, float, float]]) -> dict:
        if not rows:
            return {}
        is_rows = [r for r in rows if r[0] <= dt.date(2023, 12, 31)]
        oos_rows = [r for r in rows if r[0] >= dt.date(2024, 1, 1)]

        def agg(rr):
            if not rr:
                return None
            return {
                "avg_monthly_return_pct": round(float(np.mean([x[1] for x in rr])) * 100, 3),
                "avg_monthly_regret_pct": round(float(np.mean([x[2] for x in rr])) * 100, 3),
                "months": len(rr),
            }
        return {"full": agg(rows), "is": agg(is_rows), "oos": agg(oos_rows)}

    meta = {
        "paper": "Elmachtoub & Grigas, Smart 'Predict, then Optimize', Management Science 68(1), 2022",
        "reference_impl": "github.com/paulgrigas/SmartPredictThenOptimize (Julia: solver/sgd.jl, solver/validation_set.jl)",
        "features": FEATURE_NAMES,
        "decision_problem": "max r·w s.t. Σw=1, 0≤w≤0.15 (캡 심플렉스 LP — 닫힌형 그리디 오라클)",
        "hyperparams": {
            "numiter": SPO_NUMITER, "batchsize": SPO_BATCHSIZE,
            "step": f"λ=0: long_dynamic {SPO_LONG_FACTOR}/sqrt(iter+1) · λ>0: short 2/(λ(iter+2))",
            "num_lambda": SPO_NUM_LAMBDA, "lambda_min_ratio": SPO_LAMBDA_MIN_RATIO,
            "validation_pct": SPO_VALIDATION_PCT,
            "min_train_months": SPO_MIN_TRAIN_MONTHS,
            "weight_cap": SPO_WEIGHT_CAP,
            "seed": SPO_SEED,
        },
        "mirrored_from_julia": (
            "SPO+ 확률적 서브그래디언트 식·batchsize 10·numiter 1000·"
            "스텝 사이즈(λ=0: long_dynamic 0.1/√t [sgd.jl 기본], λ>0: short 2/(λ(t+2)) [경로 알고리즘 기본])·"
            "지연 스텝가중 평균 이터레이트·"
            "릿지 경로(λ_max=(d/n)‖X‖²_F, 로그그리드, 워름스타트)·"
            "검증셋 20% 무작위 + 검증 SPO 손실 argmin 선택"
        ),
        "adaptations": (
            "LS 베이스라인은 동일 릿지 목적식의 닫힌형 정확해 (SGD 오차 제거 — 베이스라인에 유리). "
            "num_lambda 10 (레포 100), λ_min_ratio 1e-6 (레포 1e-4) + λ=0 후보, "
            "경로 반복수는 레포의 정확도 기반 공식 대신 고정 1000회. "
            "워크포워드 확장 윈도우 매월 재학습 (레포는 단일 실험 분할). "
            "유니버스가 매월 변해 계수 b를 자산 간 공유 (컨텍스추얼 적응). "
            "피처 횡단면 z-score 표준화 + 절편."
        ),
        "panel_months": len(panel),
        "first_rebalance": first_rebalance.isoformat() if first_rebalance else None,
        "n_rebalances": len(schedules["spo_plus"]),
        "n_train_months_final": n_train_final,
        "lambda_selected_last": {
            m_: (lam_history[m_][-1] if lam_history[m_] else None)
            for m_ in ("spo_plus", "ls")
        },
        "realized_decision_stats": {
            m_: _realized_summary(realized[m_]) for m_ in ("spo_plus", "ls")
        },
    }
    if verbose:
        print(f"  SPO schedules: {meta['n_rebalances']} rebalances, "
              f"first={meta['first_rebalance']}, final train={n_train_final}mo", flush=True)
    return schedules, meta


# ──────────────────────────────────────────────────────────────────────────────
# 스모크 테스트 (단독 실행): 스케줄 계산만 — 실행기/NAV는 backtest_momentum 담당
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import backtest_momentum as bt

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("SPO smoke test: loading data...", flush=True)
    perf_all = pd.read_csv(bt.REPORT_PERFORMANCE_CSV, encoding="utf-8-sig")
    perf = perf_all[
        perf_all["ticker"].notna()
        & perf_all["report_date"].notna()
        & (perf_all["rating_class"] == "buy")
        & (perf_all["report_date"] >= bt.UNIVERSE_START.isoformat())
    ].copy()
    perf["ticker_key"] = perf.apply(
        lambda row: str(row["ticker"]).zfill(6) if str(row.get("market", "KR")) == "KR" else str(row["ticker"]),
        axis=1,
    )
    ticker_reports = bt.build_ticker_reports(perf)
    club_count = perf.groupby("ticker_key")["school"].nunique().to_dict()
    prices: dict[str, pd.DataFrame] = {}
    for _, row in perf.iterrows():
        tk = row["ticker_key"]
        if tk not in prices:
            df = bt.load_prices(str(row["ticker"]), str(row.get("market", "KR")))
            if df is not None:
                prices[tk] = df
    reports = []
    for _, row in perf.iterrows():
        tk = row["ticker_key"]
        if tk not in prices:
            continue
        try:
            rdate = dt.date.fromisoformat(str(row["report_date"]))
        except ValueError:
            continue
        reports.append((rdate, tk, str(row.get("source_file", "")), club_count.get(tk, 1)))
    reports.sort()
    raw_calendar = sorted({ts.date() for df in prices.values() for ts in df.index})
    calendar = [d for d in raw_calendar if d >= bt.SIM_START]
    print(f"  {len(reports)} reports, {len(prices)} tickers, calendar {calendar[0]}~{calendar[-1]}", flush=True)

    import time
    t0 = time.time()
    schedules, meta = compute_spo_weight_schedules(prices, reports, calendar, ticker_reports)
    print(f"  elapsed {time.time()-t0:.1f}s", flush=True)
    print(f"  realized stats: {meta['realized_decision_stats']}", flush=True)
    for method in ("spo_plus", "ls"):
        sch = schedules[method]
        if sch:
            last_m = max(sch)
            top = sorted(sch[last_m].items(), key=lambda kv: -kv[1])[:5]
            print(f"  {method} last rebalance {last_m}: {len(sch[last_m])} names, top={top}", flush=True)
