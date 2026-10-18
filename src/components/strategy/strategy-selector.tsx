"use client";

import { useMemo, useState, useEffect, useRef, useCallback } from "react";
import Link from "next/link";
import { OverlayChart, type OverlaySeries } from "@/components/strategy/overlay-chart";
import { useSortable, SortableTh, type SortColumn } from "@/components/sortable";
import { signColor } from "@/lib/verdict";
import { cn, formatPct, formatPrice } from "@/lib/utils";
import { AlertCircle } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  StrategyDetailSchema,
  type EquityPoint,
  type OpenPosition,
  type StrategyDetail,
  type Trade,
  type WealthPoint,
} from "@/lib/api-schemas";
import {
  STRATEGY_LABEL_KO,
  STRATEGY_DESC_KO,
  STRATEGY_GROUPS,
  BENCHMARK_KO,
} from "@/components/strategy/strategy-meta";

// ─── Types ────────────────────────────────────────────────────────────────────

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
  in_sample: { cagr_pct?: number | null; sharpe?: number | null; mdd_pct?: number | null };
  out_of_sample: { cagr_pct?: number | null; sharpe?: number | null; mdd_pct?: number | null };
  max_single_return_pct: number | null;
  best_trade_name: string | null;
  best_trade_ticker: string | null;
  top_decile_pnl_share_pct: number;
  trade_count: number;
  // v20: 백테스트가 생성한 데이터 주도 판정
  verdict?: string | null;
  verdict_reason?: string | null;
};

 type MultiStrategyData = {
   strategies: StrategyRow[];
   headline_key: string;
   curated_keys?: string[];
   detail_refs: Record<string, { path: string; content_hash: string; version?: string }>;
 };

