import type { FundedAccount, FundedFill, FundedPlan } from "@/lib/funded-account";
import type { z } from "zod";
import type { PaperPortfolioSchema } from "@/lib/paper-portfolio";

// One reading order for both paper accounts: today's valuation → what changed → next-open actions with
// reasons → expected cash and weights → execution log. Pure mapping from the validated Gold artifacts;
// nothing here invents a number the artifact does not carry. Estimates are labelled as estimates.

type PaperPortfolio = z.infer<typeof PaperPortfolioSchema>;

export type Basis = "estimate" | "fixed";
export type FlowAction = {
  side: "buy" | "sell" | "hold";
  id: string;
  name: string;
  market: "KR" | "US" | null;
  shares: { value: number; basis: Basis } | null;
  amountKrw: { value: number; basis: Basis } | null;
  currentWeightPct: number;
  plannedWeightPct: number | null;
  reason: string;
  conditions: string[];
};
export type FlowPlan = {
  key: string;
  title: string;
  /** null = the artifact does not fix the session (paper engine: each market's next available open) */
  tradingDay: string | null;
  frozenAt: string | null;
  basis: string;
  status: "awaiting_open" | "executed" | "cancelled" | "waiting_signal";
  actions: FlowAction[];
  excluded: { name: string; reason: string }[];
  noTradeReason: string | null;
  contributionsKrw: number;
  cashAfterKrw: { value: number; basis: Basis } | null;
  notes: string[];
};
export type FlowLogRow = {
  key: string;
  date: string;
  market: "KR" | "US";
  name: string;
  side: "buy" | "sell";
  filled: boolean;
  price: number | null;
  shares: number | null;
  amountKrw: number;
  feesKrw: number;
  note: string;
};
export type FlowSegment = {
  key: "backtest" | "live" | "combined";
  label: string;
  period: string;
  principalKrw: number;
  navKrw: number;
  pnlKrw: number;
  twrPct: number;
  note: string;
};
export type AccountFlow = {
  asOf: string;
  /** Backtest vs live vs combined, never merged without a label. null = single live segment only. */
  segments: FlowSegment[] | null;
  marks: { KR: string | null; US: string | null } | null;
  valuation: {
    navKrw: number;
    principalKrw: number;
    initialKrw: number;
    contributionsKrw: number;
    investmentPnlKrw: number;
    twrPct: number;
    xirrPct: number | null;
    cashKrw: number;
    cashLabel: string;
    cashWeightPct: number;
    feesKrw: number;
    /** Says what the totals cover when they include a seeded backtest. */
    scopeNote: string | null;
    change: { navKrw: number; principalKrw: number; pnlKrw: number; since: string } | null;
  };
  changes: string[];
  plans: FlowPlan[];
  weights: { id: string; name: string; currentPct: number; plannedPct: number | null }[];
  weightsNote: string;
  log: FlowLogRow[];
  issues: string[];
};

const won = (v: number) => `${Math.round(v).toLocaleString("ko-KR")}원`;
const PARKING_ID = "INDEX:AllWeather";

const PAPER_REASON: Record<string, string> = { prior_close_stop: "전일 종가가 샹들리에 스탑 아래 — 다음 시가 청산" };

