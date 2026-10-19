"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Compass, CornerDownLeft, FileSearch, LineChart, Search } from "lucide-react";
import { z } from "zod";
import { cn, formatPct } from "@/lib/utils";

type Entry = {
  id: string;
  kind: "page" | "stock" | "report";
  title: string;
  sub: string;
  /** /stocks/{slug} 또는 /#r={source_name} 딥링크 */
  href: string;
  /** 소문자 검색 건초더미 */
  hay: string;
  ret: number | null;
  /** 라우트 핸들러(정적 HTML)처럼 클라이언트 라우터가 그릴 수 없는 곳 — 전체 페이지 이동 */
  hard?: boolean;
};

/** 기능 바로가기 — 검색어가 맞을 때만 종목보다 위에 뜬다 */
const PAGE_ENTRIES: Entry[] = [
  { id: "p:architecture", kind: "page", title: "데이터 아키텍처", sub: "Bronze · Silver · Gold / FK · ETL 계보", href: "/developers/architecture", hay: "데이터 지도 medallion architecture 스키마 schema lineage 계보 bronze silver gold database", ret: null },
  { id: "p:review", kind: "page", title: "추출값 검토", sub: "원문 근거 · 교정 제안", href: "/developers/review", hay: "review 검토 추출 오류 교정 원문", ret: null },
  { id: "p:portfolio", kind: "page", title: "모의 포트폴리오", sub: "전진 평가 · 비중 · 텔레그램", href: "/portfolio", hay: "portfolio 포트폴리오 비중 수익률 텔레그램 모의 live", ret: null },
  { id: "p:developers", kind: "page", title: "API 레퍼런스", sub: "REST /api/v2 · 리소스 · Try it", href: "/developers", hay: "api 레퍼런스 개발자 developers rest v2 openapi 문서", ret: null },
  { id: "p:swagger", kind: "page", title: "Swagger UI", sub: "/api/v2/openapi.json", href: "/developers/swagger", hay: "swagger openapi api 스웨거", ret: null, hard: true },
  { id: "p:archive", kind: "page", title: "판결일보 축쇄판", sub: "지난 호 · 일별 신호 스냅샷", href: "/today/archive", hay: "축쇄판 지난호 지난 호 스냅샷 snapshot archive today 신문 판결일보", ret: null },
  { id: "p:strategy", kind: "page", title: "전략 랩", sub: "전략 비교 · 공유 링크 ?s=전략", href: "/strategy", hay: "전략 strategy 랩 lab 백테스트 backtest", ret: null },
];

let indexPromise: Promise<Entry[]> | null = null;

const SearchDocumentSchema = z.object({ schema_version: z.literal("1.0"), as_of: z.iso.date(), entries: z.array(z.object({
  id: z.string(), kind: z.enum(["stock", "report"]), title: z.string(), sub: z.string(),
  href: z.string().refine(v => v.startsWith("/stocks/") || v.startsWith("/#r=")), hay: z.string(), ret: z.number().nullable(),
})) });

/** Fetch a build-time projection only on first use; retry failures on the next open. */
function loadIndex(): Promise<Entry[]> {
  indexPromise ??= fetch("/research/search.json").then(async response => {
    if (!response.ok) throw new Error(`검색 자료 요청 실패 (${response.status})`);
    return [...PAGE_ENTRIES, ...SearchDocumentSchema.parse(await response.json()).entries];
  }).catch(error => { indexPromise = null; throw error; });
  return indexPromise;
}

/**
 * Ctrl/⌘+K 커맨드 팔레트 — 어느 페이지에서든 종목·판결 기록으로 점프한다.
 * 종목은 /stocks/{slug}로, 리포트는 /#r= 해시 딥링크로 (대시보드가 해시를 듣고 판결문을 펼친다).
 */
