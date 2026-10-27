// Shared loader for the Node contract tests (not matched by tests/*.test.cjs, so never run on its own).
// Transpiles one TypeScript module to CommonJS and evaluates it; `deps` maps import specifiers
// (e.g. '@/lib/api-schemas') to already-loaded modules, everything else goes to Node's require.
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const ROOT = path.join(__dirname, '..');

function loadTs(file, deps = {}) {
  const source = fs.readFileSync(path.join(ROOT, file), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  new Function('require', 'exports', outputText)((id) => (id in deps ? deps[id] : require(id)), exports);
  return exports;
}

const readJson = (file) => JSON.parse(fs.readFileSync(path.isAbsolute(file) ? file : path.join(ROOT, file), 'utf8'));

module.exports = { ROOT, loadTs, readJson };
