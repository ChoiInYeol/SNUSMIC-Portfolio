// Research and account flows: the pure mappings behind /stocks/{slug} comparisons, /search exposure tiers and
// /portfolio(/funded). Real artifacts where they exist (portfolio.json, fulltext index); the funded account only
// has the writer's test fixture until it starts, and that fixture is never rendered by the site.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const { loadTs, readJson, ROOT } = require('./_ts-module.cjs');

const meta = loadTs('src/lib/report-meta.ts');
const compare = loadTs('src/lib/research-compare.ts');
const funded = loadTs('src/lib/funded-account.ts');
const { PaperPortfolioSchema } = loadTs('src/lib/paper-portfolio.ts');
const flow = loadTs('src/lib/account-flow.ts');
const search = loadTs('src/lib/report-search.ts');

const op = (school, date, ratingClass, targetPrice, firstSeen = date) =>
  ({ id: `${school}-${date}`, school, date, firstSeen, rating: null, ratingClass, targetPrice });

test('as-of view hides reports collected after its date and unknown collection dates', () => {
  assert.equal(meta.seenBy('2025-10-10', '2026-06-09', '2025-12-31'), false);
  assert.equal(meta.seenBy('2025-10-10', '2026-06-09', '2026-06-09'), true);
  assert.equal(meta.seenBy('2025-10-10', null, '2026-12-31'), false);
  assert.equal(meta.seenBy('2027-01-02', '2026-01-01', '2026-12-31'), false);
});

test('target history compares each report with the same club only', () => {
  const steps = compare.targetHistory([op('smic', '2024-01-01', 'buy', 100), op('yig', '2024-02-01', 'buy', 300), op('smic', '2024-03-01', 'soft_buy', 80)]);
  assert.deepEqual(steps.map((s) => s.report.date), ['2024-01-01', '2024-02-01', '2024-03-01']);
  assert.equal(steps[1].previous, null);
  assert.equal(Math.round(steps[2].targetChangePct), -20);
  assert.equal(steps[2].ratingChanged, true);
});

test('club stances flag disagreement on latest ratings and the target spread', () => {
  const s = compare.clubStances([op('smic', '2024-01-01', 'sell', 50), op('smic', '2024-05-01', 'buy', 120), op('yig', '2024-03-01', 'sell', 100)]);
  assert.deepEqual(s.latest.map((r) => r.school), ['smic', 'yig']);
  assert.equal(s.split, true);
  assert.equal(Math.round(s.targetSpreadPct), 20);
  assert.equal(compare.clubStances([op('smic', '2024-01-01', 'buy', null)]).targetSpreadPct, null);
  // buy vs soft_buy is the same bullish stance, not a disagreement
  assert.equal(compare.clubStances([op('smic', '2024-01-01', 'buy', 100), op('yig', '2024-02-01', 'soft_buy', 90)]).split, false);
});

test('relation kinds fold into exposure tiers and co-mentions keep the strongest relation', () => {
  assert.deepEqual(search.KINDS.map((k) => compare.RELATION_TIER[k]), ['core', 'side', 'side', 'side', 'passing']);
  const rows = compare.coMentions([{ mentions: [[7, 4, 2, 1], [9, 2, 1, 3]] }, { mentions: [[7, 1, 1, 2], [1, 0, 0, 1]] }], search.KINDS, 1);
  assert.deepEqual(rows.map((r) => [r.stock, r.kind, r.tier, r.reports, r.mentions]), [[7, 'supplier_customer', 'side', 2, 3], [9, 'peer', 'side', 1, 3]]);
});

test('chart evidence quotes come from the real pack and skip the byline paragraph', () => {
  const pack = search.EvidencePackSchema.parse(JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(ROOT, 'public/research/fulltext/reports/3be9debdcc04.json.gz')))));
  const picks = compare.keyChunks(pack);
  assert.ok(picks.length > 0 && picks.length <= 3);
  assert.deepEqual([...picks].sort((a, b) => a - b), picks);
  for (const i of picks) assert.ok(!pack.chunks[i][4].includes('@'));
});