export function CommandPalette() {
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [indexError, setIndexError] = useState("");
  const [query, setQuery] = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);

  // 열기 — 단축키 또는 내비게이션 검색 버튼(커스텀 이벤트). 검색어는 열 때마다 백지에서 시작한다
  useEffect(() => {
    const openFresh = () => {
      setQuery("");
      setActiveIdx(0);
      setOpen(true);
    };
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setQuery("");
        setActiveIdx(0);
        setOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("verdict:open-palette", openFresh);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("verdict:open-palette", openFresh);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    loadIndex().then(data => { setEntries(data); setIndexError(""); }).catch(() => setIndexError("검색 자료를 불러오지 못했습니다. 닫고 다시 열어 주세요."));
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.clearTimeout(timer);
      document.body.style.overflow = prevOverflow;
    };
  }, [open]);

  const results = useMemo(() => {
    if (!entries) return [];
    const q = query.trim().toLowerCase();
    if (!q) {
      // 빈 검색 — 가장 많이 다뤄진 종목과 최신 판결 기록을 추천한다
      return [...entries.filter((e) => e.kind === "stock").slice(0, 6), ...entries.filter((e) => e.kind === "report").slice(0, 6)];
    }
    const tokens = q.split(/\s+/);
    return entries
      .filter((entry) => tokens.every((token) => entry.hay.includes(token)))
      .map((entry) => ({ entry, score: (entry.hay.startsWith(tokens[0]) ? 2 : 0) + (entry.kind === "page" ? 3 : entry.kind === "stock" ? 1 : 0) }))
      .sort((a, b) => b.score - a.score)
      .slice(0, 14)
      .map((scored) => scored.entry);
  }, [entries, query]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-idx="${activeIdx}"]`)?.scrollIntoView({ block: "nearest" });
  }, [activeIdx, results]);

  const go = useCallback(
    (entry: Entry) => {
      setOpen(false);
      if (entry.hard) {
        window.location.assign(entry.href);
      } else if (entry.href.startsWith("/#")) {
        const hash = entry.href.slice(1); // "#r=..."
        if (pathname === "/") {
          if (window.location.hash === hash) window.dispatchEvent(new HashChangeEvent("hashchange"));
          else window.location.hash = hash;
        } else {
          router.push(entry.href);
        }
      } else {
        router.push(entry.href);
      }
    },
    [pathname, router],
  );

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIdx((idx) => Math.min(results.length - 1, idx + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIdx((idx) => Math.max(0, idx - 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const entry = results[activeIdx];
      if (entry) go(entry);
    } else if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[80]">
      <div className="absolute inset-0 bg-foreground/30 backdrop-blur-[2px]" onClick={() => setOpen(false)} aria-hidden="true" />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="아카이브 검색"
        className="absolute left-1/2 top-[12vh] w-[min(640px,calc(100vw-1.5rem))] -translate-x-1/2 overflow-hidden rounded-lg border-2 border-foreground/80 bg-card shadow-[7px_7px_0_0_hsl(var(--foreground)/0.85)]"
      >
        <div className="flex items-center gap-2.5 border-b border-dashed border-border px-4 py-3">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActiveIdx(0);
            }}
            onKeyDown={onKeyDown}
            role="combobox"
            aria-expanded="true"
            aria-controls="cmdk-list"
            aria-activedescendant={results[activeIdx] ? `cmdk-${activeIdx}` : undefined}
            className="w-full bg-transparent text-sm font-semibold outline-none placeholder:font-normal placeholder:text-muted-foreground/70"
            placeholder="종목 · 리포트 검색 — 회사명, 티커, 파일명"
          />
          <kbd className="rounded-sm border border-border px-1.5 py-0.5 font-mono text-[9px] font-bold text-muted-foreground">ESC</kbd>
        </div>

        <ul id="cmdk-list" ref={listRef} role="listbox" aria-label="검색 결과" className="max-h-[52vh] overflow-y-auto py-1.5">
          {entries === null && <li role={indexError ? "alert" : "status"} className="px-4 py-8 text-center font-mono text-[11px] text-muted-foreground">{indexError || "색인 불러오는 중…"}</li>}
          {entries !== null && results.length === 0 && (
            <li className="px-4 py-8 text-center text-sm text-muted-foreground">‘{query}’ — 일치하는 기록이 없습니다.</li>
          )}
          {results.map((entry, idx) => (
            <Fragment key={entry.id}>
              {(idx === 0 || results[idx - 1].kind !== entry.kind) && (
                <li aria-hidden="true" className="px-4 pb-1 pt-2.5 font-mono text-[9px] font-bold uppercase tracking-[0.25em] text-muted-foreground">
                  {entry.kind === "page" ? "바로가기" : entry.kind === "stock" ? "종목 페이지" : "판결 기록"}
                </li>
              )}
              <li id={`cmdk-${idx}`} data-idx={idx} role="option" aria-selected={idx === activeIdx}>
                <button
                  type="button"
                  onClick={() => go(entry)}
                  onMouseMove={() => setActiveIdx(idx)}
                  tabIndex={-1}
                  className={cn(
                    "flex w-full items-center gap-3 px-4 py-2 text-left transition-colors",
                    idx === activeIdx ? "bg-foreground text-background" : "hover:bg-secondary",
                  )}
                >
                  {entry.kind === "page" ? (
                    <Compass className={cn("h-3.5 w-3.5 shrink-0", idx === activeIdx ? "opacity-70" : "text-muted-foreground")} aria-hidden="true" />
                  ) : entry.kind === "stock" ? (
                    <LineChart className={cn("h-3.5 w-3.5 shrink-0", idx === activeIdx ? "opacity-70" : "text-muted-foreground")} aria-hidden="true" />
                  ) : (
                    <FileSearch className={cn("h-3.5 w-3.5 shrink-0", idx === activeIdx ? "opacity-70" : "text-muted-foreground")} aria-hidden="true" />
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-bold">{entry.title}</span>
                    <span className={cn("block truncate font-mono text-[10px]", idx === activeIdx ? "opacity-70" : "text-muted-foreground")}>
                      {entry.sub}
                    </span>
                  </span>
                  {entry.ret !== null && (
                    <span
                      className={cn(
                        "tnum shrink-0 font-mono text-xs font-bold",
                        idx === activeIdx ? "text-background" : entry.ret >= 0 ? "text-up" : "text-down",
                      )}
                    >
                      {formatPct(entry.ret)}
                    </span>
                  )}
                  {idx === activeIdx && <CornerDownLeft className="h-3.5 w-3.5 shrink-0 opacity-70" aria-hidden="true" />}
                </button>
              </li>
            </Fragment>
          ))}
        </ul>

        <p className="border-t border-dashed border-border px-4 py-2 font-mono text-[9px] text-muted-foreground">
          ↑↓ 이동 · ↵ 열기 — 리포트는 판결문으로, 종목은 종목 페이지로 · 어디서든 Ctrl(⌘)+K
        </p>
      </div>
    </div>
  );
}
