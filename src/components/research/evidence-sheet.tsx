"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AlertTriangle, ExternalLink, FileText, LineChart } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { dateLabel, SCHOOL_LABELS } from "@/lib/report-meta";
import { keyChunks } from "@/lib/research-compare";
import { highlight, loadEvidencePack, loadFulltextIndex, sourceUrl, verdictHref, type EvidencePack, type FulltextManifest } from "@/lib/report-search";
import { ratingClassLabels, signColor } from "@/lib/verdict";
import { cn, formatPct, formatPrice } from "@/lib/utils";
import type { StockReport } from "@/components/research/stock-research";

type Loaded = { id: string; manifest?: FulltextManifest; pack?: EvidencePack; missing?: true; error?: true };

/** Manifest + this report's evidence pack, fetched only while the sheet is open. */
function useEvidence(id: string | null) {
  const [state, setState] = useState<Loaded | null>(null);
  useEffect(() => {
    if (!id) return;
    let live = true;
    loadFulltextIndex()
      .then(async ({ manifest }) => {
        if (!manifest.reports.some((r) => r.id === id)) return { id, manifest, missing: true as const };
        return { id, manifest, pack: await loadEvidencePack(id) };
      })
      .then((next) => live && setState(next), () => live && setState({ id, error: true }));
    return () => { live = false; };
  }, [id]);
  return state?.id === id ? state : null;
}

const RETURNS = [
  { key: "d30", label: "30일", days: 30 },
  { key: "d90", label: "90일", days: 90 },
  { key: "d365", label: "1년", days: 365 },
  { key: "latest", label: "현재까지", days: Infinity },
] as const;

/** In the as-of view a return whose horizon ends after the as-of date was not yet observable. */
function knownAt(date: string, days: number, asofDate: string | null) {
  if (!asofDate) return true;
  if (!Number.isFinite(days)) return false;
  const end = new Date(`${date}T00:00:00Z`);
  end.setUTCDate(end.getUTCDate() + days);
  return end.toISOString().slice(0, 10) <= asofDate;
}

