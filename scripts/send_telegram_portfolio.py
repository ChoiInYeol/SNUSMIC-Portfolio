"""Daily forward-paper NAV, P&L and allocation digest. Never uses backtest cumulative returns.

Dry-run sends nothing. Successful delivery is checkpointed per recipient/KST date. Missing secrets,
invalid data or API failures return nonzero. Network exceptions never print token-bearing URLs.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import lake
from lake_prices import replace_atomic

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def messages(doc: dict, report_day: str) -> list[str]:
    if doc.get("schema_version") not in ("1.0", "2.0") or doc.get("mode") != "forward_paper":
        raise ValueError("expected forward paper portfolio")
    if abs(sum(p["weight_pct"] for p in doc["positions"]) + doc["cash_weight_pct"] - 100) > 0.01:
        raise ValueError("portfolio weights do not reconcile")
    start = dt.datetime.fromisoformat(doc["started_at"]).astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
    lines = [f"📊 전략 모의 포트폴리오 · {report_day}", f"전략: {doc['strategy']}",
             f"평가 기준일: {doc['as_of']} · 기록 시작(KST): {start}",
             f"평가액 {doc['nav_krw']:,.0f}원 / 원금 {doc['initial_capital_krw']:,.0f}원",
             f"기록 이후 {doc['return_pct']:+.2f}% / 전 평가 대비 {doc['daily_return_pct']:+.2f}%",
             f"누적 모의 거래비용 {doc['fees_krw']:,.0f}원", "", "보유 비중 · 평가손익 · 시세일"]
    for p in sorted(doc["positions"], key=lambda p: -p["weight_pct"]):
        ret = f"{p['return_pct']:+.2f}%" if p["return_pct"] is not None else "미집계"
        lines.append(f"· {p['name']} ({p['id']}) {p['weight_pct']:.2f}% / {ret} / {p['quote_date']}")
    lines += [f"· 현금 {doc['cash_weight_pct']:.2f}% ({doc['cash_krw']:,.0f}원)", "",
              "첫 기록 후 다음 시가 체결 대기 중입니다 (KR·US·FX 완료 일봉 평가)." if doc["status"].startswith("awaiting_") else "완료 일봉·환율 기준 모의 평가입니다.",
              "휴장일에는 최근 평가를 반복 표시합니다. 시세 기준일을 확인하세요.",
              doc["method"], "https://verdict-archive.vercel.app/portfolio"]
    if doc["status"].startswith("awaiting_"):
        lines += ["", "다음 진입 후보 · 종목당 기본 비중 (슬롯·잔고 제한)"]
        lines.extend(f"· {p['name']} ({p['id']}) {p['weight_pct']:.2f}%" for p in doc["target_positions"])
    return chunk(lines, "📊 포트폴리오 (계속)\n")


def chunk(lines: list[str], continuation: str) -> list[str]:
    """Split lines into Telegram-sized messages (sendMessage allows 4096 chars; keep headroom)."""
    chunks = [""]
    for line in lines:
        if len(chunks[-1]) + len(line) + 1 > 3500: chunks.append(continuation)
        chunks[-1] += line + "\n"
    return chunks


def deliver(chunks: list[str], *, scope: str, receipt: Path, force: bool = False) -> int:
    """Send once per recipient and `scope`; store a hashed receipt only after every chunk succeeded."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(); chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID required. See docs/PORTFOLIO.md."); return 1
    key = hashlib.sha256(f"{scope}:{chat}".encode()).hexdigest()
    receipts = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else {"schema_version": "1.0", "delivered": []}
    if key in receipts["delivered"] and not force:
        print("Already delivered for this recipient and scope."); return 0
    for text in chunks:
        try:
            response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=30)
            if response.status_code != 200 or response.json().get("ok") is not True:
                print(f"Telegram delivery failed (HTTP {response.status_code})."); return 1
        except (requests.RequestException, ValueError):
            print("Telegram delivery failed; check connection and bot settings."); return 1
    receipts["delivered"].append(key)
    replace_atomic(receipt, lambda fh: json.dump(receipts, fh))
    print(f"Delivered {len(chunks)} message(s) for {scope}."); return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--force", action="store_true")
    p.add_argument("--receipt", type=Path, default=lake.ROOT / "artifacts" / "telegram" / "delivery.json")
    args = p.parse_args(argv)
    day = dt.datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    doc = json.loads(lake.PAPER_PORTFOLIO.read_text(encoding="utf-8"))
    chunks = messages(doc, day)
    if args.dry_run:
        print("\n\n".join(chunks)); return 0
    return deliver(chunks, scope=day, receipt=args.receipt, force=args.force)  # receipt key stays sha256("{day}:{chat}")


if __name__ == "__main__":
    raise SystemExit(main())
