import Link from "next/link";
import { ChevronLeft, ChevronRight, Library } from "lucide-react";
import { ApiResourceLink } from "@/components/api-resource-link";
import { SiteHeader } from "@/components/site-header";
import type { PaperIssue, SnapshotDiff } from "@/components/today/paper-data";
import { SnapshotPicker } from "@/components/today/snapshot-picker";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { dateLabel, getDisplayName, SCHOOL_LABELS, type ReportRecord } from "@/lib/report-model";
import { isBuy, schoolShort, signColor, tickerSlug } from "@/lib/verdict";
import { cn, formatPct, formatPrice } from "@/lib/utils";

/* ── 모자이크 잉크 — 증거의 서가(verdict-wall)와 같은 색 관습 ───────────── */
const MOSAIC_CLASS: Record<string, string> = {
  Tenbagger: "wall-prism",
  Multibagger: "wall-gold",
  Double: "bg-up",
  Winner: "bg-up/75",
  Positive: "bg-up/35",
  Drawdown: "bg-down/35",
  Wrecked: "bg-down",
  "No quote": "bg-muted-foreground/25",
};
/* 지난 호의 모자이크는 그날 알 수 있던 것만 — 결과(수익률)가 아니라 발간 당시 의견으로 칠한다 */
const OPINION_CLASS: Record<ReportRecord["rating_class"], string> = {
  buy: "bg-stamp/80",
  soft_buy: "bg-stamp/35",
  sell: "bg-down/70",
};

const WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];
const slugOf = (market: string, ticker: string) => `${market || "KR"}-${ticker}`.toLowerCase();
const shortDate = (date: string) => date.slice(5).replace("-", ".");

function BoxTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="border-y-2 border-foreground py-1 text-center font-display text-sm font-black tracking-[0.3em] [text-indent:0.3em]">
      {children}
    </h2>
  );
}

function IssueNav({ issue }: { issue: PaperIssue }) {
  const { prev, next, mode } = issue;
  const linkClass =
    "inline-flex min-h-8 items-center gap-1 rounded-sm px-1.5 font-mono text-[11px] font-bold transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  return (
    <nav aria-label="호수 이동" className="flex flex-wrap items-center justify-between gap-2 print:hidden">
      <div className="flex items-center gap-1">
        {prev ? (
          <Link href={`/today/${prev.date}`} className={linkClass} rel="prev">
            <ChevronLeft className="size-4" aria-hidden="true" />
            전호 <span className="tnum text-muted-foreground">{shortDate(prev.date)}</span>
          </Link>
        ) : (
          <span className="px-1.5 font-mono text-[11px] text-muted-foreground">창간호</span>
        )}
        {next ? (
          <Link href={`/today/${next.date}`} className={linkClass} rel="next">
            다음 호 <span className="tnum text-muted-foreground">{shortDate(next.date)}</span>
            <ChevronRight className="size-4" aria-hidden="true" />
          </Link>
        ) : (
          mode === "archive" && (
            <Link href="/today" className={linkClass}>
              최신호
              <ChevronRight className="size-4" aria-hidden="true" />
            </Link>
          )
        )}
      </div>
      <div className="flex items-center gap-2">
        <Link href="/today/archive" className={linkClass}>
          <Library className="size-4" aria-hidden="true" />
          축쇄판
          {/* A back issue does not know how many issues followed it. */}
          {mode === "latest" && <span className="tnum text-muted-foreground">{issue.index.length}호</span>}
        </Link>
        <SnapshotPicker
          current={issue.snapshot.as_of}
          entries={issue.index.map((entry) => ({
            date: entry.date,
            // Later issues' contents are not shown from a back issue — only their dates, for navigation.
            counts:
              mode === "latest" || entry.date <= issue.snapshot.as_of
                ? { open: entry.counts.open_positions, buys: entry.counts.buy_signals, sells: entry.counts.sell_signals }
                : null,
          }))}
        />
      </div>
    </nav>
  );
}

