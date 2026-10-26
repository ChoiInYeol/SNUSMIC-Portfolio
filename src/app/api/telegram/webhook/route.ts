import { NextResponse } from "next/server";
import rawBook from "../../../../../data/gold/portfolio/sota.json";
import { SotaPortfolioSchema } from "@/lib/sota-portfolio";
import { help, roleFor, renderCommand, requestRefresh, secretMatches, splitTelegram, TelegramUpdateSchema } from "@/lib/telegram-bot";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 60;
const book = SotaPortfolioSchema.parse(rawBook);
const headers = { "Cache-Control": "no-store" };

export async function POST(request: Request) {
  const config = { secret: process.env.TELEGRAM_WEBHOOK_SECRET ?? "", chatId: process.env.TELEGRAM_CHAT_ID ?? "",
    userId: process.env.TELEGRAM_ALLOWED_USER_ID ?? "",
    allowedChatIds: (process.env.TELEGRAM_ALLOWED_CHAT_IDS ?? "").split(",").map((id) => id.trim()).filter((id) => /^-?\d+$/.test(id)),
    githubToken: process.env.TELEGRAM_REFRESH_ENABLED === "true" ? process.env.TELEGRAM_ACTIONS_TOKEN : undefined,
    repository: process.env.TELEGRAM_GITHUB_REPOSITORY ?? "ChoiInYeol/SNUSMIC-Portfolio" };
  if (!config.secret || !config.chatId || !process.env.TELEGRAM_BOT_TOKEN) return NextResponse.json({ error: "Bot unavailable" }, { status: 503, headers });
  if (!secretMatches(config.secret, request.headers.get("x-telegram-bot-api-secret-token"))) return NextResponse.json({ error: "Unauthorized" }, { status: 401, headers });
  // Telegram text updates are small. Reject oversized/untrusted payloads before parsing them.
  const body = await request.text();
  if (body.length > 32_000) return NextResponse.json({ error: "Payload too large" }, { status: 413, headers });
  let raw: unknown;
  try { raw = JSON.parse(body); } catch { return NextResponse.json({ error: "Invalid JSON" }, { status: 400, headers }); }
  const parsed = TelegramUpdateSchema.safeParse(raw);
  if (!parsed.success) return NextResponse.json({ ok: true }, { headers });
  const update = parsed.data;
  if (!update.message?.text?.startsWith("/")) return NextResponse.json({ ok: true }, { headers });
  const command = update.message.text.trim().split(/\s+/, 1)[0].split("@", 1)[0].toLowerCase();
  const role = roleFor(update, config);
  // An administrator can discover a new group's id before explicitly adding it to the allowlist.
  const discoveringRoom = command === "/chatid" && config.userId !== "" &&
    String(update.message.from?.id) === config.userId && !update.message.from?.is_bot && !update.message.forward_origin;
  if (!role && !discoveringRoom) return NextResponse.json({ ok: true }, { headers });
  let text: string;
  try {
    if (discoveringRoom) {
      text = `현재 방 ID: ${update.message.chat.id}\n관리자 사용자 ID: ${update.message.from?.id}\n공유 방 연결: 이 방 ID를 TELEGRAM_ALLOWED_CHAT_IDS에 등록한 뒤 webhook setup을 다시 실행하세요. 참여자는 조회만 할 수 있습니다.`;
    } else if (command === "/refresh" && role !== "admin") {
      text = "데이터 갱신은 관리자 전용입니다. /status /holdings /signals /trades로 최신 저장 결과를 조회할 수 있습니다.";
    } else if (command === "/refresh") {
      const age = Date.now() / 1000 - update.message.date;
      text = age > 600 || age < -60 ? "오래된 갱신 요청입니다. /refresh를 다시 보내 주세요." : await requestRefresh(update, config);
    } else {
      text = ["/status", "/holdings", "/signals", "/trades"].includes(command) ? renderCommand(command, book) : help(role ?? "admin");
      if (["/status", "/holdings", "/signals", "/trades"].includes(command)) {
        const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(new Date());
        if ((Date.parse(today) - Date.parse(book.as_of)) / 86_400_000 > 3) text = `⚠ 저장된 평가 기준일 ${book.as_of} — 휴장·갱신 상태를 확인하세요.\n\n${text}`;
      }
    }
  } catch {
    // A dispatch timeout is ambiguous: it may have reached GitHub. Do not automatically dispatch again.
    text = "요청 처리 상태를 확인하지 못했습니다. Actions 실행 목록을 확인해 주세요.\nhttps://github.com/ChoiInYeol/SNUSMIC-Portfolio/actions/workflows/refresh-daily.yml";
  }
  try {
    for (const part of splitTelegram(text)) {
      const response = await fetch(`https://api.telegram.org/bot${process.env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: AbortSignal.timeout(8000),
        body: JSON.stringify({ chat_id: String(update.message.chat.id), text: part, disable_web_page_preview: true }),
      });
      if (!response.ok) throw new Error("Telegram delivery failed");
      const result: unknown = await response.json();
      if (typeof result !== "object" || result === null || !("ok" in result) || result.ok !== true) throw new Error("Telegram delivery failed");
    }
    return NextResponse.json({ ok: true }, { headers });
  } catch {
    // Avoid exposing token-bearing fetch errors. Telegram can retry delivery; /refresh is deduped above.
    return NextResponse.json({ error: "Reply unavailable" }, { status: 503, headers });
  }
}
