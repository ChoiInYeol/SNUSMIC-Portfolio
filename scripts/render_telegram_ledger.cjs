// Preview the exact server renderer without sending Telegram messages.
// node scripts/render_telegram_ledger.cjs <sota-book.json> [output-directory]
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const root = path.join(__dirname, '..');
function load(file) {
  const { outputText } = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  const scopedRequire = (id) => id.startsWith('.') ? load(path.relative(root, path.resolve(path.dirname(path.join(root, file)), `${id}.ts`))) : require(id);
  new Function('require', 'exports', outputText)(scopedRequire, exports);
  return exports;
}
async function main() {
  if (!process.argv[2]) throw new Error('Supply the validated SOTA book JSON path.');
  const out = path.resolve(process.argv[3] ?? path.join(root, 'artifacts', 'telegram', 'ledger'));
  const { SotaPortfolioSchema } = load('src/lib/sota-portfolio.ts');
  const book = SotaPortfolioSchema.parse(JSON.parse(fs.readFileSync(process.argv[2], 'utf8')));
  const { LEDGER_COMMANDS } = load('src/lib/portfolio-ledger.ts');
  const { renderLedgerImages } = load('src/lib/telegram-image.tsx');
  fs.mkdirSync(out, { recursive: true });
  for (const command of LEDGER_COMMANDS) {
    const images = await renderLedgerImages(command, book);
    for (const image of images) {
      const file = path.join(out, `${command.slice(1)}-${image.page}.png`);
      fs.writeFileSync(file, image.png);
      console.log(`${path.relative(root, file)} (${image.png.length} bytes)`);
    }
  }
}
main().catch((error) => { console.error(error.message); process.exitCode = 1; });
