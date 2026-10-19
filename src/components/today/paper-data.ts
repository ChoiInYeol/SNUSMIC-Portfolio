import { StrategyTradesDocumentSchema, type SignalSnapshotDocument, type StrategyTradesDocument } from "@/lib/api-v2";
import { loadLatestSnapshot, loadSnapshot, loadSnapshotIndex, readV2Optional, type SnapshotIndexEntry } from "@/lib/api-v2-server";
import { knownBy, loadReportFirstSeen } from "@/lib/report-first-seen";
import { reportDataset, type ReportRecord } from "@/lib/report-model";

// Everything the daily paper needs for one issue, read from /api/v2 at build time.
// Back issues (/today/[date]) use only what was knowable on their date: the frozen snapshots up to that date and
// reports that were both published and already collected by then.

export type Snapshot = SignalSnapshotDocument["data"];
type ClosedTrade = StrategyTradesDocument["data"][number];

type Named = { ticker: string; market: string; name: string };
export type SnapshotDiff = {
  /** Date of the snapshot compared against. */
  against: string;
  /** Headline strategy changed between the two issues — position diffs then compare different books. */
  headlineChanged: { from: string; to: string } | null;
  entered: Snapshot["open_positions"];
  exited: Snapshot["open_positions"];
  newBuys: Snapshot["buy_signals"];
  newSells: Snapshot["sell_signals"];
};

export type Obituary = {
  key: string;
  name: string;
  /** Exit date (trade log) or the first snapshot that no longer held the position (frozen snapshots). */
  exitDate: string;
  days: number | null;
  returnPct: number | null;
};

export type PaperIssue = {
  mode: "latest" | "archive";
  snapshot: Snapshot;
  /** The v2 URI the page is printed from (latest alias or the immutable dated snapshot). */
  resource: string;
  /** Snapshot ordinal (oldest = 1) — the same formula for /today and every back issue. */
  issueNo: number;
  /** Modern-era reports visible on this date, newest first. */
  reports: ReportRecord[];
  obituaries: {
    /** "trades": realized returns from the strategy's trade log; "snapshots": last frozen unrealized P&L. */
    basis: "trades" | "snapshots";
    items: Obituary[];
    resource: string | null;
  };
  prev: SnapshotIndexEntry | null;
  next: SnapshotIndexEntry | null;
  index: SnapshotIndexEntry[];
  diff: SnapshotDiff | null;
};

const OBITUARY_WINDOW_DAYS = 45;
const keyOf = (item: Named) => `${item.market}:${item.ticker}`;

function newIn<T extends Named>(current: T[], previous: Named[]): T[] {
  const seen = new Set(previous.map(keyOf));
  return current.filter((item) => !seen.has(keyOf(item)));
}

export function diffSnapshots(current: Snapshot, previous: Snapshot): SnapshotDiff {
  return {
    against: previous.as_of,
    headlineChanged:
      current.headline_strategy === previous.headline_strategy
        ? null
        : { from: previous.headline_strategy, to: current.headline_strategy },
    entered: newIn(current.open_positions, previous.open_positions),
    exited: newIn(previous.open_positions, current.open_positions),
    newBuys: newIn(current.buy_signals, previous.buy_signals),
    newSells: newIn(current.sell_signals, previous.sell_signals),
  };
}

// Every issue page reads overlapping snapshots; read each file once per build worker.
const snapshotCache = new Map<string, Promise<Snapshot>>();
function snapshotOn(date: string): Promise<Snapshot> {
  let hit = snapshotCache.get(date);
  if (!hit) {
    hit = loadSnapshot(date).then((doc) => {
      if (doc.data.as_of !== date) throw new Error(`Snapshot /api/v2/signals/${date} reports as_of ${doc.data.as_of}`);
      return doc.data;
    });
    snapshotCache.set(date, hit);
  }
  return hit;
}

const daysBefore = (date: string, days: number) =>
  new Date(new Date(`${date}T00:00:00Z`).getTime() - days * 86_400_000).toISOString().slice(0, 10);

