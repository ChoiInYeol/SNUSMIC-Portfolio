# -*- coding: utf-8 -*-
"""Write data/catalog.json: every dataset's layer, location, producer, inputs and size.

The catalog is the lineage map of the medallion layout (docs/DATA-LAYERS.md): any gold or serving
file can be traced through `inputs` to its silver and bronze sources.

Output is deterministic so it can be committed and drift-checked:
- entries and keys are sorted; `bytes` counts text files with LF line endings (checkouts differ);
- `updated_at` comes only from timestamps inside the data (generated_at / as_of / checked_at, or the
  newest row timestamp), never from file mtimes;
- datasets that are git-ignored (PDFs, raw and clean price files, caches) differ per machine, so their
  rows/files/bytes/updated_at are null. Their volume is visible through tracked datasets
  (bronze.report_manifest counts PDFs).

Usage:
    python scripts/build_catalog.py           # write data/catalog.json
    python scripts/build_catalog.py --check   # exit 1 if data/catalog.json is stale
    python scripts/build_catalog.py --tracked-pathspecs   # git pathspecs of every committed dataset
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lake  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CATALOG_SCHEMA_VERSION = "1.0"
TEXT_SUFFIXES = {".json", ".jsonl", ".csv", ".md"}
COLLECTORS = ["collect_ewha.py", "collect_reports.py", "collect_smic.py", "collect_voera.py"]


def _d(id_, layer, path, fmt, producer, inputs=(), schema_file=None):
    return {"id": id_, "layer": layer, "path": path, "format": fmt, "producer": producer,
            "inputs": sorted(inputs), "schema_file": schema_file}


R = lake.rel
DATASETS = [
    _d("bronze.paper_execution", "bronze", f"{R(lake.BRONZE_PAPER_EXECUTION)}/*.json", "json",
       ["paper_execution.py"]),
    _d("serving.strategy_robustness", "gold", R(lake.STRATEGY_ROBUSTNESS), "json", ["build_strategy_robustness.py"],
       ["serving.strategy_backtest", "silver.prices", "gold.report_performance"]),
    _d("bronze.paper_targets", "bronze", f"{R(lake.BRONZE_PAPER_TARGETS)}/*.json", "json",
       ["paper_portfolio.py"], schema_file=None),
    _d("silver.paper_ledger", "silver", R(lake.SILVER_PAPER_LEDGER), "json", ["paper_portfolio.py"],
       ["bronze.paper_execution"]),
    _d("serving.paper_portfolio", "gold", R(lake.PAPER_PORTFOLIO), "json", ["paper_portfolio.py"], ["silver.paper_ledger"]),
    _d("bronze.funded_account", "bronze", f"{R(lake.BRONZE_FUNDED)}/**/*.json", "json", ["funded_account.py"]),
    _d("silver.funded_ledger", "silver", R(lake.SILVER_FUNDED_LEDGER), "json", ["funded_account.py"],
       ["bronze.funded_account"]),
    _d("serving.funded_account", "gold", R(lake.FUNDED_ACCOUNT), "json", ["funded_account.py"], ["silver.funded_ledger"]),
    _d("serving.research_review", "gold", R(lake.RESEARCH_REVIEW), "json", ["build_research_review.py"],
       ["silver.report_parsed", "silver.report_markdown", "silver.corrections", "serving.report_performance"]),
    _d("serving.review_evidence", "gold", f"{R(lake.RESEARCH_REVIEW.parent)}/reviews/*.json", "json", ["build_research_review.py"],
       ["silver.report_parsed", "silver.report_markdown", "silver.corrections"]),
    _d("serving.search_index", "gold", R(lake.SEARCH_INDEX), "json", ["build_search_index.py"], ["serving.report_performance"]),
    _d("serving.fulltext_index", "gold", f"{R(lake.FULLTEXT_INDEX)}/**/*", "json/json.gz", ["build_fulltext_index.py"],
       ["silver.report_markdown", "silver.report_parsed", "silver.search_synonyms", "serving.report_performance",
        "serving.report_first_seen"], schema_file=R(lake.FULLTEXT_INDEX / "manifest.json")),
    # ── bronze ──
    _d("bronze.report_pdfs", "bronze", f"{R(lake.BRONZE_PDFS)}/**/*.pdf", "pdf", COLLECTORS),
    _d("bronze.report_manifest", "bronze", R(lake.BRONZE_REPORT_MANIFEST), "json",
       COLLECTORS, ["bronze.kuvic_items"]),
    _d("bronze.kuvic_items", "bronze", R(lake.BRONZE_KUVIC_ITEMS), "json", ["collect_kuvic_browser.py"]),
    _d("bronze.krx_names", "bronze", R(lake.BRONZE_KRX_NAMES), "json", ["build_report_performance.py"]),
    _d("bronze.krx_name_observations", "bronze", R(lake.BRONZE_KRX_NAME_OBSERVATIONS), "jsonl",
       ["build_report_performance.py"]),
    _d("bronze.prices", "bronze", f"{R(lake.BRONZE_PRICES)}/*/ingest_date=*/*.csv.gz", "csv.gz",
       ["lake_prices.py"]),
    _d("bronze.price_ingest_log", "bronze", R(lake.BRONZE_PRICE_LOG), "jsonl", ["lake_prices.py"]),
    _d("bronze.source_status", "bronze", f"{R(lake.SOURCE_STATUS)}/*.json", "json",
       [".github/workflows/refresh-daily.yml", ".github/workflows/refresh-reports.yml"]),
    # ── silver ──
    _d("silver.report_markdown", "silver", f"{R(lake.SILVER_MARKDOWN)}/**/*.md", "md",
       ["transcribe_pdfs.py"], ["bronze.report_pdfs"]),
    _d("silver.report_parsed", "silver", R(lake.SILVER_REPORT_PARSED), "csv", ["build_report_performance.py"],
       ["silver.report_markdown", "bronze.report_manifest", "silver.corrections", "silver.name_ticker",
        "bronze.krx_names", "bronze.krx_name_observations"]),
    _d("silver.report_parse_issues", "silver", R(lake.SILVER_REPORT_ISSUES), "csv",
       ["build_report_performance.py"], ["silver.report_markdown"]),
    _d("silver.name_ticker", "silver", R(lake.SILVER_NAME_TICKER), "json", ["build_report_performance.py"]),
    _d("silver.corrections", "silver", R(lake.SILVER_CORRECTIONS), "json", ["manual"]),
    _d("silver.search_synonyms", "silver", R(lake.SEARCH_SYNONYMS), "json", ["manual"]),
    _d("silver.prices", "silver", f"{R(lake.SILVER_PRICES)}/*.csv", "csv",
       ["build_report_performance.py", "backtest/warehouse.py", "rebuild_silver_prices.py"], ["bronze.prices"]),
    _d("silver.price_rejects", "silver", f"{R(lake.SILVER_REJECTS)}/*.jsonl", "jsonl", ["lake_prices.py"],
       ["bronze.prices"]),
    # ── gold ──
    _d("gold.report_performance", "gold", R(lake.GOLD_REPORT_PERFORMANCE), "csv",
       ["build_report_performance.py"], ["silver.report_parsed", "silver.prices"]),
    _d("gold.report_performance_summary", "gold", R(lake.GOLD_REPORT_SUMMARY), "csv", [],
       ["silver.report_parsed", "silver.prices"]),
    _d("gold.health", "gold", R(lake.GOLD_HEALTH), "json", ["pipeline_health.py"],
       ["bronze.report_manifest", "silver.report_markdown", "silver.report_parsed", "silver.report_parse_issues",
        "gold.report_performance", "serving.strategy_backtest", "serving.api_v1", "bronze.source_status"]),
    _d("gold.backtest_cache", "gold", f"{R(lake.GOLD_CACHE)}/**/*", "json/pickle", ["backtest_momentum.py"],
       ["gold.report_performance", "silver.prices"]),
    # ── serving (gold artifacts read by Next.js and the public API) ──
    _d("serving.schema_metadata", "gold", f"{R(lake.PUBLIC)}/metadata/*.json", "json",
       ["schema_metadata.py"], ["silver.report_parsed", "silver.prices", "bronze.report_manifest"],
       schema_file=R(lake.PUBLIC / "metadata" / "schema.json")),
    _d("serving.report_performance", "gold", R(lake.WEB_DATA / "report-performance.json"), "json",
       ["build_report_performance.py"], ["silver.report_parsed", "silver.prices", "bronze.report_manifest"]),
    _d("serving.strategy_backtest", "gold", R(lake.WEB_DATA / "strategy-backtest.json"), "json",
       ["backtest_momentum.py"], ["gold.report_performance", "silver.prices", "gold.backtest_cache"]),
    _d("serving.stats", "gold", R(lake.WEB_DATA / "stats.json"), "json", ["build_stats.py"],
       ["serving.report_performance", "silver.prices"]),
    _d("serving.report_first_seen", "gold", R(lake.WEB_DATA / "report-first-seen.json"), "json",
       ["build_report_first_seen.py"], ["bronze.report_manifest", "serving.report_performance"]),
    _d("serving.forward_record", "gold", R(lake.WEB_DATA / "forward-record.json"), "json",
       ["build_forward_record.py"], ["serving.api_v1", "silver.prices"]),
    _d("serving.strategy_trades_csv", "gold", f"{R(lake.PUBLIC)}/strategy-trades*.csv", "csv",
       ["backtest_momentum.py"], ["serving.strategy_backtest"]),
    _d("serving.api_v1", "gold", f"{R(lake.API_V1)}/**/*.json", "json",
       ["export_signals_api.py", "build_forward_record.py"], ["serving.strategy_backtest", "silver.prices"],
       schema_file=R(lake.API_V1 / "strategies.json")),
    _d("serving.api_v2", "gold", f"{R(lake.API_V2)}/**/*.json", "json",
       ["export_api_v2.py", "export_stock_charts.py"],
       ["serving.strategy_backtest", "serving.report_performance", "serving.forward_record", "silver.prices"],
       schema_file=R(lake.API_V2 / "index.json")),
]


def tracked_in_git(pattern: str) -> bool:
    """Whether the dataset is meant to be committed, i.e. its path is not git-ignored.

    Decided by .gitignore rather than the index, so a run sees the same answer before and after it
    commits new files.
    """
    probe = pattern.replace("**/", "").replace("*", "probe")
    result = subprocess.run(["git", "check-ignore", "-q", "--no-index", probe], cwd=lake.ROOT)
    return result.returncode == 1  # 0 = ignored, 1 = not ignored


def _files(pattern: str) -> list[Path]:
    if any(ch in pattern for ch in "*?["):
        return sorted(p for p in lake.ROOT.glob(pattern) if p.is_file() and not p.name.endswith(".tmp"))
    p = lake.ROOT / pattern
    return [p] if p.is_file() else []


def _bytes(path: Path) -> int:
    if path.suffix not in TEXT_SUFFIXES:
        return path.stat().st_size
    data = path.read_bytes()
    return len(data) - data.count(b"\r\n")


def _rows(path: Path, fmt: str) -> int | None:
    if fmt == "csv":
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return max(sum(1 for _ in csv.reader(fh)) - 1, 0)
    if fmt == "jsonl":
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    if fmt == "json":
        doc = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(doc, list):
            return len(doc)
        if isinstance(doc, dict) and isinstance(doc.get("records"), list):
            return len(doc["records"])
    return None


def _doc_meta(path: Path) -> tuple[str | None, str | None]:
    """(schema_version, updated_at) read from inside a JSON document."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if isinstance(doc, list):  # manifest-style rows: newest row timestamp
        stamps = [r.get(k) for r in doc if isinstance(r, dict) for k in ("downloaded_at", "fetched_at") if r.get(k)]
        return None, max(stamps) if stamps else None
    if not isinstance(doc, dict):
        return None, None
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    stamp = next((src[k] for src in (doc, meta) for k in ("generated_at", "as_of", "checked_at") if src.get(k)), None)
    return doc.get("schema_version"), stamp


