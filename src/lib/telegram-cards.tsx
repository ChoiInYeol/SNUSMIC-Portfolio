import { readFile } from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { ImageResponse } from "next/og";
import type { CSSProperties, ReactNode } from "react";
import type { SotaPortfolio } from "./sota-portfolio";

export const CARD_COMMANDS = ["/status", "/holdings", "/signals", "/trades"] as const;
export type CardCommand = typeof CARD_COMMANDS[number];
export type CardRow = { name: string; detail: string; value: string; subvalue: string; tone: number; tag?: string; footRight?: string };
export type CardSpec = { command: CardCommand; title: string; subtitle: string; rows: CardRow[]; page: number; pages: number; height: number };
export type CardImage = { png: Uint8Array; title: string; page: number; pages: number };
type Theme = Record<"paper" | "ink" | "surface" | "muted" | "border" | "up" | "down" | "upBg" | "downBg" | "warn" | "inset", string>;

const clean = (s: string) => s.replace(/[—·]/g, " ");
const number = (v: number, digits = 0) => v.toLocaleString("en-US", { maximumFractionDigits: digits });
const won = (v: number) => `${number(Math.round(v))}원`;
const signedWon = (v: number) => `${v >= 0 ? "+" : ""}${won(v)}`;
const pct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
const price = (v: number | null, market: string) => v === null ? "미집계" : market === "US" ? `$${number(v, 2)}` : won(v);
const name = (s: string) => clean(s || "이름 없음");
const strategy = (doc: SotaPortfolio) => doc.strategy === "W_allweather_chandelier" ? "W 올웨더 샹들리에" : clean(doc.strategy);
export const cardToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(new Date());

export function cardCaption(command: CardCommand, doc: SotaPortfolio, today = cardToday()): string {
  const titles = { "/status": "SOTA 현황", "/holdings": "보유 포트폴리오", "/signals": "매매 신호", "/trades": "최근 청산" };
  const stale = (Date.parse(today) - Date.parse(doc.as_of)) / 86_400_000 > 3;
  return `${titles[command]}\n${strategy(doc)}\n평가 ${doc.as_of} 종가${stale ? "\n⚠️ 저장된 평가일을 확인하세요." : ""}\n\n백테스트 모델입니다.\n텍스트 보기: ${command} text`;
}

export function buildCardSpecs(command: CardCommand, doc: SotaPortfolio): CardSpec[] {
  let rows: CardRow[] = [], title = "", subtitle = "", pageSize = 6;
  if (command === "/status") return [{ command, title: "SOTA 현황", subtitle: "시장과 포트폴리오를 한눈에", rows: [], page: 1, pages: 1, height: 1260 }];
  if (command === "/holdings") {
    title = "보유 포트폴리오"; subtitle = `${doc.positions.length}종목  /  현재 비중이 큰 순서`;
    rows = [...doc.positions].sort((a, b) => b.weight_pct - a.weight_pct).map((p) => ({
      name: name(p.name), detail: `${number(p.shares, 4)}주`,
      value: `${p.weight_pct.toFixed(2)}%`, subvalue: signedWon(p.pnl_krw), tone: p.pnl_krw,
      tag: `${p.market} ${p.ticker}  /  시세 ${p.quote_date}`, footRight: pct(p.return_pct),
    }));
  } else if (command === "/signals") {
    title = "매매 신호"; pageSize = 6;
    const sells = doc.positions.filter((p) => p.exit_pending || (p.dist_to_stop_pct !== null && p.dist_to_stop_pct <= 3))
      .sort((a, b) => Number(b.exit_pending) - Number(a.exit_pending) || (a.dist_to_stop_pct ?? Infinity) - (b.dist_to_stop_pct ?? Infinity));
    subtitle = `매도 확인 ${sells.length}종목  /  매수 대기 ${doc.buy_candidates.length}종목  /  빈 슬롯 ${doc.slots.available}개`;
    rows = [...sells.map((p): CardRow => ({
      name: name(p.name), tag: p.exit_pending ? "청산 대기" : "스탑 근접", tone: -1,
      detail: `${p.market} ${p.ticker}  /  ${p.exit_pending ? "다음 가용 시가에 청산" : "스탑까지 3% 이내"}`,
      value: price(p.current_price, p.market),
      subvalue: `스탑 ${price(p.stop_level, p.market)}  /  ${p.dist_to_stop_pct === null ? "거리 미집계" : p.dist_to_stop_pct < 0 ? `${Math.abs(p.dist_to_stop_pct).toFixed(2)}% 하회` : `${p.dist_to_stop_pct.toFixed(2)}% 위`}`,
    })), ...doc.buy_candidates.map((b): CardRow => ({
      name: name(b.display_name), tag: "매수 대기", tone: 1, value: "다음 시가 후보",
      detail: `${b.market} ${b.ticker}`, subvalue: `진입 기준 ${b.entry_basis_date ?? "다음 가용 시가"}`,
    }))];
  } else {
    title = "최근 청산"; pageSize = 6; subtitle = `${doc.recent_window_start} ~ ${doc.as_of}  /  총 ${doc.recent_closed_count}건 중 최신 ${doc.recent_exits.length}건`;
    rows = doc.recent_exits.map((t) => ({
      name: name(t.name), detail: `${t.market} ${t.ticker}  /  청산 ${t.exit_date}`,
      tag: t.pnl_krw > 0 ? "이익 청산" : t.pnl_krw < 0 ? "손실 청산" : "보합 청산", tone: t.pnl_krw,
      value: pct(t.return_pct), subvalue: `${signedWon(t.pnl_krw)}  /  ${price(t.entry_price, t.market)} → ${price(t.exit_price, t.market)}`,
    }));
  }
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  return Array.from({ length: pages }, (_, i) => ({ command, title, subtitle, page: i + 1, pages,
    rows: rows.slice(i * pageSize, (i + 1) * pageSize),
    height: Math.max(command === "/holdings" ? 900 : 700, (command === "/holdings" ? 590 : command === "/signals" ? 520 : 420) + Math.min(pageSize, Math.max(1, rows.length - i * pageSize)) * 148),
  }));
}

