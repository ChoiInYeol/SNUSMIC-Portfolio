import { z } from "zod";

// Full-text report search over the static index written by scripts/build_fulltext_index.py
// (public/research/fulltext/). This module mirrors the Python reference engine step for step — terms,
// shard hash, posting decode, query plan and ranking — so tests/test_search_golden.py and
// tests/report-search.test.cjs judge the same behaviour. Client-safe: no Node imports.

export const FULLTEXT_BASE = "/research/fulltext";
export const KINDS = ["primary", "supplier_customer", "peer", "sector", "passing"] as const;
export const CONFS = ["high", "medium", "low"] as const;
export type RelationKind = (typeof KINDS)[number];
export type Confidence = (typeof CONFS)[number];

const isoDate = z.iso.date();
const hex = (n: number) => z.string().regex(new RegExp(`^[0-9a-f]{${n}}$`));

export const FulltextManifestSchema = z.object({
  schema_version: z.literal("1.0"),
  corpus_sha256: hex(64),
  method: z.string(),
  params: z.object({
    shards: z.number().int().positive(),
    k1: z.number(),
    b: z.number(),
    qmax: z.number().int().positive(),
    source_base: z.url(),
    /** frozen BM25 statistics; reused by the writer until the corpus size drifts, so postings stay stable */
    basis: z.object({ docs: z.number().int().positive(), avg_len: z.number().positive() }),
  }),
  /** content version of each term shard; a shard must carry the version the manifest expects */
  shard_versions: z.array(hex(16)),
  stats: z.object({
    reports: z.number().int(),
    chunks: z.number().int(),
    terms: z.number().int(),
    postings: z.number().int(),
    boilerplate_chunks_dropped: z.number().int(),
    text_aliases: z.number().int(),
  }),
  synonyms: z.object({ groups: z.array(z.array(z.string())), related: z.record(z.string(), z.array(z.string())) }),
  stocks: z.array(z.object({
    slug: z.string().regex(/^[a-z]+-[a-z0-9.]+$/),
    name: z.string(),
    market: z.string(),
    ticker: z.string(),
    page: z.boolean(),
    aliases: z.array(z.string()),
  })),
  reports: z.array(z.object({
    id: hex(12),
    club: z.enum(["smic", "yig", "star", "kuvic", "ewha", "voera"]),
    date: isoDate.nullable(),
    first_seen: isoDate.nullable(),
    primary: z.number().int().nonnegative().nullable(),
    primary_conf: z.enum(CONFS).nullable(),
    scope: z.enum(["modern", "archive", "unscored"]),
    title: z.string(),
    path: z.string().startsWith("data/silver/reports/markdown/"),
    chunks: z.number().int().nonnegative(),
    mentions: z.array(z.tuple([z.number().int().nonnegative(), z.number().int().min(0).max(4), z.number().int().min(0).max(2), z.number().int().positive()])),
  })),
}).superRefine((doc, ctx) => {
  if (doc.shard_versions.length !== doc.params.shards) ctx.addIssue({ code: "custom", message: "shard_versions does not cover every shard", path: ["shard_versions"] });
  for (const [i, r] of doc.reports.entries()) {
    for (const idx of [r.primary, ...r.mentions.map((m) => m[0])]) {
      if (idx !== null && idx >= doc.stocks.length) ctx.addIssue({ code: "custom", message: `report ${i} points at unknown stock ${idx}`, path: ["reports", i] });
    }
  }
});
export type FulltextManifest = z.infer<typeof FulltextManifestSchema>;

export const TermShardSchema = z.object({
  schema_version: z.literal("1.0"),
  version: hex(16),
  /** term → "idf:postings" (idf with 4 decimals, then the varint posting list) */
  terms: z.record(z.string(), z.string().regex(/^\d+\.\d{4}:[0-9A-Za-z_-]+$/)),
});

