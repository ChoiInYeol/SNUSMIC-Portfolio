import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { DailyPaper } from "@/components/today/daily-paper";
import { loadPaperIssue } from "@/components/today/paper-data";
import { loadSnapshotIndex } from "@/lib/api-v2-server";
import { dateLabel } from "@/lib/report-model";

// One static page per published snapshot; any other date is a 404 at the edge.
export const dynamicParams = false;

export async function generateStaticParams() {
  return (await loadSnapshotIndex()).map((entry) => ({ date: entry.date }));
}

export async function generateMetadata({ params }: { params: Promise<{ date: string }> }): Promise<Metadata> {
  const { date } = await params;
  return {
    title: `판결일보 ${dateLabel(date)} 호 — 판결 아카이브`,
    description: `${dateLabel(date)}에 박제된 신호 스냅샷으로 다시 조판한 지난 호 — 그날까지의 데이터만 사용합니다.`,
  };
}

export default async function BackIssuePage({ params }: { params: Promise<{ date: string }> }) {
  const { date } = await params;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) notFound();
  const issue = await loadPaperIssue(date);
  if (!issue) notFound();
  return <DailyPaper issue={issue} />;
}
