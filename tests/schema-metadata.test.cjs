const assert = require('node:assert/strict');
const test = require('node:test');
const { loadTs: compile, readJson } = require('./_ts-module.cjs');

const model = compile('src/lib/schema-metadata.ts');
const layout = compile('src/components/architecture/graph-layout.ts', { '@/lib/schema-metadata': model });
const svg = compile('src/components/architecture/svg-export.ts', { '@/lib/schema-metadata': model, './graph-layout': layout });
const raw = readJson('public/metadata/schema.json');
const doc = model.SchemaMetadataSchema.parse(raw);

test('focus scopes and structural comparisons share the validated model', () => {
  const id = 'silver.report_parsed';
  const scoped = model.focusMetadata(doc, id, 'upstream');
  assert.ok(scoped.entities.some(e => e.id === id));
  assert.ok(scoped.entities.some(e => e.id === 'bronze.report_pdfs'));
  assert.ok(!scoped.entities.some(e => e.id === 'serving.api_v2'));
  const copy = structuredClone(doc); copy.generated_at = '2026-10-20T00:00:00Z'; copy.entities[0].row_count = 9999;
  assert.deepEqual(model.compareMetadata(doc, copy), []);
  copy.entities[0].columns.push({name: 'new_field', data_type: 'string', nullable: true, primary_key: false});
  assert.ok(model.compareMetadata(doc, copy).some(c => c.id === copy.entities[0].id));
});

test('Python metadata snapshot satisfies the frontend boundary contract', () => {
  assert.ok(doc.entities.length >= 26);
  const bad = structuredClone(doc);
  bad.relationships[0].target = 'missing';
  assert.equal(model.SchemaMetadataSchema.safeParse(bad).success, false);
});

test('filters match column names and retain only edges whose endpoints remain', () => {
  const filtered = model.filterMetadata(doc, 'source_file', ['silver']);
  assert.ok(filtered.entities.length > 0);
  assert.ok(filtered.entities.every((e) => e.layer === 'silver'));
  const ids = new Set(filtered.entities.map((e) => e.id));
  assert.ok(filtered.relationships.every((r) => ids.has(r.source) && ids.has(r.target)));
  assert.deepEqual(model.filterMetadata(doc, '', []).entities, []);
});

test('transitive lineage is cycle-safe and keeps FK separate from ETL', () => {
  const upstream = model.lineage(doc, 'gold.report_performance', 'upstream');
  assert.ok(upstream.includes('silver.report_parsed') && upstream.includes('bronze.report_manifest'));
  assert.equal(upstream.includes('gold.report_performance'), false);
  const cycled = structuredClone(doc);
  cycled.relationships.push({ id: 'test-cycle', source: 'gold.report_performance', target: 'bronze.report_manifest', kind: 'etl', source_columns: [], target_columns: [], label: 'test' });
  assert.equal(new Set(model.lineage(cycled, 'gold.report_performance', 'upstream')).size, upstream.length);
});

test('ELK lays out actual entities inside layer groups and SVG escapes metadata', async () => {
  const graph = await layout.layoutMetadata(doc);
  const tables = graph.nodes.filter((n) => n.type === 'schema');
  assert.equal(tables.length, doc.entities.length);
  assert.equal(graph.edges.length, doc.relationships.length);
  for (const node of tables) {
    const parent = graph.nodes.find((p) => p.id === node.parentId);
    assert.ok(parent && node.position.x >= 0 && node.position.y >= 0);
    assert.ok(node.position.x + node.width <= parent.style.width + 1);
    assert.ok(node.position.y + node.height <= parent.style.height + 1);
  }
  const changed = structuredClone(doc);
  changed.entities[0].name = '<script>alert("x")</script>';
  const output = svg.graphSvg(changed, graph, { background: 'white', card: 'white', foreground: 'black', muted: 'gray', border: 'gray', accent: 'red' });
  assert.ok(output.startsWith('<svg'));
  assert.ok(output.includes('&lt;script&gt;'));
  assert.ok(!output.includes('<script>') && !output.includes('foreignObject'));
});
