import { z } from "zod";

const n = z.number().finite();
const day = z.iso.date();
export const SotaPortfolioSchema = z.object({
  schema_version: z.literal("1.0"), mode: z.literal("sota_backtest"),
  quantity_basis: z.literal("fractional_model_shares"),
  strategy: z.string().min(1), as_of: day, generated_at: z.iso.datetime({ offset: true }),
  source_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  initial_capital_krw: n.positive(), nav_krw: n.positive(), return_pct: n, simulation_start: day,
  positions: z.array(z.object({
    ticker: z.string().min(1), market: z.enum(["KR", "US"]), name: z.string(), shares: n.positive(),
    entry_date: day, entry_price: n.positive(), current_price: n.positive(), quote_date: day,
    fx: n.positive(), cost_krw: n.positive(), value_krw: n.positive(), weight_pct: n.nonnegative(),
    pnl_krw: n, return_pct: n, stop_level: n.nullable(), dist_to_stop_pct: n.nullable(), exit_pending: z.boolean(),
  })),
  parked_krw: n.nonnegative(), parked_weight_pct: n.nonnegative(), parking_name: z.string(),
  cash_krw: n.nonnegative(), cash_weight_pct: n.nonnegative(),
  regime: z.object({ applies: z.boolean(), state: z.enum(["ON", "OFF"]),
    kospi_close: n.positive(), kospi_ma200: n.positive(), note: z.string() }).nullable(),
  slots: z.object({ max_positions: n.int().positive(), open: n.int().nonnegative(), available: n.int().nonnegative() }),
  buy_candidates: z.array(z.object({ ticker: z.string(), market: z.enum(["KR", "US"]),
    display_name: z.string(), entry_pending: z.literal(true), entry_basis_date: day.nullable(), entry_reason: z.string() })),
  recent_window_start: day, recent_closed_count: n.int().nonnegative(),
  recent_exits: z.array(z.object({ ticker: z.string(), market: z.enum(["KR", "US"]), name: z.string(),
    entry_date: day, exit_date: day, entry_price: n.positive(), exit_price: n.positive(),
    pnl_krw: n, return_pct: n, exit_reason: z.string() })),
}).superRefine((doc, ctx) => {
  const issue = (message: string) => ctx.addIssue({ code: "custom", message });
  const symbols = new Set(doc.positions.map((p) => p.ticker));
  if (symbols.size !== doc.positions.length || doc.slots.open !== symbols.size ||
      doc.slots.available !== doc.slots.max_positions - symbols.size) issue("Holding/slot counts differ");
  for (const p of doc.positions) {
    if (p.quote_date > doc.as_of || p.entry_date > doc.as_of ||
        Math.abs(p.shares * p.current_price * p.fx - p.value_krw) > .02 ||
        Math.abs(p.value_krw - p.cost_krw - p.pnl_krw) > .02 ||
        Math.abs(p.weight_pct - p.value_krw / doc.nav_krw * 100) > .001 ||
        Math.abs(p.return_pct - (p.value_krw / p.cost_krw - 1) * 100) > .001) issue("Holding amounts/dates differ");
  }
  if (Math.abs(doc.positions.reduce((sum, p) => sum + p.value_krw, 0) + doc.parked_krw + doc.cash_krw - doc.nav_krw) > .05 ||
      Math.abs(doc.parked_weight_pct - doc.parked_krw / doc.nav_krw * 100) > .001 ||
      Math.abs(doc.cash_weight_pct - doc.cash_krw / doc.nav_krw * 100) > .001) issue("Allocation does not reconcile");
  if (doc.regime && doc.regime.state !== (doc.regime.kospi_close >= doc.regime.kospi_ma200 ? "ON" : "OFF")) issue("Regime differs");
  if (doc.buy_candidates.some((b) => symbols.has(b.ticker))) issue("Buy candidate already held");
  if (doc.recent_exits.some((t) => t.exit_date < doc.recent_window_start || t.exit_date > doc.as_of)) issue("Exit date outside window");
});

export type SotaPortfolio = z.infer<typeof SotaPortfolioSchema>;
