import Link from "next/link";
import { AlertTriangle, ArrowRight, CircleSlash, Info } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StaleNotice } from "@/components/account/stale-notice";
import type { AccountFlow, FlowAction, FlowPlan } from "@/lib/account-flow";
import { cn, formatPct } from "@/lib/utils";

// Server-rendered account reading order: ① today's valuation → ② what changed → ③ next-open actions →
// ④ expected cash and weights → ⑤ execution log. No client JS: everything is in the static HTML.

export const STEPS = [
  { id: "valuation", label: "오늘 평가" },
  { id: "changes", label: "무엇이 바뀌었나" },
  { id: "plan", label: "다음 시가 행동" },
  { id: "weights", label: "예상 현금·비중" },
  { id: "log", label: "실행 기록" },
] as const;

const won = (v: number) => `${Math.round(v).toLocaleString("ko-KR")}원`;
const signed = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(Math.round(v)).toLocaleString("ko-KR")}원`;
const tone = (v: number) => (v > 0 ? "text-up" : v < 0 ? "text-down" : "text-muted-foreground");

const ACCOUNTS = [
  { href: "/portfolio", label: "SOTA 원장", sub: "선정 전략" },
  { href: "/portfolio/paper", label: "1억 전진 모의", sub: "execution-v2" },
  { href: "/portfolio/funded", label: "5천만 납입식", sub: "funded-w50-v1" },
] as const;

export function AccountSwitch({ current, fundedStarted }: { current: (typeof ACCOUNTS)[number]["href"]; fundedStarted: boolean }) {
  return (
    <nav aria-label="모의 계정 선택" className="flex flex-wrap gap-2">
      {ACCOUNTS.map((a) => {
        const active = a.href === current;
        return (
          <Link key={a.href} href={a.href} aria-current={active ? "page" : undefined}
            className={cn("flex min-h-11 flex-col justify-center rounded-md border px-3 py-1.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              active ? "border-foreground bg-foreground text-background" : "border-border bg-card hover:border-foreground/60")}>
            <span className="font-bold">{a.label}</span>
            <span className={cn("font-mono text-[10px]", active ? "text-background/75" : "text-muted-foreground")}>
              {a.sub}{a.href === "/portfolio/funded" && !fundedStarted ? " · 미개시" : ""}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}

export function StepRail({ disabled = false }: { disabled?: boolean }) {
  return (
    <ol aria-label="계정 읽는 순서" className="flex gap-1.5 overflow-x-auto pb-1 font-mono text-[11px] [scrollbar-width:thin]">
      {STEPS.map((s, i) => (
        <li key={s.id} className="shrink-0">
          {disabled ? (
            <span className="inline-flex min-h-9 items-center gap-1.5 rounded-sm border border-dashed border-border px-2 text-muted-foreground">
              <span className="tnum">{i + 1}</span> {s.label}
            </span>
          ) : (
            <a href={`#${s.id}`} className="inline-flex min-h-9 items-center gap-1.5 rounded-sm border border-border bg-card px-2 hover:border-foreground/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <span className="tnum font-bold text-stamp">{i + 1}</span> {s.label}
            </a>
          )}
        </li>
      ))}
    </ol>
  );
}

export function StepHeading({ n, id, title, aside }: { n?: number; id: string; title: string; aside?: React.ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-foreground/70 pb-1.5">
      <h2 id={id} className="scroll-mt-6 font-display text-xl font-black tracking-tight">
        {n !== undefined && <span className="mr-2 font-mono text-sm font-bold text-stamp tnum">{n}</span>}{title}
      </h2>
      {aside && <div className="font-mono text-[10px] text-muted-foreground">{aside}</div>}
    </div>
  );
}

