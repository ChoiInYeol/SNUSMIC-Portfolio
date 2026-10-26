// Build gate (`npm run build` runs it before `next build`): generated JSON served as-is must satisfy the
// web app's zod contract, so invalid data fails the build instead of being published.
// /api/v1/signals/latest is a rewrite to the static latest.json, so no route handler parses it any more.
// Usage: node scripts/check-build-inputs.cjs [path/to/latest.json]
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

const ROOT = path.join(__dirname, "..");

function loadTs(file) {
  const source = fs.readFileSync(path.join(ROOT, file), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  });
  const exports = {};
  new Function("require", "exports", outputText)(require, exports);
  return exports;
}

const { SignalsPayloadSchema } = loadTs("src/lib/api-schemas.ts");
const file = path.resolve(process.argv[2] ?? path.join(ROOT, "public/api/v1/signals/latest.json"));
const result = SignalsPayloadSchema.safeParse(JSON.parse(fs.readFileSync(file, "utf8")));
if (!result.success) {
  console.error(`invalid generated artifact ${path.relative(ROOT, file)}:\n${result.error.message}`);
  process.exit(1);
}
console.log(`ok ${path.relative(ROOT, file)} satisfies SignalsPayloadSchema`);

const { SotaPortfolioSchema } = loadTs("src/lib/sota-portfolio.ts");
const book = path.join(ROOT, "data/gold/portfolio/sota.json");
SotaPortfolioSchema.parse(JSON.parse(fs.readFileSync(book, "utf8")));
console.log("ok SOTA model book satisfies SotaPortfolioSchema");
