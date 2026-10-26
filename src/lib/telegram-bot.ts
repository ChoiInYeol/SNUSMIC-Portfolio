import { timingSafeEqual } from "node:crypto";
import { z } from "zod";
import type { SotaPortfolio } from "./sota-portfolio";

const MessageSchema = z.object({
  message_id: z.number().int(), date: z.number().int(), text: z.string().optional(),
  chat: z.object({ id: z.number().int(), type: z.string() }),
  from: z.object({ id: z.number().int(), is_bot: z.boolean() }).optional(),
  forward_origin: z.unknown().optional(),
});
export const TelegramUpdateSchema = z.object({ update_id: z.number().int().nonnegative(), message: MessageSchema.optional() });
export type TelegramUpdate = z.infer<typeof TelegramUpdateSchema>;
export type BotConfig = { secret: string; chatId: string; userId: string; allowedChatIds?: string[]; githubToken?: string; repository: string };
type GithubFetch = typeof fetch;

export function secretMatches(actual: string, supplied: string | null): boolean {
  if (!actual || !supplied) return false;
  const a = Buffer.from(actual), b = Buffer.from(supplied);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function roleFor(update: TelegramUpdate, config: BotConfig): "admin" | "member" | null {
  const m = update.message;
  if (!m?.from || m.from.is_bot || m.forward_origin) return null;
  const owner = config.userId || (m.chat.type === "private" ? config.chatId : "");
  const allowed = new Set([config.chatId, ...(config.allowedChatIds ?? []), owner]);
  if (!allowed.has(String(m.chat.id))) return null;
  if (owner !== "" && String(m.from.id) === owner) return "admin";
  return ["group", "supergroup"].includes(m.chat.type) ? "member" : null;
}

const won = (v: number) => `${Math.round(v).toLocaleString("en-US")}원`;
const signed = (v: number) => `${v >= 0 ? "+" : ""}${won(v)}`;
const pct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
const shares = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 4 });
const quote = (v: number | null, market: string) => v === null ? "미집계" : market === "US" ? `$${v.toFixed(2)}` : won(v);

export function help(role: "admin" | "member"): string {
  return ["명령어 · 참여자는 조회 전용", "/status — 최신 SOTA·레짐·평가 기준일", "/holdings — 보유 주수·비중·평가손익",
    "/signals — 매수 대기·스탑 근접", "/trades — 최근 익절·손절", "/help — 도움말",
    ...(role === "admin" ? ["관리자 전용", "/refresh — 새 보고서·시세 수집과 백테스트 갱신", "/chatid — 현재 방 ID 확인"] : []),
    "조회는 저장된 종가 결과로 즉시 응답합니다. 데이터 갱신은 관리자만 실행합니다.",
    "수량·손익은 소수 주식 백테스트 모델 기준입니다."].join("\n");
}

