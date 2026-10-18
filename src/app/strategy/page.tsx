import { promises as fs } from "node:fs";
import path from "node:path";

import type { Metadata } from "next";
import Link from "next/link";
import { SiteHeader } from "@/components/site-header";
import { StatStrip } from "@/components/report-ledger";
import { StrategySelector } from "@/components/strategy/strategy-selector";
import { ResearchTable, type ResearchRow } from "@/components/strategy/research-table";
import { STRATEGY_LABEL_KO } from "@/components/strategy/strategy-meta";
import backtest from "@/data/strategy-backtest.json";
import { signColor } from "@/lib/verdict";
import { cn, formatPct, formatPrice } from "@/lib/utils";
import { z } from "zod";

export const metadata: Metadata = {
  title: "전략 — 판결 아카이브",
  description:
    "학회 리포트 유니버스에서 전체 전략 변형을 선택·검증한 결과와 공표 신호를 보여줍니다. 독립 홀드아웃이 없는 기존 공표본임을 명시합니다.",
};

function tickerSlug(market: string, ticker: string): string {
  return `${market}-${ticker}`.toLowerCase();
}

// ─── Types ────────────────────────────────────────────────────────────────────

type PeriodMetrics = {
  start?: string;
  end?: string;
  total_return_pct?: number | null;
  cagr_pct?: number | null;
  sharpe?: number | null;
  mdd_pct?: number | null;
};

type WealthPoint = {
  month: number;
  date: string;
  contributed: number;
  strategy_value: number;
  KOSPI_value?: number;
  SP500_value?: number;
  NASDAQ_value?: number;
  AllWeather_value?: number;
};

type TriggerReport = {
  school: string;
  report_date: string;
  source_file?: string;
  target_price?: number | null;
  stated_upside_pct?: number | null;
};

type Trade = {
  ticker: string;
  market?: string;
  display_name?: string;
  source?: string;
  entry_date: string;
  exit_date: string;
  entry: number;
  exit: number;
  return_pct: number;
  days: number;
  n_clubs?: number;
  exit_reason?: string;
  trigger_schools?: string[];
  trigger_reports?: TriggerReport[];
  trigger_target_prices?: number[];
};

type SignalPosition = {
  ticker: string;
  market?: string;
  display_name?: string;
  entry_reason?: string;
  entry_date: string;
  entry_price: number;
  current_price?: number | null;
  unrealized_pct?: number | null;
  days_elapsed: number;
  highest_since_entry?: number | null;
  stop_level?: number | null;
  dist_to_stop_pct?: number | null;
  stop_hit?: boolean;
  extension?: number | null; // ATR% multiple from 50-MA (과열 게이지)
  trigger_schools?: string[];
  trigger_reports?: TriggerReport[];
};

type ImminentBuy = {
  ticker: string;
  market?: string;
  display_name?: string;
  n_schools: number;
  entry_basis_date?: string | null;
  entry_basis_price?: number | null;
  entry_pending?: boolean;
  entry_reason?: string;
  trigger_schools?: string[];
  trigger_reports?: TriggerReport[];
};

type SignalsData = {
  as_of: string;
  headline_strategy: string;
  disclaimer: string;
  regime?: {
    applies: boolean;
    state: "ON" | "OFF";
    kospi_close: number;
    kospi_ma200: number;
    note: string;
  } | null;
  slots?: { max_positions: number; open: number; available: number };
  open_positions: SignalPosition[];
  approaching_stop: SignalPosition[];
  imminent_buys: ImminentBuy[];
  watching_count?: number;
  counts: {
    open: number;
    approaching_stop: number;
    imminent_buys: number;
    watching: number;
  };
};

type StrategyRow = {
  key: string;
  label: string;
  metrics: {
    total_return_pct: number;
    cagr_pct: number | null;
    sharpe: number | null;
    mdd_pct: number;
    trades: number;
    win_rate_pct: number | null;
    avg_hold_days: number | null;
    max_single_return_pct: number | null;
    best_trade_ticker: string | null;
  };
  in_sample: PeriodMetrics;
  out_of_sample: PeriodMetrics;
  max_single_return_pct: number | null;
  best_trade_name: string | null;
  best_trade_ticker: string | null;
  top_decile_pnl_share_pct: number;
  trade_count: number;
  kospi_dca_ratio?: number | null;
  kospi_dca_beats?: boolean | null;
  aw_dca_ratio?: number | null;
  aw_dca_beats?: boolean | null;
  // v20: 백테스트가 실행 시점 수치로 생성한 데이터 주도 판정
  verdict?: string | null;
  verdict_reason?: string | null;
};

type StrategyWealthSim = {
  final_strategy_value: number;
  strategy_gain_on_contributed_pct: number | null;
  strategy_mdd_pct: number;
  series: WealthPoint[];
};

type OpenPositionItem = {
  ticker: string;
  market?: string;
  display_name?: string;
  entry_date: string;
  entry: number;
  last_close: number;
  stop?: number;
  return_pct: number;
  source?: string;
  n_clubs?: number;
};

 type MultiStrategyData = {
   strategies: StrategyRow[];
   headline_key: string;
   curated_keys?: string[];
   verdict_rules?: string;
   detail_refs: Record<string, { path: string; content_hash: string; version?: string }>;
 };

// ─── Verdict chip ─────────────────────────────────────────────────────────────

function VerdictChip({ kind, reason }: { kind: "SOTA" | "채택" | "기각" | "연구용"; reason?: string }) {
  const styles: Record<string, string> = {
    SOTA: "bg-stamp text-background",
    채택: "border border-up/50 bg-up/10 text-up",
    기각: "border border-down/50 bg-down/10 text-down",
    연구용: "border border-border bg-secondary/40 text-muted-foreground",
  };
  return (
    <span
      className={cn("inline-block rounded-sm px-1.5 py-0.5 font-mono text-[10px] font-bold cursor-help", styles[kind])}
      title={reason ?? (kind === "SOTA" ? "선택 기준 상위 — 독립 홀드아웃 검증 없음; 기존 공표본" : kind === "채택" ? "셀렉터 채택 전략" : undefined)}
    >
      {kind === "SOTA" ? "선택 기준 상위" : kind}
    </span>
  );
}

