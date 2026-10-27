import type { SotaPortfolio } from "./sota-portfolio";

export const LEDGER_COMMANDS = ["/status", "/holdings", "/signals", "/trades"] as const;
export type LedgerCommand = typeof LEDGER_COMMANDS[number];
export type Cell = { text: string; href?: string; pnl?: number };
export type Column = { label: string; width: number; numeric?: boolean };
export type LedgerRow = { id: string; symbol: string; date: string; side?: "buy" | "sell"; cells: Cell[] };
export type LedgerTable = { key: "positions" | "trades" | "orders"; title: string; columns: Column[]; rows: LedgerRow[]; empty: string };
export const cleanLedgerText = (s: string) => s.replace(/[—·\t\r\n]/g, " ");
export const number = (v: number, digits = 0) => v.toLocaleString("en-US", { maximumFractionDigits: digits });
const pct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;
const value = (v: number, digits = 0): Cell => ({ text: number(v, digits) });
const profit = (v: number | null, percent = false): Cell => v === null ? { text: "" } : { text: percent ? pct(v) : `${v > 0 ? "+" : ""}${number(Math.round(v))}`, pnl: v };
const text = (s: string): Cell => ({ text: cleanLedgerText(s) });
const price = (v: number | null, market: string): Cell => v === null ? text("") : text(`${market === "US" ? "$" : ""}${number(v, market === "US" ? 2 : 0)}`);
const symbol = (market: string, ticker: string) => `${market}-${ticker}`.toLowerCase();
const company = (market: string, ticker: string, name: string): Cell => ({ text: `${cleanLedgerText(name)} (${ticker})`, href: `/stocks/${symbol(market, ticker)}` });
const col = (label: string, width: number, numeric = false): Column => ({ label, width, numeric });

export function ledgerSummary(doc: SotaPortfolio): string {
  const regime = doc.regime ? `${number(doc.regime.kospi_close, 2)} / 200MA ${number(doc.regime.kospi_ma200, 2)} ${doc.regime.state}` : "자료 없음";
  return `평가 ${doc.as_of}  |  평가액 ${number(doc.nav_krw)}원  |  수익률 ${pct(doc.return_pct)}%  |  보유 ${doc.positions.length}/${doc.slots.max_positions}  |  파킹 ${doc.parked_weight_pct.toFixed(2)}%  |  현금 ${doc.cash_weight_pct.toFixed(2)}%  |  KOSPI ${regime}`;
}

export function buildPortfolioLedger(doc: SotaPortfolio): Record<LedgerTable["key"], LedgerTable> {
  const positions: LedgerTable = { key: "positions", title: "보유 원장", empty: "보유 주식 없음", columns: [
    col("종목", 340), col("진입일", 160), col("수량(주)", 150, true), col("매수가", 130, true), col("현재가", 130, true),
    col("비중(%)", 105, true), col("평가손익(원)", 180, true), col("수익률(%)", 125, true), col("시세일", 160),
  ], rows: [...doc.positions].sort((a, b) => b.weight_pct - a.weight_pct).map((p) => ({
    id: `position-${symbol(p.market, p.ticker)}`, symbol: symbol(p.market, p.ticker), date: p.entry_date,
    cells: [company(p.market, p.ticker, p.name), text(p.entry_date), value(p.shares, 4), price(p.entry_price, p.market), price(p.current_price, p.market),
      { text: p.weight_pct.toFixed(2) }, profit(p.pnl_krw), profit(p.return_pct, true), text(p.quote_date)],
  })) };
  const trades: LedgerTable = { key: "trades", title: "체결 원장", empty: "조회 기간 내 체결 없음", columns: [
    col("체결일", 160), col("매매", 75), col("종목", 340), col("진입일", 160), col("수량(주)", 150, true),
    col("매수가", 130, true), col("체결가", 130, true), col("현금흐름(원)", 185, true), col("실현손익(원)", 180, true), col("수익률(%)", 125, true),
  ], rows: [...doc.fills].reverse().map((f) => ({
    id: `fill-${symbol(f.market, f.ticker)}-${f.date}-${f.side}`, symbol: symbol(f.market, f.ticker), date: f.date, side: f.side,
    cells: [text(f.date), text(f.side === "buy" ? "매수" : "매도"), company(f.market, f.ticker, f.name), text(f.entry_date), value(f.shares, 4),
      price(f.entry_price, f.market), price(f.price, f.market), value(f.cash_flow_krw), profit(f.pnl_krw), profit(f.return_pct, true)],
  })) };
  const orders: LedgerTable = { key: "orders", title: "매매 대기", empty: "매매 대기 없음", columns: [
    col("상태", 130), col("종목", 340), col("기준일", 160), col("진입일", 160), col("수량(주)", 150, true),
    col("현재가", 130, true), col("스탑", 130, true), col("스탑거리(%)", 140, true), col("사유", 320),
  ], rows: [
    ...doc.positions.filter((p) => p.exit_pending || (p.dist_to_stop_pct !== null && p.dist_to_stop_pct <= 3)).map((p) => ({
      id: `order-${symbol(p.market, p.ticker)}`, symbol: symbol(p.market, p.ticker), date: doc.as_of, side: "sell" as const,
      cells: [text(p.exit_pending ? "매도 대기" : "스탑 근접"), company(p.market, p.ticker, p.name), text(doc.as_of), text(p.entry_date), value(p.shares, 4),
        price(p.current_price, p.market), price(p.stop_level, p.market), text(p.dist_to_stop_pct?.toFixed(2) ?? ""), text(p.exit_pending ? "종가 스탑 이탈 / 다음 시가 청산" : "스탑까지 3% 이내")],
    })), ...doc.buy_candidates.map((b) => ({
      id: `order-${symbol(b.market, b.ticker)}`, symbol: symbol(b.market, b.ticker), date: b.entry_basis_date ?? doc.as_of, side: "buy" as const,
      cells: [text("매수 대기"), company(b.market, b.ticker, b.display_name), text(b.entry_basis_date ?? ""), text(""), text(""), text(""), text(""), text(""), text(b.entry_reason)],
    })),
  ] };
  return { positions, trades, orders };
}

export function commandTable(command: LedgerCommand, doc: SotaPortfolio): LedgerTable {
  const ledger = buildPortfolioLedger(doc);
  if (command === "/trades") return { ...ledger.trades, rows: ledger.trades.rows.filter((r) => r.date >= doc.recent_window_start) };
  return command === "/signals" ? ledger.orders : ledger.positions;
}

export function ledgerUri(command: LedgerCommand, doc?: SotaPortfolio): string {
  return command === "/trades" ? `/portfolio/trades${doc ? `?from=${doc.recent_window_start}&to=${doc.as_of}` : ""}` : `/portfolio#${command === "/signals" ? "orders" : "positions"}`;
}

export function ledgerText(command: LedgerCommand, doc: SotaPortfolio): string {
  const table = commandTable(command, doc);
  return [table.title, doc.strategy, ledgerSummary(doc), "", table.columns.map((c) => c.label).join("\t"),
    ...table.rows.map((r) => r.cells.map((c) => c.text).join("\t")), ...(table.rows.length ? [] : [table.empty]),
    "", `https://verdict-archive.vercel.app${ledgerUri(command, doc)}`].join("\n");
}