def describe(spec: dict) -> dict:
    is_glob = any(ch in spec["path"] for ch in "*?[")
    tracked = tracked_in_git(spec["path"])
    entry = {k: spec[k] for k in ("id", "layer", "path", "format", "producer", "inputs")}
    entry.update({"tracked_in_git": tracked, "schema_version": None, "rows": None, "files": None,
                  "bytes": None, "updated_at": None})
    files = _files(spec["path"]) if tracked else []
    if not files:
        return entry
    entry["files"] = len(files)
    entry["bytes"] = sum(_bytes(p) for p in files)
    if is_glob:
        schema_file = lake.ROOT / spec["schema_file"] if spec["schema_file"] else None
        if schema_file and schema_file.is_file():
            entry["schema_version"], entry["updated_at"] = _doc_meta(schema_file)
        return entry
    entry["rows"] = _rows(files[0], spec["format"])
    if spec["format"] == "json":
        entry["schema_version"], entry["updated_at"] = _doc_meta(files[0])
    return entry


def build() -> dict:
    ids = {d["id"] for d in DATASETS}
    for d in DATASETS:
        unknown = set(d["inputs"]) - ids
        if unknown:
            raise SystemExit(f"{d['id']}: unknown inputs {sorted(unknown)}")
    datasets = sorted((describe(d) for d in DATASETS), key=lambda e: e["id"])
    return {"schema_version": CATALOG_SCHEMA_VERSION, "layout": "docs/DATA-LAYERS.md", "datasets": datasets}


def render(catalog: dict) -> str:
    return json.dumps(catalog, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail if data/catalog.json differs from a fresh build")
    parser.add_argument("--output", type=Path, default=lake.CATALOG)
    parser.add_argument("--tracked-pathspecs", action="store_true",
                        help="print a git pathspec per committed dataset (the refresh workflows use it to prove "
                             "the staged commit holds every file the catalog describes)")
    args = parser.parse_args()

    if args.tracked_pathspecs:
        for spec in sorted(d["path"] for d in DATASETS if tracked_in_git(d["path"])):
            print(f":(glob){spec}")
        print(f":(glob){lake.rel(args.output)}")
        return 0

    text = render(build())
    if args.check:
        current = args.output.read_text(encoding="utf-8").replace("\r\n", "\n") if args.output.exists() else ""
        if current != text:
            print(f"{args.output} is stale — run: uv run python scripts/build_catalog.py", file=sys.stderr)
            return 1
        print(f"{args.output} is up to date")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.output} ({len(json.loads(text)['datasets'])} datasets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
