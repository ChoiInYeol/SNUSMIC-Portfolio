"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { AlertTriangle, FileText, Network, Search } from "lucide-react";
import { CandleChart, type Candle, type ChartFocus, type ReportMark, type TradeMark } from "@/components/candle-chart";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { dateLabel, seenBy } from "@/lib/report-meta";
import { clubStances, coMentions, RELATION_TIER, targetHistory, TIER_LABEL, type ExposureTier, type RatingClass } from "@/lib/research-compare";
import type { FulltextManifest, RelationKind } from "@/lib/report-search";
import { ratingClassLabels, schoolShort } from "@/lib/verdict";
import { cn, formatPct, formatPrice } from "@/lib/utils";

/** One report as the stock page's research flow needs it — a chart mark plus the comparison/evidence fields. */
export type StockReport = ReportMark & {
  firstSeen: string | null;
  rating: string | null;
  ratingClass: RatingClass;
  startClose: number | null;
  returns: { d30: number | null; d90: number | null; d365: number | null; latest: number | null };
  sourceMdUrl: string | null;
  sourcePdfUrl: string | null;
};

// The search index client (zod schemas, gzip loader) and the evidence sheet load on first use, not with the page.
const EvidenceSheet = dynamic(() => import("@/components/research/evidence-sheet").then((m) => m.EvidenceSheet), { ssr: false });

// Shareable state lives in the URL: ?report=<id>&date=YYYY-MM-DD (from /search) &view=asof&asof=YYYY-MM-DD.
const URL_EVENT = "verdict:stock-url";
const subscribeUrl = (onChange: () => void) => {
  window.addEventListener("popstate", onChange);
  window.addEventListener(URL_EVENT, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(URL_EVENT, onChange);
  };
};
const isDate = (v: string | null): v is string => v !== null && /^\d{4}-\d{2}-\d{2}$/.test(v);

function useUrlState() {
  const search = useSyncExternalStore(subscribeUrl, () => window.location.search, () => "");
  const state = useMemo(() => {
    const p = new URLSearchParams(search);
    return { report: p.get("report"), date: isDate(p.get("date")) ? p.get("date") : null, asof: p.get("view") === "asof", asofDate: isDate(p.get("asof")) ? p.get("asof") : null };
  }, [search]);
  const patch = useCallback((next: Record<string, string | null>) => {
    const url = new URL(window.location.href);
    for (const [k, v] of Object.entries(next)) {
      if (v === null) url.searchParams.delete(k);
      else url.searchParams.set(k, v);
    }
    window.history.replaceState(null, "", url);
    window.dispatchEvent(new Event(URL_EVENT));
  }, []);
  return [state, patch] as const;
}

const label = "font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground";

function Perspective({ asof, asofDate, total, shown, onChange, onDate, canAsof }: {
  asof: boolean; asofDate: string | null; total: number; shown: number; canAsof: boolean;
  onChange: (asof: boolean) => void; onDate: (d: string) => void;
}) {
  return (
    <div className="mb-3 flex flex-col gap-2 rounded-md border border-border bg-background/60 p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className={label}>관점</span>
        <ToggleGroup type="single" variant="outline" size="sm" value={asof ? "asof" : "now"} aria-label="리포트를 보는 관점"
          onValueChange={(v) => { if (v) onChange(v === "asof"); }}>
          <ToggleGroupItem value="now" className="px-2.5 text-xs">현재 관점</ToggleGroupItem>
          <ToggleGroupItem value="asof" disabled={!canAsof} className="px-2.5 text-xs">당시 수집 관점</ToggleGroupItem>
        </ToggleGroup>
        {asof && asofDate && (
          <label className="flex items-center gap-1.5 text-xs">
            <span className="text-muted-foreground">기준일</span>
            <Input type="date" value={asofDate} onChange={(e) => { if (isDate(e.target.value)) onDate(e.target.value); }}
              className="h-8 w-[9.5rem] font-mono text-xs tnum" />
          </label>
        )}
      </div>
      <p className="text-[11px] leading-4 text-muted-foreground">
        {asof && asofDate
          ? <>{dateLabel(asofDate)}에 이미 발간되고 <b className="text-foreground">아카이브에 수집되어 있던</b> 리포트 {shown}건만 보여 줍니다. 늦게 수집된 {total - shown}건과 수집일 미상은 숨깁니다. 가격선은 전 기간을 그리며, 기준일 이후 가격은 당시 알 수 없던 정보입니다. 전략 매매 지점은 기준일까지만, 현재 추적 스탑은 숨깁니다.</>
          : <>지금 아카이브에 있는 리포트 {total}건 전부. 발간 뒤 늦게 수집된 리포트도 발간일에 표시됩니다.</>}
      </p>
    </div>
  );
}