test('paper account flow reads the real artifact without inventing fills or quantities', () => {
  const d = PaperPortfolioSchema.parse(readJson('public/research/portfolio.json'));
  const f = flow.paperFlow(d);
  assert.equal(f.valuation.principalKrw, d.initial_capital_krw);
  assert.equal(f.valuation.contributionsKrw, 0);
  assert.equal(f.valuation.investmentPnlKrw, d.nav_krw - d.initial_capital_krw);
  const [plan] = f.plans;
  const buys = plan.actions.filter((a) => a.side === 'buy');
  assert.equal(buys.length, d.target_positions.length);
  assert.ok(buys.every((a) => a.shares === null && a.amountKrw.basis === 'estimate'), 'fractional engine: amounts only, labelled estimates');
  assert.equal(plan.noTradeReason === null, buys.length > 0);
  assert.equal(f.log.length, d.fills.length);
  const planned = f.weights.reduce((acc, w) => acc + (w.plannedPct ?? 0), 0);
  assert.ok(Math.abs(planned - 100) < 0.01, `planned weights sum ${planned}`);
});

test('a paper account with no pending signal still explains the no-trade day', () => {
  const d = PaperPortfolioSchema.parse(readJson('public/research/portfolio.json'));
  const quiet = { ...d, target_positions: [], status: 'tracking' };
  const [plan] = flow.paperFlow(quiet).plans;
  assert.equal(plan.status, 'waiting_signal');
  assert.match(plan.noTradeReason, /진입 신호가 없습니다/);
  assert.equal(plan.cashAfterKrw, null);
});

test('funded flow separates principal from P&L and plan estimates from fills', () => {
  const account = funded.FundedAccountSchema.parse(readJson('tests/fixtures/funded-account.json'));
  const f = flow.fundedFlow(account);
  const p = account.performance;
  assert.equal(f.valuation.principalKrw, p.initial_capital_krw + p.contributions_krw);
  assert.equal(f.valuation.investmentPnlKrw, p.investment_pnl_krw);
  assert.ok(Math.abs(f.valuation.investmentPnlKrw - (p.nav_krw - p.principal_krw)) < 0.02);
  const kr = f.plans.find((x) => x.title.startsWith('KRX'));
  const buy = kr.actions.find((a) => a.side === 'buy');
  assert.equal(buy.shares.basis, 'estimate');
  assert.equal(kr.status, 'awaiting_open');
  const us = f.plans.find((x) => x.title.startsWith('미국'));
  assert.equal(us.noTradeReason, account.latest_plans.US.no_trade_reason);
  assert.match(us.actions.find((a) => a.side === 'hold').reason, /샹들리에 스탑/);
  assert.ok(f.log.some((r) => !r.filled && /거래정지/.test(r.note)));
  assert.ok(f.weights.some((w) => w.id === buy.id), 'pending buy appears in planned weights');
});

test('an executed plan no longer adds planned weights or a cash estimate', () => {
  const doc = readJson('tests/fixtures/funded-account.json');
  doc.latest_plans.KR.outcome = 'executed';
  const f = flow.fundedFlow(funded.FundedAccountSchema.parse(doc));
  const kr = f.plans.find((x) => x.title.startsWith('KRX'));
  assert.equal(kr.cashAfterKrw, null);
  const symbol = doc.latest_plans.KR.orders[0].instrument.symbol;
  assert.ok(!f.weights.some((w) => w.id === symbol), 'executed buy is not re-added as 0% → planned');
  const cash = f.weights.find((w) => w.id === 'cash');
  assert.ok(Math.abs(f.weights.reduce((a, w) => a + w.plannedPct, 0) - 100) < 0.01 || cash.plannedPct === 0);
});

test('not-started proposal mirrors the Python CONFIG and passes the principal oracle', () => {
  const py = fs.readFileSync(path.join(ROOT, 'scripts/funded_account.py'), 'utf8');
  const num = (key) => Number(py.match(new RegExp(`"${key}": ([0-9_]+)`))[1].replaceAll('_', ''));
  const P = funded.FUNDED_PROPOSAL;
  assert.equal(P.initial_capital_krw, num('initial_capital_krw'));
  assert.deepEqual(P.contribution, { base_krw: num('base_krw'), step_krw: num('step_krw'), step_every_installments: num('step_every_installments') });
  assert.equal(P.max_positions, num('max_positions'));
  assert.ok(py.includes(P.contribution_day.description), 'proposed contribution-day wording');
  assert.equal(P.contribution_day.label, '제안값(미확정)');
  assert.deepEqual([24, 48, 120].map((n) => funded.principalAfter(n)), [98_000_000, 170_000_000, 530_000_000]);
});
