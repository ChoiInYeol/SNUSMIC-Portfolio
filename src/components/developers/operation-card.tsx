import { Fragment, type ReactNode } from "react";
import { Check } from "lucide-react";
import { OperationConsole, type ConsoleParam } from "@/components/developers/operation-console";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { CachePolicy, OperationModel } from "@/lib/openapi-model";
import { cn } from "@/lib/utils";

/** `code` and **bold** from spec prose — the only markdown the descriptions use. */
export function InlineMd({ text }: { text: string }) {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith("`") ? (
          <code key={i} className="rounded-sm bg-muted px-1 py-px font-mono text-[0.92em]">
            {part.slice(1, -1)}
          </code>
        ) : part.startsWith("**") ? (
          <strong key={i} className="text-foreground">
            {part.slice(2, -2)}
          </strong>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}

/** URI 템플릿 — 경로 파라미터는 도장색으로 */
export function UriTemplate({ path, className }: { path: string; className?: string }) {
  return (
    <code className={cn("break-all font-mono", className)}>
      {path.split(/(\{[^}]+\})/g).map((part, i) =>
        part.startsWith("{") ? (
          <span key={i} className="font-bold text-stamp">
            {part}
          </span>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </code>
  );
}

export function MethodStamp({ method, small = false }: { method: string; small?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-[3px] border-2 border-stamp font-mono font-black tracking-wider text-stamp",
        small ? "h-4 border px-1 text-[8.5px]" : "h-6 px-1.5 text-[11px]",
      )}
    >
      {method}
    </span>
  );
}

export const CACHE_LABEL: Record<CachePolicy, { label: string; hint: string; className: string }> = {
  daily: {
    label: "일간",
    hint: "거래일마다 바뀌는 리소스 — 5분간 신선, 이후 최대 1일 stale-while-revalidate",
    className: "border-border text-foreground",
  },
  snapshot: {
    label: "박제",
    hint: "날짜별 스냅샷은 발행 후 바뀌지 않는다 — 브라우저는 1시간마다 재검증, CDN은 다음 배포까지 보관",
    className: "border-gold/60 text-gold",
  },
  immutable: {
    label: "영구",
    hint: "내용 주소(sha256) — 바이트가 바뀌면 URI가 바뀐다. 1년 immutable",
    className: "border-stamp/60 text-stamp",
  },
  none: { label: "—", hint: "캐시 정책이 명세에 없다", className: "border-border text-muted-foreground" },
};

const SEMANTICS: { label: string; hint: string }[] = [
  { label: "안전", hint: "GET·HEAD는 서버 상태를 바꾸지 않는다 — 크롤러·프리페치가 불러도 무해하다" },
  { label: "멱등", hint: "몇 번을 보내도 결과가 같다 — 실패하면 그냥 다시 보내면 된다" },
  { label: "캐시 가능", hint: "ETag로 조건부 요청(If-None-Match) 시 바뀌지 않았으면 304, 본문 없음" },
];

function SemanticPill({ label, hint }: { label: string; hint: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button type="button" variant="outline" size="sm" className="h-6 gap-1 rounded-sm px-1.5 font-mono text-[10px] font-bold text-muted-foreground">
          <Check className="size-4 text-foreground" aria-hidden="true" />
          {label}
        </Button>
      </TooltipTrigger>
      <TooltipContent className="max-w-64">{hint}</TooltipContent>
    </Tooltip>
  );
}

export function OperationCard({
  op,
  origin,
  params,
}: {
  op: OperationModel;
  origin: string;
  params: ConsoleParam[];
}) {
  const cache = CACHE_LABEL[op.cache.policy];
  const example: ReactNode = op.example ? (
    <div className="rounded-md border border-border bg-[hsl(var(--surface-inset))]">
      <pre className="max-h-[28rem] overflow-auto p-3 font-mono text-[11px] leading-5" tabIndex={0} aria-label={`${op.summary} 응답 예시`}>
        <code>
          {op.example.text}
          {op.example.truncated && "\n…"}
        </code>
      </pre>
      <p className="border-t border-border px-3 py-1.5 font-mono text-[10px] text-muted-foreground">
        명세(openapi.json)의 예시{op.example.truncated ? " — 길어서 앞부분만 보여줍니다" : ""} · 실제 값은 Try it으로
      </p>
    </div>
  ) : null;

  return (
    <article id={op.id} className="scroll-mt-6 border-t-2 border-foreground/70 pt-5" aria-labelledby={`${op.id}-title`}>
      <div className="flex flex-wrap items-center gap-2">
        <MethodStamp method={op.method} />
        <UriTemplate path={op.path} className="text-[15px] sm:text-base" />
      </div>
      <h3 id={`${op.id}-title`} className="mt-2 font-display text-xl font-black tracking-tight">
        {op.summary}
        {op.deprecated && <span className="ml-2 align-middle font-mono text-[10px] font-bold text-warn">DEPRECATED</span>}
      </h3>
      {op.description && (
        <p className="mt-1.5 max-w-3xl text-sm leading-6 text-muted-foreground">
          <InlineMd text={op.description} />
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button type="button" variant="outline" size="sm" className={cn("h-6 gap-1 rounded-sm px-1.5 font-mono text-[10px] font-bold", cache.className)}>
              캐시 · {cache.label}
            </Button>
          </TooltipTrigger>
          <TooltipContent className="max-w-72">
            {cache.hint}
            {op.cache.value && <span className="mt-1 block font-mono text-[10px] opacity-80">Cache-Control: {op.cache.value}</span>}
          </TooltipContent>
        </Tooltip>
        {SEMANTICS.map((s) => (
          <SemanticPill key={s.label} {...s} />
        ))}
        <span className="font-mono text-[10px] text-muted-foreground">HEAD 동일 헤더 · OPTIONS(CORS) · 그 외 메서드 405</span>
      </div>

      <div className="mt-4 grid gap-5 xl:grid-cols-2">
        <div className="min-w-0 space-y-4">
          {op.params.length > 0 && (
            <section aria-label="파라미터">
              <h4 className="font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">파라미터</h4>
              <Table className="mt-1 font-mono text-[11px]">
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="h-8 text-[10px]">이름</TableHead>
                    <TableHead className="h-8 text-[10px]">위치</TableHead>
                    <TableHead className="h-8 text-[10px]">형식</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {op.params.map((p) => (
                    <TableRow key={p.name} className="hover:bg-transparent">
                      <TableCell className="align-top">
                        <span className="font-bold text-stamp">{p.name}</span>
                        {p.required && <span className="ml-1 text-[9px] text-muted-foreground">필수</span>}
                        <p className="mt-0.5 whitespace-normal font-sans text-[11px] text-muted-foreground">
                          <InlineMd text={p.description} />
                        </p>
                      </TableCell>
                      <TableCell className="align-top">{p.in}</TableCell>
                      <TableCell className="align-top whitespace-normal break-all">
                        {p.type}
                        {p.pattern && <span className="block text-[10px] text-muted-foreground">{p.pattern}</span>}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </section>
          )}

          <section aria-label="응답">
            <h4 className="font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">응답</h4>
            <ul className="mt-1 divide-y divide-dotted divide-border border-y border-border">
              {op.responses.map((r) => (
                <li key={r.status} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-1.5 font-mono text-[11px]">
                  <span
                    className={cn(
                      "w-9 font-bold tnum",
                      r.status.startsWith("2") ? "text-foreground" : r.status.startsWith("3") ? "text-muted-foreground" : "text-destructive",
                    )}
                  >
                    {r.status}
                  </span>
                  <span className="min-w-0 flex-1 font-sans text-[12px]">
                    <InlineMd text={r.description} />
                  </span>
                  {r.contentType && <span className="text-[10px] text-muted-foreground">{r.contentType}</span>}
                </li>
              ))}
            </ul>
          </section>

          {op.body && (
            <section aria-label="응답 스키마 요약">
              <h4 className="font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
                본문 {op.body.enveloped ? "— 봉투 {schema_version, links, meta, data}의 " : "— 봉투 없음: "}
                <span className="normal-case tracking-normal text-foreground">{op.body.label}</span>
              </h4>
              {op.body.fields.length > 0 ? (
                <dl className="mt-1 grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-3 gap-y-1 font-mono text-[11px]">
                  {op.body.fields.map((f) => (
                    <div key={f.name} className="contents">
                      <dt className={cn("truncate", f.required ? "font-bold" : "text-muted-foreground")} title={f.name}>
                        {f.name}
                        {!f.required && "?"}
                      </dt>
                      <dd className="min-w-0">
                        <span className="break-all text-muted-foreground">{f.type}</span>
                        {f.description && (
                          <span className="block font-sans text-[11px] leading-4 text-muted-foreground/90">
                            <InlineMd text={f.description} />
                          </span>
                        )}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="mt-1 font-mono text-[11px] text-muted-foreground">자유 형식 객체</p>
              )}
            </section>
          )}
        </div>

        <div className="min-w-0">
          <OperationConsole
            method={op.method}
            template={op.path}
            origin={origin}
            enveloped={op.body?.enveloped ?? false}
            params={params}
            example={example}
          />
        </div>
      </div>
    </article>
  );
}
