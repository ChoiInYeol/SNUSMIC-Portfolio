import { z } from "zod";
import { StrategyDetailSchema } from "@/lib/api-schemas";

// Runtime contracts for public/api/v2 documents read by the web app.
// Writers: scripts/export_api_v2.py, scripts/export_stock_charts.py · spec: public/api/v2/openapi.json.
// Mirrors components/schemas there; unknown fields are stripped (the Python contract test rejects them).

const num = z.number();
const int = z.number().int();
const nullableNum = num.nullable();
const date = z.iso.date();
const nullableDate = date.nullable();

export const HrefSchema = z.string().regex(/^\/api\/v2(\/\S*)?$/);
export const MarketSchema = z.enum(["KR", "US"]);
export const StrategyIdSchema = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_+.-]*$/);
export const InstrumentIdSchema = z.string().regex(/^(kr|us)-[a-z0-9.-]+$/);
export const ClubIdSchema = z.enum(["smic", "yig", "star", "kuvic", "ewha", "voera"]);

export const MetaSchema = z.object({
  generated_at: z.iso.datetime({ offset: true }),
  as_of: date.optional(),
  count: int.nonnegative().optional(),
  disclaimer: z.string(),
  disclaimer_ko: z.string(),
});

const links = <const K extends string, const O extends string = never>(required: readonly K[], optional: readonly O[] = []) =>
  z.object({
    ...(Object.fromEntries(required.map((k) => [k, HrefSchema])) as Record<K, typeof HrefSchema>),
    ...(Object.fromEntries(optional.map((k) => [k, HrefSchema.optional()])) as Record<O, z.ZodOptional<typeof HrefSchema>>),
  });

/** `{schema_version: "2.0", links, meta, data}` — the uniform v2 envelope. */
export function documentSchema<L extends z.ZodType, D extends z.ZodType>(linksSchema: L, data: D) {
  return z.object({ schema_version: z.literal("2.0"), links: linksSchema, meta: MetaSchema, data });
}

const ItemLinksSchema = z.object({ instrument: HrefSchema.optional() });

// ── signals ────────────────────────────────────────────────────────────────

export const TriggerReportSchema = z.object({
  school: z.string(),
  report_date: date,
  source_file: z.string().optional(),
  target_price: nullableNum.optional(),
  stated_upside_pct: nullableNum.optional(),
});

export const RegimeSchema = z.object({
  kospi_above_200ma: z.boolean(),
  state: z.enum(["ON", "OFF"]),
  kospi_close: nullableNum,
  kospi_ma200: nullableNum,
  parking: z.enum(["allweather", "kospi", "cash"]),
  applies: z.boolean(),
});

export const SignalCountsSchema = z.object({ open_positions: int, buy_signals: int, sell_signals: int });

export const PositionSchema = z.object({
  ticker: z.string(),
  market: MarketSchema,
  name: z.string(),
  entry_date: date,
  entry_price: nullableNum,
  current_price: nullableNum,
  stop_level: nullableNum,
  unrealized_pct: nullableNum,
  days_held: int.nullable(),
  entry_reason: z.string(),
  trigger_reports: z.array(TriggerReportSchema),
  links: ItemLinksSchema.optional(),
});

export const BuySignalSchema = z.object({
  ticker: z.string(),
  market: MarketSchema,
  name: z.string(),
  signal_date: nullableDate,
  entry_basis_price: nullableNum,
  entry_reason: z.string(),
  trigger_reports: z.array(TriggerReportSchema),
  links: ItemLinksSchema.optional(),
});

export const SellSignalSchema = z.object({
  ticker: z.string(),
  market: MarketSchema,
  name: z.string(),
  reason: z.enum(["approaching_stop", "stop_hit"]),
  stop_level: nullableNum,
  dist_to_stop_pct: nullableNum,
  links: ItemLinksSchema.optional(),
});

export const SignalSnapshotSchema = z.object({
  as_of: date,
  headline_strategy: StrategyIdSchema,
  regime: RegimeSchema,
  slots: z.object({ max_positions: int, open: int, available: int }),
  counts: SignalCountsSchema,
  open_positions: z.array(PositionSchema),
  buy_signals: z.array(BuySignalSchema),
  sell_signals: z.array(SellSignalSchema),
});

export const SignalSnapshotDocumentSchema = documentSchema(links(["self", "collection"], ["snapshot"]), SignalSnapshotSchema);
export const SignalIndexDocumentSchema = documentSchema(
  links(["self", "latest"]),
  z.array(z.object({ date, headline_strategy: StrategyIdSchema, counts: SignalCountsSchema, links: links(["self"]) })),
);

// ── strategies ─────────────────────────────────────────────────────────────

export const PeriodMetricsSchema = z.object({
  start: date,
  end: date,
  total_return_pct: nullableNum,
  cagr_pct: nullableNum,
  sharpe: nullableNum,
  mdd_pct: nullableNum,
});

