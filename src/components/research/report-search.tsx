"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { AlertTriangle, ExternalLink, FileText, LineChart, Search, SearchX } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Pagination, PaginationContent, PaginationEllipsis, PaginationItem, PaginationLink } from "@/components/ui/pagination";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { SCHOOL_LABELS, dateLabel } from "@/lib/report-meta";
import {
  dominantKind,
  highlight,
  loadEvidencePack,
  loadFulltextIndex,
  rankChunks,
  sourceUrl,
  verdictHref,
  type EvidencePack,
  type Filters,
  type FulltextIndex,
  type FulltextManifest,
  type QueryPlan,
  type RelationKind,
  type SearchResult,
  type StockGroup,
} from "@/lib/report-search";
import { RELATION_TIER, TIER_LABEL, type ExposureTier } from "@/lib/research-compare";
import { TopicCompare } from "@/components/research/topic-compare";
import { schoolShort } from "@/lib/verdict";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 10;
const STOCK_STEP = 8;
const CLUBS = ["smic", "yig", "star", "kuvic", "ewha", "voera"] as const;
const YEARS = Array.from({ length: 2026 - 2006 + 1 }, (_, i) => String(2026 - i));
const EXAMPLES = ["로봇", "108490", "휴머노이드", "감속기", "데이터센터"];

const RELATION: Record<RelationKind, { label: string; tone: string }> = {
  primary: { label: "주 분석 대상", tone: "border-foreground/70 text-foreground" },
  supplier_customer: { label: "공급망·고객 언급", tone: "border-stamp/60 text-stamp" },
  peer: { label: "경쟁·비교 기업", tone: "border-gold/60 text-gold" },
  sector: { label: "산업·섹터 맥락", tone: "border-warn/60 text-warn" },
  passing: { label: "단순 언급", tone: "border-dashed border-border text-muted-foreground" },
};
const CONF_LABEL = { high: "근거 강함", medium: "근거 보통", low: "근거 약함" } as const;
const SCOPE_LABEL = { modern: "채점 대상", archive: "아카이브 · 채점 제외", unscored: "미채점" } as const;

type Report = FulltextManifest["reports"][number];
type Stock = FulltextManifest["stocks"][number];

function readParams(params: URLSearchParams) {
  const clubs = (params.get("club") ?? "").split(",").filter((c): c is (typeof CLUBS)[number] => (CLUBS as readonly string[]).includes(c));
  return {
    q: params.get("q")?.trim() ?? "",
    clubs,
    from: params.get("from") ?? "",
    to: params.get("to") ?? "",
    collected: params.get("collected") === "1",
    page: Math.max(1, Number.parseInt(params.get("page") ?? "1", 10) || 1),
  };
}
type Params = ReturnType<typeof readParams>;

function toFilters(p: Params): Filters {
  return {
    clubs: p.clubs,
    from: p.from ? `${p.from}-01-01` : undefined,
    to: p.to ? `${p.to}-12-31` : undefined,
    collectedBy: p.collected && p.to ? `${p.to}-12-31` : undefined,
  };
}

/** Stock chart at this report; `asof` carries the search's 당시 수집 관점 over to the stock page. */
function stockHref(stock: Stock, report: Report, asof?: string) {
  const query = new URLSearchParams({ report: report.id });
  if (report.date) query.set("date", report.date);
  if (asof) {
    query.set("view", "asof");
    query.set("asof", asof);
  }
  return `/stocks/${stock.slug}?${query}`;
}