/**
 * Exits seen across consecutive frozen snapshots within the window ending at index[position]. A position that
 * disappears between two snapshots exited on the later one; its P&L is the last frozen unrealized_pct. Pairs where
 * the headline strategy changed compare different books, so they are skipped.
 */
async function snapshotObituaries(index: SnapshotIndexEntry[], position: number): Promise<Obituary[]> {
  const cutoff = daysBefore(index[position].date, OBITUARY_WINDOW_DAYS);
  const items: Obituary[] = [];
  for (let k = position; k + 1 < index.length && index[k].date >= cutoff; k++) {
    const [newer, older] = await Promise.all([snapshotOn(index[k].date), snapshotOn(index[k + 1].date)]);
    if (newer.headline_strategy !== older.headline_strategy) continue;
    for (const gone of newIn(older.open_positions, newer.open_positions)) {
      items.push({
        key: `${keyOf(gone)}@${newer.as_of}`,
        name: gone.name,
        exitDate: newer.as_of,
        days: gone.days_held,
        returnPct: gone.unrealized_pct,
      });
    }
  }
  return items;
}

function tradeObituaries(trades: ClosedTrade[], asOf: string): Obituary[] {
  const cutoff = daysBefore(asOf, OBITUARY_WINDOW_DAYS);
  return trades
    .filter((t) => t.exit_date >= cutoff && t.exit_date <= asOf)
    .map((t) => ({ key: `${t.market}:${t.ticker}@${t.exit_date}`, name: t.name, exitDate: t.exit_date, days: t.days, returnPct: t.return_pct }));
}

/** Builds one issue. `date` = null prints the latest snapshot; an unknown date returns null (→ notFound). */
export async function loadPaperIssue(date: string | null): Promise<PaperIssue | null> {
  const index = await loadSnapshotIndex(); // newest first
  let snapshot: Snapshot;
  let resource: string;
  if (date === null) {
    const doc = await loadLatestSnapshot();
    snapshot = doc.data;
    resource = doc.links.self;
  } else {
    if (!index.some((entry) => entry.date === date)) return null;
    snapshot = await snapshotOn(date);
    resource = `/api/v2/signals/${date}`;
  }
  const asOf = snapshot.as_of;
  const position = index.findIndex((entry) => entry.date === asOf);
  if (position < 0) throw new Error(`Snapshot ${asOf} is missing from /api/v2/signals`);
  const prev = index[position + 1] ?? null;
  const next = index[position - 1] ?? null;
  const previousSnapshot = prev ? await snapshotOn(prev.date) : null;

  let reports: ReportRecord[];
  let obituaries: PaperIssue["obituaries"];
  if (date === null) {
    reports = reportDataset.records.filter((r) => r.report_date && r.report_date <= asOf);
    // Today's trade log is the current backtest of the headline strategy — fine for the latest issue.
    const tradesResource = `/api/v2/strategies/${snapshot.headline_strategy}/trades`;
    const tradesDoc = await readV2Optional(tradesResource, StrategyTradesDocumentSchema);
    obituaries = { basis: "trades", items: tradeObituaries(tradesDoc?.data ?? [], asOf), resource: tradesDoc ? tradesResource : null };
  } else {
    const firstSeen = await loadReportFirstSeen();
    reports = reportDataset.records.filter((r) => knownBy(r, asOf, firstSeen));
    obituaries = { basis: "snapshots", items: await snapshotObituaries(index, position), resource: "/api/v2/signals" };
  }
  obituaries.items = obituaries.items.sort((a, b) => b.exitDate.localeCompare(a.exitDate)).slice(0, 6);

  return {
    mode: date === null ? "latest" : "archive",
    snapshot,
    resource,
    issueNo: index.length - position,
    reports: reports
      .filter((r) => r.era === "modern")
      .sort((a, b) => String(b.report_date).localeCompare(String(a.report_date))),
    obituaries,
    prev,
    next,
    index,
    diff: previousSnapshot ? diffSnapshots(snapshot, previousSnapshot) : null,
  };
}
