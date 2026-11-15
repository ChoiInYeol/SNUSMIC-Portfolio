// Fail locally/CI before a deployment exceeds the server function size budget.
const fs = require('node:fs');
const path = require('node:path');

function auditTraces(root, budget = 200 * 1024 * 1024) {
  const sizes = new Map();
  const traces = [];
  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(file);
      else if (entry.name.endsWith('.nft.json')) traces.push(file);
    }
  }
  walk(root);
  if (!traces.length) throw new Error(`No server traces found in ${root}`);
  const results = [];
  for (const trace of traces) {
    let total = 0;
    for (const rel of JSON.parse(fs.readFileSync(trace, 'utf8')).files) {
      const file = path.resolve(path.dirname(trace), rel);
      if (/\/public\/api\/v2\/strategies\/[^/]+\/revisions\//.test(file.replaceAll('\\', '/'))) {
        throw new Error(`${trace} bundles a CDN-only historical revision: ${file}`);
      }
      if (!sizes.has(file)) sizes.set(file, fs.statSync(file).size);
      total += sizes.get(file);
    }
    if (total > budget) throw new Error(`${trace}: ${(total / 1024 ** 2).toFixed(2)} MiB exceeds server trace budget`);
    results.push({ trace, bytes: total });
  }
  return results.sort((a, b) => b.bytes - a.bytes);
}

if (require.main === module) {
  const results = auditTraces(path.join(process.cwd(), '.next/server'));
  console.log(`Server traces: ${results.length} checked; largest ${(results[0].bytes / 1024 ** 2).toFixed(2)} MiB (${path.relative(process.cwd(), results[0].trace)})`);
}
module.exports = { auditTraces };