export const EvidencePackSchema = z.object({
  schema_version: z.literal("1.0"),
  id: hex(12),
  path: z.string(),
  file_sha256: hex(64),
  /** [paragraph ordinal, line start, line end, content hash, text] */
  chunks: z.array(z.tuple([z.number().int().positive(), z.number().int().positive(), z.number().int().positive(), hex(16), z.string()])),
  /** [stock slug, kind, confidence, mention count, evidence chunk indexes] */
  mentions: z.array(z.tuple([z.string(), z.enum(KINDS), z.enum(CONFS), z.number().int().positive(), z.array(z.number().int().nonnegative())])),
});
export type EvidencePack = z.infer<typeof EvidencePackSchema>;

// ── terms ───────────────────────────────────────────────────────────────────────

const TOKEN = /[가-힣]+|[a-z][a-z0-9]+/g;
const HANGUL = /[가-힣]/;
const QUERY_JOSA = ["에서", "으로", "은", "는", "을", "를", "의"];
const B64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_";

export function normalize(text: string): string {
  return text.normalize("NFKC").toLowerCase();
}

export function terms(text: string): string[] {
  const out: string[] = [];
  for (const tok of normalize(text).match(TOKEN) ?? []) {
    if (HANGUL.test(tok[0])) for (let i = 0; i < tok.length - 1; i++) out.push(tok.slice(i, i + 2));
    else out.push(tok);
  }
  return out;
}

export function shardOf(term: string, shards: number): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < term.length; i++) {
    h ^= term.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h % shards;
}

export function decodePostings(text: string): Map<number, number> {
  const out = new Map<number, number>();
  let prev = 0, v = 0, shift = 0;
  for (const ch of text) {
    const d = B64.indexOf(ch);
    v += (d & 31) * 2 ** shift; // multiplication keeps values above 2^31 exact
    shift += 5;
    if (!(d & 32)) {
      prev += Math.floor(v / 32);
      out.set(prev, v % 32);
      v = 0;
      shift = 0;
    }
  }
  return out;
}

function stripQueryJosa(word: string): string {
  if (HANGUL.test(word[0] ?? "") && word.length >= 3) {
    for (const j of QUERY_JOSA) if (word.endsWith(j) && word.length - j.length >= 2) return word.slice(0, -j.length);
  }
  return word;
}

function levenshtein(a: string, b: string): number {
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) cur.push(Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)));
    prev = cur;
  }
  return prev[b.length];
}

// ── query plan and ranking ─────────────────────────────────────────────────────

const KIND_WEIGHT: Record<RelationKind, number> = { primary: 1, supplier_customer: 0.6, peer: 0.5, sector: 0.45, passing: 0.15 };
const CONF_WEIGHT: Record<Confidence, number> = { high: 1, medium: 0.8, low: 0.6 };
const SYNONYM_WEIGHT = 0.7, RELATED_WEIGHT = 0.4, FUZZY_WEIGHT = 0.8, GROUP_FLOOR = 0.2, DECAY = 0.5, FUZZY_MAX_HITS = 3;

export type Variant = { text: string; weight: number };
export type QueryPlan = { stock: number | null; fuzzy: number | null; words: Variant[][] };
export type Filters = { clubs?: string[]; from?: string; to?: string; collectedBy?: string };
export type RankedReport = { index: number; score: number };
export type StockGroup = {
  stock: number;
  score: number;
  /** contributing reports, strongest first */
  reports: { index: number; kind: RelationKind; conf: Confidence; contrib: number }[];
  clubs: string[];
  kinds: Partial<Record<RelationKind, number>>;
  first: string | null;
  last: string | null;
};
export type SearchResult = { plan: QueryPlan; reports: RankedReport[]; stocks: StockGroup[] };

export class FulltextIndex {
  private aliasToStock = new Map<string, number>();
  private groups = new Map<string, string[]>();
  private related = new Map<string, string[]>();
  private shards = new Map<number, Promise<Record<string, string>>>();

  constructor(readonly manifest: FulltextManifest, private loadShard: (id: number) => Promise<Record<string, string>>) {
    manifest.stocks.forEach((s, i) => {
      for (const a of [...s.aliases, s.slug]) {
        const key = normalize(a).replaceAll(" ", "");
        if (!this.aliasToStock.has(key)) this.aliasToStock.set(key, i);
      }
    });
    for (const g of manifest.synonyms.groups) {
      for (const w of g) this.groups.set(normalize(w), g.map(normalize).filter((x) => x !== normalize(w)));
    }
    for (const [k, v] of Object.entries(manifest.synonyms.related)) this.related.set(normalize(k), v.map(normalize));
  }

