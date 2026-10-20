"use client";

import Link from "next/link";
import { useState } from "react";
import { z } from "zod";
import { AlertTriangle, Scale } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { dateLabel, seenBy } from "@/lib/report-meta";
import { clubStances, RELATION_TIER, TIER_LABEL, type OpinionReport } from "@/lib/research-compare";
import { dominantKind, type Filters, type FulltextManifest, type StockGroup } from "@/lib/report-search";
import { ratingClassLabels, schoolShort } from "@/lib/verdict";
import { cn } from "@/lib/utils";

// The slice of GET /api/v2/instruments/{id} (InstrumentDocumentSchema in src/lib/api-v2.ts) this panel reads.
// Kept narrow so /search does not bundle the whole v2 contract; unknown fields pass through.
const InstrumentReportsSchema = z.object({
  data: z.object({
    id: z.string(),
    name: z.string(),
    market: z.enum(["KR", "US"]),
    reports: z.array(z.object({
      school: z.enum(["smic", "yig", "star", "kuvic", "ewha", "voera"]),
      era: z.enum(["modern", "archive"]),
      report_date: z.iso.date().nullable(),
      rating: z.string().nullable(),
      rating_class: z.enum(["buy", "soft_buy", "sell"]),
      target_price: z.number().nullable(),
      source_name: z.string(),
    })),
  }),
});

const TOP = 6;
type Row = { group: StockGroup; slug: string; name: string; market: string; opinions: OpinionReport[] };

async function loadRows(manifest: FulltextManifest, groups: StockGroup[], filters: Filters): Promise<Row[]> {
  // first_seen per transcript file name (v2 `source_name` = basename of the manifest `path`, unique across clubs) —
  // the as-of view hides what was not yet collected; a report the index does not know stays hidden there.
  const seen = new Map(manifest.reports.map((r) => [r.path.split("/").pop() ?? r.path, r.first_seen]));
  return Promise.all(groups.map(async (group) => {
    const stock = manifest.stocks[group.stock];
    const res = await fetch(`/api/v2/instruments/${stock.slug}`);
    if (!res.ok) throw new Error(`instrument ${stock.slug}: ${res.status}`);
    const doc = InstrumentReportsSchema.parse(await res.json());
    const opinions = doc.data.reports
      .filter((r): r is typeof r & { report_date: string } => r.era === "modern" && r.report_date !== null)
      .filter((r) => (!filters.clubs?.length || filters.clubs.includes(r.school))
        && (!filters.from || r.report_date >= filters.from) && (!filters.to || r.report_date <= filters.to)
        && (!filters.collectedBy || seenBy(r.report_date, seen.get(r.source_name), filters.collectedBy)))
      .map((r) => ({ id: r.source_name, school: r.school, date: r.report_date, firstSeen: seen.get(r.source_name) ?? null,
        rating: r.rating, ratingClass: r.rating_class, targetPrice: r.target_price }));
    return { group, slug: stock.slug, name: stock.name, market: stock.market, opinions };
  }));
}