function DiffColumn({
  title,
  count,
  tone,
  children,
}: {
  title: string;
  count: number;
  tone: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-card px-3 py-2">
      <p className="flex items-baseline justify-between font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
        {title}
        <span className={cn("text-sm tnum", count ? tone : "text-muted-foreground")}>{count ? `${count}` : "—"}</span>
      </p>
      <div className="mt-1 min-h-4 font-mono text-[11px] leading-5">{children}</div>
    </div>
  );
}

/** 전일 대비 — 직전 박제본과 비교한 편입·청산·새 신호 */
function DiffStrip({ diff }: { diff: SnapshotDiff }) {
  const nameList = (items: { ticker: string; market: string; name: string }[], extra?: (i: number) => React.ReactNode) =>
    items.length ? (
      <ul className="space-y-0.5">
        {items.map((item, i) => (
          <li key={`${item.market}:${item.ticker}`} className="truncate">
            <Link href={`/stocks/${slugOf(item.market, item.ticker)}`} className="font-bold hover:underline">
              {item.name}
            </Link>
            {extra?.(i)}
          </li>
        ))}
      </ul>
    ) : (
      <span className="text-muted-foreground">변동 없음</span>
    );
  const quiet = !diff.entered.length && !diff.exited.length && !diff.newBuys.length && !diff.newSells.length;
  return (
    <section aria-label="전일 대비 변동" className="mt-4">
      <p className="font-mono text-[10px] font-bold uppercase tracking-[0.25em] text-muted-foreground">
        전일 대비 <span className="tnum">— {dateLabel(diff.against)} 호와 비교</span>
        {quiet && <span className="ml-2 normal-case tracking-normal">· 장부에 변동이 없었다</span>}
      </p>
      {diff.headlineChanged && (
        <p className="mt-1 font-mono text-[10px] text-warn">
          헤드라인 전략 교체: {diff.headlineChanged.from} → {diff.headlineChanged.to} — 아래 편입·청산은 서로 다른 장부의 비교다.
        </p>
      )}
      <div className="mt-1.5 grid grid-cols-2 gap-px border border-border bg-border lg:grid-cols-4">
        <DiffColumn title="신규 편입" count={diff.entered.length} tone="text-stamp">
          {nameList(diff.entered)}
        </DiffColumn>
        <DiffColumn title="청산" count={diff.exited.length} tone="text-foreground">
          {nameList(diff.exited, (i) => (
            <span className={cn("ml-1 tnum", signColor(diff.exited[i].unrealized_pct))}>
              {formatPct(diff.exited[i].unrealized_pct)}
              <span className="sr-only"> (직전 호 평가손익)</span>
            </span>
          ))}
        </DiffColumn>
        <DiffColumn title="새 매수 신호" count={diff.newBuys.length} tone="text-up">
          {nameList(diff.newBuys)}
        </DiffColumn>
        <DiffColumn title="새 매도 경보" count={diff.newSells.length} tone="text-warn">
          {nameList(diff.newSells, (i) => (
            <span className="ml-1 text-muted-foreground">{diff.newSells[i].reason === "stop_hit" ? "스탑 이탈" : "스탑 근접"}</span>
          ))}
        </DiffColumn>
      </div>
    </section>
  );
}

