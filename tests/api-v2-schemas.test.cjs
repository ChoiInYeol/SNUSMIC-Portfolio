const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { ROOT, loadTs, readJson } = require('./_ts-module.cjs');

// The web app's v2 zod contracts must accept every published /api/v2 document the pipeline wrote.
const api = path.join(ROOT, 'public/api/v2');
const schemas = loadTs('src/lib/api-v2.ts', { '@/lib/api-schemas': loadTs('src/lib/api-schemas.ts') });
const files = (dir, pattern) =>
  fs.existsSync(path.join(api, dir))
    ? fs.readdirSync(path.join(api, dir)).filter((name) => pattern.test(name)).map((name) => path.join(dir, name))
    : [];

const expectations = [
  ['index.json', 'ApiIndexDocumentSchema'],
  ['signals.json', 'SignalIndexDocumentSchema'],
  ['signals/latest.json', 'SignalSnapshotDocumentSchema'],
  ...files('signals', /^\d{4}-\d{2}-\d{2}\.json$/).map((f) => [f, 'SignalSnapshotDocumentSchema']),
  ['strategies.json', 'StrategyCollectionDocumentSchema'],
  ['track-record.json', 'TrackRecordDocumentSchema'],
  ['instruments.json', 'InstrumentCollectionDocumentSchema'],
  ['clubs.json', 'ClubCollectionDocumentSchema'],
];
for (const { links } of readJson(path.join(api, 'strategies.json')).data) {
  const id = links.self.split('/').pop();
  expectations.push([`strategies/${id}.json`, 'StrategyDocumentSchema']);
  expectations.push([`strategies/${id}/trades.json`, 'StrategyTradesDocumentSchema']);
}
// Every revision on disk, including those of retired strategies (immutable, kept without a parent).
for (const dir of fs.readdirSync(path.join(api, 'strategies'), { withFileTypes: true }).filter((d) => d.isDirectory())) {
  for (const f of files(`strategies/${dir.name}/revisions`, /^[0-9a-f]{64}\.json$/)) expectations.push([f, 'StrategyRevisionSchema']);
}
for (const { id, links } of readJson(path.join(api, 'instruments.json')).data) {
  expectations.push([`instruments/${id}.json`, 'InstrumentDocumentSchema']);
  expectations.push([`instruments/${id}/prices.json`, 'PriceSeriesDocumentSchema']);
  if (links.trade_marks) expectations.push([`instruments/${id}/trade-marks.json`, 'TradeMarksDocumentSchema']);
}
for (const { id } of readJson(path.join(api, 'clubs.json')).data) {
  expectations.push([`clubs/${id}.json`, 'ClubDocumentSchema']);
  expectations.push([`clubs/${id}/reports.json`, 'ClubReportsDocumentSchema']);
}

test('every published v2 document satisfies its zod schema', () => {
  const failures = [];
  for (const [file, name] of expectations) {
    const result = schemas[name].safeParse(readJson(path.join(api, file)));
    if (!result.success) failures.push(`${file} (${name}): ${result.error.message.slice(0, 300)}`);
  }
  assert.deepEqual(failures, []);
});

test('the zod expectations cover every published v2 document', () => {
  const listed = new Set(expectations.map(([file]) => path.normalize(file)));
  const walk = (dir) =>
    fs.readdirSync(path.join(api, dir), { withFileTypes: true }).flatMap((entry) =>
      entry.isDirectory() ? walk(path.join(dir, entry.name)) : [path.join(dir, entry.name)],
    );
  const unlisted = walk('').filter((file) => file !== 'openapi.json' && !listed.has(path.normalize(file)));
  assert.deepEqual(unlisted, []);
});

test('a document with a malformed candle is rejected, not cast', () => {
  const [file] = expectations.find(([, name]) => name === 'PriceSeriesDocumentSchema');
  const doc = readJson(path.join(api, file));
  assert.equal(schemas.PriceSeriesDocumentSchema.safeParse(doc).success, true);
  const broken = { ...doc, data: { ...doc.data, candles: [{ ...doc.data.candles[0], close: 'n/a' }] } };
  assert.equal(schemas.PriceSeriesDocumentSchema.safeParse(broken).success, false);
});

test('a document with a foreign link or wrong schema_version is rejected', () => {
  const doc = readJson(path.join(api, 'index.json'));
  assert.equal(schemas.ApiIndexDocumentSchema.safeParse({ ...doc, schema_version: '1.0' }).success, false);
  const foreign = { ...doc, links: { ...doc.links, strategies: 'https://example.com/strategies' } };
  assert.equal(schemas.ApiIndexDocumentSchema.safeParse(foreign).success, false);
});
