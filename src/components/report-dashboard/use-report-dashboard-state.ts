"use client";

import { useMemo, useState } from "react";
import type { Market, ReportRecord, School } from "@/lib/report-model";
import { isBuy } from "@/lib/verdict";

type MarketFilter = "ALL" | Market;
type BucketFilter = "ALL" | ReportRecord["performance_bucket"];
type SchoolFilter = "ALL" | School;

export const bucketFilters: BucketFilter[] = ["ALL", "Tenbagger", "Multibagger", "Double", "Winner", "Positive", "Drawdown", "Wrecked", "No quote"];

export function useReportDashboardState(records: ReportRecord[]) {
  const [market, setMarket] = useState<MarketFilter>("ALL");
  const [bucket, setBucket] = useState<BucketFilter>("ALL");
  const [school, setSchool] = useState<SchoolFilter>("ALL");
  const [query, setQuery] = useState("");
  const [selectedName, setSelectedName] = useState<string | null>(null);
  /** 약한 매수·매도 참고 기록 표시 여부 — 기본은 매수 의견만 */
  const [includeReference, setIncludeReference] = useState(false);

  const baseFiltered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return records.filter((report) => {
      // 채점 가능한 modern 시대(2019-07 이후)만 — SMIC 아카이브 시대는 학회·종목 페이지에서 별도 열람
      if (report.era !== "modern") return false;
      if (!report.report_date) return false;
      if (market !== "ALL" && report.market !== market) return false;
      if (bucket !== "ALL" && report.performance_bucket !== bucket) return false;
      if (school !== "ALL" && report.school !== school) return false;
      if (!q) return true;
      return [report.company, report.ticker, report.source_name, report.rating]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(q);
    });
  }, [records, bucket, market, school, query]);

  /** 현재 필터 안의 참고 기록 수 — 토글 칩 라벨용 */
  const referenceCounts = useMemo(
    () => ({
      softBuy: baseFiltered.filter((report) => report.rating_class === "soft_buy").length,
      sell: baseFiltered.filter((report) => report.rating_class === "sell").length,
    }),
    [baseFiltered],
  );

  const filtered = useMemo(
    () => (includeReference ? baseFiltered : baseFiltered.filter(isBuy)),
    [baseFiltered, includeReference],
  );

  const sorted = useMemo(
    () => [...filtered].sort((a, b) => String(a.report_date).localeCompare(String(b.report_date))),
    [filtered],
  );

  // 선택 fallback: 현재 필터에서 현재 수익률이 가장 높은 리포트
  // (과거 chartPoints 정렬과 동일한 기준 — 차트 자체는 제거된 죽은 연산이었음)
  const bestReturn = useMemo(() => {
    const priced = sorted.filter((report) => report.report_date && report.return_latest_pct !== null);
    return (
      [...priced].sort((a, b) => (b.return_latest_pct as number) - (a.return_latest_pct as number))[0]
      ?? sorted[0]
      ?? records[0]
    );
  }, [sorted, records]);
  const selected = sorted.find((report) => report.source_name === selectedName) ?? bestReturn;

  return {
    market,
    setMarket,
    bucket,
    setBucket,
    school,
    setSchool,
    query,
    setQuery,
    includeReference,
    setIncludeReference,
    referenceCounts,
    selected,
    setSelectedName,
    sorted,
  };
}

export type { BucketFilter, MarketFilter, SchoolFilter };
