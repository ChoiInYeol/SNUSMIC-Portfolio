"use client";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { lineage, LAYER_LABEL, type SchemaEntity, type SchemaMetadata } from "@/lib/schema-metadata";

export function SchemaInspector({ entity, metadata, onSelect, onClose, onFocus }: {
  entity: SchemaEntity | null; metadata: SchemaMetadata; onSelect: (e: SchemaEntity) => void; onClose: () => void;
  onFocus?: (id: string) => void;
}) {
  const links = entity ? metadata.relationships.filter((r) => r.kind === "foreign_key" && (r.source === entity.id || r.target === entity.id)) : [];
  return (
    <Sheet open={entity !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <SheetContent className="architecture-page w-full overflow-y-auto sm:max-w-xl" onCloseAutoFocus={(event) => {
        event.preventDefault();
        if (entity) document.querySelector<HTMLElement>(`.react-flow__node[data-id="${CSS.escape(entity.id)}"] button`)?.focus();
      }}>
        {entity && <>
          <SheetHeader>
            <SheetTitle className="break-all font-sans">{entity.name}</SheetTitle>
            <SheetDescription className="break-all">{LAYER_LABEL[entity.layer]} · {entity.schema_name} · {entity.kind}<br />{entity.location}</SheetDescription>
          </SheetHeader>
          <div className="flex flex-col gap-6 px-4 pb-8 text-sm">
            {onFocus && <Button variant="outline" onClick={() => { onFocus(entity.id); onClose(); }}>이 테이블 주변으로 탐색</Button>}
            <div className="flex flex-col gap-1 text-muted-foreground">
              {entity.description && <p>{entity.description}</p>}
              <p className="font-mono text-xs tabular-nums">행 수: {entity.row_count?.toLocaleString("ko-KR") ?? "미집계"} · 컬럼: {entity.columns.length}</p>
              <p className="text-xs">{entity.column_status === "declared" ? "DB에 선언된 컬럼·키 제약입니다." : entity.column_status === "observed" ? "실제 파일에서 관측한 필드입니다. CSV 타입과 JSON 필드는 DB 제약이 아닙니다." : "로컬 캐시·비정형 파일은 컬럼을 추측하지 않습니다."}</p>
            </div>
            <section aria-label="컬럼과 키">
              <h2 className="mb-2 font-bold">컬럼과 키</h2>
              {entity.columns.length ? <Table className="font-mono text-xs">
                <TableHeader><TableRow><TableHead>이름</TableHead><TableHead>타입</TableHead><TableHead>키</TableHead><TableHead>NULL</TableHead></TableRow></TableHeader>
                <TableBody>{entity.columns.map((c) => <TableRow key={c.name}>
                  <TableCell className="max-w-40 break-all">{c.name}</TableCell><TableCell className="max-w-40 break-all">{c.data_type}</TableCell>
                  <TableCell>{[c.primary_key && "PK", links.some((r) => r.source === entity.id && r.source_columns.includes(c.name)) && "FK"].filter(Boolean).join(" · ") || "—"}</TableCell>
                  <TableCell>{c.nullable === null ? "미정" : c.nullable ? "허용" : "금지"}</TableCell>
                </TableRow>)}</TableBody>
              </Table> : <p className="text-muted-foreground">컬럼 메타데이터가 없습니다.</p>}
            </section>
            <section><h2 className="mb-2 font-bold">Foreign keys</h2>
              {links.length ? <ul className="flex flex-col gap-2">{links.map((r) => <li key={r.id} className="break-all font-mono text-xs">{r.source} ({r.source_columns.join(", ")}) → {r.target} ({r.target_columns.join(", ")})</li>)}</ul> : <p className="text-muted-foreground">선언된 FK 관계가 없습니다.</p>}
            </section>
            {(["upstream", "downstream"] as const).map((direction) => {
              const ids = lineage(metadata, entity.id, direction);
              return <section key={direction}><h2 className="mb-2 font-bold">{direction === "upstream" ? "상류 · 입력" : "하류 · 영향 범위"} <span className="font-mono tabular-nums text-muted-foreground">{ids.length}</span></h2>
                {ids.length ? <ul className="flex flex-col gap-1">{ids.map((id) => {
                  const related = metadata.entities.find((e) => e.id === id)!;
                  return <li key={id}><Button variant="ghost" size="sm" className="h-auto max-w-full justify-start whitespace-normal break-all text-left font-mono text-xs" onClick={() => onSelect(related)}>{id}</Button></li>;
                })}</ul> : <p className="text-muted-foreground">등록된 ETL 계보가 없습니다.</p>}
              </section>;
            })}
            {entity.producers.length > 0 && <section><h2 className="mb-2 font-bold">생성 파이프라인</h2><ul className="flex flex-col gap-1 font-mono text-xs">{entity.producers.map((p) => <li key={p} className="break-all">{p}</li>)}</ul></section>}
          </div>
        </>}
      </SheetContent>
    </Sheet>
  );
}
