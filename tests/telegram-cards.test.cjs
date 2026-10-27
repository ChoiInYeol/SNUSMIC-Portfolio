const assert = require('node:assert/strict');
const test = require('node:test');
const { loadTs, readJson } = require('./_ts-module.cjs');
const { SotaPortfolioSchema } = loadTs('src/lib/sota-portfolio.ts');
const cards = loadTs('src/lib/telegram-cards.tsx');
const book = SotaPortfolioSchema.parse(readJson('data/gold/portfolio/sota.json'));

test('card pages preserve all holding quantities, accounting values and quote dates', () => {
  const before = JSON.stringify(book), specs = cards.buildCardSpecs('/holdings', book);
  const rows = specs.flatMap((s) => s.rows);
  assert.equal(rows.length, book.positions.length);
  for (const p of book.positions) {
    const r = rows.find((r) => r.tag.startsWith(`${p.market} ${p.ticker} `));
    assert.ok(r, p.ticker);
    assert.ok(r.detail.includes(p.shares.toLocaleString('en-US', {maximumFractionDigits:4})+'주'));
    assert.equal(r.value, p.weight_pct.toFixed(2)+'%');
    assert.ok(r.subvalue.includes(Math.round(p.pnl_krw).toLocaleString('en-US')+'원'));
    assert.ok(r.tag.includes(p.quote_date));
    assert.equal(r.tone, p.pnl_krw);
    assert.ok(r.footRight.includes(p.return_pct.toFixed(2)+'%'));
  }
  assert.ok(specs.every((s) => s.rows.length <= 6));
  assert.equal(JSON.stringify(book), before);
});

test('signals show actual pending entries and exits, and stale captions retain the evaluation date', () => {
  const signals = cards.buildCardSpecs('/signals', book).flatMap((s) => s.rows);
  const expected = book.positions.filter((p) => p.exit_pending || p.dist_to_stop_pct !== null && p.dist_to_stop_pct <= 3);
  assert.equal(signals.length, expected.length + book.buy_candidates.length);
  for (const p of expected) assert.ok(signals.some((r) => r.detail.includes(p.ticker) && r.name === p.name));
  const caption = cards.cardCaption('/status', book, '2030-01-01');
  assert.ok(caption.includes(book.as_of) && caption.includes('⚠️') && caption.includes('/status text'));
  assert.ok(caption.length <= 1024);
});

test('empty states still produce a page and missing or zero-distance stops stay explicit', () => {
  const empty = structuredClone(book); empty.positions=[]; empty.buy_candidates=[]; empty.recent_exits=[];
  for (const command of cards.CARD_COMMANDS) assert.equal(cards.buildCardSpecs(command, empty).length, 1);
  const pending = structuredClone(book); pending.positions[0].exit_pending=true; pending.positions[0].dist_to_stop_pct=null; pending.positions[0].stop_level=null;
  assert.ok(cards.buildCardSpecs('/signals', pending).some((s) => s.rows.some((r) => r.subvalue.includes('미집계'))));
});

test('real Korean renderer creates bounded PNGs without network fonts and reuses the same render', async (t) => {
  const oldFetch = global.fetch;
  global.fetch = (url, init) => {
    assert.doesNotMatch(String(url instanceof Request ? url.url : url), /^https?:/i, 'rendering must not fetch remote fonts or images');
    return oldFetch(url, init); // Next's local WASM loader can use file:/data: URLs.
  };
  t.after(() => { global.fetch = oldFetch; });
  for (const command of cards.CARD_COMMANDS) {
    const pending = cards.renderTelegramCards(command, book, book.as_of);
    assert.equal(cards.renderTelegramCards(command, book, book.as_of), pending);
    const images = await pending;
    assert.equal(images.length, cards.buildCardSpecs(command, book).length);
    for (const image of images) {
      const png = Buffer.from(image.png);
      assert.equal(png.subarray(0,8).toString('hex'), '89504e470d0a1a0a');
      const width=png.readUInt32BE(16), height=png.readUInt32BE(20);
      assert.equal(width,960);
      assert.ok(width+height <= 10000 && Math.max(width,height)/Math.min(width,height) <= 20);
      assert.ok(png.length < 10*1024*1024);
    }
  }
});
