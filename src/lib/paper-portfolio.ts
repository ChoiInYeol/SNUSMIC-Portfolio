import { z } from "zod";
export const PaperPortfolioSchema = z.object({ schema_version: z.literal("1.0"), mode: z.literal("forward_paper"),
  as_of: z.iso.date(), started_at: z.iso.datetime({ offset: true }), strategy: z.string(), method: z.string(),
  status: z.enum(["tracking", "awaiting_next_close"]), initial_capital_krw: z.number().positive(), nav_krw: z.number().positive(),
  cash_krw: z.number(), cash_weight_pct: z.number(), return_pct: z.number(), daily_return_pct: z.number(), fees_krw: z.number().nonnegative(),
  positions: z.array(z.object({ id: z.string(), name: z.string(), quote_date: z.iso.date(), value_krw: z.number().nonnegative(), weight_pct: z.number().nonnegative(), return_pct: z.number().nullable() })),
  target_positions: z.array(z.object({ id: z.string(), name: z.string(), weight_pct: z.number().nonnegative() })),
  history: z.array(z.object({ as_of: z.iso.date(), nav_krw: z.number().positive(), daily_return_pct: z.number(), return_pct: z.number(), rebalanced: z.boolean() })),
}).superRefine((d, ctx) => {
  if (Math.abs(d.cash_weight_pct + d.positions.reduce((a, p) => a + p.weight_pct, 0) - 100) > .01) ctx.addIssue({ code: "custom", message: "Portfolio weights do not reconcile" });
});
