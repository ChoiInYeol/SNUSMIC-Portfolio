import raw from "@/data/report-performance.json";
import { ReportDatasetSchema, validateReportDataset, summarizeZodIssue, type DataQuality } from "@/lib/report-contract";
export * from "@/lib/report-contract";

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

