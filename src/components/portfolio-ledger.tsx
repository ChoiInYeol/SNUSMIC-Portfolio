import Link from "next/link";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { SotaPortfolio } from "@/lib/sota-portfolio";
import { ledgerSummary, type LedgerTable } from "@/lib/portfolio-ledger";

export function LedgerHeader({ book }: { book: SotaPortfolio }) {
  return <header className="space-y-3 border-b border-border pb-4">
    <nav aria-label="운용 원장" className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground [&_a]:underline-offset-4 [&_a]:hover:underline [&_a]:focus-visible:ring-2 [&_a]:focus-visible:ring-ring">
      <Link href="/">판결 아카이브</Link><Link href="/portfolio">보유 원장</Link><Link href="/portfolio/trades">체결 원장</Link>
      <Link href="/portfolio/paper">1억 전진 모의</Link><Link href="/portfolio/funded">납입식 계정</Link><Link href="/api/v2/portfolio" prefetch={false} target="_blank" rel="noopener">JSON</Link>
    </nav>
    <h1 className="text-lg font-bold">SOTA 운용 원장 <Link className="ml-2 break-all font-mono text-xs font-normal underline" href={`/strategy?s=${encodeURIComponent(book.strategy)}`}>{book.strategy}</Link></h1>
    <p className="text-xs leading-6 tabular-nums">{ledgerSummary(book)}</p>
    <p className="text-xs text-muted-foreground">백테스트 모델 보유 상태 / 실계좌 잔고 아님 / KOSPI 레짐은 관측값이며 W의 진입 차단 조건이 아닙니다.</p>
  </header>;
}

export function LedgerTableView({ table }: { table: LedgerTable }) {
  return <section id={table.key} className="min-w-0 space-y-2 scroll-mt-4">
    <div className="flex items-baseline gap-4"><h2 className="text-sm font-bold">{table.title} <span className="font-normal tabular-nums text-muted-foreground">{table.rows.length}건</span></h2>
      {table.key === "trades" && <Link className="text-xs underline" href="/portfolio/trades">기간별 전체 체결</Link>}</div>
    <div tabIndex={0} role="region" aria-label={`${table.title} 가로 스크롤 표`} className="overflow-x-auto border-y border-border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&_[data-slot=table-container]]:overflow-visible">
      <Table className="min-w-max text-xs tabular-nums">
        <TableHeader><TableRow>{table.columns.map((c) => <TableHead key={c.label} scope="col" style={{ minWidth: c.width * 0.7 }} className={`h-8 px-2 text-foreground ${c.numeric ? "text-right" : ""}`}>{c.label}</TableHead>)}</TableRow></TableHeader>
        <TableBody>{table.rows.length ? table.rows.map((r) => <TableRow key={r.id} id={r.id}>{r.cells.map((c, i) => <TableCell key={i} className={`px-2 py-2 ${table.columns[i].numeric ? "text-right" : ""} ${c.pnl === undefined || c.pnl === 0 ? "" : c.pnl > 0 ? "text-up" : "text-down"}`}>
          {c.href ? <Link className="underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring" href={c.href}>{c.text}</Link> : c.text}
        </TableCell>)}</TableRow>) : <TableRow><TableCell colSpan={table.columns.length} className="py-6 text-muted-foreground">{table.empty}</TableCell></TableRow>}</TableBody>
      </Table>
    </div>
  </section>;
}