// Raster images cannot consume browser CSS variables or Radix/Tailwind components.
// Resolve the site's actual light-theme tokens and reuse the installed Korean font.
let resources: Promise<{ theme: Theme; fonts: { name: string; data: ArrayBuffer; weight: 400 | 700; style: "normal" }[] }> | undefined;
function loadResources() {
  resources ??= (async () => {
    const [css, regular, bold] = await Promise.all([
      readFile(path.join(process.cwd(), "src/app/globals.css"), "utf8"),
      readFile(path.join(process.cwd(), "node_modules/pretendard/dist/public/static/Pretendard-Regular.otf")),
      readFile(path.join(process.cwd(), "node_modules/pretendard/dist/public/static/Pretendard-Bold.otf")),
    ]);
    const light = css.match(/\.light\s*\{([^}]+)\}/)?.[1];
    if (!light) throw new Error("Image theme missing");
    const token = (key: string) => {
      const value = light.match(new RegExp(`--${key}:\\s*([\\d.]+)\\s+([\\d.]+)%\\s+([\\d.]+)%`));
      if (!value) throw new Error("Image theme token missing");
      return `hsl(${value[1]}, ${value[2]}%, ${value[3]}%)`;
    };
    const theme: Theme = { paper: token("background"), ink: token("foreground"), surface: token("card"), muted: token("muted-foreground"),
      border: token("border"), up: token("up"), down: token("down"), upBg: token("up-bg"), downBg: token("down-bg"), warn: token("warn"), inset: token("surface-inset") };
    return { theme, fonts: [{ name: "Pretendard", data: Uint8Array.from(regular).buffer, weight: 400 as const, style: "normal" as const },
      { name: "Pretendard", data: Uint8Array.from(bold).buffer, weight: 700 as const, style: "normal" as const }] };
  })().catch((error) => { resources = undefined; throw error; });
  return resources;
}

const col = (style: CSSProperties = {}): CSSProperties => ({ display: "flex", flexDirection: "column", ...style });
const row = (style: CSSProperties = {}): CSSProperties => ({ display: "flex", alignItems: "center", ...style });
const text = (style: CSSProperties = {}): CSSProperties => ({ display: "flex", ...style });
const tone = (t: Theme, value: number) => value > 0 ? t.up : value < 0 ? t.down : t.muted;
function Metric({ label, value, detail, color, theme }: { label: string; value: string; detail?: string; color?: string; theme: Theme }) {
  return <div style={col({ flex: 1, padding: 28, background: theme.surface, borderRadius: 16, gap: 12 })}>
    <div style={text({ color: theme.muted, fontSize: 25 })}>{label}</div>
    <div style={text({ fontSize: 38, fontWeight: 700, color: color ?? theme.ink })}>{value}</div>
    {detail ? <div style={text({ color: theme.muted, fontSize: 22 })}>{detail}</div> : null}
  </div>;
}

