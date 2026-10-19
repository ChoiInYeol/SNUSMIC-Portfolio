import type { Metadata } from "next";
import Link from "next/link";
import { Fragment } from "react";
import { ApiResourceLink } from "@/components/api-resource-link";
import { SiteHeader } from "@/components/site-header";
import { STRATEGY_LABEL_KO } from "@/components/strategy/strategy-meta";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { loadSnapshotIndex } from "@/lib/api-v2-server";
import { dateLabel } from "@/lib/report-model";
import { cn } from "@/lib/utils";

export const metadata: Metadata = {
  title: "판결일보 축쇄판 — 판결 아카이브",
  description: "매 거래일 박제된 신호 스냅샷으로 다시 조판한 판결일보 지난 호 전체 목록.",
};

const WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];

export default async function TodayArchivePage() {
  const index = await loadSnapshotIndex(); // newest first
  const months = new Map<string, typeof index>();
  for (const entry of index) {
    const month = entry.date.slice(0, 7);
    months.set(month, [...(months.get(month) ?? []), entry]);
  }
  // Issues whose headline strategy differs from the previous (older) issue.
  const switched = new Set(index.filter((entry, i) => i + 1 < index.length && index[i + 1].headline_strategy !== entry.headline_strategy).map((entry) => entry.date));
  const maxOpen = Math.max(1, ...index.map((entry) => entry.counts.open_positions));

  return (
    <main className="mx-auto max-w-[1180px] px-4 py-6 sm:px-8">
      <SiteHeader eyebrow="Daily Verdict — Bound Volume" />

      <header className="mt-6 border-b-4 border-double border-foreground pb-4">
        <p className="font-mono text-[11px] font-bold uppercase tracking-[0.25em] text-stamp">축쇄판</p>
        <h1 className="mt-2 font-display text-4xl font-black tracking-tight sm:text-6xl">판결일보 지난 호</h1>
        <p className="mt-3 max-w-2xl text-base leading-7 text-muted-foreground">
          매 거래일 박제된 신호 스냅샷 {index.length}개 — 각 호는 그날의 스냅샷과 그날까지 발간된 리포트만으로 다시
          조판됩니다. 나중에 알게 된 결과는 지난 호에 실리지 않습니다.
        </p>
        <div className="mt-3">
          <ApiResourceLink uri="/api/v2/signals" label="스냅샷 목록" />
        </div>
      </header>

      {index.length === 0 ? (
        <p className="py-16 text-center font-mono text-sm text-muted-foreground">아직 박제된 스냅샷이 없습니다.</p>
      ) : (
        <Table className="mt-6 font-mono text-[12px]">
          <TableHeader>
            <TableRow className="border-foreground/60 hover:bg-transparent">
              <TableHead className="text-[10px] font-bold uppercase tracking-[0.18em]">발행일</TableHead>
              <TableHead className="hidden text-[10px] font-bold uppercase tracking-[0.18em] sm:table-cell">헤드라인 전략</TableHead>
              <TableHead className="text-[10px] font-bold uppercase tracking-[0.18em]">보유</TableHead>
              <TableHead className="text-right text-[10px] font-bold uppercase tracking-[0.18em]">매수 신호</TableHead>
              <TableHead className="text-right text-[10px] font-bold uppercase tracking-[0.18em]">매도 경보</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {[...months.entries()].map(([month, entries]) => (
              <Fragment key={month}>
                <TableRow className="hover:bg-transparent">
                  <TableCell colSpan={5} className="pt-5 pb-1 font-display text-lg font-black tracking-tight">
                    {month.slice(0, 4)}년 {Number(month.slice(5))}월{" "}
                    <span className="font-mono text-[11px] font-normal text-muted-foreground">{entries.length}호</span>
                  </TableCell>
                </TableRow>
                {entries.map((entry) => {
                  const latest = entry.date === index[0].date;
                  return (
                    <TableRow key={entry.date}>
                      <TableCell className="py-1.5">
                        <Link
                          href={`/today/${entry.date}`}
                          className="font-bold tnum underline-offset-2 hover:underline"
                          aria-label={`${dateLabel(entry.date)} 호 보기`}
                        >
                          {entry.date}
                        </Link>{" "}
                        <span className="text-muted-foreground">({WEEKDAY[new Date(`${entry.date}T00:00:00Z`).getUTCDay()]})</span>
                        {latest && <span className="ml-2 rounded-sm bg-stamp px-1 text-[9px] font-bold text-background">최신</span>}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "hidden max-w-[16rem] truncate py-1.5 sm:table-cell",
                          switched.has(entry.date) && "font-bold text-warn",
                        )}
                        title={entry.headline_strategy}
                      >
                        {STRATEGY_LABEL_KO[entry.headline_strategy] ?? entry.headline_strategy}
                      </TableCell>
                      <TableCell className="py-1.5">
                        <span className="flex items-center gap-2">
                          <span className="w-5 text-right tnum">{entry.counts.open_positions}</span>
                          <span aria-hidden="true" className="hidden h-1.5 w-24 overflow-hidden rounded-full bg-muted sm:block">
                            <span
                              className="block h-full rounded-full bg-foreground/70"
                              style={{ width: `${(entry.counts.open_positions / maxOpen) * 100}%` }}
                            />
                          </span>
                        </span>
                      </TableCell>
                      <TableCell className={cn("py-1.5 text-right tnum", entry.counts.buy_signals ? "font-bold text-up" : "text-muted-foreground")}>
                        {entry.counts.buy_signals}
                      </TableCell>
                      <TableCell className={cn("py-1.5 text-right tnum", entry.counts.sell_signals ? "font-bold text-warn" : "text-muted-foreground")}>
                        {entry.counts.sell_signals}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      )}
      <p className="mt-4 font-mono text-[10px] text-muted-foreground">
        황색 전략명 = 그날 헤드라인 전략이 바뀜 · 모든 신호는 백테스트 시뮬레이션이며 투자 권유가 아닙니다.
      </p>
    </main>
  );
}
