import type { School } from "@/lib/report-meta";
import type { EvidencePack, RelationKind } from "@/lib/report-search";

// Research-flow comparisons (stock page and /search). Pure and client-safe: callers pass the small slice
// they already hold (one stock's records, or v2 instrument reports), never report-performance.json itself.

export type RatingClass = "buy" | "soft_buy" | "sell";
export type OpinionReport = {
  id: string;
  school: School;
  date: string;
  firstSeen: string | null;
  rating: string | null;
  ratingClass: RatingClass;
  targetPrice: number | null;
};

/** Search relation kinds folded into the three exposure tiers the UI separates. Topic relevance, not attractiveness. */
export type ExposureTier = "core" | "side" | "passing";
export const RELATION_TIER: Record<RelationKind, ExposureTier> = {
  primary: "core",
  supplier_customer: "side",
  peer: "side",
  sector: "side",
  passing: "passing",
};
export const TIER_LABEL: Record<ExposureTier, { title: string; hint: string }> = {
  core: { title: "주된 노출", hint: "이 주제가 나온 보고서의 주 분석 대상" },
  side: { title: "부수적 연관", hint: "공급망·고객·경쟁·산업 맥락으로 언급" },
  passing: { title: "단순 언급", hint: "관계 단서 없이 본문에 이름만 나옴" },
};

export type TargetStep<R extends OpinionReport = OpinionReport> = {
  report: R;
  /** same club's previous report (null for its first) */
  previous: R | null;
  targetChangePct: number | null;
  ratingChanged: boolean;
};

/** Oldest → newest, each report compared with the same club's previous one. */
export function targetHistory<R extends OpinionReport>(reports: R[]): TargetStep<R>[] {
  const last = new Map<School, R>();
  return [...reports]
    .sort((a, b) => a.date.localeCompare(b.date) || a.school.localeCompare(b.school))
    .map((report) => {
      const previous = last.get(report.school) ?? null;
      last.set(report.school, report);
      const targetChangePct =
        previous?.targetPrice && report.targetPrice !== null ? (report.targetPrice / previous.targetPrice - 1) * 100 : null;
      return { report, previous, targetChangePct, ratingChanged: previous !== null && previous.ratingClass !== report.ratingClass };
    });
}

export type Stances<R extends OpinionReport = OpinionReport> = {
  /** each club's latest report, newest first */
  latest: R[];
  /** clubs currently disagree: some bullish (buy or soft_buy), some sell */
  split: boolean;
  /** (max / min − 1) of the latest targets, when at least two clubs state one */
  targetSpreadPct: number | null;
};

export function clubStances<R extends OpinionReport>(reports: R[]): Stances<R> {
  const byClub = new Map<School, R>();
  for (const r of reports) {
    const seen = byClub.get(r.school);
    if (!seen || r.date > seen.date) byClub.set(r.school, r);
  }
  const latest = [...byClub.values()].sort((a, b) => b.date.localeCompare(a.date));
  const targets = latest.map((r) => r.targetPrice).filter((t): t is number => t !== null && t > 0);
  return {
    latest,
    // buy vs soft_buy is one bullish stance (strength differs); only bullish vs sell counts as disagreement.
    split: new Set(latest.map((r) => (r.ratingClass === "sell" ? "sell" : "buy"))).size > 1,
    targetSpreadPct: targets.length >= 2 ? (Math.max(...targets) / Math.min(...targets) - 1) * 100 : null,
  };
}

export type CoMention = { stock: number; tier: ExposureTier; kind: RelationKind; reports: number; mentions: number };

/**
 * Stocks named inside the given reports (manifest `mentions` tuples [stock, kind, conf, count]),
 * strongest relation kept per stock. `exclude` drops the page's own stock.
 */
export function coMentions(
  reports: { mentions: [number, number, number, number][] }[],
  kinds: readonly RelationKind[],
  exclude: number,
): CoMention[] {
  const out = new Map<number, CoMention>();
  for (const r of reports) {
    for (const [stock, k, , count] of r.mentions) {
      if (stock === exclude) continue;
      const kind = kinds[k];
      const prev = out.get(stock);
      if (!prev) out.set(stock, { stock, tier: RELATION_TIER[kind], kind, reports: 1, mentions: count });
      else {
        prev.reports += 1;
        prev.mentions += count;
        if (k < kinds.indexOf(prev.kind)) {
          prev.kind = kind;
          prev.tier = RELATION_TIER[kind];
        }
      }
    }
  }
  return [...out.values()].sort((a, b) => kinds.indexOf(a.kind) - kinds.indexOf(b.kind) || b.reports - a.reports || b.mentions - a.mentions);
}

const OPINION = /투자\s?의견|목표\s?주가|투자\s?포인트|밸류에이션|valuation|target price|investment thesis/gi;

/**
 * Paragraphs to quote when a report is opened from the chart (no search query): those stating the opinion,
 * target or thesis, in document order. Falls back to the first substantive paragraphs. Skips the
 * byline/contact paragraph.
 */
export function keyChunks(pack: EvidencePack, limit = 3): number[] {
  const usable = pack.chunks.map((c, i) => ({ i, text: c[4] })).filter((c) => c.text.length >= 40 && !c.text.includes("@"));
  const hits = usable
    .map((c) => ({ i: c.i, n: c.text.match(OPINION)?.length ?? 0 }))
    .filter((c) => c.n > 0)
    .sort((a, b) => b.n - a.n || a.i - b.i);
  return (hits.length ? hits : usable).slice(0, limit).map((c) => c.i).sort((a, b) => a - b);
}
