"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Background, Controls, ReactFlow, ReactFlowProvider, useReactFlow } from "@xyflow/react";
import { AlertCircle, Download, Maximize, Network, RefreshCw, Search } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useTheme } from "@/components/theme-provider";
import { filterMetadata, LAYERS, LAYER_LABEL, SchemaMetadataSchema, type Layer, type SchemaEntity, type SchemaMetadata } from "@/lib/schema-metadata";
import { layoutMetadata, type GraphLayout } from "./graph-layout";
import { MedallionLayerNode, OpenEntityContext, SchemaTableNode } from "./schema-nodes";
import { SchemaInspector } from "./schema-inspector";
import { graphSvg } from "./svg-export";

const nodeTypes = { schema: SchemaTableNode, layer: MedallionLayerNode };
const ENDPOINT = process.env.NEXT_PUBLIC_METADATA_API_URL ?? "/api/metadata/schema";
const EMPTY_GRAPH: GraphLayout = { nodes: [], edges: [] };

function Explorer() {
  const [metadata, setMetadata] = useState<SchemaMetadata | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [layers, setLayers] = useState<Layer[]>(LAYERS);
  const [edgeKinds, setEdgeKinds] = useState<string[]>(["foreign_key", "etl"]);
  const [graph, setGraph] = useState<GraphLayout>(EMPTY_GRAPH);
  const [layoutInput, setLayoutInput] = useState<SchemaMetadata | null>(null);
  const [layoutBusy, setLayoutBusy] = useState(false);
  const [layoutError, setLayoutError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SchemaEntity | null>(null);
  const [reload, setReload] = useState(0);
  const [layoutRevision, setLayoutRevision] = useState(0);
  const opener = useRef<HTMLElement | null>(null);
  const canvas = useRef<HTMLDivElement | null>(null);
  const flow = useReactFlow();
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 20_000);
    let active = true;
    async function load() {
      setLoading(true); setError(null); setSelected(null); setMetadata(null); setGraph(EMPTY_GRAPH);
      try {
        const response = await fetch(ENDPOINT, { signal: controller.signal, cache: "no-store", headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error(`메타데이터 요청 실패 (HTTP ${response.status}). FastAPI 서버 실행 상태를 확인하세요.`);
        const parsed = SchemaMetadataSchema.safeParse(await response.json());
        if (!parsed.success) throw new Error("메타데이터 계약이 올바르지 않습니다. 서버의 스키마 버전·관계 참조를 확인하세요.");
        if (active) setMetadata(parsed.data);
      } catch (e) {
        if (active) setError(controller.signal.aborted ? "연결 시간이 초과됐습니다. 서버 상태를 확인하고 다시 연결하세요." : e instanceof Error ? e.message : "메타데이터 연결 실패");
      } finally {
        window.clearTimeout(timeout);
        if (active) setLoading(false);
      }
    }
    void load();
    return () => { active = false; controller.abort(); window.clearTimeout(timeout); };
  }, [reload]);

  const filtered = useMemo(() => {
    if (!metadata) return null;
    const doc = filterMetadata(metadata, query, layers);
    return { ...doc, relationships: doc.relationships.filter((r) => edgeKinds.includes(r.kind)) };
  }, [metadata, query, layers, edgeKinds]);

  useEffect(() => {
    let active = true;
    if (!filtered) return;
    async function arrange() {
      setLayoutBusy(true); setLayoutError(null); setGraph(EMPTY_GRAPH);
      try {
        const next = filtered!.entities.length ? await layoutMetadata(filtered!) : EMPTY_GRAPH;
        if (active) setGraph(next);
      } catch {
        if (active) setLayoutError("자동 배치에 실패했습니다. 다시 배치를 실행하거나 검색 범위를 줄여 보세요.");
      } finally { if (active) { setLayoutInput(filtered); setLayoutBusy(false); } }
    }
    // Brief debounce prevents loading several ELK engines while typing a table/column name.
    const timer = window.setTimeout(() => { void arrange(); }, 180);
    return () => { active = false; window.clearTimeout(timer); };
  }, [filtered, layoutRevision]);

  useEffect(() => {
    if (!graph.nodes.length) return;
    const timer = window.setTimeout(() => { void flow.fitView({ padding: 0.12, maxZoom: 0.85 }); }, 100);
    return () => window.clearTimeout(timer);
  }, [graph, flow]);

  useEffect(() => {
    if (!canvas.current) return;
    let timer: ReturnType<typeof setTimeout>;
    const observer = new ResizeObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(() => { void flow.fitView({ padding: 0.12, maxZoom: 0.85 }); }, 150);
    });
    observer.observe(canvas.current);
    return () => { observer.disconnect(); clearTimeout(timer); };
  }, [flow]);

  const open = useCallback((entity: SchemaEntity) => {
    if (document.activeElement instanceof HTMLElement) opener.current = document.activeElement;
    setSelected(entity);
  }, []);
  const exportSvg = () => {
    if (!filtered || !graph.nodes.length) return;
    const style = getComputedStyle(canvas.current ?? document.documentElement);
    const color = (token: string) => `hsl(${style.getPropertyValue(token).trim()})`;
    const svg = graphSvg(filtered, graph, { background: color("--background"), card: color("--card"), foreground: color("--foreground"),
      muted: color("--muted-foreground"), border: color("--border"), accent: color("--architecture-fk"),
      etl: color("--architecture-etl"), layers: Object.fromEntries(LAYERS.map((layer) => [layer, color(`--architecture-${layer}`)])) as Record<Layer, string> });
    const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = "medallion-architecture.svg"; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const busy = loading || layoutBusy || (filtered !== null && layoutInput !== filtered);

  return <div className="flex min-w-0 flex-col gap-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h1 className="font-sans text-3xl font-bold tracking-tight sm:text-4xl">데이터 아키텍처</h1><p className="mt-2 text-sm text-muted-foreground">Bronze → Silver → Gold. 스키마와 처리 흐름을 한눈에.</p></div>
      <Button variant="outline" size="sm" onClick={() => setReload((v) => v + 1)} disabled={loading}><RefreshCw data-icon="inline-start" />다시 연결</Button>
    </div>
    <div className="flex flex-wrap items-center gap-3 border-y border-border py-3">
      <label className="relative min-w-0 flex-1 basis-60"><span className="sr-only">테이블·컬럼 검색</span><Search className="pointer-events-none absolute top-2.5 left-3 size-4 text-muted-foreground" /><Input aria-label="테이블·컬럼 검색" placeholder="테이블, 스키마, 컬럼 검색" value={query} onChange={(e) => setQuery(e.target.value)} className="pl-9" disabled={!metadata} /></label>
      <ToggleGroup className="architecture-filter" type="multiple" value={layers} onValueChange={(values) => setLayers(values.filter((v): v is Layer => LAYERS.includes(v as Layer)))} aria-label="계층 필터" disabled={!metadata}>
        {LAYERS.filter((l) => l !== "unassigned" || metadata?.entities.some((e) => e.layer === l)).map((layer) => <ToggleGroupItem key={layer} value={layer} data-layer={layer} aria-label={`${LAYER_LABEL[layer]} 계층`} className="font-mono text-xs">{LAYER_LABEL[layer]}</ToggleGroupItem>)}
      </ToggleGroup>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={() => setLayoutRevision((v) => v + 1)} disabled={busy || !filtered?.entities.length}><Network data-icon="inline-start" />자동 배치</Button>
        <Button variant="outline" size="sm" onClick={() => { void flow.fitView({ padding: 0.12 }); }} disabled={busy || !graph.nodes.length}><Maximize data-icon="inline-start" />전체 보기</Button>
        <Button variant="outline" size="sm" onClick={exportSvg} disabled={busy || !graph.nodes.length}><Download data-icon="inline-start" />SVG</Button>
      </div>
    </div>
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
      <p className="font-mono tabular-nums" aria-live="polite">{metadata ? `${filtered?.entities.length ?? 0} / ${metadata.entities.length} nodes · ${filtered?.relationships.length ?? 0} relations · ${metadata.source_label}` : "메타데이터 연결 중"}</p>
      <Select value="" disabled={!filtered?.entities.length || busy} onValueChange={(id) => {
        const entity = metadata?.entities.find((e) => e.id === id);
        if (entity) window.setTimeout(() => open(entity), 0);
      }}>
        <SelectTrigger className="w-full sm:w-56" aria-label="노드 바로가기"><SelectValue placeholder="노드 바로가기" /></SelectTrigger>
        <SelectContent>{LAYERS.filter((layer) => filtered?.entities.some((e) => e.layer === layer)).map((layer) => <SelectGroup key={layer}>
          <SelectLabel>{LAYER_LABEL[layer]}</SelectLabel>{filtered?.entities.filter((e) => e.layer === layer).map((e) => <SelectItem key={e.id} value={e.id}>{e.schema_name}.{e.name}</SelectItem>)}
        </SelectGroup>)}</SelectContent>
      </Select>
      <ToggleGroup className="architecture-filter" type="multiple" value={edgeKinds} onValueChange={setEdgeKinds} aria-label="관계 필터" disabled={!metadata}>
        <ToggleGroupItem value="foreign_key" data-relation="foreign_key" aria-label="FK 관계" className="gap-2 text-xs"><span className="architecture-fk inline-block w-5 border-t-2" />FK</ToggleGroupItem>
        <ToggleGroupItem value="etl" data-relation="etl" aria-label="ETL 의존성" className="gap-2 text-xs"><span className="architecture-etl inline-block w-5 border-t-2 border-dashed" />ETL</ToggleGroupItem>
      </ToggleGroup>
    </div>
    {error && <Alert variant="destructive"><AlertCircle /><AlertTitle>연결 실패</AlertTitle><AlertDescription>{error}<Button variant="outline" size="sm" onClick={() => setReload((v) => v + 1)}>다시 시도</Button></AlertDescription></Alert>}
    {layoutError && <Alert variant="destructive"><AlertCircle /><AlertTitle>배치 실패</AlertTitle><AlertDescription>{layoutError}</AlertDescription></Alert>}
    <div ref={canvas} className="architecture-canvas relative h-[65svh] min-h-[420px] overflow-hidden rounded-md border border-border bg-card" aria-label="Medallion Architecture 데이터 계보" aria-busy={busy}>
      {busy && <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-background/90" role="status"><Skeleton className="h-28 w-60" /><p className="text-sm text-muted-foreground">{loading ? "실제 메타데이터를 읽는 중…" : "ELK 자동 배치 중…"}</p></div>}
      {!busy && metadata && !filtered?.entities.length && <Empty className="h-full"><EmptyHeader><EmptyTitle>{metadata.entities.length ? "검색 결과가 없습니다" : "등록된 테이블이 없습니다"}</EmptyTitle><EmptyDescription>{metadata.entities.length ? "검색어 또는 계층 필터를 바꿔 보세요." : "메타데이터 서버의 스키마 설정을 확인하세요."}</EmptyDescription></EmptyHeader></Empty>}
      {!busy && graph.nodes.length > 0 && <OpenEntityContext.Provider value={open}>
        <ReactFlow nodes={graph.nodes} edges={graph.edges} nodeTypes={nodeTypes} onNodeClick={(_, node) => { if (node.type === "schema") open(node.data.entity); }}
          nodesDraggable={false} nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null} minZoom={0.04} maxZoom={2} colorMode={resolvedTheme}
          fitView fitViewOptions={{ padding: 0.12, maxZoom: 0.85 }} aria-label="데이터 스키마 그래프">
          <Background gap={24} color="hsl(var(--border))" /><Controls showInteractive={false} />
        </ReactFlow>
      </OpenEntityContext.Provider>}
    </div>
    <p className="text-xs leading-5 text-muted-foreground">실선 FK: 참조 테이블 → 대상 키 · 점선 ETL: 입력 → 산출물. 노드를 선택하면 전체 컬럼과 상·하류 계보를 볼 수 있습니다. 검색·필터는 SVG에도 적용됩니다.</p>
    {metadata && <p className="break-all font-mono text-[10px] text-muted-foreground">{metadata.source === "database" ? "DB 메타데이터 실시간 조회" : "파일 레이크 메타데이터 · PK/FK를 추측하지 않음"} · {metadata.generated_at} · {ENDPOINT}</p>}
    {metadata && <SchemaInspector entity={selected} metadata={metadata} onSelect={setSelected} onClose={() => { setSelected(null); window.setTimeout(() => opener.current?.focus(), 0); }} />}
  </div>;
}

export default function ArchitectureExplorer() { return <ReactFlowProvider><Explorer /></ReactFlowProvider>; }