/** 판결일보 한 호 — 최신호(/today)와 지난 호(/today/[date])가 같은 조판을 쓴다. */
export function DailyPaper({ issue }: { issue: PaperIssue }) {
  const { snapshot, mode, obituaries } = issue;
  const asOf = snapshot.as_of;
  const archived = mode === "archive";
  const headlineKey = snapshot.headline_strategy;
  const opens = snapshot.open_positions;
  const buys = snapshot.buy_signals;
  const sells = snapshot.sell_signals;
  const regime = snapshot.regime;

  // 1면 머리기사 — 보유 종목 중 가장 크게 움직인 자리
  const lead = opens.length
    ? [...opens].sort((a, b) => Math.abs(b.unrealized_pct ?? 0) - Math.abs(a.unrealized_pct ?? 0))[0]
    : null;
  const leadSchools = lead ? [...new Set(lead.trigger_reports.map((t) => t.school))] : [];
  const leadSlug = lead ? slugOf(lead.market, lead.ticker) : null;

  // 리포트 지면 — 지난 호는 그날 발간되고 이미 수집돼 있던 것만 (paper-data가 걸러 넘긴다)
  const freshDesk = issue.reports.slice(0, 6);
  const mosaic = issue.reports.slice(0, 132);
  const fromSnapshots = obituaries.basis === "snapshots";
  const weather =
    regime.state === "ON"
      ? { mark: "☀", text: "맑음 — KOSPI 200일선 위" }
      : { mark: "☂", text: "흐림 — KOSPI 200일선 아래" };

  return (
    <main className="mx-auto max-w-[1180px] px-4 py-6 sm:px-8">
      <div className="print:hidden">
        <SiteHeader eyebrow={archived ? "Daily Verdict — Back Issue" : "Daily Verdict Front Page"} />
      </div>

      <div className="mt-4">
        <IssueNav issue={issue} />
      </div>

      {/* ── 제호(마스트헤드) ─────────────────────────────────────────────── */}
      <header className="relative mt-3 border-b-4 border-double border-foreground pb-3 text-center">
        <div className="flex items-baseline justify-between gap-2 font-mono text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground sm:text-[11px]">
          <span>통권 {issue.issueNo.toLocaleString("ko-KR")}호</span>
          <span className="hidden sm:inline">{archived ? "박제본 — 발행 당시 그대로" : "매 거래일 자동 조판"}</span>
          <span aria-label="그날의 장세">
            {weather.mark} {weather.text}
          </span>
        </div>
        <h1 className="mt-2 font-display text-6xl font-black tracking-tight sm:text-8xl">판결일보</h1>
        <p className="mt-1 font-mono text-[10px] font-bold uppercase tracking-[0.5em] text-muted-foreground [text-indent:0.5em]">
          The Verdict Daily
        </p>
        {archived && (
          <span
            className="stamp stamp-pop right-1 top-8 text-base text-stamp sm:right-6 sm:top-10 sm:text-2xl"
            aria-label={`${dateLabel(asOf)} 발행 지난 호`}
          >
            지난 호
          </span>
        )}
        <div className="mt-3 flex items-center justify-between gap-2 border-t border-foreground/60 pt-2 font-mono text-[11px] font-semibold text-muted-foreground">
          <time dateTime={asOf}>
            {dateLabel(asOf)} ({WEEKDAY[new Date(`${asOf}T00:00:00Z`).getUTCDay()]})
          </time>
          <span className="hidden sm:inline">학생 리서치 판결 아카이브 발행</span>
          <span className="truncate">전략 {headlineKey}</span>
        </div>
      </header>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <ApiResourceLink uri={issue.resource} label="이 호의 원본" />
        {!archived && (
          <p className="font-mono text-[10px] text-muted-foreground print:hidden">
            영구 링크:{" "}
            <Link href={`/today/${asOf}`} className="font-bold underline underline-offset-2">
              /today/{asOf}
            </Link>
          </p>
        )}
      </div>

      {issue.diff && <DiffStrip diff={issue.diff} />}

      {/* ── 본면: 머리기사(좌) + 시세표·부고·신착(우) ───────────────────── */}
      <div className="mt-6 grid gap-8 lg:grid-cols-12">
        {/* 머리기사 */}
        <article className="lg:col-span-8 lg:border-r lg:border-foreground/40 lg:pr-8">
          {lead ? (
            <>
              <p className="font-mono text-[11px] font-bold uppercase tracking-[0.25em] text-stamp">1면 머리기사</p>
              <h2 className="mt-2 font-display text-3xl font-black leading-tight tracking-tight sm:text-5xl">
                {lead.name}, 보유 {lead.days_held ?? "—"}일 만에{" "}
                <span className={signColor(lead.unrealized_pct)}>{formatPct(lead.unrealized_pct)}</span>
              </h2>
              <p className="mt-3 border-l-2 border-foreground/50 pl-3 text-base font-medium leading-7 text-muted-foreground">
                {leadSchools.length > 1
                  ? `${leadSchools.map((s) => schoolShort[s as keyof typeof schoolShort] ?? s).join(" · ")} — ${leadSchools.length}개 학회가 먼저 짚었던 종목이 전략 랩 포트폴리오의 간판이 됐다.`
                  : `${leadSchools.map((s) => schoolShort[s as keyof typeof schoolShort] ?? s).join("")}의 매수 리포트 한 장이 만든 자리다.`}
              </p>

              <div className="mt-5 grid grid-cols-2 gap-px border border-border bg-border sm:grid-cols-4">
                {[
                  { label: "진입가", value: formatPrice(lead.entry_price, lead.market) },
                  { label: archived ? "당일가" : "현재가", value: formatPrice(lead.current_price, lead.market) },
                  { label: "추적 스탑", value: formatPrice(lead.stop_level, lead.market) },
                  { label: "진입일", value: dateLabel(lead.entry_date) },
                ].map((s) => (
                  <div key={s.label} className="bg-card px-3 py-2">
                    <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">{s.label}</p>
                    <p className="mt-0.5 font-mono text-sm font-bold tnum">{s.value}</p>
                  </div>
                ))}
              </div>

              <div className="mt-5 space-y-4 text-[15px] leading-8 [&>p:first-child]:first-letter:float-left [&>p:first-child]:first-letter:mr-2 [&>p:first-child]:first-letter:font-display [&>p:first-child]:first-letter:text-5xl [&>p:first-child]:first-letter:font-black [&>p:first-child]:first-letter:leading-none">
                <p>
                  {dateLabel(lead.entry_date)} {formatPrice(lead.entry_price, lead.market)}에 진입한 {lead.name}
                  {lead.market === "US" ? "" : ` (${lead.ticker})`}은 {dateLabel(asOf)} 현재{" "}
                  {formatPrice(lead.current_price, lead.market)} — 수익률 {formatPct(lead.unrealized_pct)}를 기록 중이다.
                  이 자리는 사람이 고른 것이 아니라, 학회 매수 리포트 발간을 신호로 익일 시가에 기계적으로 진입하는
                  백테스트 규칙이 만들었다.
                </p>
                <p>
                  청산 규칙도 기계적이다. 추적 스탑(샹들리에)은 {archived ? "그날" : "현재"} {formatPrice(lead.stop_level, lead.market)}에 걸려
                  있고, 종가가 그 아래로 내려오면 다음 거래일 시가에 미련 없이 판다. 목표가도, 뉴스도, 회의도 없다 —
                  규칙이 곧 판결이다.
                </p>
                {leadSchools.length > 0 && (
                  <p>
                    근거 서류는{" "}
                    {lead.trigger_reports
                      .slice(0, 4)
                      .map((t) => `${SCHOOL_LABELS[t.school as keyof typeof SCHOOL_LABELS] ?? t.school} (${dateLabel(t.report_date)})`)
                      .join(", ")}
                    의 매수 리포트{lead.trigger_reports.length > 4 ? ` 외 ${lead.trigger_reports.length - 4}건` : ""}.
                    {leadSlug && (
                      <>
                        {" "}전체 기록은{" "}
                        <Link href={`/stocks/${leadSlug}`} className="font-bold underline decoration-stamp decoration-2 underline-offset-2">
                          종목 판결 페이지
                        </Link>
                        에서 차트 위 매매 지점과 함께 볼 수 있다.
                      </>
                    )}
                  </p>
                )}
              </div>
            </>
          ) : (
            <p className="py-12 text-center text-muted-foreground">
              {archived ? "이날은" : "오늘은"} 보유 포지션이 없습니다 — 현금(파킹)이 1면입니다.
            </p>
          )}

          {/* 2단 기사 — 그날의 매수 신호 */}
          <div className="mt-8 border-t-2 border-foreground/70 pt-4">
            <p className="font-mono text-[11px] font-bold uppercase tracking-[0.25em] text-muted-foreground">
              {archived ? "그날의 매수 신호" : "오늘의 매수 신호"}
            </p>
            {buys.length ? (
              <ul className="mt-3 space-y-3">
                {buys.map((b) => (
                  <li key={`${b.market}:${b.ticker}`}>
                    <h3 className="font-display text-xl font-black tracking-tight">「{b.name}」 — 새 신호가 울렸다</h3>
                    <p className="mt-1 text-sm leading-6 text-muted-foreground">
                      {dateLabel(b.signal_date)} 신호 · 기준가 {formatPrice(b.entry_basis_price, b.market)} · {b.entry_reason}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">
                {archived ? "이날은" : "오늘은"} 새 매수 신호가 없다. 신문은 침묵도 기록한다.
              </p>
            )}
          </div>
        </article>

        {/* 우측 칼럼 — 시세표 · 부고 · 신착 */}
        <aside className="space-y-7 lg:col-span-4">
          {/* 시세표 */}
          <section aria-label="시세표">
            <BoxTitle>시세표</BoxTitle>
            <p className="mt-1.5 text-center font-mono text-[10px] text-muted-foreground">
              신규 매수 {buys.length} · 매도 임박 {sells.length} · 보유 {opens.length} / {snapshot.slots.max_positions}
            </p>
            <Table className="mt-2 font-mono text-[11px]">
              <TableHeader>
                <TableRow className="border-foreground/60 text-[10px] uppercase tracking-wider hover:bg-transparent">
                  <TableHead className="h-7 px-0 font-semibold">종목</TableHead>
                  <TableHead className="h-7 px-0 text-right font-semibold">{archived ? "당일가" : "현재가"}</TableHead>
                  <TableHead className="h-7 px-0 text-right font-semibold">손익</TableHead>
                  <TableHead className="h-7 px-0 text-right font-semibold">스탑</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...opens]
                  .sort((a, b) => (b.unrealized_pct ?? 0) - (a.unrealized_pct ?? 0))
                  .map((p) => (
                    <TableRow key={`${p.market}:${p.ticker}`} className="border-dotted">
                      <TableCell className="whitespace-normal px-0 py-1.5 pr-1">
                        <Link href={`/stocks/${slugOf(p.market, p.ticker)}`} className="font-bold hover:underline">
                          {p.name}
                        </Link>
                      </TableCell>
                      <TableCell className="px-0 py-1.5 text-right tnum">{formatPrice(p.current_price, p.market)}</TableCell>
                      <TableCell className={cn("px-0 py-1.5 text-right font-bold tnum", signColor(p.unrealized_pct))}>{formatPct(p.unrealized_pct)}</TableCell>
                      <TableCell className="px-0 py-1.5 text-right tnum text-muted-foreground">{formatPrice(p.stop_level, p.market)}</TableCell>
                    </TableRow>
                  ))}
                {!opens.length && (
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={4} className="py-3 text-center text-muted-foreground">
                      보유 없음 — 전량 파킹 중
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
            {sells.length > 0 && (
              <p className="mt-2 border border-warn/60 bg-[hsl(var(--finance-warning-bg))] px-2 py-1.5 font-mono text-[10px] font-bold text-warn">
                ⚠ 매도 임박: {sells.map((s) => `${s.name} (스탑까지 ${s.dist_to_stop_pct?.toFixed(1) ?? "—"}%)`).join(" · ")}
              </p>
            )}
          </section>

          {/* 부고 — 청산 */}
          <section aria-label="부고 — 최근 청산">
            <BoxTitle>부고</BoxTitle>
            <p className="mt-1.5 text-center font-mono text-[10px] text-muted-foreground">
              {fromSnapshots
                ? "발행일까지 45일 — 박제본에서 사라진 포지션 · 손익은 마지막 박제본의 평가손익"
                : `최근 45일 내 청산 포지션 · 실현 손익${obituaries.resource ? "" : " · 이 전략의 거래 기록 없음"}`}
            </p>
            {obituaries.items.length ? (
              <ul className="mt-2 space-y-2 border border-foreground/70 p-3">
                {obituaries.items.map((t) => (
                  <li key={t.key} className="font-mono text-[11px] leading-5">
                    <span className="font-display font-black">† {t.name}</span>{" "}
                    <span className="text-muted-foreground">
                      {dateLabel(t.exitDate)} {fromSnapshots ? "부고 확인" : "영면"} · 향년 {t.days ?? "—"}일 ·{" "}
                    </span>
                    <span className={cn("font-bold tnum", signColor(t.returnPct))}>
                      {formatPct(t.returnPct)}
                      {fromSnapshots && <span className="font-normal text-muted-foreground"> (최종 평가)</span>}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 border border-foreground/40 p-3 text-center font-mono text-[11px] text-muted-foreground">
                최근 45일 부고 없음 — 모두 무탈하다.
              </p>
            )}
          </section>

          {/* 신착 리포트 */}
          <section aria-label="신착 리포트">
            <BoxTitle>신착 리포트</BoxTitle>
            {freshDesk.length ? (
              <ul className="mt-2 divide-y divide-dotted divide-border">
                {freshDesk.map((r) => {
                  const slug = tickerSlug(r);
                  return (
                    <li key={r.source_file} className="py-2 font-mono text-[11px] leading-5">
                      <span className="text-muted-foreground tnum">{dateLabel(r.report_date)}</span>{" "}
                      <span className="font-bold text-stamp">{schoolShort[r.school]}</span>{" "}
                      {slug ? (
                        <Link href={`/stocks/${slug}`} className="font-bold hover:underline">
                          {getDisplayName(r)}
                        </Link>
                      ) : (
                        <span className="font-bold">{getDisplayName(r)}</span>
                      )}
                      {isBuy(r) && r.target_price !== null && (
                        <span className="text-muted-foreground tnum"> — 목표 {formatPrice(r.target_price, r.market)}</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="mt-2 text-center font-mono text-[11px] text-muted-foreground">발행일까지 수집된 리포트가 없습니다.</p>
            )}
          </section>
        </aside>
      </div>

      {/* ── 하단 모자이크 띠 — 최근 판결 132건 ──────────────────────────── */}
      <section aria-label="최근 판결 모자이크" className="mt-8 border-t-2 border-foreground/70 pt-3">
        <div className="flex items-baseline justify-between gap-2">
          <p className="font-mono text-[10px] font-bold uppercase tracking-[0.25em] text-muted-foreground">
            {archived
              ? `발행일까지의 최근 리포트 ${mosaic.length}건 — 당시엔 결과를 몰랐다: 진한 적색 매수 · 옅은 적색 약한 매수 · 청색 매도`
              : `최근 판결 ${mosaic.length}건 — 짙은 적색일수록 높이 올랐다`}
          </p>
          <Link href="/" className="shrink-0 font-mono text-[10px] font-bold uppercase tracking-[0.2em] hover:underline print:hidden">
            전체 서가 보기 →
          </Link>
        </div>
        <div className="mt-2 flex flex-wrap gap-[3px]">
          {mosaic.map((r) => {
            const slug = tickerSlug(r);
            const cell = (
              <span
                className={cn(
                  "block h-3.5 w-3.5 rounded-[2px]",
                  archived ? OPINION_CLASS[r.rating_class] : (MOSAIC_CLASS[r.performance_bucket] ?? MOSAIC_CLASS["No quote"]),
                )}
                title={
                  archived
                    ? `${getDisplayName(r)} · ${dateLabel(r.report_date)} · ${r.rating ?? r.rating_class}`
                    : `${getDisplayName(r)} · ${dateLabel(r.report_date)} · ${formatPct(r.return_latest_pct)}`
                }
              />
            );
            return slug ? (
              <Link key={r.source_file} href={`/stocks/${slug}`} className="wall-cell">
                {cell}
              </Link>
            ) : (
              <span key={r.source_file}>{cell}</span>
            );
          })}
        </div>
      </section>

      {/* ── 판권면 ──────────────────────────────────────────────────────── */}
      <footer className="mt-8 border-t-4 border-double border-foreground pt-3 text-center">
        <p className="font-mono text-[10px] leading-5 text-muted-foreground">
          {archived
            ? "이 지면은 그날 박제된 신호 스냅샷과 그날까지 발간된 리포트만으로 다시 조판했습니다 · "
            : "본 신문은 매 거래일 공개 데이터에서 자동 조판됩니다 · "}
          모든 신호는 백테스트 시뮬레이션이며 투자 권유가 아닙니다 · 과거 성과는 미래 수익을 보장하지 않습니다
        </p>
        <p className="mt-1 font-mono text-[10px] font-bold uppercase tracking-[0.3em] text-muted-foreground">
          The Verdict Daily — printed from data, {dateLabel(asOf)}
        </p>
      </footer>
    </main>
  );
}
