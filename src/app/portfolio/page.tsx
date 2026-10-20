import fs from "node:fs/promises";
import path from "node:path";
import type { Metadata } from "next";
import { SiteHeader } from "@/components/site-header";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { Table, TableHeader, TableHead, TableBody, TableRow, TableCell } from "@/components/ui/table";
import { PaperPortfolioSchema } from "@/lib/paper-portfolio";
import { cn, formatPct } from "@/lib/utils";
export const metadata: Metadata = { title: "모의 포트폴리오 — 판결 아카이브" };
export default async function PortfolioPage() {
  const d = PaperPortfolioSchema.parse(JSON.parse(await fs.readFile(path.join(process.cwd(), "public/research/portfolio.json"), "utf8")));
  return <div className="mx-auto max-w-5xl px-4 py-8 sm:px-8"><SiteHeader eyebrow="FORWARD PAPER PORTFOLIO" /><main className="flex flex-col gap-6 pt-8">
    <div><h1 className="text-3xl font-bold">전략 모의 포트폴리오</h1><p className="mt-2 break-all text-sm text-muted-foreground">{d.strategy} · 평가 {d.as_of} · 기록 시작(KST) {new Date(d.started_at).toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul" })}</p></div>
    <dl className="grid grid-cols-2 gap-4 rounded-md border p-4 sm:grid-cols-4"><div><dt>평가액</dt><dd className="font-mono tabular-nums">{d.nav_krw.toLocaleString("ko-KR")}원</dd></div><div><dt>기록 이후</dt><dd className={cn("tabular-nums", d.return_pct >= 0 ? "text-up" : "text-down")}>{formatPct(d.return_pct)}</dd></div><div><dt>전 평가 대비</dt><dd className={cn("tabular-nums", d.daily_return_pct >= 0 ? "text-up" : "text-down")}>{formatPct(d.daily_return_pct)}</dd></div><div><dt>모의 거래비용</dt><dd className="tabular-nums">{d.fees_krw.toLocaleString("ko-KR")}원</dd></div></dl>
    <Alert><AlertTitle>{d.status === "awaiting_next_open" ? "첫 기록 완료 · 다음 시가 체결 대기" : "백테스트와 동일 엔진의 전진 모의 기록"}</AlertTitle><AlertDescription>{d.method}</AlertDescription></Alert>
    {!!d.data_issues.length && <Alert variant="destructive"><AlertTitle>일부 입력을 확인하세요</AlertTitle><AlertDescription>{d.data_issues.join(" · ")}</AlertDescription></Alert>}
    <Table><TableHeader><TableRow><TableHead>자산</TableHead><TableHead>현재 비중</TableHead><TableHead>평가손익</TableHead><TableHead>시세일</TableHead></TableRow></TableHeader><TableBody>{d.positions.map(p => <TableRow key={p.id}><TableCell>{p.name}<p className="text-xs text-muted-foreground">{p.id}</p></TableCell><TableCell className="tabular-nums">{p.weight_pct.toFixed(2)}%</TableCell><TableCell className={cn("tabular-nums", (p.return_pct ?? 0) >= 0 ? "text-up" : "text-down")}>{formatPct(p.return_pct)}</TableCell><TableCell className="tabular-nums">{p.quote_date}</TableCell></TableRow>)}<TableRow><TableCell>현금</TableCell><TableCell className="tabular-nums">{d.cash_weight_pct.toFixed(2)}%</TableCell><TableCell>—</TableCell><TableCell>{d.as_of}</TableCell></TableRow></TableBody></Table>
    <p className="text-sm text-muted-foreground">텔레그램 연동 시 매일 20:00 KST 알림. 휴장일에는 최근 평가를 표시합니다. 올웨더·KOSPI는 합성지수 프록시이며 실제 계좌 체결과 다릅니다.</p>
    {d.status === "awaiting_next_open" && <section><h2 className="mb-3 font-bold">다음 시가 진입 후보 · 현재 보유와 구분</h2><p className="mb-3 text-xs text-muted-foreground">종목당 기본 비중이며 슬롯·잔고에 따라 체결됩니다. 파라미터는 계정 시작 시 고정합니다.</p><Table><TableHeader><TableRow><TableHead>자산</TableHead><TableHead>기본 진입 비중</TableHead></TableRow></TableHeader><TableBody>{d.target_positions.map(p => <TableRow key={p.id}><TableCell>{p.name}</TableCell><TableCell className="tabular-nums">{p.weight_pct.toFixed(2)}%</TableCell></TableRow>)}</TableBody></Table></section>}
    <section><h2 className="mb-3 font-bold">실행 기록</h2>{d.fills.length ? <Table><TableHeader><TableRow><TableHead>날짜 / 종목</TableHead><TableHead>동작 / 체결가</TableHead><TableHead>모의 비용</TableHead></TableRow></TableHeader><TableBody>{d.fills.slice(-30).reverse().map((f, i) => <TableRow key={`${f.date}-${f.ticker}-${i}`}><TableCell>{f.date}<p>{f.ticker}</p></TableCell><TableCell className="tabular-nums">{f.side === "buy" ? "매수" : "매도"} · {f.price.toLocaleString("ko-KR")}</TableCell><TableCell className="tabular-nums">{f.fees_krw.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}원</TableCell></TableRow>)}</TableBody></Table> : <p className="text-sm text-muted-foreground">아직 체결된 주문이 없습니다.</p>}</section>
  </main></div>;
}
