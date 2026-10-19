import { z } from "zod";

// Runtime contracts for public/api/v1 payloads read by the web app.
// Writers: scripts/export_signals_api.py · spec: public/api/v1/openapi.json.

const num = z.number();
const nullableNum = num.nullable();

export const WealthPointSchema = z.object({
  month: num,
  date: z.string(),
  contributed: num,
  strategy_value: num,
  KOSPI_value: num.optional(),
  SP500_value: num.optional(),
  NASDAQ_value: num.optional(),
  AllWeather_value: num.optional(),
});

export const EquityPointSchema = z.object({ date: z.string(), nav: num });
export const YearlyReturnSchema = z.object({ year: num, return_pct: num });

export const StrategyWealthSimSchema = z.object({
  final_strategy_value: num,
  strategy_gain_on_contributed_pct: nullableNum,
  strategy_mdd_pct: num,
  series: z.array(WealthPointSchema),
});

export const TriggerReportSchema = z.object({
  school: z.string(),
  report_date: z.string(),
  source_file: z.string().optional(),
  target_price: nullableNum.optional(),
  stated_upside_pct: nullableNum.optional(),
});

export const TradeSchema = z.object({
  ticker: z.string(),
  market: z.string().optional(),
  display_name: z.string().optional(),
  source: z.string().optional(),
  entry_date: z.string(),
  exit_date: z.string(),
  entry: num,
  exit: num,
  return_pct: num,
  pnl_krw: num.optional(),
  days: num,
  n_clubs: num.optional(),
  exit_reason: z.string().optional(),
  entry_reason: z.string().optional(),
  trigger_schools: z.array(z.string()).optional(),
  trigger_reports: z.array(TriggerReportSchema).optional(),
  trigger_target_prices: z.array(num).optional(),
});

export const OpenPositionSchema = z.object({
  ticker: z.string(),
  market: z.string().optional(),
  display_name: z.string().optional(),
  entry_date: z.string(),
  entry: num,
  last_close: num,
  stop: num.optional(),
  return_pct: num,
  source: z.string().optional(),
  n_clubs: num.optional(),
  extension: nullableNum.optional(),
  entry_reason: z.string().optional(),
});

export const StrategyDetailSchema = z.object({
  schema_version: z.string().optional(),
  strategy_key: z.string(),
  content_hash: z.string().regex(/^[0-9a-f]{64}$/),
  nav: z.array(EquityPointSchema).optional(),
  wealth: StrategyWealthSimSchema.optional(),
  trades: z.array(TradeSchema).optional(),
  open_positions: z.array(OpenPositionSchema).optional(),
  yearly: z.array(YearlyReturnSchema).optional(),
});

export const SignalsPayloadSchema = z.looseObject({
  schema_version: z.string(),
  as_of: z.iso.date(),
  headline_strategy: z.string().min(1),
  open_positions: z.array(z.looseObject({ ticker: z.string() })),
  buy_signals: z.array(z.looseObject({ ticker: z.string() })),
  sell_signals: z.array(z.looseObject({ ticker: z.string() })),
});

export type WealthPoint = z.infer<typeof WealthPointSchema>;
export type EquityPoint = z.infer<typeof EquityPointSchema>;
export type YearlyReturn = z.infer<typeof YearlyReturnSchema>;
export type StrategyWealthSim = z.infer<typeof StrategyWealthSimSchema>;
export type TriggerReport = z.infer<typeof TriggerReportSchema>;
export type Trade = z.infer<typeof TradeSchema>;
export type OpenPosition = z.infer<typeof OpenPositionSchema>;
export type StrategyDetail = z.infer<typeof StrategyDetailSchema>;
