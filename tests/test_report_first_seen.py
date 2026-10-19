"""report-first-seen.json: the date the pipeline first held each report (no look-ahead for back issues)."""
from __future__ import annotations

import datetime as dt
import json

import pytest

import build_report_first_seen as fs
import lake

COMMITTED = json.loads(fs.OUTPUT.read_text(encoding="utf-8"))


def test_committed_file_keeps_the_fixed_contract():
    assert set(COMMITTED) == {"schema_version", "generated_at", "method", "first_seen"}
    assert COMMITTED["schema_version"] == "1.0"
    assert COMMITTED["method"] == fs.METHOD
    dt.datetime.fromisoformat(COMMITTED["generated_at"])
    records = {r["source_file"]: r for r in json.loads(fs.REPORTS.read_text(encoding="utf-8"))["records"]}
    assert COMMITTED["first_seen"] and set(COMMITTED["first_seen"]) <= set(records)
    for source_file, day in COMMITTED["first_seen"].items():
        assert dt.date.fromisoformat(day).isoformat() == day, source_file


def test_committed_file_matches_a_fresh_build_and_coverage_is_high():
    doc, missing, total = fs.build(COMMITTED)
    assert doc == COMMITTED, "stale — run scripts/build_report_first_seen.py"
    assert len(doc["first_seen"]) + len(missing) == total
    assert len(doc["first_seen"]) / total >= 0.98


def test_first_seen_is_the_earliest_utc_download_of_the_matching_pdf():
    manifest = [
        {"school": "ewha", "file": "data/pdfs/ewha/2020-10-10_A.pdf", "downloaded_at": "2026-06-10T23:30:00-03:00"},
        {"school": "ewha", "file": f"{lake.rel(lake.BRONZE_PDFS)}/ewha/2020-10-10_A.pdf",
         "downloaded_at": "2026-09-01T00:00:00+00:00"},
        {"school": "smic", "file": "data/pdfs/smic/2020-10-10_A.pdf", "downloaded_at": "2026-01-01T00:00:00+00:00"},
        {"school": "ewha", "file": None, "downloaded_at": None, "error": "404"},
    ]
    records = [
        {"school": "ewha", "source_file": "data/markdown/ewha/2020-10-10_A.md"},
        {"school": "ewha", "source_file": f"{lake.rel(lake.SILVER_MARKDOWN)}/ewha/2020-10-10_B.md"},
    ]
    first_seen, missing = fs.build_first_seen(manifest, records)
    # 2026-06-10T23:30-03:00 is 2026-06-11 in UTC; the smic row with the same stem is another school.
    assert first_seen == {"data/markdown/ewha/2020-10-10_A.md": "2026-06-11"}
    assert missing == [f"{lake.rel(lake.SILVER_MARKDOWN)}/ewha/2020-10-10_B.md"]


def test_naive_download_timestamp_is_rejected():
    manifest = [{"school": "ewha", "file": "data/pdfs/ewha/x.pdf", "downloaded_at": "2026-06-10T10:00:00"}]
    with pytest.raises(ValueError, match="timezone"):
        fs.build_first_seen(manifest, [])


def test_unchanged_result_keeps_generated_at():
    doc, _, _ = fs.build(COMMITTED)
    assert doc["generated_at"] == COMMITTED["generated_at"]
    changed = {**COMMITTED, "first_seen": {}}
    assert fs.build(changed)[0]["generated_at"] != "never"