function Quote({ text, needles }: { text: string; needles: string[] }) {
  return (
    <>
      {highlight(text, needles).map((part, i) =>
        part.mark ? (
          <mark key={i} className="rounded-sm bg-warn/25 px-0.5 font-semibold text-foreground">{part.text}</mark>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  );
}

function usePack(id: string | null) {
  const [state, setState] = useState<{ id: string; pack?: EvidencePack; error?: true } | null>(null);
  useEffect(() => {
    if (!id) return;
    let live = true;
    loadEvidencePack(id).then(
      (pack) => live && setState({ id, pack }),
      () => live && setState({ id, error: true }),
    );
    return () => { live = false; };
  }, [id]);
  return state?.id === id ? state : null;
}

function EvidenceLines({ manifest, report, pack, chunkIdx, needles }: {
  manifest: FulltextManifest; report: Report; pack: EvidencePack; chunkIdx: number; needles: string[];
}) {
  const [para, start, end, , text] = pack.chunks[chunkIdx];
  return (
    <figure className="border-l-2 border-warn/60 pl-3">
      <blockquote className="break-words text-sm leading-6 text-foreground/90">
        <Quote text={text} needles={needles} />
      </blockquote>
      <figcaption className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10px] text-muted-foreground">
        <span className="tnum">문단 {para} · L{start}–L{end}</span>
        <a className="inline-flex items-center gap-1 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          href={sourceUrl(manifest, report.path, [start, end])} target="_blank" rel="noreferrer">
          원문 위치 열기 <ExternalLink className="size-3" aria-hidden="true" />
        </a>
      </figcaption>
    </figure>
  );
}

function StockEvidence({ index, group, plan }: { index: FulltextIndex; group: StockGroup; plan: QueryPlan }) {
  const { manifest } = index;
  const stock = manifest.stocks[group.stock];
  const best = group.reports[0];
  const report = manifest.reports[best.index];
  const state = usePack(report.id);
  const queryNeedles = plan.words.flat().map((v) => v.text);
  if (!state) return <Skeleton className="h-16 w-full" />;
  if (state.error || !state.pack) return <p role="alert" className="text-xs text-destructive">근거 문단을 불러오지 못했습니다.</p>;
  const pack = state.pack;
  let chunkIdx: number | undefined;
  if (best.kind === "primary") chunkIdx = rankChunks(pack, plan, 1)[0]?.index;
  else chunkIdx = pack.mentions.find((m) => m[0] === stock.slug)?.[4][0];
  if (chunkIdx === undefined) return <p className="text-xs text-muted-foreground">이 보고서에서 표시할 문단을 찾지 못했습니다.</p>;
  const together = best.kind !== "primary" && rankChunks({ ...pack, chunks: [pack.chunks[chunkIdx]] }, plan, 1).length > 0;
  return (
    <div className="space-y-1.5">
      <EvidenceLines manifest={manifest} report={report} pack={pack} chunkIdx={chunkIdx}
        needles={[...queryNeedles, ...stock.aliases.filter((a) => !/^\d+$/.test(a))]} />
      {best.kind !== "primary" && (
        <p className="font-mono text-[10px] text-muted-foreground">
          {together ? "같은 문단에 검색어가 함께 나옵니다." : "검색어는 이 보고서의 다른 문단에 있습니다 — 종목 언급 문단만 보여 줍니다."}
        </p>
      )}
    </div>
  );
}

function StockCard({ index, group, plan, asof }: { index: FulltextIndex; group: StockGroup; plan: QueryPlan; asof?: string }) {
  const [open, setOpen] = useState(false);
  const { manifest } = index;
  const stock = manifest.stocks[group.stock];
  const kind = dominantKind(group);
  const best = group.reports[0];
  const bestReport = manifest.reports[best.index];
  const reportCount = new Set(group.reports.map((r) => r.index)).size;
  const counts = group.reports.reduce<Partial<Record<RelationKind, number>>>((acc, r) => ({ ...acc, [r.kind]: (acc[r.kind] ?? 0) + 1 }), {});
  return (
    <li className="min-w-0 rounded-lg border border-border bg-card p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="min-w-0">
          {stock.page ? (
            <Link href={stockHref(stock, bestReport, asof)} className="font-bold underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {stock.name}
            </Link>
          ) : (
            <span className="font-bold">{stock.name}</span>
          )}
          <span className="ml-2 font-mono text-[10px] text-muted-foreground">{stock.market} · {stock.ticker}</span>
        </div>
        <div className="flex flex-wrap gap-1">
          <Badge variant="outline" className={cn("rounded-sm font-mono text-[10px]", RELATION[kind].tone)}>{RELATION[kind].label}</Badge>
          <Badge variant="outline" className="rounded-sm border-dashed font-mono text-[10px] text-muted-foreground">{CONF_LABEL[best.conf]}</Badge>
        </div>
      </div>
      <p className="mt-1 font-mono text-[10px] text-muted-foreground tnum">
        보고서 {reportCount}건 · 학회 {group.clubs.length}곳 · {group.first ? `${dateLabel(group.first)} ~ ${dateLabel(group.last)}` : "발간일 미상"}
      </p>
      <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
        <span className="font-semibold text-foreground">왜: </span>
        {(Object.keys(counts) as RelationKind[]).map((k) => `${RELATION[k].label} ${counts[k]}건`).join(" · ")}
        {" — 가장 강한 근거는 "}
        {schoolShort[bestReport.club]} {dateLabel(bestReport.date)} 「{bestReport.title}」 ({CONF_LABEL[best.conf]})
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" variant="outline" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          <FileText className="size-4" aria-hidden="true" /> {open ? "근거 접기" : "근거 문단 보기"}
        </Button>
        {stock.page ? (
          <Button asChild size="sm" variant="ghost">
            <Link href={stockHref(stock, bestReport, asof)}><LineChart className="size-4" aria-hidden="true" /> 주가에서 발간 시점 보기 · 관측된 연관</Link>
          </Button>
        ) : (
          <span className="font-mono text-[10px] text-muted-foreground">종목 페이지 없음 (채점 기록 없는 종목)</span>
        )}
      </div>
      {open && <div className="mt-3"><StockEvidence index={index} group={group} plan={plan} /></div>}
    </li>
  );
}

function ReportCard({ index, report, plan, primaryOfQuery, asof }: { index: FulltextIndex; report: Report; plan: QueryPlan; primaryOfQuery: boolean; asof?: string }) {
  const { manifest } = index;
  const state = usePack(report.id);
  const primary = report.primary !== null ? manifest.stocks[report.primary] : null;
  const verdict = verdictHref(report);
  const needles = plan.words.flat().map((v) => v.text);
  const chunks = state?.pack ? rankChunks(state.pack, plan, 2) : [];
  return (
    <li className="min-w-0 border-b border-dashed border-border py-4 last:border-b-0">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Badge variant="outline" className="rounded-sm font-mono text-[10px]">{schoolShort[report.club]}</Badge>
        <h3 className="min-w-0 break-words font-bold">{report.title}</h3>
        {primaryOfQuery && <Badge variant="secondary" className="rounded-sm text-[10px]">검색 종목이 주 분석 대상</Badge>}
      </div>
      <p className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10px] text-muted-foreground tnum">
        <span>발간 {dateLabel(report.date)}</span>
        <span>{report.first_seen ? `수집 ${dateLabel(report.first_seen)}` : "수집 시점 미상"}</span>
        <span>{SCOPE_LABEL[report.scope]}</span>
        {primary && <span>주 분석: {primary.name}</span>}
      </p>
      <div className="mt-2 space-y-2">
        {!state && <Skeleton className="h-14 w-full" />}
        {state?.error && <p role="alert" className="text-xs text-destructive">문단을 불러오지 못했습니다. 원문 링크로 확인해 주세요.</p>}
        {state?.pack && chunks.length === 0 && (
          <p className="text-xs text-muted-foreground">보고서 전체에서는 일치하지만 한 문단에 모두 모이지는 않았습니다.</p>
        )}
        {state?.pack && chunks.map((c) => (
          <div key={c.index}>
            {!c.exact && <p className="mb-1 font-mono text-[10px] text-warn">근사 일치 (오타 보정 · 일부 음절 일치)</p>}
            <EvidenceLines manifest={manifest} report={report} pack={state.pack!} chunkIdx={c.index} needles={needles} />
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <a className="inline-flex items-center gap-1 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          href={sourceUrl(manifest, report.path)} target="_blank" rel="noreferrer">
          원문 전체 <ExternalLink className="size-3" aria-hidden="true" />
        </a>
        {verdict && <Link className="underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" href={verdict}>판결문 열기</Link>}
        {primary?.page && (
          <Link className="underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" href={stockHref(primary, report, asof)}>
            {primary.name} 주가에서 이 발간일 보기
          </Link>
        )}
      </div>
    </li>
  );
}

function PageLinks({ page, pages, hrefFor, onGo }: { page: number; pages: number; hrefFor: (p: number) => string; onGo: (p: number) => void }) {
  if (pages <= 1) return null;
  const shown = [...new Set([1, page - 1, page, page + 1, pages])].filter((p) => p >= 1 && p <= pages).sort((a, b) => a - b);
  const link = (p: number, label: React.ReactNode, aria?: string, active = false) => (
    <PaginationLink href={hrefFor(p)} isActive={active} aria-label={aria} size="sm" className="min-w-8"
      onClick={(e) => { e.preventDefault(); onGo(p); }}>{label}</PaginationLink>
  );
  return (
    <Pagination aria-label="보고서 결과 페이지" className="mt-4">
      <PaginationContent className="flex-wrap">
        {page > 1 && <PaginationItem>{link(page - 1, "이전", "이전 페이지")}</PaginationItem>}
        {shown.map((p, i) => (
          <PaginationItem key={p}>
            {i > 0 && p - shown[i - 1] > 1 && <PaginationEllipsis />}
            {link(p, String(p), `${p}쪽`, p === page)}
          </PaginationItem>
        ))}
        {page < pages && <PaginationItem>{link(page + 1, "다음", "다음 페이지")}</PaginationItem>}
      </PaginationContent>
    </Pagination>
  );
}

export function ReportSearchSkeleton() {
  return (
    <div className="space-y-4" role="status" aria-label="검색 화면 불러오는 중">
      <Skeleton className="h-10 w-2/3" />
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}

export function ReportSearch() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const params = useMemo(() => readParams(new URLSearchParams(searchParams.toString())), [searchParams]);
  const [draft, setDraft] = useState(params.q);
  const [draftFor, setDraftFor] = useState(params.q);
  const [index, setIndex] = useState<FulltextIndex | null>(null);
  const [indexError, setIndexError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [outcome, setOutcome] = useState<{ key: string; result?: SearchResult; error?: true } | null>(null);
  const [stockLimit, setStockLimit] = useState<{ key: string; n: Record<ExposureTier, number> }>({ key: "", n: { core: STOCK_STEP, side: STOCK_STEP, passing: STOCK_STEP } });

  // Keep the input in step with back/forward navigation.
  if (draftFor !== params.q) {
    setDraftFor(params.q);
    setDraft(params.q);
  }

  useEffect(() => {
    let live = true;
    loadFulltextIndex().then(
      (loaded) => { if (live) { setIndex(loaded); setIndexError(false); } },
      () => { if (live) setIndexError(true); },
    );
    return () => { live = false; };
  }, [attempt]);

  const searchKey = JSON.stringify([params.q, params.clubs, params.from, params.to, params.collected]);
  useEffect(() => {
    if (!index || !params.q) return;
    let live = true;
    index.search(params.q, toFilters(params)).then(
      (result) => live && setOutcome({ key: searchKey, result }),
      () => live && setOutcome({ key: searchKey, error: true }),
    );
    return () => { live = false; };
    // searchKey captures every input of the search
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, searchKey]);

  const navigate = (next: Partial<Params>) => {
    const merged = { ...params, page: 1, ...next };
    const query = new URLSearchParams();
    if (merged.q) query.set("q", merged.q);
    if (merged.clubs.length) query.set("club", merged.clubs.join(","));
    if (merged.from) query.set("from", merged.from);
    if (merged.to) query.set("to", merged.to);
    if (merged.collected) query.set("collected", "1");
    if (merged.page > 1) query.set("page", String(merged.page));
    const qs = query.toString();
    return `${pathname}${qs ? `?${qs}` : ""}`;
  };
  // Every filter, page and query change is a history entry, so Back undoes it. Native History API: Next syncs
  // useSearchParams with it, while router.push on this static page, opened with a query (a shared link),
  // committed the old URL back in Next 16.2 and dropped the change.
  const go = (next: Partial<Params>) => window.history.pushState(null, "", navigate(next));


  const current = outcome?.key === searchKey ? outcome : null;
  const result = current?.result;
  const manifest = index?.manifest;
  const limits = stockLimit.key === searchKey ? stockLimit.n : { core: STOCK_STEP, side: STOCK_STEP, passing: STOCK_STEP };
  const pages = result ? Math.max(1, Math.ceil(result.reports.length / PAGE_SIZE)) : 1;
  const page = Math.min(params.page, pages);
  const visible = result ? result.reports.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE) : [];
  const planStock = result && result.plan.stock !== null && manifest ? manifest.stocks[result.plan.stock] : null;
  const expansions = result ? result.plan.words.flatMap((w) => w.slice(1)).filter((v) => v.weight < 1) : [];

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-3xl font-black tracking-tight sm:text-5xl">보고서 본문 검색</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
          주제어·종목명·티커로 학회 리포트 본문을 찾아 <b className="text-foreground">관련 종목 → 근거 문단 → 원문 위치 → 주가의 발간 시점</b>으로 이어 갑니다.
          키워드·동의어 기반 검색이며 모든 관련 보고서를 찾는다고 보장하지 않습니다. 관련도는 본문 근거의 양이지 투자 매력도가 아닙니다.
        </p>
        {manifest && (
          <p className="mt-1 font-mono text-[10px] text-muted-foreground tnum">
            보고서 {manifest.stats.reports.toLocaleString("ko-KR")}건 · 문단 {manifest.stats.chunks.toLocaleString("ko-KR")}개 색인 · 종목 별칭은 아카이브가 다룬 종목만
          </p>
        )}
      </header>

      <form role="search" className="flex flex-col gap-2 sm:flex-row"
        onSubmit={(e) => { e.preventDefault(); go({ q: draft.trim() }); }}>
        <label htmlFor="fulltext-q" className="sr-only">검색어</label>
        <Input id="fulltext-q" type="search" value={draft} onChange={(e) => setDraft(e.target.value)} autoComplete="off"
          placeholder="예: 로봇, 휴머노이드 감속기, 108490, 로보티즈" className="h-10 text-base sm:text-sm" />
        <Button type="submit" className="h-10"><Search className="size-4" aria-hidden="true" /> 검색</Button>
      </form>

      <fieldset className="space-y-2">
        <legend className="sr-only">필터</legend>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">학회</span>
          <ToggleGroup type="multiple" variant="outline" size="sm" value={params.clubs} aria-label="학회 필터"
            onValueChange={(clubs) => go({ clubs: clubs as Params["clubs"] })} className="flex-wrap">
            {CLUBS.map((c) => <ToggleGroupItem key={c} value={c} aria-label={SCHOOL_LABELS[c]} className="font-mono text-[10px]">{schoolShort[c]}</ToggleGroupItem>)}
          </ToggleGroup>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">발간</span>
          <Select value={params.from || "any"} onValueChange={(v) => go({ from: v === "any" ? "" : v })}>
            <SelectTrigger size="sm" className="w-[104px]" aria-label="시작 연도"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="any">처음부터</SelectItem>
              {YEARS.map((y) => <SelectItem key={y} value={y}>{y}년~</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={params.to || "any"} onValueChange={(v) => go({ to: v === "any" ? "" : v, collected: v === "any" ? false : params.collected })}>
            <SelectTrigger size="sm" className="w-[104px]" aria-label="끝 연도"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="any">현재까지</SelectItem>
              {YEARS.map((y) => <SelectItem key={y} value={y}>~{y}년</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">관점</span>
          <ToggleGroup type="single" variant="outline" size="sm" value={params.collected && params.to ? "asof" : "now"} aria-label="검색 관점"
            onValueChange={(v) => { if (v) go(v === "asof" ? { collected: true, to: params.to || YEARS[0] } : { collected: false }); }}>
            <ToggleGroupItem value="now" className="px-2.5 text-xs">현재 관점</ToggleGroupItem>
            <ToggleGroupItem value="asof" className="px-2.5 text-xs">당시 수집 관점</ToggleGroupItem>
          </ToggleGroup>
        </div>
        <p className="font-mono text-[10px] text-muted-foreground">
          {params.collected && params.to
            ? `당시 수집 관점 — ${params.to}년 말까지 아카이브에 들어와 있던 보고서만 보여 줍니다(늦게 수집된 보고서·수집 시점 미상은 제외). 끝 연도로 기준을 바꿉니다.`
            : "현재 관점 — 지금 아카이브에 있는 모든 보고서. 발간 뒤 늦게 수집된 보고서도 포함됩니다."}
        </p>
      </fieldset>

      {indexError && (
        <Alert variant="destructive">
          <AlertTriangle className="size-4" aria-hidden="true" />
          <AlertTitle>검색 색인을 불러오지 못했습니다</AlertTitle>
          <AlertDescription>
            네트워크 상태를 확인한 뒤 다시 시도해 주세요.
            <Button type="button" size="sm" variant="outline" className="mt-2 w-fit" onClick={() => { setIndexError(false); setAttempt((n) => n + 1); }}>다시 시도</Button>
          </AlertDescription>
        </Alert>
      )}

      {!params.q && (
        <section aria-label="검색 예시" className="rounded-lg border border-dashed border-border p-4">
          <p className="text-sm text-muted-foreground">주제어 하나로 시작해 보세요. 티커(6자리)·종목명은 그 종목이 주 분석 대상인 보고서를 먼저 보여 줍니다.</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {EXAMPLES.map((e) => <Button key={e} type="button" size="sm" variant="secondary" onClick={() => go({ q: e })}>{e}</Button>)}
          </div>
        </section>
      )}

      {params.q && !indexError && !current && (
        <div role="status" aria-label="검색 중" className="space-y-3"><Skeleton className="h-6 w-1/3" /><Skeleton className="h-28 w-full" /><Skeleton className="h-28 w-full" /></div>
      )}

      {current?.error && (
        <Alert variant="destructive">
          <AlertTriangle className="size-4" aria-hidden="true" />
          <AlertTitle>검색 중 오류가 났습니다</AlertTitle>
          <AlertDescription>색인 일부를 불러오지 못했습니다. 새로고침 후 다시 시도해 주세요.</AlertDescription>
        </Alert>
      )}

      {result && manifest && (
        <div className="space-y-8" aria-live="polite">
          <p className="text-xs leading-5 text-muted-foreground">
            {planStock && <>‘<b className="text-foreground">{planStock.name}</b>’{result.plan.fuzzy !== null && result.plan.fuzzy === result.plan.stock ? "(오타 보정)" : ""} 종목으로 인식 — 그 종목이 주 분석 대상인 보고서를 먼저 보여 줍니다. </>}
            {expansions.length > 0 && <>함께 찾은 말: {[...new Set(expansions.map((v) => v.text))].join(", ")} (동의어·관련어는 낮은 가중치). </>}
            보고서 {result.reports.length.toLocaleString("ko-KR")}건 · 관련 종목 {result.stocks.length.toLocaleString("ko-KR")}개
          </p>

          {result.reports.length === 0 ? (
            <Empty className="border border-dashed">
              <EmptyHeader>
                <EmptyMedia variant="icon"><SearchX className="size-5" aria-hidden="true" /></EmptyMedia>
                <EmptyTitle>‘{params.q}’과 일치하는 본문이 없습니다</EmptyTitle>
                <EmptyDescription>철자를 확인하거나 더 짧은 주제어, 필터 해제를 시도해 보세요. 한 음절 검색어는 색인하지 않습니다.</EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <>
              <section aria-labelledby="stocks-heading">
                <h2 id="stocks-heading" className="font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">관련 종목 — 본문 근거 기준</h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  <Badge variant="outline" className="mr-1.5 rounded-sm border-dashed text-[10px]">관측된 연관</Badge>
                  주된 노출 · 부수적 연관 · 단순 언급으로 나눕니다(종목마다 가장 강한 관계 기준). 관계 분류는 문장 속 단서어 규칙이며 사람이 검증하지 않았고, 관련도는 투자 매력도가 아닙니다.
                </p>
                <div className="mt-3 space-y-5">
                  {(["core", "side", "passing"] as ExposureTier[]).map((tier) => {
                    const groups = result.stocks.filter((g) => RELATION_TIER[dominantKind(g)] === tier);
                    if (!groups.length) return null;
                    const n = limits[tier];
                    return (
                      <div key={tier} role="group" aria-labelledby={`tier-${tier}`}>
                        <h3 id={`tier-${tier}`} className="flex flex-wrap items-baseline gap-x-2 border-b border-border pb-1 text-sm font-bold">
                          {TIER_LABEL[tier].title} <span className="font-mono text-xs font-normal text-muted-foreground tnum">{groups.length}</span>
                          <span className="text-xs font-normal text-muted-foreground">— {TIER_LABEL[tier].hint}</span>
                        </h3>
                        <ul className="mt-2 grid gap-3 md:grid-cols-2">
                          {groups.slice(0, n).map((g) => <StockCard key={g.stock} index={index!} group={g} plan={result.plan} asof={toFilters(params).collectedBy} />)}
                        </ul>
                        {groups.length > n && (
                          <Button type="button" variant="outline" size="sm" className="mt-2"
                            onClick={() => setStockLimit({ key: searchKey, n: { ...limits, [tier]: n + STOCK_STEP } })}>
                            {TIER_LABEL[tier].title} 더 보기 ({(groups.length - n).toLocaleString("ko-KR")}개 남음)
                          </Button>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>

              <TopicCompare manifest={manifest} stocks={result.stocks} filters={toFilters(params)} searchKey={searchKey} />

              <section aria-labelledby="reports-heading">
                <h2 id="reports-heading" tabIndex={-1} className="scroll-mt-4 outline-none font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">
                  보고서 · 근거 문단 <span className="tnum">({page}/{pages}쪽)</span>
                </h2>
                <p className="mt-1 text-xs text-muted-foreground">발간 이후 주가 움직임은 관측된 연관일 뿐, 보고서가 가격을 움직였다는 뜻이 아닙니다.</p>
                <ul className="mt-1">
                  {visible.map((r) => (
                    <ReportCard key={r.index} index={index!} report={manifest.reports[r.index]} plan={result.plan} asof={toFilters(params).collectedBy}
                      primaryOfQuery={result.plan.stock !== null && manifest.reports[r.index].primary === result.plan.stock} />
                  ))}
                </ul>
                <PageLinks page={page} pages={pages} hrefFor={(p) => navigate({ page: p })}
                  onGo={(p) => {
                    go({ page: p });
                    // Keyboard and screen-reader users land on the new page's results, not back on the pager.
                    const heading = document.getElementById("reports-heading");
                    heading?.focus({ preventScroll: true });
                    heading?.scrollIntoView({ block: "start" });
                  }} />
              </section>
            </>
          )}
        </div>
      )}
    </div>
  );
}
