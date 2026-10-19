const assert = require('node:assert/strict');
const test = require('node:test');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// The browser re-derives each revision's content_hash (src/lib/canonical-json.ts); it must agree with the
// Python writer's sha256 over sort_keys/compact JSON for every published revision.
const source = fs.readFileSync(path.join(__dirname, '../src/lib/canonical-json.ts'), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const lib = {};
new Function('require', 'exports', outputText)(require, lib);

const root = path.join(__dirname, '../public/api/v2/strategies');
const revisions = fs.existsSync(root)
  ? fs.readdirSync(root, { withFileTypes: true })
      .filter((d) => d.isDirectory() && fs.existsSync(path.join(root, d.name, 'revisions')))
      .flatMap((d) => fs.readdirSync(path.join(root, d.name, 'revisions')).map((f) => path.join(root, d.name, 'revisions', f)))
  : [];

test('canonical JSON reproduces every published revision hash', () => {
  assert.ok(revisions.length > 0, 'no revisions found');
  for (const file of revisions) {
    const text = fs.readFileSync(file, 'utf8');
    const digest = crypto.createHash('sha256').update(lib.canonicalWithout(text, 'content_hash'), 'utf8').digest('hex');
    assert.equal(digest, JSON.parse(text).content_hash, path.relative(root, file));
    assert.equal(digest, path.basename(file, '.json'));
  }
});

test('number literals are kept verbatim and keys sorted by code point', () => {
  assert.equal(lib.canonicalWithout('{"b":1.0,"a":[2.50,-0,1e5],"h":"x","é":"\\n"}', 'h'), '{"a":[2.50,-0,1e5],"b":1.0,"é":"\\n"}');
  assert.throws(() => lib.canonicalWithout('{"a":1} x', 'h'));
});

test('a tampered payload no longer matches its hash', async () => {
  const text = fs.readFileSync(revisions[0], 'utf8');
  const tampered = text.replace(/(\d)\.(\d)/, (m, a, b) => `${a}.${(Number(b) + 1) % 10}`);
  assert.notEqual(await lib.revisionContentHash(tampered), JSON.parse(text).content_hash);
});