  private shard(id: number) {
    let p = this.shards.get(id);
    if (!p) {
      p = this.loadShard(id).catch((error) => { this.shards.delete(id); throw error; });
      this.shards.set(id, p);
    }
    return p;
  }

  /** report index → BM25 weight of the term in that report */
  async postings(term: string): Promise<Map<number, number>> {
    const value = (await this.shard(shardOf(term, this.manifest.params.shards)))[term];
    const out = new Map<number, number>();
    if (!value) return out;
    const cut = value.indexOf(":");
    const idf = Number(value.slice(0, cut));
    const { k1, qmax } = this.manifest.params;
    for (const [doc, q] of decodePostings(value.slice(cut + 1))) out.set(doc, idf * (k1 + 1) * (q / qmax) ** 2);
    return out;
  }

  async variantScores(text: string): Promise<Map<number, number>> {
    const ts = [...new Set(terms(text))];
    if (!ts.length) return new Map();
    const need = ts.length <= 3 ? ts.length : Math.ceil(ts.length * 0.75);
    const hits = new Map<number, number[]>();
    for (const list of await Promise.all(ts.map((t) => this.postings(t)))) {
      for (const [doc, w] of list) hits.set(doc, [...(hits.get(doc) ?? []), w]);
    }
    const out = new Map<number, number>();
    for (const [doc, ws] of hits) {
      if (ws.length >= need) out.set(doc, ws.reduce((sum, w) => sum + w, 0) / ts.length);
    }
    return out;
  }

  stockVariants(stock: number, weight: number): Variant[] {
    return this.manifest.stocks[stock].aliases.filter((a) => !/^\d+$/.test(a)).map((a) => ({ text: normalize(a), weight }));
  }

  plan(query: string): QueryPlan {
    const q = normalize(query).trim();
    const compact = q.replaceAll(" ", "");
    const stock = this.aliasToStock.get(compact) ?? null;
    const words = stock === null ? q.split(/\s+/).filter(Boolean).map(stripQueryJosa) : [compact];
    let fuzzy: number | null = null;
    if (stock === null && words.length === 1 && compact.length >= 3) {
      let best: [number, string] | null = null;
      for (const a of this.aliasToStock.keys()) {
        if (Math.abs(a.length - compact.length) > 1) continue;
        const cand: [number, string] = [levenshtein(compact, a), a];
        if (!best || cand[0] < best[0] || (cand[0] === best[0] && cand[1] < best[1])) best = cand;
      }
      if (best && best[0] === 1) fuzzy = this.aliasToStock.get(best[1]) ?? null;
    }
    return {
      stock,
      fuzzy,
      words: words.map((w) => {
        let variants: Variant[] = [{ text: w, weight: 1 }];
        if (stock !== null) {
          const names = this.stockVariants(stock, 1);
          if (names.length) variants = names;
        }
        return [
          ...variants,
          ...(this.groups.get(w) ?? []).map((text) => ({ text, weight: SYNONYM_WEIGHT })),
          ...(this.related.get(w) ?? []).map((text) => ({ text, weight: RELATED_WEIGHT })),
        ];
      }),
    };
  }

