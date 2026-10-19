"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  BaselineSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  LineStyle,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useTheme } from "@/components/theme-provider";
import { PriceSeriesDocumentSchema } from "@/lib/api-v2";
import { chartMonoFamily, cssHsl } from "@/lib/chart-colors";
import { cn } from "@/lib/utils";

type Candle = { time: string; close: number };

/** 같은 종목 JSON을 여러 카드가 다시 받지 않도록 모듈 레벨에서 공유한다 */
const priceCache = new Map<string, Promise<Candle[] | null>>();

/** GET /api/v2/instruments/{id}/prices — 404·네트워크 오류·스키마 위반은 모두 "시세 없음"(null)으로 그린다 */
function fetchCandles(slug: string): Promise<Candle[] | null> {
  const cached = priceCache.get(slug);
  if (cached) return cached;
  const promise = fetch(`/api/v2/instruments/${slug}/prices`)
    .then(async (res): Promise<Candle[] | null> => {
      if (!res.ok) return null;
      const parsed = PriceSeriesDocumentSchema.safeParse(await res.json());
      if (!parsed.success) return null;
      const valid = parsed.data.data.candles
        .filter((c) => Number.isFinite(c.close))
        .map((c) => ({ time: c.time, close: c.close }));
      valid.sort((a, b) => a.time.localeCompare(b.time));
      const candles = valid.filter((c, i) => i === 0 || c.time !== valid[i - 1].time);
      return candles.length >= 2 ? candles : null;
    })
    .catch(() => null);
  priceCache.set(slug, promise);
  return promise;
}

/** 동시에 살아있는 차트 수 상한 — 수백 장의 판결 기록이 한 번에 캔버스를 만들지 않도록 */
const MAX_LIVE_CHARTS = 6;
const liveCharts = new Set<symbol>();

export type ReportPathChartProps = {
  slug: string;
  market: string | null;
  /** 발간일 — 기준선(발간가)이 이 날의 종가에 찍힌다 */
  reportDate: string;
  targetPrice: number | null;
  /** 목표가 최초 도달일 — 적중 마커 */
  hitDate?: string | null;
  /** true면 IntersectionObserver를 건너뛰고 즉시 마운트 (펼침/클릭 컨텍스트) */
  eager?: boolean;
  /** 발간일 이전 며칠까지 보여줄지 — 발간 직전 맥락 */
  lookbackDays?: number;
  className?: string;
  /** 시세 데이터가 발간일을 덮지 못할 때 대신 그릴 내용 */
  fallback?: ReactNode;
};

/**
 * 판결 기록 가격 경로 — 발간가를 기준선으로 위는 적색, 아래는 청색(한국 관습).
 * 뷰포트에 들어올 때만 차트를 만들고, 벗어나면 해제해 수백 개가 동시에 살지 않는다.
 */