export function paperFlow(d: PaperPortfolio): AccountFlow {
  const prev = d.history.length > 1 ? d.history[d.history.length - 2] : null;
  const last = d.history[d.history.length - 1];
  const parked = d.positions.find((p) => p.id === PARKING_ID);
  const stocks = d.positions.filter((p) => p.id !== PARKING_ID);
  const todays = d.fills.filter((f) => f.date === d.as_of);

  const changes: string[] = [];
  if (!prev) changes.push(`첫 평가(${d.as_of}) — 비교할 이전 평가가 없습니다.`);
  else changes.push(`평가액 ${won(prev.nav_krw)} → ${won(last.nav_krw)} (${prev.as_of} 대비).`);
  if (todays.length) {
    const buys = todays.filter((f) => f.side === "buy").length;
    changes.push(`${d.as_of} 시가 체결 — 매수 ${buys}건 · 매도 ${todays.length - buys}건.`);
  } else changes.push(`${d.as_of}에 체결된 주문이 없습니다.`);
  if (d.status === "awaiting_next_open") changes.push(`기록 시작 ${d.started_at.slice(0, 10)} 이후 아직 시장별 다음 시가가 오지 않아 현금 100%입니다.`);

  const budget = d.nav_krw * d.policy.position_weight;
  const slots = d.policy.max_positions - stocks.length;
  const buys: FlowAction[] = d.target_positions.map((t) => ({
    side: "buy",
    id: t.id,
    name: t.name,
    market: t.id.startsWith("US:") ? "US" : t.id.startsWith("KR:") ? "KR" : null,
    shares: null,
    amountKrw: { value: budget, basis: "estimate" },
    currentWeightPct: 0,
    plannedWeightPct: t.weight_pct,
    reason: "학회 매수 신호 관측 후 진입 대기 — 해당 시장의 다음 가용 시가에 기본 NAV 5%로 진입",
    // Same for every candidate, so stated once in the plan notes instead of on each row.
    conditions: [],
  }));
  const holds: FlowAction[] = d.positions.map((p) => ({
    side: "hold",
    id: p.id,
    name: p.name,
    market: p.id.startsWith("US:") ? "US" : p.id.startsWith("KR:") ? "KR" : null,
    shares: null,
    amountKrw: { value: p.value_krw, basis: "fixed" },
    currentWeightPct: p.weight_pct,
    plannedWeightPct: null,
    reason: p.id === PARKING_ID
      ? "미투자 잔액 파킹 — 시가가 없는 합성지수 프록시"
      : "보유 유지 — 종가 스탑 이탈은 다음 완료 종가에서 판정 (이 산출물에 스탑 수준 없음)",
    conditions: [],
  }));
  const plannedBuyPct = buys.reduce((a, b) => a + (b.plannedWeightPct ?? 0), 0);
  const idlePct = d.cash_weight_pct + (parked?.weight_pct ?? 0);
  const estCost = buys.length * budget * d.policy.cost_per_side;

  const plan: FlowPlan = {
    key: "paper-next-open",
    title: "다음 시가 (시장별 다음 가용 세션)",
    tradingDay: null,
    frozenAt: null,
    basis: `${d.as_of} 완료 일봉 기준`,
    status: buys.length ? "awaiting_open" : "waiting_signal",
    actions: [...buys, ...holds],
    excluded: [],
    noTradeReason: buys.length
      ? null
      : "대기 중인 진입 신호가 없습니다 — 새 학회 매수 리포트가 관측되지 않았습니다. 종가 스탑 이탈 매도는 다음 완료 종가에서 판정되어 그다음 시가에 실행됩니다.",
    contributionsKrw: 0,
    cashAfterKrw: buys.length ? { value: Math.max(0, d.nav_krw * (idlePct - plannedBuyPct) / 100 - estCost), basis: "estimate" } : null,
    notes: [
      "이 계정은 W 엔진의 소수 주식 모형이라 주문 수량 대신 금액(추정 NAV × 5%)을 보여 줍니다.",
      "모든 매수 후보는 슬롯·잔고·시가 갭에 따라 체결되지 않거나 금액이 달라질 수 있습니다.",
      ...(buys.length > slots ? [`대기 신호 ${buys.length}건 > 남은 슬롯 ${slots}개 — 일부는 체결되지 않습니다.`] : []),
      "매도 계획은 산출물에 없습니다: 스탑 판정은 다음 완료 종가 이후에만 정해집니다.",
    ],
  };

  const weights = [
    ...stocks.map((p) => ({ id: p.id, name: p.name, currentPct: p.weight_pct, plannedPct: p.weight_pct })),
    ...buys.map((b) => ({ id: b.id, name: b.name, currentPct: 0, plannedPct: b.plannedWeightPct })),
    {
      id: "idle",
      name: d.status === "awaiting_next_open" ? "현금 → 첫 시가 이후 올웨더 파킹" : "올웨더 파킹 · 현금",
      currentPct: idlePct,
      plannedPct: Math.max(0, idlePct - plannedBuyPct),
    },
  ];

  return {
    asOf: d.as_of,
    segments: null,
    marks: null,
    valuation: {
      navKrw: d.nav_krw,
      principalKrw: d.initial_capital_krw,
      initialKrw: d.initial_capital_krw,
      contributionsKrw: 0,
      investmentPnlKrw: d.nav_krw - d.initial_capital_krw,
      twrPct: d.return_pct,
      xirrPct: null,
      cashKrw: d.cash_krw + (parked?.value_krw ?? 0),
      cashLabel: parked ? "파킹 · 현금" : "현금",
      cashWeightPct: idlePct,
      feesKrw: d.fees_krw,
      scopeNote: null,
      change: prev ? { navKrw: last.nav_krw - prev.nav_krw, principalKrw: 0, pnlKrw: last.nav_krw - prev.nav_krw, since: prev.as_of } : null,
    },
    changes,
    plans: [plan],
    weights,
    weightsNote: `추정 — 대기 신호마다 기본 진입 비중 ${(d.policy.position_weight * 100).toFixed(0)}%를 더한 값. 시가 갭·슬롯(최대 ${d.policy.max_positions})·비용 ${(d.policy.cost_per_side * 100).toFixed(1)}%에 따라 달라집니다.`,
    log: [...d.fills].reverse().map((f, i) => ({
      key: `${f.date}-${f.ticker}-${i}`,
      date: f.date,
      market: f.market,
      name: f.ticker,
      side: f.side,
      filled: true,
      price: f.price,
      shares: f.shares,
      amountKrw: f.gross_krw,
      feesKrw: f.fees_krw,
      note: PAPER_REASON[f.reason] ?? `신규 리포트 진입 (${f.reason})`,
    })),
    issues: d.data_issues,
  };
}