export function renderCommand(command: string, doc: SotaPortfolio): string {
  const title = `SOTA ${doc.strategy}\n평가 기준일 ${doc.as_of} (종가)\n`;
  if (command === "/status") {
    const r = doc.regime;
    return [title, r ? `KOSPI 200MA 레짐 ${r.state} · 이격 ${pct((r.kospi_close / r.kospi_ma200 - 1) * 100)}` : "KOSPI 레짐 자료 없음",
      r ? `KOSPI ${r.kospi_close.toFixed(2)} / 200일선 ${r.kospi_ma200.toFixed(2)}` : "",
      doc.strategy === "W_allweather_chandelier" ? "W는 OFF여도 매수 규칙·올웨더 파킹 유지" : r?.note ?? "",
      `모델 평가액 ${won(doc.nav_krw)} · 백테스트 누적 ${pct(doc.return_pct)}`,
      `보유 ${doc.positions.length}종목 / 최대 ${doc.slots.max_positions}`,
      `주식 ${doc.positions.reduce((s, p) => s + p.weight_pct, 0).toFixed(2)}% · 파킹 ${doc.parked_weight_pct.toFixed(2)}% · 현금 ${doc.cash_weight_pct.toFixed(2)}%`,
      "", "/holdings /signals /trades /refresh", "백테스트 모델이며 실계좌 잔고가 아닙니다."].filter(Boolean).join("\n");
  }
  if (command === "/holdings") return [title, "보유 수량·현재 비중·평가손익",
    ...doc.positions.map((p) => `· ${p.name} (${p.market}:${p.ticker}) ${shares(p.shares)}주 · ${p.weight_pct.toFixed(2)}%\n  ${signed(p.pnl_krw)} (${pct(p.return_pct)}) · 시세 ${p.quote_date}`),
    doc.positions.length ? "" : "보유 주식 없음", `${doc.parking_name} ${won(doc.parked_krw)} · 현금 ${won(doc.cash_krw)}`,
    "시작자금 기준 소수 주식 모델. 평가손익은 진입비용·환율 포함."].join("\n");
  if (command === "/signals") {
    const sells = doc.positions.filter((p) => p.exit_pending || (p.dist_to_stop_pct !== null && p.dist_to_stop_pct <= 3));
    return [title, "매도 대기·스탑 3% 이내",
      ...sells.map((p) => `· ${p.name} (${p.ticker}) ${p.exit_pending ? "다음 가용 시가 청산 대기" : "스탑 근접"}\n  현재 ${quote(p.current_price, p.market)} / 스탑 ${quote(p.stop_level, p.market)} · 거리 ${p.dist_to_stop_pct?.toFixed(2) ?? "미집계"}%`),
      sells.length ? "" : "· 없음", `매수 대기 후보 · 빈 슬롯 ${doc.slots.available}`,
      ...doc.buy_candidates.map((b) => `· ${b.display_name} (${b.ticker}) ${b.entry_basis_date ?? "다음 가용 시가"}\n  ${b.entry_reason}`),
      doc.buy_candidates.length ? "후보는 슬롯·잔액 제한을 적용한 뒤 진입합니다." : "· 없음", "실제 주문이 아닌 모델 신호입니다."].join("\n");
  }
  if (command === "/trades") return [title, `최근 익절·손절 (${doc.recent_window_start}~${doc.as_of})`,
    ...doc.recent_exits.map((t) => `· ${t.exit_date} ${t.name} (${t.ticker}) ${t.pnl_krw >= 0 ? "수익 실현" : "손실 확정"}\n  ${signed(t.pnl_krw)} (${pct(t.return_pct)}) · ${quote(t.entry_price, t.market)} → ${quote(t.exit_price, t.market)}`),
    doc.recent_exits.length ? `총 ${doc.recent_closed_count}건 중 최신 ${doc.recent_exits.length}건` : "· 청산 없음"].join("\n");
  return help("member");
}

export function splitTelegram(text: string): string[] {
  const chunks: string[] = [];
  let current = "";
  for (const char of text) { // Telegram counts UTF-16 units; never split a surrogate pair.
    if (current.length + char.length > 3500) { chunks.push(current); current = ""; }
    current += char;
  }
  if (current) chunks.push(current);
  return chunks;
}

const RunsSchema = z.object({ workflow_runs: z.array(z.object({
  id: z.number().int(), display_title: z.string(), status: z.string(), html_url: z.url(),
})) });

export async function requestRefresh(update: TelegramUpdate, config: BotConfig, request: GithubFetch = fetch): Promise<string> {
  if (!config.githubToken) return "갱신 실행 토큰이 아직 설정되지 않았습니다. TELEGRAM_ACTIONS_TOKEN 연결이 필요합니다.";
  if (!/^[\w.-]+\/[\w.-]+$/.test(config.repository)) throw new Error("Invalid repository");
  const base = `https://api.github.com/repos/${config.repository}/actions/workflows/refresh-daily.yml`;
  const headers = { Authorization: `Bearer ${config.githubToken}`, Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json" };
  const existing = await request(`${base}/runs?per_page=100`, { headers, cache: "no-store", signal: AbortSignal.timeout(8000) });
  if (!existing.ok) throw new Error("GitHub workflow lookup failed");
  const runs = RunsSchema.parse(await existing.json()).workflow_runs;
  const prior = runs.find((r) => r.display_title === `refresh-daily · Telegram ${update.update_id}`);
  if (prior) return `이미 접수된 갱신 요청입니다.\n${prior.html_url}`;
  const active = runs.find((r) => r.status !== "completed");
  if (active) return `이미 데이터 갱신이 진행 중입니다. 중복 실행하지 않습니다.\n${active.html_url}`;
  const response = await request(`${base}/dispatches`, { method: "POST", headers,
    body: JSON.stringify({ ref: "main", inputs: { telegram_request_id: String(update.update_id) } }), signal: AbortSignal.timeout(8000) });
  if (!response.ok) throw new Error("GitHub workflow dispatch failed");
  return `갱신 요청을 접수했습니다. 새 보고서·시세 수집 후 백테스트를 계산합니다. 완료·실패 결과를 알려드리겠습니다.\nhttps://github.com/${config.repository}/actions/workflows/refresh-daily.yml`;
}
