"use client";

import { useEffect, useMemo, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useTheme } from "@/components/theme-provider";
import { chartMonoFamily, cssHsl } from "@/lib/chart-colors";
import { schoolShort } from "@/lib/verdict";
import type { School } from "@/lib/report-model";

export type Candle = { time: string; open: number; high: number; low: number; close: number; volume: number };
/** SOTA 전략 백테스트 매매 지점 — ▲ 매수 / ▼ 매도·스탑 */
export type TradeMark = {
  date: string;
  side: "buy" | "sell" | "stop";
  price: number | null;
  reason: string;
};
export type ReportMark = {
  sourceName: string;
  /** 본문 검색 리포트 id — 선택(?report=<id>)하면 이 마커를 강조하고, 마커 클릭은 이 id로 근거를 연다 */
  reportId: string;
  /** 아카이브(채점 제외) 기록 — 검색이 가리킬 때만 그린다 */
  archived?: boolean;
  school: School;
  date: string;
  targetPrice: number | null;
  /** 목표 시퀀스 — 같은 학회가 이 종목에 제시한 N번째 목표 (총 M회). 단발이면 null/1 */
  targetSeq: number | null;
  targetSeqTotal: number | null;
};

/** 학회별 마커 잉크 — 발간 시점을 차트에 찍는다 */
const SCHOOL_VARS: Record<School, string> = {
  smic: "--stamp",
  yig: "--down",
  star: "--warn",
  kuvic: "--quality",
  ewha: "--ewha",
  voera: "--voera",
};

function shiftDays(date: string, days: number) {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** 리포트 발간일을 실제 캔들 시간축에 맞춘다 (주봉 구간 대응) */
function snapToCandle(date: string, times: string[]) {
  let lo = 0;
  let hi = times.length - 1;
  if (date <= times[0]) return times[0];
  if (date >= times[hi]) return times[hi];
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] < date) lo = mid + 1;
    else hi = mid;
  }
  return times[lo];
}

export type ChartFocus = { date: string; mark?: ReportMark; label: string };

