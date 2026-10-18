import { promises as fs } from "node:fs";
import path from "node:path";
import Link from "next/link";
import type { Metadata } from "next";
import { SiteHeader } from "@/components/site-header";
import { StatStrip } from "@/components/report-ledger";
import { cn, formatPct } from "@/lib/utils";

export const metadata: Metadata = {
  title: "전진 기록 — 판결 아카이브",
  description:
    "백테스트가 아니라 기록. 매일 커밋 시점에 박제된 매수 신호를 이후의 실현 시세로 채점하는 전진(forward) 트랙 레코드.",
};

/* ── forward-record.json 타입 ────────────────────────────────────────────── */

type ForwardEntry = {
  signal_date: string;
  ticker: string;
  market: string;
  name: string;
  basis_price: number | null;
  entry_date: string | null;
  entry_price: number | null;
  current_price: number | null;
  return_pct: number | null;
  peak_pct: number | null;
  days: number;
  status: "tracking" | "pending";
  trigger_schools: string[];
  headline_strategy?: string;
};

type ForwardRecord = {
  generated_at: string;
  method: string;
  summary: {
    n_signals: number;
    n_tracking: number;
    avg_return_pct: number | null;
    win_rate_pct: number | null;
    best_pct: number | null;
    worst_pct: number | null;
    first_snapshot: string;
    last_snapshot: string;
    n_snapshots: number;
  };
  entries: ForwardEntry[];
};

async function loadRecord(): Promise<ForwardRecord | null> {
  try {
    const raw = await fs.readFile(
      path.join(process.cwd(), "src", "data", "forward-record.json"),
      "utf-8",
    );
    return JSON.parse(raw) as ForwardRecord;
  } catch {
    return null;
  }
}

function signColor(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "text-muted-foreground";
  return v > 0 ? "text-up" : v < 0 ? "text-down" : "";
}

function fmtLocalPrice(v: number | null, market: string): string {
  if (v == null) return "—";
  return market === "US"
    ? `$${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}`
    : `${Math.round(v).toLocaleString("ko-KR")}원`;
}

function fmtPctSafe(v: number | null | undefined): string {
  return v != null && Number.isFinite(v) ? formatPct(v, 1) : "—";
}

