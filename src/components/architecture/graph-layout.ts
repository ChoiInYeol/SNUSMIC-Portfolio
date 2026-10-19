import type { Node, Edge } from "@xyflow/react";
import { LAYERS, LAYER_LABEL, type SchemaEntity, type SchemaMetadata } from "@/lib/schema-metadata";

export const NODE_WIDTH = 280;
export const VISIBLE_COLUMNS = 5;
export const nodeHeight = (entity: SchemaEntity) => 92 + Math.max(1, Math.min(entity.columns.length, VISIBLE_COLUMNS)) * 26;
export type TableNode = Node<{ entity: SchemaEntity; foreignKeys: string[] }, "schema">;
export type LayerNode = Node<{ label: string; count: number }, "layer">;
export type GraphNode = TableNode | LayerNode;
export type GraphLayout = { nodes: GraphNode[]; edges: Edge[] };

export async function layoutMetadata(doc: SchemaMetadata): Promise<GraphLayout> {
  const { default: ELK } = await import("elkjs/lib/elk.bundled.js");
  const elk = new ELK();
  // elk.bundled uses an in-process worker shim; it owns no browser Worker to terminate.
  {
    const result = await elk.layout({
      id: "root",
      layoutOptions: {
        "elk.algorithm": "layered", "elk.direction": "RIGHT", "elk.hierarchyHandling": "INCLUDE_CHILDREN",
        "elk.spacing.nodeNode": "60", "elk.layered.spacing.nodeNodeBetweenLayers": "90",
        "elk.padding": "[top=20,left=20,bottom=20,right=20]",
      },
      children: LAYERS.filter((l) => doc.entities.some((e) => e.layer === l)).map((layer) => ({
        id: `layer:${layer}`,
        layoutOptions: { "elk.padding": "[top=64,left=24,bottom=24,right=24]" },
        children: doc.entities.filter((e) => e.layer === layer).map((e) => ({ id: e.id, width: NODE_WIDTH, height: nodeHeight(e) })),
      })),
      edges: doc.relationships.map((r) => ({ id: r.id, sources: [r.source], targets: [r.target] })),
    });
    const entities = new Map(doc.entities.map((e) => [e.id, e]));
    const nodes: GraphNode[] = [];
    for (const group of result.children ?? []) {
      const layer = LAYERS.find((l) => group.id === `layer:${l}`)!;
      nodes.push({ id: group.id, type: "layer", position: { x: group.x ?? 0, y: group.y ?? 0 },
        data: { label: LAYER_LABEL[layer], count: group.children?.length ?? 0 },
        style: { width: group.width, height: group.height }, selectable: false, draggable: false });
      for (const child of group.children ?? []) {
        nodes.push({ id: child.id, type: "schema", parentId: group.id, extent: "parent",
          position: { x: child.x ?? 0, y: child.y ?? 0 }, width: NODE_WIDTH, height: child.height,
          data: { entity: entities.get(child.id)!, foreignKeys: doc.relationships.filter((r) => r.kind === "foreign_key" && r.source === child.id).flatMap((r) => r.source_columns) }, draggable: false,
          ariaLabel: `${entities.get(child.id)!.schema_name}.${entities.get(child.id)!.name} 상세 정보` });
      }
    }
    return { nodes, edges: doc.relationships.map((r) => ({
      id: r.id, source: r.source, target: r.target, type: "smoothstep", label: r.kind === "foreign_key" ? "FK" : undefined,
      ariaLabel: `${r.kind === "foreign_key" ? "FK" : "ETL"}: ${r.source} → ${r.target}`,
      style: { stroke: r.kind === "foreign_key" ? "hsl(var(--stamp))" : "hsl(var(--muted-foreground))", strokeWidth: 1.5,
        strokeDasharray: r.kind === "etl" ? "6 4" : undefined },
      markerEnd: { type: "arrowclosed" as const, color: r.kind === "foreign_key" ? "hsl(var(--stamp))" : "hsl(var(--muted-foreground))" },
    })) };
  }
}
