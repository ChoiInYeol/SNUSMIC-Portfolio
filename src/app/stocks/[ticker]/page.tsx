import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { ApiResourceLink } from "@/components/api-resource-link";
import { SiteHeader } from "@/components/site-header";
import { ReportLedger, StatStrip } from "@/components/report-ledger";
import { StockChart } from "@/components/stock-chart";
import { dateLabel, getDisplayName, reportDataset, SCHOOL_LABELS, type ReportRecord } from "@/lib/report-model";
import { isBuy, signColor, tickerSlug } from "@/lib/verdict";
import { formatPct, formatPrice } from "@/lib/utils";
import { v2Exists } from "@/lib/api-v2-server";

function groupBySlug() {
  const groups = new Map<string, ReportRecord[]>();
  for (const record of reportDataset.records) {
    const slug = tickerSlug(record);
    if (!slug) continue;
    groups.set(slug, [...(groups.get(slug) ?? []), record]);
  }
  return groups;
}

export function generateStaticParams() {
  return [...groupBySlug().keys()].map((ticker) => ({ ticker }));
}

export async function generateMetadata({ params }: { params: Promise<{ ticker: string }> }): Promise<Metadata> {
  const { ticker } = await params;
  const records = groupBySlug().get(ticker.toLowerCase());
  const name = records?.length ? getDisplayName(records[0]) : "종목";
  return { title: `${name} — 학회 컨센서스 · 판결 아카이브` };
}

export default async function StockPage({ params }: { params: Promise<{ ticker: string }> }) {
  const { ticker } = await params;
  const records = groupBySlug().get(ticker.toLowerCase());
  if (!records?.length) notFound();

  const all = [...records].sort((a, b) => String(b.report_date ?? "").localeCompare(String(a.report_date ?? "")));
  // 채점 모집단은 modern 시대(2019-07 이후) — 아카이브 시대 기록은 별도 보관
  const sorted = all.filter((r) => r.era === "modern");
  const archive = all.filter((r) => r.era === "archive");
  const latest = sorted[0] ?? all[0];
  const name = getDisplayName(latest);
  const schools = [...new Set(sorted.map((r) => r.school))];
  // 컨센서스 통계는 매수 의견 기준 — 약한 매수·매도는 참고 기록
  const buys = sorted.filter(isBuy);
  const hits = buys.filter((r) => r.target_hit_until_latest).length;
  const targets = buys.filter((r) => r.target_price !== null).map((r) => r.target_price as number);
  const instrumentUri = `/api/v2/instruments/${ticker.toLowerCase()}`;
  const hasInstrument = await v2Exists(instrumentUri);
  const upsides = buys.filter((r) => r.stated_upside_pct !== null).map((r) => r.stated_upside_pct as number);

  return (
    <main className="mx-auto max-w-[1500px] space-y-8 px-4 py-6 sm:px-8">
      <SiteHeader eyebrow="Stock Consensus" />

      <header>
        <p className="mt-3 font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">
          {latest.market ?? "—"} · {latest.ticker} · {latest.exchange ?? ""}
        </p>
        <h1 className="mt-2 font-display text-4xl font-black tracking-tight sm:text-6xl">{name}</h1>
        <p className="mt-3 max-w-2xl text-base leading-7 text-muted-foreground">
          {sorted.length === 0
            ? `이 종목의 기록 ${archive.length}건은 모두 2019년 7월 이전 아카이브 시대의 것입니다 — 채점 없이 보관만 합니다.`
            : schools.length > 1
              ? `${schools.map((s) => SCHOOL_LABELS[s]).join(" · ")} — ${schools.length}개 학회가 이 종목을 다뤘습니다. 같은 종목에 대한 서로 다른(또는 같은) 판단을 시간 순으로 비교해 보세요.`
              : `${SCHOOL_LABELS[latest.school]}가 이 종목을 ${sorted.length}회 다뤘습니다.${archive.length > 0 ? ` 2019-07 이전 아카이브 ${archive.length}건은 하단에 따로 보관됩니다.` : ""}`}
        </p>
        {hasInstrument && (
          <div className="mt-3">
            <ApiResourceLink uri={instrumentUri} label="종목 리소스" />
          </div>
        )}
      </header>

      {sorted.length > 0 && (
      <StatStrip
        items={[
          { label: "리포트", value: `${sorted.length}건` },
          { label: "커버 학회", value: `${schools.length}곳` },
          { label: "목표가 적중", value: `${hits}건`, tone: "text-stamp" },
          {
            label: "목표가 범위",
            value: targets.length
              ? `${formatPrice(Math.min(...targets), latest.market)}~${formatPrice(Math.max(...targets), latest.market)}`
              : "—",
          },
          {
            label: "평균 제시 상승여력",
            value: formatPct(upsides.length ? upsides.reduce((a, b) => a + b, 0) / upsides.length : null, 0),
          },
          {
            label: `최신 종가 (${dateLabel(latest.latest_trade_date)})`,
            value: formatPrice(latest.latest_close, latest.market),
            tone: signColor(latest.return_latest_pct),
          },
        ]}
      />
      )}

      <StockChart slug={ticker.toLowerCase()} name={name} reports={sorted} archived={archive} />

      {sorted.length > 0 && (
        <section aria-label="학회별 주장 비교">
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">학회별 판결 기록 — 2019-07 이후</h2>
            <p className="font-mono text-[10px] text-muted-foreground">머리글 클릭으로 정렬 · Shift+클릭으로 다중 정렬 · ▸ 발간일로 가격 경로 펼침</p>
          </div>
          <ReportLedger reports={sorted} showSchool linkStocks={false} />
        </section>
      )}

      {archive.length > 0 && (
        <section aria-label="아카이브 시대 기록 — 채점 제외">
          <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1">
            <h2 className="font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">아카이브 (2019-07 이전)</h2>
            <span className="rounded-sm border border-dashed border-warn px-1.5 py-0.5 font-mono text-[10px] font-bold text-warn">채점 제외</span>
            <p className="font-mono text-[10px] text-muted-foreground">다른 학회 수집 범위 밖의 SMIC 단독 기록 {archive.length}건 — 성적 비교에서 빠집니다</p>
          </div>
          <ReportLedger reports={archive} showSchool linkStocks={false} />
        </section>
      )}
    </main>
  );
}
