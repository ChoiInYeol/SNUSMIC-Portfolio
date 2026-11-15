const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { auditTraces } = require('../scripts/check-server-traces.cjs');

test('server trace gate catches accumulated assets and keeps CDN revisions outside functions', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'server-trace-'));
  try {
    const trace = path.join(dir, 'page.js.nft.json');
    fs.writeFileSync(path.join(dir, 'a'), 'a'.repeat(60));
    fs.writeFileSync(path.join(dir, 'b'), 'b'.repeat(60));
    fs.writeFileSync(trace, JSON.stringify({ files: ['a'] }));
    assert.equal(auditTraces(dir, 100)[0].bytes, 60);
    fs.writeFileSync(trace, JSON.stringify({ files: ['a', 'b'] }));
    assert.throws(() => auditTraces(dir, 100), /exceeds server trace budget/);
    const revision = 'public/api/v2/strategies/test/revisions/hash.json';
    fs.mkdirSync(path.dirname(path.join(dir, revision)), { recursive: true });
    fs.writeFileSync(path.join(dir, revision), '{}');
    fs.writeFileSync(trace, JSON.stringify({ files: [revision] }));
    assert.throws(() => auditTraces(dir), /CDN-only historical revision/);
    assert.equal(fs.readFileSync(path.join(dir, revision), 'utf8'), '{}', 'static API bytes remain available');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
