"""Forward track record — 전진 기록 채점기.

백테스트가 아니라 기록이다. 매일 CI가 박제해 둔 신호 스냅샷
(public/api/v1/signals/{YYYY-MM-DD}.json — append-only, v21부터)을 읽어,
그날 공표된 매수 신호를 이후의 실현 시세로 채점한다. 스냅샷은 미래를 보기
전에 커밋되었으므로 구조적으로 out-of-sample이다.

채점 규칙 (단순·보수적, 전략 규칙 재현이 아니라 신호 자체의 성적):
  * 진입 = 신호일(as_of) 다음 거래일 시가. 시가 없으면 그날 종가.
  * 수익률 = 현재 종가 / 진입가 − 1 (로컬 통화 — US는 USD 기준).
  * 피크 = 진입 후 최고 종가 기준 수익률.
  * 중복 제거 = 같은 티커가 7일 내 반복 신호되면 첫 신호만 기록.

산출:
  src/data/forward-record.json   — /track 페이지 데이터
  public/api/v1/forward.json     — 공개 API 미러

Usage:
    python scripts/build_forward_record.py
"""

from __future__ import annotations

import datetime as dt
import json
import re
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SIGNALS_DIR = ROOT / "public" / "api" / "v1" / "signals"
sys.path.insert(0, str(ROOT / "scripts"))
from lake import SILVER_PRICES as PRICE_DIR  # noqa: E402
OUT_INTERNAL = ROOT / "src" / "data" / "forward-record.json"
OUT_API = ROOT / "public" / "api" / "v1" / "forward.json"

DEDUP_DAYS = 7   # 같은 티커 반복 신호 무시 윈도
SNAPSHOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")



def _load_price(ticker: str, market: str) -> pd.DataFrame | None:
    prefix = "US_" if market == "US" else "KR_"
    path = PRICE_DIR / f"{prefix}{ticker}.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    except Exception:
        return None
    if df.empty or "close" not in df.columns:
        return None
    return df[~df.index.duplicated(keep="last")]


def _finite_positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _base_entry(sig: dict, as_of: dt.date, status: str) -> dict:
    return {
        "signal_date": as_of.isoformat(), "ticker": sig.get("ticker", ""),
        "market": sig.get("market", "KR"), "name": sig.get("name", sig.get("ticker", "")),
        "basis_price": sig.get("entry_basis_price"), "entry_date": None,
        "entry_price": None, "current_price": None, "return_pct": None,
        "peak_pct": None, "days": 0, "status": status,
        "trigger_schools": sorted({t.get("school", "") for t in sig.get("trigger_reports", [])}),
    }


def _score_entry(sig: dict, as_of: dt.date) -> dict:
    ticker, market = sig.get("ticker", ""), sig.get("market", "KR")
    df = _load_price(ticker, market)
    if df is None:
        return _base_entry(sig, as_of, "unavailable")
    after = df[df.index > pd.Timestamp(as_of)]
    if after.empty:
        return _base_entry(sig, as_of, "pending")
    row = after.iloc[0]
    entry_price = _finite_positive(row.get("open")) or _finite_positive(row.get("close"))
    closes = pd.to_numeric(after["close"], errors="coerce")
    closes = closes[closes.map(lambda x: math.isfinite(float(x)) and float(x) > 0)]
    if entry_price is None or closes.empty:
        return _base_entry(sig, as_of, "unavailable")
    current, peak = _finite_positive(closes.iloc[-1]), _finite_positive(closes.max())
    if current is None or peak is None:
        return _base_entry(sig, as_of, "unavailable")
    result = _base_entry(sig, as_of, "tracking")
    entry_date = after.index[0].date()
    result.update({"entry_date": entry_date.isoformat(), "entry_price": round(entry_price, 4),
                   "current_price": round(current, 4), "return_pct": round((current / entry_price - 1) * 100, 2),
                   "peak_pct": round((peak / entry_price - 1) * 100, 2),
                   "days": (closes.index[-1].date() - entry_date).days})
    return result


def main() -> int:
    snapshots = sorted(
        p for p in SIGNALS_DIR.glob("*.json") if SNAPSHOT_RE.match(p.name)
    )
    if not snapshots:
        print("No dated snapshots yet — nothing to score.", file=sys.stderr)
        return 1

    entries: list[dict] = []
    recent_by_key: dict[tuple[str, str], dt.date] = {}
    headline_by_date: dict[str, str] = {}

    for snap_path in snapshots:
        try:
            snap = json.loads(snap_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  skip {snap_path.name}: {e}", file=sys.stderr)
            continue
        as_of_str = snap.get("as_of") or snap_path.stem
        as_of = dt.date.fromisoformat(as_of_str)
        headline_by_date[as_of_str] = snap.get("headline_strategy", "")

        for sig in snap.get("buy_signals", []):
            ticker = sig.get("ticker", "")
            if not ticker:
                continue
            market = sig.get("market", "KR")
            key = (market, ticker)
            last = recent_by_key.get(key)
            if last is not None and (as_of - last).days < DEDUP_DAYS:
                continue
            scored = _score_entry(sig, as_of)
            recent_by_key[key] = as_of
            scored["headline_strategy"] = snap.get("headline_strategy", "")
            entries.append(scored)

    tracked = [e for e in entries if e["status"] == "tracking"]
    rets = [e["return_pct"] for e in tracked if e["return_pct"] is not None]
    summary = {
        "n_signals": len(entries),
        "n_tracking": len(tracked),
        "n_unavailable": sum(1 for e in entries if e["status"] == "unavailable"),
        "n_pending": sum(1 for e in entries if e["status"] == "pending"),
        "coverage_pct": round(len(tracked) / len(entries) * 100, 1) if entries else None,
        "avg_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
        "win_rate_pct": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1) if rets else None,
        "best_pct": max(rets) if rets else None,
        "worst_pct": min(rets) if rets else None,
        "first_snapshot": snapshots[0].stem,
        "last_snapshot": snapshots[-1].stem,
        "n_snapshots": len(snapshots),
    }

    payload = {
        "schema_version": "1.0",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "method": (
            "신호 스냅샷(append-only, 커밋 시점 박제)의 매수 신호를 이후 실현 시세로 채점. "
            f"진입 = 신호일 익일 시가, 수익률 = 로컬 통화 종가 기준, 시장+티커 {DEDUP_DAYS}일 내 중복 신호 제외. "
            "전략 규칙(스탑·슬롯) 재현이 아니라 공표된 신호 자체의 전진 성적; 가격 미가용은 unavailable로 보존."
        ),
        "provenance": {
            "first_public_commit": "unknown",
            "first_public_at": "unknown",
            "publication_commit": "unknown",
            "publication_at": "unknown",
            "coverage": "unknown",
        },
        "summary": summary,
        "entries": sorted(entries, key=lambda e: e["signal_date"], reverse=True),
        "headline_by_date": headline_by_date,
    }

    for out in (OUT_INTERNAL, OUT_API):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
        print(f"  wrote {out.relative_to(ROOT).as_posix()}")

    print(f"Forward record: {summary['n_signals']} signals over {summary['n_snapshots']} snapshots "
          f"(since {summary['first_snapshot']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
