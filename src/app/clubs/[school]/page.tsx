import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { ApiResourceLink } from "@/components/api-resource-link";
import { SiteHeader } from "@/components/site-header";
import { ReportLedger, StatStrip } from "@/components/report-ledger";
import { reportDataset, SCHOOL_LABELS, type School } from "@/lib/report-model";
import { clubStats, isBuy, median, signColor } from "@/lib/verdict";
import { formatPct } from "@/lib/utils";

const SCHOOLS: School[] = ["smic", "yig", "star", "kuvic", "ewha", "voera"];

export function generateStaticParams() {
  return SCHOOLS.map((school) => ({ school }));
}

export async function generateMetadata({ params }: { params: Promise<{ school: string }> }): Promise<Metadata> {
  const { school } = await params;
  const label = SCHOOL_LABELS[school as School];
  return { title: label ? `${label} 성적표 — 판결 아카이브` : "학회 성적표" };
}

export default async function ClubPage({ params }: { params: Promise<{ school: string }> }) {
  const { school } = await params;
  if (!SCHOOLS.includes(school as School)) notFound();
  const club = school as School;

  const all = reportDataset.records
    .filter((r) => r.school === club)
    .sort((a, b) => String(b.report_date ?? "").localeCompare(String(a.report_date ?? "")));
  // 채점 모집단은 modern 시대(2019-07 이후) — 네 학회가 같은 출발선에서 비교된다
  const records = all.filter((r) => r.era === "modern");
  const archive = all.filter((r) => r.era === "archive");
  const stats = clubStats(all);

  // 연도별 요약도 성적표와 같은 잣대 — modern 시대 매수 의견만 집계
  const buys = records.filter(isBuy);
  const yearGroups = new Map<string, typeof records>();
  for (const record of buys) {
    const year = record.report_date?.slice(0, 4) ?? "????";
    yearGroups.set(year, [...(yearGroups.get(year) ?? []), record]);
  }
  const years = [...yearGroups.entries()].sort(([a], [b]) => b.localeCompare(a));

  return (
    <main className="mx-auto max-w-[1500px] space-y-8 px-4 py-6 sm:px-8">
      <SiteHeader eyebrow={`Club Scorecard — ${SCHOOL_LABELS[club]}`} />

      <header>
        <h1 className="mt-3 font-display text-4xl font-black tracking-tight sm:text-6xl">{SCHOOL_LABELS[club]}</h1>
        <p className="mt-3 max-w-2xl text-base leading-7 text-foreground/75">
          {SCHOOL_LABELS[club]}의 2019년 7월 이후 매수 의견 리포트 {stats.total}건을 point-in-time 시세로 채점한 성적표입니다. 발간 90일
          미만의 신생 {stats.fresh}건은 판결 보류, 약한 매수·매도 등 참고 기록 {stats.reference}건은 집계에서 제외했습니다.
          {archive.length > 0 && ` 2019년 7월 이전 수집분 ${archive.length}건은 페이지 하단에 채점 없이 보관되어 있습니다.`}
        </p>
        <div className="mt-3">
          <ApiResourceLink uri={`/api/v2/clubs/${club}/reports`} label="학회 리포트 원본" />
        </div>
      </header>

      <StatStrip
        items={[
          { label: "매수 리포트", value: `${stats.total}건` },
          { label: "가격 검증", value: `${stats.priced}건` },
          { label: "목표가 적중", value: `${stats.hits}건`, tone: "text-stamp" },
          { label: "적중률", value: formatPct(stats.hitRate, 1).replace("+", "") },
          { label: "수익률 중앙값", value: formatPct(stats.medianReturn), tone: signColor(stats.medianReturn) },
          {
            label: "판결까지 걸린 시간",
            value: stats.medianDaysToTarget !== null ? `${Math.round(stats.medianDaysToTarget)}일` : "—",
          },
        ]}
      />

      <section aria-label="연도별 요약">
        <h2 className="mb-3 font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">연도별 요약</h2>
        <div className="flex flex-wrap gap-2">
          {years.map(([year, list]) => {
            const returns = list.filter((r) => r.return_latest_pct !== null).map((r) => r.return_latest_pct as number);
            const med = median(returns);
            const hits = list.filter((r) => r.target_hit_until_latest).length;
            return (
              <div key={year} className="rounded-md border border-border bg-card px-3 py-2">
                <p className="font-display text-base font-black leading-none">{year}</p>
                <p className="tnum mt-1 font-mono text-[10px] text-muted-foreground">
                  {list.length}건 · 적중 {hits} · <span className={signColor(med)}>{formatPct(med, 0)}</span>
                </p>
              </div>
            );
          })}
        </div>
      </section>

      <section aria-label="리포트 전체 목록">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">전체 기록 — 2019-07 이후</h2>
          <p className="font-mono text-[10px] text-muted-foreground">머리글 클릭으로 정렬 · Shift+클릭으로 다중 정렬 · ▸ 발간일로 가격 경로 펼침</p>
        </div>
        <ReportLedger reports={records} />
      </section>

      {archive.length > 0 && (
        <section aria-label="아카이브 시대 기록 — 채점 제외">
          <details className="group rounded-lg border border-dashed border-border bg-card/60">
            <summary className="flex min-h-[2.75rem] cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 sm:px-5 [&::-webkit-details-marker]:hidden">
              <span className="font-mono text-[11px] font-bold transition-transform group-open:rotate-90" aria-hidden="true">▸</span>
              <h2 className="font-display text-lg font-black tracking-tight">아카이브 (2019-07 이전)</h2>
              <span className="rounded-sm border border-dashed border-warn px-1.5 py-0.5 font-mono text-[10px] font-bold text-warn">채점 제외</span>
              <span className="font-mono text-[11px] text-muted-foreground">
                {archive.length}건 — 다른 학회 수집 범위 밖의 SMIC 단독 기록이라 성적 비교에서 빠집니다
              </span>
            </summary>
            <div className="border-t border-dashed border-border px-4 py-4 sm:px-5">
              <ReportLedger reports={archive} />
            </div>
          </details>
        </section>
      )}
    </main>
  );
}