/** Principal vs investment P&L, drawn to scale so contributions never read as return. A loss is the hatched part of principal. */
function PrincipalBar({ v }: { v: AccountFlow["valuation"] }) {
  const loss = v.investmentPnlKrw < 0;
  const scale = Math.max(v.principalKrw, v.navKrw, 1);
  // With a loss, what remains of each principal part shrinks pro rata and the gap is the loss.
  const kept = loss ? v.navKrw / Math.max(v.principalKrw, 1) : 1;
  const pct = (x: number) => `${(x / scale) * 100}%`;
  return (
    <figure className="mt-4">
      <div className="flex h-3 w-full overflow-hidden rounded-sm bg-muted" role="img"
        aria-label={`초기 자금 ${won(v.initialKrw)}, 납입 ${won(v.contributionsKrw)}, 투자 손익 ${signed(v.investmentPnlKrw)}`}>
        <span className="h-full bg-foreground/80" style={{ width: pct(v.initialKrw * kept) }} />
        <span className="h-full bg-foreground/45" style={{ width: pct(v.contributionsKrw * kept) }} />
        {v.investmentPnlKrw !== 0 && (
          <span className={cn("h-full", loss ? "bg-[repeating-linear-gradient(135deg,hsl(var(--down)/0.75)_0_3px,transparent_3px_6px)]" : "bg-up")}
            style={{ width: pct(Math.abs(v.investmentPnlKrw)) }} />
        )}
      </div>
      <figcaption className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-[10px] text-muted-foreground">
        <span><i className="mr-1 inline-block size-2 rounded-[1px] bg-foreground/80 align-middle" />초기 자금 {won(v.initialKrw)}</span>
        <span><i className="mr-1 inline-block size-2 rounded-[1px] bg-foreground/45 align-middle" />납입(원금) {won(v.contributionsKrw)}</span>
        <span className={tone(v.investmentPnlKrw)}><i className={cn("mr-1 inline-block size-2 rounded-[1px] align-middle", loss ? "bg-down/70" : "bg-up")} />투자 손익 {signed(v.investmentPnlKrw)}</span>
      </figcaption>
    </figure>
  );
}

function Valuation({ flow }: { flow: AccountFlow }) {
  const v = flow.valuation;
  return (
    <section aria-labelledby="valuation">
      <StepHeading n={1} id="valuation" title="오늘 평가"
        aside={<>평가 기준일 <span className="tnum">{flow.asOf}</span>{flow.marks && <> · KR <span className="tnum">{flow.marks.KR ?? "—"}</span> · US <span className="tnum">{flow.marks.US ?? "—"}</span></>}</>} />
      <StaleNotice asOf={flow.asOf} />
      <div className="rounded-md border border-border bg-card p-4">
        <p className="font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">평가액</p>
        <p className="mt-1 font-display text-3xl font-black tracking-tight tnum sm:text-4xl">{won(v.navKrw)}</p>
        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-sm sm:grid-cols-4">
          <div><dt className="text-xs text-muted-foreground">누적 원금</dt><dd className="font-mono tnum">{won(v.principalKrw)}</dd></div>
          <div><dt className="text-xs text-muted-foreground">투자 손익</dt><dd className={cn("font-mono tnum", tone(v.investmentPnlKrw))}>{signed(v.investmentPnlKrw)}</dd></div>
          <div>
            <dt className="text-xs text-muted-foreground">TWR <span className="text-[10px]">(납입 제외)</span></dt>
            <dd className={cn("font-mono tnum", tone(v.twrPct))}>{formatPct(v.twrPct, 2)}</dd>
          </div>
          <div><dt className="text-xs text-muted-foreground">{v.cashLabel} 비중</dt><dd className="font-mono tnum">{v.cashWeightPct.toFixed(2)}%</dd></div>
          <div><dt className="text-xs text-muted-foreground">XIRR</dt><dd className="font-mono tnum">{v.xirrPct === null ? <span className="text-xs text-muted-foreground">30일 경과 후 계산</span> : formatPct(v.xirrPct, 2)}</dd></div>
          <div><dt className="text-xs text-muted-foreground">누적 모의 비용</dt><dd className="font-mono tnum">{won(v.feesKrw)}</dd></div>
        </dl>
        <PrincipalBar v={v} />
        <p className="mt-2 text-xs text-muted-foreground">투자 손익 = 평가액 − 누적 원금. 납입은 원금이며 수익률(TWR)에 들어가지 않습니다.</p>
        {v.scopeNote && <p className="mt-1 text-xs font-bold text-warn">{v.scopeNote}</p>}
      </div>
      {flow.segments && <Segments segments={flow.segments} />}
    </section>
  );
}