function ReportList({ reports, selectedId, onSelect, onOpen }: {
  reports: StockReport[]; selectedId: string | null;
  onSelect: (r: StockReport) => void; onOpen: (r: StockReport) => void;
}) {
  if (!reports.length) return <p className="rounded-md border border-dashed border-border px-4 py-5 text-center text-sm text-muted-foreground">이 관점에서 보이는 리포트가 없습니다.</p>;
  return (
    <ol className="divide-y divide-dashed divide-border rounded-md border border-border bg-card">
      {[...reports].sort((a, b) => b.date.localeCompare(a.date)).map((r) => {
        const active = r.reportId === selectedId;
        return (
          <li key={r.reportId} className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2", active && "bg-muted")}>
            <button type="button" aria-pressed={active} onClick={() => onSelect(r)}
              className="flex min-h-9 min-w-0 flex-1 items-center gap-2 rounded-sm text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <span aria-hidden="true" className={cn("font-mono text-xs", active ? "text-foreground" : "text-muted-foreground")}>{active ? "◆" : "▲"}</span>
              <span className="font-mono text-xs tnum">{dateLabel(r.date)}</span>
              <span className="font-bold">{schoolShort[r.school]}</span>
              <span className="truncate text-xs text-muted-foreground">{r.rating ?? ratingClassLabels[r.ratingClass]} · 목표 {r.targetPrice === null ? "—" : r.targetPrice.toLocaleString("ko-KR")}</span>
              {r.firstSeen && r.firstSeen > r.date && <Badge variant="outline" className="rounded-sm border-dashed px-1 text-[10px] text-warn">늦게 수집</Badge>}
              {r.archived && <Badge variant="outline" className="rounded-sm px-1 text-[10px]">아카이브</Badge>}
            </button>
            <Button data-evidence={r.reportId} type="button" size="sm" variant="ghost" className="h-8 px-2 text-xs" onClick={() => onOpen(r)}>
              <FileText className="size-4" aria-hidden="true" /> 근거
            </Button>
          </li>
        );
      })}
    </ol>
  );
}

