import type { Metadata } from "next";
import Link from "next/link";
import { ExternalLink, FileJson } from "lucide-react";
import { ApiResourceLink } from "@/components/api-resource-link";
import { CACHE_LABEL, InlineMd, MethodStamp, OperationCard, UriTemplate } from "@/components/developers/operation-card";
import type { ConsoleParam } from "@/components/developers/operation-console";
import { SiteHeader } from "@/components/site-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StrategyDocumentSchema } from "@/lib/api-v2";
import { loadLatestSnapshot, readV2, v2Exists } from "@/lib/api-v2-server";
import { loadApiReference, type ApiReference, type OperationModel } from "@/lib/openapi-model";

export const metadata: Metadata = {
  title: "API 레퍼런스 — 판결 아카이브",
  description: "판결 아카이브 REST API v2 — 리소스 트리, HTTP 의미론, 캐시 정책, 코드 예제와 브라우저에서 바로 보내는 Try it.",
};

const fill = (template: string, values: Record<string, string>) =>
  template.replace(/\{([^}]+)\}/g, (_, name: string) => values[name] ?? `{${name}}`);

/** Prefill values that resolve to real resources today (spec examples can go stale, e.g. revision hashes). */
async function liveDefaults(): Promise<Record<string, string>> {
  const latest = await loadLatestSnapshot();
  const headline = latest.data.headline_strategy;
  const strategy = await readV2(`/api/v2/strategies/${headline}`, StrategyDocumentSchema);
  const sha256 = strategy.links.current_revision.split("/").pop() ?? "";
  const held = [...latest.data.open_positions, ...latest.data.buy_signals]
    .map((item) => item.links?.instrument?.split("/").pop())
    .filter((id): id is string => Boolean(id));
  let instrument = "kr-000020";
  for (const id of held) {
    if (await v2Exists(`/api/v2/instruments/${id}/trade-marks`)) {
      instrument = id;
      break;
    }
  }
  return { date: latest.data.as_of, strategy_id: headline, sha256, instrument_id: instrument, club_id: "smic" };
}

async function consoleParams(op: OperationModel, defaults: Record<string, string>): Promise<ConsoleParam[]> {
  return Promise.all(
    op.params
      .filter((p) => p.in === "path")
      .map(async (p) => {
        const examples = [];
        for (const ex of p.examples) {
          if (ex.value !== defaults[p.name] && (await v2Exists(fill(op.path, { ...defaults, [p.name]: ex.value })))) examples.push(ex);
        }
        return { name: p.name, pattern: p.pattern, description: p.description, initial: defaults[p.name] ?? "", examples };
      }),
  );
}

const PRINCIPLES: { title: string; body: string }[] = [
  {
    title: "URI는 리소스를 가리킨다",
    body: "복수 명사, 확장자·동사 없음 (`/api/v2/strategies/{id}/trades`). 식별자는 버전을 넘어 안정적이다 — 전략 id는 파이프라인 키, 종목 id는 `{market}-{ticker}`, 학회 id는 학교 코드.",
  },
  {
    title: "메서드가 의미를 싣는다",
    body: "파이프라인 출력을 발행하는 읽기 전용 API — 모든 리소스가 `GET`·`HEAD`(안전·멱등·캐시 가능)와 `OPTIONS`(CORS)에 답한다. 쓰기 메서드는 없다: 데이터는 검토된 파이프라인(CI)으로만 바뀐다.",
  },
  {
    title: "상태 코드는 정직하게",
    body: "`200` 있음 · `304` 바뀌지 않음(ETag) · `404` 없는 리소스, `application/problem+json`(RFC 9457) · `405` 그 외 메서드. 실패를 200 본문에 숨기지 않는다.",
  },
  {
    title: "캐시는 가변성을 따른다",
    body: "일간 리소스는 짧은 `max-age` + `stale-while-revalidate`, 날짜 스냅샷은 박제(발행 후 불변), sha256 리비전은 `immutable`. 정적 호스팅의 ETag로 조건부 요청이 304를 받는다.",
  },
  {
    title: "균일한 봉투, 링크로 탐색",
    body: "모든 문서는 `{schema_version, links, meta, data}`. 컬렉션 항목은 `links.self`를 갖는다 — URI를 조립하지 말고 `/api/v2`에서 링크를 따라가라.",
  },
  {
    title: "CRUD가 아닌 것은 그대로",
    body: "신호 스냅샷·전략 리비전처럼 CRUD로 접히지 않는 도메인 사건은 동사를 만들지 않고 읽기 전용 리소스로 모델링한다.",
  },
];

