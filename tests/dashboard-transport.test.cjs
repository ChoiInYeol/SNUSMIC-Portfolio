const test = require('node:test');
const assert = require('node:assert/strict');
const { loadTs: compile, readJson } = require('./_ts-module.cjs');

const contract = compile('src/lib/report-contract.ts', {'@/lib/report-meta': {}});
const transport = compile('src/lib/dashboard-transport.ts', {'@/lib/report-contract': contract});
const raw = readJson('src/data/report-performance.json');
const doc = contract.ReportDatasetSchema.parse(raw);
test('home projection preserves every modern record and field with a smaller payload', () => {
  const packed = transport.packDashboard(doc);
  const decoded = transport.unpackDashboard(JSON.parse(JSON.stringify(packed)));
  assert.deepEqual(decoded.records, doc.records.filter(r => r.era === 'modern'));
  assert.ok(JSON.stringify(packed).length < JSON.stringify(doc).length * .75);
  assert.throws(() => transport.unpackDashboard({...packed, columns: ['ticker','ticker']}));
});
test('review and paper serving artifacts validate at frontend boundaries', () => {
  const review = compile('src/lib/research-review.ts');
  const paper = compile('src/lib/paper-portfolio.ts');
  review.ReviewDocumentSchema.parse(readJson('public/research/review.json'));
  paper.PaperPortfolioSchema.parse(readJson('public/research/portfolio.json'));
});

test('sensitivity artifact has seven unique fixed-policy conditions', () => {
  const { StrategyRobustnessSchema } = compile('src/lib/strategy-robustness.ts');
  const raw = readJson('public/research/strategy-robustness.json');
  StrategyRobustnessSchema.parse(raw);
  assert.equal(StrategyRobustnessSchema.safeParse({...raw, reoptimized: true}).success, false);
  assert.equal(StrategyRobustnessSchema.safeParse({...raw, scenarios: Array(7).fill(raw.scenarios[0])}).success, false);
});
