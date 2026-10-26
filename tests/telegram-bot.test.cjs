const assert = require('node:assert/strict');
const test = require('node:test');
const { loadTs, readJson } = require('./_ts-module.cjs');
const schemas = loadTs('src/lib/sota-portfolio.ts');
const bot = loadTs('src/lib/telegram-bot.ts');
const rawBook = readJson('data/gold/portfolio/sota.json');
const book = schemas.SotaPortfolioSchema.parse(rawBook);
const config = { secret: 'webhook-secret', chatId: '123', userId: '123', allowedChatIds: ['-1001'],
  githubToken: 'dedicated-actions-token', repository: 'ChoiInYeol/SNUSMIC-Portfolio' };
const update = (text = '/status', user = 123, chat = 123, type = 'private') => ({ update_id: 456,
  message: { message_id: 7, date: Math.floor(Date.now() / 1000), text, from: { id: user, is_bot: false }, chat: { id: chat, type } } });

test('webhook SOTA schema accepts published data and rejects fabricated accounting', () => {
  assert.equal(schemas.SotaPortfolioSchema.safeParse(rawBook).success, true);
  const broken = structuredClone(rawBook); broken.positions[0].shares *= 2;
  assert.equal(schemas.SotaPortfolioSchema.safeParse(broken).success, false);
});

test('secret and role checks separate allowed group readers from the administrator', () => {
  assert.ok(bot.secretMatches('secret', 'secret'));
  assert.ok(!bot.secretMatches('secret', null) && !bot.secretMatches('secret', 'wrong'));
  assert.equal(bot.roleFor(update(), config), 'admin');
  assert.equal(bot.roleFor(update('/status', 999, -1001, 'supergroup'), config), 'member');
  assert.equal(bot.roleFor(update('/refresh', 123, -1001, 'supergroup'), config), 'admin');
  assert.equal(bot.roleFor(update('/status', 999, 999), config), null);
  assert.equal(bot.roleFor(update('/status', 999, -2000, 'supergroup'), config), null);
  const forwarded = update(); forwarded.message.forward_origin = { type: 'user' };
  assert.equal(bot.roleFor(forwarded, config), null);
  assert.ok(!bot.help('member').includes('/refresh —') && bot.help('admin').includes('/refresh —'));
});

test('query commands report the stored book and UTF-16 splitting preserves all text', () => {
  assert.ok(bot.renderCommand('/status', book).includes(book.as_of));
  for (const p of book.positions) assert.ok(bot.renderCommand('/holdings', book).includes(p.ticker));
  assert.ok(bot.renderCommand('/signals', book).includes('빈 슬롯'));
  assert.ok(bot.renderCommand('/trades', book).includes('최근 익절·손절'));
  const text = '한글😀'.repeat(2500);
  const chunks = bot.splitTelegram(text);
  assert.equal(chunks.join(''), text);
  assert.ok(chunks.every((c) => c.length <= 3500));
});

const response = (data, status = 200) => new Response(JSON.stringify(data), { status });
test('refresh dispatch is fixed to main and retries/active runs do not dispatch again', async () => {
  const calls = [];
  const request = async (url, init) => {
    calls.push({ url, init });
    return url.includes('/runs?') ? response({ workflow_runs: [] }) : new Response(null, { status: 204 });
  };
  assert.ok((await bot.requestRefresh(update('/refresh'), config, request)).includes('접수'));
  assert.deepEqual(JSON.parse(calls[1].init.body), { ref: 'main', inputs: { telegram_request_id: '456' } });
  for (const [title, status] of [['refresh-daily · Telegram 456', 'completed'], ['refresh-daily', 'in_progress']]) {
    let count = 0;
    const fetchRuns = async () => { count++; return response({ workflow_runs: [{ id: 1, display_title: title, status, html_url: 'https://github.com/owner/repo/actions/runs/1' }] }); };
    assert.ok((await bot.requestRefresh(update('/refresh'), config, fetchRuns)).includes('이미'));
    assert.equal(count, 1);
  }
});

