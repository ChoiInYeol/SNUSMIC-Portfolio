const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// The web app's zod contracts must accept every published payload the pipeline wrote.
const api = path.join(__dirname, '../public/api/v1');
const source = fs.readFileSync(path.join(__dirname, '../src/lib/api-schemas.ts'), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const schemas = {};
new Function('require', 'exports', outputText)(require, schemas);
const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));

test('latest signals satisfy SignalsPayloadSchema', () => {
  const result = schemas.SignalsPayloadSchema.safeParse(readJson(path.join(api, 'signals/latest.json')));
  assert.equal(result.success, true, result.error?.message);
});

const manifest = readJson(path.join(api, 'strategy-details/manifest.json'));
for (const [key, asset] of Object.entries(manifest.assets)) {
  test(`strategy detail ${key} satisfies StrategyDetailSchema`, () => {
    const result = schemas.StrategyDetailSchema.safeParse(readJson(path.join(api, asset.path)));
    assert.equal(result.success, true, result.error?.message);
    assert.equal(result.data.content_hash, asset.content_hash);
  });
}

test('a detail with a malformed trade is rejected, not cast', () => {
  const detail = Object.values(manifest.assets)
    .map((asset) => readJson(path.join(api, asset.path)))
    .find((value) => value.trades?.length);
  assert.ok(detail, 'no published detail with trades');
  assert.equal(schemas.StrategyDetailSchema.safeParse(detail).success, true);
  const broken = { ...detail, trades: [{ ...detail.trades[0], entry: 'n/a' }] };
  assert.equal(schemas.StrategyDetailSchema.safeParse(broken).success, false);
});