function Compare({ reports, market, onSelect }: { reports: StockReport[]; market: string | null; onSelect: (r: StockReport) => void }) {
  const opinions = reports.filter((r) => !r.archived).map((r) => ({ ...r, id: r.reportId }));
  const stances = clubStances(opinions);
  const history = targetHistory(opinions).reverse();
  if (!opinions.length) return null;
  return (
    <section aria-labelledby="compare-heading" id="compare" className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="compare-heading" className="font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">비교 — 학회 의견과 목표가 변화</h2>
        <p className="font-mono text-[10px] text-muted-foreground">위 관점 설정을 따릅니다</p>
      </div>
      <div className="rounded-md border border-border bg-card p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className={label}>학회별 최신 입장</span>
          {stances.latest.length > 1 && (
            <Badge variant="outline" className={cn("rounded-sm text-[10px]", stances.split ? "border-warn/70 bg-warn/10 text-warn" : "border-border")}>
              {stances.split ? "의견 엇갈림" : `의견 일치 · ${ratingClassLabels[stances.latest[0].ratingClass]}`}
            </Badge>
          )}
          {stances.targetSpreadPct !== null && <span className="font-mono text-[10px] text-muted-foreground tnum">최신 목표가 차이 {stances.targetSpreadPct.toFixed(0)}%</span>}
        </div>
        <ul className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {stances.latest.map((r) => (
            <li key={r.reportId} className="flex items-baseline justify-between gap-2 rounded-sm border border-border px-2.5 py-1.5 text-sm">
              <span><b>{schoolShort[r.school]}</b> <span className="font-mono text-[10px] text-muted-foreground tnum">{dateLabel(r.date)}</span></span>
              <span className="text-right">
                <span className={cn("text-xs", r.ratingClass === "sell" && "font-bold")}>{ratingClassLabels[r.ratingClass]}</span>
                <span className="ml-2 font-mono text-xs tnum">{formatPrice(r.targetPrice, market)}</span>
              </span>
            </li>
          ))}
        </ul>
        {stances.latest.length === 1 && <p className="mt-2 text-xs text-muted-foreground">이 관점에서 이 종목을 다룬 학회는 한 곳뿐이라 반대 의견을 비교할 수 없습니다.</p>}
      </div>
      <div>
        <p className={cn(label, "mb-1.5")}>목표가·등급 변경 이력 — 같은 학회 직전 리포트 대비</p>
        <Table>
          <TableHeader>
            <TableRow><TableHead>발간</TableHead><TableHead>학회</TableHead><TableHead>의견</TableHead><TableHead className="text-right">목표가</TableHead><TableHead className="text-right">직전 대비</TableHead></TableRow>
          </TableHeader>
          <TableBody>
            {history.map(({ report: r, previous, targetChangePct, ratingChanged }) => (
              <TableRow key={r.reportId}>
                <TableCell>
                  <button type="button" onClick={() => onSelect(r)} className="font-mono text-xs tnum underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                    {dateLabel(r.date)}
                  </button>
                </TableCell>
                <TableCell className="text-xs font-bold">{schoolShort[r.school]}</TableCell>
                <TableCell className="text-xs">
                  {ratingClassLabels[r.ratingClass]}
                  {ratingChanged && previous && <span className="ml-1 text-[10px] text-warn">({ratingClassLabels[previous.ratingClass]}→)</span>}
                </TableCell>
                <TableCell className="text-right font-mono text-xs tnum">{formatPrice(r.targetPrice, market)}</TableCell>
                <TableCell className={cn("text-right font-mono text-xs tnum", targetChangePct === null ? "text-muted-foreground" : targetChangePct >= 0 ? "text-up" : "text-down")}>
                  {previous ? (targetChangePct === null ? "목표가 없음" : formatPct(targetChangePct)) : "첫 리포트"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

type Related = { manifest: FulltextManifest; kinds: readonly RelationKind[]; stock: number };

function RelatedStocks({ slug, name, asofDate }: { slug: string; name: string; asofDate: string | null }) {
  const [state, setState] = useState<"idle" | "loading" | "error" | Related>("idle");
  const load = () => {
    setState("loading");
    import("@/lib/report-search")
      .then(async ({ loadFulltextIndex, KINDS }) => ({ manifest: (await loadFulltextIndex()).manifest, kinds: KINDS }))
      .then(
        ({ manifest, kinds }) => setState({ manifest, kinds, stock: manifest.stocks.findIndex((s) => s.slug === slug) }),
        () => setState("error"),
      );
  };
  const view = useMemo(() => {
    if (typeof state !== "object" || state.stock < 0) return null;
    const { manifest, kinds, stock } = state;
    const known = manifest.reports.filter((r) => !asofDate || seenBy(r.date, r.first_seen, asofDate));
    const own = known.filter((r) => r.primary === stock);
    const together = coMentions(own, kinds, stock);
    const elsewhere = known
      .filter((r) => r.primary !== null && r.primary !== stock)
      .flatMap((r) => r.mentions.filter((m) => m[0] === stock).map((m) => ({ report: r, kind: kinds[m[1]] })))
      .sort((a, b) => kinds.indexOf(a.kind) - kinds.indexOf(b.kind) || String(b.report.date).localeCompare(String(a.report.date)));
    return { manifest, own: own.length, together, elsewhere };
  }, [state, asofDate]);

  return (
    <section aria-labelledby="related-heading" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="related-heading" className="font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">같은 주제의 다른 종목 — 본문 근거</h2>
        <Link href={`/search?q=${encodeURIComponent(name)}`} className="inline-flex items-center gap-1 text-xs underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <Search className="size-4" aria-hidden="true" />‘{name}’ 본문 검색
        </Link>
      </div>
      {state === "idle" && (
        <Button type="button" variant="outline" size="sm" onClick={load}><Network className="size-4" aria-hidden="true" />함께 언급된 종목 불러오기</Button>
      )}
      {state === "loading" && <div role="status" aria-label="불러오는 중" className="space-y-2"><Skeleton className="h-5 w-1/2" /><Skeleton className="h-16 w-full" /></div>}
      {state === "error" && (
        <Alert variant="destructive">
          <AlertTriangle className="size-4" aria-hidden="true" />
          <AlertTitle>검색 색인을 불러오지 못했습니다</AlertTitle>
          <AlertDescription><Button type="button" size="sm" variant="outline" className="mt-1 w-fit" onClick={load}>다시 시도</Button></AlertDescription>
        </Alert>
      )}
      {typeof state === "object" && !view && <p className="text-sm text-muted-foreground">이 종목은 본문 색인의 종목 목록에 없습니다.</p>}
      {view && (
        <div className="space-y-3">
          <p className="text-xs leading-5 text-muted-foreground">
            <Badge variant="outline" className="mr-1.5 rounded-sm border-dashed text-[10px]">관측된 연관</Badge>
            이 종목이 주 분석 대상인 리포트 {view.own}건의 본문에 나온 다른 종목과, 다른 종목 리포트가 이 종목을 언급한 방식입니다. 관계는 문장 속 단서어 규칙으로 분류했고 사람이 검증하지 않았습니다.
            {asofDate && ` ${dateLabel(asofDate)} 당시 수집된 리포트만 셉니다.`}
          </p>
          <div className="grid gap-3 md:grid-cols-2">
            {(["side", "passing"] as ExposureTier[]).map((tier) => {
              const items = view.together.filter((c) => c.tier === tier);
              return (
                <div key={tier} className="rounded-md border border-border bg-card p-3">
                  <p className="text-sm font-bold">{TIER_LABEL[tier].title} <span className="font-mono text-xs text-muted-foreground tnum">{items.length}</span></p>
                  <p className="text-[11px] text-muted-foreground">{TIER_LABEL[tier].hint}</p>
                  {items.length === 0 ? <p className="mt-2 text-xs text-muted-foreground">없음</p> : (
                    <ul className="mt-2 flex flex-wrap gap-1.5">
                      {items.slice(0, 18).map((c) => {
                        const s = view.manifest.stocks[c.stock];
                        const inner = <>{s.name}<span className="ml-1 font-mono text-[10px] text-muted-foreground tnum">{c.reports}건</span></>;
                        return (
                          <li key={c.stock}>
                            {s.page
                              ? <Link href={`/stocks/${s.slug}`} className="inline-flex min-h-8 items-center rounded-sm border border-border px-2 text-xs hover:border-foreground/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{inner}</Link>
                              : <span className="inline-flex min-h-8 items-center rounded-sm border border-dashed border-border px-2 text-xs">{inner}</span>}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
          <div className="rounded-md border border-border bg-card p-3">
            <p className="text-sm font-bold">다른 종목 리포트 속 {name} <span className="font-mono text-xs text-muted-foreground tnum">{view.elsewhere.length}</span></p>
            {view.elsewhere.length === 0 ? <p className="mt-1 text-xs text-muted-foreground">다른 종목 리포트에서 이 종목을 언급한 기록이 없습니다.</p> : (
              <ul className="mt-2 space-y-1 text-xs">
                {view.elsewhere.slice(0, 8).map(({ report, kind }) => {
                  const host = view.manifest.stocks[report.primary!];
                  const q = new URLSearchParams({ report: report.id });
                  if (report.date) q.set("date", report.date);
                  return (
                    <li key={`${report.id}-${kind}`} className="flex flex-wrap items-baseline gap-x-2">
                      <Badge variant="outline" className={cn("rounded-sm px-1 text-[10px]", RELATION_TIER[kind] === "passing" && "border-dashed text-muted-foreground")}>{TIER_LABEL[RELATION_TIER[kind]].title}</Badge>
                      {host.page
                        ? <Link href={`/stocks/${host.slug}?${q}`} className="font-bold underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{host.name}</Link>
                        : <span className="font-bold">{host.name}</span>}
                      <span className="font-mono text-[10px] text-muted-foreground tnum">{schoolShort[report.club]} {dateLabel(report.date)}</span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

type UrlState = ReturnType<typeof useUrlState>[0];

/**
 * What the page shows for the URL state. Archived records join only when a search link points at one; the as-of
 * view keeps reports already published AND collected by its date (report-meta `seenBy`). Memoized as one unit so
 * the chart re-marks and re-zooms only when the list or focus really changes.
 */
function researchView(reports: StockReport[], url: UrlState) {
  const selected = url.report ? reports.find((r) => r.reportId === url.report) ?? null : null;
  const inScope = reports.filter((r) => !r.archived || r.reportId === url.report);
  const latestDate = inScope.reduce<string | null>((acc, r) => (acc && acc > r.date ? acc : r.date), null);
  const asofDate = url.asof ? (url.asofDate ?? selected?.date ?? latestDate) : null;
  const visible = asofDate ? inScope.filter((r) => seenBy(r.date, r.firstSeen, asofDate)) : inScope;
  let focus: ChartFocus | null = null;
  if (selected && visible.includes(selected)) focus = { date: selected.date, mark: selected, label: `선택 ${schoolShort[selected.school]}` };
  else if (!selected && url.date) focus = { date: url.date, label: "검색한 리포트의 언급" };
  return { selected, total: inScope.length, latestDate, asofDate, visible, focus };
}

export function StockResearch({ slug, name, market, candles, reports, tradeMarks, currentStop, caption, legend }: {
  slug: string;
  name: string;
  market: string | null;
  candles: Candle[];
  reports: StockReport[];
  tradeMarks?: TradeMark[];
  currentStop?: number | null;
  caption: React.ReactNode;
  legend: React.ReactNode;
}) {
  const [url, patch] = useUrlState();
  const [sheetId, setSheetId] = useState<string | null>(null);
  // The sheet's close-focus callback runs after sheetId is cleared, so remember who opened it.
  const opener = useRef<string | null>(null);
  const chartBox = useRef<HTMLDivElement | null>(null);

  const { selected, total, latestDate, asofDate, visible, focus } = useMemo(() => researchView(reports, url), [reports, url]);
  const selectedVisible = selected !== null && visible.includes(selected);
  // The as-of view also drops later backtest trades and today's trailing stop.
  const shownTradeMarks = useMemo(() => (asofDate ? tradeMarks?.filter((m) => m.date <= asofDate) : tradeMarks), [tradeMarks, asofDate]);

  const select = useCallback((r: StockReport) => {
    patch({ report: r.reportId, date: r.date });
    chartBox.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [patch]);
  const open = useCallback((r: StockReport) => {
    patch({ report: r.reportId, date: r.date });
    opener.current = r.reportId;
    setSheetId(r.reportId);
  }, [patch]);
  const fromChart = useCallback((id: string) => {
    const r = reports.find((x) => x.reportId === id);
    if (r) open(r);
  }, [reports, open]);

  const sheetReport = sheetId ? reports.find((r) => r.reportId === sheetId) ?? null : null;

  return (
    <>
      <figure className="rounded-lg border border-border bg-card p-5">
        {caption}
        <Perspective asof={url.asof} asofDate={asofDate} total={total} shown={visible.length} canAsof={latestDate !== null}
          onChange={(asof) => patch(asof ? { view: "asof", asof: selected?.date ?? latestDate } : { view: null, asof: null })}
          onDate={(d) => patch({ asof: d })} />
        <div ref={chartBox} tabIndex={-1} className="scroll-mt-4 focus:outline-none">
          <CandleChart candles={candles} marks={visible} market={market} tradeMarks={shownTradeMarks} currentStop={asofDate ? null : currentStop}
            focus={focus} onSelectReport={fromChart} />
        </div>
        {(selected || url.date) && (
          <p role="status" className="mt-2 border-l-2 border-foreground/60 pl-2 font-mono text-[10px] leading-4 text-muted-foreground">
            {selected && !selectedVisible
              ? <>선택한 {schoolShort[selected.school]} {dateLabel(selected.date)} 리포트는 {selected.firstSeen ? `${dateLabel(selected.firstSeen)}에 수집되어` : "수집일을 알 수 없어"} 이 관점({dateLabel(asofDate)})에서는 보이지 않습니다.</>
              : selected
                ? <>◆ 선택한 리포트 — {schoolShort[selected.school]} 발간 {dateLabel(selected.date)}{selected.archived ? " (아카이브 · 채점 제외)" : ""} · 앞 6개월~뒤 12개월.</>
                : <>◆ 본문 검색에서 연 다른 종목 리포트의 언급 — 발간 {dateLabel(url.date)}.</>}
            {" "}발간 이후 주가 움직임은 관측된 연관이며, 리포트가 가격을 움직였다는 뜻이 아닙니다.
            {selected && selectedVisible && (
              <button type="button" onClick={() => open(selected)} className="ml-2 font-bold text-foreground underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">근거 열기</button>
            )}
          </p>
        )}
        {legend}
      </figure>

      <section aria-labelledby="timeline-heading" className="space-y-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="timeline-heading" className="font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">리포트 시점 — 선택하면 차트에 ◆ 표시</h2>
          <p className="font-mono text-[10px] text-muted-foreground">차트 마커를 눌러도 근거가 열립니다</p>
        </div>
        <ReportList reports={visible} selectedId={url.report} onSelect={select} onOpen={open} />
      </section>

      <Compare reports={visible} market={market} onSelect={select} />
      <RelatedStocks slug={slug} name={name} asofDate={asofDate} />

      <EvidenceSheet report={sheetReport} stockName={name} market={market} asofDate={asofDate} onClose={() => setSheetId(null)}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          // Back to the list's 근거 button for that report (also when the sheet was opened from a chart marker).
          const target = opener.current ? document.querySelector<HTMLElement>(`[data-evidence="${opener.current}"]`) : null;
          (target ?? chartBox.current)?.focus();
        }} />
    </>
  );
}
