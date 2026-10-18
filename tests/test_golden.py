"""골든 스냅샷 회귀 게이트.

고정 시드 합성 유니버스에서 대표 전략 3종(고정보유 / 샹들리에 / 포트폴리오
최적화 HRP)을 구동하고, 산출물(지표·거래 로그)을 커밋된 골든 JSON과 비교한다.

의도된 로직 변경으로 골든을 갱신하려면:
    UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py
갱신된 tests/golden/strategies.json 을 diff로 확인 후 함께 커밋할 것.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any

import pytest

import backtest_momentum as bt
from conftest import GOLDEN_DIR

GOLDEN_PATH = GOLDEN_DIR / "strategies.json"
TRADE_FIELDS = ("ticker", "market", "entry_date", "exit_date", "entry", "exit",
                "return_pct", "days", "exit_reason")


def _snapshot(result: dict) -> dict:
    nav_df = result["nav_df"]
    return {
        "metrics": result["metrics"],
        "in_sample": result["in_sample"],
        "out_of_sample": result["out_of_sample"],
        "final_nav_ratio": round(float(nav_df.iloc[-1] / nav_df.iloc[0]), 6),
        "n_trades": len(result["trades"]),
        "trades": [{k: t.get(k) for k in TRADE_FIELDS} for t in result["trades"]],
    }


def _assert_close(actual: Any, expected: Any, path: str) -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: dict 기대, {type(actual).__name__}"
        assert set(actual) == set(expected), (
            f"{path}: 키 불일치 +{set(actual) - set(expected)} -{set(expected) - set(actual)}"
        )
        for k in expected:
            _assert_close(actual[k], expected[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert len(actual) == len(expected), f"{path}: 길이 {len(actual)} != {len(expected)}"
        for i, (a, e) in enumerate(zip(actual, expected)):
            _assert_close(a, e, f"{path}[{i}]")
    elif isinstance(expected, float) and not isinstance(expected, bool):
        assert isinstance(actual, (int, float)) and math.isclose(
            actual, expected, rel_tol=1e-9, abs_tol=1e-6
        ), f"{path}: {actual} != {expected}"
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


@pytest.fixture(scope="module")
def golden_runs(universe) -> dict[str, dict]:
    # FX 원복은 conftest의 autouse _restore_fx_state 픽스처가 담당한다
    # (bt._USDKRW 재대입은 shim 네임스페이스만 바꾸는 무효 동작이라 제거).
    bt.set_usdkrw(universe["usdkrw"])
    prices, reports, calendar, tr = (
        universe["prices"], universe["reports"], universe["calendar"],
        universe["ticker_reports"],
    )
    cal_s_months = sorted({(d.year, d.month) for d in calendar})
    runs = {
        "fixed_hold_12mo": bt.run_fixed_hold(prices, reports, calendar, 12, "고정12", tr),
        "chandelier": bt.run_chandelier(prices, reports, calendar, "샹들리에", tr),
        "portfolio_hrp": bt.run_portfolio_opt(
            prices, reports, calendar, "HRP", variant="hrp", ticker_reports=tr,
        ),
    }
    assert len(cal_s_months) >= 24  # 유니버스 기간 sanity
    return runs


def test_golden_snapshot(golden_runs):
    snap = {k: _snapshot(v) for k, v in golden_runs.items()}

    if os.environ.get("UPDATE_GOLDEN") == "1":
        if os.environ.get("CI", "").lower() in {"1", "true"}:
            pytest.fail("CI cannot update or skip the golden regression gate")
        GOLDEN_DIR.mkdir(exist_ok=True)
        GOLDEN_PATH.write_text(
            json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        pytest.skip(f"golden updated: {GOLDEN_PATH}")

    assert GOLDEN_PATH.exists(), (
        "골든 파일이 없습니다. UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py 로 생성 후 커밋하세요."
    )
    expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    _assert_close(snap, expected, "snapshot")


def test_golden_universe_actually_trades(golden_runs):
    """골든이 무의미한 0-거래 스냅샷으로 퇴화하지 않았는지 보증."""
    for key, result in golden_runs.items():
        closed = [t for t in result["trades"] if "미청산" not in t["exit_reason"]]
        assert len(closed) >= 3, f"{key}: 청산 거래 {len(closed)}건 — 유니버스 점검"
    # US 종목이 실제로 거래에 등장 (FX 경로 활성 보증)
    all_trades = [t for r in golden_runs.values() for t in r["trades"]]
    assert any(t["market"] == "US" for t in all_trades), "US 거래 없음 — FX 경로 미검증"
