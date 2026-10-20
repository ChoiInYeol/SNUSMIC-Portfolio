"""Writer-produced sample of the funded-account Gold artifact for the TS (zod) boundary test.

The real artifact only exists after the operator starts the account, so the Node contract test reads
this deterministic synthetic replay instead. Regenerate with UPDATE_GOLDEN=1 after a schema change.
"""
import datetime as dt
import json
import os
from pathlib import Path

import market_calendars as cal
from test_funded_account import at, frame, gold, lake_tmp, run  # noqa: F401  (fixture re-export)

FIXTURE = Path(__file__).parent / "fixtures" / "funded-account.json"


def scenario():
    crash = {"2026-10-23": {"close": 8_000.0}, "2026-10-26": {"open": 0.0, "volume": 0.0, "close": 8_000.0},
             "2026-10-27": {"open": 7_900.0, "close": 7_900.0}}
    prices = lambda: {"005930": frame("KR", 10_000.0, crash), "AAPL": frame("US", 100.0, {"2026-10-28": {"close": 110.0}}),
                      "000660": frame("KR", 50_000.0)}
    day = dt.date(2026, 10, 20)
    while day <= dt.date(2026, 11, 3):
        run(at(day.isoformat()), prices=prices())
        day = cal.next_trading_day(day, "KR")
    run(at("2026-11-03", dt.time(12, 0)), prices=prices(), reports=[(dt.date(2026, 11, 3), "000660", "2026-11-03_hynix.md", 2)])
    return gold()


def test_sample_artifact_is_current(lake_tmp):  # noqa: F811
    doc = scenario()
    perf = doc["performance"]
    assert perf["principal_krw"] == 52_000_000 and perf["installments_applied"] == 1
    assert {f["side"] for f in doc["fills"] if f["status"] == "filled"} == {"buy", "sell"}
    assert any(f["reason_code"] == "halt_or_missing_open" for f in doc["fills"])
    assert doc["latest_plans"]["KR"]["orders"][0]["reason_code"] == "new_report"
    text = json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        FIXTURE.write_text(text, encoding="utf-8", newline="\n")
    assert FIXTURE.read_text(encoding="utf-8") == text, "stale fixture: rerun with UPDATE_GOLDEN=1"


SEEDED = Path(__file__).parent / "fixtures" / "funded-account-seeded.json"


def test_seeded_sample_artifact_is_current(lake_tmp):  # noqa: F811
    from test_funded_account_seed import seeded
    doc, _ = seeded(lake_tmp, at("2026-10-20", dt.time(11, 0)))
    for day in ("2026-10-20", "2026-10-30", "2026-11-02"):
        run(at(day))
    out = gold()
    out["seed"]["artifact"] = "tests/fixtures/funded-backtest-sample.json"  # tmp path → stable placeholder
    assert out["segments"]["backtest"]["run_id"] == doc["run_id"] and out["performance"]["scope"] == "combined_backtest_and_live"
    text = json.dumps(out, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        SEEDED.write_text(text, encoding="utf-8", newline="\n")
    assert SEEDED.read_text(encoding="utf-8") == text, "stale fixture: rerun with UPDATE_GOLDEN=1"
