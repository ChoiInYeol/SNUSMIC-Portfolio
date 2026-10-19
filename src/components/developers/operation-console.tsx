"use client";

import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { AlertCircle, ChevronDown, ChevronUp, Play } from "lucide-react";
import { CopyButton } from "@/components/developers/copy-button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

export type ConsoleParam = {
  name: string;
  pattern: string | null;
  description: string;
  initial: string;
  examples: { label: string; value: string }[];
};

type Result =
  | { kind: "ok"; status: number; statusText: string; ms: number; bytes: number; headers: [string, string][]; body: string; json: boolean }
  | { kind: "error"; message: string; ms: number };

const SHOWN_HEADERS = ["content-type", "cache-control", "etag", "age", "access-control-allow-origin", "link", "deprecation", "x-vercel-cache"];
const PREVIEW_LINES = 80;

// `+` is a literal path character in strategy ids (spec: no encoding needed), so keep it readable.
const encodeSegment = (value: string) => encodeURIComponent(value).replace(/%2B/gi, "+");

function fill(template: string, values: Record<string, string>) {
  return template.replace(/\{([^}]+)\}/g, (_, name: string) => encodeSegment(values[name] ?? ""));
}

function samples(url: string, enveloped: boolean) {
  return {
    curl: `curl -sS -H 'Accept: application/json' \\\n  '${url}'\n\n# 조건부 요청 — 바뀌지 않았으면 304, 본문 없음\ncurl -sS -o /dev/null -w '%{http_code}\\n' \\\n  -H 'If-None-Match: "<직전 응답의 ETag>"' '${url}'`,
    js: `const res = await fetch("${url}", {\n  headers: { Accept: "application/json" },\n});\nif (!res.ok) {\n  // 404는 application/problem+json (RFC 9457)\n  const problem = await res.json();\n  throw new Error(\`\${res.status} \${problem.title}\`);\n}\n${
      enveloped
        ? `const { data, links, meta } = await res.json();\nconsole.log(meta.as_of, links.self, data);`
        : `const doc = await res.json(); // 봉투 없는 문서\nconsole.log(doc);`
    }`,
    python: `import requests\n\nres = requests.get(\n    "${url}",\n    headers={"Accept": "application/json"},\n    timeout=10,\n)\nres.raise_for_status()  # 404 → problem+json\ndoc = res.json()\n${
      enveloped ? `data, links, meta = doc["data"], doc["links"], doc["meta"]\nprint(meta.get("as_of"), links["self"])` : `print(doc)`
    }`,
  };
}

function CodeBlock({ code, label }: { code: string; label: string }) {
  return (
    <div className="relative rounded-md border border-border bg-[hsl(var(--surface-inset))]">
      <div className="flex items-center justify-between border-b border-border px-3 py-1">
        <span className="font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">{label}</span>
        <CopyButton text={code} label={`${label} 코드 복사`} />
      </div>
      <ScrollArea className="max-w-full">
        <pre className="p-3 font-mono text-[11.5px] leading-5">
          <code>{code}</code>
        </pre>
        <ScrollBar orientation="horizontal" />
      </ScrollArea>
    </div>
  );
}

/** 한 operation의 콘솔 — 경로 파라미터를 고치면 Try it·curl·JS·Python이 함께 바뀐다 */
export function OperationConsole({
  method,
  template,
  origin,
  enveloped,
  params,
  example,
}: {
  method: string;
  template: string;
  origin: string;
  enveloped: boolean;
  params: ConsoleParam[];
  example: ReactNode;
}) {
  const formId = useId();
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(params.map((p) => [p.name, p.initial])));
  const [result, setResult] = useState<Result | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  // Compile spec patterns once; a pattern JS cannot compile is skipped rather than blocking the request.
  const patterns = useMemo(
    () =>
      new Map(
        params.map((p) => {
          if (p.pattern === null) return [p.name, null] as const;
          try {
            return [p.name, new RegExp(p.pattern)] as const;
          } catch {
            return [p.name, null] as const;
          }
        }),
      ),
    [params],
  );
  const invalid = useMemo(
    () =>
      params
        .filter((p) => {
          const value = values[p.name] ?? "";
          return !value || patterns.get(p.name)?.test(value) === false;
        })
        .map((p) => p.name),
    [params, patterns, values],
  );
  const path = fill(template, values);
  const code = samples(`${origin}${path}`, enveloped);

  const send = async () => {
    if (invalid.length) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setExpanded(false);
    const started = performance.now();
    try {
      // no-store: show what the server answers now, not a copy from this browser's cache.
      const res = await fetch(path, { method, headers: { Accept: "application/json" }, cache: "no-store", signal: controller.signal });
      const text = await res.text();
      const ms = Math.round(performance.now() - started);
      let body = text;
      let json = false;
      try {
        body = JSON.stringify(JSON.parse(text), null, 2);
        json = true;
      } catch {
        // Not JSON (e.g. an HTML error page): show the raw text.
      }
      const headers = SHOWN_HEADERS.flatMap((name) => {
        const value = res.headers.get(name);
        return value === null ? [] : [[name, value] as [string, string]];
      });
      setResult({ kind: "ok", status: res.status, statusText: res.statusText, ms, bytes: new Blob([text]).size, headers, body, json });
    } catch (error) {
      if (controller.signal.aborted) return;
      setResult({ kind: "error", message: error instanceof Error ? error.message : String(error), ms: Math.round(performance.now() - started) });
    } finally {
      if (abortRef.current === controller) setLoading(false);
    }
  };

  const lines = result?.kind === "ok" ? result.body.split("\n") : [];
  const shown = expanded ? lines : lines.slice(0, PREVIEW_LINES);

  return (
    <Tabs defaultValue="try" className="mt-4">
      <ScrollArea className="max-w-full">
        <TabsList className="h-9 rounded-sm">
          <TabsTrigger value="try" className="font-mono text-[11px]">Try it</TabsTrigger>
          <TabsTrigger value="curl" className="font-mono text-[11px]">curl</TabsTrigger>
          <TabsTrigger value="js" className="font-mono text-[11px]">JavaScript</TabsTrigger>
          <TabsTrigger value="python" className="font-mono text-[11px]">Python</TabsTrigger>
          {example && <TabsTrigger value="example" className="font-mono text-[11px]">응답 예시</TabsTrigger>}
        </TabsList>
        <ScrollBar orientation="horizontal" />
      </ScrollArea>

      <TabsContent value="try" className="space-y-3">
        <form
          id={formId}
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            void send();
          }}
        >
          {params.map((p) => {
            const inputId = `${formId}-${p.name}`;
            const bad = invalid.includes(p.name);
            return (
              <div key={p.name} className="space-y-1">
                <label htmlFor={inputId} className="flex flex-wrap items-baseline gap-2 font-mono text-[11px] font-bold">
                  <span className="text-stamp">{`{${p.name}}`}</span>
                  <span className="font-sans text-[11px] font-normal text-muted-foreground">{p.description}</span>
                </label>
                <Input
                  id={inputId}
                  value={values[p.name] ?? ""}
                  onChange={(event) => setValues((prev) => ({ ...prev, [p.name]: event.target.value.trim() }))}
                  spellCheck={false}
                  autoComplete="off"
                  aria-invalid={bad}
                  aria-describedby={bad ? `${inputId}-err` : undefined}
                  className="rounded-sm bg-card font-mono text-[12px] md:text-[12px]"
                />
                {bad && (
                  <p id={`${inputId}-err`} className="font-mono text-[10px] text-destructive">
                    {values[p.name] ? `형식이 맞지 않습니다 — ${p.pattern}` : "값을 입력하세요"}
                  </p>
                )}
                {p.examples.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1">
                    <span className="font-mono text-[10px] text-muted-foreground">예시</span>
                    {p.examples.map((ex) => (
                      <Button
                        key={`${ex.label}-${ex.value}`}
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => setValues((prev) => ({ ...prev, [p.name]: ex.value }))}
                        className="h-6 max-w-full truncate rounded-sm px-1.5 font-mono text-[10px]"
                        title={ex.label}
                      >
                        {ex.value.length > 24 ? `${ex.value.slice(0, 12)}…${ex.value.slice(-6)}` : ex.value}
                      </Button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" size="sm" disabled={invalid.length > 0 || loading} className="gap-1.5 font-mono text-[11px]">
              <Play className="size-4" aria-hidden="true" />
              {loading ? "요청 중…" : `${method} 보내기`}
            </Button>
            <code className="min-w-0 break-all font-mono text-[11px] text-muted-foreground">{path}</code>
          </div>
        </form>

        <div aria-live="polite" aria-busy={loading}>
          {loading && (
            <div className="space-y-2" aria-label="응답 대기 중">
              <Skeleton className="h-5 w-48" />
              <Skeleton className="h-24 w-full" />
            </div>
          )}
          {!loading && result?.kind === "error" && (
            <Alert variant="destructive">
              <AlertCircle />
              <AlertTitle>요청이 실패했습니다 ({result.ms}ms)</AlertTitle>
              <AlertDescription>{result.message} — 네트워크 연결을 확인하고 다시 보내 보세요.</AlertDescription>
            </Alert>
          )}
          {!loading && result?.kind === "ok" && (
            <div className="rounded-md border border-border">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border px-3 py-2 font-mono text-[11px]">
                <span
                  className={cn(
                    "rounded-sm px-1.5 py-0.5 font-bold text-background",
                    result.status < 300 ? "bg-foreground" : result.status < 400 ? "bg-muted-foreground" : "bg-destructive",
                  )}
                >
                  {result.status} {result.statusText}
                </span>
                <span className="tnum text-muted-foreground">{result.ms.toLocaleString("ko-KR")} ms</span>
                <span className="tnum text-muted-foreground">{(result.bytes / 1024).toLocaleString("ko-KR", { maximumFractionDigits: 1 })} KB</span>
                <CopyButton text={result.body} label="응답 본문 복사" className="ml-auto" />
              </div>
              {result.headers.length > 0 && (
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 border-b border-border px-3 py-2 font-mono text-[10.5px]">
                  {result.headers.map(([name, value]) => (
                    <div key={name} className="contents">
                      <dt className="text-muted-foreground">{name}</dt>
                      <dd className="break-all">{value}</dd>
                    </div>
                  ))}
                </dl>
              )}
              <ScrollArea className="[&>[data-slot=scroll-area-viewport]]:max-h-[28rem]">
                <pre className="p-3 font-mono text-[11px] leading-5">
                  <code>{shown.join("\n")}</code>
                </pre>
                <ScrollBar orientation="horizontal" />
              </ScrollArea>
              {lines.length > PREVIEW_LINES && (
                <div className="border-t border-border px-3 py-1.5">
                  <Button type="button" variant="ghost" size="sm" className="h-7 gap-1 font-mono text-[10px]" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}>
                    {expanded ? <ChevronUp className="size-4" aria-hidden="true" /> : <ChevronDown className="size-4" aria-hidden="true" />}
                    {expanded ? "접기" : `전체 ${lines.length.toLocaleString("ko-KR")}줄 펼치기`}
                  </Button>
                </div>
              )}
            </div>
          )}
          {!loading && !result && (
            <p className="font-mono text-[10px] text-muted-foreground">
              이 사이트와 같은 출처로 실제 GET을 보냅니다 — 상태 코드·응답 시간·캐시 헤더를 그대로 보여줍니다.
            </p>
          )}
        </div>
      </TabsContent>
      <TabsContent value="curl">
        <CodeBlock code={code.curl} label="curl" />
      </TabsContent>
      <TabsContent value="js">
        <CodeBlock code={code.js} label="JavaScript (fetch)" />
      </TabsContent>
      <TabsContent value="python">
        <CodeBlock code={code.python} label="Python (requests)" />
      </TabsContent>
      {example && <TabsContent value="example">{example}</TabsContent>}
    </Tabs>
  );
}
