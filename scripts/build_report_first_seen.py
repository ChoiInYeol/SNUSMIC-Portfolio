# -*- coding: utf-8 -*-
"""Write src/data/report-first-seen.json: the date the pipeline first held each report.

A report's `report_date` is the date printed on it, but several clubs (ewha, kuvic, ...) publish or are
collected months later. Any point-in-time view ("what could a reader have known on day D") must use the
date the report was first seen, which the bronze download manifest records as `downloaded_at`.

Join: manifest rows (`file` = PDF path, legacy `data/pdfs/...` or current, resolved with
lake.current_rel) to report-performance records (`source_file` = transcript path) by (school, file stem).
Reports without a manifest row are omitted, never guessed.

Contract (consumed by the web through zod; do not rename fields):
  {"schema_version": "1.0", "generated_at": <ISO>, "method": "...", "first_seen": {"<source_file>": "YYYY-MM-DD"}}

Deterministic: an unchanged result keeps the previous generated_at, so a no-op run leaves no diff.

Usage:
    python scripts/build_report_first_seen.py
    python scripts/build_report_first_seen.py --check   # write nothing; exit 1 if the file is stale
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lake  # noqa: E402
from report_sources import manifest_stems  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCHEMA_VERSION = "1.0"
OUTPUT = lake.WEB_DATA / "report-first-seen.json"
REPORTS = lake.WEB_DATA / "report-performance.json"
METHOD = ("first_seen = UTC date of min(downloaded_at) over bronze manifest rows for the report's source PDF; "
          "reports with no manifest row are omitted")


def _key(school: str, path_str: str) -> tuple[str, str]:
    return school, PurePosixPath(lake.current_rel(path_str)).stem


def _utc_date(stamp: str) -> str:
    moment = dt.datetime.fromisoformat(stamp)
    if moment.tzinfo is None:
        raise ValueError(f"downloaded_at without timezone: {stamp!r}")
    return moment.astimezone(dt.timezone.utc).date().isoformat()


def build_first_seen(manifest: list[dict], records: list[dict]) -> tuple[dict[str, str], list[str]]:
    """({source_file: first-seen date}, source_files with no manifest row)."""
    seen: dict[tuple[str, str], str] = {}
    for row in manifest:
        if not row.get("file") or not row.get("downloaded_at"):
            continue  # failed downloads carry no file and no timestamp
        day = _utc_date(row["downloaded_at"])
        for stem in manifest_stems(row):
            key = row["school"], stem
            seen[key] = min(day, seen.get(key, day))
    first_seen: dict[str, str] = {}
    missing: list[str] = []
    for record in records:
        day = seen.get(_key(record["school"], record["source_file"]))
        if day is None:
            missing.append(record["source_file"])
        else:
            first_seen[record["source_file"]] = day
    return dict(sorted(first_seen.items())), sorted(missing)


def build(previous: dict | None = None) -> tuple[dict, list[str], int]:
    manifest = json.loads(lake.BRONZE_REPORT_MANIFEST.read_text(encoding="utf-8"))
    records = json.loads(REPORTS.read_text(encoding="utf-8"))["records"]
    if not isinstance(manifest, list) or not records:
        raise ValueError("expected a manifest row list and a non-empty report-performance.records")
    first_seen, missing = build_first_seen(manifest, records)
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if previous and previous.get("method") == METHOD and previous.get("first_seen") == first_seen:
        generated_at = previous["generated_at"]
    doc = {"schema_version": SCHEMA_VERSION, "generated_at": generated_at, "method": METHOD,
           "first_seen": first_seen}
    return doc, missing, len(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if the committed file is stale")
    args = parser.parse_args(argv)

    previous = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else None
    doc, missing, total = build(previous)
    print(f"first_seen: {len(doc['first_seen'])}/{total} reports joined to the bronze manifest "
          f"({len(doc['first_seen']) / total:.1%}); {len(missing)} omitted", flush=True)
    for source_file in missing:
        print(f"  no manifest row: {source_file}", flush=True)
    if args.check:
        if previous is None or {k: v for k, v in previous.items() if k != "generated_at"} != \
                {k: v for k, v in doc.items() if k != "generated_at"}:
            print(f"{lake.rel(OUTPUT)} is stale — run: uv run python scripts/build_report_first_seen.py",
                  file=sys.stderr)
            return 1
        print(f"{lake.rel(OUTPUT)} is up to date")
        return 0
    if doc != previous:
        OUTPUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {lake.rel(OUTPUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
