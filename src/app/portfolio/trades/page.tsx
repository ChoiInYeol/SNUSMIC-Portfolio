import Link from "next/link";
import { z } from "zod";
import type { Metadata } from "next";
import { LedgerHeader, LedgerTableView } from "@/components/portfolio-ledger";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, FieldLabel } from "@/components/ui/field";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { portfolio } from "@/lib/portfolio-data";
import { buildPortfolioLedger } from "@/lib/portfolio-ledger";

export const metadata: Metadata = { title: "체결 원장" };
export default async function TradesPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const params = await searchParams;
  const arg = (key: string, fallback: string) => { const v = params[key]; return typeof v === "string" ? v : fallback; };
  const from = arg("from", portfolio.recent_window_start), to = arg("to", portfolio.as_of), q = arg("q", "").trim(), side = arg("side", "all");
  const valid = z.iso.date().safeParse(from).success && z.iso.date().safeParse(to).success && from <= to && ["all", "buy", "sell"].includes(side);
  const trades = buildPortfolioLedger(portfolio).trades;
  const filtered = { ...trades, rows: valid ? trades.rows.filter((r) => r.date >= from && r.date <= to && (side === "all" || r.side === side) && `${r.symbol} ${r.cells[2].text}`.toLowerCase().includes(q.toLowerCase())) : [] };
  return <main className="min-h-screen bg-[hsl(var(--ledger-background))] px-3 py-5 sm:px-6"><div className="mx-auto flex max-w-[1680px] flex-col gap-5">
    <LedgerHeader book={portfolio} />
    <form action="/portfolio/trades" method="get" className="flex flex-wrap items-end gap-3">
      <Field className="w-40"><FieldLabel htmlFor="from">시작일</FieldLabel><Input id="from" name="from" type="date" defaultValue={from} required /></Field>
      <Field className="w-40"><FieldLabel htmlFor="to">종료일</FieldLabel><Input id="to" name="to" type="date" defaultValue={to} required /></Field>
      <Field className="w-32"><FieldLabel htmlFor="side">매매</FieldLabel><Select name="side" defaultValue={["all", "buy", "sell"].includes(side) ? side : "all"}><SelectTrigger id="side"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="all">전체</SelectItem><SelectItem value="buy">매수</SelectItem><SelectItem value="sell">매도</SelectItem></SelectGroup></SelectContent></Select></Field>
      <Field className="w-48"><FieldLabel htmlFor="q">종목명 / 코드</FieldLabel><Input id="q" name="q" defaultValue={q} /></Field>
      <Button type="submit">조회</Button><Link className="self-end py-2 text-sm underline" href={`/portfolio/trades?from=${portfolio.simulation_start}&to=${portfolio.as_of}`}>전체 기간</Link>
    </form>
    {!valid && <p role="alert" className="text-sm text-down">조회 날짜와 매매 구분을 확인하세요.</p>}
    <LedgerTableView table={filtered} />
    <p className="text-xs text-muted-foreground">현금흐름은 매매 비용 포함 / 매수의 실현손익은 공란 / 매도에는 원래 진입일과 매수가를 표시합니다.</p>
  </div></main>;
}
