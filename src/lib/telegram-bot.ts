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
const outcome = (v: number) => v > 0 ? "🔴 이익" : v < 0 ? "🔵 손실" : "⚪ 보합";
const strategyName = (strategy: string) => strategy === "W_allweather_chandelier" ? "W 올웨더 샹들리에" : strategy;
const clean = (text: string) => text.replace(/[—·]/g, " ");

export function help(role: "admin" | "member"): string {
  return ["📖 명령어 안내", "",
    "📊 /status\nSOTA 요약과 시장 상태", "",
    "💼 /holdings\n보유 수량, 비중, 평가손익", "",
    "🎯 /signals\n매수 후보와 매도 근접 종목", "",
    "🧾 /trades\n최근 수익 실현과 손실 청산", "",
    "❔ /help\n명령어 안내",
    ...(role === "admin" ? ["", "🔐 관리자 전용", "", "🔄 /refresh\n보고서와 시세 갱신 후 백테스트 계산", "", "/chatid\n현재 방 ID 확인"] : []),
    "", "참여자는 조회만 가능합니다. 갱신은 관리자만 실행합니다.",
    "수량과 손익은 소수 주식 백테스트 모델 기준입니다."].join("\n");
}

export function renderCommand(command: string, doc: SotaPortfolio): string {
  return clean(renderCommandText(command, doc));
}

function renderCommandText(command: string, doc: SotaPortfolio): string {
  const title = `${strategyName(doc.strategy)}\n📅 ${doc.as_of} 종가 기준`;
  if (command === "/status") {
    const r = doc.regime;
    return ["📊 SOTA 현황", title, "", "🚦 시장 상태",
      r ? `KOSPI 200MA 레짐 ${r.state}\nKOSPI ${r.kospi_close.toLocaleString("en-US", { maximumFractionDigits: 2 })}\n200일선 ${r.kospi_ma200.toLocaleString("en-US", { maximumFractionDigits: 2 })}\n이격 ${pct((r.kospi_close / r.kospi_ma200 - 1) * 100)}` : "KOSPI 레짐 자료 없음",
      doc.strategy === "W_allweather_chandelier" ? "W는 OFF여도 매수 규칙과 올웨더 파킹을 유지합니다." : r?.note ?? "",
      "", "💰 모델 평가", `평가액 ${won(doc.nav_krw)}`, `백테스트 누적 ${pct(doc.return_pct)}`,
      `시뮬레이션 시작 ${doc.simulation_start}`, "", "💼 자산 구성",
      `보유 ${doc.positions.length}종목 / 최대 ${doc.slots.max_positions}종목`,
      `주식 ${doc.positions.reduce((s, p) => s + p.weight_pct, 0).toFixed(2)}%`,
      `${doc.parking_name} 파킹 ${doc.parked_weight_pct.toFixed(2)}%`, `현금 ${doc.cash_weight_pct.toFixed(2)}%`,
      "", "자세히 보기\n/holdings  /signals  /trades", "", "백테스트 모델이며 실계좌 잔고가 아닙니다."].join("\n");
  }
  if (command === "/holdings") return ["💼 보유 종목", title, `총 ${doc.positions.length}종목\n현재 비중이 큰 순서`, "",
    ...[...doc.positions].sort((a, b) => b.weight_pct - a.weight_pct).map((p, i) =>
      `${i + 1}. ${p.name} (${p.market}:${p.ticker})\n수량 ${shares(p.shares)}주\n비중 ${p.weight_pct.toFixed(2)}%\n${outcome(p.pnl_krw)} ${signed(p.pnl_krw)} (${pct(p.return_pct)})\n시세 ${p.quote_date}\n`),
    ...(doc.positions.length ? [] : ["보유 주식 없음", ""]), "🏦 파킹과 현금",
    `${doc.parking_name} ${won(doc.parked_krw)} (${doc.parked_weight_pct.toFixed(2)}%)`,
    `현금 ${won(doc.cash_krw)} (${doc.cash_weight_pct.toFixed(2)}%)`, "",
    "소수 주식 모델 수량입니다. 평가손익은 진입비용과 환율을 포함합니다."].join("\n");
  if (command === "/signals") {
    const sells = doc.positions.filter((p) => p.exit_pending || (p.dist_to_stop_pct !== null && p.dist_to_stop_pct <= 3))
      .sort((a, b) => Number(b.exit_pending) - Number(a.exit_pending) || (a.dist_to_stop_pct ?? Infinity) - (b.dist_to_stop_pct ?? Infinity));
    return ["🎯 매매 신호", title, "", `🚨 매도 확인 ${sells.length}종목`, "청산 대기 또는 스탑까지 3% 이내", "",
      ...sells.map((p) => `${p.exit_pending ? "⏳ 청산 대기" : "⚠️ 스탑 근접"}\n${p.name} (${p.market}:${p.ticker})\n현재가 ${quote(p.current_price, p.market)}\n스탑 ${quote(p.stop_level, p.market)}\n스탑까지 ${p.dist_to_stop_pct === null ? "미집계" : `${p.dist_to_stop_pct.toFixed(2)}%`}${p.exit_pending ? "\n다음 가용 시가에 청산 예정" : ""}\n`),
      ...(sells.length ? [] : ["해당 종목 없음", ""]), `🛒 매수 대기 ${doc.buy_candidates.length}종목`, `빈 슬롯 ${doc.slots.available}개`, "",
      ...doc.buy_candidates.map((b) => `${b.display_name} (${b.market}:${b.ticker})\n진입 ${b.entry_basis_date ?? "다음 가용 시가"}\n사유 ${b.entry_reason}\n`),
      doc.buy_candidates.length ? "빈 슬롯과 잔액을 확인한 뒤 진입합니다." : "현재 대기 후보 없음", "", "실제 주문이 아닌 모델 신호입니다."].join("\n");
  }
  if (command === "/trades") return ["🧾 최근 청산", title, `조회 기간 ${doc.recent_window_start} ~ ${doc.as_of}`, "",
    ...doc.recent_exits.map((t, i) => `${i + 1}. ${t.pnl_krw > 0 ? "🔴 수익 실현" : t.pnl_krw < 0 ? "🔵 손실 확정" : "⚪ 보합 청산"}\n${t.name} (${t.market}:${t.ticker})\n청산일 ${t.exit_date}\n손익 ${signed(t.pnl_krw)} (${pct(t.return_pct)})\n진입 ${quote(t.entry_price, t.market)} → 청산 ${quote(t.exit_price, t.market)}\n`),
    doc.recent_exits.length ? `총 ${doc.recent_closed_count}건 중 최신 ${doc.recent_exits.length}건` : "기간 내 청산 없음"].join("\n");
  return help("member");
}