const WalkForwardSchema = z.object({
  n_windows: int,
  positive_pct: nullableNum,
  beat_kospi_pct: nullableNum,
  median_sharpe: nullableNum,
  worst_sharpe: nullableNum,
  worst_window_return_pct: nullableNum,
});

export const StrategySchema = z.object({
  id: StrategyIdSchema,
  label: z.string(),
  is_headline: z.boolean(),
  is_curated: z.boolean(),
  verdict: z.string().nullable(),
  verdict_reason: z.string().nullable(),
  trade_count: int,
  metrics: PeriodMetricsSchema.extend({ win_rate_pct: nullableNum, avg_hold_days: nullableNum }),
  in_sample: PeriodMetricsSchema,
  out_of_sample: PeriodMetricsSchema,
  best_trade: z.object({ ticker: z.string().nullable(), name: z.string().nullable(), return_pct: nullableNum }),
  top_decile_pnl_share_pct: nullableNum,
  benchmarks: z.object({
    kospi_dca_ratio: nullableNum,
    kospi_dca_beats: z.boolean().nullable(),
    aw_dca_ratio: nullableNum,
    aw_dca_beats: z.boolean().nullable(),
  }),
  dsr: z
    .object({ psr: nullableNum, dsr: nullableNum, sr0_annualized: nullableNum, n_trials: int, significant_after_deflation: z.boolean() })
    .nullable(),
  walkforward: WalkForwardSchema.nullable(),
  walkforward_oos: WalkForwardSchema.nullable(),
});

export const StrategyCollectionDocumentSchema = documentSchema(
  links(["self"]),
  z.array(StrategySchema.extend({ links: links(["self"]) })),
);
export const StrategyDocumentSchema = documentSchema(links(["self", "collection", "trades", "current_revision"]), StrategySchema);

export const TradeSchema = z.object({
  ticker: z.string(),
  market: MarketSchema,
  name: z.string(),
  entry_date: date,
  exit_date: date,
  entry_price: nullableNum,
  exit_price: nullableNum,
  return_pct: nullableNum,
  days: int.nullable(),
  exit_reason: z.string(),
  entry_reason: z.string(),
  trigger_reports: z.array(TriggerReportSchema),
  links: ItemLinksSchema.optional(),
});
export const StrategyTradesDocumentSchema = documentSchema(links(["self", "strategy"]), z.array(TradeSchema));

/** Content-addressed revision: no envelope, bytes shared with the v1 strategy-details asset. */
export const StrategyRevisionSchema = StrategyDetailSchema.extend({
  schema_version: z.literal("1.0"),
  strategy_key: StrategyIdSchema,
});

// ── track record ───────────────────────────────────────────────────────────

export const ForwardEntrySchema = z.object({
  signal_date: date,
  ticker: z.string(),
  market: MarketSchema,
  name: z.string(),
  basis_price: nullableNum,
  entry_date: nullableDate,
  entry_price: nullableNum,
  current_price: nullableNum,
  return_pct: nullableNum,
  peak_pct: nullableNum,
  days: int,
  status: z.enum(["tracking", "pending", "unavailable"]),
  trigger_schools: z.array(z.string()),
  headline_strategy: z.string(),
  links: ItemLinksSchema.optional(),
});

export const TrackRecordDocumentSchema = documentSchema(
  links(["self", "signals"]),
  z.object({
    method: z.string(),
    summary: z.object({
      n_signals: int,
      n_tracking: int,
      n_unavailable: int.optional(),
      n_pending: int.optional(),
      coverage_pct: nullableNum.optional(),
      avg_return_pct: nullableNum.optional(),
      win_rate_pct: nullableNum.optional(),
      best_pct: nullableNum.optional(),
      worst_pct: nullableNum.optional(),
      first_snapshot: date,
      last_snapshot: date,
      n_snapshots: int,
    }),
    entries: z.array(ForwardEntrySchema),
    headline_by_date: z.record(z.string(), z.string()),
    provenance: z.record(z.string(), z.string()).optional(),
  }),
);

// ── reports, instruments, clubs ────────────────────────────────────────────

export const ReportSchema = z.object({
  school: ClubIdSchema,
  report_type: z.string(),
  era: z.enum(["modern", "archive"]),
  report_date: nullableDate,
  authored_date: z.string().nullable(), // as transcribed, not validated
  market: MarketSchema,
  ticker: z.string(),
  exchange: z.string().nullable(),
  company: z.string().nullable(),
  display_name: z.string().nullable(),
  rating: z.string().nullable(),
  rating_class: z.enum(["buy", "soft_buy", "sell"]),
  target_price: nullableNum,
  report_current_price: nullableNum,
  stated_upside_pct: nullableNum,
  first_trade_date: nullableDate,
  start_close: nullableNum,
  latest_trade_date: nullableDate,
  latest_close: nullableNum,
  return_latest_pct: nullableNum,
  return_30d_pct: nullableNum,
  return_90d_pct: nullableNum,
  return_180d_pct: nullableNum,
  return_365d_pct: nullableNum,
  benchmark_return_pct: nullableNum,
  alpha_latest_pct: nullableNum,
  max_high_return_pct: nullableNum,
  target_hit_until_latest: z.boolean().nullable(),
  first_target_hit_date: nullableDate,
  days_to_target: int.nullable(),
  peak_return_24m_pct: nullableNum,
  peak_date_24m: nullableDate,
  age_days: int.nullable(),
  maturity: z.string().nullable(),
  performance_bucket: z.string(),
  bucket_peak: z.string(),
  data_issue: z.string().nullable(),
  source_name: z.string(),
  source_md_url: z.url(),
  source_pdf_url: z.url().nullable(),
});