export function ReportPathChart({
  slug,
  market,
  reportDate,
  targetPrice,
  hitDate,
  eager = false,
  lookbackDays = 45,
  className,
  fallback,
}: ReportPathChartProps) {
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [active, setActive] = useState(eager);
  const [candles, setCandles] = useState<Candle[] | null | undefined>(undefined);
  const { resolvedTheme } = useTheme();

  // 1) 뷰포트 감시 — 보일 때만 살리고, 멀어지면 자리만 남긴다
  useEffect(() => {
    if (eager) return;
    const node = wrapperRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      ([entry]) => setActive(entry.isIntersecting),
      { rootMargin: "260px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [eager]);

  // 2) 데이터는 처음 활성화될 때 한 번만 받는다 (모듈 캐시 공유)
  useEffect(() => {
    if (!active || candles !== undefined) return;
    let cancelled = false;
    fetchCandles(slug).then((data) => {
      if (!cancelled) setCandles(data);
    });
    return () => {
      cancelled = true;
    };
  }, [active, candles, slug]);

  // 3) 차트 생성/해제 — 동시 마운트 상한을 지킨다
  useEffect(() => {
    const container = containerRef.current;
    if (!active || !container || !candles) return;

    // 발간일 이후 구간만 의미가 있다 — 시세가 발간일을 덮지 못하면 그리지 않는다
    if (reportDate < candles[0].time) return;
    const fromTime = new Date(new Date(reportDate).getTime() - lookbackDays * 86_400_000).toISOString().slice(0, 10);
    const sliced = candles.filter((c) => c.time >= fromTime);
    if (sliced.length < 2) return;

    if (liveCharts.size >= MAX_LIVE_CHARTS) return;
    const token = Symbol(slug);
    liveCharts.add(token);

    // 발간일(또는 그 직후 첫 거래일)의 종가 = 기준선
    const pubCandle = sliced.find((c) => c.time >= reportDate) ?? sliced[0];
    const basePrice = pubCandle.close;

    const up = cssHsl("--up");
    const down = cssHsl("--down");
    const text = cssHsl("--muted-foreground");
    const border = cssHsl("--border");

    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: text,
        fontFamily: chartMonoFamily(),
        fontSize: 10,
        attributionLogo: false,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: cssHsl("--border", 0.35), style: LineStyle.Dotted },
      },
      rightPriceScale: { borderColor: border, scaleMargins: { top: 0.12, bottom: 0.1 } },
      timeScale: { borderColor: border, timeVisible: false },
      crosshair: {
        vertLine: { color: cssHsl("--foreground", 0.4), labelBackgroundColor: cssHsl("--foreground") },
        horzLine: { color: cssHsl("--foreground", 0.4), labelBackgroundColor: cssHsl("--foreground") },
      },
      handleScroll: false,
      handleScale: false,
      localization: {
        priceFormatter: (price: number) =>
          market === "US"
            ? `$${price.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}`
            : price.toLocaleString("ko-KR", { maximumFractionDigits: 0 }),
      },
    });

    // 기준선 = 발간가. 위는 적색(상승), 아래는 청색(하락)
    const series = chart.addSeries(BaselineSeries, {
      baseValue: { type: "price", price: basePrice },
      topLineColor: up,
      topFillColor1: cssHsl("--up", 0.26),
      topFillColor2: cssHsl("--up", 0.04),
      bottomLineColor: down,
      bottomFillColor1: cssHsl("--down", 0.04),
      bottomFillColor2: cssHsl("--down", 0.26),
      lineWidth: 2,
      priceLineVisible: false,
    });
    series.setData(sliced.map((c) => ({ time: c.time as Time, value: c.close })));

    series.createPriceLine({
      price: basePrice,
      color: cssHsl("--foreground", 0.55),
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "발간가",
    });
    if (targetPrice !== null && Number.isFinite(targetPrice)) {
      series.createPriceLine({
        price: targetPrice,
        color: cssHsl("--stamp", 0.85),
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "목표가",
      });
    }

    const markers: SeriesMarker<Time>[] = [
      {
        time: pubCandle.time as Time,
        position: "belowBar" as const,
        shape: "arrowUp" as const,
        color: cssHsl("--foreground", 0.85),
        text: "발간",
        size: 1.1,
      },
    ];
    if (hitDate) {
      const hitCandle = sliced.find((c) => c.time >= hitDate);
      if (hitCandle) {
        markers.push({
          time: hitCandle.time as Time,
          position: "aboveBar" as const,
          shape: "circle" as const,
          color: cssHsl("--stamp"),
          text: "적중",
          size: 1.1,
        });
      }
    }
    createSeriesMarkers(series, markers.sort((a, b) => String(a.time).localeCompare(String(b.time))));

    chart.timeScale().fitContent();

    return () => {
      liveCharts.delete(token);
      chart.remove();
    };
    // resolvedTheme 변경 시 테마 색으로 다시 그린다
  }, [active, candles, hitDate, lookbackDays, market, reportDate, slug, targetPrice, resolvedTheme]);

  const unavailable = candles === null || (candles && reportDate < candles[0].time);

  return (
    <div ref={wrapperRef} className={cn("relative", className)}>
      {unavailable ? (
        fallback ?? (
          <div className="flex h-full min-h-[120px] items-center justify-center rounded-md border border-dashed border-border px-4 text-center font-mono text-[11px] text-muted-foreground">
            이 발간일을 덮는 시세 데이터가 없습니다
          </div>
        )
      ) : (
        <div
          ref={containerRef}
          className="h-full w-full"
          role="img"
          aria-label="발간 이후 가격 경로 — 기준선은 발간가, 위는 상승(적색)·아래는 하락(청색)"
        >
          {candles === undefined && (
            <div className="flex h-full items-center justify-center font-mono text-[11px] text-muted-foreground">시세 불러오는 중…</div>
          )}
        </div>
      )}
    </div>
  );
}
