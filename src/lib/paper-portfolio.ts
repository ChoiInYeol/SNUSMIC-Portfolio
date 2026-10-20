import { z } from "zod";
export const PaperPortfolioSchema = z.object({ schema_version: z.literal("2.0"), mode: z.literal("forward_paper"),
  engine_version: z.literal("parking-open-v2"), source_as_of: z.iso.date(),
  policy: z.object({ strategy: z.literal("W_allweather_chandelier"), engine_version: z.literal("parking-open-v2"),
    atr_period: z.union([z.literal(20), z.literal(42), z.literal(63)]), atr_mult: z.number().positive(), max_positions: z.number().int().positive(),
    position_weight: z.number().positive(), cost_per_side: z.number().nonnegative(), parking_cost: z.number().nonnegative() }),
  fills: z.array(z.object({ date: z.iso.date(), ticker: z.string(), market: z.enum(["KR", "US"]), side: z.enum(["buy", "sell"]),
    price: z.number().positive(), fx: z.number().positive(), shares: z.number().positive(), gross_krw: z.number().nonnegative(),
    cash_flow_krw: z.number(), fees_krw: z.number().nonnegative(), reason: z.string() })),
  data_issues: z.array(z.string()),
  as_of: z.iso.date(), started_at: z.iso.datetime({ offset: true }), strategy: z.string(), method: z.string(),
  status: z.enum(["tracking", "awaiting_next_open"]), initial_capital_krw: z.number().positive(), nav_krw: z.number().positive(),
  cash_krw: z.number(), cash_weight_pct: z.number(), return_pct: z.number(), daily_return_pct: z.number(), fees_krw: z.number().nonnegative(),
  positions: z.array(z.object({ id: z.string(), name: z.string(), quote_date: z.iso.date(), value_krw: z.number().nonnegative(), weight_pct: z.number().nonnegative(), return_pct: z.number().nullable() })),
  target_positions: z.array(z.object({ id: z.string(), name: z.string(), weight_pct: z.number().nonnegative() })),
  history: z.array(z.object({ as_of: z.iso.date(), nav_krw: z.number().positive(), daily_return_pct: z.number(), return_pct: z.number(), rebalanced: z.boolean() })),
}).superRefine((d, ctx) => {
  if (Math.abs(d.cash_weight_pct + d.positions.reduce((a, p) => a + p.weight_pct, 0) - 100) > .01) ctx.addIssue({ code: "custom", message: "Portfolio weights do not reconcile" });
});
