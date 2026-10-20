import { z } from "zod";

const Metrics = z.object({ cagr_pct: z.number(), mdd_pct: z.number(), sharpe: z.number() });
export const StrategyRobustnessSchema = z.object({
  schema_version: z.literal("1.0"), as_of: z.iso.date(), strategy: z.literal("W_allweather_chandelier"),
  engine_version: z.literal("parking-open-v2"), reoptimized: z.literal(false), method: z.string(),
  input_fingerprint: z.string().regex(/^[a-f0-9]{64}$/),
  policy: z.object({ atr_period: z.number().int().positive(), atr_mult: z.number().positive(), max_positions: z.number().int().positive() }),
  top5_positive_pnl_share_pct: z.number().min(0).max(100).nullable(),
  ranking: z.array(z.object({ ticker: z.string(), name: z.string(), pnl_krw: z.number() })),
  scenarios: z.array(z.object({
    id: z.enum(["baseline", "exclude_top_1", "exclude_top_3", "exclude_top_5", "delay_1", "delay_3", "delay_5"]),
    kind: z.enum(["baseline", "winner_exclusion", "entry_delay"]), excluded_tickers: z.array(z.string()),
    extra_entry_sessions: z.number().int().nonnegative(), metrics: Metrics, fees_krw: z.number().nonnegative(),
  })).length(7),
}).superRefine((d, ctx) => {
  if (new Set(d.scenarios.map(s => s.id)).size !== 7) ctx.addIssue({ code: "custom", message: "Duplicate sensitivity scenarios" });
});
