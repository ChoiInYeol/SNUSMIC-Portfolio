# -*- coding: utf-8 -*-
"""Medallion data-lake layout — the single source of truth for pipeline paths.

Bronze (raw)   : source bytes as collected, append-only, with collected_at / source / version.
Silver (clean) : validated, de-duplicated, schema-standardised derivations of bronze.
Gold (serving) : aggregates built for a purpose — web build inputs, the public REST API, ops.

See docs/adr/0001-api-v2-and-medallion.md. Scripts must import paths from here instead of
spelling `data/...` literals, so a layout change is a one-file change.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# ── Bronze: raw, append-only ────────────────────────────────────────────────
BRONZE = DATA / "bronze"
BRONZE_REPORTS = BRONZE / "reports"
BRONZE_PAPER_TARGETS = BRONZE / "portfolio" / "published-targets"
BRONZE_PAPER_EXECUTION = BRONZE / "portfolio" / "execution-v2"
BRONZE_FUNDED = BRONZE / "portfolio" / "funded-w50-v1"   # genesis, contributions, sessions, signals, order plans
BRONZE_PDFS = BRONZE_REPORTS / "pdfs"                    # {school}/{stem}.pdf (git-ignored, re-downloadable)
BRONZE_REPORT_MANIFEST = BRONZE_REPORTS / "manifest.json"  # one row per download: url, sha256, downloaded_at
BRONZE_KUVIC_ITEMS = BRONZE_REPORTS / "kuvic_items.json"
BRONZE_PRICES = BRONZE / "prices"                        # {vendor}/ingest_date=YYYY-MM-DD/{run_id}.csv.gz (git-ignored)
BRONZE_PRICE_LOG = BRONZE_PRICES / "_ingest.jsonl"       # one line per raw price fetch
BRONZE_REFERENCE = BRONZE / "reference"
BRONZE_KRX_NAMES = BRONZE_REFERENCE / "krx_names.json"   # pykrx official names, cached as fetched
BRONZE_KRX_NAME_OBSERVATIONS = BRONZE_REFERENCE / "krx_name_observations.jsonl"  # append-only new lookups

# ── Silver: cleaned, standardised ───────────────────────────────────────────
SILVER = DATA / "silver"
SILVER_REPORTS = SILVER / "reports"
SILVER_PAPER_LEDGER = SILVER / "portfolio" / "ledger.json"
SILVER_FUNDED_LEDGER = SILVER / "portfolio" / "funded-w50-v1-ledger.json"
SILVER_FUNDED_BACKTEST = SILVER / "portfolio" / "funded-w50-v1-backtest.json"  # funded_backtest.py (seed of the live genesis)
SILVER_MARKDOWN = SILVER_REPORTS / "markdown"            # kordoc transcripts, {school}/{stem}.md
SILVER_REPORT_PARSED = SILVER_REPORTS / "parsed.csv"
SILVER_REPORT_ISSUES = SILVER_REPORTS / "parse_issues.csv"
SILVER_REFERENCE = SILVER / "reference"
SILVER_NAME_TICKER = SILVER_REFERENCE / "kr_name_ticker.json"
SILVER_CORRECTIONS = SILVER_REFERENCE / "corrections.json"  # curated manual corrections
SEARCH_SYNONYMS = SILVER_REFERENCE / "search_synonyms.json"  # curated query-expansion words for full-text search
SILVER_PRICES = SILVER / "prices"                        # {MARKET}_{ticker}.csv, IDX_{name}.csv (git-ignored)

# ── Gold: serving ───────────────────────────────────────────────────────────
GOLD = DATA / "gold"
GOLD_REPORT_PERFORMANCE = GOLD / "reports" / "performance.csv"
GOLD_REPORT_SUMMARY = GOLD / "reports" / "performance_summary.csv"
GOLD_HEALTH = GOLD / "ops" / "health.json"
GOLD_CACHE = GOLD / "_cache"                             # deterministic backtest stage caches (git-ignored)

# Gold serving artifacts that must live where Next.js reads them.
WEB_DATA = ROOT / "src" / "data"                         # build-time page inputs
PUBLIC = ROOT / "public"
RESEARCH_REVIEW = PUBLIC / "research" / "review.json"
SEARCH_INDEX = PUBLIC / "research" / "search.json"
FULLTEXT_INDEX = PUBLIC / "research" / "fulltext"        # sharded report full-text index (build_fulltext_index.py)
PAPER_PORTFOLIO = PUBLIC / "research" / "portfolio.json"
FUNDED_ACCOUNT = PUBLIC / "research" / "funded-account.json"
STRATEGY_ROBUSTNESS = PUBLIC / "research" / "strategy-robustness.json"
BRONZE_BACKUP = ROOT / "artifacts" / "bronze-backup"
API_V1 = PUBLIC / "api" / "v1"                           # frozen, deprecated
API_V2 = PUBLIC / "api" / "v2"                           # current public REST API

CATALOG = DATA / "catalog.json"                          # dataset → layer, path, producer, inputs

# CI collector run status (written by the refresh workflows, read by pipeline_health; git-ignored)
BRONZE_RUNS = BRONZE / "_runs"
SOURCE_STATUS = BRONZE_RUNS / "source-status"
QUARANTINE = BRONZE_RUNS / "quarantine"

SILVER_REJECTS = SILVER / "_rejects"                     # {symbol}.jsonl: price rows that failed validation (git-ignored)


def rel(path: Path) -> str:
    """Repo-relative POSIX path string, the form stored inside artifacts."""
    return Path(path).resolve().relative_to(ROOT).as_posix()


# Pre-medallion locations. Bronze rows (manifest `file`) and older artifacts (`source_file`) keep
# these strings because bronze is never rewritten; resolve them through `current_rel`.
LEGACY_PREFIXES: dict[str, Path] = {
    "data/pdfs/": BRONZE_PDFS,
    "data/markdown/": SILVER_MARKDOWN,
    "data/prices/": SILVER_PRICES,
}


def current_rel(path_str: str) -> str:
    """Map a legacy `data/...` path string to its medallion location; other paths pass through."""
    s = path_str.replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    for old, new in LEGACY_PREFIXES.items():
        if s.startswith(old):
            return f"{rel(new)}/{s[len(old):]}"
    return s


def resolve_data_path(path_str: str) -> Path:
    """Absolute path for a repo-relative path string, legacy or current."""
    return ROOT / current_rel(path_str)