const MIGRATION: { v1: string; v2: string; note?: string }[] = [
  { v1: "/api/v1/signals/latest.json", v2: "/api/v2/signals/latest" },
  { v1: "/api/v1/signals/{date}.json", v2: "/api/v2/signals/{date}" },
  { v1: "/api/v1/strategies.json", v2: "/api/v2/strategies", note: "strategies[].key → data[].id, links.self" },
  { v1: "/api/v1/trades/{key}.json", v2: "/api/v2/strategies/{id}/trades" },
  { v1: "/api/v1/strategy-details/manifest.json", v2: "/api/v2/strategies/{id}", note: "links.current_revision" },
  { v1: "/api/v1/strategy-details/{key}/{hash}.json", v2: "/api/v2/strategies/{id}/revisions/{sha256}", note: "같은 바이트" },
  { v1: "/api/v1/forward.json", v2: "/api/v2/track-record" },
  { v1: "/prices/{slug}.json", v2: "/api/v2/instruments/{id}/prices", note: "옛 경로는 308" },
  { v1: "/strategy-marks/{slug}.json", v2: "/api/v2/instruments/{id}/trade-marks", note: "옛 경로는 308" },
];

function ResourceTree({ reference }: { reference: ApiReference }) {
  return (
    <nav aria-label="리소스 트리" className="space-y-4">
      {reference.tags.map((tag) => (
        <div key={tag.name}>
          <p className="font-mono text-[10px] font-bold uppercase tracking-[0.22em] text-muted-foreground">{tag.name}</p>
          <ul className="mt-1 space-y-0.5">
            {tag.operations.map((op) => (
              <li key={op.id}>
                <a
                  href={`#${op.id}`}
                  className="flex min-h-7 items-center gap-1.5 rounded-sm px-1 font-mono text-[11px] hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <MethodStamp method={op.method} small />
                  <UriTemplate path={op.path.replace(/^\/api\/v2/, "") || "/"} className="truncate" />
                </a>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

export default async function DevelopersPage() {
  const [reference, defaults] = await Promise.all([loadApiReference(), liveDefaults()]);
  const cards = await Promise.all(
    reference.tags.map(async (tag) => ({
      ...tag,
      operations: await Promise.all(tag.operations.map(async (op) => ({ op, params: await consoleParams(op, defaults) }))),
    })),
  );

  return (
    <main className="mx-auto max-w-[1500px] px-4 py-6 sm:px-8">
      <SiteHeader eyebrow="Developers — REST API v2" />

      <header className="mt-6 border-b-4 border-double border-foreground pb-5">
        <p className="font-mono text-[11px] font-bold uppercase tracking-[0.25em] text-stamp">
          기술 부록 · OpenAPI {reference.openapi} · v{reference.version}
        </p>
        <h1 className="mt-2 font-display text-4xl font-black tracking-tight sm:text-6xl">API 레퍼런스</h1>
        <p className="mt-3 max-w-3xl text-base leading-7 text-muted-foreground">
          이 사이트의 모든 지면은 같은 공개 API에서 인쇄됩니다. 리소스 {reference.operationCount}개, 전부 읽기 전용 —
          키도 가입도 없이 <code className="rounded-sm bg-muted px-1 font-mono text-[0.9em] text-foreground">GET</code> 한 번이면 같은
          바이트를 받습니다. 아래 콘솔에서 바로 보내 보세요.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Link href="/developers/architecture" className="nav-link font-mono text-xs font-bold">데이터의 지도 →</Link>
          <ApiResourceLink uri="/api/v2" label="진입점" />
          <a
            href="/developers/swagger"
            className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-foreground/40 bg-card px-2.5 font-mono text-[11px] font-bold hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ExternalLink className="size-4" aria-hidden="true" />
            Swagger UI
          </a>
          <a
            href="/api/v2/openapi.json"
            className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-foreground/40 bg-card px-2.5 font-mono text-[11px] font-bold hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <FileJson className="size-4" aria-hidden="true" />
            openapi.json
          </a>
          <span className="font-mono text-[11px] text-muted-foreground">
            Base URL <code className="text-foreground">{reference.server}</code>
          </span>
        </div>
        <dl className="mt-4 flex flex-wrap gap-x-5 gap-y-1 font-mono text-[10px] text-muted-foreground">
          {(["daily", "snapshot", "immutable"] as const).map((policy) => (
            <div key={policy} className="flex items-baseline gap-1.5">
              <dt className="font-bold text-foreground">캐시 · {CACHE_LABEL[policy].label}</dt>
              <dd>{CACHE_LABEL[policy].hint}</dd>
            </div>
          ))}
        </dl>
      </header>

      <details className="mt-5 rounded-sm border border-border bg-card p-3 lg:hidden">
        <summary className="cursor-pointer font-mono text-[11px] font-bold uppercase tracking-[0.2em]">리소스 트리</summary>
        <div className="mt-3">
          <ResourceTree reference={reference} />
        </div>
      </details>

      <div className="mt-6 grid gap-10 lg:grid-cols-[15rem_minmax(0,1fr)]">
        <aside className="hidden lg:block">
          <ScrollArea className="sticky top-4 h-[calc(100vh-2rem)] pr-3">
            <ResourceTree reference={reference} />
            <div className="mt-6 space-y-1 border-t border-border pt-3 font-mono text-[11px]">
              <a href="#principles" className="block hover:underline">설계 원칙</a>
              <a href="#migration" className="block hover:underline">v1 → v2 이전</a>
            </div>
          </ScrollArea>
        </aside>

        <div className="min-w-0 space-y-14">
          {cards.map((tag) => (
            <section key={tag.name} aria-labelledby={`tag-${tag.name}`} className="space-y-8">
              <div>
                <h2 id={`tag-${tag.name}`} className="font-display text-3xl font-black tracking-tight">
                  {tag.name}
                </h2>
                {tag.description && <p className="mt-1 text-sm text-muted-foreground">{tag.description}</p>}
              </div>
              {tag.operations.map(({ op, params }) => (
                <OperationCard key={op.id} op={op} origin={reference.server} params={params} />
              ))}
            </section>
          ))}

          <section id="principles" aria-labelledby="principles-title" className="scroll-mt-6">
            <h2 id="principles-title" className="font-display text-3xl font-black tracking-tight">
              설계 원칙
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              ADR 0001 요약 — REST를 문자 그대로 적용하되, 도메인이 CRUD가 아닌 곳에서는 실용적으로.
            </p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {PRINCIPLES.map((p, i) => (
                <Card key={p.title} className="rounded-sm">
                  <CardHeader className="pb-2">
                    <p className="font-mono text-[10px] font-bold text-stamp tnum">{String(i + 1).padStart(2, "0")}</p>
                    <CardTitle className="font-display text-lg font-black">{p.title}</CardTitle>
                  </CardHeader>
                  <CardContent className="text-sm leading-6 text-muted-foreground">
                    <InlineMd text={p.body} />
                  </CardContent>
                </Card>
              ))}
            </div>
          </section>

          <section id="migration" aria-labelledby="migration-title" className="scroll-mt-6">
            <h2 id="migration-title" className="font-display text-3xl font-black tracking-tight">
              v1 → v2 이전
            </h2>
            <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
              <code className="font-mono">/api/v1</code>은 동결·폐기 예정입니다 — 같은 URI와 페이로드를 유지하며{" "}
              <code className="font-mono">Deprecation</code>과 <code className="font-mono">Link: rel=&quot;successor-version&quot;</code>{" "}
              헤더를 붙여 답합니다. 최상위 필드는 <code className="font-mono">{"{links, meta, data}"}</code>로 옮겨졌습니다.
            </p>
            <Table className="mt-3 font-mono text-[11px]">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="text-[10px]">v1 (폐기 예정)</TableHead>
                  <TableHead className="text-[10px]">v2</TableHead>
                  <TableHead className="hidden text-[10px] md:table-cell">비고</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {MIGRATION.map((row) => (
                  <TableRow key={row.v1}>
                    <TableCell className="whitespace-normal break-all text-muted-foreground line-through decoration-muted-foreground/40">
                      {row.v1}
                    </TableCell>
                    <TableCell className="whitespace-normal break-all">
                      <UriTemplate path={row.v2} />
                      {row.note && <span className="mt-0.5 block text-[10px] text-muted-foreground md:hidden">{row.note}</span>}
                    </TableCell>
                    <TableCell className="hidden whitespace-normal text-muted-foreground md:table-cell">{row.note ?? ""}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <p className="mt-3 font-mono text-[10px] text-muted-foreground">
              v2 신규: /instruments, /instruments/{"{id}"}, /clubs, /clubs/{"{id}"}, /clubs/{"{id}"}/reports, /signals (스냅샷 목록) ·{" "}
              <Link href="/today/archive" className="underline">
                스냅샷으로 다시 조판한 지난 호
              </Link>
            </p>
          </section>
        </div>
      </div>
    </main>
  );
}