  async search(query: string, filters: Filters = {}): Promise<SearchResult> {
    const plan = this.plan(query);
    const reports = this.manifest.reports;
    const scoreWords = async (words: Variant[][]) =>
      Promise.all(words.map(async (variants) => {
        const best = new Map<number, number>();
        for (const v of variants) {
          for (const [d, s] of await this.variantScores(v.text)) best.set(d, Math.max(best.get(d) ?? 0, s * v.weight));
        }
        return best;
      }));

    let perWord = await scoreWords(plan.words);
    // A near-miss of a covered company's name (edit distance 1) that finds almost nothing is read as that
    // company: its names join the query at a lower weight.
    if (plan.fuzzy !== null && new Set(perWord.flatMap((m) => [...m.keys()])).size < FUZZY_MAX_HITS) {
      plan.stock = plan.fuzzy;
      plan.words = [[...plan.words[0], ...this.stockVariants(plan.fuzzy, FUZZY_WEIGHT)]];
      perWord = await scoreWords(plan.words);
    }
    const totals = new Map<number, number>();
    const matched = new Map<number, number>();
    for (const best of perWord) {
      for (const [d, s] of best) {
        totals.set(d, (totals.get(d) ?? 0) + s);
        matched.set(d, (matched.get(d) ?? 0) + 1);
      }
    }
    const nWords = perWord.length;
    const need = nWords <= 2 ? nWords : nWords - 1;
    const clubs = filters.clubs?.length ? new Set(filters.clubs) : null;
    const scored: RankedReport[] = [];
    for (const [d, s] of totals) {
      const r = reports[d];
      const m = matched.get(d) ?? 0;
      if (m < need) continue;
      if (clubs && !clubs.has(r.club)) continue;
      if (filters.from && (!r.date || r.date < filters.from)) continue;
      if (filters.to && (!r.date || r.date > filters.to)) continue;
      if (filters.collectedBy && (!r.first_seen || r.first_seen > filters.collectedBy)) continue;
      scored.push({ index: d, score: s * (m / nWords) ** 2 });
    }
    const stock = plan.stock;
    const isPrimary = (d: number) => (stock !== null && reports[d].primary === stock ? 1 : 0);
    scored.sort((a, b) => isPrimary(b.index) - isPrimary(a.index) || b.score - a.score || a.index - b.index);

    const top = scored.reduce((mx, r) => Math.max(mx, r.score), 0);
    const groups = new Map<number, StockGroup>();
    for (const { index: d, score: s } of scored) {
      const r = reports[d];
      const links: [number, RelationKind, Confidence][] = r.primary !== null ? [[r.primary, "primary", r.primary_conf ?? "medium"]] : [];
      if (s >= GROUP_FLOOR * top) for (const m of r.mentions) links.push([m[0], KINDS[m[1]], CONFS[m[2]]]);
      for (const [idx, kind, conf] of links) {
        let g = groups.get(idx);
        if (!g) groups.set(idx, (g = { stock: idx, score: 0, reports: [], clubs: [], kinds: {}, first: null, last: null }));
        const contrib = s * KIND_WEIGHT[kind] * CONF_WEIGHT[conf];
        g.reports.push({ index: d, kind, conf, contrib });
        if (!g.clubs.includes(r.club)) g.clubs.push(r.club);
        g.kinds[kind] = (g.kinds[kind] ?? 0) + contrib;
        if (r.date) {
          if (!g.first || r.date < g.first) g.first = r.date;
          if (!g.last || r.date > g.last) g.last = r.date;
        }
      }
    }
    for (const g of groups.values()) {
      g.reports.sort((a, b) => b.contrib - a.contrib);
      g.score = g.reports.reduce((sum, r, i) => sum + r.contrib * DECAY ** i, 0);
    }
    const ordered = [...groups.values()].sort(
      (a, b) => Number(b.stock === stock) - Number(a.stock === stock) || b.score - a.score || a.stock - b.stock,
    );
    return { plan, reports: scored, stocks: ordered };
  }
}

/** The strongest relation behind a stock group — what the "why" line names. */
export function dominantKind(group: StockGroup): RelationKind {
  return (Object.entries(group.kinds) as [RelationKind, number][]).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "passing";
}

// ── paragraphs inside an evidence pack ─────────────────────────────────────────

export type RankedChunk = { index: number; score: number; exact: boolean };

