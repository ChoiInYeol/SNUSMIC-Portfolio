"use client";

import { createContext, useContext, Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Link2, Printer, Search } from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";
import { SiteNav } from "@/components/site-nav";
import { VerdictWall } from "@/components/verdict-wall";
import { ReportPathChart } from "@/components/report-path-chart";
import { CountUp } from "@/components/count-up";
import { SourceLinks } from "@/components/source-links";
import Link from "next/link";
import { bucketFilters, type BucketFilter, type MarketFilter, type SchoolFilter, useReportDashboardState } from "@/components/report-dashboard/use-report-dashboard-state";
import type { DataQuality, ReportDataset, ReportRecord } from "@/lib/report-model";
import { dateLabel, getDisplayName, SCHOOL_LABELS } from "@/lib/report-meta";
import {
  bucketBadgeClass,
  bucketLabels,
  bucketThresholds,
  clubStats,
  isBuy,
  maturityLabels,
  median,
  peakLag,
  reportDidItsJob,
  schoolShort,
  signColor,
  stampTone,
  tickerSlug,
  verdictOf,
  type Verdict,
} from "@/lib/verdict";
import { cn, formatPct, formatPrice } from "@/lib/utils";
import { unpackDashboard, type DashboardTransport } from "@/lib/dashboard-transport";

const marketFilters: MarketFilter[] = ["ALL", "KR", "US"];
const marketLabels: Record<MarketFilter, string> = { ALL: "시장 전체", KR: "국내", US: "미국" };
const schoolFilters: SchoolFilter[] = ["ALL", "smic", "yig", "star", "kuvic", "ewha", "voera"];
const schoolLabels: Record<SchoolFilter, string> = { ALL: "전체 아카이브", ...SCHOOL_LABELS };
const bucketFilterLabels: Record<BucketFilter, string> = { ALL: "전체", ...bucketLabels };

const ReportDataContext = createContext<{ dataset: ReportDataset; quality: DataQuality } | null>(null);

function useReportData() {
  const data = useContext(ReportDataContext);
  if (!data) throw new Error("Report data provider is missing");
  return data;
}

function yearOf(report: ReportRecord) {
  return report.report_date?.slice(0, 4) ?? "????";
}

function diffDays(from: string | null, to: string | null) {
  if (!from || !to) return null;
  const ms = new Date(to).getTime() - new Date(from).getTime();
  return Number.isFinite(ms) ? Math.round(ms / 86_400_000) : null;
}

export function ReportDashboard({ packed, quality }: { packed: DashboardTransport; quality: DataQuality }) {
  const dataset = useMemo(() => unpackDashboard(packed), [packed]);
  return <ReportDataContext.Provider value={{ dataset, quality }}><DashboardContent /></ReportDataContext.Provider>;
}

