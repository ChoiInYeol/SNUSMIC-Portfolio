const assert = require('node:assert/strict');
const test = require('node:test');
const { loadTs, readJson } = require('./_ts-module.cjs');

const raw = readJson('src/data/report-performance.json');
const deps = { '@/data/report-performance.json': raw, '@/lib/report-meta': {} };
deps['@/lib/report-contract'] = loadTs('src/lib/report-contract.ts', deps);
const { validateReportDataset, ReportRecordSchema } = loadTs('src/lib/report-model.ts', deps);

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
