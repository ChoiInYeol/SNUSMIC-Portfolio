import { promises as fs } from "node:fs";
import path from "node:path";
import { z } from "zod";

// Build-time view of public/api/v2/openapi.json for /developers. Only the parts the page renders are validated;
// a spec that lacks them fails the build instead of rendering an empty reference.

const RefSchema = z.object({ $ref: z.string() });
const JsonSchema: z.ZodType<Record<string, unknown>> = z.record(z.string(), z.unknown());

const ParameterSchema = z.object({
  name: z.string(),
  in: z.enum(["path", "query", "header", "cookie"]),
  required: z.boolean().optional(),
  description: z.string().optional(),
  schema: JsonSchema.optional(),
  examples: z.record(z.string(), z.object({ value: z.unknown(), summary: z.string().optional() })).optional(),
});
const HeaderSchema = z.object({ description: z.string().optional(), example: z.unknown().optional() });
const MediaSchema = z.object({ schema: JsonSchema.optional(), example: z.unknown().optional() });
const ResponseSchema = z.object({
  description: z.string(),
  headers: z.record(z.string(), z.union([RefSchema, HeaderSchema])).optional(),
  content: z.record(z.string(), MediaSchema).optional(),
});
const OperationSchema = z.object({
  tags: z.array(z.string()).min(1),
  operationId: z.string(),
  summary: z.string(),
  description: z.string().optional(),
  deprecated: z.boolean().optional(),
  parameters: z.array(z.union([RefSchema, ParameterSchema])).optional(),
  responses: z.record(z.string(), z.union([RefSchema, ResponseSchema])),
});
const SpecSchema = z.object({
  openapi: z.string(),
  info: z.object({ title: z.string(), version: z.string(), summary: z.string().optional() }),
  servers: z.array(z.object({ url: z.string(), description: z.string().optional() })).min(1),
  tags: z.array(z.object({ name: z.string(), description: z.string().optional() })),
  paths: z.record(z.string(), z.record(z.string(), z.unknown())),
  components: z.object({
    schemas: z.record(z.string(), JsonSchema),
    parameters: z.record(z.string(), ParameterSchema).default({}),
    headers: z.record(z.string(), HeaderSchema).default({}),
    responses: z.record(z.string(), ResponseSchema).default({}),
  }),
});
type Spec = z.infer<typeof SpecSchema>;

export type CachePolicy = "daily" | "snapshot" | "immutable" | "none";

export type ParamModel = {
  name: string;
  in: string;
  required: boolean;
  description: string;
  type: string;
  pattern: string | null;
  examples: { label: string; value: string }[];
};
export type FieldModel = { name: string; type: string; required: boolean; description: string };
export type ResponseModel = {
  status: string;
  description: string;
  contentType: string | null;
  schemaName: string | null;
  headers: { name: string; description: string; example: string | null }[];
};
export type OperationModel = {
  id: string;
  method: string;
  path: string;
  tag: string;
  summary: string;
  description: string;
  deprecated: boolean;
  params: ParamModel[];
  responses: ResponseModel[];
  cache: { policy: CachePolicy; value: string | null; description: string };
  /** Shape of the success body: the envelope's `data` when enveloped, the document itself otherwise. */
  body: { label: string; enveloped: boolean; fields: FieldModel[] } | null;
  /** Spec example of the 200 body, pretty-printed and cut to a readable length. */
  example: { text: string; truncated: boolean } | null;
};
export type ApiReference = {
  title: string;
  version: string;
  openapi: string;
  server: string;
  tags: { name: string; description: string; operations: OperationModel[] }[];
  operationCount: number;
};

const refName = (ref: string) => ref.split("/").pop() ?? ref;
const isRef = (value: unknown): value is { $ref: string } => typeof value === "object" && value !== null && "$ref" in value;

function typeOf(schema: Record<string, unknown> | undefined): string {
  if (!schema) return "any";
  if (typeof schema.$ref === "string") return refName(schema.$ref);
  if ("const" in schema) return JSON.stringify(schema.const);
  if (Array.isArray(schema.enum)) return schema.enum.map((v) => JSON.stringify(v)).join(" | ");
  const type = Array.isArray(schema.type) ? schema.type.join(" | ") : typeof schema.type === "string" ? schema.type : null;
  if (type?.startsWith("array")) {
    const items = schema.items as Record<string, unknown> | undefined;
    return `${typeOf(items)}[]${type.includes("null") ? " | null" : ""}`;
  }
  const format = typeof schema.format === "string" ? ` (${schema.format})` : "";
  return `${type ?? "object"}${format}`;
}

function fieldsOf(spec: Spec, schema: Record<string, unknown> | undefined): FieldModel[] {
  const resolved = schema && typeof schema.$ref === "string" ? spec.components.schemas[refName(schema.$ref)] : schema;
  const properties = resolved?.properties as Record<string, Record<string, unknown>> | undefined;
  if (!properties) return [];
  const required = new Set((resolved?.required as string[] | undefined) ?? []);
  return Object.entries(properties).map(([name, prop]) => {
    const target = typeof prop.$ref === "string" ? spec.components.schemas[refName(prop.$ref)] : undefined;
    return {
      name,
      type: typeOf(prop),
      required: required.has(name),
      description: String(prop.description ?? target?.description ?? ""),
    };
  });
}