export const InstrumentSchema = z.object({
  id: InstrumentIdSchema,
  market: MarketSchema,
  ticker: z.string(),
  name: z.string(),
  exchange: z.string().nullable(),
  report_count: int,
  club_report_counts: z.record(z.string(), int),
  first_report_date: nullableDate,
  latest_report_date: nullableDate,
});

export const InstrumentCollectionDocumentSchema = documentSchema(
  links(["self"]),
  z.array(InstrumentSchema.extend({ links: links(["self", "prices"], ["trade_marks"]) })),
);
export const InstrumentDocumentSchema = documentSchema(
  links(["self", "collection", "prices"], ["trade_marks"]),
  InstrumentSchema.extend({ reports: z.array(ReportSchema.extend({ links: links(["club"]) })) }),
);

export const CandleSchema = z.object({
  time: date,
  open: nullableNum,
  high: nullableNum,
  low: nullableNum,
  close: num,
  volume: int.nullable(),
});
export const PriceSeriesDocumentSchema = documentSchema(
  links(["self", "instrument"]),
  z.object({ candles: z.array(CandleSchema), report_dates: z.array(date) }),
);

export const TradeMarkSchema = z.object({
  date,
  side: z.enum(["buy", "sell", "stop"]),
  price: num,
  reason: z.string(),
});
export const TradeMarksDocumentSchema = documentSchema(
  links(["self", "instrument"], ["strategy"]),
  z.object({
    strategy_id: StrategyIdSchema,
    ticker: z.string(),
    market: MarketSchema,
    name: z.string(),
    marks: z.array(TradeMarkSchema),
    open_stop: z.object({ stop_level: num, entry_date: date, as_of: date }).nullable(),
  }),
);

export const ClubSchema = z.object({
  id: ClubIdSchema,
  name: z.string(),
  full_name: z.string(),
  report_count: int,
  first_report_date: nullableDate,
  latest_report_date: nullableDate,
  scorecard: z
    .object({
      reports: int,
      priced_reports: int,
      with_target: int,
      up_latest: int,
      down_latest: int,
      target_hit: int,
      avg_return_latest_pct: nullableNum,
      median_return_latest_pct: nullableNum,
    })
    .nullable(),
});
export const ClubCollectionDocumentSchema = documentSchema(
  links(["self"]),
  z.array(ClubSchema.extend({ links: links(["self", "reports"]) })),
);
export const ClubDocumentSchema = documentSchema(links(["self", "collection", "reports"]), ClubSchema);
export const ClubReportsDocumentSchema = documentSchema(
  links(["self", "club"]),
  z.array(ReportSchema.extend({ links: ItemLinksSchema.optional() })),
);

// ── index ──────────────────────────────────────────────────────────────────

export const ApiIndexDocumentSchema = documentSchema(
  links(["self", "openapi", "signals", "latest_signals", "strategies", "track_record", "instruments", "clubs"], ["portfolio"]),
  z.object({ title: z.string(), api_version: z.string(), description: z.string() }),
);

export type SignalSnapshotDocument = z.infer<typeof SignalSnapshotDocumentSchema>;
export type SignalIndexDocument = z.infer<typeof SignalIndexDocumentSchema>;
export type StrategyDocument = z.infer<typeof StrategyDocumentSchema>;
export type StrategyCollectionDocument = z.infer<typeof StrategyCollectionDocumentSchema>;
export type StrategyTradesDocument = z.infer<typeof StrategyTradesDocumentSchema>;
export type StrategyRevision = z.infer<typeof StrategyRevisionSchema>;
export type TrackRecordDocument = z.infer<typeof TrackRecordDocumentSchema>;
export type InstrumentCollectionDocument = z.infer<typeof InstrumentCollectionDocumentSchema>;
export type InstrumentDocument = z.infer<typeof InstrumentDocumentSchema>;
export type PriceSeriesDocument = z.infer<typeof PriceSeriesDocumentSchema>;
export type Candle = z.infer<typeof CandleSchema>;
export type TradeMarksDocument = z.infer<typeof TradeMarksDocumentSchema>;
export type TradeMark = z.infer<typeof TradeMarkSchema>;
export type ClubCollectionDocument = z.infer<typeof ClubCollectionDocumentSchema>;
export type ClubDocument = z.infer<typeof ClubDocumentSchema>;
export type ClubReportsDocument = z.infer<typeof ClubReportsDocumentSchema>;
export type ApiIndexDocument = z.infer<typeof ApiIndexDocumentSchema>;
