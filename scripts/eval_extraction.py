"""표지 필드 추출 방식 비교 — PDF를 직접 읽은 정답셋(tests/fixtures/parse_gold/*.jsonl) 기준.

방식 (전사본 × 추출기):
  A   legacy  parse_report  on 기존 opendataloader 전사본 (--baseline-dir)
  A'  candidate extract     on 기존 전사본                (추출기 효과 분리)
  B   legacy  parse_report  on kordoc 전사본 (data/markdown)  (엔진 효과 분리)
  C   candidate extract     on kordoc 전사본
  D   C + 발간일 종가 정합 (data/prices, 티커·날짜는 legacy identity 파서)

필드별로 correct / wrong(다른 값) / missed(값 없음) / spurious(정답이 null인데 값) 를 세고
error = (wrong + missed + spurious) / n, critical = (wrong + spurious) / n 을 낸다.
틀린 값은 빈 값보다 해롭다 → 방식 선택은 critical 우선, 그다음 error.

사용:
    python scripts/eval_extraction.py --baseline-dir <old data/markdown 사본> [--kordoc-dir cache/kordoc_covers] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_report_performance as brp  # noqa: E402
from report_extract import extract  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GOLD_DIR = ROOT / "tests" / "fixtures" / "parse_gold"
MD_ROOT = ROOT / "data" / "markdown"
FIELDS = ("rating", "target", "current", "upside", "ticker")
RATING_CLASS = {
    "buy": {"strong buy", "buy", "outperform", "overweight", "매수", "적극매수", "강력매수"},
    "hold": {"hold", "neutral", "중립", "보유"},
    "sell": {"sell", "underperform", "underweight", "매도"},
}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def rating_class(value: str | None) -> str | None:
    if not value:
        return None
    key = value.strip().lower()
    return next((cls for cls, names in RATING_CLASS.items() if key in names), "other")


def gold_rows() -> list[dict]:
    rows = []
    for path in sorted(GOLD_DIR.glob("*.jsonl")):
        school = path.stem
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("error"):
                continue
            row["school"] = school
            lo, hi = row.get("target_price_low"), row.get("target_price_high")
            row["target"] = row.get("target_price") if row.get("target_price") is not None else (
                (lo + hi) / 2 if lo is not None and hi is not None else None)
            row["current"] = row.get("current_price")
            row["upside"] = row.get("upside_pct")
            row["rating_cls"] = rating_class(row.get("rating"))
            rows.append(row)
    return rows


def md_path(source_pdf: str, md_root: Path) -> Path:
    pdf = Path(source_pdf)
    rel = pdf.relative_to("data/pdfs")
    return md_root / rel.with_suffix(".md")


def market_close(market: str | None, ticker: str | None, date: str | None) -> float | None:
    if market not in {"KR", "US"} or not ticker or not date:
        return None
    path = brp.PRICE_CACHE_DIR / f"{market}_{ticker}.csv"
    if not path.exists():
        return None
    prices = pd.read_csv(path, index_col=0, parse_dates=True)
    before = prices[prices.index <= pd.Timestamp(date)]
    return float(before.iloc[-1]["close"]) if not before.empty else None


def run_legacy(path: Path, school: str, hint: dict | None) -> dict:
    r = brp.parse_report(path, school, hint)
    return {"rating": r.rating, "target": r.target_price, "current": r.report_current_price,
            "upside": r.stated_upside_pct, "ticker": r.ticker, "market": r.market, "date": r.report_date}


def run_candidate(path: Path, school: str, hint: dict | None, with_market: bool) -> dict:
    ident = run_legacy(path, school, hint)  # identity/date stay with the legacy parser
    text = path.read_text(encoding="utf-8", errors="ignore")
    close = market_close(ident["market"], ident["ticker"], ident["date"]) if with_market else None
    e = extract(text, ident["market"], close)
    return {**ident, "rating": e.rating, "target": e.target, "current": e.current, "upside": e.upside}


def grade(field: str, pred: dict, gold: dict) -> str:
    if field == "rating":
        p, g = rating_class(pred["rating"]), gold["rating_cls"]
    elif field == "ticker":
        p, g = pred["ticker"], gold.get("ticker")
        p = p.upper() if p else None
        g = g.upper().lstrip("A") if g and len(g) == 7 and g[1:].isdigit() else (g.upper() if g else None)
    else:
        p, g = pred[field], gold[field]
    if g is None:
        return "true_null" if p is None else "spurious"
    if p is None:
        return "missed"
    if field in {"target", "current"}:
        return "correct" if abs(p / g - 1) <= 0.005 else "wrong"
    if field == "upside":
        return "correct" if abs(p - g) <= 0.6 else "wrong"
    return "correct" if p == g else "wrong"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True, help="기존 엔진 전사본의 data/markdown 사본")
    parser.add_argument("--kordoc-dir", type=Path, default=MD_ROOT,
                        help="kordoc 전사본 루트 (기본 data/markdown; 표지 캐시는 cache/kordoc_covers)")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--high-confidence-only", action="store_true")
    parser.add_argument("--all-types", action="store_true", help="섹터 리포트 포함 (기본: company만 — 성과 집계 대상)")
    args = parser.parse_args()

    hints = brp.load_hints()
    gold = [g for g in gold_rows()
            if (args.all_types or g.get("report_type") == "company")
            and (not args.high_confidence_only or g.get("confidence") == "high")]
    methods = {
        "A": lambda g: run_legacy(md_path(g["source_pdf"], args.baseline_dir), g["school"], g["hint"]),
        "A'": lambda g: run_candidate(md_path(g["source_pdf"], args.baseline_dir), g["school"], g["hint"], False),
        "B": lambda g: run_legacy(md_path(g["source_pdf"], args.kordoc_dir), g["school"], g["hint"]),
        "C": lambda g: run_candidate(md_path(g["source_pdf"], args.kordoc_dir), g["school"], g["hint"], False),
        "D": lambda g: run_candidate(md_path(g["source_pdf"], args.kordoc_dir), g["school"], g["hint"], True),
    }
    counts: dict[str, dict[str, Counter]] = {m: defaultdict(Counter) for m in methods}
    by_school: dict[str, dict[str, Counter]] = {m: defaultdict(Counter) for m in methods}
    failures: list[dict] = []
    for g in gold:
        g["hint"] = hints.get(Path(g["source_pdf"]).stem)
        for name, run in methods.items():
            try:
                pred = run(g)
            except FileNotFoundError:
                counts[name]["_missing_md"]["n"] += 1
                continue
            for field in FIELDS:
                outcome = grade(field, pred, g)
                counts[name][field][outcome] += 1
                by_school[name][g["school"]][outcome if outcome in {"wrong", "spurious"} else "ok"] += 1
                if outcome in {"wrong", "spurious", "missed"}:
                    failures.append({"method": name, "field": field, "outcome": outcome, "pdf": g["source_pdf"],
                                     "pred": pred.get(field), "gold": g.get(field) if field != "rating" else g.get("rating")})

    report = {}
    print(f"gold records: {len(gold)}")
    header = f"{'method':6s} " + " ".join(f"{f:>18s}" for f in FIELDS) + f" {'ALL err/crit':>14s}"
    print(header)
    for name in methods:
        cells, tot_err, tot_crit, tot_n = [], 0, 0, 0
        report[name] = {}
        for field in FIELDS:
            c = counts[name][field]
            n = sum(c.values())
            err = c["wrong"] + c["missed"] + c["spurious"]
            crit = c["wrong"] + c["spurious"]
            tot_err, tot_crit, tot_n = tot_err + err, tot_crit + crit, tot_n + n
            report[name][field] = {**c, "n": n, "error_rate": err / n if n else None, "critical_rate": crit / n if n else None}
            cells.append(f"{err / n:6.1%} / {crit / n:6.1%}" if n else " " * 18)
        report[name]["_all"] = {"error_rate": tot_err / tot_n if tot_n else None, "critical_rate": tot_crit / tot_n if tot_n else None,
                                "missing_md": counts[name]["_missing_md"]["n"]}
        print(f"{name:6s} " + " ".join(f"{c:>18s}" for c in cells) +
              (f" {tot_err / tot_n:6.1%}/{tot_crit / tot_n:6.1%}" if tot_n else "") +
              (f"  (missing md {counts[name]['_missing_md']['n']})" if counts[name]["_missing_md"]["n"] else ""))
    print("\nper-school critical errors (wrong+spurious) / fields graded:")
    for name in methods:
        print(f"  {name:3s} " + "  ".join(f"{s}={c['wrong'] + c['spurious']}/{sum(c.values())}" for s, c in sorted(by_school[name].items())))
    if args.json:
        args.json.write_text(json.dumps({"methods": report, "failures": failures}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
