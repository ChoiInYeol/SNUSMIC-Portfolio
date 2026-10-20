const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { loadTs, readJson, ROOT } = require('./_ts-module.cjs');

const { FundedAccountSchema } = loadTs('src/lib/funded-account.ts');
// Writer-produced sample (tests/test_funded_account_contract.py); the live artifact exists only once the account starts.
const sample = readJson('tests/fixtures/funded-account.json');

test('funded-account Gold sample validates and keeps plan and fill apart', () => {
  const doc = FundedAccountSchema.parse(sample);
  assert.equal(doc.config.contribution_day.label, '제안값(미확정)');
  assert.ok(doc.fills.every((f) => f.plan_id && !('orders' in f)));
  assert.ok(Object.values(doc.latest_plans).every((p) => p === null || p.quantity_basis === 'estimate_from_close'));
});

test('funded-account boundary rejects contributions counted as returns and index orders', () => {
  const inflated = structuredClone(sample);
  inflated.performance.investment_pnl_krw += inflated.performance.contributions_krw;
  assert.throws(() => FundedAccountSchema.parse(inflated), /NAV minus principal/);
  const indexOrder = structuredClone(sample);
  const orders = indexOrder.latest_plans.KR.orders;
  orders.push({ ...orders[0], ticker: 'INDEX:AllWeather' });
  assert.throws(() => FundedAccountSchema.parse(indexOrder), /not orderable/);
  const confirmed = structuredClone(sample);
  confirmed.config.contribution_day.label = '확정';
  assert.throws(() => FundedAccountSchema.parse(confirmed));
});

test('loader reports not_started when the artifact is absent', async () => {
  const { loadFundedAccount } = loadTs('src/lib/funded-account-data.ts', {
    '@/lib/funded-account': { FundedAccountSchema },
  });
  const missing = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'funded-')), 'none.json');
  assert.deepEqual(await loadFundedAccount(missing), { status: 'not_started' });
  const live = path.join(ROOT, 'public/research/funded-account.json');
  if (fs.existsSync(live)) assert.equal((await loadFundedAccount(live)).status, 'ready');
});

test('seeded account keeps the backtest and live segments apart', () => {
  const flow = loadTs('src/lib/account-flow.ts');
  const seeded = readJson('tests/fixtures/funded-account-seeded.json');
  const doc = FundedAccountSchema.parse(seeded);
  assert.equal(doc.performance.scope, 'combined_backtest_and_live');
  assert.equal(doc.config.contribution_day.label, '사용자 확정');
  const f = flow.fundedFlow(doc);
  assert.deepEqual(f.segments.map((s) => s.label), ['백테스트 구간 (simulated)', '실전 구간', '백테스트+실전 합산']);
  assert.match(f.valuation.scopeNote, /백테스트 구간을 포함한 합산/);
  // Folding the seeded backtest P&L into the live track record is rejected at the boundary.
  const mixed = structuredClone(seeded);
  mixed.segments.live.investment_pnl_krw += mixed.segments.backtest.investment_pnl_krw;
  assert.throws(() => FundedAccountSchema.parse(mixed), /exclude the seeded backtest/);
  const unlabelled = structuredClone(seeded);
  unlabelled.performance.scope = 'live';
  assert.throws(() => FundedAccountSchema.parse(unlabelled), /combined/);
});