const UNFILLED: Record<NonNullable<FundedFill["reason_code"]>, string> = {
  no_position: "보유 없음",
  halt_or_missing_open: "거래정지·시가 없음",
  missing_fx: "환율 없음",
  already_held: "이미 보유",
  slots_full: "슬롯 30개 가득",
  insufficient_cash: "현금 부족(예산 50% 미만)",
  below_one_share_at_open: "시가 기준 1주 미만",
};

function fundedPlan(plan: FundedPlan, account: FundedAccount): FlowPlan {
  const sells = new Set(plan.orders.filter((o) => o.side === "sell").map((o) => o.ticker));
  const actions: FlowAction[] = plan.orders.map((o) => ({
    side: o.side,
    id: o.instrument.symbol,
    name: o.name,
    market: o.market,
    // Sell quantity is the whole held position (fixed); buy quantity is re-computed at the real open.
    shares: { value: o.est_shares, basis: o.side === "sell" ? "fixed" : "estimate" },
    amountKrw: { value: o.est_gross_krw, basis: "estimate" },
    currentWeightPct: o.current_weight_pct,
    plannedWeightPct: o.planned_weight_pct,
    reason: o.reason,
    conditions: o.conditions,
  }));
  for (const p of account.positions.filter((x) => x.market === plan.market && !sells.has(x.ticker))) {
    actions.push({
      side: "hold",
      id: p.instrument.symbol,
      name: p.name,
      market: p.market,
      shares: { value: p.shares, basis: "fixed" },
      amountKrw: { value: p.value_krw, basis: "fixed" },
      currentWeightPct: p.weight_pct,
      plannedWeightPct: null,
      reason: p.halted
        ? "거래정지 — 시세 재개 전까지 평가 보류"
        : p.pending_exit
          ? "청산 대기 — 이전 계획의 매도가 아직 체결되지 않음"
          : `종가 ${p.last_close.toLocaleString("ko-KR")} ≥ 샹들리에 스탑 ${p.stop.toLocaleString("ko-KR", { maximumFractionDigits: 2 })} — 유지`,
      conditions: [],
    });
  }
  const contributionsKrw = plan.contributions.reduce((a, c) => a + c.amount_krw, 0);
  return {
    key: plan.plan_id,
    title: `${plan.market === "KR" ? "KRX" : "미국"} ${plan.trading_day} 시가`,
    tradingDay: plan.trading_day,
    frozenAt: plan.frozen_at,
    basis: `${plan.basis_session} 종가 기준 · r${String(plan.revision).padStart(2, "0")}${plan.revision_reason ? ` (${plan.revision_reason})` : ""}`,
    status: plan.outcome,
    actions,
    excluded: plan.excluded.map((e) => ({ name: e.name, reason: e.reason })),
    noTradeReason: plan.orders.length ? null : (plan.no_trade_reason ?? "주문 없음"),
    contributionsKrw,
    // An executed plan's cash estimate is history; the ledger cash above is what is true now.
    cashAfterKrw: plan.outcome === "awaiting_open" ? { value: plan.account_snapshot.cash_after_plan_est_krw, basis: "estimate" } : null,
    notes: [
      "수량은 기준일 종가로 계산한 추정치입니다. 실제 수량·체결가는 다음 실제 시가가 기록된 뒤에만 정해집니다.",
      plan.nav_timing_note,
      ...(plan.skipped_closures.length ? [`휴장으로 건너뛴 날: ${plan.skipped_closures.join(", ")}`] : []),
    ],
  };
}