/** Rank a report's paragraphs for the plan: term coverage per word, exact phrase occurrences first. */
export function rankChunks(pack: EvidencePack, plan: QueryPlan, limit = 3): RankedChunk[] {
  const out: RankedChunk[] = [];
  pack.chunks.forEach((chunk, index) => {
    const text = normalize(chunk[4]);
    const present = new Set(terms(text));
    let score = 0, words = 0, exact = false;
    for (const variants of plan.words) {
      let best = 0;
      for (const v of variants) {
        const ts = [...new Set(terms(v.text))];
        if (!ts.length) continue;
        const hit = ts.filter((t) => present.has(t)).length;
        const need = ts.length <= 3 ? ts.length : Math.ceil(ts.length * 0.75);
        if (hit < need) continue;
        const occurrences = text.split(v.text).length - 1;
        if (occurrences) exact = true;
        best = Math.max(best, v.weight * (hit / ts.length + Math.min(occurrences, 5)));
      }
      if (best > 0) words++;
      score += best;
    }
    const need = plan.words.length <= 2 ? plan.words.length : plan.words.length - 1;
    if (score > 0 && words >= need) out.push({ index, score, exact });
  });
  return out.sort((a, b) => b.score - a.score || a.index - b.index).slice(0, limit);
}

/** Split text into plain/marked segments for every case-insensitive occurrence of the given needles. */
export function highlight(text: string, needles: string[]): { text: string; mark: boolean }[] {
  const lower = text.toLowerCase();
  const marks: [number, number][] = [];
  for (const n of [...new Set(needles.map((x) => x.toLowerCase()).filter((x) => x.length >= 2))]) {
    for (let at = lower.indexOf(n); at !== -1; at = lower.indexOf(n, at + n.length)) marks.push([at, at + n.length]);
  }
  marks.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
  const out: { text: string; mark: boolean }[] = [];
  let pos = 0;
  for (const [s, e] of marks) {
    if (s < pos) continue;
    if (s > pos) out.push({ text: text.slice(pos, s), mark: false });
    out.push({ text: text.slice(s, e), mark: true });
    pos = e;
  }
  if (pos < text.length) out.push({ text: text.slice(pos), mark: false });
  return out;
}

/** GitHub view of the transcript at the evidence lines (plain view so line anchors resolve). */
export function sourceUrl(manifest: FulltextManifest, path: string, lines?: [number, number]): string {
  const encoded = path.split("/").map(encodeURIComponent).join("/");
  return `${manifest.params.source_base}/${encoded}${lines ? `?plain=1#L${lines[0]}-L${lines[1]}` : ""}`;
}

/** Home deep link that opens the verdict card (only scored modern records have one). */
export function verdictHref(report: FulltextManifest["reports"][number]): string | null {
  return report.scope === "modern" ? `/#r=${encodeURIComponent(report.path.split("/").pop() ?? "")}` : null;
}

// ── loading (browser) ──────────────────────────────────────────────────────────

async function fetchJson(url: string): Promise<unknown> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`검색 색인 요청 실패 (${response.status})`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  // Packs are stored gzip-compressed; a host that already decoded them (Content-Encoding) hands over plain JSON.
  if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
    return JSON.parse(await new Response(stream).text());
  }
  return JSON.parse(new TextDecoder().decode(bytes));
}

let indexPromise: Promise<FulltextIndex> | null = null;

export function loadFulltextIndex(): Promise<FulltextIndex> {
  indexPromise ??= fetchJson(`${FULLTEXT_BASE}/manifest.json`)
    .then((raw) => {
      const manifest = FulltextManifestSchema.parse(raw);
      return new FulltextIndex(manifest, async (id) => {
        const shard = TermShardSchema.parse(await fetchJson(`${FULLTEXT_BASE}/terms/${String(id).padStart(3, "0")}.json`));
        if (shard.version !== manifest.shard_versions[id]) throw new Error("검색 색인 버전이 섞였습니다. 새로고침해 주세요.");
        return shard.terms;
      });
    })
    .catch((error) => { indexPromise = null; throw error; });
  return indexPromise;
}

const packs = new Map<string, Promise<EvidencePack>>();

export function loadEvidencePack(id: string): Promise<EvidencePack> {
  let p = packs.get(id);
  if (!p) {
    p = fetchJson(`${FULLTEXT_BASE}/reports/${id}.json.gz`).then((raw) => EvidencePackSchema.parse(raw));
    p.catch(() => packs.delete(id));
    packs.set(id, p);
  }
  return p;
}
