import { promises as fs } from "node:fs";
import path from "node:path";
import { z } from "zod";

// When each report first entered the archive (gold artifact src/data/report-first-seen.json, keyed by the
// records' source_file). A report's report_date can precede its collection by months, so back issues use this
// to show only what the archive actually had that day. Server Components only; a missing or invalid file fails
// the build rather than silently showing everything.

export const ReportFirstSeenSchema = z.object({
  schema_version: z.literal("1.0"),
  generated_at: z.iso.datetime({ offset: true }),
  method: z.string(),
  first_seen: z.record(z.string(), z.iso.date()),
});
export type ReportFirstSeen = z.infer<typeof ReportFirstSeenSchema>;

let cached: Promise<ReportFirstSeen["first_seen"]> | null = null;

export function loadReportFirstSeen(): Promise<ReportFirstSeen["first_seen"]> {
  cached ??= fs
    .readFile(path.join(process.cwd(), "src", "data", "report-first-seen.json"), "utf-8")
    .then((raw) => ReportFirstSeenSchema.parse(JSON.parse(raw)).first_seen);
  return cached;
}

/** Visible in a back issue dated `asOf`: published by then AND already in the archive by then. Unknown = hidden. */
export function knownBy(
  record: { source_file: string; report_date: string | null },
  asOf: string,
  firstSeen: ReportFirstSeen["first_seen"],
): boolean {
  const seen = firstSeen[record.source_file];
  return record.report_date !== null && record.report_date <= asOf && seen !== undefined && seen <= asOf;
}