type WealthSimGlobal = {
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


// ─── Helpers ─────────────────────────────────────────────────────────────────

function tickerSlugFromTrade(trade: Trade): string | null {
  if (!trade.ticker) return null;
  const market = trade.market ?? "KR";
  return `${market}-${trade.ticker}`.toLowerCase();
}

function fmt만(v: number) {
  if (v >= 100_000_000) return `${(v / 100_000_000).toFixed(1)}억원`;
  return `${Math.round(v / 10_000).toLocaleString("ko-KR")}만원`;
}

function fmtNav(v: number) {
  return v.toFixed(2);
}

// ─── 색 팔레트 — 전략별 고정·구분 가능한 색 (SOTA는 스탬프 적색) ──────────────
// 인덱스는 그룹 순서 기준으로 안정적 — 토글을 껐다 켜도 색이 바뀌지 않는다.
const PALETTE = [
  "#1971c2", "#2f9e44", "#9c36b5", "#f08c00", "#0c8599", "#e64980", "#5f3dc4",
  "#74b816", "#d6336c", "#1098ad", "#7048e8", "#c2255c", "#099268", "#3b5bdb",
  "#fd7e14", "#0ca678", "#845ef7", "#a61e4d", "#228be6", "#66a80f", "#fab005",
  "#15aabf", "#7950f2", "#fa5252", "#40c057", "#e8590c",
];

const BENCH_DEFS: { id: string; label: string; color: string; field: keyof WealthPoint }[] = [
  { id: "KOSPI",      label: "KOSPI DCA",  color: "#868e96", field: "KOSPI_value" },
  { id: "AllWeather", label: "올웨더 DCA",  color: "#f59e0b", field: "AllWeather_value" },
  { id: "SP500",      label: "S&P500 DCA", color: "#60a5fa", field: "SP500_value" },
  { id: "NASDAQ",     label: "NASDAQ DCA", color: "#34d399", field: "NASDAQ_value" },
];

// ─── Verdict chip (데이터 주도 — JSON verdict 필드 표시) ─────────────────────

function VerdictChip({ kind, reason }: { kind: string; reason?: string | null }) {
  const styles: Record<string, string> = {
    SOTA: "bg-stamp text-background",
    채택: "border border-up/50 bg-up/10 text-up",
    기각: "border border-down/50 bg-down/10 text-down",
    연구용: "border border-border bg-secondary/40 text-muted-foreground",
  };
  return (
    <span
      className={cn("inline-block rounded-sm px-1.5 py-0.5 font-mono text-[10px] font-bold cursor-help", styles[kind] ?? styles["연구용"])}
      title={reason ?? undefined}
    >
      {kind === "SOTA" ? "선택 기준 상위" : kind}
    </span>
  );
}

// ─── Trade columns definition ─────────────────────────────────────────────────

const TRADE_COLS: SortColumn<Trade>[] = [
  { key: "display_name", value: (t) => t.display_name ?? t.ticker },
  { key: "entry_date",   value: (t) => t.entry_date,   groupValue: (t) => t.entry_date?.slice(0, 7) ?? null },
  { key: "entry",        value: (t) => t.entry,        firstDir: "desc" },
  { key: "exit_date",    value: (t) => t.exit_date,    groupValue: (t) => t.exit_date?.slice(0, 7) ?? null },
  { key: "exit",         value: (t) => t.exit,         firstDir: "desc" },
  { key: "return_pct",   value: (t) => t.return_pct,   firstDir: "desc", groupValue: (t) => Math.floor(t.return_pct / 25) },
  { key: "days",         value: (t) => t.days,         firstDir: "desc", groupValue: (t) => Math.floor(t.days / 30) },
  { key: "entry_reason", value: (t) => t.entry_reason ?? "" },
  { key: "exit_reason",  value: (t) => t.exit_reason ?? "" },
];

const PAGE_SIZE = 20;

// ─── Open positions table ─────────────────────────────────────────────────────

function OpenPositionsTable({ positions, stratKey }: { positions: OpenPosition[]; stratKey: string }) {
  if (!positions.length) return null;
  return (
    <section className="rounded-lg border border-border bg-card p-5">
      <h3 className="mb-3 font-display text-xl font-black tracking-tight">
        보유 중 — {STRATEGY_LABEL_KO[stratKey] ?? stratKey} ({positions.length}종목)
      </h3>
      <p className="mb-2 text-xs text-muted-foreground">
        백테스트 마지막 날 기준 미청산 포지션. 동시 보유 20종목 한도로 신호 일부는 미체결.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] border-collapse text-sm">
          <thead>
            <tr className="border-b-4 border-double border-border font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
              <th className="px-3 py-2 text-left">종목</th>
              <th className="px-3 py-2 text-right">진입일</th>
              <th className="px-3 py-2 text-right">진입가</th>
              <th className="px-3 py-2 text-right">현재가</th>
              <th className="px-3 py-2 text-right">평가손익</th>
              <th className="px-3 py-2 text-right">스탑</th>
              <th className="px-3 py-2 text-right" title="ATR% Multiple from 50-MA — 8× 이상이면 과열 구간 (Minervini circle)">과열계수</th>
              <th className="px-3 py-2 text-right">커버</th>
            </tr>
          </thead>
          <tbody>
            {[...positions].sort((a, b) => b.return_pct - a.return_pct).map((pos) => {
              const market = pos.market ?? "KR";
              const slug = pos.ticker ? `${market}-${pos.ticker}`.toLowerCase() : null;
              const ext = pos.extension;
              const extColor = ext == null ? "" : ext >= 12 ? "text-down font-bold" : ext >= 8 ? "text-amber-500 font-bold" : ext >= 4 ? "text-foreground" : "text-muted-foreground";
              return (
                <tr key={pos.ticker} className="border-b border-dashed border-border last:border-b-0">
                  <td className="px-3 py-2" title={pos.entry_reason || undefined}>
                    {slug ? (
                      <Link href={`/stocks/${slug}`} className="hover:underline">
                        <p className="font-mono text-xs font-bold">{pos.display_name ?? pos.ticker}</p>
                      </Link>
                    ) : (
                      <p className="font-mono text-xs font-bold">{pos.display_name ?? pos.ticker}</p>
                    )}
                    <p className="font-mono text-[10px] text-muted-foreground">{pos.ticker}{market !== "KR" ? ` · ${market}` : ""}</p>
                    {pos.entry_reason && (
                      <p className="mt-0.5 max-w-[260px] truncate font-mono text-[10px] text-muted-foreground">{pos.entry_reason}</p>
                    )}
                  </td>
                  <td className="tnum px-3 py-2 text-right font-mono text-xs text-muted-foreground">{pos.entry_date}</td>
                  <td className="tnum px-3 py-2 text-right font-mono text-xs">{formatPrice(pos.entry, market)}</td>
                  <td className="tnum px-3 py-2 text-right font-mono text-xs">{formatPrice(pos.last_close, market)}</td>
                  <td className={cn("tnum px-3 py-2 text-right font-mono text-xs font-bold", signColor(pos.return_pct))}>
                    {formatPct(pos.return_pct, 1)}
                  </td>
                  <td className="tnum px-3 py-2 text-right font-mono text-xs text-muted-foreground">
                    {pos.stop && pos.stop > 0 ? formatPrice(pos.stop, market) : "—"}
                  </td>
                  <td className={cn("tnum px-3 py-2 text-right font-mono text-xs", extColor)}>
                    {ext != null ? `${ext.toFixed(1)}×` : "—"}
                  </td>
                  <td className="tnum px-3 py-2 text-right font-mono text-xs text-muted-foreground">
                    {pos.n_clubs ?? 1}개교
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[10px] text-muted-foreground">
        과열계수 = ATR% Multiple from 50-MA (B/A, A=ATR14/가격, B=(가격−50SMA)/50SMA). 8× 이상 황색, 12× 이상 적색 경보 — Minervini 커뮤니티 관행.
      </p>
    </section>
  );
}

// ─── Ticker history panel ─────────────────────────────────────────────────────

type Candle = { time: string; open: number; high: number; low: number; close: number };

/** Fetch /prices/{slug}.json → sorted candles; 404 = no data ([]), other failures reject (module-level cache) */
const _candleCache = new Map<string, Promise<Candle[]>>();
function fetchCandles(slug: string): Promise<Candle[]> {
  const hit = _candleCache.get(slug);
  if (hit) return hit;
  const p = fetch(`/prices/${slug}.json`).then(async (r) => {
    if (r.status === 404) return [];
    if (!r.ok) throw new Error(`prices ${r.status}`);
    const j = await r.json() as { candles?: Candle[] };
    const valid = (j.candles ?? []).filter(
      (c) => typeof c?.time === "string" && typeof c.close === "number" && isFinite(c.close),
    );
    valid.sort((a, b) => a.time.localeCompare(b.time));
    return valid.length >= 2 ? valid : [];
  });
  // Failed requests are not cached so the retry button can refetch.
  p.catch(() => _candleCache.delete(slug));
  _candleCache.set(slug, p);
  return p;
}

function TickerHistoryPanel({
  ticker,
  market,
  displayName,
  trades,
  onClose,
}: {
  ticker: string;
  market: string;
  displayName: string;
  trades: Trade[];
  onClose: () => void;
}) {
  const slug = `${market}-${ticker}`.toLowerCase();
  const [candleState, setCandleState] = useState<{ slug: string; candles: Candle[] | "error" } | null>(null);
  const [retry, setRetry] = useState(0);
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    let active = true;
    fetchCandles(slug).then(
      (value) => { if (active) setCandleState({ slug, candles: value }); },
      () => { if (active) setCandleState({ slug, candles: "error" }); },
    );
    return () => { active = false; };
  }, [slug, retry]);
  const loaded = candleState?.slug === slug ? candleState.candles : null;
  const candles = Array.isArray(loaded) ? loaded : null;

  // Sort trades oldest first for timeline display
  const sortedTrades = [...trades].sort((a, b) => a.entry_date.localeCompare(b.entry_date));

  // Mini price chart with trade markers
  const W = 600; const H = 120;
  const chartEl = (() => {
    if (!candles || !candles.length) return null;
    // Clip candles to range covering all trades
    const firstEntry = sortedTrades[0]?.entry_date;
    const lastExit = sortedTrades[sortedTrades.length - 1]?.exit_date;
    if (!firstEntry || !lastExit) return null;
    // Show 60 days before first entry to 30 days after last exit
    const startClip = new Date(firstEntry);
    startClip.setDate(startClip.getDate() - 60);
    const endClip = new Date(lastExit);
    endClip.setDate(endClip.getDate() + 30);
    const startStr = startClip.toISOString().slice(0, 10);
    const endStr = endClip.toISOString().slice(0, 10);
    const visible = candles.filter((c) => c.time >= startStr && c.time <= endStr);
    if (visible.length < 2) return null;

    const prices = visible.map((c) => c.close);
    const minP = Math.min(...prices);
    const maxP = Math.max(...prices);
    const px = (i: number) => (i / (visible.length - 1)) * W;
    const py = (p: number) => H - 4 - ((p - minP) / ((maxP - minP) || 1)) * (H - 12);
    const linePath = visible.map((c, i) => `${i ? "L" : "M"}${px(i).toFixed(1)},${py(c.close).toFixed(1)}`).join(" ");

    // Marker helpers: find index of candle closest to a date string
    const nearestIdx = (dateStr: string) => {
      let best = 0;
      let bestDiff = Infinity;
      visible.forEach((c, i) => {
        const diff = Math.abs(new Date(c.time).getTime() - new Date(dateStr).getTime());
        if (diff < bestDiff) { bestDiff = diff; best = i; }
      });
      return best;
    };

    return (
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full" aria-label={`${displayName} 가격 차트`}>
        <path d={linePath} fill="none" stroke="currentColor" strokeWidth="1.5" className="text-muted-foreground" strokeLinejoin="round" />
        {sortedTrades.map((t, ti) => {
          const ei = nearestIdx(t.entry_date);
          const xi = nearestIdx(t.exit_date);
          const ex_ = px(ei); const ey_ = py(visible[ei]?.close ?? minP);
          const xx = px(xi);  const xy  = py(visible[xi]?.close ?? minP);
          return (
            <g key={ti}>
              {/* Entry: red triangle up (▲ KR convention: buy=red) */}
              <polygon
                points={`${ex_},${ey_ - 8} ${ex_ - 5},${ey_} ${ex_ + 5},${ey_}`}
                className="fill-[hsl(var(--up))]"
                opacity="0.9"
              />
              {/* Exit: blue triangle down (▼ KR convention: sell=blue) */}
              <polygon
                points={`${xx},${xy + 8} ${xx - 5},${xy} ${xx + 5},${xy}`}
                className="fill-[hsl(var(--down))]"
                opacity="0.9"
              />
            </g>
          );
        })}
      </svg>
    );
  })();

  const avgReturn = sortedTrades.reduce((s, t) => s + t.return_pct, 0) / (sortedTrades.length || 1);

  return (
    <div className="rounded-lg border-2 border-stamp bg-card p-5 shadow-lg">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.2em] text-stamp">
            종목별 매매 이력
          </p>
          <h4 className="mt-0.5 font-display text-xl font-black tracking-tight">
            {displayName}
            <span className="ml-2 font-mono text-sm font-normal text-muted-foreground">{ticker}</span>
          </h4>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">
            총 {sortedTrades.length}회 거래 · 평균 수익률{" "}
            <span className={cn("font-bold", signColor(avgReturn))}>
              {formatPct(avgReturn, 1)}
            </span>
          </p>
        </div>
        <button
          onClick={onClose}
          className="rounded-md border border-border px-2.5 py-1 font-mono text-xs text-muted-foreground hover:border-stamp/40 hover:text-foreground transition-colors"
          aria-label="닫기"
        >
          ✕ 닫기
        </button>
      </div>

      {/* Price chart with trade markers */}
      <div className="mt-4">
        {loaded === null ? (
          <div role="status" aria-live="polite">
            <span className="sr-only">차트 로딩 중…</span>
            <Skeleton className="h-[120px] w-full" />
          </div>
        ) : loaded === "error" ? (
          <Alert variant="destructive">
            <AlertCircle />
            <AlertTitle>가격 데이터를 불러오지 못했습니다.</AlertTitle>
            <AlertDescription>
              <Button size="sm" variant="outline" onClick={() => { setCandleState(null); setRetry((n) => n + 1); }}>
                다시 시도
              </Button>
            </AlertDescription>
          </Alert>
        ) : chartEl ? (
          <>
            <p className="mb-1 font-mono text-[9px] text-muted-foreground">
              ▲ 매수(빨강)  ▼ 매도(파랑) — 진입 전후 60일 구간
            </p>
            {chartEl}
          </>
        ) : (
          <p className="font-mono text-xs text-muted-foreground">가격 데이터 없음</p>
        )}
      </div>

      {/* Trade timeline list */}
      <div className="mt-4 space-y-2">
        {sortedTrades.map((t, i) => (
          <div
            key={`${t.entry_date}-${i}`}
            className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-dashed border-border px-3 py-2"
          >
            <span className="font-mono text-[10px] text-muted-foreground w-5 shrink-0">#{i + 1}</span>
            <div className="flex items-center gap-1.5 font-mono text-xs">
              <span className="font-mono text-[9px] px-1 rounded bg-[hsl(var(--up)/0.15)] text-[hsl(var(--up))] font-bold">매수</span>
              <span className="text-muted-foreground">{t.entry_date}</span>
              <span className="font-bold">{typeof t.entry === "number" ? formatPrice(t.entry, t.market ?? "KR") : t.entry}</span>
            </div>
            <div className="flex items-center gap-1.5 font-mono text-xs">
              <span className="font-mono text-[9px] px-1 rounded bg-[hsl(var(--down)/0.15)] text-[hsl(var(--down))] font-bold">매도</span>
              <span className="text-muted-foreground">{t.exit_date}</span>
              <span className="font-bold">{typeof t.exit === "number" ? formatPrice(t.exit, t.market ?? "KR") : t.exit}</span>
            </div>
            <span className={cn("tnum font-mono text-xs font-bold ml-auto", signColor(t.return_pct))}>
              {formatPct(t.return_pct, 1)}
            </span>
            <span className="font-mono text-[10px] text-muted-foreground">{t.days}일</span>
            <span className="font-mono text-[10px] text-muted-foreground truncate max-w-[120px]" title={t.exit_reason}>{t.exit_reason}</span>
            {t.entry_reason && (
              <span className="basis-full font-mono text-[10px] text-muted-foreground">
                매수 사유: <span className="text-foreground">{t.entry_reason}</span>
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Trade log with sort + pagination ────────────────────────────────────────

function TradeLog({ trades, stratKey }: { trades: Trade[]; stratKey: string }) {
  const [page, setPage] = useState(0);
  // openHistoryTicker: the ticker whose history panel is currently open (cap 1)
  const [openHistoryTicker, setOpenHistoryTicker] = useState<string | null>(null);
  const { sorted, specs, toggle } = useSortable(trades, TRADE_COLS, [{ key: "exit_date", dir: "desc" }]);
  const totalPages = Math.ceil(sorted.length / PAGE_SIZE);
  const pageRows = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const sort = { specs, toggle };

  // Reset page and history panel when strategy changes
  const [lastKey, setLastKey] = useState(stratKey);
  if (lastKey !== stratKey) { setLastKey(stratKey); setPage(0); setOpenHistoryTicker(null); }

  if (!trades.length) return null;

  // Group all trades by ticker for the history panel
  const tradesByTicker: Record<string, Trade[]> = {};
  for (const t of trades) {
    if (!tradesByTicker[t.ticker]) tradesByTicker[t.ticker] = [];
    tradesByTicker[t.ticker].push(t);
  }

  // Find the open-panel ticker's info
  const openTicker = openHistoryTicker;
  const openTrades = openTicker ? (tradesByTicker[openTicker] ?? []) : [];
  const openDisplayName = openTrades[0]?.display_name ?? openTicker ?? "";
  const openMarket = openTrades[0]?.market ?? "KR";

  return (
    <section className="rounded-lg border border-border bg-card p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h3 className="font-display text-xl font-black tracking-tight">
          거래 로그 — {STRATEGY_LABEL_KO[stratKey] ?? stratKey} ({trades.length}건)
        </h3>
        <p className="font-mono text-[10px] text-muted-foreground">
          Shift+클릭: 다중 정렬 · ▸ 이력: 종목별 매매 이력 패널
        </p>
      </div>

      {/* Ticker history panel (capped to 1 open at a time) */}
      {openTicker && openTrades.length > 0 && (
        <div className="mt-4">
          <TickerHistoryPanel
            ticker={openTicker}
            market={openMarket}
            displayName={openDisplayName}
            trades={openTrades}
            onClose={() => setOpenHistoryTicker(null)}
          />
        </div>
      )}

      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[1020px] border-collapse text-sm">
          <thead>
            <tr className="border-b-4 border-double border-border font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
              <SortableTh sortKey="display_name" sort={sort} className="px-3 py-2 text-left">종목</SortableTh>
              <SortableTh sortKey="entry_date"   sort={sort} align="right" className="px-3 py-2">매수일</SortableTh>
              <SortableTh sortKey="entry"        sort={sort} align="right" className="px-3 py-2">매수가</SortableTh>
              <SortableTh sortKey="entry_reason" sort={sort} className="px-3 py-2">매수사유</SortableTh>
              <SortableTh sortKey="exit_date"    sort={sort} align="right" className="px-3 py-2">매도일</SortableTh>
              <SortableTh sortKey="exit"         sort={sort} align="right" className="px-3 py-2">매도가</SortableTh>
              <SortableTh sortKey="return_pct"   sort={sort} align="right" className="px-3 py-2">수익률</SortableTh>
              <SortableTh sortKey="days"         sort={sort} align="right" className="px-3 py-2">보유일</SortableTh>
              <SortableTh sortKey="exit_reason"  sort={sort} className="px-3 py-2">매도사유</SortableTh>
              <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">이력</th>
            </tr>
          </thead>
          <tbody>
            {pageRows.map((t, i) => {
              const slug = tickerSlugFromTrade(t);
              const isOpen = openHistoryTicker === t.ticker;
              const tickerTradeCount = tradesByTicker[t.ticker]?.length ?? 1;
              return (
                <tr key={`${t.ticker}-${t.entry_date}-${i}`} className={cn(
                  "border-b border-dashed border-border last:border-b-0",
                  isOpen && "bg-stamp/5"
                )}>
                  <td className="px-3 py-2">
                    {slug ? (
                      <Link href={`/stocks/${slug}`} className="hover:underline">
                        <p className="font-mono text-xs font-bold">{t.display_name ?? t.ticker}</p>
                      </Link>
                    ) : (
                      <p className="font-mono text-xs font-bold">{t.display_name ?? t.ticker}</p>
                    )}
                    <p className="font-mono text-[10px] text-muted-foreground">{t.ticker}{t.market !== "KR" ? ` · ${t.market}` : ""}</p>
                  </td>
                  <td className="tnum px-3 py-2 text-right font-mono text-xs text-muted-foreground">{t.entry_date}</td>
                  <td className="tnum px-3 py-2 text-right font-mono text-xs">{typeof t.entry === "number" ? formatPrice(t.entry, t.market ?? "KR") : t.entry}</td>
                  <td className="px-3 py-2 font-mono text-[10px] text-muted-foreground">
                    <span className="block max-w-[220px] truncate" title={t.entry_reason}>{t.entry_reason || "—"}</span>
                  </td>
                  <td className="tnum px-3 py-2 text-right font-mono text-xs text-muted-foreground">{t.exit_date}</td>
                  <td className="tnum px-3 py-2 text-right font-mono text-xs">{typeof t.exit === "number" ? formatPrice(t.exit, t.market ?? "KR") : t.exit}</td>
                  <td className={cn("tnum px-3 py-2 text-right font-mono text-xs font-bold", signColor(t.return_pct))}>
                    {formatPct(t.return_pct, 1)}
                  </td>
                  <td className="tnum px-3 py-2 text-right font-mono text-xs text-muted-foreground">{t.days}일</td>
                  <td className="px-3 py-2 font-mono text-[10px] text-muted-foreground">{t.exit_reason}</td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => setOpenHistoryTicker(isOpen ? null : t.ticker)}
                      className={cn(
                        "rounded px-2 py-0.5 font-mono text-[10px] font-bold transition-colors border",
                        isOpen
                          ? "border-stamp bg-stamp/10 text-stamp"
                          : "border-border bg-card text-muted-foreground hover:border-stamp/40 hover:text-foreground"
                      )}
                      title={`${t.display_name ?? t.ticker} 매매이력 (${tickerTradeCount}회)`}
                    >
                      {isOpen ? "▾" : "▸"} {tickerTradeCount > 1 ? `${tickerTradeCount}회` : "이력"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {totalPages > 1 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-dashed border-border pt-3">
          <span className="font-mono text-[10px] text-muted-foreground mr-1">페이지:</span>
          {Array.from({ length: totalPages }, (_, i) => (
            <button
              key={i}
              onClick={() => setPage(i)}
              className={cn(
                "tnum h-6 min-w-[24px] rounded px-1.5 font-mono text-[11px] font-bold transition-colors",
                i === page
                  ? "bg-stamp text-background"
                  : "border border-border bg-card text-muted-foreground hover:border-stamp/40 hover:text-foreground"
              )}
            >
              {i + 1}
            </button>
          ))}
          <span className="ml-1 font-mono text-[10px] text-muted-foreground">
            ({page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, sorted.length)} / {sorted.length})
          </span>
        </div>
      )}
    </section>
  );
}

// ─── Main component ────────────────────────────────────────────────────────────
// v20 UX: 단일 선택 탭 → 멀티 온/오프 오버레이 토글 + 포커스.
//   · 색 스와치 클릭 = 곡선 표시 토글 (자산곡선 + 적립 시뮬 동시 오버레이)
//   · 전략 이름 클릭 = 포커스 (상세 통계·보유 중·거래 로그·CSV 패널 바인딩, 곡선도 자동 ON)
//   · 벤치마크(KOSPI/올웨더 DCA 등)는 적립 시뮬 차트의 항상 가능한 토글

export function StrategySelector({
  multiStrategy,
  wealthSimGlobal,
  curatedKeys,
}: {
  multiStrategy: MultiStrategyData;
  wealthSimGlobal: WealthSimGlobal;
  /** 채택(셀렉터) 키 목록 — JSON multi_strategy.curated_keys (프리셋 버튼용) */
  curatedKeys?: string[];
}) {
  const headlineKey = multiStrategy.headline_key;
  const presentKeys = useMemo(() => new Set(multiStrategy.strategies.map((s) => s.key)), [multiStrategy.strategies]);

  // 그룹 순서대로 전 전략 나열 (그룹에 없는 신규 키는 "기타"로)
  const groups = useMemo(() => {
    const seen = new Set<string>();
    const g = STRATEGY_GROUPS.map((grp) => ({
      name: grp.name,
      keys: grp.keys.filter((k) => {
        if (!presentKeys.has(k)) return false;
        seen.add(k);
        return true;
      }),
    })).filter((grp) => grp.keys.length > 0);
    const leftovers = multiStrategy.strategies.map((s) => s.key).filter((k) => !seen.has(k));
    if (leftovers.length) g.push({ name: "기타", keys: leftovers });
    return g;
  }, [presentKeys, multiStrategy.strategies]);

  const allKeysOrdered = useMemo(() => groups.flatMap((g) => g.keys), [groups]);

  // 색 배정 — 그룹 순서 인덱스로 고정, SOTA는 스탬프 적색(차트에서 "stamp" 해석)
  const colorOf = useMemo(() => {
    const map = new Map<string, string>();
    allKeysOrdered.forEach((k, i) => map.set(k, k === headlineKey ? "stamp" : PALETTE[i % PALETTE.length]));
    return map;
  }, [allKeysOrdered, headlineKey]);

  // 기본 ON: SOTA + D + B (+ KOSPI DCA 벤치마크)
  const [enabled, setEnabled] = useState<Set<string>>(() => {
    const def = [headlineKey, "D_chandelier", "B_36mo"].filter((k) => presentKeys.has(k));
    return new Set(def);
  });
  const [benchOn, setBenchOn] = useState<Set<string>>(() => new Set(["KOSPI"]));
  const [focusKey, setFocusKey] = useState<string>(headlineKey);
  const [wealthLog, setWealthLog] = useState(true); // 자산곡선은 로그가 정직함
  const [details, setDetails] = useState<Record<string, StrategyDetail>>({});
  const [detailState, setDetailState] = useState<Record<string, "error">>({});
  const [detailRetry, setDetailRetry] = useState(0);
  const detailRequests = useRef(new Map<string, Promise<StrategyDetail>>());
  useEffect(() => {
    let active = true;
    for (const key of new Set([...enabled, focusKey])) {
      const ref = multiStrategy.detail_refs[key];
      const requestKey = `${key}:${ref?.content_hash ?? "missing"}`;
      let request = detailRequests.current.get(requestKey);
      if (!request) {
        request = Promise.resolve().then(async () => {
          if (!ref) throw new Error("detail reference missing");
          // No force-cache: a cached 404 would make the retry button useless; hash URLs are immutable anyway.
          const response = await fetch(ref.path);
          if (!response.ok) throw new Error(`detail ${response.status}`);
          const parsed = StrategyDetailSchema.safeParse(await response.json());
          if (!parsed.success) throw new Error("detail schema mismatch");
          if (parsed.data.content_hash !== ref.content_hash) throw new Error("detail version mismatch");
          return parsed.data;
        });
        detailRequests.current.set(requestKey, request);
      }
      request.then((value) => {
        if (active) {
          setDetails((previous) => previous[key]?.content_hash === value.content_hash ? previous : { ...previous, [key]: value });
          setDetailState((previous) => {
            if (!(key in previous)) return previous;
            const next = { ...previous };
            delete next[key];
            return next;
          });
        }
      }).catch(() => {
        detailRequests.current.delete(requestKey);
        if (active) setDetailState((previous) => ({ ...previous, [key]: "error" }));
      });
    }
    return () => { active = false; };
  }, [enabled, focusKey, multiStrategy.detail_refs, detailRetry]);
  const matchingDetail = useCallback((key: string): StrategyDetail | undefined => {
    const detail = details[key];
    const ref = multiStrategy.detail_refs[key];
    return detail && ref && detail.content_hash === ref.content_hash ? detail : undefined;
  }, [details, multiStrategy.detail_refs]);

  const toggleStrategy = (key: string) => {
    setEnabled((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const focusStrategy = (key: string) => {
    setFocusKey(key);
    setEnabled((prev) => (prev.has(key) ? prev : new Set(prev).add(key)));
  };
  const toggleBench = (id: string) => {
    setBenchOn((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selected =
    multiStrategy.strategies.find((s) => s.key === focusKey) ??
    multiStrategy.strategies.find((s) => s.key === headlineKey)!;
  const wealthSim = matchingDetail(focusKey)?.wealth;
  const yearly = matchingDetail(focusKey)?.yearly ?? [];
  const tradesRaw = matchingDetail(focusKey)?.trades ?? [];
  const trades = tradesRaw.filter((t) => !t.exit_reason?.endsWith("미청산"));
  const openPositions: OpenPosition[] = matchingDetail(focusKey)?.open_positions ?? [];

  const isHeadline = focusKey === headlineKey;
  const oosBoundaryYear = 2024;
  const oosBoundary = `${oosBoundaryYear}-01-01`;

  // ── 오버레이 시리즈 (memo — OverlayChart effect 재실행 최소화) ──────────────
  const equitySeries: OverlaySeries[] = useMemo(() => {
    return allKeysOrdered
      .filter((k) => enabled.has(k))
      .map((k) => ({
        id: k,
        label: STRATEGY_LABEL_KO[k] ?? k,
        color: colorOf.get(k) ?? "#888888",
        width: (k === focusKey ? 3 : k === headlineKey ? 2 : 1) as 1 | 2 | 3,
        data: (matchingDetail(k)?.nav ?? []).map((p) => ({ time: p.date, value: p.nav })),
      }))
      .filter((s) => s.data.length >= 2);
  }, [allKeysOrdered, enabled, colorOf, focusKey, headlineKey, matchingDetail]);

  const wealthSeries: OverlaySeries[] = useMemo(() => {
    const out: OverlaySeries[] = [];
    // 납입금 — 항상 표시 (점선)
    out.push({
      id: "_contributed",
      label: "납입금",
      color: "#adb5bd",
      dashed: true,
      width: 1,
      data: wealthSimGlobal.series.map((p) => ({ time: p.date, value: p.contributed })),
    });
    // 벤치마크 DCA 토글
    for (const b of BENCH_DEFS) {
      if (!benchOn.has(b.id)) continue;
      const data = wealthSimGlobal.series
        .filter((p) => typeof p[b.field] === "number")
        .map((p) => ({ time: p.date, value: p[b.field] as number }));
      if (data.length >= 2) out.push({ id: `_bench_${b.id}`, label: b.label, color: b.color, dashed: true, width: 1, data });
    }
    // 전략들
    for (const k of allKeysOrdered) {
      if (!enabled.has(k)) continue;
      const sim = matchingDetail(k)?.wealth;
      if (!sim) continue;
      out.push({
        id: k,
        label: STRATEGY_LABEL_KO[k] ?? k,
        color: colorOf.get(k) ?? "#888888",
        width: (k === focusKey ? 3 : k === headlineKey ? 2 : 1) as 1 | 2 | 3,
        data: sim.series.map((p) => ({ time: p.date, value: p.strategy_value })),
      });
    }
    return out.filter((s) => s.data.length >= 2);
  }, [allKeysOrdered, enabled, benchOn, colorOf, focusKey, headlineKey, matchingDetail, wealthSimGlobal.series]);

  const m = selected.metrics;
  const is = selected.in_sample;
  const oos = selected.out_of_sample;
  const curatedSet = useMemo(() => new Set(curatedKeys ?? []), [curatedKeys]);
  const failedDetailKeys = [focusKey, ...Array.from(enabled).filter((key) => key !== focusKey)]
    .filter((key) => detailState[key] === "error");

  return (
    <div className="space-y-6">
      <div role="status" aria-live="polite" className="font-mono text-xs text-muted-foreground empty:hidden">
        {!matchingDetail(focusKey) && detailState[focusKey] !== "error" && "전략 상세를 불러오는 중…"}
      </div>
      {failedDetailKeys.length > 0 && (
        <Alert variant="destructive">
          <AlertCircle />
          <AlertTitle>전략 상세를 불러오지 못했습니다.</AlertTitle>
          <AlertDescription>
            <p>
              {failedDetailKeys.map((key) => STRATEGY_LABEL_KO[key] ?? key).join(", ")} — 버전이 일치하는 게시 자산이 필요합니다.
            </p>
            <Button size="sm" variant="outline" onClick={() => { setDetailState({}); setDetailRetry((n) => n + 1); }}>
              다시 시도
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {/* ── 전략 토글 보드 — 전 전략 멀티 온/오프 + 포커스 ───────────── */}
      <div>
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
          <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">
            전략 오버레이 — 색칩 클릭 = 곡선 토글 · 이름 클릭 = 포커스(상세)
          </p>
          <div className="flex gap-1.5">
            <button
              onClick={() => {
                setEnabled(new Set([headlineKey, "D_chandelier", "B_36mo"].filter((k) => presentKeys.has(k))));
                setBenchOn(new Set(["KOSPI"]));
              }}
              className="rounded border border-border px-2 py-0.5 font-mono text-[10px] text-muted-foreground hover:border-stamp/40 hover:text-foreground transition-colors"
            >
              기본값
            </button>
            <button
              onClick={() => setEnabled(new Set((curatedKeys ?? []).filter((k) => presentKeys.has(k))))}
              className="rounded border border-border px-2 py-0.5 font-mono text-[10px] text-muted-foreground hover:border-stamp/40 hover:text-foreground transition-colors"
            >
              채택만
            </button>
            <button
              onClick={() => setEnabled(new Set())}
              className="rounded border border-border px-2 py-0.5 font-mono text-[10px] text-muted-foreground hover:border-stamp/40 hover:text-foreground transition-colors"
            >
              전체 해제
            </button>
          </div>
        </div>

        <div className="space-y-2.5">
          {groups.map((grp) => (
            <div key={grp.name} className="flex flex-wrap items-center gap-1.5">
              <span className="w-14 shrink-0 font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted-foreground">
                {grp.name}
              </span>
              {grp.keys.map((key) => {
                const row = multiStrategy.strategies.find((s) => s.key === key);
                const on = enabled.has(key);
                const isFocus = key === focusKey;
                const isHL = key === headlineKey;
                const color = colorOf.get(key);
                return (
                  <div
                    key={key}
                    className={cn(
                      "flex items-center overflow-hidden rounded-md border transition-all",
                      isFocus ? "border-stamp shadow-sm" : on ? "border-border bg-card" : "border-border/60 bg-card opacity-60 hover:opacity-100",
                    )}
                  >
                    <button
                      onClick={() => toggleStrategy(key)}
                      aria-pressed={on}
                      title={`${STRATEGY_LABEL_KO[key] ?? key} 곡선 ${on ? "끄기" : "켜기"}`}
                      className="flex h-full items-center self-stretch border-r border-border/60 px-1.5 py-1.5 hover:bg-secondary/40 transition-colors"
                    >
                      <span
                        className={cn(
                          "inline-block h-3 w-3 rounded-sm",
                          on && isHL && "bg-stamp",
                          !on && "border border-dashed",
                          !on && isHL && "border-stamp",
                        )}
                        style={on && !isHL ? { backgroundColor: color } : !on && !isHL ? { borderColor: color } : undefined}
                      />
                    </button>
                    <button
                      onClick={() => focusStrategy(key)}
                      title={`${row?.verdict ?? ""} ${row?.verdict_reason ?? ""}`.trim() || undefined}
                      className={cn("px-2 py-1.5 text-left hover:bg-stamp/5 transition-colors", isFocus && "bg-stamp/10")}
                    >
                      <span className="flex items-center gap-1 font-mono text-[11px] font-bold leading-none">
                        {STRATEGY_LABEL_KO[key] ?? key}
                        {isHL && (
                          <span className="rounded-sm bg-stamp px-1 py-px font-mono text-[8px] font-black tracking-wider text-background">
                            선택 기준 상위
                          </span>
                        )}
                      </span>
                      <span className="tnum mt-0.5 block font-mono text-[9px] text-muted-foreground">
                        IS {row?.in_sample.sharpe ?? "—"} · OOS {row?.out_of_sample.sharpe ?? "—"}
                      </span>
                    </button>
                  </div>
                );
              })}
            </div>
          ))}
          {/* 벤치마크 토글 — 적립 시뮬 차트에 점선으로 */}
          <div className="flex flex-wrap items-center gap-1.5 border-t border-dashed border-border pt-2">
            <span className="w-14 shrink-0 font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted-foreground">
              벤치마크
            </span>
            {BENCH_DEFS.map((b) => {
              const on = benchOn.has(b.id);
              return (
                <button
                  key={b.id}
                  onClick={() => toggleBench(b.id)}
                  aria-pressed={on}
                  className={cn(
                    "flex items-center gap-1.5 rounded-md border px-2 py-1.5 font-mono text-[11px] font-bold transition-all",
                    on ? "border-border bg-card" : "border-border/60 bg-card opacity-60 hover:opacity-100",
                  )}
                >
                  <span
                    className={cn("inline-block h-3 w-3 rounded-sm", !on && "border border-dashed")}
                    style={on ? { backgroundColor: b.color } : { borderColor: b.color }}
                  />
                  {b.label}
                </button>
              );
            })}
            <span className="font-mono text-[9px] text-muted-foreground">— 월 적립 시뮬 차트에 표시</span>
          </div>
        </div>
        <p className="mt-2 font-mono text-[10px] text-muted-foreground">
          켜진 곡선 {enabled.size}개 · 판정(선택 기준 상위/채택/연구용/기각)은 백테스트 실행 시점 수치로 생성 — 칩에 마우스를 올리면 사유 표시
        </p>
      </div>

      {/* ── 포커스 전략 hero ──────────────────────────────────── */}
      <div className={cn(
        "rounded-lg border-2 p-5",
        isHeadline ? "border-stamp bg-stamp/5" : "border-border bg-card"
      )}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.25em] text-stamp">
              포커스 전략 {isHeadline ? "— 선택 기준 상위 (타이브레이크 1위)" : ""}
            </p>
            <h3 className="mt-0.5 flex items-center gap-2 font-display text-2xl font-black tracking-tight">
              {STRATEGY_LABEL_KO[focusKey] ?? focusKey}
              {selected.verdict && <VerdictChip kind={selected.verdict} reason={selected.verdict_reason} />}
            </h3>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
              {STRATEGY_DESC_KO[focusKey] ?? ""}
            </p>
            {selected.verdict_reason && (
              <p className="mt-1 max-w-2xl font-mono text-[11px] text-muted-foreground">
                판정 사유: <span className="text-foreground">{selected.verdict_reason}</span>
              </p>
            )}
          </div>
          <a
            href={`/strategy-trades-${focusKey}.csv`}
            download={`strategy-trades-${focusKey}.csv`}
            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-secondary px-3 py-1.5 font-mono text-xs font-semibold hover:bg-secondary/70 transition-colors"
          >
            CSV 다운로드 ({selected.trade_count}건)
          </a>
        </div>

        {/* Stats strip */}
        <dl className="mt-4 grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-4 lg:grid-cols-7">
          {[
            { label: "IS CAGR",   value: is.cagr_pct  != null ? formatPct(is.cagr_pct,  1) : "—", tone: signColor(is.cagr_pct) },
            { label: "IS 샤프",   value: String(is.sharpe  ?? "—"), tone: "" },
            { label: "IS MDD",    value: is.mdd_pct   != null ? formatPct(is.mdd_pct,   1) : "—", tone: "text-down" },
            { label: "OOS CAGR",  value: oos.cagr_pct != null ? formatPct(oos.cagr_pct, 1) : "—", tone: signColor(oos.cagr_pct) },
            { label: "OOS 샤프",  value: String(oos.sharpe ?? "—"), tone: "" },
            { label: "최대 1종목", value: m.max_single_return_pct != null ? formatPct(m.max_single_return_pct, 0) : "—", tone: signColor(m.max_single_return_pct) },
            { label: "승률",      value: m.win_rate_pct != null ? `${m.win_rate_pct}%` : "—", tone: "" },
          ].map((item) => (
            <div key={item.label} className="bg-card px-3 py-2.5">
              <dt className="font-mono text-[9px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">{item.label}</dt>
              <dd className={cn("tnum mt-1 font-display text-lg font-black tracking-tight", item.tone)}>{item.value}</dd>
            </div>
          ))}
        </dl>

        {selected.best_trade_name && selected.max_single_return_pct != null && selected.max_single_return_pct > 0 && (
          <p className="mt-3 font-mono text-xs text-muted-foreground">
            최대 단일 수익:{" "}
            <strong className={cn("font-bold", signColor(selected.max_single_return_pct))}>
              {selected.best_trade_name} {formatPct(selected.max_single_return_pct, 0)}
            </strong>
            {curatedSet.size > 0 && !curatedSet.has(focusKey) && (
              <span className="ml-3 text-muted-foreground">· 이 전략은 셀렉터 비채택 — 사유는 판정 칩 참조</span>
            )}
          </p>
        )}
      </div>

      {/* ── 자산 곡선 오버레이 (lightweight-charts) ───────────────────── */}
      <section className="rounded-lg border border-border bg-card p-5">
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
          <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">
            자산 곡선 오버레이 — NAV (시작=1.00) · 켜진 전략 {equitySeries.length}개
          </p>
          <p className="font-mono text-[10px] text-muted-foreground">크로스헤어를 올리면 전체 시리즈 값 표시 · 2024-01부터 OOS</p>
        </div>
        {equitySeries.length > 0 ? (
          <OverlayChart
            series={equitySeries}
            valueFormatter={fmtNav}
            oosBoundary={oosBoundary}
            heightClass="h-[320px]"
            ariaLabel="전략별 자산 곡선 오버레이 차트"
          />
        ) : (
          <p className="py-8 text-center font-mono text-xs text-muted-foreground">켜진 전략이 없습니다 — 위에서 색칩을 눌러 곡선을 켜세요.</p>
        )}
        {/* 연도별 수익률 — 포커스 전략 */}
        {yearly.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-dashed border-border pt-3">
            <span className="font-mono text-[10px] text-muted-foreground">연도별 ({STRATEGY_LABEL_KO[focusKey] ?? focusKey}):</span>
            {yearly.map((row) => (
              <div
                key={row.year}
                className={cn(
                  "rounded-md border border-border px-2.5 py-1 text-center",
                  row.year >= oosBoundaryYear && "border-stamp/50 bg-stamp/5"
                )}
              >
                <p className="font-mono text-[9px] text-muted-foreground">
                  {row.year}{row.year >= oosBoundaryYear ? " OOS" : ""}
                </p>
                <p className={cn("tnum font-mono text-xs font-bold", signColor(row.return_pct))}>
                  {formatPct(row.return_pct, 1)}
                </p>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ── 월 적립 시뮬레이션 오버레이 ─────────────────────────── */}
      <section className="rounded-lg border border-border bg-card p-5">
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="font-display text-xl font-black tracking-tight">
            월 적립 시뮬레이션 오버레이
          </h3>
          <button
            onClick={() => setWealthLog((v) => !v)}
            aria-pressed={wealthLog}
            className={cn(
              "rounded border px-2 py-0.5 font-mono text-[10px] font-bold transition-colors",
              wealthLog ? "border-stamp bg-stamp/10 text-stamp" : "border-border text-muted-foreground hover:text-foreground",
            )}
            title="자산곡선은 로그가 정직함 — 복리 구간의 기울기가 수익률"
          >
            로그 스케일 {wealthLog ? "ON" : "OFF"}
          </button>
        </div>
        <p className="text-xs text-muted-foreground">{wealthSimGlobal.schedule_desc}</p>

        {wealthSim && (
          <>
            <dl className="mt-3 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3 lg:grid-cols-6">
              {[
                { label: "총 납입금",     value: fmt만(wealthSimGlobal.final_contributed), tone: "" },
                { label: `${STRATEGY_LABEL_KO[focusKey] ?? focusKey} 최종`, value: fmt만(wealthSim.final_strategy_value), tone: signColor(wealthSim.final_strategy_value - wealthSimGlobal.final_contributed) },
                ...Object.entries(wealthSimGlobal.final_benchmark_values).map(([k, v]) => ({
                  label: `${BENCHMARK_KO[k] ?? k} 벤치마크`,
                  value: fmt만(v),
                  tone: signColor(v - wealthSimGlobal.final_contributed),
                })),
              ].map((item) => (
                <div key={item.label} className="bg-card px-3 py-2.5">
                  <dt className="font-mono text-[9px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">{item.label}</dt>
                  <dd className={cn("tnum mt-1 font-display text-lg font-black tracking-tight", item.tone)}>{item.value}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-3 flex flex-wrap gap-2">
              <div className="rounded-lg border border-stamp/60 bg-stamp/5 px-3 py-1.5">
                <p className="font-mono text-[9px] text-muted-foreground">{STRATEGY_LABEL_KO[focusKey] ?? focusKey} (납입 대비)</p>
                <p className={cn("tnum font-display text-xl font-black", signColor(wealthSim.strategy_gain_on_contributed_pct))}>
                  {formatPct(wealthSim.strategy_gain_on_contributed_pct, 1)}
                </p>
              </div>
              {Object.entries(wealthSimGlobal.benchmark_gain_on_contributed_pct).map(([k, v]) => (
                <div key={k} className="rounded-lg border border-border bg-secondary/20 px-3 py-1.5">
                  <p className="font-mono text-[9px] text-muted-foreground">{BENCHMARK_KO[k] ?? k}</p>
                  <p className={cn("tnum font-display text-xl font-black", signColor(v))}>{formatPct(v, 1)}</p>
                </div>
              ))}
            </div>
          </>
        )}

        <div className="mt-4">
          {wealthSeries.length > 1 ? (
            <OverlayChart
              series={wealthSeries}
              valueFormatter={fmt만}
              logScale={wealthLog}
              heightClass="h-[340px]"
              ariaLabel="월 적립 시뮬레이션 오버레이 차트 — 전략·벤치마크·납입금"
            />
          ) : (
            <p className="py-8 text-center font-mono text-xs text-muted-foreground">켜진 전략/벤치마크가 없습니다.</p>
          )}
        </div>
        <p className="mt-2 font-mono text-[10px] text-muted-foreground">
          {wealthSimGlobal.fx_assumption}
        </p>
      </section>

      {/* ── 보유 중 (포커스 전략) ───────────────────────────────── */}
      <OpenPositionsTable positions={openPositions} stratKey={focusKey} />

      {/* ── 거래 로그 (포커스 전략) ─────────────────────────────── */}
      <TradeLog trades={trades} stratKey={focusKey} />
    </div>
  );
}