export function fundedFlow(a: FundedAccount): AccountFlow {
  const p = a.performance;
  const prev = a.history.length > 1 ? a.history[a.history.length - 2] : null;
  const last = a.history[a.history.length - 1] ?? null;
  const asOf = [a.marks.KR, a.marks.US].filter((x): x is string => x !== null).sort().pop() ?? a.epoch_kst_date;
  const plans = (["KR", "US"] as const).map((m) => a.latest_plans[m]).filter((x): x is FundedPlan => x !== null);

  const changes: string[] = [];
  if (prev && last) {
    changes.push(`평가액 ${won(prev.nav_krw)} → ${won(last.nav_krw)} (${prev.session ?? prev.at.slice(0, 10)} → ${last.session ?? last.at.slice(0, 10)}).`);
    if (last.principal_krw !== prev.principal_krw) changes.push(`원금 납입 ${won(last.principal_krw - prev.principal_krw)} — 수익이 아니라 원금 증가입니다.`);
    changes.push(`투자 손익 ${won(prev.investment_pnl_krw)} → ${won(last.investment_pnl_krw)}.`);
  } else changes.push("첫 평가 — 비교할 이전 평가가 없습니다.");
  for (const plan of plans.filter((x) => x.outcome === "executed")) {
    const filled = plan.fills.filter((f) => f.status === "filled").length;
    changes.push(`${plan.market} ${plan.trading_day} 시가: 체결 ${filled}건 · 미체결 ${plan.fills.length - filled}건.`);
  }
  const queued = a.signals.filter((s) => s.status === "queued").length;
  if (queued) changes.push(`진입 대기 신호 ${queued}건.`);

  // Only plans still waiting for their open change tomorrow's weights; an executed plan is already in the positions.
  const pending = plans.filter((x) => x.outcome === "awaiting_open");
  const weights = [
    ...a.positions.map((x) => {
      const order = pending.find((plan) => plan.market === x.market)?.orders.find((o) => o.ticker === x.ticker);
      return { id: x.id, name: x.name, currentPct: x.weight_pct, plannedPct: order ? order.planned_weight_pct : x.weight_pct };
    }),
    ...pending.flatMap((plan) => plan.orders.filter((o) => o.side === "buy").map((o) => ({
      id: o.instrument.symbol, name: o.name, currentPct: o.current_weight_pct, plannedPct: o.planned_weight_pct }))),
  ];
  const plannedStock = weights.reduce((acc, w) => acc + (w.plannedPct ?? 0), 0);
  weights.push({ id: "cash", name: "현금 (무이자 원화)", currentPct: p.cash_weight_pct, plannedPct: Math.max(0, 100 - plannedStock) });

  const seg = a.segments;
  const kst = (iso: string) => new Date(iso).toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });
  const segments: FlowSegment[] | null = seg.backtest
    ? [
        { key: "backtest", label: seg.backtest.label, period: `${kst(seg.backtest.start_at)} ~ ${kst(seg.backtest.end_at)}`,
          principalKrw: seg.backtest.principal_krw, navKrw: seg.backtest.nav_krw, pnlKrw: seg.backtest.investment_pnl_krw,
          twrPct: seg.backtest.twr_pct,
          note: seg.backtest.availability === "publication_date"
            ? "같은 W 결정 코드로 과거를 재생한 모의 결과. 리포트는 발간일에 공개된 것으로 가정(파이프라인 최초 관측 아님). 동결된 실전 계획이 아님."
            : "같은 W 결정 코드로 과거를 재생한 모의 결과(파이프라인 최초 수집일 기준). 동결된 실전 계획이 아님." },
        { key: "live", label: seg.live.label, period: `${kst(seg.live.start_at)} ~`,
          principalKrw: seg.live.start_principal_krw + seg.live.contributions_krw, navKrw: seg.live.nav_krw,
          pnlKrw: seg.live.investment_pnl_krw, twrPct: seg.live.twr_pct,
          note: `개시 시점 평가액 ${won(seg.live.start_nav_krw)}(백테스트 종료 상태)에서 출발. 손익·TWR은 개시 후 변화만, 납입 제외.` },
        { key: "combined", label: seg.combined.label, period: `${kst(seg.backtest.start_at)} ~`,
          principalKrw: seg.combined.principal_krw, navKrw: seg.combined.nav_krw, pnlKrw: seg.combined.investment_pnl_krw,
          twrPct: seg.combined.twr_pct, note: "백테스트 구간을 포함한 합산 — 실전 성과로 읽지 마세요." },
      ]
    : null;

  return {
    asOf,
    segments,
    marks: a.marks,
    valuation: {
      navKrw: p.nav_krw,
      principalKrw: p.principal_krw,
      initialKrw: p.initial_capital_krw,
      contributionsKrw: p.contributions_krw,
      investmentPnlKrw: p.investment_pnl_krw,
      twrPct: p.twr_pct,
      xirrPct: p.xirr_pct,
      cashKrw: p.cash_krw,
      cashLabel: "현금",
      cashWeightPct: p.cash_weight_pct,
      feesKrw: p.fees_krw,
      scopeNote: p.scope === "combined_backtest_and_live"
        ? "누적 원금·투자 손익·TWR은 백테스트 구간을 포함한 합산입니다. 실전 구간만의 성과는 아래 구간별 표를 보세요."
        : null,
      change: prev && last
        ? { navKrw: last.nav_krw - prev.nav_krw, principalKrw: last.principal_krw - prev.principal_krw,
            pnlKrw: last.investment_pnl_krw - prev.investment_pnl_krw, since: prev.session ?? prev.at.slice(0, 10) }
        : null,
    },
    changes,
    plans: plans.map((plan) => fundedPlan(plan, a)),
    weights,
    weightsNote: "추정 — 계획의 종가 기준 비중. 실제 비중은 시가 체결 뒤 달라집니다. KR·US 평가 기준일이 다를 수 있습니다.",
    log: [...a.fills].reverse().map((f) => ({
      key: f.fill_id,
      date: f.trading_day,
      market: f.market,
      name: a.positions.find((x) => x.ticker === f.ticker)?.name ?? f.ticker,
      side: f.side,
      filled: f.status === "filled",
      price: f.price,
      shares: f.status === "filled" ? f.shares : null,
      amountKrw: f.gross_krw,
      feesKrw: f.fees_krw,
      note: f.status === "filled" ? (f.realized_pnl_krw !== undefined ? `실현 손익 ${won(f.realized_pnl_krw)}` : "시가 체결") : `미체결 — ${f.reason_code ? UNFILLED[f.reason_code] : "사유 미기재"}`,
    })),
    issues: a.data_issues,
  };
}
