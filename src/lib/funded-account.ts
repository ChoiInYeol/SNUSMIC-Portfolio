import { z } from "zod";

// Boundary contract for public/research/funded-account.json (scripts/funded_account.py, schema 1.0).
// Plan = frozen next-open decision from a close; fill = what the real next open did. Never merge them.
const Market = z.enum(["KR", "US"]);
const Krw = z.number();
const Instrument = z.object({ symbol: z.string(), venue: z.string(), currency: z.enum(["KRW", "USD"]), lot: z.literal(1) });

export const FundedOrderSchema = z.object({
  order_id: z.string(), seq: z.number().int().positive(), side: z.enum(["buy", "sell"]), ticker: z.string(), market: Market,
  name: z.string(), instrument: Instrument, signal_id: z.string().nullable(),
  reason_code: z.enum(["new_report", "bootstrap_published_holding", "chandelier_stop"]), reason: z.string(),
  budget_krw: Krw.positive().nullable(), est_price: z.number().positive(), est_fx: z.number().positive(),
  est_shares: z.number().int().positive(), est_gross_krw: Krw.nonnegative(), est_cost_krw: Krw.nonnegative(),
  est_cash_flow_krw: Krw, current_weight_pct: z.number().nonnegative(), planned_weight_pct: z.number().nonnegative(),
  atr_prior: z.number().nullable(), conditions: z.array(z.string()),
});

export const FundedFillSchema = z.object({
  fill_id: z.string(), plan_id: z.string(), order_id: z.string(), trading_day: z.iso.date(), market: Market, ticker: z.string(),
  side: z.enum(["buy", "sell"]), status: z.enum(["filled", "unfilled"]),
  reason_code: z.enum(["no_position", "halt_or_missing_open", "missing_fx", "already_held", "slots_full",
    "insufficient_cash", "below_one_share_at_open"]).nullable(),
  price: z.number().positive().nullable(), fx: z.number().positive().nullable(), shares: z.number().int().nonnegative(),
  gross_krw: Krw.nonnegative(), fees_krw: Krw.nonnegative(), cash_flow_krw: Krw, realized_pnl_krw: Krw.optional(),
});

const Excluded = z.object({ ticker: z.string(), name: z.string(), signal_id: z.string(), reason_code: z.string(),
  reason: z.string(), final: z.boolean() });

export const FundedPlanSchema = z.object({
  schema_version: z.literal("1.0"), plan_id: z.string(), account_id: z.string(), policy_version: z.string(), market: Market,
  trading_day: z.iso.date(), open_at: z.iso.datetime({ offset: true }), revision: z.number().int().positive(),
  status: z.enum(["active", "cancelled"]), supersedes: z.string().nullable(), revision_reason: z.string().nullable(),
  frozen_at: z.iso.datetime({ offset: true }), basis_session: z.iso.date(), basis_observed_at: z.iso.datetime({ offset: true }),
  skipped_closures: z.array(z.iso.date()), quantity_basis: z.literal("estimate_from_close"),
  account_snapshot: z.object({
    nav_est_krw: Krw, cash_krw: Krw, incoming_contributions_krw: Krw.nonnegative(), reserved_for_earlier_plans_krw: Krw,
    est_sell_proceeds_krw: Krw.nonnegative(), cash_after_plan_est_krw: Krw, target_budget_krw: Krw.nonnegative(),
    position_count: z.number().int().nonnegative(), slots_free: z.number().int(), principal_krw: Krw.positive(),
    marks: z.object({ KR: z.iso.date().nullable(), US: z.iso.date().nullable() }),
  }),
  contributions: z.array(z.object({ installment: z.number().int().positive(), amount_krw: Krw.positive(),
    scheduled_date: z.iso.date(), effective_at: z.iso.datetime({ offset: true }), label: z.string() })),
  orders: z.array(FundedOrderSchema), excluded: z.array(Excluded), no_trade_reason: z.string().nullable(),
  nav_timing_note: z.string(),
});

