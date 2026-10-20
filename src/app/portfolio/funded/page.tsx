import type { Metadata } from "next";
import { CalendarClock, Hourglass } from "lucide-react";
import { SiteHeader } from "@/components/site-header";
import { AccountFlowView, AccountSwitch, STEPS, StepHeading, StepRail } from "@/components/account/account-flow";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fundedFlow } from "@/lib/account-flow";
import { FUNDED_PROPOSAL, principalAfter, type FundedAccount } from "@/lib/funded-account";
import { loadFundedAccount } from "@/lib/funded-account-data";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "납입식 모의 계정 — 판결 아카이브" };

const won = (v: number) => `${v.toLocaleString("ko-KR")}원`;
const STATUS_TONE = {
  "사용자 확정": "border-foreground/60 text-foreground",
  "제안값(미확정)": "border-warn/70 bg-warn/10 text-warn",
  "개시 시 동결": "border-dashed border-border text-muted-foreground",
} as const;

function StatusBadge({ label }: { label: keyof typeof STATUS_TONE }) {
  return <Badge variant="outline" className={cn("whitespace-nowrap rounded-sm font-mono text-[10px]", STATUS_TONE[label])}>{label}</Badge>;
}

function contributionTiers() {
  const c = FUNDED_PROPOSAL.contribution;
  return [0, 1, 2].map((k) => ({
    range: `${k * c.step_every_installments + 1}~${(k + 1) * c.step_every_installments}회`,
    amount: c.base_krw + c.step_krw * k,
  }));
}

