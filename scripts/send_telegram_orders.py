"""Next-open order plan digest for the funded account (funded-w50-v1). Mock account, not broker orders.

Sends a market's plan only when it is today's: frozen from that market's latest completed session
(own cutoff and holidays) and its targeted open has not started. A missing or older plan means the
pipeline or prices are not ready: nothing is sent for that market and the exit code is nonzero, so a
stale plan is never re-sent as today's. Delivery, chunking, receipts and failure handling are the
shared `send_telegram_portfolio` code; each plan revision is delivered at most once per recipient.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import lake
from backtest.parking_engine import market_open
from funded_account import ACCOUNT_ID, MARKETS, expected_session
from send_telegram_portfolio import chunk, deliver

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

KST = ZoneInfo("Asia/Seoul")
SIDE = {"buy": "매수", "sell": "매도"}


def validate(doc: dict) -> None:
    if doc.get("schema_version") != "1.0" or doc.get("mode") != "forward_paper_funded" or doc.get("account_id") != ACCOUNT_ID:
        raise ValueError("expected the funded forward-paper account artifact")
    perf = doc["performance"]
    start = doc["seed"]["principal_krw"] if doc.get("seed") else perf["initial_capital_krw"]
    if abs(start + sum(c["amount_krw"] for c in doc["contributions"]) - perf["principal_krw"]) > 0.01:
        raise ValueError("principal does not reconcile with contributions")
    if abs(perf["cash_krw"] + sum(p["value_krw"] for p in doc["positions"]) - perf["nav_krw"]) > 1:
        raise ValueError("NAV does not reconcile with cash and positions")


def todays_plans(doc: dict, now: dt.datetime) -> tuple[list[dict], list[str]]:
    plans, problems = [], []
    for market in MARKETS:
        expected = expected_session(market, now).isoformat()
        plan = doc["latest_plans"].get(market)
        if plan is None or plan["basis_session"] < expected:
            problems.append(f"{market}: {expected} 종가 기준 계획 없음(파이프라인·시세 미준비) — 전송하지 않음")
        elif market_open(dt.date.fromisoformat(plan["trading_day"]), market) <= now:
            problems.append(f"{market}: {plan['trading_day']} 시가가 이미 지남 — 지난 계획을 재전송하지 않음")
        else:
            plans.append(plan)
    return plans, problems


def _kst(value: str) -> str:
    return dt.datetime.fromisoformat(value).astimezone(KST).strftime("%Y-%m-%d %H:%M KST")


def messages(doc: dict, plan: dict) -> list[str]:
    snap = plan["account_snapshot"]; perf = doc["performance"]; label = doc["config"]["contribution_day"]["label"]
    revised = f" · 개정 r{plan['revision']}" if plan["revision"] > 1 else ""
    title = "취소된 주문 계획" if plan["status"] == "cancelled" else "익일 시가 주문 계획"
    lines = [f"🧾 {title} · {plan['market']} · {doc['account_id']}{revised}",
             f"기준일(종가) {plan['basis_session']} → 주문일 {plan['trading_day']} 시가 (다음 실제 거래일)",
             f"계획 동결 {_kst(plan['frozen_at'])} · 정책 {plan['policy_version']}"]
    if plan["skipped_closures"]:
        lines.append(f"휴장 건너뜀: {', '.join(plan['skipped_closures'])}")
    if plan.get("revision_reason"):
        lines.append(f"개정 사유: {plan['revision_reason']}")
    lines += [f"평가액(추정) {snap['nav_est_krw']:,.0f}원 · 원금 {snap['principal_krw']:,.0f}원 · "
              f"투자손익 {snap['nav_est_krw'] - snap['principal_krw']:+,.0f}원",
              f"현금 {snap['cash_krw']:,.0f}원 + 납입 {snap['incoming_contributions_krw']:,.0f}원"
              f" − 선행 계획 예약 {snap['reserved_for_earlier_plans_krw']:,.0f}원"
              f" → 계획 후 추정 {snap['cash_after_plan_est_krw']:,.0f}원",
              f"보유 {snap['position_count']}종목 · 계획 후 빈 슬롯 {snap['slots_free']}"]
    seg = doc["segments"]; live = seg["live"]
    lines.append(f"{live['label']}({_kst(live['start_at'])[:10]}~) TWR {live['twr_pct']:+.2f}% · 손익 {live['investment_pnl_krw']:+,.0f}원 (납입 제외)")
    if seg["backtest"]:
        bt = seg["backtest"]
        lines += [f"{bt['label']} {bt['start_at'][:10]}~{bt['end_at'][:10]} TWR {bt['twr_pct']:+.2f}% (공표일 기준, 파이프라인 최초 관측 아님)"
                  if bt["availability"] == "publication_date" else
                  f"{bt['label']} {bt['start_at'][:10]}~{bt['end_at'][:10]} TWR {bt['twr_pct']:+.2f}% (모의, 실거래 아님)",
                  f"합산(백테스트 포함) TWR {seg['combined']['twr_pct']:+.2f}% · 손익 {seg['combined']['investment_pnl_krw']:+,.0f}원"]
    for c in plan["contributions"]:
        lines.append(f"납입 {c['installment']}회차 {c['amount_krw']:,.0f}원 · {c['scheduled_date']} 시가 전 → 현금(신규 진입 재원) · {label}")
    lines.append("")
    for o in plan["orders"]:
        qty = f"{o['est_shares']:,}주" + ("(확정)" if o["side"] == "sell" else "(추정)")
        amount = f"예산 {o['budget_krw']:,.0f}원" if o["side"] == "buy" else f"추정 {o['est_cash_flow_krw']:,.0f}원"
        lines += [f"[{SIDE[o['side']]}] {o['name']} {o['instrument']['symbol']} · {qty} · {amount}",
                  f"  비중 {o['current_weight_pct']:.2f}% → {o['planned_weight_pct']:.2f}% · 종가 {o['est_price']:,.2f}"
                  + (f" × 환율 {o['est_fx']:,.2f}" if o["market"] == "US" else "") + f" · 추정비용 {o['est_cost_krw']:,.0f}원",
                  f"  사유: {o['reason']}"]
    conditions = list(dict.fromkeys(c for o in plan["orders"] for c in o["conditions"]))
    if conditions:
        lines += ["", "체결 조건"]
        lines.extend(f"· {c}" for c in conditions)
    if not plan["orders"]:
        lines.append(f"주문 없음 — {plan['no_trade_reason']}. 현금 유지.")
    if plan["excluded"]:
        lines += ["", "제외"]
        lines.extend(f"· {e['name']} ({e['ticker']}): {e['reason']}" for e in plan["excluded"])
    lines += ["", *(f"· {e['ticker']}: {e['reason']}" for e in doc["excluded_instruments"])]
    nxt = doc["next_contribution"]
    lines += [f"납입 규칙: {doc['config']['contribution_day']['description']} — {label}",
              f"다음 납입: {nxt['installment']}회차 {nxt['amount_krw']:,.0f}원 · {nxt['scheduled_date'] or '휴장표 갱신 필요'} ({label})",
              plan["nav_timing_note"] + ". 수량·금액은 종가 기준 추정이며 실제 체결가가 아님.",
              "모의 계정의 계획이며 실계좌 주문이 아닙니다."]
    return chunk(lines, f"🧾 주문 계획 {plan['market']} {plan['trading_day']} (계속)\n")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--force", action="store_true")
    p.add_argument("--data", type=Path, default=lake.FUNDED_ACCOUNT)
    p.add_argument("--now", help="ISO instant, dry-run only (render a past day's plans)")
    p.add_argument("--receipt", type=Path, default=lake.ROOT / "artifacts" / "telegram" / "orders.json")
    args = p.parse_args(argv)
    if args.now and not args.dry_run:
        p.error("--now is only allowed with --dry-run")
    now = dt.datetime.fromisoformat(args.now) if args.now else _now()
    if not args.data.exists():
        print(f"{ACCOUNT_ID} has not started (no {args.data.name}); nothing to send."); return 0
    doc = json.loads(args.data.read_text(encoding="utf-8"))
    validate(doc)
    plans, problems = todays_plans(doc, now)
    for problem in problems:
        print(problem)
    status = 0
    if plans and not args.dry_run and not (os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() and os.environ.get("TELEGRAM_CHAT_ID", "").strip()):
        # Not configured is not a failure of today's pipeline: warn (visible in Actions) and skip.
        print("::warning::TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping delivery (nothing sent).")
        return 1 if problems else 0
    for plan in plans:
        chunks = messages(doc, plan)
        if args.dry_run:
            print("\n\n".join(chunks)); continue
        status = max(status, deliver(chunks, scope=f"orders:{plan['plan_id']}", receipt=args.receipt, force=args.force))
    return 1 if problems else status


if __name__ == "__main__":
    raise SystemExit(main())