export const FundedAccountSchema = z.object({
  schema_version: z.literal("1.0"), account_id: z.literal("funded-w50-v1"), policy_version: z.string(),
  mode: z.literal("forward_paper_funded"), strategy: z.string(), started_at: z.iso.datetime({ offset: true }),
  epoch_kst_date: z.iso.date(), method: z.string(), data_issues: z.array(z.string()),
  config: z.object({
    initial_capital_krw: Krw.positive(), position_weight: z.number().positive(), max_positions: z.number().int().positive(),
    atr_period: z.number().int(), atr_mult: z.number().positive(), cost_per_side: z.number().nonnegative(),
    min_fill_fraction: z.number().positive(), missing_open_retry_sessions: z.number().int().positive(), cash_policy: z.string(),
    contribution: z.object({ base_krw: Krw.positive(), step_krw: Krw.nonnegative(), step_every_installments: z.number().int().positive() }),
    // The UI must show `label` next to the contribution day. Status and label are tied: a proposal is never shown as confirmed.
    contribution_day: z.object({ rule: z.string(), day_of_month: z.number().int().min(1).max(28).nullable(),
      first_installment: z.string(), holiday: z.string(), description: z.string() }).and(z.discriminatedUnion("status", [
      z.object({ status: z.literal("proposed_unconfirmed"), label: z.literal("제안값(미확정)") }),
      z.object({ status: z.literal("user_confirmed"), label: z.literal("사용자 확정") }),
    ])),
  }).loose(),
  marks: z.object({ KR: z.iso.date().nullable(), US: z.iso.date().nullable() }),
  performance: z.object({
    initial_capital_krw: Krw.positive(), contributions_krw: Krw.nonnegative(), installments_applied: z.number().int().nonnegative(),
    principal_krw: Krw.positive(), nav_krw: Krw.positive(), cash_krw: Krw, cash_weight_pct: z.number(),
    investment_pnl_krw: Krw, realized_pnl_krw: Krw, fees_krw: Krw.nonnegative(), twr_pct: z.number(), xirr_pct: z.number().nullable(),
  }),
  positions: z.array(z.object({
    id: z.string(), ticker: z.string(), market: Market, name: z.string(), instrument: Instrument, shares: z.number().int().positive(),
    last_close: z.number().positive(), fx: z.number().positive(), quote_date: z.iso.date(), value_krw: Krw.nonnegative(),
    weight_pct: z.number().nonnegative(), cost_krw: Krw.positive(), unrealized_pnl_krw: Krw, entry_date: z.iso.date(),
    entry_price: z.number().positive(), stop: z.number(), halted: z.boolean(), pending_exit: z.boolean(),
  })),
  contributions: z.array(z.object({ installment: z.number().int().positive(), amount_krw: Krw.positive(), scheduled_date: z.iso.date(),
    effective_at: z.iso.datetime({ offset: true }), recorded_at: z.iso.datetime({ offset: true }), late: z.boolean() })),
  // Recorded but not yet landed (e.g. tomorrow 08:30 KST): shown as scheduled, never counted in principal/NAV.
  scheduled_contributions: z.array(z.object({ installment: z.number().int().positive(), amount_krw: Krw.positive(),
    scheduled_date: z.iso.date(), effective_at: z.iso.datetime({ offset: true }), label: z.enum(["제안값(미확정)", "사용자 확정"]) })),
  recorded_contributions: z.number().int().nonnegative(), as_of: z.iso.datetime({ offset: true }),
  next_contribution: z.object({ installment: z.number().int().positive(), amount_krw: Krw.positive(),
    scheduled_date: z.iso.date().nullable(), label: z.enum(["제안값(미확정)", "사용자 확정"]) }),
  latest_plans: z.object({
    KR: FundedPlanSchema.extend({ outcome: z.enum(["awaiting_open", "executed", "cancelled"]), fills: z.array(FundedFillSchema) }).nullable(),
    US: FundedPlanSchema.extend({ outcome: z.enum(["awaiting_open", "executed", "cancelled"]), fills: z.array(FundedFillSchema) }).nullable(),
  }),
  plans: z.array(z.object({ plan_id: z.string(), market: Market, trading_day: z.iso.date(), revision: z.number().int().positive(),
    status: z.enum(["active", "cancelled", "superseded"]), supersedes: z.string().nullable(), frozen_at: z.iso.datetime({ offset: true }),
    basis_session: z.iso.date(), orders: z.number().int().nonnegative(),
    outcome: z.enum(["awaiting_open", "executed", "cancelled", "superseded"]) })),
  fills: z.array(FundedFillSchema),
  signals: z.array(z.object({ id: z.string(), ticker: z.string(), market: Market, name: z.string(), report_date: z.iso.date(),
    observed_at: z.iso.datetime({ offset: true }), status: z.enum(["queued", "filled", "dropped", "expired"]), outcome: z.string().nullable() })),
  history: z.array(z.object({ at: z.iso.datetime({ offset: true }), market: Market.nullable(), session: z.iso.date().nullable(),
    nav_krw: Krw.positive(), cash_krw: Krw, principal_krw: Krw.positive(), investment_pnl_krw: Krw, twr_pct: z.number() })),
  excluded_instruments: z.array(z.object({ ticker: z.string(), reason_code: z.literal("not_orderable"), reason: z.string() })),
}).superRefine((d, ctx) => {
  const p = d.performance;
  // Contributions are principal, never return: principal = initial + contributions, P&L = NAV − principal.
  if (Math.abs(p.initial_capital_krw + d.contributions.reduce((a, c) => a + c.amount_krw, 0) - p.principal_krw) > 0.01)
    ctx.addIssue({ code: "custom", message: "Principal does not reconcile with contributions" });
  if (Math.abs(p.nav_krw - p.principal_krw - p.investment_pnl_krw) > 0.02)
    ctx.addIssue({ code: "custom", message: "Investment P&L must equal NAV minus principal" });
  if (Math.abs(p.cash_krw + d.positions.reduce((a, x) => a + x.value_krw, 0) - p.nav_krw) > 1)
    ctx.addIssue({ code: "custom", message: "NAV does not reconcile with cash and positions" });
  for (const plan of [d.latest_plans.KR, d.latest_plans.US])
    if (plan?.orders.some((o) => o.ticker.startsWith("INDEX:")))
      ctx.addIssue({ code: "custom", message: "Index proxies are not orderable" });
});

