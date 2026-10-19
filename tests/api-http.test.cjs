const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { NextRequest } = require('next/server');

const source = fs.readFileSync(path.join(__dirname, '../src/proxy.ts'), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const guard = {};
new Function('require', 'exports', outputText)(require, guard);

for (const uri of ['/api/v2', '/api/v2/signals/latest', '/api/v2/signals/latest.json', '/api/v2/unknown']) {
  test(`v2 method contract before file resolution: ${uri}`, async () => {
    const request = (method) => new NextRequest(`https://example.com${uri}`, { method });
    for (const method of ['GET', 'HEAD']) {
      assert.equal(guard.proxy(request(method)).headers.get('x-middleware-next'), '1');
    }
    const preflight = guard.proxy(request('OPTIONS'));
    assert.equal(preflight.status, 204);
    assert.equal(await preflight.text(), '');
    assert.equal(preflight.headers.get('access-control-allow-origin'), '*');
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
      const response = guard.proxy(request(method));
      assert.equal(response.status, 405);
      assert.equal(response.headers.get('content-type'), 'application/problem+json');
      assert.equal(response.headers.get('allow'), 'GET, HEAD, OPTIONS');
      assert.equal(response.headers.get('cache-control'), 'no-store');
      assert.deepEqual(await response.json(), {
        type: 'about:blank', title: 'Method Not Allowed', status: 405,
        detail: 'The API is read-only: use GET, HEAD or OPTIONS.', instance: uri,
      });
    }
  });
}
