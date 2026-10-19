"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Download, ExternalLink, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, FieldGroup, FieldLabel, FieldDescription } from "@/components/ui/field";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { Empty, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { Table, TableHeader, TableHead, TableBody, TableRow, TableCell } from "@/components/ui/table";
import { ReviewDetailSchema, ReviewDocumentSchema, reviewFields, reviewLabels, numericFields, type ReviewRow, type ReviewSummary } from "@/lib/research-review";

export function ReviewQueue() {
  const [rows, setRows] = useState<ReviewSummary[] | null>(null);
  const [error, setError] = useState(""); const [reload, setReload] = useState(0);
  const [query, setQuery] = useState(""); const [selected, setSelected] = useState<ReviewRow | null>(null);
  const [opening, setOpening] = useState<ReviewSummary | null>(null);
  const [detailError, setDetailError] = useState(""); const request = useRef<AbortController | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [reviewer, setReviewer] = useState(""); const [reason, setReason] = useState(""); const [evidence, setEvidence] = useState("");
  async function openReview(row: ReviewSummary) {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    request.current?.abort(); const abort = new AbortController(); request.current = abort;
    setOpening(row); setSelected(null); setDetailError(""); setEvidence("");
    try {
      const response = await fetch(row.detail_url, { signal: abort.signal });
      if (!response.ok) throw new Error(`원문 요청 실패 (${response.status})`);
      const detail = ReviewDetailSchema.parse(await response.json());
      if (detail.id !== row.id || detail.transcript_sha256 !== row.transcript_sha256) throw new Error("검토 목록과 원문 버전이 다릅니다. 새로고침하세요.");
      if (!abort.signal.aborted) { setSelected(detail); setDraft(Object.fromEntries(reviewFields.map(f => [f, String(detail.values[f] ?? "")]))); }
    } catch (e) { if (!abort.signal.aborted) setDetailError(e instanceof Error ? e.message : "원문 요청 실패"); }
  }
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    const abort = new AbortController();
    fetch("/research/review.json", { signal: abort.signal }).then(async r => {
      if (!r.ok) throw new Error(`검토 자료 요청 실패 (${r.status})`);
      return ReviewDocumentSchema.parse(await r.json());
    }).then(d => setRows(d.records)).catch(e => { if (!abort.signal.aborted) setError(e instanceof Error ? e.message : "자료 오류"); });
    return () => abort.abort();
  }, [reload]);
  const visible = useMemo(() => rows?.filter(r => `${r.school} ${r.values.company} ${r.values.ticker} ${r.reasons.join(" ")} ${r.id}`.toLowerCase().includes(query.toLowerCase())).slice(0, 100) ?? [], [rows, query]);
  const changes = Object.fromEntries(reviewFields.flatMap(f => {
    const text = draft[f]?.trim() ?? "";
    const value = text === "" ? null : numericFields.has(f) ? Number(text) : text;
    return selected && value !== selected.values[f] ? [[f, value]] : [];
  }));
  const invalidField = (f: string) => {
    const value = changes[f];
    return (numericFields.has(f) && value != null && (typeof value !== "number" || !Number.isFinite(value) || (f !== "stated_upside_pct" && value <= 0))) ||
      (f === "report_date" && typeof value === "string" && (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(Date.parse(value)) || new Date(value).toISOString().slice(0, 10) !== value));
  };
  const invalid = reviewFields.some(invalidField);
  function download() {
    if (!selected) return;
    const doc = { schema_version: "1.0", reviewer: reviewer.trim(), reason: reason.trim(), changes: [{
      id: selected.id, transcript_sha256: selected.transcript_sha256, before: selected.values, before_correction: selected.correction,
      after: changes, evidence: evidence.trim(),
    }] };
    const url = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" }));
    const a = document.createElement("a"); a.href = url; a.download = "review-corrections.json"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <div className="flex flex-col gap-6">
    <div><h1 className="text-3xl font-bold">추출값 검토</h1><p className="mt-2 text-sm text-muted-foreground">원문 근거를 확인하고 교정을 제안합니다. 내보낸 파일은 검증 후 파이프라인에서 반영합니다.</p></div>
    <div className="flex flex-wrap gap-3"><Input aria-label="검토 목록 검색" placeholder="회사·종목·이슈 검색" value={query} onChange={e => setQuery(e.target.value)} className="min-w-0 flex-1" /><Button variant="outline" onClick={() => { setRows(null); setError(""); setReload(v => v + 1); }}><RefreshCw />새로고침</Button></div>
    {error && <Alert variant="destructive"><AlertTitle>검토 자료를 읽지 못했습니다</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {!rows && !error && <Skeleton className="h-60 w-full" />}
    {rows && <p role="status" className="text-sm tabular-nums text-muted-foreground">검토 대상 {rows.length}건 · 표시 {visible.length}건 (최대 100건, 검색으로 범위 축소)</p>}
    {rows && !visible.length && <Empty><EmptyHeader><EmptyTitle>검토 결과가 없습니다</EmptyTitle></EmptyHeader></Empty>}
    {!!visible.length && <Table><TableHeader><TableRow><TableHead>회사 / 학회</TableHead><TableHead>발간일</TableHead><TableHead>검토 사유</TableHead><TableHead>원문</TableHead></TableRow></TableHeader><TableBody>{visible.map(r => <TableRow key={r.id}>
      <TableCell><Button variant="ghost" className="h-auto whitespace-normal text-left" onClick={() => { void openReview(r); }}>{r.values.company ?? r.values.ticker ?? r.id.split("/").pop()}</Button><p className="text-xs text-muted-foreground">{r.school}</p></TableCell>
      <TableCell className="tabular-nums">{r.values.report_date ?? "미상"}</TableCell><TableCell className="max-w-72 whitespace-normal break-words text-xs">{r.reasons.join(" · ")}</TableCell>
      <TableCell>{r.pdf_url ? <a href={r.pdf_url} target="_blank" rel="noreferrer" aria-label="원문 열기"><ExternalLink className="size-4" /></a> : "미연결"}</TableCell>
    </TableRow>)}</TableBody></Table>}
    <Sheet open={!!opening} onOpenChange={open => { if (!open) { request.current?.abort(); setOpening(null); setSelected(null); } }}><SheetContent className="w-full overflow-y-auto sm:max-w-4xl" onCloseAutoFocus={e => { e.preventDefault(); opener.current?.focus(); }}>
      <SheetHeader><SheetTitle>{opening?.values.company ?? "추출값 검토"}</SheetTitle><SheetDescription>빈 값은 null 교정입니다. 원문 페이지와 단위를 확인하세요. 제안 파일 다운로드만으로 데이터가 바뀌지 않습니다.</SheetDescription></SheetHeader>
      {!selected && !detailError && <Skeleton className="mx-4 h-60" />}
      {detailError && <Alert variant="destructive"><AlertTitle>원문을 읽지 못했습니다</AlertTitle><AlertDescription>{detailError}</AlertDescription></Alert>}
      {selected && <div className="grid gap-6 px-4 pb-8 md:grid-cols-2"><section><h2 className="mb-3 font-bold">전사 원문 · 표지 구간</h2><div className="mb-3 flex gap-3 text-sm">{selected.pdf_url && <a className="underline" href={selected.pdf_url} target="_blank" rel="noreferrer">PDF / 원문 페이지</a>}{selected.markdown_url && <a className="underline" href={selected.markdown_url} target="_blank" rel="noreferrer">전체 전사</a>}</div><pre className="max-h-[65svh] overflow-auto whitespace-pre-wrap break-all rounded-md border p-3 text-xs">{selected.excerpt}</pre></section>
      <FieldGroup>{reviewFields.map(f => <Field key={f} data-invalid={invalidField(f) || undefined}><FieldLabel htmlFor={`review-${f}`}>{reviewLabels[f]}</FieldLabel><Input id={`review-${f}`} aria-invalid={invalidField(f)} value={draft[f] ?? ""} onChange={e => setDraft(v => ({ ...v, [f]: e.target.value }))} /><FieldDescription>기존: {String(selected.values[f] ?? "없음")}</FieldDescription><div className="flex flex-col gap-1">{selected.matches.filter(m => m.field === f).map(m => <Button key={`${f}-${m.line}`} variant="ghost" size="sm" className="h-auto justify-start whitespace-normal break-all text-left text-xs" onClick={() => setEvidence(`전사 ${m.line}행: ${m.quote}`)} aria-label={`${reviewLabels[f]} 근거 ${m.line}행 사용`}>일치 후보 {m.line}행: {m.quote}</Button>)}</div></Field>)}
        <Field><FieldLabel htmlFor="reviewer">검토자</FieldLabel><Input id="reviewer" value={reviewer} onChange={e => setReviewer(e.target.value)} /></Field>
        <Field><FieldLabel htmlFor="reason">교정 사유</FieldLabel><Input id="reason" value={reason} onChange={e => setReason(e.target.value)} /></Field>
        <Field><FieldLabel htmlFor="evidence">원문 근거 (페이지·인용)</FieldLabel><Input id="evidence" value={evidence} onChange={e => setEvidence(e.target.value)} /></Field>
        {invalid && <p role="alert" className="text-destructive">숫자·날짜 형식을 확인하세요. 가격은 양수여야 합니다.</p>}
        <Button disabled={invalid || !Object.keys(changes).length || !reviewer.trim() || !reason.trim() || !evidence.trim()} onClick={download}><Download />교정 제안 내보내기</Button>
      </FieldGroup></div>}
    </SheetContent></Sheet>
  </div>;
}
