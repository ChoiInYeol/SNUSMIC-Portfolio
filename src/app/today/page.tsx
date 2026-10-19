import type { Metadata } from "next";
import { DailyPaper } from "@/components/today/daily-paper";
import { loadPaperIssue } from "@/components/today/paper-data";

export const metadata: Metadata = {
  title: "오늘의 판결 신문 — 판결 아카이브",
  description: "매일 데이터에서 자동 조판되는 1면 — 전략 신호·시세표·부고·신착 리포트",
};

export default async function TodayPage() {
  const issue = await loadPaperIssue(null);
  // The latest snapshot always exists in a valid build; a missing one is a pipeline failure, not an empty state.
  if (!issue) throw new Error("No latest signal snapshot in /api/v2/signals");
  return <DailyPaper issue={issue} />;
}
