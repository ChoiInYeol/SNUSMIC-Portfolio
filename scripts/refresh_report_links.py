"""Refresh transcript links after the lake move without recomputing research results.

Only source_md_url changes; source_file stays as provenance and all scores remain untouched.
Normal pipeline runs already write the current paths through build_source_urls.
"""
from __future__ import annotations

import argparse
import json
from urllib.parse import quote, urlsplit, urlunsplit

import lake
from lake_prices import replace_atomic

REPORTS = lake.WEB_DATA / "report-performance.json"


def refresh(doc: dict) -> int:
    count = 0
    for record in doc["records"]:
        url = record.get("source_md_url")
        if not url:
            continue
        parts = urlsplit(url)
        old = "/blob/main/data/markdown/"
        if parts.hostname != "github.com" or old not in parts.path:
            continue
        current = lake.current_rel(record["source_file"])
        if not lake.resolve_data_path(current).is_file():
            raise ValueError(f"missing transcript: {current}")
        prefix = parts.path.split(old, 1)[0] + "/blob/main/"
        updated = urlunsplit((parts.scheme, parts.netloc, prefix + quote(current, safe="/"), parts.query, parts.fragment))
        if updated != url:
            record["source_md_url"] = updated
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    doc = json.loads(REPORTS.read_text(encoding="utf-8"))
    count = refresh(doc)
    if args.check:
        print(f"{count} legacy transcript links")
        return int(count > 0)
    body = json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False)
    if REPORTS.read_text(encoding="utf-8") != body:
        replace_atomic(REPORTS, lambda fh: fh.write(body))
    print(f"refreshed {count} transcript links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