/** Topic → the strongest related stocks side by side: who covers them, whether clubs disagree, how far targets spread. */
export function TopicCompare({ manifest, stocks, filters, searchKey }: { manifest: FulltextManifest; stocks: StockGroup[]; filters: Filters; searchKey: string }) {
  const [state, setState] = useState<{ key: string; rows?: Row[]; error?: true; loading?: true } | null>(null);
  const candidates = stocks.filter((g) => manifest.stocks[g.stock].page && RELATION_TIER[dominantKind(g)] !== "passing").slice(0, TOP);
  const current = state?.key === searchKey ? state : null;
  const load = () => {
    setState({ key: searchKey, loading: true });
    loadRows(manifest, candidates, filters).then(
      (rows) => setState({ key: searchKey, rows }),
      () => setState({ key: searchKey, error: true }),
    );
  };
  const stockHref = (slug: string) => {
    const q = filters.collectedBy ? `?${new URLSearchParams({ view: "asof", asof: filters.collectedBy })}` : "";
    return `/stocks/${slug}${q}#compare`;
  };

  return (
    <section aria-labelledby="compare-heading" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="compare-heading" className="font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">비교 — 같은 주제 상위 종목의 학회 의견</h2>
        <p className="font-mono text-[10px] text-muted-foreground">주된 노출·부수적 연관 상위 {TOP}종목 · 채점 대상(2019-07 이후) 리포트</p>
      </div>
      {candidates.length < 2 ? (
        <p className="text-xs text-muted-foreground">비교할 종목 페이지가 있는 관련 종목이 2개 미만입니다.</p>
      ) : !current ? (
        <Button type="button" variant="outline" size="sm" onClick={load}><Scale className="size-4" aria-hidden="true" />{candidates.length}종목 의견 비교 불러오기</Button>
      ) : current.loading ? (
        <div role="status" aria-label="비교 불러오는 중" className="space-y-2"><Skeleton className="h-8 w-full" /><Skeleton className="h-24 w-full" /></div>
      ) : current.error || !current.rows ? (
        <Alert variant="destructive">
          <AlertTriangle className="size-4" aria-hidden="true" />
          <AlertTitle>종목 리소스를 불러오지 못했습니다</AlertTitle>
          <AlertDescription><Button type="button" size="sm" variant="outline" className="mt-1 w-fit" onClick={load}>다시 시도</Button></AlertDescription>
        </Alert>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>종목</TableHead><TableHead>주제 연관</TableHead><TableHead>학회별 최신 의견 · 목표가</TableHead>
                <TableHead>의견</TableHead><TableHead className="text-right">목표가 차이</TableHead><TableHead>최근 발간</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {current.rows.map(({ group, slug, name, market, opinions }) => {
                const s = clubStances(opinions);
                const tier = RELATION_TIER[dominantKind(group)];
                return (
                  <TableRow key={slug}>
                    <TableCell><Link href={stockHref(slug)} className="font-bold underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{name}</Link></TableCell>
                    <TableCell className="text-xs">{TIER_LABEL[tier].title}</TableCell>
                    <TableCell className="min-w-[14rem] whitespace-normal">
                      {s.latest.length === 0 ? <span className="text-xs text-muted-foreground">이 조건의 채점 리포트 없음</span> : (
                        <ul className="flex flex-wrap gap-1">
                          {s.latest.map((r) => (
                            <li key={r.id} className="rounded-sm border border-border px-1.5 py-0.5 font-mono text-[10px] tnum">
                              {schoolShort[r.school]} {ratingClassLabels[r.ratingClass]}{r.targetPrice !== null && ` ${market === "US" ? "$" : ""}${r.targetPrice.toLocaleString("ko-KR")}`}
                            </li>
                          ))}
                        </ul>
                      )}
                    </TableCell>
                    <TableCell>
                      {s.latest.length > 1
                        ? <Badge variant="outline" className={cn("rounded-sm text-[10px]", s.split ? "border-warn/70 bg-warn/10 text-warn" : "border-border")}>{s.split ? "엇갈림" : "일치"}</Badge>
                        : <span className="text-[10px] text-muted-foreground">학회 {s.latest.length}곳</span>}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs tnum">{s.targetSpreadPct === null ? "—" : `${s.targetSpreadPct.toFixed(0)}%`}</TableCell>
                    <TableCell className="font-mono text-xs tnum">{s.latest[0] ? dateLabel(s.latest[0].date) : "—"}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
          <p className="text-[11px] leading-4 text-muted-foreground">
            종목을 누르면 그 종목의 목표가·등급 변경 이력으로 갑니다{filters.collectedBy ? ` (같은 ${dateLabel(filters.collectedBy)} 당시 수집 관점 유지)` : ""}. 주제 연관은 본문 근거의 양이지 투자 매력도가 아니며, 의견 차이는 관측된 기록입니다.
          </p>
        </>
      )}
    </section>
  );
}
