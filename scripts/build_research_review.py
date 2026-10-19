"""Build a bounded, evidence-bearing review queue; apply exported reviews with optimistic locking.

The public UI only exports proposals. --apply validates reviewer, evidence, field types, transcript
hash and old values before atomically committing corrections and their history in one Silver file.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path

import lake
from lake_prices import replace_atomic, _lock

FIELDS = ("ticker", "company", "report_date", "rating", "target_price", "report_current_price", "stated_upside_pct")
NUMBERS = {"target_price", "report_current_price", "stated_upside_pct"}


def digest(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode()).hexdigest()


def build() -> dict:
    performance = json.loads((lake.WEB_DATA / "report-performance.json").read_text(encoding="utf-8"))
    links = {lake.current_rel(r["source_file"]): r for r in performance["records"]}
    corrections = json.loads(lake.SILVER_CORRECTIONS.read_text(encoding="utf-8"))
    rows = []
    with lake.SILVER_REPORT_PARSED.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            source = lake.current_rel(r["source_file"])
            path = lake.resolve_data_path(source)
            if not path.resolve().is_relative_to(lake.SILVER_MARKDOWN.resolve()):
                raise ValueError("review source escapes the Silver transcript root")
            text = path.read_text(encoding="utf-8")
            values = {f: (float(r[f]) if f in NUMBERS and r.get(f) else r.get(f) or None) for f in FIELDS}
            reasons = [v for v in (r.get("parse_issue"), r.get("qa_flags")) if v]
            if not reasons and values["target_price"] is not None and values["report_current_price"]:
                expected = (values["target_price"] / values["report_current_price"] - 1) * 100
                if values["stated_upside_pct"] is not None and abs(expected - values["stated_upside_pct"]) > 5:
                    reasons.append("가격·상승여력 불일치 (원문 단위·기준일 확인 필요)")
            if not reasons:
                continue
            record = links.get(source, {})
            matches = []
            lines = text.splitlines()[:220]
            for field, value in values.items():
                if value is None: continue
                variants = {str(value)}
                if field in NUMBERS:
                    variants.update({f"{value:g}", f"{value:,.0f}"})
                raw_field = {"target_price": "target_price_raw", "report_current_price": "report_current_price_raw"}.get(field)
                if raw_field and r.get(raw_field): variants.add(r[raw_field])
                found = [(i + 1, line) for i, line in enumerate(lines) if any(v in line for v in variants if v)]
                matches.extend({"field": field, "line": n, "quote": line[:700]} for n, line in found[:3])
            rows.append({"id": source, "school": r["school"], "values": values,
                         "transcript_sha256": digest(text), "reasons": reasons,
                         "excerpt": "\n".join(text.splitlines()[:220])[:14000],
                         "matches": matches,
                         "pdf_url": record.get("source_pdf_url"), "markdown_url": record.get("source_md_url"),
                         "correction": corrections.get(path.name, {})})
    return {"schema_version": "1.0", "as_of": performance["as_of"], "records": rows}


def apply(proposal: dict) -> int:
    lock = lake.SILVER_CORRECTIONS.with_suffix(".json.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _lock(fd)
        try:
            return _apply_locked(proposal)
        finally:
            _lock(fd, release=True)
    finally:
        os.close(fd)


def _apply_locked(proposal: dict) -> int:
    if proposal.get("schema_version") != "1.0" or not all(isinstance(proposal.get(k), str) and proposal[k].strip() for k in ("reviewer", "reason")):
        raise ValueError("schema_version, reviewer and reason are required")
    changes = proposal.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError("changes must be non-empty")
    queue = {r["id"]: r for r in build()["records"]}
    current = json.loads(lake.SILVER_CORRECTIONS.read_text(encoding="utf-8"))
    seen = set()
    for change in changes:
        row = queue.get(change["id"])
        if row is None or change["id"] in seen:
            raise ValueError("unknown or duplicate report")
        seen.add(change["id"])
        if row["transcript_sha256"] != change["transcript_sha256"] or row["values"] != change["before"]:
            raise ValueError("stale review: regenerate queue and review again")
        if row["correction"] != change.get("before_correction"):
            raise ValueError("corrections changed since review")
        name = Path(change["id"]).name
        # Legacy corrections are keyed by basename; refuse collisions rather than touching two reports.
        if sum(p.name == name for p in lake.SILVER_MARKDOWN.rglob("*.md")) != 1:
            raise ValueError("ambiguous correction basename")
        after = change.get("after")
        if not isinstance(after, dict) or not after or set(after) - set(FIELDS):
            raise ValueError("unsupported correction field")
        if not isinstance(change.get("evidence"), str) or not change["evidence"].strip():
            raise ValueError("evidence is required")
        for field, value in after.items():
            if value is None:
                continue
            if field in NUMBERS:
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or (field != "stated_upside_pct" and value <= 0):
                    raise ValueError(f"invalid {field}")
            elif not isinstance(value, str) or not value.strip():
                raise ValueError(f"invalid {field}")
            if field == "report_date":
                dt.date.fromisoformat(value)
        current[name] = {**current.get(name, {}), **after, "_keep_issue": True}
    event = {**proposal, "applied_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    current.setdefault("_review_history", []).append(event)
    replace_atomic(lake.SILVER_CORRECTIONS, lambda fh: json.dump(current, fh, ensure_ascii=False, indent=2, allow_nan=False))
    return len(changes)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--apply", type=Path)
    mode.add_argument("--check", action="store_true")
    args = p.parse_args()
    if args.apply:
        print(f"Applied {apply(json.loads(args.apply.read_text(encoding='utf-8')))} reviewed corrections; rebuild performance before publication.")
        return 0
    doc = build()
    details = {}
    for row in doc["records"]:
        key = digest(row["id"])
        details[lake.RESEARCH_REVIEW.parent / "reviews" / f"{key}.json"] = json.dumps({"schema_version": "1.0", **row}, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        row.pop("excerpt"); row.pop("matches")
        row["detail_url"] = f"/research/reviews/{key}.json"
    output = json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    if args.check:
        files = {lake.RESEARCH_REVIEW: output, **details}
        return 0 if all(path.exists() and path.read_text(encoding="utf-8") == content for path, content in files.items()) else 1
    for path, content in details.items():
        replace_atomic(path, lambda fh: fh.write(content))
    replace_atomic(lake.RESEARCH_REVIEW, lambda fh: fh.write(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
