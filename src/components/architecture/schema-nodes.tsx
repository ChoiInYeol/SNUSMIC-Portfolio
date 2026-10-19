"use client";

import { createContext, memo, useContext } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Database, FileJson, KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";
import { LAYER_LABEL, type SchemaEntity } from "@/lib/schema-metadata";
import { VISIBLE_COLUMNS, type TableNode, type LayerNode } from "./graph-layout";

export const OpenEntityContext = createContext<(entity: SchemaEntity) => void>(() => {});

export const SchemaTableNode = memo(function SchemaTableNode({ data, selected }: NodeProps<TableNode>) {
  const entity = data.entity;
  const open = useContext(OpenEntityContext);
  const Icon = entity.kind === "dataset" ? FileJson : Database;
  return (
    <div className="schema-node h-full overflow-hidden rounded-md border border-border bg-card text-card-foreground shadow-sm" data-selected={selected} data-layer={entity.layer}>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <div className="schema-node-header border-b border-border px-3 py-2">
        <p className="flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-wider text-muted-foreground"><Icon className="size-4" />{LAYER_LABEL[entity.layer]} · {entity.kind === "dataset" ? "파일 데이터셋" : entity.schema_name}</p>
        <Button variant="ghost" size="sm" className="nodrag mt-1 h-6 w-full justify-start truncate px-0 text-xs font-bold" onClick={() => open(entity)} title={entity.name}>{entity.name}</Button>
      </div>
      <Table className="table-fixed font-mono text-[10px]">
        <TableBody>
          {entity.columns.slice(0, VISIBLE_COLUMNS).map((column) => (
            <TableRow key={column.name} className="h-[26px]">
              <TableCell className="truncate px-3 py-1" title={column.name}>{column.name}</TableCell>
              <TableCell className="w-24 truncate px-3 py-1 text-right text-muted-foreground" title={column.data_type}>
                {column.primary_key || data.foreignKeys.includes(column.name) ? <span className="inline-flex items-center gap-1 text-stamp"><KeyRound className="size-4" />{[column.primary_key && "PK", data.foreignKeys.includes(column.name) && "FK"].filter(Boolean).join("/")}</span> : column.data_type}
              </TableCell>
            </TableRow>
          ))}
          {!entity.columns.length && <TableRow className="h-[26px]"><TableCell className="px-3 py-1 text-muted-foreground">컬럼 정보 없음</TableCell></TableRow>}
        </TableBody>
      </Table>
      <p className="border-t border-border px-3 py-1.5 font-mono text-[9px] tabular-nums text-muted-foreground">{entity.columns.length} fields{entity.columns.length > VISIBLE_COLUMNS ? ` · +${entity.columns.length - VISIBLE_COLUMNS} 상세에서 보기` : ""} · {entity.column_status === "declared" ? "DB 선언" : entity.column_status === "observed" ? "파일 필드 관측" : "미검사"}</p>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
});

export const MedallionLayerNode = memo(function MedallionLayerNode({ data }: NodeProps<LayerNode>) {
  return <div className="medallion-layer h-full w-full rounded-lg border p-5" data-layer={data.layer}><p className="flex items-baseline gap-3 font-sans text-2xl font-bold tracking-tight">{data.label}<span className="font-mono text-sm font-medium tabular-nums">{data.count}</span></p></div>;
});
