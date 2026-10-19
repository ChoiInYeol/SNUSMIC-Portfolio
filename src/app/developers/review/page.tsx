import type { Metadata } from "next";
import { SiteHeader } from "@/components/site-header";
import { ReviewQueue } from "@/components/research/review-queue";

export const metadata: Metadata = { title: "추출값 검토 — 판결 아카이브" };
export default function ReviewPage() {
  return <div className="mx-auto max-w-7xl px-4 py-8 sm:px-8"><SiteHeader eyebrow="EXTRACTION REVIEW" /><main className="pt-8"><ReviewQueue /></main></div>;
}