/** Backtest, live and combined side by side so a simulated result never reads as the live track record. */
function Segments({ segments }: { segments: NonNullable<AccountFlow["segments"]> }) {
  return (
    <div className="mt-3">
      <Table>
        <TableCaption className="text-left text-xs">구간별 성과 — TWR은 각 구간 안에서 납입을 제외해 계산합니다.</TableCaption>
        <TableHeader>
          <TableRow><TableHead>구간</TableHead><TableHead className="text-right">원금</TableHead><TableHead className="text-right">평가액</TableHead>
            <TableHead className="text-right">손익</TableHead><TableHead className="text-right">TWR</TableHead></TableRow>
        </TableHeader>
        <TableBody>
          {segments.map((g) => (
            <TableRow key={g.key} className={g.key === "backtest" ? "bg-muted/40" : undefined}>
              <TableCell className="whitespace-normal">
                <span className="font-bold">{g.label}</span>
                <span className="block font-mono text-[10px] text-muted-foreground tnum">{g.period}</span>
                <span className="block text-[11px] text-muted-foreground">{g.note}</span>
              </TableCell>
              <TableCell className="text-right font-mono tnum">{won(g.principalKrw)}</TableCell>
              <TableCell className="text-right font-mono tnum">{won(g.navKrw)}</TableCell>
              <TableCell className={cn("text-right font-mono tnum", tone(g.pnlKrw))}>{signed(g.pnlKrw)}</TableCell>
              <TableCell className={cn("text-right font-mono tnum", tone(g.twrPct))}>{formatPct(g.twrPct, 2)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function Changes({ flow }: { flow: AccountFlow }) {
  const c = flow.valuation.change;
  return (
    <section aria-labelledby="changes">
      <StepHeading n={2} id="changes" title="무엇이 바뀌었나" aside={c ? <>직전 평가 <span className="tnum">{c.since}</span> 대비</> : "첫 평가"} />
      {c && (
        <dl className="mb-3 grid grid-cols-3 gap-2 text-center">
          {[
            { k: "평가액", v: c.navKrw },
            { k: "원금(납입)", v: c.principalKrw },
            { k: "투자 손익", v: c.pnlKrw },
          ].map((x) => (
            <div key={x.k} className="rounded-sm border border-border bg-card px-2 py-2">
              <dt className="text-[11px] text-muted-foreground">{x.k}</dt>
              <dd className={cn("font-mono text-sm tnum", x.k === "원금(납입)" ? "text-foreground" : tone(x.v))}>{signed(x.v)}</dd>
            </div>
          ))}
        </dl>
      )}
      <ul className="space-y-1.5 text-sm">
        {flow.changes.map((line) => (
          <li key={line} className="flex gap-2"><ArrowRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" /><span>{line}</span></li>
        ))}
      </ul>
    </section>
  );
}

const SIDE: Record<FlowAction["side"], { label: string; cls: string }> = {
  buy: { label: "매수", cls: "border-up/60 bg-up/10 text-up" },
  sell: { label: "매도", cls: "border-down/60 bg-down/10 text-down" },
  hold: { label: "유지", cls: "border-border text-muted-foreground" },
};
const STATUS: Record<FlowPlan["status"], string> = {
  awaiting_open: "시가 대기 · 체결 전",
  executed: "체결 완료",
  cancelled: "취소됨",
  waiting_signal: "신호 대기",
};

function ActionRow({ a }: { a: FlowAction }) {
  return (
    <li className="grid gap-x-3 gap-y-1 border-b border-dashed border-border py-2.5 last:border-b-0 sm:grid-cols-[minmax(0,1fr)_auto]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className={cn("rounded-sm px-1.5 font-mono text-[10px]", SIDE[a.side].cls)}>{SIDE[a.side].label}</Badge>
          <span className="font-bold">{a.name}</span>
          <span className="font-mono text-[10px] text-muted-foreground">{a.id}</span>
        </div>
        <p className="mt-1 text-xs leading-5 text-muted-foreground"><span className="font-semibold text-foreground">이유 </span>{a.reason}</p>
        {a.conditions.length > 0 && a.side !== "hold" && (
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px] leading-4 text-muted-foreground">
            {a.conditions.map((c) => <li key={c}>{c}</li>)}
          </ul>
        )}
      </div>
      <dl className="grid grid-cols-3 gap-x-3 font-mono text-xs tnum sm:min-w-[300px] sm:text-right">
        <div><dt className="text-[10px] text-muted-foreground">수량</dt><dd>{a.shares ? <>{a.shares.basis === "estimate" && <abbr title="종가 기준 추정" className="mr-0.5 no-underline">~</abbr>}{a.shares.value.toLocaleString("ko-KR")}주</> : "—"}</dd></div>
        <div><dt className="text-[10px] text-muted-foreground">금액{a.amountKrw?.basis === "estimate" ? " (추정)" : ""}</dt><dd>{a.amountKrw ? won(a.amountKrw.value) : "—"}</dd></div>
        <div><dt className="text-[10px] text-muted-foreground">비중</dt><dd>{a.currentWeightPct.toFixed(1)}%{a.plannedWeightPct !== null && <> → {a.plannedWeightPct.toFixed(1)}%</>}</dd></div>
      </dl>
    </li>
  );
}

function PlanCard({ plan }: { plan: FlowPlan }) {
  const groups = (["sell", "buy", "hold"] as const).map((side) => ({ side, items: plan.actions.filter((a) => a.side === side) }));
  const trades = plan.actions.filter((a) => a.side !== "hold").length;
  return (
    <article className="rounded-md border border-border bg-card p-4" aria-label={plan.title}>
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="font-bold">{plan.title}</h3>
          <p className="font-mono text-[10px] text-muted-foreground tnum">
            {plan.basis}{plan.frozenAt && <> · 동결 {new Date(plan.frozenAt).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", dateStyle: "short", timeStyle: "short" })} KST</>}
          </p>
        </div>
        <Badge variant="outline" className="rounded-sm font-mono text-[10px]">{STATUS[plan.status]}</Badge>
      </header>
      {trades > 0 && plan.status === "awaiting_open" && (
        <p className="mt-3 flex gap-2 rounded-sm bg-muted px-2.5 py-2 text-xs leading-5">
          <Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span><b>계획 수치는 종가로 계산한 추정치입니다.</b> 실제 수량·체결가는 다음 실제 시가 이후에만 실행 기록에 나타납니다.</span>
        </p>
      )}
      {plan.contributionsKrw > 0 && (
        <p className="mt-2 font-mono text-xs tnum">시가 전 납입 예정 {won(plan.contributionsKrw)} — 원금 증가(수익 아님)</p>
      )}
      {plan.noTradeReason && (
        <p className="mt-3 flex gap-2 rounded-sm border border-dashed border-border px-2.5 py-2 text-sm">
          <CircleSlash className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <span><b>매매 없음 — </b>{plan.noTradeReason}</span>
        </p>
      )}
      {groups.filter((g) => g.items.length).map((g) => (
        <div key={g.side} className="mt-3">
          <h4 className="font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">{SIDE[g.side].label} <span className="tnum">{g.items.length}</span></h4>
          <ul>{g.items.map((a) => <ActionRow key={`${a.side}-${a.id}`} a={a} />)}</ul>
        </div>
      ))}
      {plan.excluded.length > 0 && (
        <div className="mt-3">
          <h4 className="font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">제외된 신호</h4>
          <ul className="mt-1 space-y-0.5 text-xs">{plan.excluded.map((e) => <li key={`${e.name}-${e.reason}`}><b>{e.name}</b> — {e.reason}</li>)}</ul>
        </div>
      )}
      {plan.cashAfterKrw && (
        <p className="mt-3 font-mono text-xs tnum">계획 후 현금{plan.cashAfterKrw.basis === "estimate" ? " (추정)" : ""} {won(plan.cashAfterKrw.value)}</p>
      )}
      <ul className="mt-3 space-y-0.5 text-[11px] leading-4 text-muted-foreground">{plan.notes.map((n) => <li key={n}>· {n}</li>)}</ul>
    </article>
  );
}

function Weights({ flow }: { flow: AccountFlow }) {
  const rows = [...flow.weights].sort((a, b) => (b.plannedPct ?? b.currentPct) - (a.plannedPct ?? a.currentPct));
  return (
    <section aria-labelledby="weights">
      <StepHeading n={4} id="weights" title="예상 현금·비중" aside="현재 → 계획 후(추정)" />
      <ul className="space-y-2">
        {rows.map((w) => (
          <li key={w.id} className="grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)_auto] items-center gap-2 text-xs sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)_auto]">
            <span className="truncate" title={w.name}>{w.name}</span>
            <span className="relative block h-4" aria-hidden="true">
              <span className="absolute inset-y-0 left-0 rounded-[1px] bg-foreground/15" style={{ width: `${Math.min(100, w.currentPct)}%` }} />
              {w.plannedPct !== null && <span className="absolute inset-y-[5px] left-0 rounded-[1px] bg-foreground/80" style={{ width: `${Math.min(100, w.plannedPct)}%` }} />}
            </span>
            <span className="font-mono tnum">{w.currentPct.toFixed(1)}% → {w.plannedPct === null ? "—" : `${w.plannedPct.toFixed(1)}%`}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 flex flex-wrap gap-x-4 font-mono text-[10px] text-muted-foreground">
        <span><i className="mr-1 inline-block h-2 w-3 rounded-[1px] bg-foreground/15 align-middle" />현재</span>
        <span><i className="mr-1 inline-block h-1 w-3 rounded-[1px] bg-foreground/80 align-middle" />계획 후(추정)</span>
      </p>
      <p className="mt-1 text-xs text-muted-foreground">{flow.weightsNote}</p>
    </section>
  );
}

function Log({ flow }: { flow: AccountFlow }) {
  return (
    <section aria-labelledby="log">
      <StepHeading n={5} id="log" title="실행 기록" aside="실제 시가로 기록된 모의 체결만" />
      {flow.log.length === 0 ? (
        <p className="rounded-md border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
          아직 체결된 주문이 없습니다. 첫 체결은 다음 실제 시가가 기록된 뒤에 나타납니다.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>날짜</TableHead><TableHead>종목</TableHead><TableHead>동작</TableHead>
              <TableHead className="text-right">체결가</TableHead><TableHead className="text-right">수량</TableHead>
              <TableHead className="text-right">금액</TableHead><TableHead className="text-right">비용</TableHead><TableHead>비고</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {flow.log.slice(0, 40).map((r) => (
              <TableRow key={r.key} className={cn(!r.filled && "text-muted-foreground")}>
                <TableCell className="font-mono text-xs tnum">{r.date}<span className="ml-1 text-[10px] text-muted-foreground">{r.market}</span></TableCell>
                <TableCell>{r.name}</TableCell>
                <TableCell><span className={cn("font-mono text-xs", r.side === "buy" ? "text-up" : "text-down")}>{r.side === "buy" ? "매수" : "매도"}</span>{!r.filled && <span className="ml-1 text-[10px]">미체결</span>}</TableCell>
                <TableCell className="text-right font-mono text-xs tnum">{r.price === null ? "—" : r.price.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}</TableCell>
                <TableCell className="text-right font-mono text-xs tnum">{r.shares === null ? "—" : r.shares.toLocaleString("ko-KR", { maximumFractionDigits: 4 })}</TableCell>
                <TableCell className="text-right font-mono text-xs tnum">{won(r.amountKrw)}</TableCell>
                <TableCell className="text-right font-mono text-xs tnum">{won(r.feesKrw)}</TableCell>
                <TableCell className="min-w-[10rem] text-xs">{r.note}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}

export function AccountFlowView({ flow, method }: { flow: AccountFlow; method: string }) {
  return (
    <div className="flex flex-col gap-10">
      <Valuation flow={flow} />
      <Changes flow={flow} />
      <section aria-labelledby="plan">
        <StepHeading n={3} id="plan" title="다음 시가 행동" aside="매수 · 매도 · 유지와 이유" />
        <div className="grid gap-4">{flow.plans.map((p) => <PlanCard key={p.key} plan={p} />)}</div>
      </section>
      <Weights flow={flow} />
      <Log flow={flow} />
      {flow.issues.length > 0 && (
        <Alert variant="destructive">
          <AlertTriangle className="size-4" aria-hidden="true" />
          <AlertTitle>일부 입력을 확인하세요</AlertTitle>
          <AlertDescription>{flow.issues.join(" · ")}</AlertDescription>
        </Alert>
      )}
      <p className="border-t border-border pt-3 text-xs leading-5 text-muted-foreground">{method}</p>
    </div>
  );
}
