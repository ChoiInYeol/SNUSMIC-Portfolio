"""Pipeline health check — 데이터 파이프라인 헬스 체크 & Telegram 운영 알림.

매일 CI 마지막에 실행한다. 파이프라인 산출물의 핵심 카운트를 집계해
직전 실행(data/health.json, 커밋됨)과 비교하고, 이상 징후가 있으면
Telegram으로 운영 알림을 보낸다. "조용한 데이터 부패" 방지가 목적:
수집기가 죽어도, 파서가 빈 줄만 뱉어도, CI는 green이기 때문이다.

점검 항목:
  * 카운트 감소 — manifest PDF / 전사 markdown / 파싱 행 / 성과 행이 줄면
    데이터 손실 (append-only 파이프라인에서 감소는 버그).
  * 파싱 이슈 급증 — report_parse_issues.csv +15행 이상이면 소스 포맷 변경 의심.
  * 백테스트 신선도 — signals.as_of가 4일(주말 버퍼) 이상 묵으면 갱신 실패.
  * 전략 수 변동 — multi_strategy.strategies 수가 바뀌면 의도 여부 확인 필요.

Behavior contract: quality failures return nonzero and block publication; Telegram notification is best-effort and independent. Dry-run is strictly read-only.

Usage:
    python scripts/pipeline_health.py            # normal CI run
    python scripts/pipeline_health.py --dry-run  # 전송 없이 리포트만 출력
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEALTH_PATH = ROOT / "data" / "health.json"
MANIFEST_PATH = ROOT / "data" / "sources" / "manifest.json"
MARKDOWN_DIR = ROOT / "data" / "markdown"
BACKTEST_PATH = ROOT / "src" / "data" / "strategy-backtest.json"
SIGNALS_LATEST = ROOT / "public" / "api" / "v1" / "signals" / "latest.json"
REPORT_STATS_PATHS = (ROOT / "src" / "data" / "forward-record.json", ROOT / "src" / "data" / "stats.json")
REPORT_PERFORMANCE_PATH = ROOT / "src" / "data" / "report-performance.json"

PARSE_ISSUE_SPIKE = 15   # 직전 대비 이 행수 이상 증가하면 경고
STALE_DAYS = 2           # calendar-day warning; exchange holidays are not inferred

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")



def _csv_rows(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        return max(len(rows) - 1, 0) if rows else 0
    except (OSError, csv.Error):
        return None


def _finite_tree(value: object) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_finite_tree(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _finite_tree(v) for k, v in value.items())
    return False


def _load_strict(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if _finite_tree(value) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _strict_json(path: Path) -> bool:
    return _load_strict(path) is not None


def _shape_issues() -> list[str]:
    issues: list[str] = []
    back = _load_strict(BACKTEST_PATH)
    latest = _load_strict(SIGNALS_LATEST)
    multi = back.get("multi_strategy") if isinstance(back, dict) else None
    strategies = multi.get("strategies") if isinstance(multi, dict) else None
    if not isinstance(strategies, list):
        issues.append("backtest schema missing multi_strategy.strategies array")
    else:
        keys = [s.get("key") for s in strategies if isinstance(s, dict)]
        headline = multi.get("headline_key") if isinstance(multi, dict) else None
        if not headline or headline not in keys:
            issues.append("backtest headline_key does not match strategy keys")
    if not isinstance(latest, dict):
        issues.append("signals latest must be an object")
    else:
        if not isinstance(multi, dict) or latest.get("headline_strategy") != multi.get("headline_key"):
            issues.append("signals headline does not match the backtest generation")
        back_signals = back.get("signals") if isinstance(back, dict) else None
        if not isinstance(back_signals, dict) or latest.get("as_of") != back_signals.get("as_of"):
            issues.append("signals as_of does not match the backtest generation")
        try:
            parsed = dt.date.fromisoformat(latest.get("as_of"))
            if parsed > dt.date.today(): issues.append("signals as_of is in the future")
        except (TypeError, ValueError):
            issues.append("signals as_of is not ISO date")
        names = ("open_positions", "buy_signals", "sell_signals")
        arrays = {name: latest.get(name) for name in names}
        for name, value in arrays.items():
            if not isinstance(value, list): issues.append(f"signals {name} must be an array")
        counts = latest.get("counts")
        if not isinstance(counts, dict) or any(type(counts.get(k)) is not int or counts[k] < 0 or (isinstance(arrays[k], list) and counts[k] != len(arrays[k])) for k in names):
            issues.append("signals counts must match nonnegative integer array lengths")
        slots = latest.get("slots")
        slot_names = ("max_positions", "open", "available")
        if (not isinstance(slots, dict)
                or any(type(slots.get(k)) is not int or slots[k] < 0 for k in slot_names)):
            issues.append("signals slots must contain nonnegative integer counters")
        elif (slots["open"] + slots["available"] != slots["max_positions"]
              or not isinstance(arrays["open_positions"], list)
              or slots["open"] != len(arrays["open_positions"])):
            issues.append("signals slots do not match position count or maximum")
    forward = _load_strict(REPORT_STATS_PATHS[0])
    if isinstance(forward, dict):
        entries = forward.get("entries")
        if not isinstance(entries, list): issues.append("forward entries must be an array")
        elif any(not isinstance(e, dict) or not isinstance(e.get("status"), str) or e["status"] not in {"tracking", "pending", "unavailable"} for e in entries):
            issues.append("forward entry has unknown status")
    else:
        issues.append("forward record must be an object")
    reports = _load_strict(REPORT_PERFORMANCE_PATH)
    if not isinstance(reports, dict) or not isinstance(reports.get("records"), list):
        issues.append("report-performance records must be an array")
    else:
        classifications = {
            "school": {"smic", "yig", "star", "kuvic", "ewha", "voera"},
            "rating_class": {"buy", "soft_buy", "sell"},
            "era": {"modern", "archive"},
            "report_type": {"company", "sector"},
        }
        for index, record in enumerate(reports["records"]):
            if not isinstance(record, dict):
                issues.append(f"report {index} must be an object")
                continue
            for field, allowed in classifications.items():
                value = record.get(field)
                if not isinstance(value, str) or value not in allowed:
                    issues.append(f"report {index} has invalid {field}; quarantine before publication")
    return issues

def collect_metrics() -> dict:
    metrics: dict = {"checked_at": dt.date.today().isoformat()}

    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        metrics["manifest_pdfs"] = len(manifest)
    except Exception:
        metrics["manifest_pdfs"] = None

    metrics["transcripts_md"] = (
        len(list(MARKDOWN_DIR.glob("**/*.md"))) if MARKDOWN_DIR.exists() else None
    )
    metrics["parsed_rows"] = _csv_rows(ROOT / "data" / "report_parsed.csv")
    metrics["parse_issue_rows"] = _csv_rows(ROOT / "data" / "report_parse_issues.csv")
    metrics["performance_rows"] = _csv_rows(ROOT / "data" / "report_performance.csv")
    metrics["report_performance_json_valid"] = _strict_json(REPORT_PERFORMANCE_PATH)
    metrics["backtest_json_valid"] = _strict_json(BACKTEST_PATH)
    metrics["signals_json_valid"] = _strict_json(SIGNALS_LATEST)
    metrics["aux_json_invalid"] = [p.name for p in REPORT_STATS_PATHS if _load_strict(p) is None]
    metrics["shape_issues"] = _shape_issues()
    metrics["source_status_failures"] = []
    status_dir = ROOT / "data" / "source-status"
    if status_dir.exists():
        for status_path in sorted(status_dir.glob("*.json")):
            status = _load_strict(status_path)
            if (not isinstance(status, dict) or status.get("source") != status_path.stem or status.get("attempted") is not True or type(status.get("exit_code")) is not int or status.get("exit_code") != 0 or status.get("collector_success") is not True):
                metrics["source_status_failures"].append(status_path.stem)

    metrics["backtest_as_of"] = None
    metrics["strategy_count"] = None
    try:
        data = json.loads(BACKTEST_PATH.read_text(encoding="utf-8"))
        metrics["backtest_as_of"] = data.get("signals", {}).get("as_of")
        strategies = data.get("multi_strategy", {}).get("strategies")
        if isinstance(strategies, dict):
            metrics["strategy_count"] = len(strategies)
        elif isinstance(strategies, list):
            metrics["strategy_count"] = len(strategies)
    except Exception:
        pass

    metrics["signals_latest_exists"] = SIGNALS_LATEST.exists()
    return metrics


# 카운트 감소가 곧 데이터 손실인 append-only 지표들
_MONOTONIC = ("manifest_pdfs", "transcripts_md", "parsed_rows", "performance_rows")

_LABELS = {
    "manifest_pdfs": "수집 PDF(manifest)",
    "transcripts_md": "전사 markdown",
    "parsed_rows": "파싱 행",
    "parse_issue_rows": "파싱 이슈 행",
    "performance_rows": "성과 결합 행",
    "strategy_count": "백테스트 전략 수",
}


def diagnose(metrics: dict, prev: dict | None) -> list[str]:
    """이상 징후 목록 (비어 있으면 정상)."""
    issues: list[str] = []

    for key in _MONOTONIC:
        if metrics.get(key) is None:
            issues.append(f"{_LABELS[key]} 집계 실패 — 산출물 누락 또는 포맷 변경")

    if not metrics.get("signals_latest_exists"):
        issues.append("signals/latest.json 없음 — export_signals_api 실패")
    if not metrics.get("backtest_json_valid"):
        issues.append("strategy-backtest.json invalid JSON or non-finite value")
    if not metrics.get("signals_json_valid"):
        issues.append("signals/latest.json invalid JSON or non-finite value")
    if not metrics.get("report_performance_json_valid"):
        issues.append("report-performance.json invalid JSON or non-finite value")
    if metrics.get("shape_issues"):
        issues.extend(metrics["shape_issues"])
    if metrics.get("source_status_failures"):
        issues.append("collector source failure: " + ",".join(metrics["source_status_failures"]))
    if metrics.get("aux_json_invalid"):
        issues.append("auxiliary JSON invalid: " + ",".join(metrics["aux_json_invalid"]))

    as_of = metrics.get("backtest_as_of")
    if as_of:
        try:
            age = (dt.date.today() - dt.date.fromisoformat(as_of)).days
            if age > STALE_DAYS:
                issues.append(f"백테스트 신호가 {age}일 묵음 (as_of {as_of}) — 거래소 휴일은 판별하지 않음")
        except ValueError:
            issues.append(f"backtest as_of 파싱 불가: {as_of!r}")
    else:
        issues.append("backtest as_of 없음 — strategy-backtest.json 점검 필요")

    if prev:
        for key in _MONOTONIC:
            cur, old = metrics.get(key), prev.get(key)
            if cur is not None and old is not None and cur < old:
                issues.append(f"{_LABELS[key]} 감소: {old} → {cur} — append-only 위반(데이터 손실)")

        cur_iss, old_iss = metrics.get("parse_issue_rows"), prev.get("parse_issue_rows")
        if cur_iss is not None and old_iss is not None and cur_iss - old_iss >= PARSE_ISSUE_SPIKE:
            issues.append(f"파싱 이슈 급증: {old_iss} → {cur_iss} (+{cur_iss - old_iss}) — 소스 포맷 변경 의심")

        cur_sc, old_sc = metrics.get("strategy_count"), prev.get("strategy_count")
        if cur_sc is not None and old_sc is not None and cur_sc != old_sc:
            issues.append(f"전략 수 변동: {old_sc} → {cur_sc} — 의도된 변경인지 확인")

    return issues


def compose_message(metrics: dict, issues: list[str]) -> str:
    from send_telegram_signals import esc

    lines = [f"*{esc('⚠️ 판결 아카이브 파이프라인 이상 감지')}*", ""]
    for issue in issues:
        lines.append(esc(f"• {issue}"))
    lines.append("")
    summary = " / ".join(
        f"{_LABELS[k]} {metrics.get(k)}" for k in (*_MONOTONIC, "parse_issue_rows")
        if metrics.get(k) is not None
    )
    lines.append(esc(f"현재 상태: {summary}"))
    lines.append(esc(f"점검일: {metrics['checked_at']}"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="전송 없이 리포트만 출력")
    args = parser.parse_args(argv)

    prev: dict | None = None
    if HEALTH_PATH.exists():
        try:
            prev = json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
        except Exception:
            prev = None

    metrics = collect_metrics()
    issues = diagnose(metrics, prev)

    print("pipeline health:")
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    # Dry-run is strictly read-only; normal runs persist the comparison baseline.
    if not args.dry_run:
        HEALTH_PATH.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8"
        )

    if not issues:
        print("OK — no anomalies.")
        return 0

    print(f"{len(issues)} issue(s):")
    for issue in issues:
        print(f"  ! {issue}")

    if args.dry_run:
        print("--- message (MarkdownV2) ---")
        print(compose_message(metrics, issues))
        return 1

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if token and chat_id:
        from send_telegram_signals import send_message
        send_message(token, chat_id, compose_message(metrics, issues))
    else:
        print("TELEGRAM secrets not set — console report only.")
    return 1



if __name__ == "__main__":
    raise SystemExit(main())