export default async function StrategyPage() {
  const m = backtest.metrics as {
    start: string; end: string;
    total_return_pct: number; cagr_pct: number | null;
    sharpe: number | null; mdd_pct: number;
    trades: number; win_rate_pct: number | null;
    max_single_return_pct?: number | null;
  };
  const ws = backtest.wealth_sim as {
    fx_assumption: string;
    schedule_desc: string;
    final_contributed: number;
    final_strategy_value: number;
    final_benchmark_values: Record<string, number>;
    strategy_gain_on_contributed_pct: number | null;
    benchmark_gain_on_contributed_pct: Record<string, number | null>;
    strategy_mdd_pct: number;
    series: WealthPoint[];
  };
  const consensus = backtest.consensus_stats as {
    single_club: { count: number; avg_return_pct: number | null; win_rate_pct: number | null };
    multi_club: { count: number; avg_return_pct: number | null; win_rate_pct: number | null };
    alpha_multi_vs_single: number | null;
    note: string;
  };
  const inSample = backtest.in_sample as PeriodMetrics;
  const outOfSample = backtest.out_of_sample as PeriodMetrics;
  const params = backtest.params as {
    headline_strategy: string;
    headline_key: string;
    is_period: string;
    oos_period: string;
    universe_start: string;
    sim_start: string;
    cost_per_side: number;
    position_weight: number;
    max_positions: number;
    chandelier_atr_mult: number;
    faber_ma_period: number;
    anti_overfit_note: string;
  };
  const signals = backtest.signals as unknown as SignalsData | undefined;
  const optunaNote = (backtest as Record<string, unknown>).optuna_chandelier as {
    adopted: boolean;
    best_params: { atr_period: number; atr_mult: number; max_positions: number };
    fold1_sharpe: number | null;
    fold2_sharpe: number | null;
    oos_sharpe: number | null;
    is_sharpe: number | null;
    n_trials: number;
    search_space: {
      atr_period: number[];
      atr_mult: number[] | { min: number; max: number; step: number };
      max_positions: number[];
    };
    methodology: string;
    adoption_criteria: string;
  } | undefined;
  const spoNote = (backtest as Record<string, unknown>).spo_predict_optimize as {
    paper: string;
    reference_impl: string;
    decision_problem: string;
    features: string[];
    mirrored_from_julia: string;
    adaptations: string;
    panel_months: number;
    first_rebalance: string | null;
    n_rebalances: number;
    n_train_months_final: number;
    V_spo: { is_sharpe: number | null; oos_sharpe: number | null; trades: number; mdd_pct: number | null; kospi_dca_ratio: number | null };
    V_ls: { is_sharpe: number | null; oos_sharpe: number | null; trades: number; mdd_pct: number | null; kospi_dca_ratio: number | null };
    spo_beats_ls: { is: boolean; oos: boolean };
    spo_vs_ls_verdict: string;
    promoted_to_selector: boolean;
    promotion_verdict: string;
    promotion_criteria: string;
    warmup_note: string;
  } | undefined;
  const sourceMulti = backtest.multi_strategy as unknown as Record<string, unknown> & { strategies: StrategyRow[]; headline_key: string; curated_keys?: string[]; verdict_rules?: string };
  const manifestPath = path.join(process.cwd(), "public", "api", "v1", "strategy-details", "manifest.json");
  const manifest = z.object({
    as_of: z.iso.date(),
    assets: z.record(z.string(), z.object({
      content_hash: z.string().regex(/^[a-f0-9]{64}$/),
      path: z.string().regex(/^strategy-details\/[A-Za-z0-9_+.-]+\/[a-f0-9]{64}\.json$/),
    })),
  }).parse(JSON.parse(await fs.readFile(manifestPath, "utf-8")));
  if (manifest.as_of !== signals?.as_of) throw new Error("Strategy detail generation date mismatch");
  const detailRefs = Object.fromEntries(sourceMulti.strategies.map(({ key }) => {
    const ref = manifest.assets[key];
    if (!ref || !ref.path.endsWith(`/${ref.content_hash}.json`)) {
      throw new Error(`Missing or inconsistent detail reference for ${key}`);
    }
    return [key, { path: `/api/v1/${ref.path}`, content_hash: ref.content_hash }];
  }));
  // Never spread the producer envelope: it also contains every trade and curve.
  const multiStrategy: MultiStrategyData = {
    strategies: sourceMulti.strategies,
    headline_key: sourceMulti.headline_key,
    curated_keys: sourceMulti.curated_keys,
    verdict_rules: sourceMulti.verdict_rules,
    detail_refs: detailRefs,
  };
  // U(과열 스케일아웃) vs T- 판정 — 백테스트가 생성한 에디토리얼 (하드코딩 금지: 데이터가 승자를 정한다)
  const uNote = ((backtest as Record<string, unknown>).v12_new_strategies as {
    U_chandelier_scaleout?: { vs_tminus?: { promoted?: boolean; verdict?: string; editorial?: string } };
  } | undefined)?.U_chandelier_scaleout?.vs_tminus;
  const universeStats = backtest.universe_stats as {
    kr_reports: number; us_reports: number; total_reports: number;
    kr_tickers: number; us_tickers: number;
  };


  // v20: 채택(셀렉터) 키와 판정은 백테스트 JSON이 단일 소스 — 프런트 하드코딩 없음.
  const curatedKeys = multiStrategy.curated_keys ?? [multiStrategy.headline_key];

  // 연구 기록 테이블: 전 전략 노출 (L/M은 백테스트에서 가지치기되어 행 자체가 없음).
  const researchRows = multiStrategy.strategies;

  // v24: 헤드라인의 다중검정 보정 결과 — 정직성 공시 (백테스트 JSON이 단일 소스)
  const headlineDsr = (researchRows as ResearchRow[]).find(
    (r) => r.key === multiStrategy.headline_key,
  )?.dsr;

  const headlineLabel = STRATEGY_LABEL_KO[params.headline_key] ?? params.headline_key;
  const simStartDisplay = (params as { sim_start?: string }).sim_start ?? "2020-01-01";
  const strategyCount = multiStrategy.strategies.length;

  const regime = signals?.regime;
  const slots = signals?.slots;

  return (
    <main className="mx-auto max-w-[1500px] space-y-8 px-4 py-6 sm:px-8">
      <SiteHeader eyebrow="Strategy Lab" />

      {/* ══════════════════════════════════════════════════════════════
          ① 오늘의 신호 — 선택된 전략의 임박 매매
      ══════════════════════════════════════════════════════════════ */}
      {signals && (
        <section className="rounded-lg border-2 border-stamp bg-stamp/5 p-5" aria-label="오늘의 신호">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="font-display text-xl font-black tracking-tight text-stamp">오늘의 신호</h2>
            <p className="font-mono text-[11px] text-muted-foreground">
              기준일: {signals.as_of} · {headlineLabel}
            </p>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            선택된 전략({headlineLabel})이 현재 규칙대로 산출한 임박 주문입니다. 리포트 뉴스 피드가 아니라 전략의 공표 신호입니다.
          </p>

          {/* Regime banner */}
          {regime && (
            <div
              className={cn(
                "mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border px-3 py-2",
                regime.applies && regime.state === "OFF"
                  ? "border-down/50 bg-down/10"
                  : "border-border bg-card",
              )}
            >
              <span className={cn(
                "rounded-sm px-1.5 py-0.5 font-mono text-[10px] font-black",
                regime.state === "ON" ? "bg-up/15 text-up" : "bg-down/15 text-down",
              )}>
                레짐 {regime.state}
              </span>
              <span className="font-mono text-[11px] text-muted-foreground">
                KOSPI {regime.kospi_close.toLocaleString()} vs 200MA {regime.kospi_ma200.toLocaleString()}
              </span>
              <span className="text-xs text-muted-foreground">{regime.note}</span>
            </div>
          )}

          {/* Count badges */}
          <div className="mt-3 flex flex-wrap gap-2">
            {[
              { label: "보유 중", count: signals.counts.open, tone: "bg-card border-border" },
              { label: "매도 임박 (스탑 3% 이내)", count: signals.counts.approaching_stop, tone: signals.counts.approaching_stop > 0 ? "bg-down/10 border-down/40" : "bg-card border-border" },
              { label: "매수 임박 (최근 5거래일)", count: signals.counts.imminent_buys, tone: signals.counts.imminent_buys > 0 ? "bg-up/10 border-up/40" : "bg-card border-border" },
              ...(slots ? [{ label: `슬롯 여유 (최대 ${slots.max_positions})`, count: slots.available, tone: slots.available === 0 ? "bg-down/10 border-down/40" : "bg-card border-border" }] : []),
            ].map((item) => (
              <div key={item.label} className={cn("rounded-lg border px-3 py-1.5", item.tone)}>
                <p className="font-mono text-[10px] text-muted-foreground">{item.label}</p>
                <p className="tnum font-display text-xl font-black">{item.count}</p>
              </div>
            ))}
          </div>

          {/* 매도 임박 — within 3% of trailing stop (incl. stop hit) */}
          {(signals.approaching_stop?.length ?? 0) > 0 && (
            <div className="mt-4">
              <p className="mb-2 font-mono text-[10px] font-semibold uppercase tracking-[0.2em] text-down">
                매도 임박 — 트레일링 스탑 3% 이내
              </p>
              <div className="space-y-2">
                {signals.approaching_stop.map((pos, i) => {
                  const slug = pos.market && pos.ticker ? tickerSlug(pos.market, pos.ticker) : null;
                  return (
                    <div key={`${pos.ticker}-${i}`} className="rounded-md border border-down/40 bg-down/5 px-4 py-3">
                      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                        {slug ? (
                          <Link href={`/stocks/${slug}`} className="font-mono text-sm font-bold hover:underline">
                            {pos.display_name ?? pos.ticker}
                          </Link>
                        ) : (
                          <span className="font-mono text-sm font-bold">{pos.display_name ?? pos.ticker}</span>
                        )}
                        <span className="font-mono text-xs text-muted-foreground">({pos.ticker})</span>
                        {pos.stop_hit ? (
                          <span className="rounded-sm bg-down px-1.5 py-0.5 font-mono text-[10px] font-bold text-background">
                            스탑 터치 — 다음 시가 청산
                          </span>
                        ) : pos.dist_to_stop_pct != null && (
                          <span className="rounded-sm bg-down/20 px-1.5 py-0.5 font-mono text-[10px] font-bold text-down">
                            스탑까지 {pos.dist_to_stop_pct.toFixed(1)}%
                          </span>
                        )}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted-foreground">
                        <span>진입가: <strong className="text-foreground">{formatPrice(pos.entry_price, pos.market)}</strong></span>
                        {pos.current_price != null && <span>현재가: <strong className={cn(pos.unrealized_pct != null && pos.unrealized_pct >= 0 ? "text-up" : "text-down")}>{formatPrice(pos.current_price, pos.market)}</strong></span>}
                        {pos.stop_level != null && <span>스탑: <strong className="text-down">{formatPrice(pos.stop_level, pos.market)}</strong></span>}
                        {pos.unrealized_pct != null && <span>미실현: <strong className={cn(pos.unrealized_pct >= 0 ? "text-up" : "text-down")}>{pos.unrealized_pct > 0 ? "+" : ""}{pos.unrealized_pct.toFixed(1)}%</strong></span>}
                      </div>
                      {pos.entry_reason && (
                        <p className="mt-1 text-xs text-muted-foreground">
                          매수 사유: <span className="text-foreground">{pos.entry_reason}</span>
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* 매수 임박 — reports in last 5 trading days, not yet entered */}
          {signals.imminent_buys.length > 0 && (
            <div className="mt-4">
              <p className="mb-2 font-mono text-[10px] font-semibold uppercase tracking-[0.2em] text-up">
                매수 임박 — 최근 5거래일 리포트, 익일 시가 진입 대기
                {slots && slots.available === 0 && (
                  <span className="ml-2 normal-case tracking-normal text-down">슬롯 가득 — 빈 슬롯이 생겨야 진입</span>
                )}
              </p>
              <div className="space-y-2">
                {signals.imminent_buys.map((sig, i) => {
                  const slug = sig.market && sig.ticker ? tickerSlug(sig.market, sig.ticker) : null;
                  return (
                    <div key={`${sig.ticker}-${i}`} className="rounded-md border border-up/40 bg-up/5 px-4 py-3">
                      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                        {slug ? (
                          <Link href={`/stocks/${slug}`} className="font-mono text-sm font-bold hover:underline">
                            {sig.display_name ?? sig.ticker}
                          </Link>
                        ) : (
                          <span className="font-mono text-sm font-bold">{sig.display_name ?? sig.ticker}</span>
                        )}
                        <span className="font-mono text-xs text-muted-foreground">({sig.ticker})</span>
                        <span className="rounded-sm bg-up/20 px-1.5 py-0.5 font-mono text-[10px] font-bold text-up">
                          {sig.n_schools}개교
                        </span>
                        {sig.entry_pending && (
                          <span className="rounded-sm border border-up/50 px-1.5 py-0.5 font-mono text-[10px] font-bold text-up">
                            진입 대기 중
                          </span>
                        )}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted-foreground">
                        <span>진입 기준일: <strong className="text-foreground">{sig.entry_basis_date ?? "다음 거래일"}</strong></span>
                        <span>진입 기준가: <strong className="text-foreground">{sig.entry_basis_price != null ? sig.entry_basis_price.toLocaleString() : "익일 시가"}</strong></span>
                      </div>
                      {sig.entry_reason && (
                        <p className="mt-1 text-xs text-muted-foreground">
                          매수 사유: <span className="text-foreground">{sig.entry_reason}</span>
                        </p>
                      )}
                      {sig.trigger_reports && sig.trigger_reports.length > 0 && (
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {sig.trigger_reports.map((r, ri) => (
                            <span key={ri} className="rounded border border-border bg-card px-1.5 py-0.5 font-mono text-[10px]">
                              [{r.school}] {r.report_date}
                              {r.target_price ? ` 목표가 ${r.target_price.toLocaleString()}` : ""}
                              {r.stated_upside_pct != null ? ` (업사이드 +${r.stated_upside_pct.toFixed(0)}%)` : ""}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* 보유 중 — open positions table */}
          {signals.open_positions.length > 0 && (
            <div className="mt-4">
              <p className="mb-2 font-mono text-[10px] font-semibold uppercase tracking-[0.2em] text-foreground">
                보유 중 — {signals.counts.open}종목 (현재가·스탑·과열계수)
              </p>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] border-collapse text-sm">
                  <thead>
                    <tr className="border-b-2 border-border font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
                      <th className="px-3 py-1.5 text-left">종목</th>
                      <th className="px-3 py-1.5 text-right">미실현</th>
                      <th className="px-3 py-1.5 text-right">현재가</th>
                      <th className="px-3 py-1.5 text-right">스탑</th>
                      <th className="px-3 py-1.5 text-right" title="ATR% Multiple from 50-MA. 8× 이상 과열 (Minervini circle)">과열계수</th>
                      <th className="px-3 py-1.5 text-right">보유일</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...signals.open_positions].sort((a, b) => (b.unrealized_pct ?? -999) - (a.unrealized_pct ?? -999)).map((pos, i) => {
                      const slug = pos.market && pos.ticker ? tickerSlug(pos.market, pos.ticker) : null;
                      const ext = pos.extension;
                      const extColor = ext == null ? "text-muted-foreground" : ext >= 12 ? "text-down font-bold" : ext >= 8 ? "text-amber-500 font-bold" : "text-muted-foreground";
                      return (
                        <tr key={`${pos.ticker}-${i}`} className="border-b border-dashed border-border last:border-b-0">
                          <td className="px-3 py-1.5" title={pos.entry_reason || undefined}>
                            {slug ? (
                              <Link href={`/stocks/${slug}`} className="hover:underline">
                                <p className="font-mono text-xs font-bold">{pos.display_name ?? pos.ticker}</p>
                              </Link>
                            ) : (
                              <p className="font-mono text-xs font-bold">{pos.display_name ?? pos.ticker}</p>
                            )}
                            <p className="font-mono text-[10px] text-muted-foreground">{pos.ticker}</p>
                          </td>
                          <td className={cn("tnum px-3 py-1.5 text-right font-mono text-xs font-bold", pos.unrealized_pct != null ? (pos.unrealized_pct >= 0 ? "text-up" : "text-down") : "")}>
                            {pos.unrealized_pct != null ? `${pos.unrealized_pct > 0 ? "+" : ""}${pos.unrealized_pct.toFixed(1)}%` : "—"}
                          </td>
                          <td className="tnum px-3 py-1.5 text-right font-mono text-xs">
                            {pos.current_price != null ? formatPrice(pos.current_price, pos.market) : "—"}
                          </td>
                          <td className="tnum px-3 py-1.5 text-right font-mono text-xs text-muted-foreground">
                            {pos.stop_level != null ? formatPrice(pos.stop_level, pos.market) : "—"}
                          </td>
                          <td className={cn("tnum px-3 py-1.5 text-right font-mono text-xs", extColor)}>
                            {ext != null ? `${ext.toFixed(1)}×` : "—"}
                          </td>
                          <td className="tnum px-3 py-1.5 text-right font-mono text-xs text-muted-foreground">
                            {pos.days_elapsed}일
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="mt-1 text-[10px] text-muted-foreground">
                과열계수 = B/A (A=ATR14/가격, B=(가격−50SMA)/50SMA). 8× 황색, 12× 이상 적색 — Minervini 커뮤니티 관행, TradingView Fred6724.
                상세 보유 테이블은{" "}
                <a href="#strategy-selector" className="underline hover:text-foreground">전략 비교 → 보유 중 탭</a>에서 확인하세요.
              </p>
            </div>
          )}

          {/* Watching — demoted to a one-line count (report flow, not a strategy signal) */}
          {(signals.watching_count ?? 0) > 0 && (
            <p className="mt-4 border-t border-dashed border-border pt-3 text-xs text-muted-foreground">
              그 외 유효 리포트가 있는 미보유 종목 {signals.watching_count}개는 전략 신호가 아닌 리포트 흐름입니다 —{" "}
              <Link href="/" className="underline hover:text-foreground">아카이브에서 확인</Link>.
            </p>
          )}

          <p className="mt-3 text-[11px] italic text-muted-foreground">{signals.disclaimer}</p>
        </section>
      )}

      {/* ══════════════════════════════════════════════════════════════
          ② 헤드라인 요약
      ══════════════════════════════════════════════════════════════ */}
      <header>
        <h1 className="mt-2 font-display text-4xl font-black leading-[1.15] tracking-tight sm:text-5xl">
          전성기 수익을 어떻게 <span className="text-stamp">수확</span>할 것인가.
        </h1>
        <p className="mt-3 max-w-3xl text-base leading-7 text-muted-foreground">
          학회 리포트 발간 후 24개월 내 피크 수익률이 거의 전부 양(+)이라는 사실 —
          하지만 라운드트립을 타면 그 이익은 사라집니다.
          {strategyCount}개 전략 변형을 인샘플({simStartDisplay.slice(0, 7)}–2023-12)로 비교하고
          선택에 사용한 검증 구간(2024–현재)과 기존 공표본을 표시합니다. 독립 홀드아웃 검증은 제공되지 않으며, 회계 정정 재계산 전 자료입니다.
        </p>
        <p className="mt-1.5 text-sm italic text-muted-foreground">
          주의: 과거 데이터 기반 시뮬레이션. 미래 수익 보장 없음. 과최적화 방지를 위해 파라미터는 문헌 표준값 고정.
        </p>
        {headlineDsr && (
          <p className="mt-1.5 text-sm text-muted-foreground">
            정직성 공시 — {headlineDsr.n_trials}개 변형 중 최고를 고른 선택 편향을 보정한{" "}
            <strong className="text-foreground">DSR {headlineDsr.dsr.toFixed(2)}</strong>
            {headlineDsr.significant_after_deflation
              ? " · 보정 후에도 통계적으로 유의합니다 (≥ 0.95)."
              : ` · 0.95 미달 — ${headlineDsr.n_trials}번 시도해 골랐다는 사실을 감안하면 유의성은 보류입니다. 전진 기록이 쌓여야 판가름납니다.`}
          </p>
        )}
        <p className="mt-1.5 text-sm text-muted-foreground">
          유니버스:{" "}
          <strong className="text-foreground">KR {universeStats.kr_tickers}개 + US {universeStats.us_tickers}개</strong>{" "}
          종목 (1회 이상 Buy 언급 — 단독·컨센서스 모두 진입) · 시뮬 기점 {simStartDisplay}
        </p>
      </header>

      <StatStrip
        items={[
          { label: "누적 수익률 (선택 기준 상위)", value: formatPct(m.total_return_pct, 1), tone: signColor(m.total_return_pct) },
          { label: "CAGR (전체)", value: formatPct(m.cagr_pct, 1), tone: signColor(m.cagr_pct) },
          { label: "인샘플 샤프", value: String(inSample.sharpe ?? "—") },
          { label: "검증 구간 샤프", value: String(outOfSample.sharpe ?? "—"), tone: "text-up" },
          { label: "MDD", value: formatPct(m.mdd_pct, 1), tone: "text-down" },
          { label: "승률 / 거래", value: `${m.win_rate_pct ?? "—"}% / ${m.trades}건` },
        ]}
      />

      {/* ══════════════════════════════════════════════════════════════
          ③ 전략 비교 + 자산곡선·시뮬 (curated selector)
      ══════════════════════════════════════════════════════════════ */}
      <details open id="strategy-selector" className="group rounded-lg border border-border bg-card">
        <summary className="flex cursor-pointer select-none list-none items-baseline justify-between gap-2 p-5 transition-colors hover:bg-secondary/20 [&::-webkit-details-marker]:hidden">
          <div>
            <h2 className="font-display text-2xl font-black tracking-tight">전략 비교 &amp; 상세</h2>
            <p className="mt-0.5 text-sm text-muted-foreground">
              전 전략 {strategyCount}개 멀티 오버레이 (채택 {curatedKeys.length}개) · 자산곡선 · 월 적립 시뮬레이션 · 거래 로그
            </p>
          </div>
          <span className="font-mono text-sm text-muted-foreground transition-transform group-open:rotate-180">▼</span>
        </summary>
        <div className="space-y-6 border-t border-border px-5 pb-5 pt-4">
          <p className="text-xs text-muted-foreground">
            모든 전략: 1회 이상 Buy 언급 시 즉시 진입, 동일비중 {Math.round((params.position_weight ?? 0.05) * 100)}%,
            편도 수수료 {(params.cost_per_side ?? 0.003) * 100}%. 동시 보유 한도로 신호 일부는 미체결.{" "}
            <span className="font-medium text-foreground">{params.anti_overfit_note}</span>
          </p>
          <StrategySelector
            multiStrategy={multiStrategy}
            wealthSimGlobal={ws}
            curatedKeys={curatedKeys}
          />
        </div>
      </details>

      {/* ══════════════════════════════════════════════════════════════
          ④ 전체 연구 기록 — every strategy ever tested, with verdicts
      ══════════════════════════════════════════════════════════════ */}
      <details className="group rounded-lg border border-border bg-card">
        <summary className="flex cursor-pointer select-none list-none items-baseline justify-between gap-2 p-5 transition-colors hover:bg-secondary/20 [&::-webkit-details-marker]:hidden">
          <div>
            <h2 className="font-display text-2xl font-black tracking-tight">전체 연구 기록 — {strategyCount}개 전략 변형</h2>
            <p className="mt-0.5 text-sm text-muted-foreground">
              시험한 모든 전략의 IS/OOS 성적과 채택·기각 사유 · CSV 다운로드
            </p>
          </div>
          <span className="font-mono text-sm text-muted-foreground transition-transform group-open:rotate-180">▼</span>
        </summary>
        <div className="border-t border-border px-5 pb-5 pt-4">
          <p className="mb-3 text-xs text-muted-foreground">
            <VerdictChip kind="SOTA" /> 선택 기준 상위(독립 홀드아웃 없음) ·{" "}
            <VerdictChip kind="채택" /> 셀렉터 채택 ·{" "}
            <VerdictChip kind="기각" /> 비용·성과 미달로 제외 ·{" "}
            <VerdictChip kind="연구용" /> 비교 앵커·오버레이·대조군 — 판정과 사유는 백테스트가 실행 시점
            수치로 생성합니다 (칩에 마우스를 올리면 사유 표시). 헤더 클릭으로 정렬 (Shift+클릭: 다중 정렬).
          </p>
          <ResearchTable
            rows={researchRows as ResearchRow[]}
            headlineKey={multiStrategy.headline_key}
          />
          <p className="mt-2 text-xs text-muted-foreground">
            IS = In-Sample {simStartDisplay.slice(0, 7)} ~ 2023-12 · 검증 구간 = 2024-01 ~ 현재(독립 홀드아웃 아님) ·
            vs KOSPI/올웨더 DCA = 동일 적립 스케줄 최종 자산 비율 (1.00x 초과 = 해당 적립식 우위).
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            표에서 생략: L 민리버전(RSI-2)·M 단기 리버설은 룩어헤드·버그 수정 후 재검증에서도
            거래비용 사망이 확정되어 v18부터 백테스트에서 제거 — 테스트했고 실패했다는 기록만 남깁니다.
            그 외 전 전략(배분형 변형·대조군 포함)은 모두 이 표와 위 오버레이 셀렉터에 노출됩니다.
          </p>
        </div>
      </details>

      {/* ══════════════════════════════════════════════════════════════
          ⑤ 방법론 / 전략 규칙 / 각주 — collapsible
      ══════════════════════════════════════════════════════════════ */}
      <details className="group rounded-lg border border-border bg-card">
        <summary className="flex cursor-pointer select-none list-none items-baseline justify-between gap-2 p-5 transition-colors hover:bg-secondary/20 [&::-webkit-details-marker]:hidden">
          <div>
            <h2 className="font-display text-2xl font-black tracking-tight">방법론 &amp; 각주</h2>
            <p className="mt-0.5 text-sm text-muted-foreground">전략 규칙 · 한계 · 면책사항</p>
          </div>
          <span className="font-mono text-sm text-muted-foreground transition-transform group-open:rotate-180">▼</span>
        </summary>
        <div className="space-y-5 border-t border-border px-5 pb-5 pt-4">

          {/* Strategy rules */}
          <div className="grid gap-4 lg:grid-cols-2">
            {[
              {
                n: 1,
                title: "유니버스 — KR + US 전체 Buy 언급",
                body: `KR ${universeStats.kr_tickers}개 + US ${universeStats.us_tickers}개 종목. 리포트 풀 ${params.universe_start} 이후, 시뮬 기점 ${simStartDisplay}. 1회 이상 Buy 언급 즉시 진입 — 단독 커버·컨센서스 모두 포함. 동시 보유 한도로 신호 일부 미체결. US 종목 손익은 일별 USDKRW 환율(KRW=X)로 원화 환산 (v19 FX 레이어).`,
              },
              {
                n: 2,
                title: "진입 — 전략별 트리거 조건 (거래 로그의 '매수사유'와 동일)",
                body: "A/B/C/D/E/T/W: 학회 매수리포트 발간 → 다음 거래일 시가 즉시 진입. F: 발간 + 200MA 상회 모멘텀 필터. G/P: 발간일 종가 대비 −20% 하락 도달 시 진입 (딥바이). H: 미너비니 5-point 템플릿 통과 시. I: 발간 시 Supertrend(10,3) 상방 또는 3개월 내 상방 전환 시. K: 발간 + R:R 세팅(스탑 1×ATR20). N: 리포트 당일 close≥52w고점×85%. O: MTT 템플릿+RS≥80. Q: RS≥KOSPI+60d돌파+거래량 1.5×. 각 거래의 구체적 사유는 거래 로그 '매수사유' 열에 기록.",
              },
              {
                n: 3,
                title: "청산 — 전략별 규칙",
                body: "D/D+/T/T-/W: ATR 트레일링 스탑 (Le Beau Chandelier). G: 목표가/+50%/12mo/ATR×3 선착. H: 주간 close<50MA. I: Supertrend 하향전환. J: 80/20 자동 디레버. K: half at +2.5R + ATR×3 트레일. N: 월말 close<52w고점×70%. O: R-배수 체인(−8%/BE/트레일/+3.5R/RS<82/115d).",
              },
              {
                n: 4,
                title: `포지션·비용 — 동일비중 ${Math.round((params.position_weight ?? 0.05) * 100)}% + 현금이자·차입 모델 (v18)`,
                body: `슬롯당 NAV의 ${Math.round((params.position_weight ?? 0.05) * 100)}%. 최대 ${params.max_positions ?? 20}종목 (K는 최대 10, D+ Optuna는 탐색 결과에 따름). 거래비용 편도 ${(params.cost_per_side ?? 0.003) * 100}%. 유휴 현금: 모든 전략에 연 3.0% 일복리 — (1.03)^(1/252)−1/거래일 — 적용 (한국 MMF/단기채 ETF 프록시 가정; T-/U 레짐 OFF 구간의 파킹 잔액 포함). J(레버리지): 차입비용 연 6.0% 일복리 — (1.06)^(1/252)−1 — 차입 잔액에 적용.`,
              },
              {
                n: 5,
                title: "N 52주 고가 근접 — George & Hwang 2004 출처",
                body: "George & Hwang (2004) 'The 52-Week High and Momentum Investing'. 리포트 당일 close ≥ 52w high × 85% 진입. 월말 체크: close < 52w high × 70% 청산.",
              },
              {
                n: 6,
                title: "G 딥바이 / H 미너비니 / I 슈퍼트렌드 / K R:R",
                body: "G: Jegadeesh & Kim (2006). H: Minervini (2013). I: Supertrend(10,3) Seban. K: Van Tharp R-배수 프레임워크. 상세는 각 전략 결과 참조.",
              },
              {
                n: 7,
                title: "L 민리버전 / M 단기 리버설 — 테스트 후 제거 (v18)",
                body: "Connors RSI-2 평균회귀(L)와 월간 하위 5분위 단기 리버설(M)을 이 유니버스에서 구현·검증했다. v11에서 룩어헤드·현금 누락 버그까지 수정한 뒤에도 두 전략 모두 거래비용(0.3%/side × 높은 회전율)으로 알파가 소진되는 '비용 사망'이 확정 — v18부터 백테스트에서 제거하고 이 한 줄로만 기록한다.",
              },
              {
                n: 8,
                title: "Q 깡토 추세추종 — 시장 필터 + RS 브레이크아웃",
                body: "시장 신호: KOSPI close > 200MA AND 50MA 상승 시 그린(2유닛) 아니면 레드(1유닛). 유닛 = 총자본/20, Max 2% Rule(단일 포지션 위험 ≤ 자본 2%). 진입: RS 퍼센타일 ≥ KOSPI RS AND close = 60일 신고가 AND 거래량 ≥ 20일 평균×1.5, 다음 시가 체결. 스탑: -8%(=1R). +3R에서 반매도, +1R에서 BE, +1.5R 이후 고점-8% 트레일. 비용 0.3%/side. 기대 승률 ~30% (추세형).",
              },
              {
                n: 9,
                title: "R Kelly 샹들리에 — D+ 위에 Kelly 포지션 사이징 오버레이",
                body: "D+ 샹들리에(Optuna) 진입/청산 규칙 그대로 + 포지션 크기만 Kelly로 대체. Kelly 공식: (p - (1-p)/(avg_win/avg_loss)) × 0.5 안전계수, 상한 25%, 하한 1%, 직전 40거래 기준 롤링 계산. 거래 이력 10건 미만이면 플랫 5%로 폴백. 플랫 5%와 비교하여 사이징 효과 단독 측정.",
              },
              {
                n: 10,
                title: "S 포트폴리오 최적화 — 월간 리밸런스 3종 배분형",
                body: "활성 유니버스(18개월 매수 리포트 윈도우) 종목을 매월 리밸런스. (a) S_hrp: HRP — 상관거리 단일연결 클러스터링, 준대각 재정렬, 역분산 재귀분할. (b) S_msharpe: max-Sharpe — LedoitWolf 공분산 축소(sklearn), 개별 비중 ≤15%. (c) S_mincvar: min-CVaR 95% — scipy linprog LP, 개별 비중 ≤15%. IS 샤프 최고 변형만 셀렉터에 포함. 턴오버 비용 적용. v15: scipy 의존성 누락 시 침묵 실패(평탄 NAV) 버그 수정 — 의존성 프리플라이트로 즉시 실패.",
              },
              {
                n: 11,
                title: "V. SPO 포트폴리오 — Smart 'Predict, then Optimize'",
                body: "Elmachtoub & Grigas (2022, Management Science) SPO+ 손실 구현 — 'SPO는 향후 과제' 항목을 결과로 대체. 선형모델 ĉ=Bx를 SPO+ 손실로 SGD 학습(V-a), 동일 파이프라인 LS 손실 대조군(V-b). 캡 심플렉스(Σw=1, w≤15%) LP는 닫힌형 그리디 오라클. 워크포워드 최소 24개월, 월말 신호 → 익월 첫 거래일 시가 체결, 비용 0.3%/side. 상세·정직한 판정은 아래 'SPO 방법론 공개' 참조. 장중(intraday) 전략과 유상증자(Secondary Public Offering) 이벤트 전략은 여전히 범위 밖(데이터 부재).",
              },
              {
                n: 12,
                title: "T / T-. 코어-KOSPI 샹들리에 — 유휴 현금 KOSPI 파킹",
                body: "D+ 샹들리에 규칙 완전 동일. 유휴 현금(비어있는 슬롯 금액)을 KOSPI 지수 익스포저(KODEX200 기준 ETF 가정)로 주차. T-(레짐 변형): KOSPI < 200MA 구간에서는 파킹 수익률 대신 현금 이자 연 3% 일복리(v18). 인덱스 ETF 전환 비용 0.05%/side 가정(실제 스프레드·세금 상이 가능). 베이스라인 = KOSPI DCA — 주식 픽이 KOSPI 알파를 더하면 ratio>1, 전환 비용이 알파를 삼키면 ratio≤1. 참조: Faber (2007) 레짐 필터.",
              },
              {
                n: 13,
                title: "W. 올웨더-샹들리에 — 유휴 현금 올웨더 파킹 (v18)",
                body: "T-와 동일한 D+ 샹들리에 진입/청산 규칙이되, 유휴 현금을 KOSPI 대신 올웨더 바스켓(25% GLD / 25% NASDAQ / 25% S&P500 / 25% KOSPI, 분기 리밸런스 — 벤치마크와 동일 계산)에 상시 주차. 레짐 게이트 없음 — 올웨더 자체가 방어 자산(GLD)을 포함하므로 KOSPI<200MA 타이밍 스위치가 불필요하다는 설계 가설 (KOSPI 파킹+레짐의 과적합 우려에 대한 대안). 자기 벤치마크 = 올웨더 DCA. 헤드라인 콘테스트: W vs T- — 자기 파킹 벤치마크를 이기고 IS+OOS 샤프가 높은 쪽이 승격.",
              },
              {
                n: 14,
                title: "U. 과열 스케일아웃 — ATR% Multiple from 50-MA",
                body: "T-와 완전 동일 + 과열 게이지: extension = B/A, A = ATR(14)/가격 (ATR%), B = (가격 − 50SMA)/50SMA. extension > 8× 시 보유 주수의 절반 매도 → KOSPI 파킹(1차). extension 이후 > 12× 시 남은 포지션의 절반 다시 매도(2차). 트리거는 포지션당 1회(오실레이션 재발동 없음; 재진입 시 초기화). 출처: Minervini 커뮤니티 관행, TradingView Fred6724.",
              },
            ].map((rule) => (
              <article key={rule.n} className="rounded-lg border border-border bg-secondary/20 p-4">
                <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.25em] text-stamp">규칙 {rule.n}</p>
                <h4 className="mt-1 font-display text-base font-black tracking-tight">{rule.title}</h4>
                <p className="mt-1.5 text-sm leading-6 text-muted-foreground">{rule.body}</p>
              </article>
            ))}
          </div>

          {/* Optuna methodology disclosure */}
          {optunaNote && (
            <section className="rounded-lg border border-stamp/40 bg-stamp/5 p-5">
              <h3 className="font-display text-lg font-black tracking-tight">
                D+ Optuna 강건 최적화 — 방법론 전문 공개
                {optunaNote.adopted
                  ? <span className="ml-2 font-mono text-[11px] font-normal text-up">채택됨</span>
                  : <span className="ml-2 font-mono text-[11px] font-normal text-down">채택 불가 (OOS 미달)</span>
                }
              </h3>
              <div className="mt-3 grid gap-3 text-sm text-muted-foreground sm:grid-cols-2">
                <div>
                  <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.15em] text-foreground">탐색 공간 (5차원 이하)</p>
                  <ul className="mt-1 space-y-0.5">
                    <li>· ATR 기간: {optunaNote.search_space.atr_period?.join(" / ")}</li>
                    <li>
                      · ATR 배수:{" "}
                      {Array.isArray(optunaNote.search_space.atr_mult)
                        ? `[${optunaNote.search_space.atr_mult.join(", ")}]`
                        : `${optunaNote.search_space.atr_mult.min} ~ ${optunaNote.search_space.atr_mult.max} (step ${optunaNote.search_space.atr_mult.step})`}
                    </li>
                    <li>· 최대 포지션: {optunaNote.search_space.max_positions?.join(" / ")}</li>
                  </ul>
                </div>
                <div>
                  <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.15em] text-foreground">최적 파라미터</p>
                  <ul className="mt-1 space-y-0.5">
                    <li>· ATR 기간: {optunaNote.best_params?.atr_period}</li>
                    <li>· ATR 배수: {optunaNote.best_params?.atr_mult?.toFixed(3)}</li>
                    <li>· 최대 포지션: {optunaNote.best_params?.max_positions}</li>
                  </ul>
                </div>
                <div>
                  <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.15em] text-foreground">IS 폴드 검증 ({optunaNote.n_trials} trials)</p>
                  <ul className="mt-1 space-y-0.5">
                    <li>· 폴드1 (2020-21) 샤프: {optunaNote.fold1_sharpe?.toFixed(2) ?? "—"}</li>
                    <li>· 폴드2 (2022-23) 샤프: {optunaNote.fold2_sharpe?.toFixed(2) ?? "—"}</li>
                    <li>· 목적함수: min(f1,f2) − 0.1×|f1−f2|</li>
                  </ul>
                </div>
                <div>
                  <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.15em] text-foreground">OOS 1회 평가 결과</p>
                  <ul className="mt-1 space-y-0.5">
                    <li>· IS 샤프: {optunaNote.is_sharpe?.toFixed(2) ?? "—"}</li>
                    <li>· OOS 샤프: {optunaNote.oos_sharpe?.toFixed(2) ?? "—"}</li>
                    <li>· 채택 기준: {optunaNote.adoption_criteria}</li>
                  </ul>
                </div>
              </div>
              <p className="mt-3 text-xs italic text-muted-foreground">{optunaNote.methodology}</p>
            </section>
          )}

          {/* SPO (Smart Predict-then-Optimize) methodology disclosure */}
          {spoNote && (
            <section className="rounded-lg border border-stamp/40 bg-stamp/5 p-5">
              <h3 className="font-display text-lg font-black tracking-tight">
                V. SPO 방법론 공개 — Smart &lsquo;Predict, then Optimize&rsquo;
                {spoNote.promoted_to_selector
                  ? <span className="ml-2 font-mono text-[11px] font-normal text-up">셀렉터 승격</span>
                  : <span className="ml-2 font-mono text-[11px] font-normal text-muted-foreground">연구 기록 전용</span>
                }
              </h3>
              <p className="mt-2 text-xs text-muted-foreground">
                논문: {spoNote.paper} · 참조 구현: {spoNote.reference_impl}
              </p>
              <div className="mt-3 grid gap-3 text-sm text-muted-foreground sm:grid-cols-2">
                <div>
                  <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.15em] text-foreground">결과 — V-a SPO+ vs V-b LS</p>
                  <ul className="mt-1 space-y-0.5">
                    <li>· IS 샤프: {spoNote.V_spo.is_sharpe ?? "—"} vs {spoNote.V_ls.is_sharpe ?? "—"}</li>
                    <li>· OOS 샤프: {spoNote.V_spo.oos_sharpe ?? "—"} vs {spoNote.V_ls.oos_sharpe ?? "—"}</li>
                    <li>· vs KOSPI DCA: {spoNote.V_spo.kospi_dca_ratio ?? "—"}x vs {spoNote.V_ls.kospi_dca_ratio ?? "—"}x</li>
                    <li>· SPO+ &gt; LS (논문 주장): IS {spoNote.spo_beats_ls.is ? "성립" : "불성립"} · OOS {spoNote.spo_beats_ls.oos ? "성립" : "불성립"}</li>
                  </ul>
                </div>
                <div>
                  <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.15em] text-foreground">워크포워드 설정</p>
                  <ul className="mt-1 space-y-0.5">
                    <li>· 의사결정: {spoNote.decision_problem}</li>
                    <li>· 패널 {spoNote.panel_months}개월 · 리밸런스 {spoNote.n_rebalances}회 (첫 리밸런스 {spoNote.first_rebalance ?? "—"})</li>
                    <li>· 최종 학습 윈도우 {spoNote.n_train_months_final}개월 (확장, 최소 24)</li>
                    <li>· 피처 {spoNote.features.length}종: {spoNote.features.join(", ")}</li>
                  </ul>
                </div>
              </div>
              <p className="mt-3 text-xs text-muted-foreground">
                <strong className="text-foreground">Julia 레퍼런스에서 미러:</strong> {spoNote.mirrored_from_julia}
              </p>
              <p className="mt-1.5 text-xs text-muted-foreground">
                <strong className="text-foreground">우리의 적응:</strong> {spoNote.adaptations}
              </p>
              <p className="mt-1.5 text-xs text-muted-foreground">{spoNote.warmup_note}</p>
              <p className="mt-3 text-xs italic text-muted-foreground">
                판정: {spoNote.promotion_verdict} (승격 기준: {spoNote.promotion_criteria}) · {spoNote.spo_vs_ls_verdict}
              </p>
            </section>
          )}

          {/* Honest footnotes */}
          <section
            className="rounded-lg border-2 border-foreground/70 bg-card p-5 shadow-[4px_4px_0_0_hsl(var(--foreground)/0.7)]"
            aria-label="전략의 한계"
          >
            <h3 className="font-display text-xl font-black tracking-tight">정직한 각주 — 이 전략이 무너지는 곳</h3>
            <ul className="mt-3 space-y-2 text-sm leading-6 text-muted-foreground">
              <li>· <strong className="text-foreground">과최적화 주의.</strong> D 기본값 파라미터는 문헌 표준값(ATR×5, 200MA) 고정. D+ Optuna는 IS 2-폴드 교차검증으로 강건화했으나 표본 크기 제한으로 과최적화 위험이 남습니다.</li>
              <li>· <strong className="text-foreground">시뮬 기점 주의.</strong> 2020-01 이전 리포트 풀이 얇아 기점을 {simStartDisplay}로 설정. 실질 트레이딩은 신호 발생 시점부터이므로 첫 거래는 이후.</li>
              <li>· <strong className="text-foreground">내러티브 홀드(C) 주의.</strong> 무한 홀드 전략은 MDD 허용 범위가 넓습니다. 포지션 청산 규칙이 느슨합니다.</li>
              <li>· <strong className="text-foreground">딥바이(G) 솔직한 평가.</strong> 20% 하락이 저점이 아니라 시작인 경우가 많습니다. IS/OOS 모두 벤치마크 하회 — 채택 불가.</li>
              <li>· <strong className="text-foreground">민리버전(L) / 단기 리버설(M).</strong> 구현·검증 후 거래비용 사망 확정 — v18부터 백테스트에서 제거. 방법론 규칙 7에 한 줄로만 기록합니다.</li>
              <li>· <strong className="text-foreground">코어-새틀라이트 레버리지(J) 솔직한 평가.</strong> 차입비용(연 6% 일복리)과 폭락 타이밍 미스 리스크가 있습니다. D 보조 시뮬로만 참고하세요.</li>
              <li>· <strong className="text-foreground">현금 이자 가정.</strong> 모든 전략의 유휴 현금에 연 3.0% 일복리(MMF/단기채 ETF 프록시)를 일괄 적용합니다 — 고정 가정이며 실제 단기 금리는 기간별로 변동했습니다.</li>
              <li>· <strong className="text-foreground">US 종목 FX.</strong> v19부터 일별 USDKRW 환율(yfinance KRW=X)로 원화 환산 적용. 원화 강세 구간에서 달러 자산 수익이 감소하며, 약세 구간에서 FX 수익이 추가됩니다.</li>
              <li>· <strong className="text-foreground">컨센서스 표본 제한.</strong> US 컨센서스 종목 소수. 통계적 유의성 낮음.</li>
              <li>· 롱온리 전략. 대세 하락장 손실 불가피. 선택 기준 상위 MDD {formatPct(m.mdd_pct, 1)}.</li>
              <li>· <strong className="text-foreground">컨센서스 분석.</strong>{" "}
                선택 기준 상위 거래 기준: 단독 커버 {consensus.single_club.count}건 (평균 {formatPct(consensus.single_club.avg_return_pct, 1)}, 승률 {consensus.single_club.win_rate_pct ?? "—"}%) vs
                멀티 커버 {consensus.multi_club.count}건 (평균 {formatPct(consensus.multi_club.avg_return_pct, 1)}, 승률 {consensus.multi_club.win_rate_pct ?? "—"}%).
                {consensus.alpha_multi_vs_single != null && ` 멀티 커버 차이: ${formatPct(consensus.alpha_multi_vs_single, 1)}p.`}
              </li>
              <li>· 생존 편향: 공개를 유지한 리포트만 수집. 상장폐지 종목 시세 일부 누락 가능.</li>
              <li>· 올웨더는 25% GLD/NASDAQ/S&P500/KOSPI 분기 리밸런싱으로 단순화. 채권 미포함.</li>
              <li>· 투자 권유가 아닙니다. 과거 데이터 기반 시뮬레이션으로 미래 수익을 보장하지 않습니다.</li>
            </ul>
          </section>

          {/* Editorial: can price-only data hold tenbaggers? */}
          <section className="rounded-lg border border-stamp/40 bg-stamp/5 p-5">
            <h3 className="font-display text-lg font-black tracking-tight">가격-전용 데이터로 텐배거를 끝까지 들고 갈 수 있는가?</h3>
            <p className="mt-3 text-sm leading-7 text-muted-foreground">
              샹들리에 ATR×5 트레일은 “고점이 어디인지 모른다”는 사실을 설계로 인정하고,
              단지 <em>가격이 최고점에서 충분히 떨어질 때까지</em> 기다린다.
              이 접근은 10배·14배 구간을 가진 종목을 처음부터 끝까지 타는 것을 이론상 허용한다.
              실제로 백테스트에서 단일 최대 수익률 거래는 300~400%대로 나타났으며,
              샹들리에가 그런 종목들을 12개월 고정 보유나 목표가 절반익절보다 훨씬 오래 들고 있었다.
            </p>
            <p className="mt-2 text-sm leading-7 text-muted-foreground">
              그러나 <strong className="text-foreground">과열 구간에서 가격-전용 신호는 한계를 가진다.</strong>{" "}
              PLTR·TSLA·NVDA 유형처럼 extension(50SMA 대비 ATR% 배수)이 10×를 넘어 과열된 뒤 급락하는 패턴에서
              ATR 트레일은 이미 30~50% 되돌림을 감수한 뒤에야 청산한다.
              이에 U 전략(과열 스케일아웃: extension 8×·12× 부분 익절)을 설계해 T-와 비교했다 —
              판정은 매 백테스트 실행마다 데이터가 내린다 (전체 연구 기록 참조).
            </p>
            {uNote?.editorial && (
              <p className="mt-2 text-sm leading-7 text-muted-foreground">
                <strong className="text-foreground">이번 실행의 판정:</strong> {uNote.editorial}
              </p>
            )}
          </section>
        </div>
      </details>
    </main>
  );
}