export function CandleChart({
  candles,
  marks,
  market,
  tradeMarks,
  currentStop,
  focus = null,
  onSelectReport,
}: {
  candles: Candle[];
  /** 그릴 발간 마커 — 아카이브는 focus가 가리킬 때만 그린다 */
  marks: ReportMark[];
  market: string | null;
  /** SOTA 전략 백테스트 매매 마커 — 없으면 발간 마커만 그린다 */
  tradeMarks?: TradeMark[];
  /** 보유 중 포지션의 현재 추적 스탑 — 점선 수평선 */
  currentStop?: number | null;
  /** 선택한 리포트(또는 다른 종목 리포트의 언급일) — ◆로 강조하고 앞 6개월~뒤 12개월을 보여 준다 */
  focus?: ChartFocus | null;
  /** 발간 마커 클릭 — 그 리포트의 근거를 연다 */
  onSelectReport?: (reportId: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const { resolvedTheme } = useTheme();

  const times = useMemo(() => candles.map((c) => c.time), [candles]);
  const focusInRange = focus !== null && times.length > 1 && focus.date >= times[0] && focus.date <= times[times.length - 1];

  // 클릭 핸들러는 차트를 다시 만들지 않고 최신 마커·콜백을 읽는다
  const clickRef = useRef({ marks, onSelectReport, times });
  useEffect(() => {
    clickRef.current = { marks, onSelectReport, times };
  }, [marks, onSelectReport, times]);

  // ① 차트·캔들·거래량·스탑 — 데이터나 테마가 바뀔 때만 다시 만든다
  useEffect(() => {
    const container = containerRef.current;
    if (!container || candles.length < 2) return;

    const up = cssHsl("--up");
    const down = cssHsl("--down");
    const text = cssHsl("--muted-foreground");
    const grid = cssHsl("--border", 0.45);
    const border = cssHsl("--border");

    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: text,
        fontFamily: chartMonoFamily(),
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: grid, style: LineStyle.Dotted },
        horzLines: { color: grid, style: LineStyle.Dotted },
      },
      rightPriceScale: { borderColor: border, scaleMargins: { top: 0.08, bottom: 0.22 } },
      timeScale: { borderColor: border, timeVisible: false },
      crosshair: {
        vertLine: { color: cssHsl("--foreground", 0.4), labelBackgroundColor: cssHsl("--foreground") },
        horzLine: { color: cssHsl("--foreground", 0.4), labelBackgroundColor: cssHsl("--foreground") },
      },
      localization: {
        priceFormatter: (price: number) =>
          market === "US"
            ? `$${price.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}`
            : price.toLocaleString("ko-KR", { maximumFractionDigits: 0 }),
      },
    });
    chartRef.current = chart;

    // 캔들 — 한국 증시 색 관습: 상승 적색, 하락 청색
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      borderUpColor: up,
      borderDownColor: down,
      wickUpColor: up,
      wickDownColor: down,
    });
    candleSeries.setData(candles.map((c) => ({ time: c.time as Time, open: c.open, high: c.high, low: c.low, close: c.close })));
    seriesRef.current = candleSeries;

    // 거래량 — 하단 18% 영역의 히스토그램
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 }, visible: false });
    volumeSeries.setData(
      candles.map((c) => ({
        time: c.time as Time,
        value: Number.isFinite(c.volume) ? c.volume : 0,
        color: c.close >= c.open ? cssHsl("--up", 0.35) : cssHsl("--down", 0.35),
      })),
    );
    markersRef.current = createSeriesMarkers(candleSeries, []);

    // 현재 추적 스탑 — 보유 중일 때만 점선으로 깔린다
    if (typeof currentStop === "number" && Number.isFinite(currentStop)) {
      const stopText =
        market === "US"
          ? `$${currentStop.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}`
          : currentStop.toLocaleString("ko-KR", { maximumFractionDigits: 0 });
      candleSeries.createPriceLine({
        price: currentStop,
        color: down,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `SOTA 스탑 ${stopText}`,
      });
    }

    // 발간 마커 클릭 → 그날(캔들에 맞춘 날) 찍힌 리포트의 근거를 연다. 같은 날 여러 건이면 첫 건
    const markAt = (time: unknown) => {
      const { marks: current, times: axis } = clickRef.current;
      return typeof time === "string" ? current.find((m) => snapToCandle(m.date, axis) === time) : undefined;
    };
    chart.subscribeClick((param) => {
      const mark = markAt(param.time);
      if (mark) clickRef.current.onSelectReport?.(mark.reportId);
    });
    chart.subscribeCrosshairMove((param) => {
      container.style.cursor = clickRef.current.onSelectReport && markAt(param.time) ? "pointer" : "";
    });

    chart.timeScale().fitContent();

    return () => {
      chartRef.current = null;
      seriesRef.current = null;
      markersRef.current = null;
      chart.remove();
    };
    // resolvedTheme 변경 시 차트를 테마 색으로 다시 그린다
  }, [candles, market, currentStop, resolvedTheme]);

  // ② 발간·매매 마커와 목표가 수평선 — 선택·관점이 바뀌면 차트를 유지한 채 다시 찍는다.
  // ①과 같은 의존성을 포함해 차트가 새로 만들어진 커밋에서도 이어서 실행된다.
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart || times.length < 2) return;
    const up = cssHsl("--up");
    const down = cssHsl("--down");

    const reportMarkers: SeriesMarker<Time>[] = marks
      .filter((mark) => !mark.archived && mark !== focus?.mark)
      .map((mark) => ({
        time: snapToCandle(mark.date, times) as Time,
        position: "belowBar" as const,
        shape: "arrowUp" as const,
        color: cssHsl(SCHOOL_VARS[mark.school]),
        text: schoolShort[mark.school],
        size: 1.4,
      }));
    if (focus && focusInRange) {
      reportMarkers.push({
        time: snapToCandle(focus.date, times) as Time,
        position: "belowBar",
        shape: "arrowUp",
        color: cssHsl("--foreground"),
        text: `◆ ${focus.label}`,
        size: 2.4,
      });
    }

    // SOTA 매매 마커 — ▲ 매수(상승색) 아래, ▼ 매도·스탑(하락색) 위
    const tradeMarkers: SeriesMarker<Time>[] = (tradeMarks ?? []).map((mark) => ({
      time: snapToCandle(mark.date, times) as Time,
      position: mark.side === "buy" ? ("belowBar" as const) : ("aboveBar" as const),
      shape: mark.side === "buy" ? ("arrowUp" as const) : ("arrowDown" as const),
      color: mark.side === "buy" ? up : down,
      text: mark.side === "buy" ? "매수" : mark.side === "stop" ? "스탑" : "매도",
      size: 1.1,
    }));
    markersRef.current?.setMarkers([...reportMarkers, ...tradeMarkers].sort((a, b) => String(a.time).localeCompare(String(b.time))));

    // 목표가 수평선 — 그날의 주장이 가격축에 남는다. 거듭 제시한 목표는 목표 1/3 → 2/3 순으로 번호가 붙는다
    const lines: IPriceLine[] = [];
    for (const mark of marks) {
      if (mark.targetPrice === null || (mark.archived && mark !== focus?.mark)) continue;
      const seq = mark.targetSeq !== null && (mark.targetSeqTotal ?? 0) > 1 ? ` ${mark.targetSeq}/${mark.targetSeqTotal}` : "";
      const priceText =
        market === "US"
          ? `$${mark.targetPrice.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}`
          : mark.targetPrice.toLocaleString("ko-KR", { maximumFractionDigits: 0 });
      const selected = mark === focus?.mark;
      lines.push(series.createPriceLine({
        price: mark.targetPrice,
        color: cssHsl(SCHOOL_VARS[mark.school], selected ? 1 : 0.75),
        lineWidth: selected ? 2 : 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `${schoolShort[mark.school]} 목표${seq} ${priceText}`,
      }));
    }

    // 선택한 시점: 발간일 앞 6개월 ~ 뒤 12개월
    if (focus && focusInRange) {
      chart.timeScale().setVisibleRange({
        from: snapToCandle(shiftDays(focus.date, -183), times) as Time,
        to: snapToCandle(shiftDays(focus.date, 365), times) as Time,
      });
    }

    return () => {
      // ①의 정리에서 차트가 먼저 제거됐다면 수평선도 함께 사라졌다
      if (seriesRef.current === series) for (const line of lines) series.removePriceLine(line);
    };
  }, [marks, focus, focusInRange, times, tradeMarks, market, candles, currentStop, resolvedTheme]);

  if (candles.length < 2) {
    return (
      <div className="rounded-lg border border-dashed border-border px-6 py-10 text-center text-sm text-muted-foreground">
        가격 차트 데이터가 부족합니다.
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="h-[300px] w-full sm:h-[420px]"
      role="img"
      aria-label={`종목 캔들 차트 — 발간 시점 마커와 학회별 목표가 수평선 포함.${onSelectReport ? " 마커를 누르면 그 리포트의 근거가 열립니다. 키보드로는 차트 아래 리포트 목록을 사용하세요." : ""}`}
    />
  );
}