export function EvidenceSheet({ report, stockName, market, asofDate, onClose, onCloseAutoFocus }: {
  report: StockReport | null;
  /** 당시 수집 관점의 기준일 — 이후에야 알 수 있었던 수익률은 가린다 */
  asofDate: string | null;
  stockName: string;
  market: string | null;
  onClose: () => void;
  onCloseAutoFocus: (event: Event) => void;
}) {
  const state = useEvidence(report?.reportId ?? null);
  const entry = state?.manifest?.reports.find((r) => r.id === report?.reportId);
  const late = report?.firstSeen && report.firstSeen > report.date;
  const verdict = entry ? verdictHref(entry) : null;
  const needles = [stockName, "목표주가", "투자의견", "목표 주가", "투자 의견"];

  return (
    <Sheet open={report !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg" onCloseAutoFocus={onCloseAutoFocus}>
        {report && (
          <>
            <SheetHeader>
              <SheetTitle className="font-sans">{SCHOOL_LABELS[report.school]} · {stockName}</SheetTitle>
              <SheetDescription className="tnum">
                발간 {dateLabel(report.date)} · {report.firstSeen ? `수집 ${dateLabel(report.firstSeen)}` : "수집 시점 미상"}
                {report.archived ? " · 아카이브(채점 제외)" : ""}
              </SheetDescription>
            </SheetHeader>
            <div className="space-y-5 px-4">
              <dl className="grid grid-cols-3 gap-2 text-sm">
                <div><dt className="text-[11px] text-muted-foreground">의견</dt><dd>{report.rating ?? ratingClassLabels[report.ratingClass]} <span className="text-[11px] text-muted-foreground">({ratingClassLabels[report.ratingClass]})</span></dd></div>
                <div><dt className="text-[11px] text-muted-foreground">목표가</dt><dd className="font-mono tnum">{formatPrice(report.targetPrice, market)}</dd></div>
                <div><dt className="text-[11px] text-muted-foreground">발간 시 주가</dt><dd className="font-mono tnum">{formatPrice(report.startClose, market)}</dd></div>
              </dl>
              {late && (
                <p className="rounded-sm border border-warn/50 bg-warn/10 px-2.5 py-1.5 text-xs leading-5">
                  발간 {dateLabel(report.date)} 이후 <b>{dateLabel(report.firstSeen)}</b>에야 아카이브에 수집된 리포트입니다. ‘당시 수집 관점’에서 그 사이 날짜로 보면 이 리포트는 숨겨집니다.
                </p>
              )}

              <section aria-label="발간 이후 관측 수익률">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">발간 이후 주가</h3>
                  <Badge variant="outline" className="rounded-sm border-dashed text-[10px]">관측된 연관</Badge>
                </div>
                <dl className="mt-1.5 grid grid-cols-4 gap-2 text-center">
                  {RETURNS.map((r) => {
                    const known = knownAt(report.date, r.days, asofDate);
                    return (
                      <div key={r.key} className="rounded-sm border border-border px-1 py-1.5">
                        <dt className="text-[10px] text-muted-foreground">{r.label}</dt>
                        <dd className={cn("font-mono text-xs tnum", known ? signColor(report.returns[r.key]) : "text-[10px] text-muted-foreground")}>
                          {known ? formatPct(report.returns[r.key]) : "기준일 이후"}
                        </dd>
                      </div>
                    );
                  })}
                </dl>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  발간일 종가 대비 움직임입니다. 리포트가 가격을 움직였다는 뜻이 아닙니다.
                  {asofDate && ` ${dateLabel(asofDate)} 당시 관점이라 그 뒤에 끝나는 구간은 가렸습니다.`}
                </p>
              </section>

              <section aria-label="본문 근거">
                <h3 className="font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">본문 근거 — 의견·목표가·논지 문단</h3>
                <div className="mt-2 space-y-3" aria-live="polite">
                  {!state && <div role="status" aria-label="근거 불러오는 중" className="space-y-2"><Skeleton className="h-16 w-full" /><Skeleton className="h-16 w-full" /></div>}
                  {state?.error && (
                    <Alert variant="destructive">
                      <AlertTriangle className="size-4" aria-hidden="true" />
                      <AlertTitle>근거를 불러오지 못했습니다</AlertTitle>
                      <AlertDescription>네트워크를 확인하거나 아래 원문 링크로 확인해 주세요.</AlertDescription>
                    </Alert>
                  )}
                  {state?.missing && <p className="text-sm text-muted-foreground">이 리포트는 본문 색인에 없습니다(전사본 없음). 원문 링크로 확인해 주세요.</p>}
                  {state?.pack && entry && state.manifest && (() => {
                    const picks = keyChunks(state.pack);
                    if (!picks.length) return <p className="text-sm text-muted-foreground">인용할 문단을 찾지 못했습니다.</p>;
                    return picks.map((i) => {
                      const [para, start, end, , text] = state.pack!.chunks[i];
                      return (
                        <figure key={i} className="border-l-2 border-warn/60 pl-3">
                          <blockquote className="break-words text-sm leading-6">
                            {highlight(text, needles).map((part, k) => part.mark
                              ? <mark key={k} className="rounded-sm bg-warn/25 px-0.5 font-semibold text-foreground">{part.text}</mark>
                              : <span key={k}>{part.text}</span>)}
                          </blockquote>
                          <figcaption className="mt-1 flex flex-wrap gap-x-3 font-mono text-[10px] text-muted-foreground">
                            <span className="tnum">문단 {para} · L{start}–L{end}</span>
                            <a href={sourceUrl(state.manifest!, entry.path, [start, end])} target="_blank" rel="noreferrer"
                              className="inline-flex items-center gap-1 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                              원문 위치 열기 <ExternalLink className="size-3" aria-hidden="true" />
                            </a>
                          </figcaption>
                        </figure>
                      );
                    });
                  })()}
                </div>
              </section>
            </div>
            <SheetFooter className="flex-row flex-wrap gap-2">
              {entry && state?.manifest && (
                <Button asChild size="sm" variant="outline">
                  <a href={sourceUrl(state.manifest, entry.path)} target="_blank" rel="noreferrer"><FileText className="size-4" aria-hidden="true" />원문 전체</a>
                </Button>
              )}
              {!entry && report.sourceMdUrl && (
                <Button asChild size="sm" variant="outline">
                  <a href={report.sourceMdUrl} target="_blank" rel="noreferrer"><FileText className="size-4" aria-hidden="true" />원문 전사본</a>
                </Button>
              )}
              {report.sourcePdfUrl && (
                <Button asChild size="sm" variant="outline">
                  <a href={report.sourcePdfUrl} target="_blank" rel="noreferrer"><ExternalLink className="size-4" aria-hidden="true" />PDF</a>
                </Button>
              )}
              {verdict && <Button asChild size="sm" variant="ghost"><Link href={verdict}>판결문 열기</Link></Button>}
              <Button type="button" size="sm" variant="ghost" onClick={onClose}><LineChart className="size-4" aria-hidden="true" />차트에서 이 시점 보기</Button>
            </SheetFooter>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
