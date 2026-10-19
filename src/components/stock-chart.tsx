import { CandleChart, type Candle, type ReportMark } from "@/components/candle-chart";
import { PriceSeriesDocumentSchema, TradeMarksDocumentSchema, type TradeMarksDocument } from "@/lib/api-v2";
import { readV2Optional } from "@/lib/api-v2-server";
import { dateLabel, type ReportRecord } from "@/lib/report-model";
import { schoolShort } from "@/lib/verdict";
import { formatPrice } from "@/lib/utils";

/** GET /api/v2/instruments/{id}/prices — 404(가격 데이터 없음)이면 null, 스키마 위반은 빌드 실패. */
async function loadCandles(slug: string): Promise<Candle[] | null> {
  const doc = await readV2Optional(`/api/v2/instruments/${slug}/prices`, PriceSeriesDocumentSchema);
  if (!doc) return null;
  const valid: Candle[] = [];
  for (const c of doc.data.candles) {
    const { open, high, low, close } = c;
    // 거래정지일(시·고·저 0 또는 null)은 캔들로 그릴 수 없다
    if (open === null || high === null || low === null || ![open, high, low, close].every(Number.isFinite)) continue;
    if (open === 0 || high === 0 || low === 0) continue;
    valid.push({ time: c.time, open, high, low, close, volume: c.volume ?? 0 });
  }
  // lightweight-charts는 시간 오름차순·중복 없는 데이터를 요구한다
  valid.sort((a, b) => a.time.localeCompare(b.time));
  const candles = valid.filter((c, i) => i === 0 || c.time !== valid[i - 1].time);
  return candles.length >= 2 ? candles : null;
}

/** GET /api/v2/instruments/{id}/trade-marks — 헤드라인 전략이 거래한 종목에만 존재한다 (404 = 마크 없음). */
async function loadStrategyMarks(slug: string): Promise<TradeMarksDocument["data"] | null> {
  const doc = await readV2Optional(`/api/v2/instruments/${slug}/trade-marks`, TradeMarksDocumentSchema);
  if (!doc || (!doc.data.marks.length && !doc.data.open_stop)) return null;
  return doc.data;
}

/** 종목 캔들 차트 — SSG 시점에 v2 instrument prices 리소스를 읽어 클라이언트 차트에 넘긴다. */
export async function StockChart({ slug, reports }: { slug: string; reports: ReportRecord[] }) {
  const [candles, strategyMarks] = await Promise.all([loadCandles(slug), loadStrategyMarks(slug)]);
  if (!candles) {
    return (
      <div className="rounded-lg border border-dashed border-border px-6 py-10 text-center text-sm text-muted-foreground">
        가격 차트 데이터가 아직 없습니다. (scripts/export_stock_charts.py 실행 필요)
      </div>
    );
  }

  const market = reports[0]?.market ?? null;
  const first = candles[0];
  const last = candles[candles.length - 1];
  const marks: ReportMark[] = reports
    .filter((r) => r.report_date)
    .map((r) => ({
      sourceName: r.source_name,
      school: r.school,
      date: r.report_date as string,
      targetPrice: r.target_price,
      targetSeq: r.target_seq,
      targetSeqTotal: r.target_seq_total,
    }));
  const schools = [...new Set(marks.map((m) => m.school))];
  const hasSequence = marks.some((m) => (m.targetSeqTotal ?? 0) > 1);

  return (
    <figure className="rounded-lg border border-border bg-card p-5">
      <figcaption className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">
          가격 경로 — ▲ 발간 시점 · ┄ 학회 목표가
        </span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {dateLabel(first.time)} ~ {dateLabel(last.time)} · 캔들 {candles.length.toLocaleString("ko-KR")}개 · 마지막 종가{" "}
          {formatPrice(last.close, market)}
        </span>
      </figcaption>
      <CandleChart
        candles={candles}
        marks={marks}
        market={market}
        tradeMarks={strategyMarks?.marks}
        currentStop={strategyMarks?.open_stop?.stop_level ?? null}
      />
      {strategyMarks && (
        <p className="mt-2 font-mono text-[10px] text-muted-foreground">
          <span className="font-bold text-up">▲ 매수</span> · <span className="font-bold text-down">▼ 매도/스탑</span>
          {strategyMarks.open_stop && <> · ┄ 현재 추적 스탑 {formatPrice(strategyMarks.open_stop.stop_level, market)}</>}
          {" — "}전략 랩 SOTA({strategyMarks.strategy_id})의 백테스트 매매 지점 · 시뮬레이션 결과이며 투자 권유가 아닙니다
        </p>
      )}
      {(schools.length > 1 || hasSequence) && (
        <p className="mt-2 font-mono text-[10px] text-muted-foreground">
          {schools.length > 1 && <>마커 색상: {schools.map((school) => schoolShort[school]).join(" · ")} — 학회별로 다른 잉크가 찍힙니다</>}
          {schools.length > 1 && hasSequence && " · "}
          {hasSequence && "같은 학회가 목표를 거듭 제시한 경우, 목표 1/3 → 2/3 → 3/3 순으로 번호가 붙습니다"}
        </p>
      )}
    </figure>
  );
}
