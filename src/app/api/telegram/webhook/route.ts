import { NextResponse } from "next/server";
import rawBook from "../../../../../data/gold/portfolio/sota.json";
import { SotaPortfolioSchema } from "@/lib/sota-portfolio";
import { help, roleFor, renderCommand, requestRefresh, secretMatches, splitTelegram, TelegramUpdateSchema } from "@/lib/telegram-bot";
import type { CardCommand, CardImage } from "@/lib/telegram-cards";

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
  const words = update.message.text.trim().split(/\s+/);
  const command = words[0].split("@", 1)[0].toLowerCase();
  const textOnly = ["text", "텍스트"].includes(words[1]?.toLowerCase());
  const role = roleFor(update, config);
  // An administrator can discover a new group's id before explicitly adding it to the allowlist.
  const discoveringRoom = command === "/chatid" && config.userId !== "" &&
    String(update.message.from?.id) === config.userId && !update.message.from?.is_bot && !update.message.forward_origin;
  if (!role && !discoveringRoom) return NextResponse.json({ ok: true }, { headers });
  let text = "";
  let picture: { caption: string; images: CardImage[] } | undefined;
  try {
    if (discoveringRoom) {
      text = `🔐 공유 방 연결 정보\n\n현재 방 ID\n${update.message.chat.id}\n\n관리자 사용자 ID\n${update.message.from?.id}\n\n이 방 ID를 TELEGRAM_ALLOWED_CHAT_IDS에 등록한 뒤 webhook setup을 다시 실행하세요.\n참여자는 조회만 할 수 있습니다.`;
    } else if (command === "/refresh" && role !== "admin") {
      text = "🔐 관리자 전용\n\n데이터 갱신은 관리자만 실행할 수 있습니다.\n\n📊 /status\n💼 /holdings\n🎯 /signals\n🧾 /trades\n\n위 명령으로 최신 저장 결과를 조회하세요.";
    } else if (command === "/refresh") {
      const age = Date.now() / 1000 - update.message.date;
      text = age > 600 || age < -60 ? "오래된 갱신 요청입니다. /refresh를 다시 보내 주세요." : await requestRefresh(update, config);
    } else {
      const query = ["/status", "/holdings", "/signals", "/trades"].includes(command);
      if (query && !textOnly) {
        try {
          const cards = await import("@/lib/telegram-cards");
          const key = command as CardCommand;
          picture = { caption: cards.cardCaption(key, book), images: await cards.renderTelegramCards(key, book) };
        } catch {
          text = `이미지를 불러오지 못해 텍스트로 표시합니다.\n\n${renderCommand(command, book)}`;
        }
      } else {
        text = query ? renderCommand(command, book) : help(role ?? "admin");
      }
      if (query && !picture) {
        const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(new Date());
        if ((Date.parse(today) - Date.parse(book.as_of)) / 86_400_000 > 3) text = `⚠️ 평가일 확인\n저장된 기준일 ${book.as_of}\n휴장 또는 갱신 지연 여부를 확인하세요.\n\n${text}`;
      }
    }
  } catch {
    // A dispatch timeout is ambiguous: it may have reached GitHub. Do not automatically dispatch again.
    text = "⚠️ 처리 상태 확인 필요\n\n요청의 처리 상태를 확인하지 못했습니다.\n아래 실행 목록을 확인해 주세요.\n\nhttps://github.com/ChoiInYeol/SNUSMIC-Portfolio/actions/workflows/refresh-daily.yml";
  }
  try {
    const send = async (method: string, body: FormData | string) => {
      const response = await fetch(`https://api.telegram.org/bot${process.env.TELEGRAM_BOT_TOKEN}/${method}`, {
        method: "POST", headers: typeof body === "string" ? { "Content-Type": "application/json" } : undefined,
        signal: AbortSignal.timeout(15000), body,
      });
      if (!response.ok) throw new Error("Telegram delivery failed");
      const result: unknown = await response.json();
      if (typeof result !== "object" || result === null || !("ok" in result) || result.ok !== true) throw new Error("Telegram delivery failed");
    };
    if (picture) {
      // Albums keep multiple pages in a single compact group. Upload bytes directly;
      // no public image endpoint, secret-bearing URL, or permanent image files.
      for (let start = 0; start < picture.images.length; start += 10) {
        const images = picture.images.slice(start, start + 10), form = new FormData();
        form.set("chat_id", String(update.message.chat.id));
        if (images.length === 1) {
          form.set("photo", new Blob([new Uint8Array(images[0].png)], { type: "image/png" }), "portfolio.png");
          form.set("caption", picture.caption);
          await send("sendPhoto", form);
        } else {
          form.set("media", JSON.stringify(images.map((_, i) => ({ type: "photo", media: `attach://page${i}`, ...(i === 0 ? { caption: picture.caption } : {}) }))));
          images.forEach((image, i) => form.set(`page${i}`, new Blob([new Uint8Array(image.png)], { type: "image/png" }), `portfolio-${image.page}.png`));
          await send("sendMediaGroup", form);
        }
      }
      return NextResponse.json({ ok: true }, { headers });
    }
    const parts = splitTelegram(text.replace(/[—·]/g, " "));
    for (const [index, part] of parts.entries()) {
      await send("sendMessage", JSON.stringify({ chat_id: String(update.message.chat.id),
          text: parts.length > 1 ? `📄 ${index + 1}/${parts.length}\n\n${part.trim()}` : part,
          disable_web_page_preview: true }));
    }
    return NextResponse.json({ ok: true }, { headers });
  } catch {
    // Avoid exposing token-bearing fetch errors. Telegram can retry delivery; /refresh is deduped above.
    return NextResponse.json({ error: "Reply unavailable" }, { status: 503, headers });
  }
}
