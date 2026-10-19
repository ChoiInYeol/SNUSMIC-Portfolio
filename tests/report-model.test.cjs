const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const raw = JSON.parse(fs.readFileSync(path.join(__dirname, '../src/data/report-performance.json'), 'utf8'));
const source = fs.readFileSync(path.join(__dirname, '../src/lib/report-model.ts'), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exportsObject = {};
const moduleRequire = (name) => {
  if (name === '@/data/report-performance.json') return raw;
  if (name === '@/lib/report-meta') return {};
  if (name === '@/lib/report-contract') {
    const contract = fs.readFileSync(path.join(__dirname, '../src/lib/report-contract.ts'), 'utf8');
    const compiled = ts.transpileModule(contract, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    const result = {}; new Function('require', 'exports', compiled)(moduleRequire, result); return result;
  }
  return require(name);
};
new Function('require', 'exports', outputText)(moduleRequire, exportsObject);
const { validateReportDataset, ReportRecordSchema } = exportsObject;

for (const [field, invalid] of [
  ['school', 'unknown-school'], ['rating_class', 'unexpected'],
  ['era', 'future'], ['report_type', 'invalid'], ['bucket_peak', 'invalid'],
]) {
  test(`invalid ${field} is quarantined, not replaced by a synthetic value`, () => {
    const row = { ...raw.records[0], [field]: invalid };
    assert.equal(ReportRecordSchema.safeParse(row).success, false);
    const input = { ...raw, records: [raw.records[0], row] };
    const { dataset, quarantine } = validateReportDataset(input);
    assert.equal(dataset.records.length, 1);
    assert.equal(quarantine.length, 1);
    assert.equal(dataset.records.length + quarantine.length, input.records.length);
    assert.equal(quarantine[0].raw[field], invalid);
    assert.equal(quarantine[0].sourceFile, row.source_file);
    assert.ok(quarantine[0].issues.some((issue) => issue.startsWith(`${field}:`)));
    assert.deepEqual(dataset.summary, []);
  });
}

test('invalid envelope cannot masquerade as an empty healthy dataset', () => {
  assert.throws(() => validateReportDataset(null));
  assert.throws(() => validateReportDataset({ ...raw, records: 'not-an-array' }));
});

test('nonfinite quote is quarantined with raw evidence preserved', () => {
  const row = { ...raw.records[0], latest_close: NaN };
  const { dataset, quarantine } = validateReportDataset({ ...raw, records: [row] });
  assert.equal(dataset.records.length, 0);
  assert.equal(quarantine.length, 1);
  assert.ok(Number.isNaN(quarantine[0].raw.latest_close));
});

test('valid reports keep original classifications and do not lose rows', () => {
  const { dataset, quarantine } = validateReportDataset(raw);
  assert.equal(quarantine.length, 0);
  assert.equal(dataset.records.length, raw.records.length);
  assert.equal(dataset.records[0].school, raw.records[0].school);
  assert.equal(dataset.records[0].rating_class, raw.records[0].rating_class);
});
