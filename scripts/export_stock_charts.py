"""종목 페이지용 가격 차트 데이터를 public/api/v2/instruments/{id}/prices.json으로 내보낸다.

- 소스: data/prices/{MARKET}_{ticker}.csv (build_report_performance.py가 채운 캐시)
- 일별 OHLCV 캔들 전체를 emits한다 (주간 다운샘플 없음).
- slug 규칙은 웹과 동일: {market}-{ticker} 소문자 (예: kr-252990)

JSON shape (REST API v2 envelope, schema PriceSeriesDocument in public/api/v2/openapi.json):
  {
    "schema_version": "2.0",
    "links": {"self": "/api/v2/instruments/kr-252990/prices", "instrument": "/api/v2/instruments/kr-252990"},
    "meta": {"generated_at": "...", "as_of": "<last candle date>", "count": <candles>, "disclaimer": "...", ...},
    "data": {
      "candles": [
        {"time": "YYYY-MM-DD", "open": 1234.0, "high": 1300.0, "low": 1200.0, "close": 1280.0, "volume": 123456},
        ...
      ],
      "report_dates": ["YYYY-MM-DD", ...]   // dates where a report was published for this ticker
    }
  }

  - 이 파일들의 존재가 v2 instrument 리소스 집합을 정의한다 (export_api_v2.py가 읽음). 쓰기 후 스테일 파일 prune.
  - 내용이 같으면 파일을 다시 쓰지 않는다 (generated_at 유지 → 무변경 재실행은 diff 0).

  - open/high/low/close/volume 값이 NaN이거나 소스에 열이 없는 경우 해당 필드를 null 로 emit.
  - 모든 float 값은 소수 4자리로 반올림.
  - volume 이 없거나 0인 경우에도 레코드는 유지한다.

사용:
    python scripts/export_stock_charts.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lake import SILVER_PRICES as PRICE_DIR  # noqa: E402
import export_api_v2 as api_v2  # noqa: E402
from export_signals_api import _comparable  # noqa: E402
OUT_DIR = api_v2.API / "instruments"

# A healthy run prunes a handful of series at most; more means a cold or broken price cache.
MAX_PRUNE_FRACTION = 0.05

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class PruneRefused(RuntimeError):
    pass


def prune_price_series(out_dir: Path, fresh: set[Path], keep: set[str], price_dir: Path) -> tuple[list[Path], list[Path]]:
    """Delete published price series that are no longer produced. Returns (deleted, retained).

    Retained: instruments linked from frozen signal snapshots (`keep`), whose last document stays
    so those links never break. Refused (nothing deleted): an empty silver price dir, or pruning
    more than MAX_PRUNE_FRACTION of the published series — both mean a cold or failed cache.
    """
    existing = sorted(out_dir.glob("*/prices.json"))
    stale = [p for p in existing if p not in fresh]
    retained = [p for p in stale if p.parent.name in keep]
    doomed = [p for p in stale if p.parent.name not in keep]
    if doomed and not any(price_dir.glob("*.csv")):
        raise PruneRefused(f"{price_dir} has no price files (cold cache?): refusing to prune {len(doomed)} "
                           "published series")
    if existing and len(doomed) > MAX_PRUNE_FRACTION * len(existing):
        raise PruneRefused(f"refusing to prune {len(doomed)} of {len(existing)} published series "
                           f"(> {MAX_PRUNE_FRACTION:.0%}); check the silver price cache, e.g. "
                           f"{', '.join(p.parent.name for p in doomed[:5])}")
    for old in doomed:
        old.unlink()
    return doomed, retained


def _round_or_null(value: object) -> float | None:
    """float → 소수 4자리 반올림; NaN/None → None."""
    if value is None:
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return round(f, 4)


def _int_or_null(value: object) -> int | None:
    if value is None:
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return int(f)


def main() -> int:
    dataset = json.loads((ROOT / "src" / "data" / "report-performance.json").read_text(encoding="utf-8"))

    # Build slug → (market, ticker) and slug → set of report dates
    slugs: dict[str, tuple[str, str]] = {}
    slug_report_dates: dict[str, set[str]] = {}
    for record in dataset["records"]:
        if record.get("ticker") and record.get("market"):
            slug = f"{record['market']}-{record['ticker']}".lower()
            slugs[slug] = (record["market"], record["ticker"])
            date = record.get("report_date") or record.get("filename_date")
            if date:
                slug_report_dates.setdefault(slug, set()).add(date)

    now = api_v2._now()
    fresh: set[Path] = set()
    written = 0
    for slug, (market, ticker) in sorted(slugs.items()):
        csv_path = PRICE_DIR / f"{market}_{ticker}.csv"
        if not csv_path.exists():
            continue
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True).sort_index()
        if df.empty or "close" not in df.columns:
            continue
        df = df[~df.index.duplicated(keep="last")]

        # Determine available OHLCV columns
        has_open = "open" in df.columns
        has_high = "high" in df.columns
        has_low = "low" in df.columns
        has_volume = "volume" in df.columns

        candles: list[dict] = []
        for ts, row in df.iterrows():
            close_val = _round_or_null(row["close"])
            if close_val is None:
                continue  # skip rows where close is missing
            candle: dict = {
                "time": ts.date().isoformat(),  # type: ignore[union-attr]
                "open": _round_or_null(row["open"]) if has_open else None,
                "high": _round_or_null(row["high"]) if has_high else None,
                "low": _round_or_null(row["low"]) if has_low else None,
                "close": close_val,
                "volume": _int_or_null(row["volume"]) if has_volume else None,
            }
            candles.append(candle)

        if len(candles) < 2:
            continue

        report_dates = sorted(slug_report_dates.get(slug, set()))
        uri = api_v2.href("instruments", slug, "prices")
        payload = api_v2.document(uri, {"candles": candles, "report_dates": report_dates}, generated_at=now,
                                  as_of=candles[-1]["time"], count=len(candles),
                                  links={"instrument": api_v2.href("instruments", slug)})
        out = OUT_DIR / slug / "prices.json"
        fresh.add(out)
        if out.exists() and _comparable(json.loads(out.read_text(encoding="utf-8"))) == _comparable(payload):
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                       encoding="utf-8")
        written += 1
    # Write-then-prune: a price series that is no longer produced removes its instrument resource.
    try:
        pruned, retained = prune_price_series(OUT_DIR, fresh, set(api_v2.frozen_instrument_refs()), PRICE_DIR)
    except PruneRefused as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"price series: {len(fresh)} published ({written} rewritten, {len(pruned)} pruned, "
          f"{len(retained)} retained for frozen snapshots) -> public/api/v2/instruments/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
