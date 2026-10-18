import { z } from "zod";
import raw from "@/data/report-performance.json";

const MarketSchema = z.enum(["KR", "US"]);
const DirectionSchema = z.enum(["up", "down", "flat"]);
export const BucketSchema = z.enum(["Tenbagger", "Multibagger", "Double", "Winner", "Positive", "Drawdown", "Wrecked", "No quote"]);
const RatingClassSchema = z.enum(["buy", "soft_buy", "sell"]);
const MaturitySchema = z.enum(["fresh", "developing", "seasoned", "veteran"]);
const SchoolSchema = z.enum(["smic", "yig", "star", "kuvic", "ewha", "voera"]);

// 데이터 없는 메타 헬퍼는 report-meta.ts로 분리 — 서버 쪽 기존 import 경로는 그대로 살린다
export { SCHOOL_LABELS, dateLabel, getDisplayName } from "@/lib/report-meta";

const NullableNumber = z.number().finite().nullable();
const NullableString = z.string().nullable();
const EraSchema = z.enum(["modern", "archive"]);

export const ReportRecordSchema = z.object({
  source_file: z.string(),
  source_name: z.string(),
  school: SchoolSchema,
  report_type: z.enum(["company", "sector"]),
  era: EraSchema,
  qa_flags: NullableString.optional().default(null),
  report_date: NullableString,
  authored_date: NullableString.optional().default(null),
  filename_date: NullableString,
  market: MarketSchema.nullable(),
  company: NullableString,
  ticker: NullableString,
  exchange: NullableString,
  rating: NullableString,
  target_price: NullableNumber,
  target_price_raw: NullableString,
  report_current_price: NullableNumber,
  report_current_price_raw: NullableString,
  stated_upside_pct: NullableNumber,
  first_trade_date: NullableString,
  start_close: NullableNumber,
  latest_trade_date: NullableString,
  latest_close: NullableNumber,
  return_latest_pct: NullableNumber,
  direction_latest: DirectionSchema.nullable(),
  return_30d_pct: NullableNumber,
  return_90d_pct: NullableNumber,
  return_180d_pct: NullableNumber,
  return_365d_pct: NullableNumber,
  return_ytd_pct: NullableNumber.optional().default(null),
  benchmark_return_pct: NullableNumber.optional().default(null),
  alpha_latest_pct: NullableNumber.optional().default(null),
  max_high_until_latest: NullableNumber,
  max_high_return_pct: NullableNumber,
  target_hit_until_latest: z.boolean().nullable(),
  first_target_hit_date: NullableString,
  days_to_target: z.number().int().nullable(),
  peak_return_24m_pct: NullableNumber.optional().default(null),
  peak_date_24m: NullableString.optional().default(null),
  bucket_peak: BucketSchema.optional().default("No quote"),
  target_seq: z.number().int().nullable().optional().default(null),
  target_seq_total: z.number().int().nullable().optional().default(null),
  rating_class: RatingClassSchema,
  display_name: NullableString.optional().default(null),
  age_days: z.number().int().nullable().optional().default(null),
  maturity: MaturitySchema.nullable().optional().default(null),
  data_issue: NullableString,
  parse_issue: NullableString,
  performance_bucket: BucketSchema,
  source_md_url: NullableString.optional().default(null),
  source_pdf_url: NullableString.optional().default(null),
});

export const SummaryRecordSchema = z.object({
  group: z.string(),
  reports: z.number().int().nonnegative(),
  priced_reports: z.number().int().nonnegative(),
  with_target: z.number().int().nonnegative(),
  up_latest: z.number().int().nonnegative(),
  down_latest: z.number().int().nonnegative(),
  target_hit: z.number().int().nonnegative(),
  avg_return_latest_pct: NullableNumber,
  median_return_latest_pct: NullableNumber,
});

export const ReportDatasetSchema = z.object({
  generated_at: z.string(),
  as_of: z.string(),
  excluded_sector_count: z.number().int().nonnegative().default(0),
  records: z.array(ReportRecordSchema),
  summary: z.array(SummaryRecordSchema),
});

export type Market = z.infer<typeof MarketSchema>;
export type School = z.infer<typeof SchoolSchema>;
export type Era = z.infer<typeof EraSchema>;
export type ReportRecord = z.infer<typeof ReportRecordSchema>;
export type SummaryRecord = z.infer<typeof SummaryRecordSchema>;
export type ReportDataset = z.infer<typeof ReportDatasetSchema>;
export type RatingClass = z.infer<typeof RatingClassSchema>;
export type Maturity = z.infer<typeof MaturitySchema>;

export type DataQuality = {
  valid: boolean;
  generatedAt: string | null;
  asOf: string | null;
  totalRecords: number;
  validRecords: number;
  validationIssueCount: number;
  dataIssueCount: number;
  parseIssueCount: number;
  missingQuoteCount: number;
  missingTargetCount: number;
  issueSummaries: string[];
};

export type QuarantinedReport = {
  sourceFile: string | null;
  raw: unknown;
  issues: string[];
};

export function validateReportDataset(input: unknown): {
  dataset: ReportDataset;
  quarantine: QuarantinedReport[];
} {
  const envelope = ReportDatasetSchema.omit({ records: true, summary: true })
    .extend({ records: z.array(z.unknown()), summary: z.array(z.unknown()) })
    .parse(input);
  const quarantine: QuarantinedReport[] = [];
  const records: ReportRecord[] = [];
  for (const record of envelope.records) {
    const parsed = ReportRecordSchema.safeParse(record);
    if (parsed.success) {
      records.push(parsed.data);
    } else {
      const source = record && typeof record === "object" && "source_file" in record
        ? record.source_file
        : null;
      quarantine.push({
        sourceFile: typeof source === "string" ? source : null,
        raw: record,
        issues: parsed.error.issues.map(summarizeZodIssue),
      });
    }
  }
  // Published aggregates cannot describe a quarantined subset truthfully.
  const summary = quarantine.length === 0
    ? z.array(SummaryRecordSchema).parse(envelope.summary)
    : [];
  return { dataset: { ...envelope, records, summary }, quarantine };
}

function summarizeZodIssue(issue: z.ZodIssue) {
  const path = issue.path.length ? issue.path.join(".") : "root";
  return `${path}: ${issue.message}`;
}

const parsedDataset = ReportDatasetSchema.safeParse(raw);
const validatedDataset = validateReportDataset(raw);

export const reportDataset = validatedDataset.dataset;
export const reportQuarantine = validatedDataset.quarantine;

export const reportDataQuality: DataQuality = {
  valid: parsedDataset.success,
  generatedAt: reportDataset.generated_at || null,
  asOf: reportDataset.as_of || null,
  totalRecords: Array.isArray((raw as { records?: unknown }).records) ? ((raw as { records: unknown[] }).records.length) : 0,
  validRecords: reportDataset.records.length,
  validationIssueCount: parsedDataset.success ? 0 : parsedDataset.error.issues.length,
  dataIssueCount: reportDataset.records.filter((record) => record.data_issue).length,
  parseIssueCount: reportDataset.records.filter((record) => record.parse_issue).length,
  missingQuoteCount: reportDataset.records.filter((record) => record.latest_close === null || record.start_close === null).length,
  missingTargetCount: reportDataset.records.filter((record) => record.target_price === null).length,
  issueSummaries: parsedDataset.success ? [] : parsedDataset.error.issues.slice(0, 8).map(summarizeZodIssue),
};