export function splitTelegram(text: string): string[] {
  const chunks: string[] = [];
  let remaining = text;
  while (remaining) {
    const items = [...remaining.matchAll(/(?:^|\n\n)\d+\. /g)];
    const limit = Math.min(1400, items.length > 5 ? items[5].index + 2 : Infinity);
    if (remaining.length <= limit) { chunks.push(remaining); break; }
    // Keep each stock's paragraph together and leave room for the page label.
    let end = remaining.lastIndexOf("\n\n", limit - 2);
    end = end > 0 ? end + 2 : remaining.lastIndexOf("\n", limit - 1) + 1;
    if (end <= 0) end = limit;
    // Telegram counts UTF-16 units; never split a surrogate pair.
    if (/[\uD800-\uDBFF]/.test(remaining[end - 1])) end--;
    chunks.push(remaining.slice(0, end));
    remaining = remaining.slice(end);
  }
  return chunks;
}

const RunsSchema = z.object({ workflow_runs: z.array(z.object({
  id: z.number().int(), display_title: z.string(), status: z.string(), html_url: z.url(),
})) });

export async function requestRefresh(update: TelegramUpdate, config: BotConfig, request: GithubFetch = fetch): Promise<string> {
  if (!config.githubToken) return "⚠️ 갱신 연결 필요\n\n관리자의 갱신 실행 토큰이 아직 설정되지 않았습니다.";
  if (!/^[\w.-]+\/[\w.-]+$/.test(config.repository)) throw new Error("Invalid repository");
  const base = `https://api.github.com/repos/${config.repository}/actions/workflows/refresh-daily.yml`;
  const headers = { Authorization: `Bearer ${config.githubToken}`, Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json" };
  const existing = await request(`${base}/runs?per_page=100`, { headers, cache: "no-store", signal: AbortSignal.timeout(8000) });
  if (!existing.ok) throw new Error("GitHub workflow lookup failed");
  const runs = RunsSchema.parse(await existing.json()).workflow_runs;
  const prior = runs.find((r) => r.display_title === `refresh-daily · Telegram ${update.update_id}`);
  if (prior) return `📬 이미 접수된 요청\n\n같은 요청의 실행 내역을 확인하세요.\n${prior.html_url}`;
  const active = runs.find((r) => r.status !== "completed");
  if (active) return `🔄 이미 갱신 중\n\n현재 실행이 끝나면 새 결과를 확인할 수 있습니다.\n${active.html_url}`;
  const response = await request(`${base}/dispatches`, { method: "POST", headers,
    body: JSON.stringify({ ref: "main", inputs: { telegram_request_id: String(update.update_id) } }), signal: AbortSignal.timeout(8000) });
  if (!response.ok) throw new Error("GitHub workflow dispatch failed");
  return `📬 갱신 요청 접수\n\n1. 새 보고서와 시세 수집\n2. 백테스트 계산\n3. 새 결과 게시\n\n완료 또는 실패 시 알림을 보냅니다.\n\n실행 내역\nhttps://github.com/${config.repository}/actions/workflows/refresh-daily.yml`;
}
