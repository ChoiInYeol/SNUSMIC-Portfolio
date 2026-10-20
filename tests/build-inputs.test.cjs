const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { ROOT, readJson } = require('./_ts-module.cjs');

// `npm run build` runs this gate before `next build`; static JSON served as-is must not publish invalid data.
const gate = (...args) => spawnSync(process.execPath, [path.join(ROOT, 'scripts/check-build-inputs.cjs'), ...args],
  { cwd: ROOT, encoding: 'utf8' });

test('build script runs the gate before next build', () => {
  assert.match(readJson('package.json').scripts.build, /^node scripts\/check-build-inputs\.cjs && next build$/);
});

test('the published latest signals pass the build gate', () => {
  const result = gate();
  assert.equal(result.status, 0, result.stderr);
});

test('a corrupted or truncated latest signals file fails the build gate', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'build-inputs-'));
  try {
    const corrupted = readJson('public/api/v1/signals/latest.json');
    delete corrupted.as_of;
    fs.writeFileSync(path.join(dir, 'corrupted.json'), JSON.stringify(corrupted));
    fs.writeFileSync(path.join(dir, 'truncated.json'), '{"as_of": ');
    for (const name of ['corrupted.json', 'truncated.json']) {
      assert.equal(gate(path.join(dir, name)).status, 1, name);
    }
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
