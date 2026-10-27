import { timingSafeEqual } from "node:crypto";
import { z } from "zod";
import type { SotaPortfolio } from "./sota-portfolio";
import { LEDGER_COMMANDS, ledgerText } from "./portfolio-ledger";

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

export function help(role: "admin" | "member"): string {
  return ["SOTA 운용 원장", "", "/status: 평가와 보유 원장", "/holdings: 진입일, 수량, 비중, 평가손익",
    "/trades: 최근 매수와 매도 체결", "/signals: 매매 대기", "/help: 명령어",
    ...(role === "admin" ? ["", "관리자 전용", "/refresh: 보고서와 시세 갱신", "/chatid: 방 ID 확인"] : []),
    "", "텍스트는 /holdings text", "https://verdict-archive.vercel.app/portfolio"].join("\n");
}

export function renderCommand(command: string, doc: SotaPortfolio): string {
  const key = LEDGER_COMMANDS.find((key) => key === command);
  return key ? ledgerText(key, doc) : help("member");
}

export function splitTelegram(text: string): string[] {
  const chunks: string[] = [];
  let remaining = text;
  while (remaining) {
    const limit = 3500;
    if (remaining.length <= limit) { chunks.push(remaining); break; }
    // Keep complete rows together when possible.
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
