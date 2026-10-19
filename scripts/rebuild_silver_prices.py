# -*- coding: utf-8 -*-
"""Rebuild silver prices (data/silver/prices/*.csv) by replaying bronze raw price files.

Each symbol's fetches are replayed in `fetched_at` order (stable, so rows of one fetch keep their order);
for a date fetched more than once the latest fetch wins. File names are not used for ordering. Each vendor frame gets the same
normalisation the live fetchers apply, then goes through lake_prices.write_silver_prices, so the
validation and quarantine rules are identical to a pipeline run.

Silver files written by scripts/backtest/warehouse.py keep only `close` for yfinance indices; a replay
writes every OHLCV column the vendor returned. Readers select columns by name, so this is additive.

Usage:
    python scripts/rebuild_silver_prices.py                         # replay all bronze into data/silver/prices
    python scripts/rebuild_silver_prices.py --out-dir cache/silver  # replay elsewhere (to diff against silver)
    python scripts/rebuild_silver_prices.py --symbols KR_005930 IDX_KOSPI
    python scripts/rebuild_silver_prices.py --bootstrap-from-silver # one-time: snapshot current silver into bronze
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lake import BRONZE_PRICES, SILVER_PRICES, SILVER_REJECTS, rel  # noqa: E402
from lake_prices import PRICE_COLUMNS, BronzePriceRecorder, write_silver_prices  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LEGACY_VENDOR = "legacy_import"


def normalise(vendor: str, raw: pd.DataFrame) -> pd.DataFrame:
    """Raw vendor rows (date index, vendor columns) → OHLCV frame, as the live fetchers produce it.

    Cells stay as the text bronze stored, so silver gets the vendor's exact digits (parsing and
    re-printing floats can move the last bit). write_silver_prices validates them numerically.
    """
    if vendor == "pykrx_stock":  # 시가, 고가, 저가, 종가, 거래량[, 등락률] — positional, as fetch_kr_prices
        out = raw.iloc[:, :5].set_axis(list(PRICE_COLUMNS[: min(5, raw.shape[1])]), axis=1)
        return out.dropna(subset=["close"])
    if vendor == "pykrx_index":  # close is the 4th column, as fetch_index
        return raw.iloc[:, [3]].set_axis(["close"], axis=1).dropna(subset=["close"])
    if vendor in ("yfinance", LEGACY_VENDOR):
        out = raw.rename(columns=lambda c: str(c).lower())
        return out[[c for c in PRICE_COLUMNS if c in out.columns]]
    raise ValueError(f"no replay rule for vendor {vendor!r}")


def bronze_files(root: Path) -> list[tuple[str, Path]]:
    """(vendor, file) for every bronze price file; replay order comes from fetched_at, not names."""
    return sorted((path.parent.parent.name, path) for path in root.glob("*/ingest_date=*/*.csv.gz"))


def replay(root: Path, symbols: set[str] | None) -> dict[str, list[pd.DataFrame]]:
    """Normalised frames per symbol, each carrying the fetch time in `_fetched_at`."""
    frames: dict[str, list[pd.DataFrame]] = {}
    for vendor, path in bronze_files(root):
        raw = pd.read_csv(path, dtype=str)
        date_col = raw.columns[1]  # recorder layout: symbol, <vendor index>, <vendor columns>..., fetched_at
        for symbol, group in raw.groupby("symbol", sort=False):
            if symbols and symbol not in symbols:
                continue
            body = group.drop(columns=["symbol"]).set_index(date_col)
            body.index = pd.to_datetime(body.index)
            fetched_at = body.pop("fetched_at")
            out = normalise(vendor, body.dropna(axis=1, how="all"))
            out["_fetched_at"] = pd.to_datetime(fetched_at, utc=True, format="ISO8601").reindex(out.index)
            frames.setdefault(symbol, []).append(out)
    return frames


def in_fetch_order(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate one symbol's fetches oldest first (stable), so the newest fetch wins a date."""
    combined = pd.concat(frames)
    return combined.sort_values("_fetched_at", kind="mergesort").drop(columns="_fetched_at")


def bootstrap_from_silver(silver_dir: Path, root: Path) -> list[Path]:
    """Snapshot every silver CSV into bronze as vendor `legacy_import` (no fetch history exists for them)."""
    if (root / LEGACY_VENDOR).exists():
        raise SystemExit(f"{rel(root / LEGACY_VENDOR)} already exists — the legacy snapshot is one-time")
    recorder = BronzePriceRecorder(root=root)
    for path in sorted(silver_dir.glob("*.csv")):
        df = pd.read_csv(path, index_col=0, dtype=str)  # text as stored: no float re-printing
        df.index.name = "date"
        mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat(timespec="seconds")
        recorder.record(LEGACY_VENDOR, path.stem, df, library="pandas", fetched_at=mtime, request={
            "source": rel(path),
            "start": str(df.index[0]) if len(df) else None,
            "end": str(df.index[-1]) if len(df) else None,
            "adjusted": None,
        })
    return recorder.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bronze-dir", type=Path, default=BRONZE_PRICES)
    parser.add_argument("--out-dir", type=Path, default=SILVER_PRICES)
    parser.add_argument("--symbols", nargs="+", default=None, help="silver keys, e.g. KR_005930 IDX_KOSPI")
    parser.add_argument("--bootstrap-from-silver", action="store_true",
                        help="one-time: write the current silver files to bronze as vendor legacy_import")
    args = parser.parse_args()

    if args.bootstrap_from_silver:
        for path in bootstrap_from_silver(SILVER_PRICES, args.bronze_dir):
            print(f"wrote {path} ({path.stat().st_size / 1e6:.1f} MB)")
        return 0

    frames = replay(args.bronze_dir, set(args.symbols) if args.symbols else None)
    if not frames:
        print(f"no bronze price files under {args.bronze_dir}", file=sys.stderr)
        return 1
    rejects_dir = SILVER_REJECTS if args.out_dir.resolve() == SILVER_PRICES.resolve() else args.out_dir / "_rejects"
    for symbol in sorted(frames):
        out = write_silver_prices(symbol, in_fetch_order(frames[symbol]), out_dir=args.out_dir,
                                  rejects_dir=rejects_dir)
        print(f"{symbol}: {len(out)} rows")
    print(f"rebuilt {len(frames)} silver price files in {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
