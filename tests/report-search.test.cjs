// Full-text search: the TypeScript engine the /search page runs, against the committed index and the
// golden queries shared with tests/test_search_golden.py (same expectations, independent implementation).
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/lib/report-search.ts'), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const lib = {};
new Function('require', 'exports', outputText)(require, lib);

const ROOT = path.join(__dirname, '../public/research/fulltext');
const readJson = (rel) => JSON.parse(fs.readFileSync(path.join(ROOT, rel), 'utf8'));
const manifest = lib.FulltextManifestSchema.parse(readJson('manifest.json'));
const index = new lib.FulltextIndex(manifest, async (id) =>
  lib.TermShardSchema.parse(readJson(`terms/${String(id).padStart(3, '0')}.json`)).terms);
const pack = (id) => lib.EvidencePackSchema.parse(JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(ROOT, `reports/${id}.json.gz`))).toString('utf8')));
const golden = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/search-golden.json'), 'utf8'));
const slugsOf = (res) => res.stocks.map((g) => manifest.stocks[g.stock].slug);

test('terms, shard hash and posting decode match the Python writer', () => {
  assert.deepEqual(lib.terms('협동로봇을 AI2 공급'), ['협동', '동로', '로봇', '봇을', 'ai2', '공급']);
  assert.deepEqual(['로봇', '휴머', 'robot', '감속'].map((t) => lib.shardOf(t, 512)), [84, 65, 223, 102]);
  const shard = readJson('terms/084.json').terms;
  const [idf, enc] = shard['로봇'].split(':');
  assert.ok(Number(idf) > 0);
  const robot = lib.decodePostings(enc);
  assert.ok(robot.size > 100);
  for (const [doc, q] of robot) assert.ok(doc < manifest.reports.length && q >= 1 && q <= manifest.params.qmax);
});

for (const q of golden.queries) {
  test(`golden query ${q.id} (${q.q})`, async () => {
    const res = await index.search(q.q);
    const got = slugsOf(res);
    if (q.empty) {
      assert.equal(res.reports.length, 0);
      assert.equal(res.stocks.length, 0);
      return;
    }
    if (q.plan_stock) assert.equal(manifest.stocks[res.plan.stock].slug, q.plan_stock);
    if (q.top1_stock) assert.equal(got[0], q.top1_stock);
    for (const slug of q.stocks_in_top ?? []) assert.ok(got.slice(0, q.top_n).includes(slug), `${slug} not in ${got.slice(0, q.top_n)}`);
    if (q.relevant_stocks) {
      const relevant = new Set(q.relevant_stocks);
      const acceptable = new Set([...q.relevant_stocks, ...q.acceptable_stocks]);
      const recall = got.slice(0, q.recall_at).filter((s) => relevant.has(s)).length / relevant.size;
      const precision = got.slice(0, q.precision_at).filter((s) => acceptable.has(s)).length / q.precision_at;
      assert.ok(recall >= q.min_recall, `recall ${recall}`);
      assert.ok(precision >= q.min_precision, `precision ${precision}`);
    }
    if (q.primary_reports_first) {
      const flags = res.reports.map((r) => manifest.reports[r.index].primary === res.plan.stock);
      assert.ok(flags[0]);
      assert.deepEqual(flags, [...flags].sort((a, b) => Number(b) - Number(a)));
    }
    if (q.passing_groups_below) {
      const anchor = got.indexOf(q.passing_groups_below);
      res.stocks.forEach((g, i) => { if (lib.dominantKind(g) === 'passing') assert.ok(i > anchor); });
    }
  });
}

test('the best paragraph of the top robot report quotes the query from the source', async () => {
  const res = await index.search('로봇');
  const report = manifest.reports[res.reports[0].index];
  const p = pack(report.id);
  const [best] = lib.rankChunks(p, res.plan);
  assert.ok(best && best.exact);
  const chunk = p.chunks[best.index];
  assert.match(chunk[4], /로봇|휴머노이드|로보틱스/);
  const marked = lib.highlight(chunk[4], res.plan.words.flat().map((v) => v.text)).filter((s) => s.mark);
  assert.ok(marked.length > 0);
  assert.match(lib.sourceUrl(manifest, report.path, [chunk[1], chunk[2]]), /\?plain=1#L\d+-L\d+$/);
});

test('filters respect club, period and what was collected by then', async () => {
  const res = await index.search('로봇', { clubs: ['yig'], from: '2025-01-01', collectedBy: '2026-09-01' });
  assert.ok(res.reports.length > 0);
  for (const { index: d } of res.reports) {
    const r = manifest.reports[d];
    assert.equal(r.club, 'yig');
    assert.ok(r.date >= '2025-01-01' && r.first_seen <= '2026-09-01');
  }
});

test('invalid manifests are rejected at the boundary', () => {
  const bad = structuredClone(readJson('manifest.json'));
  bad.reports[0].primary = bad.stocks.length + 5;
  assert.equal(lib.FulltextManifestSchema.safeParse(bad).success, false);
  assert.equal(lib.FulltextManifestSchema.safeParse({ ...bad, schema_version: '2.0' }).success, false);
});
