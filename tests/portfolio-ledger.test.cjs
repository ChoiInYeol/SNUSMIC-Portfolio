const assert = require('node:assert/strict');
const test = require('node:test');
const { loadTs, readJson } = require('./_ts-module.cjs');
const { SotaPortfolioSchema } = loadTs('src/lib/sota-portfolio.ts');
const ledger = loadTs('src/lib/portfolio-ledger.ts');
const images = loadTs('src/lib/telegram-image.tsx', {'./portfolio-ledger':ledger});
const book = SotaPortfolioSchema.parse(readJson('data/gold/portfolio/sota.json'));

test('shared ledger preserves actual positions and both sides of execution history', () => {
  const before = JSON.stringify(book), tables = ledger.buildPortfolioLedger(book);
  assert.equal(tables.positions.rows.length, book.positions.length);
  for (const p of book.positions) {
    const row = tables.positions.rows.find(r => r.symbol === `${p.market}-${p.ticker}`.toLowerCase());
    assert.equal(row.cells[1].text, p.entry_date);
    assert.equal(row.cells[2].text, ledger.number(p.shares,4));
    assert.equal(row.cells[5].text, p.weight_pct.toFixed(2));
    assert.equal(row.cells[6].pnl, p.pnl_krw);
    assert.equal(row.cells[8].text, p.quote_date);
  }
  assert.equal(tables.trades.rows.length, book.fills.length);
  for (const [i,f] of [...book.fills].reverse().entries()) {
    const r=tables.trades.rows[i];
    assert.equal(r.side,f.side); assert.equal(r.date,f.date);
    assert.equal(r.cells[3].text,f.entry_date);
    assert.equal(r.cells[4].text,ledger.number(f.shares,4));
    assert.equal(r.cells[7].text,ledger.number(f.cash_flow_krw));
    assert.equal(r.cells[8].pnl,f.pnl_krw ?? undefined);
  }
  assert.equal(JSON.stringify(book),before);
});

test('pending entries never invent fill prices or quantities, and captions use canonical URIs', () => {
  const rows=ledger.commandTable('/signals',book).rows;
  for (const r of rows.filter(r=>r.side==='buy')) assert.ok(r.cells.slice(3,8).every(c=>c.text===''));
  for (const command of ledger.LEDGER_COMMANDS) {
    const caption=images.ledgerCaption(command,book,'2030-01-01');
    assert.ok(caption.includes(book.as_of));
    assert.ok(caption.includes(ledger.ledgerUri(command,book)));
    assert.ok(caption.length<=1024);
  }
});

test('real table renderer creates bounded PNG pages without remote requests', async t => {
  const oldFetch=global.fetch;
  global.fetch=(url,init)=>{ assert.doesNotMatch(String(url instanceof Request ? url.url : url),/^https?:/i); return oldFetch(url,init); };
  t.after(()=>{global.fetch=oldFetch;});
  for (const command of ledger.LEDGER_COMMANDS) {
    const pending=images.renderLedgerImages(command,book,book.as_of);
    assert.equal(images.renderLedgerImages(command,book,book.as_of),pending);
    const pages=await pending, table=ledger.commandTable(command,book);
    assert.equal(pages.length,Math.max(1,Math.ceil(table.rows.length/24)));
    for (const image of pages) {
      const png=Buffer.from(image.png);
      assert.equal(png.subarray(0,8).toString('hex'),'89504e470d0a1a0a');
      assert.equal(png.readUInt32BE(16),table.columns.reduce((n,c)=>n+c.width,40));
      assert.ok(png.readUInt32BE(16)+png.readUInt32BE(20)<=10000);
      assert.ok(png.length<10*1024*1024);
    }
  }
});
