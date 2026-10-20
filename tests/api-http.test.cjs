const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { NextRequest } = require('next/server');
const { ROOT, loadTs, readJson } = require('./_ts-module.cjs');

const index = readJson('src/data/api-v2-resources.json');
const guard = loadTs('src/proxy.ts', { '@/data/api-v2-resources.json': index });
const request = (uri, method) => new NextRequest(`https://example.com${uri}`, { method });

const PUBLISHED = ['/api/v2', '/api/v2/signals/latest', '/api/v2/signals/latest.json', '/api/v2/openapi.json',
  '/api/v2/strategies/D+_chandelier_optuna', '/api/v2/strategies/D%2B_chandelier_optuna'];
const MISSING = ['/api/v2/unknown', '/api/v2/signals/2099-01-01', `/api/v2/strategies/D+_chandelier_optuna/revisions/${'0'.repeat(64)}`,
  '/api/v2/instruments/kr-000000', '/api/v2/Strategies', '/api/v2/%E0%A4%A',
  // an encoded slash is not a path separator to the file system; the entry point has exactly one URI
  '/api/v2/clubs%2Fewha', '/api/v2/clubs%2fewha/reports', '/api/v2/index', '/api/v2/index.json'];

for (const uri of [...PUBLISHED, ...MISSING]) {
  test(`v2 method contract before file resolution: ${uri}`, async () => {
    const preflight = guard.proxy(request(uri, 'OPTIONS'));
    assert.equal(preflight.status, 204);
    assert.equal(await preflight.text(), '');
    assert.equal(preflight.headers.get('access-control-allow-origin'), '*');
    assert.equal(preflight.headers.get('cache-control'), 'no-store');
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
      const response = guard.proxy(request(uri, method));
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

for (const uri of PUBLISHED) {
  test(`published v2 resource passes through to its static file: ${uri}`, () => {
    for (const method of ['GET', 'HEAD']) {
      assert.equal(guard.proxy(request(uri, method)).headers.get('x-middleware-next'), '1');
    }
  });
}

// next.config.ts header rules win over route-handler headers, so the guard (not the fallback route)
// must answer unknown URIs, and never with a cacheable response.
for (const uri of MISSING) {
  test(`unknown v2 resource is a no-store 404 from the guard: ${uri}`, async () => {
    const get = guard.proxy(request(uri, 'GET'));
    assert.equal(get.status, 404);
    assert.equal(get.headers.get('x-middleware-next'), null);
    assert.equal(get.headers.get('content-type'), 'application/problem+json');
    assert.equal(get.headers.get('cache-control'), 'no-store');
    assert.equal(get.headers.get('access-control-allow-origin'), '*');
    assert.deepEqual(await get.json(), {
      type: 'about:blank', title: 'Not Found', status: 404,
      detail: 'No /api/v2 resource at this URI. Start from /api/v2 and follow links.', instance: uri,
    });
    const head = guard.proxy(request(uri, 'HEAD'));
    assert.equal(head.status, 404);
    assert.equal(head.headers.get('cache-control'), 'no-store');
    assert.equal(await head.text(), '');
  });
}

// Deprecated v1: same method contract; only the strategy-details alias can be checked against the index.
const revision = index.resources.find((key) => key.includes('/revisions/'));
const [sid, sha] = revision.slice('strategies/'.length).split('/revisions/');
test('v1 static files answer OPTIONS and writes instead of 500', async () => {
  for (const uri of ['/api/v1/signals/latest.json', '/api/v1/signals/latest', '/api/v1/openapi.json']) {
    assert.equal(guard.proxy(request(uri, 'GET')).headers.get('x-middleware-next'), '1');
    assert.equal(guard.proxy(request(uri, 'OPTIONS')).status, 204);
    const post = guard.proxy(request(uri, 'POST'));
    assert.equal(post.status, 405);
    assert.equal(post.headers.get('allow'), 'GET, HEAD, OPTIONS');
  }
});

test('v1 strategy-details alias: published revision passes, unknown hash is a no-store 404', async () => {
  for (const key of [sid, encodeURIComponent(sid)]) {
    assert.equal(guard.proxy(request(`/api/v1/strategy-details/${key}/${sha}.json`, 'GET')).headers.get('x-middleware-next'), '1');
  }
  const encodedSlash = guard.proxy(request(`/api/v1/strategy-details/x%2F${sid}/${sha}.json`, 'GET'));
  assert.equal(encodedSlash.status, 404);
  const uri = `/api/v1/strategy-details/${sid}/${'0'.repeat(64)}.json`;
  const missing = guard.proxy(request(uri, 'GET'));
  assert.equal(missing.status, 404);
  assert.equal(missing.headers.get('cache-control'), 'no-store');
  assert.equal((await missing.json()).instance, uri);
});

test('v1 dated snapshot: published date passes, unpublished date is a no-store 404', async () => {
  const date = index.resources.find((key) => /^signals\/\d{4}-\d{2}-\d{2}$/.test(key)).slice('signals/'.length);
  assert.equal(guard.proxy(request(`/api/v1/signals/${date}.json`, 'GET')).headers.get('x-middleware-next'), '1');
  const missing = guard.proxy(request('/api/v1/signals/2099-01-01.json', 'GET'));
  assert.equal(missing.status, 404);
  assert.equal(missing.headers.get('cache-control'), 'no-store');
  assert.equal(guard.proxy(request('/api/v1/trades/UNKNOWN.json', 'GET')).headers.get('x-middleware-next'), '1');
});

test('resource index lists exactly the published v2 files', () => {
  const api = path.join(ROOT, 'public', 'api', 'v2');
  const files = fs.readdirSync(api, { recursive: true })
    .map((rel) => rel.split(path.sep).join('/'))
    .filter((rel) => rel.endsWith('.json') && !path.basename(rel).startsWith('.'))
    .map((rel) => rel.slice(0, -'.json'.length))
    .sort();
  assert.deepEqual(index.resources, files);
});
