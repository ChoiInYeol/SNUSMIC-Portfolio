import type { Metadata } from "next";
import { LedgerHeader, LedgerTableView } from "@/components/portfolio-ledger";
import { portfolio } from "@/lib/portfolio-data";
import { buildPortfolioLedger } from "@/lib/portfolio-ledger";

export const metadata: Metadata = { title: "SOTA 운용 원장" };
export default function PortfolioPage() {
  const ledger = buildPortfolioLedger(portfolio);
  const recent = { ...ledger.trades, rows: ledger.trades.rows.filter((r) => r.date >= portfolio.recent_window_start) };
  return <main className="min-h-screen bg-[hsl(var(--ledger-background))] px-3 py-5 sm:px-6"><div className="mx-auto flex max-w-[1680px] flex-col gap-6">
    <LedgerHeader book={portfolio} /><LedgerTableView table={ledger.positions} /><LedgerTableView table={ledger.orders} /><LedgerTableView table={recent} />
    <p className="text-xs text-muted-foreground">가격 KRW/USD / 손익 KRW / 수량은 소수 주식 모델 / 체결과 매매 대기를 구분합니다. 모바일에서는 표를 좌우로 이동하세요.</p>
  </div></main>;
}