function Status({ doc, theme: t }: { doc: SotaPortfolio; theme: Theme }) {
  const r = doc.regime, stocks = doc.positions.reduce((sum, p) => sum + p.weight_pct, 0);
  const allocations = [{ label: "주식", weight: stocks, color: t.ink }, { label: "올웨더 파킹", weight: doc.parked_weight_pct, color: t.warn }, { label: "현금", weight: doc.cash_weight_pct, color: t.border }];
  return <div style={col({ gap: 24 })}>
    <div style={col({ padding: 32, background: t.ink, color: t.surface, borderRadius: 18, gap: 14 })}>
      <div style={text({ fontSize: 25, opacity: .8 })}>모델 평가액</div>
      <div style={text({ fontSize: 64, fontWeight: 700, letterSpacing: -2 })}>{won(doc.nav_krw)}</div>
      <div style={row({ justifyContent: "space-between", fontSize: 25 })}>
        <div style={text()}>누적 수익률 {pct(doc.return_pct)}</div><div style={text()}>시작 {doc.simulation_start}</div>
      </div>
    </div>
    <div style={row({ gap: 18 })}>
      <Metric theme={t} label="KOSPI 200MA 레짐" value={r?.state ?? "미집계"} detail={r ? `200일선 대비 ${pct((r.kospi_close / r.kospi_ma200 - 1) * 100)}` : "시장 상태 자료 없음"} color={r?.state === "OFF" ? t.down : t.up} />
      <Metric theme={t} label="보유 종목" value={`${doc.positions.length} / ${doc.slots.max_positions}`} detail={`빈 슬롯 ${doc.slots.available}개`} />
    </div>
    <div style={col({ padding: 28, border: `1px solid ${t.border}`, borderRadius: 16, gap: 20 })}>
      <div style={text({ fontSize: 28, fontWeight: 700 })}>자산 구성</div>
      <div style={row({ height: 24, borderRadius: 8, overflow: "hidden", background: t.inset })}>
        {allocations.filter((a) => a.weight > 0).map((a) => <div key={a.label} style={text({ width: `${a.weight}%`, height: 24, background: a.color })} />)}
      </div>
      <div style={row({ justifyContent: "space-between" })}>{allocations.map((a) => <div key={a.label} style={col({ gap: 8 })}>
        <div style={text({ color: t.muted, fontSize: 23 })}>{a.label}</div><div style={text({ fontSize: 34, fontWeight: 700 })}>{a.weight.toFixed(2)}%</div>
      </div>)}</div>
    </div>
    <div style={row({ gap: 18 })}>
      <Metric theme={t} label="평가이익" value={`${doc.positions.filter((p) => p.pnl_krw > 0).length}종목`} color={t.up} />
      <Metric theme={t} label="평가손실" value={`${doc.positions.filter((p) => p.pnl_krw < 0).length}종목`} color={t.down} />
    </div>
    <div style={text({ fontSize: 24, color: t.muted })}>{doc.strategy === "W_allweather_chandelier" ? "W는 레짐 OFF여도 매수 규칙과 올웨더 파킹을 유지합니다." : clean(r?.note ?? "저장된 종가 기준 모델입니다.")}</div>
  </div>;
}

