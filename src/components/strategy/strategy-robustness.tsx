import { readFile } from "node:fs/promises";
import path from "node:path";
import Link from "next/link";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StrategyRobustnessSchema } from "@/lib/strategy-robustness";
import { formatPct } from "@/lib/utils";
import { signColor } from "@/lib/verdict";

export async function StrategyRobustness() {
  const data = StrategyRobustnessSchema.parse(JSON.parse(await readFile(path.join(process.cwd(), "public/research/strategy-robustness.json"), "utf8")));
  const labels: Record<string, string> = { baseline: "기준 W", exclude_top_1: "상위 1종목 제외", exclude_top_3: "상위 3종목 제외", exclude_top_5: "상위 5종목 제외", delay_1: "진입 +1거래일", delay_3: "진입 +3거래일", delay_5: "진입 +5거래일" };
  const names = new Map(data.ranking.map(r => [r.ticker, r.name]));
  return <section id="robustness" className="mt-10 flex flex-col gap-4 border-t pt-6">
    <div><p className="font-mono text-xs text-muted-foreground">FIXED-POLICY DIAGNOSTICS · {data.as_of}</p>
      <h2 className="mt-2 font-display text-2xl font-black">성과가 몇 종목에 기대고 있는가</h2>
      <p className="mt-2 text-sm text-muted-foreground">ATR {data.policy.atr_period} × {data.policy.atr_mult} · 최대 {data.policy.max_positions}종목. 파라미터 재탐색 없이 공통 실행 엔진으로 7개 조건을 비교했습니다.</p></div>
    <Alert><AlertTitle>과거 구간 민감도 검사</AlertTitle><AlertDescription>{data.method} 지연 조건의 더 높은 수익률은 채택 근거로 사용하지 않습니다.</AlertDescription></Alert>
    <Table>
      <TableCaption>제외 종목은 원화 순손익 순위로 선택합니다. 비워진 자금과 매매 기회도 전체 엔진에서 다시 계산합니다.</TableCaption>
      <TableHeader><TableRow><TableHead>조건</TableHead><TableHead className="text-right">CAGR</TableHead><TableHead className="text-right">최대 낙폭</TableHead><TableHead className="text-right">Sharpe</TableHead></TableRow></TableHeader>
      <TableBody>{data.scenarios.map(s => <TableRow key={s.id}>
        <TableCell className="min-w-28 max-w-44 whitespace-normal"><span className="font-medium">{labels[s.id]}</span>{s.excluded_tickers.length > 0 && <span className="mt-1 block text-xs text-muted-foreground">{s.excluded_tickers.map(t => names.get(t) ?? t).join(" · ")}</span>}</TableCell>
        <TableCell className={`text-right tabular-nums ${signColor(s.metrics.cagr_pct)}`}>{formatPct(s.metrics.cagr_pct)}</TableCell>
        <TableCell className="text-right tabular-nums text-down">{formatPct(s.metrics.mdd_pct)}</TableCell>
        <TableCell className="text-right tabular-nums">{s.metrics.sharpe.toFixed(2)}</TableCell>
      </TableRow>)}</TableBody>
    </Table>
    <p className="text-sm text-muted-foreground">상위 5종목의 양의 순손익 기여 비중: <span className="tabular-nums">{data.top5_positive_pnl_share_pct?.toFixed(1) ?? "—"}%</span>. 미청산 평가손익 포함, 파킹 손익 제외.</p>
    <div className="flex flex-wrap gap-4 text-sm underline underline-offset-4"><Link href="/portfolio/paper">같은 엔진의 전진 모의계정</Link><a href="/research/strategy-robustness.json">진단 원본 JSON</a></div>
  </section>;
}