export type FundedAccount = z.infer<typeof FundedAccountSchema>;
export type FundedPlan = NonNullable<FundedAccount["latest_plans"]["KR"]>;
export type FundedOrder = z.infer<typeof FundedOrderSchema>;
export type FundedFill = z.infer<typeof FundedFillSchema>;

// Shown while the account has no genesis (not started). Mirrors scripts/funded_account.py CONFIG — the contribution
// day stays a proposal until the operator's `init` freezes it (tests/funded-account.test.cjs keeps both in step).
export const FUNDED_PROPOSAL = {
  account_id: "funded-w50-v1",
  initial_capital_krw: 50_000_000,
  contribution: { base_krw: 2_000_000, step_krw: 1_000_000, step_every_installments: 24 },
  contribution_day: {
    label: "제안값(미확정)",
    description: "개시 다음 달부터 매월 첫 KRX 거래일 시가 30분 전(08:30 KST) 입금, 휴장일은 다음 거래일로 순연",
  },
  position_weight: 0.05,
  max_positions: 30,
  cost_per_side: 0.003,
} as const;

/** Initial capital plus the first `installments` contributions — a sum check, not a return forecast. */
export function principalAfter(installments: number, c: { initial_capital_krw: number; contribution: { base_krw: number; step_krw: number; step_every_installments: number } } = FUNDED_PROPOSAL): number {
  let total = c.initial_capital_krw;
  for (let n = 1; n <= installments; n++)
    total += c.contribution.base_krw + c.contribution.step_krw * Math.floor((n - 1) / c.contribution.step_every_installments);
  return total;
}