function Card({ spec, doc, theme: t, today }: { spec: CardSpec; doc: SotaPortfolio; theme: Theme; today: string }) {
  const stale = (Date.parse(today) - Date.parse(doc.as_of)) / 86_400_000 > 3;
  return <div style={col({ width: 960, height: spec.height, background: t.paper, color: t.ink, padding: "40px 44px 32px", fontFamily: "Pretendard", fontVariantNumeric: "tabular-nums" })}>
    <div style={row({ justifyContent: "space-between", marginBottom: 26, fontSize: 20, letterSpacing: 3 })}>
      <div style={text({ fontWeight: 700 })}>VERDICT ARCHIVE</div><div style={text({ color: t.muted, letterSpacing: 0 })}>{spec.page} / {spec.pages}</div>
    </div>
    <div style={row({ justifyContent: "space-between", alignItems: "flex-end", paddingBottom: 22, borderBottom: `2px solid ${t.ink}` })}>
      <div style={col({ gap: 10 })}><div style={text({ fontSize: 46, fontWeight: 700, letterSpacing: -1.5 })}>{spec.title}</div><div style={text({ fontSize: 23, color: t.muted })}>{strategy(doc)}</div></div>
      <div style={col({ alignItems: "flex-end", gap: 9 })}><div style={text({ fontSize: 27, fontWeight: 700 })}>{doc.as_of}</div><div style={text({ fontSize: 22, color: stale ? t.warn : t.muted })}>{stale ? "저장된 평가일 확인" : "종가 기준"}</div></div>
    </div>
    <div style={col({ paddingTop: 24, flex: 1, gap: 20 })}>
      {spec.command === "/status" ? <Status doc={doc} theme={t} /> : <div style={col({ gap: 20, width: "100%" })}>
        <div style={text({ fontSize: 23, color: t.muted })}>{spec.subtitle}</div>
        {spec.command === "/holdings" ? <div style={row({ fontSize: 22, color: t.muted, justifyContent: "space-between", padding: "0 16px" })}><div style={text()}>종목 / 보유 수량</div><div style={text()}>현재 비중 / 평가손익</div></div> : null}
        <div style={col({ background: t.surface, borderRadius: 16, overflow: "hidden" })}>
          {spec.rows.length ? spec.rows.map((r, i) => <div key={`${i}-${r.name}`} style={col({ height: 148, padding: "19px 22px", gap: 10, ...(i < spec.rows.length - 1 ? { borderBottom: `1px solid ${t.border}` } : {}) })}>
            <div style={row({ justifyContent: "space-between", gap: 16 })}>
              <div style={text({ fontWeight: 700, fontSize: 34, maxWidth: 480, overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis" })}>{r.name}</div>
              <div style={text({ fontWeight: 700, fontSize: 34, color: spec.command === "/holdings" || spec.command === "/signals" ? t.ink : tone(t, r.tone) })}>{r.value}</div>
            </div>
            <div style={row({ justifyContent: "space-between", fontSize: spec.command === "/holdings" ? 28 : 22, gap: 16 })}>
              <div style={text({ color: t.muted })}>{r.detail}</div>
              <div style={text({ color: spec.command === "/holdings" ? tone(t, r.tone) : t.muted, fontWeight: spec.command === "/holdings" ? 700 : 400 })}>{r.subvalue}</div>
            </div>
            <div style={row({ justifyContent: "space-between", fontSize: 20, color: t.muted })}><div style={text(spec.command === "/signals" ? { color: tone(t, r.tone), background: r.tone > 0 ? t.upBg : t.downBg, padding: "3px 10px", borderRadius: 5, fontWeight: 700 } : {})}>{r.tag}</div>{r.footRight ? <div style={text({ fontSize: 25, fontWeight: 700, color: tone(t, r.tone) })}>{r.footRight}</div> : null}</div>
          </div>) : <div style={col({ padding: 50, gap: 14 })}><div style={text({ fontSize: 32, fontWeight: 700 })}>{spec.command === "/holdings" ? "현재 보유 주식 없음" : spec.command === "/trades" ? "기간 내 청산 없음" : "현재 매매 대기 없음"}</div><div style={text({ fontSize: 24, color: t.muted })}>평가 기준일에 해당하는 저장 결과입니다.</div></div>}
        </div>
        {spec.command === "/holdings" ? <div style={row({ gap: 18 })}><Metric theme={t} label={`${doc.parking_name} 파킹`} value={`${doc.parked_weight_pct.toFixed(2)}%`} /><Metric theme={t} label="현금" value={`${doc.cash_weight_pct.toFixed(2)}%`} /></div> : null}
        {spec.command === "/signals" ? <div style={col({ gap: 10, padding: 24, background: t.inset, borderRadius: 14, fontSize: 24 })}>
          <div style={text()}>{doc.buy_candidates.length ? "매수 후보는 빈 슬롯과 잔액을 확인한 뒤 진입합니다." : "현재 신규 매수 대기 후보가 없습니다."}</div><div style={text({ color: t.muted })}>청산 대기는 스탑 신호 후 다음 가용 시가 기준입니다.</div>
        </div> : null}
      </div>}
    </div>
    <div style={row({ justifyContent: "space-between", fontSize: 19, color: t.muted, paddingTop: 22, marginTop: 18, borderTop: `1px solid ${t.border}` })}>
      <div style={text()}>{spec.command === "/holdings" ? "소수 주식 모델 수량  /  손익은 비용과 환율 포함" : "백테스트 모델  /  실계좌 주문과 잔고가 아닙니다"}</div><div style={text()}>verdict-archive.vercel.app</div>
    </div>
  </div>;
}

// Bounded process-local cache: no per-command files or deployment history are created.
const cache = new Map<string, Promise<CardImage[]>>();
export function renderTelegramCards(command: CardCommand, doc: SotaPortfolio, today = cardToday()): Promise<CardImage[]> {
  const key = createHash("sha256").update(JSON.stringify([command, doc, today])).digest("hex");
  const existing = cache.get(key);
  if (existing) return existing;
  const task = (async () => {
    const { theme, fonts } = await loadResources();
    const specs = buildCardSpecs(command, doc), result: CardImage[] = [];
    // Bound concurrent raster work to two pages per request.
    for (let start = 0; start < specs.length; start += 2) {
      result.push(...await Promise.all(specs.slice(start, start + 2).map(async (spec) => {
        const response = new ImageResponse(<Card spec={spec} doc={doc} theme={theme} today={today} />, { width: 960, height: spec.height, fonts });
        const png = new Uint8Array(await response.arrayBuffer());
        if (png.length > 10 * 1024 * 1024) throw new Error("Telegram image is too large");
        return { png, title: spec.title, page: spec.page, pages: spec.pages };
      })));
    }
    return result;
  })().catch((error) => { cache.delete(key); throw error; });
  if (cache.size >= 8) cache.delete(cache.keys().next().value!);
  cache.set(key, task);
  return task;
}
