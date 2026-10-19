import { z } from "zod";
import { ReportDatasetSchema, type ReportDataset } from "@/lib/report-contract";

const PackedSchema = z.object({ schema_version: z.literal("1.0"), columns: z.array(z.string()), rows: z.array(z.array(z.unknown())), meta: ReportDatasetSchema.omit({ records: true }) });
export type DashboardTransport = z.infer<typeof PackedSchema>;

/** Keys occur once per payload; no raw dataset import in the client graph. */
export function packDashboard(dataset: ReportDataset): DashboardTransport {
  const { records, ...meta } = dataset;
  const modern = records.filter(r => r.era === "modern");
  const columns = Object.keys(ReportDatasetSchema.shape.records.element.shape);
  return { schema_version: "1.0", columns, rows: modern.map(r => columns.map(k => Reflect.get(r, k))), meta };
}
export function unpackDashboard(input: DashboardTransport): ReportDataset {
  const doc = PackedSchema.parse(input);
  if (new Set(doc.columns).size !== doc.columns.length || doc.rows.some(r => r.length !== doc.columns.length)) throw new Error("Invalid dashboard transport columns");
  return ReportDatasetSchema.parse({ ...doc.meta, records: doc.rows.map(row => Object.fromEntries(doc.columns.map((k, i) => [k, row[i]]))) });
}
