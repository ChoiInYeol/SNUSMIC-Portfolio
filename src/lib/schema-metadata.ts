import { z } from "zod";

// Mirror of scripts/schema_metadata.py. Both API adapters, graph, inspector and export use this model.
export const LayerSchema = z.enum(["bronze", "silver", "gold", "unassigned"]);
const ColumnSchema = z.object({
  name: z.string(), data_type: z.string(), nullable: z.boolean().nullable(),
  primary_key: z.boolean(), description: z.string().nullable(),
}).strict();
const EntitySchema = z.object({
  id: z.string(), name: z.string(), schema_name: z.string(), layer: LayerSchema,
  kind: z.enum(["table", "view", "dataset"]), columns: z.array(ColumnSchema),
  column_status: z.enum(["declared", "observed", "unavailable"]), location: z.string(),
  row_count: z.number().int().nonnegative().nullable(), description: z.string().nullable(), producers: z.array(z.string()),
}).strict();
const RelationshipSchema = z.object({
  id: z.string(), source: z.string(), target: z.string(), kind: z.enum(["foreign_key", "etl"]),
  source_columns: z.array(z.string()), target_columns: z.array(z.string()), label: z.string(),
}).strict();
export const SchemaMetadataSchema = z.object({
  schema_version: z.literal("1.0"), generated_at: z.iso.datetime({ offset: true }),
  source: z.enum(["catalog", "database"]), source_label: z.string(),
  entities: z.array(EntitySchema), relationships: z.array(RelationshipSchema),
}).strict().superRefine((doc, ctx) => {
  const ids = new Map(doc.entities.map((e) => [e.id, e]));
  const invalid = (message: string) => ctx.addIssue({ code: "custom", message });
  if (ids.size !== doc.entities.length) invalid("Duplicate entity ids");
  if (new Set(doc.relationships.map((r) => r.id)).size !== doc.relationships.length) invalid("Duplicate relationship ids");
  for (const entity of doc.entities) {
    if (new Set(entity.columns.map((c) => c.name)).size !== entity.columns.length) invalid(`Duplicate columns: ${entity.id}`);
  }
  for (const r of doc.relationships) {
    if (!ids.has(r.source) || !ids.has(r.target)) invalid(`Unresolved relationship: ${r.id}`);
    if (r.kind === "foreign_key") {
      if (!r.source_columns.length || r.source_columns.length !== r.target_columns.length) invalid(`Invalid FK: ${r.id}`);
      for (const [id, columns] of [[r.source, r.source_columns], [r.target, r.target_columns]] as const) {
        if (columns.some((c) => !ids.get(id)?.columns.some((col) => col.name === c))) invalid(`Unknown FK column: ${r.id}`);
      }
    }
  }
});
export type SchemaMetadata = z.infer<typeof SchemaMetadataSchema>;
export type SchemaEntity = SchemaMetadata["entities"][number];
export type SchemaRelationship = SchemaMetadata["relationships"][number];
export type Layer = z.infer<typeof LayerSchema>;
export const LAYERS: Layer[] = ["bronze", "silver", "gold", "unassigned"];
export const LAYER_LABEL: Record<Layer, string> = { bronze: "Bronze", silver: "Silver", gold: "Gold", unassigned: "미분류" };

/** ETL direction is source → output. FK direction is referencing table → referenced table. */
export function lineage(doc: SchemaMetadata, id: string, direction: "upstream" | "downstream"): string[] {
  const visited = new Set([id]);
  const queue = [id];
  for (let i = 0; i < queue.length; i++) {
    for (const edge of doc.relationships) {
      if (edge.kind !== "etl") continue;
      const from = direction === "upstream" ? edge.target : edge.source;
      const to = direction === "upstream" ? edge.source : edge.target;
      if (from === queue[i] && !visited.has(to)) { visited.add(to); queue.push(to); }
    }
  }
  return queue.slice(1);
}

export function filterMetadata(doc: SchemaMetadata, query: string, layers: Layer[]): SchemaMetadata {
  const q = query.trim().toLocaleLowerCase();
  const entities = doc.entities.filter((e) => layers.includes(e.layer) &&
    (!q || [e.name, e.schema_name, e.location, ...e.columns.map((c) => c.name)].some((s) => s.toLocaleLowerCase().includes(q))));
  const ids = new Set(entities.map((e) => e.id));
  return { ...doc, entities, relationships: doc.relationships.filter((r) => ids.has(r.source) && ids.has(r.target)) };
}

export function focusMetadata(doc: SchemaMetadata, id: string | null, mode: string): SchemaMetadata {
  if (!id || mode === "all") return doc;
  const ids = new Set([id]);
  if (mode === "neighbors") for (const r of doc.relationships) {
    if (r.source === id) ids.add(r.target);
    if (r.target === id) ids.add(r.source);
  }
  else for (const related of lineage(doc, id, mode === "upstream" ? "upstream" : "downstream")) ids.add(related);
  return { ...doc, entities: doc.entities.filter(e => ids.has(e.id)), relationships: doc.relationships.filter(r => ids.has(r.source) && ids.has(r.target)) };
}

/** Compare structural metadata only: changing collection timestamps or row counts is not DDL. */
export function compareMetadata(before: SchemaMetadata, after: SchemaMetadata): { id: string; change: string }[] {
  const old = new Map(before.entities.map(e => [e.id, e]));
  const now = new Set(after.entities.map(e => e.id));
  const shape = (e: SchemaEntity) => JSON.stringify({ layer: e.layer, kind: e.kind,
    columns: [...e.columns].sort((a, b) => a.name.localeCompare(b.name)),
  });
  const changes = after.entities.flatMap(e => !old.has(e.id) ? [{ id: e.id, change: "추가" }] : shape(old.get(e.id)!) !== shape(e) ? [{ id: e.id, change: "컬럼·키·계층 변경" }] : []);
  for (const id of old.keys()) if (!now.has(id)) changes.push({ id, change: "삭제" });
  const relationShape = (doc: SchemaMetadata) => new Map(doc.relationships.map(r => [r.id, JSON.stringify(r)]));
  const a = relationShape(before), b = relationShape(after);
  for (const id of new Set([...a.keys(), ...b.keys()])) if (a.get(id) !== b.get(id)) changes.push({ id, change: !a.has(id) ? "관계 추가" : !b.has(id) ? "관계 삭제" : "관계 변경" });
  return changes;
}