function route() {
  return loadTs('src/app/api/telegram/webhook/route.ts', {
    'next/server': { NextResponse: { json: (data, init) => response(data, init?.status ?? 200) } },
    '@/lib/sota-portfolio': schemas, '@/lib/telegram-bot': bot,
    '../../../../../data/gold/portfolio/sota.json': rawBook,
  });
}
function environment(t) {
  const saved = { ...process.env }, oldFetch = global.fetch;
  Object.assign(process.env, { TELEGRAM_WEBHOOK_SECRET: config.secret, TELEGRAM_BOT_TOKEN: 'bot-token',
    TELEGRAM_CHAT_ID: config.chatId, TELEGRAM_ALLOWED_USER_ID: config.userId,
    TELEGRAM_ALLOWED_CHAT_IDS: '-1001', TELEGRAM_ACTIONS_TOKEN: config.githubToken, TELEGRAM_REFRESH_ENABLED: 'true' });
  t.after(() => { for (const key of Object.keys(process.env)) if (!(key in saved)) delete process.env[key]; Object.assign(process.env, saved); global.fetch = oldFetch; });
}
const req = (value, secret = config.secret) => new Request('https://example.test/api/telegram/webhook', {
  method: 'POST', headers: { 'x-telegram-bot-api-secret-token': secret }, body: JSON.stringify(value),
});

test('unauthenticated or unauthorized webhook requests cannot send or execute anything', async (t) => {
  environment(t); global.fetch = async () => assert.fail('must not call a service');
  const { POST } = route();
  assert.equal((await POST(req(update(), 'wrong'))).status, 401);
  assert.equal((await POST(req(update('/refresh', 999, 999)))).status, 200);
  assert.equal((await POST(req({ update_id: 123, edited_message: update().message }))).status, 200);
});

test('group members receive queries in the group and cannot refresh', async (t) => {
  environment(t); const sent = [];
  global.fetch = async (url, init) => {
    assert.ok(url.startsWith('https://api.telegram.org/')); sent.push(JSON.parse(init.body)); return response({ ok: true });
  };
  const { POST } = route();
  assert.equal((await POST(req(update('/holdings', 999, -1001, 'supergroup')))).status, 200);
  assert.ok(sent.length && sent.every((m) => m.chat_id === '-1001'));
  assert.equal((await POST(req(update('/refresh', 999, -1001, 'supergroup')))).status, 200);
  assert.ok(sent.at(-1).text.includes('관리자 전용'));
});

test('administrator can discover a new room id without granting its members access', async (t) => {
  environment(t); const sent = [];
  global.fetch = async (url, init) => { sent.push(JSON.parse(init.body)); return response({ ok: true }); };
  const { POST } = route();
  await POST(req(update('/chatid@mybot', 123, -777, 'supergroup')));
  assert.equal(sent[0].chat_id, '-777'); assert.ok(sent[0].text.includes('-777'));
  await POST(req(update('/holdings', 999, -777, 'supergroup')));
  assert.equal(sent.length, 1);
});

test('old refresh commands cannot run; current admin request dispatches and replies safely', async (t) => {
  environment(t); const calls = [];
  global.fetch = async (url, init) => {
    calls.push({ url, init });
    if (url.includes('/runs?')) return response({ workflow_runs: [] });
    if (url.endsWith('/dispatches')) return new Response(null, { status: 204 });
    return response({ ok: true });
  };
  const { POST } = route();
  const old = update('/refresh'); old.message.date -= 3600;
  await POST(req(old)); assert.equal(calls.length, 1); assert.ok(calls[0].url.includes('/sendMessage'));
  calls.length = 0;
  await POST(req(update('/refresh')));
  assert.equal(calls.length, 3); assert.ok(calls[1].url.endsWith('/dispatches'));
  assert.ok(!JSON.parse(calls[2].init.body).text.includes(config.githubToken));
});