function DashboardContent() {
  const { dataset: reportDataset, quality: reportDataQuality } = useReportData();
  const wallReports = useMemo(() => [...reportDataset.records]
    .filter((report) => report.era === "modern" && report.rating_class === "buy" && report.report_date)
    .sort((a, b) => String(a.report_date).localeCompare(String(b.report_date))), [reportDataset]);
  const state = useReportDashboardState(reportDataset.records);
  const selected = state.selected as ReportRecord | undefined;
  const selectedIndex = state.sorted.findIndex((report) => report.source_name === selected?.source_name);

  const { sorted } = state;
  const { setSelectedName } = state;
  const move = useCallback(
    (delta: number) => {
      if (!sorted.length) return;
      const current = sorted.findIndex((report) => report.source_name === selected?.source_name);
      const next = Math.min(sorted.length - 1, Math.max(0, current + delta));
      setSelectedName(sorted[next].source_name);
    },
    [sorted, setSelectedName, selected?.source_name],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        move(-1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        move(1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [move]);

  // 판결문 딥링크 — /#r={source_name} 해시로 특정 기록에 바로 닿는다 (공유 버튼·커맨드 팔레트와 한 쌍)
  const { setMarket, setBucket, setSchool, setQuery, setIncludeReference } = state;
  useEffect(() => {
    const apply = () => {
      const match = window.location.hash.match(/^#r=(.+)$/);
      if (!match) return;
      let name = "";
      try {
        name = decodeURIComponent(match[1]);
      } catch {
        return;
      }
      const record = reportDataset.records.find((r) => r.source_name === name && r.era === "modern" && r.report_date);
      if (!record) return;
      setMarket("ALL");
      setBucket("ALL");
      setSchool("ALL");
      setQuery("");
      if (!isBuy(record)) setIncludeReference(true);
      setSelectedName(name);
      requestAnimationFrame(() => document.getElementById("verdict-paper")?.scrollIntoView({ behavior: "smooth", block: "start" }));
    };
    apply();
    window.addEventListener("hashchange", apply);
    return () => window.removeEventListener("hashchange", apply);
  }, [reportDataset, setMarket, setBucket, setSchool, setQuery, setIncludeReference, setSelectedName]);

  // 안정 참조 — 서가(WallGrid memo)·신규 사건 카드가 대시보드 재렌더마다 다시 그려지지 않도록
  const pickFromWall = useCallback(
    (name: string) => {
      setMarket("ALL");
      setBucket("ALL");
      setSchool("ALL");
      setQuery("");
      setSelectedName(name);
      requestAnimationFrame(() => document.getElementById("verdict-paper")?.scrollIntoView({ behavior: "smooth", block: "start" }));
    },
    [setMarket, setBucket, setSchool, setQuery, setSelectedName],
  );

  if (!selected) {
    return (
      <main className="flex min-h-screen items-center justify-center px-8 text-center">
        <p className="text-sm leading-7 text-muted-foreground">
          표시할 리포트 데이터가 없습니다.
          <br />
          데이터 파이프라인(scripts/build_report_performance.py)을 먼저 실행해 주세요.
        </p>
      </main>
    );
  }

  const verdict = verdictOf(selected);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <main className="mx-auto max-w-[1500px] px-4 pt-6 sm:px-8">
        <Masthead />
        <div className="mt-10 print:hidden">
          <VerdictWall reports={wallReports} selectedName={selected.source_name} onSelect={pickFromWall} />
        </div>
      </main>

      <div className="mt-10 print:hidden">
        <VerdictTape />
      </div>

      <main className="mx-auto max-w-[1500px] space-y-10 px-4 pb-6 pt-8 sm:px-8">
        <FreshVerdicts onPick={pickFromWall} />

        <FilterBar state={state} />

        <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_370px]" aria-label="판결문과 연대기">
          <VerdictPaper
            report={selected}
            verdict={verdict}
            index={selectedIndex}
            total={state.sorted.length}
            onPrev={() => move(-1)}
            onNext={() => move(1)}
          />
          <Chronicle reports={state.sorted} selected={selected} onSelect={state.setSelectedName} />
        </section>

        <footer className="flex flex-col gap-2 border-t-4 border-double border-foreground/50 pb-10 pt-4 font-mono text-[11px] leading-5 text-muted-foreground sm:flex-row sm:items-baseline sm:justify-between print:hidden">
          <p>
            데이터 생성 {reportDataQuality.generatedAt?.slice(0, 10) ?? "—"} · 시세 이슈 {reportDataQuality.dataIssueCount}건 · 파싱 이슈{" "}
            {reportDataQuality.parseIssueCount}건 · 격리 {reportDataQuality.totalRecords - reportDataQuality.validRecords}건
          </p>
          <p>PDF → MARKDOWN 전사 → 목표가 파싱 → POINT-IN-TIME 검증</p>
        </footer>
      </main>
    </div>
  );
}

/** 마스트헤드 — modern 시대(2019-07 이후) 매수 의견, 신생 제외의 헤드라인 성적 */
function Masthead() {
  const { dataset: reportDataset } = useReportData();
  const stats = useMemo(() => clubStats(reportDataset.records), [reportDataset]);
  const archiveCount = useMemo(() => reportDataset.records.filter((report) => report.era === "archive").length, [reportDataset]);
  return (
    <header className="print:hidden">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b-4 border-double border-foreground/70 pb-2">
        <p className="font-mono text-[11px] font-bold uppercase tracking-[0.3em]">SMIC · YIG · STAR · KUVIC · EIA · VOERA — Research Verdict Archive</p>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
          <SiteNav />
          <p className="hidden font-mono text-[11px] text-muted-foreground lg:block">기준일 {dateLabel(reportDataset.as_of)}</p>
          <ThemeToggle />
        </div>
      </div>

      <h1 className="mt-9 font-display text-[2.4rem] font-black leading-[1.12] tracking-tight sm:text-6xl lg:text-7xl">
        모든 리포트는, 결국
        <br />
        <span className="text-stamp">시장의 판결</span>을 받는다.
      </h1>
      <p className="mt-5 max-w-2xl text-base leading-7 text-foreground/75">
        서울대·연세대·성균관대·고려대·이화여대·홍익대 투자동아리(SMIC·YIG·STAR·KUVIC·EIA·VOERA)의 리포트 PDF {reportDataset.records.length}건을 전사·파싱해 목표가와
        투자의견을 추출하고, point-in-time 시세로 발간 이후의 실제 주가 경로를 추적했습니다. 성적은 여섯 학회가 나란히 비교되는{" "}
        <strong className="font-bold">2019년 7월 이후 매수 의견 {stats.total}건</strong>으로 매기고, 발간 90일이 지나지 않은 신생 {stats.fresh}건은
        판결을 보류합니다. 그 이전 SMIC 단독 수집분 {archiveCount}건은 채점 없이 아카이브로 보관합니다.
      </p>

      <dl className="mt-9 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border md:grid-cols-3 xl:grid-cols-6">
        <KpiCell
          label="검증된 매수 리포트"
          value={<CountUp value={stats.priced} suffix="건" />}
          sub={`매수 ${stats.total}건 중 · 신생 ${stats.fresh}건 보류`}
        />
        <KpiCell label="목표가 적중" value={<CountUp value={stats.hits} suffix="건" />} sub="발간 후 목표가 도달" valueClass="text-stamp" />
        <KpiCell label="적중률" value={formatPct(stats.hitRate, 1).replace("+", "")} sub="가격 검증 가능 건 기준" />
        <KpiCell label="수익률 중앙값" value={formatPct(stats.medianReturn)} sub="발간일 → 최신 종가" valueClass={signColor(stats.medianReturn)} />
        <KpiCell
          label="전성기 — 더블 이상"
          value={<CountUp value={stats.peakDoubles} suffix="건" />}
          sub={`발간 24개월 내 +100% 도달${stats.peakDoubleRate !== null ? ` · ${stats.peakDoubleRate.toFixed(1)}%` : ""}`}
          valueClass="text-up"
        />
        <KpiCell
          label="판결까지 걸린 시간"
          value={stats.medianDaysToTarget !== null ? `${Math.round(stats.medianDaysToTarget)}일` : "—"}
          sub="적중 리포트의 목표가 도달 중앙값"
        />
      </dl>
    </header>
  );
}

function KpiCell({ label, value, sub, valueClass }: { label: string; value: React.ReactNode; sub: string; valueClass?: string }) {
  return (
    <div className="bg-card px-4 py-4 sm:px-5">
      <dt className="font-mono text-[10px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">{label}</dt>
      <dd className={cn("tnum mt-2 font-display text-2xl font-black tracking-tight sm:text-3xl", valueClass)}>{value}</dd>
      <dd className="mt-1 text-[11px] text-muted-foreground">{sub}</dd>
    </div>
  );
}

function VerdictTape() {
  const { dataset: reportDataset } = useReportData();
  const items = useMemo(
    () =>
      [...reportDataset.records]
        .filter((report) => report.era === "modern" && report.report_date && isBuy(report))
        .sort((a, b) => String(b.report_date).localeCompare(String(a.report_date)))
        .slice(0, 48),
    [reportDataset],
  );
  return (
    <div className="tape overflow-hidden border-y border-border bg-card/70 py-2" aria-hidden="true">
      {/* 항목당 12초 — 트랙 폭과 무관하게 일정한 저속 유지 */}
      <div className="tape-track" style={{ animationDuration: `${items.length * 12}s` }}>
        {[0, 1].map((copy) => (
          <div key={copy} className="flex shrink-0 items-center">
            {items.map((report) => (
              <span key={`${copy}-${report.source_name}`} className="mr-10 flex items-center gap-2 whitespace-nowrap font-mono text-[11px]">
                <span className="text-muted-foreground/80">{schoolShort[report.school]} {dateLabel(report.report_date).slice(2, 7)}</span>
                <span className="font-semibold text-foreground/70">{getDisplayName(report)}</span>
                <span className={cn("tnum font-bold opacity-85", signColor(report.return_latest_pct))}>{formatPct(report.return_latest_pct)}</span>
                {report.target_hit_until_latest && <span className="text-[10px] font-black tracking-tight text-stamp/80">[적중]</span>}
              </span>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** 새로 접수된 사건 + 판결까지 걸린 시간 분포 — 리포트에는 숙성의 시간이 필요하다 */
function FreshVerdicts({ onPick }: { onPick: (sourceName: string) => void }) {
  const { dataset: reportDataset } = useReportData();
  const latest = useMemo(
    () =>
      [...reportDataset.records]
        .filter((report) => report.era === "modern" && report.report_date && isBuy(report))
        .sort((a, b) => String(b.report_date).localeCompare(String(a.report_date)))
        .slice(0, 6),
    [reportDataset],
  );
  if (!latest.length) return null;
  return (
    <section aria-label="최근 발간 리포트와 판결 소요 시간" className="print:hidden">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">새로 접수된 사건</h2>
        <p className="font-mono text-[10px] text-muted-foreground">
          <span className="rounded-sm border border-dashed border-warn px-1 py-px font-bold text-warn">신생</span> 발간 90일 미만 — 성과 집계에서 보류
        </p>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {latest.map((report) => {
          const info = verdictOf(report);
          const fresh = report.maturity === "fresh";
          return (
            <button
              key={report.source_name}
              type="button"
              onClick={() => onPick(report.source_name)}
              className="rounded-lg border border-border bg-card p-3 text-left transition hover:-translate-y-0.5 hover:border-stamp"
              title="판결문 열기 (필터가 초기화됩니다)"
            >
              <p className="flex items-center justify-between font-mono text-[10px] text-muted-foreground">
                <span>
                  {schoolShort[report.school]} · {dateLabel(report.report_date)}
                </span>
                {fresh && (
                  <span className="rounded-sm border border-dashed border-warn px-1 py-px font-bold text-warn" title={`발간 ${report.age_days ?? "?"}일차`}>
                    신생
                  </span>
                )}
              </p>
              <p className="mt-1 truncate text-sm font-bold">{getDisplayName(report)}</p>
              <p className="mt-1.5 flex items-center justify-between font-mono text-xs">
                <span className={cn("font-black", stampTone[info.tone])}>{fresh ? "심리중" : info.stamp}</span>
                <span className={cn("tnum font-bold", signColor(report.return_latest_pct))}>{formatPct(report.return_latest_pct)}</span>
              </p>
            </button>
          );
        })}
      </div>
      <VerdictTimeStrip />
    </section>
  );
}

/** 판결까지 걸린 시간 분포 — 적중 리포트의 days_to_target 히스토그램 */
function VerdictTimeStrip() {
  const { dataset: reportDataset } = useReportData();
  const data = useMemo(() => {
    const days = reportDataset.records
      .filter((report) => report.era === "modern" && isBuy(report) && report.target_hit_until_latest && report.days_to_target !== null)
      .map((report) => report.days_to_target as number);
    const bins = [
      { label: "30일 이내", max: 30 },
      { label: "90일", max: 90 },
      { label: "180일", max: 180 },
      { label: "1년", max: 365 },
      { label: "1년 이후", max: Infinity },
    ].map((bin, index, all) => {
      const min = index === 0 ? -Infinity : all[index - 1].max;
      return { ...bin, count: days.filter((d) => d > min && d <= bin.max).length };
    });
    return { total: days.length, med: median(days), bins };
  }, [reportDataset]);
  if (!data.total) return null;
  const maxCount = Math.max(...data.bins.map((bin) => bin.count), 1);
  return (
    <div className="mt-4 rounded-lg border border-dashed border-border bg-card/60 px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">판결까지 걸린 시간 — 적중 {data.total}건의 분포</p>
        <p className="font-mono text-[10px] text-muted-foreground">
          중앙값 <span className="font-black text-stamp">{data.med !== null ? `${Math.round(data.med)}일` : "—"}</span> · 가치투자 리포트는 시간이 필요합니다
        </p>
      </div>
      <div className="mt-2.5 grid grid-cols-5 items-end gap-2">
        {data.bins.map((bin) => (
          <div key={bin.label} className="text-center">
            <p className="tnum font-mono text-[10px] font-bold">{bin.count}</p>
            <div
              className="mx-auto mt-1 w-full max-w-[72px] rounded-t-sm bg-stamp/75"
              style={{ height: `${Math.max(4, Math.round((bin.count / maxCount) * 44))}px` }}
              aria-hidden="true"
            />
            <p className="mt-1 border-t border-border pt-1 font-mono text-[9px] text-muted-foreground">{bin.label}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function FilterBar({ state }: { state: ReturnType<typeof useReportDashboardState> }) {
  const referenceTotal = state.referenceCounts.softBuy + state.referenceCounts.sell;
  /* 검색 디바운스 — 입력은 즉시 보이고, 1,395건 필터·연대기 재렌더는 150ms 숨 고른 뒤 한 번만 */
  const { query, setQuery } = state;
  const [draft, setDraft] = useState(query);
  const [lastQuery, setLastQuery] = useState(query);
  if (query !== lastQuery) {
    // 바깥(딥링크·서가 클릭)에서 query가 초기화되면 입력칸도 따라간다 — 렌더 중 상태 보정 패턴
    setLastQuery(query);
    setDraft(query);
  }
  useEffect(() => {
    if (draft === query) return;
    const timer = window.setTimeout(() => setQuery(draft), 150);
    return () => window.clearTimeout(timer);
  }, [draft, query, setQuery]);
  return (
    <section className="space-y-4 border-b border-border pb-5 print:hidden" aria-label="아카이브 필터">
      <div className="flex flex-wrap items-end gap-x-6 gap-y-2" role="group" aria-label="학교 필터">
        {schoolFilters.map((item) => {
          const active = state.school === item;
          return (
            <button
              key={item}
              type="button"
              onClick={() => state.setSchool(item)}
              aria-pressed={active}
              className={cn(
                "border-b-[3px] pb-1 font-display text-xl font-black tracking-tight transition",
                active ? "border-stamp text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {schoolLabels[item]}
            </button>
          );
        })}
      </div>

      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-wrap items-center gap-1.5">
          <span role="group" aria-label="시장 필터" className="mr-2 flex gap-1.5">
            {marketFilters.map((item) => {
              const active = state.market === item;
              return (
                <button
                  key={item}
                  type="button"
                  onClick={() => state.setMarket(item)}
                  aria-pressed={active}
                  className={cn(
                    "chip rounded-full border px-3 py-1 text-xs font-bold transition",
                    active ? "border-stamp bg-stamp text-background" : "border-border bg-transparent text-muted-foreground hover:border-foreground/50 hover:text-foreground",
                  )}
                >
                  {marketLabels[item]}
                </button>
              );
            })}
          </span>
          <span role="group" aria-label="성과 등급 필터" className="flex flex-wrap gap-1.5">
            {bucketFilters.map((item) => {
              const active = state.bucket === item;
              return (
                <button
                  key={item}
                  type="button"
                  onClick={() => state.setBucket(item)}
                  aria-pressed={active}
                  title={item === "ALL" ? undefined : bucketThresholds[item]}
                  className={cn(
                    "chip rounded-full border px-3 py-1 text-xs font-bold transition",
                    active ? "border-foreground bg-foreground text-background" : "border-border bg-transparent text-muted-foreground hover:border-foreground/50 hover:text-foreground",
                  )}
                >
                  {bucketFilterLabels[item]}
                </button>
              );
            })}
          </span>
          {referenceTotal > 0 && (
            <button
              type="button"
              onClick={() => state.setIncludeReference(!state.includeReference)}
              aria-pressed={state.includeReference}
              title="약한 매수(명시 의견 없음·보유)와 매도 의견은 참고용 — 성과 집계에서 제외됩니다"
              className={cn(
                "chip ml-2 rounded-full border border-dashed px-3 py-1 text-xs font-semibold transition",
                state.includeReference
                  ? "border-foreground/60 bg-secondary text-foreground"
                  : "border-border bg-transparent text-muted-foreground hover:border-foreground/50 hover:text-foreground",
              )}
            >
              참고: 약한 매수 {state.referenceCounts.softBuy}건
              {state.referenceCounts.sell > 0 ? ` · 매도 ${state.referenceCounts.sell}건` : ""}
            </button>
          )}
        </div>

        <label className="relative block w-full lg:max-w-xs">
          <span className="sr-only">회사명, 티커, 파일명 검색</span>
          <Search className="pointer-events-none absolute left-0 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            className="w-full border-0 border-b border-border bg-transparent py-1.5 pl-7 pr-2 text-sm outline-none transition placeholder:text-muted-foreground focus:border-stamp"
            placeholder="회사명 · 티커 · 파일명 검색"
          />
        </label>
      </div>
    </section>
  );
}

function VerdictPaper({
  report,
  verdict,
  index,
  total,
  onPrev,
  onNext,
}: {
  report: ReportRecord;
  verdict: Verdict;
  index: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  const { dataset: reportDataset } = useReportData();
  const targetReturn = report.target_price && report.start_close ? (report.target_price / report.start_close - 1) * 100 : null;
  const fresh = report.maturity === "fresh";
  const horizons = [
    { label: "1M", value: report.return_30d_pct },
    { label: "3M", value: report.return_90d_pct },
    { label: "6M", value: report.return_180d_pct },
    { label: "1Y", value: report.return_365d_pct },
    { label: "YTD", value: report.return_ytd_pct },
    { label: "최고", value: report.max_high_return_pct },
  ];

  return (
    <article
      id="verdict-paper"
      className="relative scroll-mt-4 overflow-hidden rounded-lg border-2 border-foreground/80 bg-card shadow-[7px_7px_0_0_hsl(var(--foreground)/0.85)] print:rounded-none print:border print:border-foreground print:shadow-none"
      aria-label="선택된 리포트 판결문"
    >
      {/* 인쇄 전용 레터헤드 — A4 판결문의 머리띠 */}
      <div className="hidden items-baseline justify-between border-b-2 border-foreground px-6 pb-2 pt-4 font-mono text-[10px] uppercase tracking-[0.3em] sm:px-8 print:flex">
        <span>판결 아카이브 — Research Verdict Archive</span>
        <span className="tracking-[0.1em]">기준일 {dateLabel(reportDataset.as_of)}</span>
      </div>
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-dashed border-border px-6 py-3 font-mono text-[11px] uppercase tracking-[0.2em] text-muted-foreground sm:px-8">
        <span>판결 기록 제 {index < 0 ? "—" : String(index + 1).padStart(3, "0")} 호</span>
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span>
            {SCHOOL_LABELS[report.school]} · {dateLabel(report.report_date)} 발간 · {report.market ?? "—"} · {report.ticker ?? "—"}
          </span>
          <SourceLinks report={report} className="print:hidden" />
          <span className="flex items-center gap-1.5 print:hidden">
            <ShareButton report={report} />
            <button
              type="button"
              onClick={() => window.print()}
              title="A4 판결문 양식으로 인쇄 — 화면 요소 없이 판결문만 찍힙니다"
              className="inline-flex items-center gap-1 rounded-sm border border-border px-1.5 py-0.5 font-mono text-[10px] font-semibold tracking-normal text-muted-foreground transition hover:border-foreground hover:text-foreground"
            >
              <Printer className="h-2.5 w-2.5" aria-hidden="true" /> 인쇄
            </button>
          </span>
        </span>
      </header>

      {/* key=source_name — 기록을 넘길 때마다 CSS 등장 애니메이션이 처음부터 다시 돈다 (framer-motion 대체) */}
      <div key={report.source_name} className="paper-enter relative px-6 py-6 sm:px-8">
        <div
          className={cn("stamp stamp-pop right-6 top-2 z-10 select-none text-2xl sm:right-10 sm:text-3xl", fresh ? "text-warn" : stampTone[verdict.tone])}
          aria-hidden="true"
        >
          {fresh ? "심리중" : verdict.stamp}
        </div>

        <h2 className="max-w-[70%] font-display text-3xl font-black tracking-tight sm:max-w-[78%] sm:text-5xl">
          {tickerSlug(report) ? (
            <Link href={`/stocks/${tickerSlug(report)}`} className="transition hover:text-stamp" title="이 종목을 다룬 모든 학회 리포트 보기">
              {getDisplayName(report)}
            </Link>
          ) : (
            getDisplayName(report)
          )}
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          <span className={cn("font-bold", stampTone[verdict.tone])}>{verdict.label}</span> · {verdict.detail}
        </p>
        {report.maturity && (
          <p className="mt-2 flex flex-wrap items-center gap-2 font-mono text-[11px]">
            <span
              className={cn(
                "rounded-sm border px-1.5 py-0.5 font-bold",
                fresh ? "border-dashed border-warn text-warn" : "border-border text-muted-foreground",
              )}
            >
              {maturityLabels[report.maturity]} · 발간 {report.age_days?.toLocaleString("ko-KR") ?? "?"}일차
            </span>
            {fresh && <span className="text-muted-foreground">90일이 지나기 전에는 성적 집계에서 보류됩니다 — 판결에는 시간이 필요합니다</span>}
          </p>
        )}

        <div className="mt-7 grid gap-7 lg:grid-cols-2">
          <section aria-label="발간 당시의 주장">
            <h3 className="font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">그날의 주장 — {dateLabel(report.report_date)}</h3>
            <dl className="mt-3 border-y border-dashed border-border">
              <ClaimRow label="투자의견" value={report.rating ?? "—"} />
              <ClaimRow label="발간 시점 주가" value={formatPrice(report.report_current_price, report.market)} />
              <ClaimRow
                label="목표 주가"
                value={formatPrice(report.target_price, report.market)}
                emphasis
                badge={
                  report.target_seq !== null && (report.target_seq_total ?? 0) > 1 ? (
                    <span
                      className="rounded-sm border border-stamp/40 px-1 py-px font-mono text-[10px] font-bold tracking-normal text-stamp"
                      title={`${schoolShort[report.school]}가 이 종목에 제시한 ${report.target_seq}번째 목표 — 총 ${report.target_seq_total}회에 걸쳐 목표를 이어 썼습니다`}
                    >
                      목표 {report.target_seq}/{report.target_seq_total}
                    </span>
                  ) : null
                }
              />
              <ClaimRow label="제시 상승여력 (괴리율)" value={formatPct(report.stated_upside_pct)} />
            </dl>
          </section>

          <section aria-label="시간이 내린 판정">
            <h3 className="font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">시간의 판정 — {dateLabel(report.latest_trade_date)}</h3>
            <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
              <p className={cn("tnum font-display text-5xl font-black leading-none tracking-tight sm:text-6xl", signColor(report.return_latest_pct))}>
                {formatPct(report.return_latest_pct)}
              </p>
              {(report.performance_bucket === "Tenbagger" || report.performance_bucket === "Multibagger") && (
                <span
                  className={cn(
                    "inline-block -rotate-3 rounded-md border-2 px-2.5 py-1 font-display text-base tracking-tight",
                    bucketBadgeClass[report.performance_bucket],
                  )}
                  title={`${bucketLabels[report.performance_bucket]} — ${bucketThresholds[report.performance_bucket]}`}
                >
                  {bucketLabels[report.performance_bucket]}
                </span>
              )}
            </div>
            <p className="mt-1.5 text-[11px] text-muted-foreground">
              발간 후 현재까지 수익률 · 최신 종가 {formatPrice(report.latest_close, report.market)}
              {report.alpha_latest_pct !== null && (
                <>
                  {" · "}
                  지수 대비 <span className={cn("font-bold", signColor(report.alpha_latest_pct))}>{formatPct(report.alpha_latest_pct, 1)}</span>
                  <span className="opacity-75"> (동기간 지수 {formatPct(report.benchmark_return_pct, 1)})</span>
                </>
              )}
              {report.target_hit_until_latest && report.days_to_target !== null && (
                <>
                  {" · "}
                  판결까지 <span className="font-bold text-stamp">{report.days_to_target.toLocaleString("ko-KR")}일</span>
                </>
              )}
            </p>
            <div className="mt-4 grid grid-cols-3 border-y border-dashed border-border sm:grid-cols-6 sm:divide-x sm:divide-dashed sm:divide-border">
              {horizons.map((item) => (
                <div key={item.label} className="px-2 py-2 text-center sm:first:pl-0 sm:last:pr-0">
                  <p className="font-mono text-[10px] text-muted-foreground">{item.label}</p>
                  <p className={cn("tnum mt-1 text-[13px] font-black", signColor(item.value))}>{formatPct(item.value, 0)}</p>
                </div>
              ))}
            </div>
          </section>
        </div>

        <DualVerdict report={report} />

        {tickerSlug(report) && report.report_date ? (
          <figure className="mt-7">
            <figcaption className="mb-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">
                가격 경로 — 발간가 기준선
              </span>
              <span className="font-mono text-[10px] text-muted-foreground">위는 상승(적) · 아래는 하락(청) · ┄ 목표가</span>
            </figcaption>
            <ReportPathChart
              slug={tickerSlug(report) as string}
              market={report.market}
              reportDate={report.report_date}
              targetPrice={report.target_price}
              hitDate={report.first_target_hit_date}
              eager
              className="h-[220px] sm:h-[280px]"
              fallback={<Trajectory report={report} targetReturn={targetReturn} />}
            />
          </figure>
        ) : (
          <Trajectory report={report} targetReturn={targetReturn} className="mt-7" />
        )}

        <div className="mt-7 flex items-center justify-between gap-3 border-t border-dashed border-border pt-4 print:hidden">
          <button
            type="button"
            onClick={onPrev}
            className="flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs font-bold transition hover:border-foreground hover:bg-secondary"
          >
            <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" /> 이전 기록
          </button>
          <span className="tnum text-center font-mono text-[11px] text-muted-foreground">
            {index < 0 ? "—" : index + 1} / {total}
            <span className="hidden sm:inline"> · 방향키 ← → 로 넘길 수 있습니다</span>
          </span>
          <button
            type="button"
            onClick={onNext}
            className="flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs font-bold transition hover:border-foreground hover:bg-secondary"
          >
            다음 기록 <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      </div>
    </article>
  );
}

/**
 * 이중 판결 — 보고서의 시간과 시장의 시간은 다르다.
 * 왼쪽에는 발간 24개월 안에 닿은 전성기, 오른쪽에는 오늘 다시 읽은 성적.
 * 전성기 더블 이상 · 현재 조정 이하면 판결문이 한 줄로 변호한다: "보고서는 제 일을 했다."
 */
function DualVerdict({ report }: { report: ReportRecord }) {
  if (report.peak_return_24m_pct === null) return null;
  const line = reportDidItsJob(report);
  const lag = peakLag(report);
  return (
    <section aria-label="이중 판결 — 전성기와 현재" className="mt-7 overflow-hidden rounded-md border border-dashed border-border">
      <div className="grid sm:grid-cols-2 sm:divide-x sm:divide-dashed sm:divide-border">
        <div className="px-4 py-3.5 sm:px-5">
          <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">
            전성기 판결 — {dateLabel(report.peak_date_24m)}
          </p>
          <p className="mt-2 flex flex-wrap items-center gap-2">
            <span className={cn("tnum font-display text-3xl font-black leading-none tracking-tight", signColor(report.peak_return_24m_pct))}>
              {formatPct(report.peak_return_24m_pct)}
            </span>
            <span
              className={cn("inline-block -rotate-2 rounded-md border px-1.5 py-0.5 font-display text-xs tracking-tight", bucketBadgeClass[report.bucket_peak])}
              title={`${bucketLabels[report.bucket_peak]} — ${bucketThresholds[report.bucket_peak]} · 발간 24개월 내 최고가 기준`}
            >
              {bucketLabels[report.bucket_peak]}
            </span>
          </p>
          <p className="mt-1.5 text-[11px] text-muted-foreground">{lag ? `${lag} 닿은 ` : ""}발간 24개월 내 장중 최고가</p>
        </div>
        <div className="border-t border-dashed border-border px-4 py-3.5 sm:border-t-0 sm:px-5">
          <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">
            현재 판결 — {dateLabel(report.latest_trade_date)}
          </p>
          <p className="mt-2 flex flex-wrap items-center gap-2">
            <span className={cn("tnum font-display text-3xl font-black leading-none tracking-tight", signColor(report.return_latest_pct))}>
              {formatPct(report.return_latest_pct)}
            </span>
            <span
              className={cn("inline-block -rotate-2 rounded-md border px-1.5 py-0.5 font-display text-xs tracking-tight", bucketBadgeClass[report.performance_bucket])}
              title={`${bucketLabels[report.performance_bucket]} — ${bucketThresholds[report.performance_bucket]}`}
            >
              {bucketLabels[report.performance_bucket]}
            </span>
          </p>
          <p className="mt-1.5 text-[11px] text-muted-foreground">오늘 다시 읽은 같은 보고서의 성적</p>
        </div>
      </div>
      {line && (
        <p className="border-t border-dashed border-border bg-secondary/50 px-4 py-2.5 font-display text-sm font-bold tracking-tight sm:px-5">
          <span className="text-stamp">{line.lead}</span>
          <span className="text-muted-foreground"> — </span>
          {line.rest}
        </p>
      )}
    </section>
  );
}

/** 판결문 공유 — /#r= 딥링크를 클립보드로. 누구에게 보내도 같은 판결문이 펼쳐진다 */
function ShareButton({ report }: { report: ReportRecord }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(timer);
  }, [copied]);
  const copy = async () => {
    const url = `${window.location.origin}/#r=${encodeURIComponent(report.source_name)}`;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      // 클립보드 권한이 없으면 주소창 해시로라도 남긴다
      window.history.replaceState(null, "", `#r=${encodeURIComponent(report.source_name)}`);
      setCopied(true);
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      title="이 판결문으로 바로 오는 링크 복사"
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-[10px] font-semibold tracking-normal transition",
        copied ? "border-stamp/60 text-stamp" : "border-border text-muted-foreground hover:border-foreground hover:text-foreground",
      )}
    >
      {copied ? <Check className="h-2.5 w-2.5" aria-hidden="true" /> : <Link2 className="h-2.5 w-2.5" aria-hidden="true" />}
      {copied ? "복사됨" : "공유"}
    </button>
  );
}

function ClaimRow({ label, value, emphasis = false, badge }: { label: string; value: string; emphasis?: boolean; badge?: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-dashed border-border py-2.5 last:border-b-0">
      <dt className="flex items-baseline gap-2 text-xs font-semibold text-muted-foreground">
        {label}
        {badge}
      </dt>
      <dd className={cn("tnum text-right font-bold", emphasis ? "font-display text-xl text-stamp underline decoration-stamp/40 decoration-2 underline-offset-4" : "text-sm")}>
        {value}
      </dd>
    </div>
  );
}

function Trajectory({ report, targetReturn, className }: { report: ReportRecord; targetReturn: number | null; className?: string }) {
  const data = useMemo(() => {
    const elapsed = diffDays(report.first_trade_date, report.latest_trade_date);
    const totalDays = Math.max(elapsed ?? 0, 30);
    const raw = [
      { day: 0, ret: 0 as number | null, label: "발간" },
      { day: 30, ret: report.return_30d_pct, label: "30일" },
      { day: 90, ret: report.return_90d_pct, label: "90일" },
      { day: 180, ret: report.return_180d_pct, label: "180일" },
      { day: 365, ret: report.return_365d_pct, label: "1년" },
      { day: totalDays, ret: report.return_latest_pct, label: "현재" },
    ];
    const byDay = new Map<number, { day: number; ret: number; label: string }>();
    for (const point of raw) {
      if (point.ret === null || point.day > totalDays) continue;
      byDay.set(point.day, { day: point.day, ret: point.ret, label: point.label });
    }
    return { totalDays, points: [...byDay.values()].sort((a, b) => a.day - b.day) };
  }, [report]);

  if (data.points.length < 2) {
    return (
      <div className={cn("rounded-md border border-dashed border-border px-6 py-8 text-center text-sm text-muted-foreground", className)}>
        가격 경로를 그릴 시세 데이터가 없습니다{report.data_issue ? ` — ${report.data_issue}` : "."}
      </div>
    );
  }

  const W = 720;
  const H = 240;
  const padL = 12;
  const padR = 16;
  const padT = 26;
  const padB = 30;
  const returns = data.points.map((point) => point.ret);
  const maxHigh = report.max_high_return_pct;
  const yMaxRaw = Math.max(...returns, targetReturn ?? -Infinity, maxHigh ?? -Infinity, 10);
  const yMinRaw = Math.min(...returns, -8);
  const pad = (yMaxRaw - yMinRaw) * 0.14;
  const yMax = yMaxRaw + pad;
  const yMin = yMinRaw - pad;
  const yRange = yMax - yMin || 1;
  const x = (day: number) => padL + Math.sqrt(day / data.totalDays) * (W - padL - padR);
  const y = (ret: number) => padT + ((yMax - ret) / yRange) * (H - padT - padB);
  const path = data.points.map((point, i) => `${i ? "L" : "M"}${x(point.day).toFixed(1)},${y(point.ret).toFixed(1)}`).join(" ");
  const last = data.points[data.points.length - 1];
  const lineTone = last.ret >= 0 ? "stroke-up" : "stroke-down";

  return (
    <figure className={className}>
      <figcaption className="mb-2 flex items-baseline justify-between gap-3">
        <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">가격 경로 — 발간 후 {data.totalDays.toLocaleString()}일</span>
        <span className="font-mono text-[10px] text-muted-foreground">가로축은 √시간 척도</span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="발간 이후 구간별 수익률 경로">
        <line x1={padL} x2={W - padR} y1={y(0)} y2={y(0)} className="stroke-border" strokeWidth="1" />
        <text x={W - padR} y={y(0) + 13} textAnchor="end" fontSize="10" className="fill-muted-foreground font-mono">
          발간가 0%
        </text>
        {targetReturn !== null && targetReturn > yMin && targetReturn < yMax && (
          <g>
            <line x1={padL} x2={W - padR} y1={y(targetReturn)} y2={y(targetReturn)} className="stroke-stamp" strokeDasharray="7 5" strokeWidth="1.4" />
            <text x={W - padR} y={y(targetReturn) - 6} textAnchor="end" fontSize="11" fontWeight="700" className="fill-stamp font-mono">
              목표 {formatPct(targetReturn)}
            </text>
          </g>
        )}
        {maxHigh !== null && maxHigh > (last.ret ?? 0) && maxHigh > yMin && maxHigh < yMax && (
          <g>
            <line x1={padL} x2={W - padR} y1={y(maxHigh)} y2={y(maxHigh)} className="stroke-muted-foreground/60" strokeDasharray="2 6" strokeWidth="1" />
            <text x={padL} y={y(maxHigh) - 5} fontSize="10" className="fill-muted-foreground font-mono">
              장중 최고 {formatPct(maxHigh)}
            </text>
          </g>
        )}
        <path d={path} fill="none" className={lineTone} strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" />
        {data.points.map((point) => (
          <g key={point.day}>
            <circle
              cx={x(point.day)}
              cy={y(point.ret)}
              r={point.label === "현재" ? 5 : 3.2}
              strokeWidth="1.5"
              className={cn("stroke-card", point.ret >= 0 ? "fill-up" : "fill-down")}
            />
            <text x={x(point.day)} y={H - 9} textAnchor="middle" fontSize="10" className="fill-muted-foreground font-mono">
              {point.label}
            </text>
            <text
              x={x(point.day)}
              y={y(point.ret) - 10}
              textAnchor="middle"
              fontSize="10"
              fontWeight="700"
              className={cn("font-mono", point.ret >= 0 ? "fill-up" : "fill-down")}
            >
              {formatPct(point.ret, 0)}
            </text>
          </g>
        ))}
      </svg>
    </figure>
  );
}

function Chronicle({ reports, selected, onSelect }: { reports: ReportRecord[]; selected: ReportRecord; onSelect: (sourceName: string) => void }) {
  // 발간 번호는 연대순을 유지하되, 목록은 최신 리포트부터 보여준다
  const rows = reports.map((report, index) => ({ report, no: index + 1 })).reverse();
  return (
    <aside className="flex max-h-[920px] flex-col overflow-hidden rounded-lg border border-border bg-card print:hidden" aria-label="발간 연대기">
      <header className="flex items-baseline justify-between border-b-4 border-double border-border px-4 py-3">
        <h2 className="font-display text-xl font-black tracking-tight">연대기</h2>
        <p className="font-mono text-[11px] text-muted-foreground">{reports.length}건 · 최신순</p>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {rows.map(({ report, no }, index) => {
          const active = report.source_name === selected.source_name;
          const divider = index === 0 || yearOf(report) !== yearOf(rows[index - 1].report);
          const reference = !isBuy(report);
          return (
            <Fragment key={report.source_name}>
              {divider && (
                <p className="sticky top-0 z-10 border-y border-border bg-secondary px-4 py-1 font-display text-sm font-black">{yearOf(report)}</p>
              )}
              <button
                type="button"
                onClick={() => onSelect(report.source_name)}
                aria-pressed={active}
                className={cn(
                  "cv-row grid w-full grid-cols-[2.6rem_minmax(0,1fr)_auto] items-center gap-2 border-b border-dashed border-border px-4 py-2.5 text-left transition",
                  active ? "bg-foreground text-background" : "hover:bg-secondary",
                  reference && !active && "opacity-55",
                )}
              >
                <span className={cn("font-mono text-[11px]", active ? "opacity-70" : "text-muted-foreground")}>{String(no).padStart(3, "0")}</span>
                <span className="min-w-0">
                  <span className="block truncate text-sm font-bold">
                    {getDisplayName(report)}
                    {report.maturity === "fresh" && (
                      <span className={cn("ml-1.5 align-middle font-mono text-[9px] font-bold", active ? "opacity-80" : "text-warn")}>신생</span>
                    )}
                  </span>
                  <span className={cn("block text-[11px]", active ? "opacity-70" : "text-muted-foreground")}>
                    {schoolShort[report.school]} · {dateLabel(report.report_date)} · {report.ticker ?? "—"}
                    {report.target_hit_until_latest ? " · 적중" : ""}
                    {reference ? " · 참고" : ""}
                  </span>
                </span>
                <span className={cn("tnum font-mono text-sm font-bold", active ? "text-background" : signColor(report.return_latest_pct))}>
                  {formatPct(report.return_latest_pct)}
                </span>
              </button>
            </Fragment>
          );
        })}
        {reports.length === 0 && <p className="px-6 py-10 text-center text-sm text-muted-foreground">조건에 맞는 리포트가 없습니다.</p>}
      </div>
    </aside>
  );
}

