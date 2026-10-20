import type { Metadata } from "next";
import { Suspense } from "react";
import { SiteHeader } from "@/components/site-header";
import { ReportSearch, ReportSearchSkeleton } from "@/components/research/report-search";

export const metadata: Metadata = {
  title: "보고서 본문 검색 — 판결 아카이브",
  description: "학회 리포트 본문에서 주제 → 관련 종목 → 근거 문단 → 주가 시점으로 이어 찾습니다.",
};

// Static shell; the query lives in ?q= and the index is fetched on demand from /research/fulltext/.
export default function SearchPage() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-8">
      <SiteHeader eyebrow="FULL-TEXT SEARCH" />
      <main className="pt-6">
        <Suspense fallback={<ReportSearchSkeleton />}>
          <ReportSearch />
        </Suspense>
      </main>
    </div>
  );
}
