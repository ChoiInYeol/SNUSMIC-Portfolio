import type { Metadata } from "next";
import { ArchitectureLoader } from "@/components/architecture/loader";
import { SiteHeader } from "@/components/site-header";
import "@xyflow/react/dist/style.css";
import "@/components/architecture/graph.css";

export const metadata: Metadata = { title: "데이터 아키텍처 — 판결 아카이브", description: "Bronze · Silver · Gold 스키마, 외래 키와 ETL 데이터 계보를 탐색합니다." };

export default function ArchitecturePage() {
  return <div className="architecture-page min-h-screen"><main className="mx-auto max-w-[1800px] px-4 py-6 sm:px-8"><SiteHeader eyebrow="Data Architecture" /><div className="mt-6"><ArchitectureLoader /></div></main></div>;
}