function bodyOf(spec: Spec, schemaName: string | null): OperationModel["body"] {
  if (!schemaName) return null;
  const doc = spec.components.schemas[schemaName];
  if (!doc) return null;
  const data = (doc.properties as Record<string, Record<string, unknown>> | undefined)?.data;
  if (data) {
    const items = data.items as Record<string, unknown> | undefined;
    const target = items ?? data;
    return { label: `data: ${typeOf(data)}`, enveloped: true, fields: fieldsOf(spec, target) };
  }
  return { label: schemaName, enveloped: false, fields: fieldsOf(spec, { $ref: `#/components/schemas/${schemaName}` }) };
}

const CACHE_BY_HEADER: Record<string, CachePolicy> = {
  CacheControlDaily: "daily",
  CacheControlSnapshot: "snapshot",
  CacheControlImmutable: "immutable",
};

function exampleOf(value: unknown): OperationModel["example"] {
  if (value === undefined) return null;
  const lines = JSON.stringify(value, null, 2).split("\n");
  const max = 48;
  return { text: lines.slice(0, max).join("\n"), truncated: lines.length > max };
}

export async function loadApiReference(): Promise<ApiReference> {
  const raw = await fs.readFile(path.join(process.cwd(), "public", "api", "v2", "openapi.json"), "utf-8");
  const spec = SpecSchema.parse(JSON.parse(raw));
  const operations: OperationModel[] = [];

  for (const [route, item] of Object.entries(spec.paths)) {
    for (const [method, value] of Object.entries(item)) {
      if (!["get", "head", "options", "post", "put", "patch", "delete"].includes(method)) continue;
      const op = OperationSchema.parse(value);
      const params: ParamModel[] = (op.parameters ?? []).map((entry) => {
        const p = isRef(entry) ? spec.components.parameters[refName(entry.$ref)] : entry;
        if (!p) throw new Error(`${op.operationId}: unresolved parameter ${JSON.stringify(entry)}`);
        const schema = p.schema && typeof p.schema.$ref === "string" ? spec.components.schemas[refName(p.schema.$ref)] : p.schema;
        return {
          name: p.name,
          in: p.in,
          required: p.required ?? false,
          description: p.description ?? String(schema?.description ?? ""),
          type: typeOf(p.schema),
          pattern: typeof schema?.pattern === "string" ? schema.pattern : null,
          examples: Object.entries(p.examples ?? {}).map(([label, ex]) => ({ label: ex.summary ?? label, value: String(ex.value) })),
        };
      });

      let cache: OperationModel["cache"] = { policy: "none", value: null, description: "" };
      let body: OperationModel["body"] = null;
      let example: OperationModel["example"] = null;
      const responses: ResponseModel[] = Object.entries(op.responses).map(([status, entry]) => {
        const r = isRef(entry) ? spec.components.responses[refName(entry.$ref)] : entry;
        if (!r) throw new Error(`${op.operationId}: unresolved response ${status}`);
        const [contentType, media] = Object.entries(r.content ?? {})[0] ?? [null, null];
        const schemaName = media?.schema && typeof media.schema.$ref === "string" ? refName(media.schema.$ref) : null;
        const headers = Object.entries(r.headers ?? {}).map(([name, h]) => {
          const header = isRef(h) ? spec.components.headers[refName(h.$ref)] : h;
          if (status.startsWith("2") && name === "Cache-Control" && isRef(h)) {
            const policy = CACHE_BY_HEADER[refName(h.$ref)];
            if (policy) cache = { policy, value: header?.example ? String(header.example) : null, description: header?.description ?? "" };
          }
          return { name, description: header?.description ?? "", example: header?.example !== undefined ? String(header.example) : null };
        });
        if (status === "200") {
          body = bodyOf(spec, schemaName);
          example = exampleOf(media?.example);
        }
        return { status, description: r.description, contentType, schemaName, headers };
      });

      operations.push({
        id: op.operationId,
        method: method.toUpperCase(),
        path: route,
        tag: op.tags[0],
        summary: op.summary,
        description: op.description ?? "",
        deprecated: op.deprecated ?? false,
        params,
        responses,
        cache,
        body,
        example,
      });
    }
  }

  const tags = spec.tags
    .map((tag) => ({ name: tag.name, description: tag.description ?? "", operations: operations.filter((op) => op.tag === tag.name) }))
    .filter((tag) => tag.operations.length > 0);
  const untagged = operations.filter((op) => !spec.tags.some((tag) => tag.name === op.tag));
  if (untagged.length) throw new Error(`Operations with undeclared tags: ${untagged.map((op) => op.id).join(", ")}`);

  return {
    title: spec.info.title,
    version: spec.info.version,
    openapi: spec.openapi,
    server: spec.servers[0].url,
    tags,
    operationCount: operations.length,
  };
}