function NotStarted() {
  const p = FUNDED_PROPOSAL;
  return (
    <div className="flex flex-col gap-8">
      <Alert className="border-warn/60">
        <Hourglass className="size-4 text-warn" aria-hidden="true" />
        <AlertTitle>미개시 — 이 계정에는 아직 기록이 없습니다</AlertTitle>
        <AlertDescription>
          평가액·주문 계획·체결은 개시 후 실제 종가와 시가가 기록될 때부터 나타납니다. 이 화면은 시연용 숫자나 dry-run 결과로 채우지 않습니다.
        </AlertDescription>
      </Alert>

      <section aria-labelledby="start-needs">
        <StepHeading id="start-needs" title="개시에 필요한 것" />
        <ol className="space-y-3 text-sm">
          <li className="rounded-md border border-border bg-card p-3">
            <p className="font-bold">1. 사용자 결정</p>
            <ul className="mt-1 list-disc space-y-1 pl-5 text-muted-foreground">
              <li><b className="text-foreground">개시 시점(epoch)</b> — 지금부터 24시간 이내, 공표 W 보유 목록 생성 이후의 시각. 그 시점 공표 보유 종목이 초기 매수 후보가 됩니다(과거 수량·수익은 승계하지 않음).</li>
              <li><b className="text-foreground">납입일 규칙</b> — 아래 제안값을 그대로 쓸지, 매월 1~28일 중 하루로 할지, 그리고 확정 여부.</li>
            </ul>
          </li>
          <li className="rounded-md border border-border bg-card p-3">
            <p className="font-bold">2. 운영자 실행 (한 번, 되돌릴 수 없는 genesis 기록)</p>
            <pre className="mt-2 overflow-x-auto rounded-sm bg-muted p-2.5 font-mono text-[11px] leading-5"><code>{`uv run python scripts/funded_account.py init --epoch <YYYY-MM-DDTHH:MM:SS+09:00> \\
  --contribution-day first-trading-day --contribution-day-status proposed`}</code></pre>
            <p className="mt-1.5 text-xs text-muted-foreground">
              <code>proposed</code>면 모든 화면·알림에 「제안값(미확정)」, <code>confirmed</code>면 「사용자 확정」이 붙습니다. 규칙을 바꾸려면 새 계정 ID가 필요합니다.
            </p>
          </li>
          <li className="rounded-md border border-border bg-card p-3">
            <p className="font-bold">3. 개시 후 매일</p>
            <p className="mt-1 text-muted-foreground">시장별 종가·입력 검증 뒤 다음 실제 거래일 시가 주문 계획을 동결하고, 실제 시가가 기록된 뒤에만 모의 체결을 남깁니다.</p>
          </li>
        </ol>
      </section>

      <section aria-labelledby="terms">
        <StepHeading id="terms" title="계정 조건" aside={p.account_id} />
        <Table>
          <TableHeader><TableRow><TableHead>항목</TableHead><TableHead>값</TableHead><TableHead>상태</TableHead></TableRow></TableHeader>
          <TableBody>
            <TableRow><TableCell>초기 자금</TableCell><TableCell className="font-mono tnum">{won(p.initial_capital_krw)}</TableCell><TableCell><StatusBadge label="사용자 확정" /></TableCell></TableRow>
            <TableRow>
              <TableCell>월 납입액</TableCell>
              <TableCell className="whitespace-normal text-xs">
                <ul className="font-mono tnum">{contributionTiers().map((t) => <li key={t.range}>{t.range} {won(t.amount)}</li>)}<li>… {p.contribution.step_every_installments}회마다 +{won(p.contribution.step_krw)}</li></ul>
              </TableCell>
              <TableCell><StatusBadge label="사용자 확정" /></TableCell>
            </TableRow>
            <TableRow>
              <TableCell>납입일</TableCell>
              <TableCell className="whitespace-normal text-xs"><CalendarClock className="mr-1 inline size-4 align-text-bottom" aria-hidden="true" />{p.contribution_day.description}</TableCell>
              <TableCell><StatusBadge label={p.contribution_day.label} /></TableCell>
            </TableRow>
            <TableRow>
              <TableCell>정책</TableCell>
              <TableCell className="whitespace-normal text-xs">W — 신규 학회 매수 리포트 → 다음 시가 진입(NAV {(p.position_weight * 100).toFixed(0)}%), ATR42×5.75 샹들리에 종가 스탑 → 다음 시가 청산, 최대 {p.max_positions}종목, 정수 주식, 편도 {(p.cost_per_side * 100).toFixed(1)}%</TableCell>
              <TableCell><StatusBadge label="개시 시 동결" /></TableCell>
            </TableRow>
            <TableRow>
              <TableCell>미투자 잔액</TableCell>
              <TableCell className="whitespace-normal text-xs">무이자 원화 현금 (올웨더 합성지수는 주문 불가라 파킹하지 않음 — W 백테스트와 성과가 다름)</TableCell>
              <TableCell><StatusBadge label="개시 시 동결" /></TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </section>

      <section aria-labelledby="oracle">
        <StepHeading id="oracle" title="원금 합계 검증" aside="무수익·무비용 — 수익 예측 아님" />
        <dl className="grid grid-cols-3 gap-2 text-center">
          {[24, 48, 120].map((n) => (
            <div key={n} className="rounded-sm border border-border bg-card px-2 py-2">
              <dt className="font-mono text-[11px] text-muted-foreground tnum">{n}회 납입 후</dt>
              <dd className="font-mono text-sm tnum">{(principalAfter(n) / 1e8).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}억원</dd>
            </div>
          ))}
        </dl>
        <p className="mt-2 text-xs text-muted-foreground">개시 후 화면은 이 원금과 투자 손익을 분리해 보여 주고, 수익률(TWR)에서 납입을 제외합니다.</p>
      </section>

      <section aria-label="개시 후 표시될 흐름">
        <p className="mb-2 font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">개시 후 이 순서로 표시됩니다</p>
        <ol className="grid gap-2 sm:grid-cols-5">
          {STEPS.map((s, i) => (
            <li key={s.id} className="rounded-sm border border-dashed border-border px-3 py-3 text-sm text-muted-foreground">
              <span className="font-mono text-xs font-bold tnum">{i + 1}</span> {s.label}
              <p className="mt-1 text-[11px]">기록 없음</p>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

function Started({ account }: { account: FundedAccount }) {
  const flow = fundedFlow(account);
  const rule = account.config.contribution_day;
  const next = account.next_contribution;
  return (
    <div className="flex flex-col gap-6">
      <p className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-xs">
        <span className="font-bold">납입 규칙</span><span>{rule.description}</span><StatusBadge label={rule.label} />
        <span className="font-mono tnum text-muted-foreground">
          다음 {next.installment}회 {won(next.amount_krw)} · {next.scheduled_date ?? "날짜 미정"} ({next.label})
        </span>
      </p>
      <AccountFlowView flow={flow} method={account.method} />
    </div>
  );
}

export default async function FundedAccountPage() {
  const load = await loadFundedAccount();
  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-8">
      <SiteHeader eyebrow="FUNDED PAPER ACCOUNT" />
      <main className="flex flex-col gap-6 pt-8">
        <header className="flex flex-col gap-3">
          <h1 className="font-display text-3xl font-black tracking-tight sm:text-4xl">납입식 모의 계정</h1>
          <p className="text-sm text-muted-foreground">
            초기 5천만원 + 체증식 월 납입 · 정수 주식 · 익일 시가 주문 계획. 1억원 전진 모의 계정과 별개의 계정이며 실계좌 주문이 아닙니다.
          </p>
          <AccountSwitch current="/portfolio/funded" fundedStarted={load.status === "ready"} />
          <StepRail disabled={load.status !== "ready"} />
        </header>
        {load.status === "ready" ? <Started account={load.account} /> : <NotStarted />}
      </main>
    </div>
  );
}
