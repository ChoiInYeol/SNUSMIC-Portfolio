import fs from "node:fs/promises";
import path from "node:path";
import type { Metadata } from "next";
import { SiteHeader } from "@/components/site-header";
import { AccountFlowView, AccountSwitch, StepRail } from "@/components/account/account-flow";
import { paperFlow } from "@/lib/account-flow";
import { loadFundedAccount } from "@/lib/funded-account-data";
import { PaperPortfolioSchema } from "@/lib/paper-portfolio";

export const metadata: Metadata = { title: "모의 계정 — 판결 아카이브" };

export default async function PortfolioPage() {
  const d = PaperPortfolioSchema.parse(JSON.parse(await fs.readFile(path.join(process.cwd(), "public/research/portfolio.json"), "utf8")));
  const funded = await loadFundedAccount();
  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-8">
      <SiteHeader eyebrow="FORWARD PAPER ACCOUNTS" />
      <main className="flex flex-col gap-6 pt-8">
        <header className="flex flex-col gap-3">
          <h1 className="font-display text-3xl font-black tracking-tight sm:text-4xl">모의 계정</h1>
          <p className="break-words text-sm text-muted-foreground">
            1억원 전진 모의 · {d.strategy} · 기록 시작 {new Date(d.started_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", dateStyle: "medium", timeStyle: "short" })} KST.
            실제 증권계좌·실시간 호가가 아니며 과거 백테스트 수익을 승계하지 않습니다.
          </p>
          <AccountSwitch current="/portfolio" fundedStarted={funded.status === "ready"} />
          <StepRail />
        </header>
        <AccountFlowView flow={paperFlow(d)} method={d.method} />
      </main>
    </div>
  );
}
