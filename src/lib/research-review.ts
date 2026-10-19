import { z } from "zod";

export const reviewFields = ["ticker", "company", "report_date", "rating", "target_price", "report_current_price", "stated_upside_pct"] as const;
export const reviewLabels: Record<typeof reviewFields[number], string> = {
  ticker: "종목코드", company: "회사명", report_date: "발간일", rating: "투자의견",
  target_price: "목표가", report_current_price: "발간가", stated_upside_pct: "상승여력 (%)",
};
export const numericFields = new Set<string>(["target_price", "report_current_price", "stated_upside_pct"]);
const values = z.object({ ticker: z.string().nullable(), company: z.string().nullable(), report_date: z.string().nullable(),
  rating: z.string().nullable(), target_price: z.number().finite().nullable(), report_current_price: z.number().finite().nullable(), stated_upside_pct: z.number().finite().nullable() });
const ReviewBaseSchema = z.object({
  id: z.string(), school: z.string(), values, transcript_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  reasons: z.array(z.string()), pdf_url: z.string().url().regex(/^https?:\/\//).nullable(), markdown_url: z.string().url().regex(/^https?:\/\//).nullable(),
  correction: z.record(z.string(), z.unknown()),
});
export const ReviewSummarySchema = ReviewBaseSchema.extend({ detail_url: z.string().regex(/^\/research\/reviews\/[a-f0-9]{64}\.json$/) });
export const ReviewDetailSchema = ReviewBaseSchema.extend({ schema_version: z.literal("1.0"), excerpt: z.string(),
  matches: z.array(z.object({ field: z.enum(reviewFields), line: z.number().int().positive(), quote: z.string() })),
});
export const ReviewDocumentSchema = z.object({ schema_version: z.literal("1.0"), as_of: z.iso.date(), records: z.array(ReviewSummarySchema) });
export type ReviewSummary = z.infer<typeof ReviewSummarySchema>;
export type ReviewRow = z.infer<typeof ReviewDetailSchema>;