export default async function TrackPage() {
  const record = await loadRecord();
  const summary = record?.summary;
  const entries = record?.entries ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-10 px-4 pb-20 pt-6 sm:px-6">
      <SiteHeader eyebrow="Forward Track Record" />

      <header>
        <h1 className="mt-2 font-display text-4xl font-black leading-[1.15] tracking-tight sm:text-5xl">
          백테스트가 아니라, <span className="text-stamp">기록</span>이다.
        </h1>
        <p className="mt-3 max-w-3xl text-base leading-7 text-muted-foreground">
          이 장부는 공개된 신호 스냅샷과 이후 시세를 비교합니다. 최초 공개 시각과 데이터 커버리지가 확인되지 않는 경우 결과는
          <strong className="text-foreground"> 확인 불가</strong>로 표시하며, 사후 수정 불가나 미래 미관측을 보장하지 않습니다.
        </p>
        <p className="mt-1.5 text-sm italic text-muted-foreground">
          채점 규칙: 진입 = 신호일 익일 시가 · 수익률 = 로컬 통화 종가 기준 · 동일 티커 7일 내 반복
          신호 제외. 전략 규칙(스탑·슬롯) 재현이 아니라 공표된 신호 자체의 성적입니다.
        </p>
      </header>

      {summary && (
        <StatStrip
          items={[
            { label: "기록 시작", value: summary.first_snapshot },
            { label: "스냅샷", value: `${summary.n_snapshots}일` },
            { label: "공표 신호", value: `${summary.n_signals}건` },
            {
              label: "평균 수익률",
              value: fmtPctSafe(summary.avg_return_pct),
              tone: signColor(summary.avg_return_pct),
            },
            {
              label: "승률",
              value: summary.win_rate_pct != null && Number.isFinite(summary.win_rate_pct) ? `${summary.win_rate_pct}%` : "—",
            },
            {
              label: "최고 / 최악",
              value:
                summary.best_pct != null
                  ? `${fmtPctSafe(summary.best_pct)} / ${fmtPctSafe(summary.worst_pct)}`
                  : "—",
            },
          ]}
        />
      )}

      {entries.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-card p-10 text-center">
          <p className="font-display text-xl font-black tracking-tight">
            기록 장치는 {summary?.first_snapshot ?? "2026-06-11"}부터 가동 중입니다.
          </p>
          <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-muted-foreground">
            아직 채점할 신호가 없습니다. 다음 매수 신호가 공표되는 순간부터 이 장부에 한 줄씩
            박제됩니다 — 좋은 신호도, 나쁜 신호도, 전부. 오늘의 임박 주문은{" "}
            <Link href="/strategy" className="underline hover:text-foreground">
              전략 페이지
            </Link>
            에서 볼 수 있습니다.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border bg-card p-5">
          <table className="w-full min-w-[900px] border-collapse text-sm">
            <thead>
              <tr className="border-b-4 border-double border-border font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                <th className="px-3 py-2 text-left">신호일</th>
                <th className="px-3 py-2 text-left">종목</th>
                <th className="px-3 py-2 text-left">트리거</th>
                <th className="px-3 py-2 text-right">진입가 (익일 시가)</th>
                <th className="px-3 py-2 text-right">현재가</th>
                <th className="px-3 py-2 text-right">수익률</th>
                <th className="px-3 py-2 text-right">피크</th>
                <th className="px-3 py-2 text-right">경과일</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => {
                const slug = `${e.market}-${e.ticker}`.toLowerCase();
                return (
                  <tr
                    key={`${e.signal_date}-${e.ticker}`}
                    className="border-b border-dashed border-border last:border-b-0"
                  >
                    <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                      {e.signal_date}
                    </td>
                    <td className="px-3 py-2">
                      <Link href={`/stocks/${slug}`} className="font-medium hover:underline">
                        {e.name}
                      </Link>{" "}
                      <span className="font-mono text-[10px] text-muted-foreground">
                        {e.ticker} · {e.market}
                      </span>
                    </td>
                    <td className="px-3 py-2 font-mono text-[10px] uppercase text-muted-foreground">
                      {e.trigger_schools.join(", ") || "—"}
                    </td>
                    <td className="tnum px-3 py-2 text-right font-mono text-xs">
                      {e.status === "pending" ? "대기" : fmtLocalPrice(e.entry_price, e.market)}
                    </td>
                    <td className="tnum px-3 py-2 text-right font-mono text-xs">
                      {fmtLocalPrice(e.current_price, e.market)}
                    </td>
                    <td
                      className={cn(
                        "tnum px-3 py-2 text-right font-mono text-xs font-bold",
                        signColor(e.return_pct),
                      )}
                    >
                      {fmtPctSafe(e.return_pct)}
                    </td>
                    <td className={cn("tnum px-3 py-2 text-right font-mono text-xs", signColor(e.peak_pct))}>
                      {fmtPctSafe(e.peak_pct)}
                    </td>
                    <td className="tnum px-3 py-2 text-right font-mono text-xs text-muted-foreground">
                      {e.days}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-2 font-mono text-[10px] leading-4 text-muted-foreground">
            신호 스냅샷 원본은{" "}
            <a href="/api/v1/signals/latest.json" className="underline">
              /api/v1/signals/latest.json
            </a>{" "}
            (일별 박제본: /api/v1/signals/{"{YYYY-MM-DD}"}.json, append-only) · 채점 데이터는{" "}
            <a href="/api/v1/forward.json" className="underline">
              /api/v1/forward.json
            </a>
            에서 그대로 받을 수 있습니다.
          </p>
        </div>
      )}
    </div>
  );
}
