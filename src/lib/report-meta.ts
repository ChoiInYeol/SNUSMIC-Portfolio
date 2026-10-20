/**
 * 데이터 없는 메타 헬퍼 — report-model.ts에서 분리.
 * report-model.ts는 2.6MB report-performance.json을 모듈 평가 시점에 import하므로,
 * dateLabel 하나가 필요한 클라이언트 컴포넌트(장부·서가·차트)가 그 JSON 전체를
 * 번들로 끌고 오지 않도록 순수 함수·라벨만 여기로 옮겼다.
 * 타입 import는 컴파일 시점에 지워져 순환·번들 비용이 없다.
 */
import type { ReportRecord, School } from "@/lib/report-model";

export type { ReportRecord, School };

export const SCHOOL_LABELS: Record<School, string> = {
  smic: "서울대 SMIC",
  yig: "연세대 YIG",
  star: "성균관대 STAR",
  kuvic: "고려대 KUVIC",
  ewha: "이화여대 EIA",
  voera: "홍익대 Voera",
};

export function getDisplayName(report: ReportRecord) {
  return report.display_name || report.company || report.ticker || report.source_name.replace(/\.md$/, "");
}

export function dateLabel(value: string | null) {
  if (!value) return "날짜 없음";
  return value.replaceAll("-", ".");
}

/**
 * Visible in a view dated `asOf`: published by then AND already collected by then. Unknown collection = hidden.
 * Client-safe; report-first-seen.ts `knownBy` (server, reads the Gold file) applies the same rule.
 */
export function seenBy(reportDate: string | null, firstSeen: string | null | undefined, asOf: string): boolean {
  return reportDate !== null && reportDate <= asOf && firstSeen !== undefined && firstSeen !== null && firstSeen <= asOf;
}
