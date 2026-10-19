const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
function compile(file, dependencies = {}) {
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
  const exports = {}; new Function('require', 'exports', code)(id => dependencies[id] ?? require(id), exports); return exports;
}
const contract = compile('src/lib/report-contract.ts', {'@/lib/report-meta': {}});
const transport = compile('src/lib/dashboard-transport.ts', {'@/lib/report-contract': contract});
const raw = JSON.parse(fs.readFileSync('src/data/report-performance.json', 'utf8'));
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
  review.ReviewDocumentSchema.parse(JSON.parse(fs.readFileSync('public/research/review.json', 'utf8')));
  paper.PaperPortfolioSchema.parse(JSON.parse(fs.readFileSync('public/research/portfolio.json', 'utf8')));
});
