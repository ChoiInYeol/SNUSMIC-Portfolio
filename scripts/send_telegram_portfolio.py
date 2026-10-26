"""Daily SOTA model book: regime, actual model quantities, P&L, exits and pending signals.

Default source is the current research winner, explicitly labelled as a backtest model.
--source paper retains the separate forward-paper account digest.

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


def price(value, market):
    if value is None:
        return "미집계"
    return f"${value:,.2f}" if market == "US" else f"{value:,.0f}원"


def sota_messages(doc: dict, report_day: str) -> list[str]:
    from sota_portfolio import validate
    validate(doc)
    rows = doc["positions"]
    regime = doc["regime"]
    lines = [f"📊 SOTA 모델 포트폴리오 · {report_day}", f"전략: {doc['strategy']}",
             f"평가 기준일: {doc['as_of']} · 종가 기준", ""]
    lag = (dt.date.fromisoformat(report_day) - dt.date.fromisoformat(doc["as_of"])).days
    if lag < 0:
        raise ValueError("SOTA evaluation date is in the future")
    if lag > 3:
        lines += [f"⚠ 평가일이 발송일보다 {lag}일 이전입니다. 휴장·일일 갱신 상태를 확인하세요.", ""]
    if regime:
        gap = (regime["kospi_close"] / regime["kospi_ma200"] - 1) * 100
        lines += [f"KOSPI 200MA 레짐 {regime['state']} · 이격 {gap:+.2f}%",
                  f"KOSPI {regime['kospi_close']:,.2f} / 200일선 {regime['kospi_ma200']:,.2f}"]
        if doc["strategy"] == "W_allweather_chandelier":
            lines += ["W는 레짐 필터 없음: OFF여도 신규 매수 규칙·올웨더 파킹 유지."]
        else:
            lines += [regime["note"]]
    else:
        lines += ["KOSPI 200MA 레짐: 자료 없음"]
    winners = sum(p["pnl_krw"] > 0 for p in rows)
    losers = sum(p["pnl_krw"] < 0 for p in rows)
    lines += ["", f"모델 평가액 {doc['nav_krw']:,.0f}원 / 시작자금 {doc['initial_capital_krw']:,.0f}원",
              f"백테스트 누적 {doc['return_pct']:+.2f}% ({doc['simulation_start']}부터)",
              f"주식 {sum(p['weight_pct'] for p in rows):.2f}% · {doc['parking_name']} 파킹 {doc['parked_weight_pct']:.2f}% "
              f"· 현금 {doc['cash_weight_pct']:.2f}%",
              f"보유 {len(rows)}종목 · 이익 {winners} / 손실 {losers} / 보합 {len(rows) - winners - losers}",
              "", "보유 수량 · 현재 비중 · 평가손익 (진입비용·환율 포함)"]
    if not rows:
        lines += ["· 보유 주식 없음"]
    for p in rows:
        shares = f"{p['shares']:,.4f}".rstrip("0").rstrip(".")
        tag = "이익" if p["pnl_krw"] > 0 else "손실" if p["pnl_krw"] < 0 else "보합"
        lines += [f"· {p['name']} ({p['market']}:{p['ticker']}) {shares}주 · {p['weight_pct']:.2f}%",
                  f"  {tag} {p['pnl_krw']:+,.0f}원 ({p['return_pct']:+.2f}%) · "
                  f"진입 {price(p['entry_price'], p['market'])} → 현재 {price(p['current_price'], p['market'])} · 시세 {p['quote_date']}"]
    lines += [f"· {doc['parking_name']} 파킹 {doc['parked_krw']:,.0f}원 ({doc['parked_weight_pct']:.2f}%)",
              f"· 현금 {doc['cash_krw']:,.0f}원 ({doc['cash_weight_pct']:.2f}%)", "",
              "매도 대기·근접 (샹들리에 스탑까지 3% 이내)"]
    sells = sorted((p for p in rows if p["exit_pending"] or
                    (p["dist_to_stop_pct"] is not None and p["dist_to_stop_pct"] <= 3)),
                   key=lambda p: p["dist_to_stop_pct"] if p["dist_to_stop_pct"] is not None else float("inf"))
    if not sells:
        lines += ["· 없음"]
    for p in sells:
        status = "종가 스탑 이탈 → 다음 가용 시가 매도 대기" if p["exit_pending"] else "스탑 근접"
        distance = f"{p['dist_to_stop_pct']:+.2f}%" if p["dist_to_stop_pct"] is not None else "미집계"
        lines += [f"· {p['name']} ({p['ticker']}) · {status}",
                  f"  현재 {price(p['current_price'], p['market'])} / 스탑 {price(p['stop_level'], p['market'])} · 거리 {distance}"]
    slots = doc["slots"]
    lines += ["", f"매수 대기 후보 · 보유 {slots['open']}/{slots['max_positions']} · 빈 슬롯 {slots['available']}"]
    if not doc["buy_candidates"]:
        lines += ["· 없음 — 최근 5거래일 리포트 중 다음 시가 대기 후보 없음"]
    else:
        lines += ["최근 5거래일 신규 매수 리포트 → 다음 가용 시가 후보. 슬롯·잔액 확인 후 진입."]
        if slots["available"] <= 0:
            lines += ["· 슬롯이 가득 차 신규 진입 제한"]
        if doc["cash_krw"] + doc["parked_krw"] < doc["nav_krw"] * 0.05 * 0.5:
            lines += ["· 최소 진입 예산(기본 NAV 5%의 절반) 미달: 잔액 부족"]
        for b in doc["buy_candidates"]:
            lines += [f"· {b['display_name']} ({b['market']}:{b['ticker']}) · {b['entry_basis_date'] or '다음 가용 시가'}",
                      f"  {b['entry_reason']}"]
    lines += ["", f"최근 익절·손절 · {doc['recent_window_start']}~{doc['as_of']} (최근 20거래일)"]
    if not doc["recent_exits"]:
        lines += ["· 청산 없음"]
    for t in doc["recent_exits"]:
        tag = "수익 실현" if t["pnl_krw"] > 0 else "손실 확정" if t["pnl_krw"] < 0 else "보합 청산"
        reason = "샹들리에 추적스탑" if "chandelier" in t["exit_reason"].lower() else t["exit_reason"]
        lines += [f"· {t['exit_date']} {t['name']} ({t['ticker']}) · {tag} {t['pnl_krw']:+,.0f}원 ({t['return_pct']:+.2f}%)",
                  f"  {price(t['entry_price'], t['market'])} → {price(t['exit_price'], t['market'])} · {reason}"]
    if doc["recent_closed_count"] > len(doc["recent_exits"]):
        lines += [f"· 총 {doc['recent_closed_count']}건 중 최신 {len(doc['recent_exits'])}건 표시"]
    lines += ["", "수량은 시작자금 기준 백테스트 모델의 소수 주식 수량입니다. 정수 주문 수량과 다릅니다.",
              "현재 비중은 평가액 기준이며 기본 진입 비중 5%와 다를 수 있습니다. 올웨더는 합성지수 프록시입니다.",
              "선정된 SOTA의 과거 시뮬레이션 상태이며 실계좌·전진 모의계좌 보유 내역이 아닙니다.",
              "https://verdict-archive.vercel.app/strategy"]
    return chunk(lines, "📊 SOTA 포트폴리오 (계속)\n")


def chunk(lines: list[str], continuation: str) -> list[str]:
    """Split lines into Telegram-sized messages (sendMessage allows 4096 chars; keep headroom)."""
    chunks = [""]
    for line in lines:
        if len(chunks[-1]) + len(line) + 1 > 3500: chunks.append(continuation)
        remaining = line + "\n"
        while remaining:
            room = 3500 - len(chunks[-1])
            if room == 0:
                chunks.append(continuation)
                room = 3500 - len(continuation)
            chunks[-1] += remaining[:room]
            remaining = remaining[room:]
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
    p.add_argument("--source", choices=("sota", "paper"), default="sota")
    p.add_argument("--receipt", type=Path, default=lake.ROOT / "artifacts" / "telegram" / "delivery.json")
    args = p.parse_args(argv)
    day = dt.datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    if args.source == "sota":
        from sota_portfolio import validate
        doc = json.loads(lake.GOLD_SOTA_PORTFOLIO.read_text(encoding="utf-8"))
        model = json.loads((lake.WEB_DATA / "strategy-backtest.json").read_text(encoding="utf-8"))
        validate(doc, model)
        chunks = sota_messages(doc, day)
    else:
        doc = json.loads(lake.PAPER_PORTFOLIO.read_text(encoding="utf-8"))
        chunks = messages(doc, day)
    if args.dry_run:
        print("\n\n".join(chunks)); return 0
    scope = f"sota:{day}" if args.source == "sota" else day
    return deliver(chunks, scope=scope, receipt=args.receipt, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
