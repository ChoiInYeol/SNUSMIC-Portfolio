import { LAYER_LABEL, type Layer, type SchemaMetadata } from "@/lib/schema-metadata";
import { NODE_WIDTH, VISIBLE_COLUMNS, nodeHeight, type GraphLayout } from "./graph-layout";

const esc = (text: string) => text.replace(/[<>&"']/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" })[c]!);
const short = (text: string, limit: number) => text.length > limit ? text.slice(0, limit - 1) + "…" : text;
export type GraphPalette = { background: string; card: string; foreground: string; muted: string; border: string; accent: string;
  etl?: string; layers?: Record<Layer, string> };

/** Portable SVG: real vector text/paths, no foreignObject, remote fonts or DOM screenshot. */
export function graphSvg(doc: SchemaMetadata, graph: GraphLayout, palette: GraphPalette): string {
  const entities = new Map(doc.entities.map((e) => [e.id, e]));
  const groups = graph.nodes.filter((n) => n.type === "layer");
  const positions = new Map(graph.nodes.filter((n) => n.type === "schema").map((n) => {
    const parent = groups.find((g) => g.id === n.parentId);
    return [n.id, { x: n.position.x + (parent?.position.x ?? 0), y: n.position.y + (parent?.position.y ?? 0) }];
  }));
  const width = Math.max(400, ...groups.map((g) => g.position.x + Number(g.style?.width ?? 0))) + 40;
  const height = Math.max(200, ...groups.map((g) => g.position.y + Number(g.style?.height ?? 0))) + 90;
  const text = (x: number, y: number, value: string, size = 12, bold = false) =>
    `<text x="${x}" y="${y}" font-size="${size}"${bold ? ' font-weight="700"' : ""}>${esc(value)}</text>`;
  const shapes: string[] = [];
  for (const group of groups) {
    const layer = group.type === "layer" ? group.data.layer : "unassigned";
    const layerColor = palette.layers?.[layer] ?? palette.border;
    shapes.push(`<rect x="${group.position.x}" y="${group.position.y + 50}" width="${group.style?.width}" height="${group.style?.height}" rx="6" fill="${esc(layerColor)}" fill-opacity="0.07" stroke="${esc(layerColor)}" stroke-opacity="0.4"/>`);
    shapes.push(text(group.position.x + 24, group.position.y + 80, group.data.label + "", 18, true));
  }
  for (const edge of doc.relationships) {
    const presentation = graph.edges.find(e => e.id === edge.id)?.style;
    const source = positions.get(edge.source), target = positions.get(edge.target);
    if (!source || !target) continue;
    const sx = source.x + NODE_WIDTH, sy = source.y + 50 + nodeHeight(entities.get(edge.source)!) / 2;
    const tx = target.x, ty = target.y + 50 + nodeHeight(entities.get(edge.target)!) / 2;
    shapes.push(`<path d="M${sx} ${sy} C${sx + 65} ${sy},${tx - 65} ${ty},${tx} ${ty}" fill="none" stroke="${esc(edge.kind === "foreign_key" ? palette.accent : palette.etl ?? palette.muted)}" stroke-width="${Number(presentation?.strokeWidth ?? 1.8)}" opacity="${Number(presentation?.opacity ?? 1)}"${edge.kind === "etl" ? ' stroke-dasharray="6 4"' : ""} marker-end="url(#${edge.kind})"/>`);
    if (edge.kind === "foreign_key") shapes.push(text((sx + tx) / 2, (sy + ty) / 2 - 5, "FK", 10));
  }
  for (const entity of doc.entities) {
    const position = positions.get(entity.id);
    if (!position) continue;
    const x = position.x, y = position.y + 50;
    shapes.push(`<g><title>${esc(entity.location)}</title><rect x="${x}" y="${y}" width="${NODE_WIDTH}" height="${nodeHeight(entity)}" rx="6" fill="${esc(palette.card)}" stroke="${esc(palette.border)}"/>`);
    shapes.push(`<path d="M${x + 3} ${y + 2}H${x + NODE_WIDTH - 3}" stroke="${esc(palette.layers?.[entity.layer] ?? palette.border)}" stroke-width="3"/>`);
    shapes.push(text(x + 12, y + 20, `${LAYER_LABEL[entity.layer]} · ${entity.kind}`, 10));
    shapes.push(text(x + 12, y + 42, short(entity.name, 28), 14, true));
    if (!entity.columns.length) shapes.push(text(x + 12, y + 72, "컬럼 메타데이터 없음", 11));
    entity.columns.slice(0, VISIBLE_COLUMNS).forEach((column, i) => {
      shapes.push(text(x + 12, y + 72 + i * 26, short(column.name, 22), 11));
      const fk = doc.relationships.some((r) => r.kind === "foreign_key" && r.source === entity.id && r.source_columns.includes(column.name));
      shapes.push(text(x + 216, y + 72 + i * 26, [column.primary_key && "PK", fk && "FK"].filter(Boolean).join("/") || short(column.data_type, 7), 10));
    });
    shapes.push(text(x + 12, y + nodeHeight(entity) - 12, `${entity.columns.length} fields · ${entity.schema_name}`, 10));
    shapes.push("</g>");
  }
  const markers = [["foreign_key", palette.accent], ["etl", palette.etl ?? palette.muted]].map(([id, color]) => `<marker id="${id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="${esc(color)}"/></marker>`).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img"><title>Medallion Architecture · ${esc(doc.source_label)}</title><defs>${markers}</defs><rect width="100%" height="100%" fill="${esc(palette.background)}"/><g font-family="sans-serif" fill="${esc(palette.foreground)}">${text(24, 28, "Medallion Architecture · FK 실선 / ETL 점선", 16, true)}${shapes.join("")}</g></svg>`;
}
