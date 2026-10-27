import { readFile } from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { ImageResponse } from "next/og";
import type { CSSProperties } from "react";
import type { SotaPortfolio } from "./sota-portfolio";
import { commandTable, ledgerSummary, ledgerUri, type LedgerCommand } from "./portfolio-ledger";

export type LedgerImage = { png: Uint8Array; title: string; page: number; pages: number };
const today = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(new Date());
const flex = (style: CSSProperties = {}): CSSProperties => ({ display: "flex", ...style });
let resources: Promise<{ colors: Record<string, string>; fonts: { name: string; data: ArrayBuffer; weight: 400 | 700; style: "normal" }[] }> | undefined;
function loadResources() {
  resources ??= (async () => {
    const [css, regular, bold] = await Promise.all([
      readFile(path.join(process.cwd(), "src/app/globals.css"), "utf8"),
      readFile(path.join(process.cwd(), "node_modules/pretendard/dist/public/static/Pretendard-Regular.otf")),
      readFile(path.join(process.cwd(), "node_modules/pretendard/dist/public/static/Pretendard-Bold.otf")),
    ]);
    const light = css.match(/\.light\s*\{([^}]+)\}/)?.[1] ?? "";
    const colors = Object.fromEntries(["ledger-background", "foreground", "muted-foreground", "border", "up", "down", "warn"].map((key) => {
      const value = light.match(new RegExp(`--${key}:\\s*([\\d.]+)\\s+([\\d.]+)%\\s+([\\d.]+)%`));
      if (!value) throw new Error("Ledger theme token missing");
      return [key, `hsl(${value[1]}, ${value[2]}%, ${value[3]}%)`];
    }));
    return { colors, fonts: [{ name: "Pretendard", data: Uint8Array.from(regular).buffer, weight: 400 as const, style: "normal" as const },
      { name: "Pretendard", data: Uint8Array.from(bold).buffer, weight: 700 as const, style: "normal" as const }] };
  })().catch((error) => { resources = undefined; throw error; });
  return resources;
}

export function ledgerCaption(command: LedgerCommand, doc: SotaPortfolio, date = today()): string {
  const stale = (Date.parse(date) - Date.parse(doc.as_of)) / 86_400_000 > 3;
  return `${commandTable(command, doc).title} / ${doc.as_of} 종가${stale ? "\n저장된 평가일을 확인하세요." : ""}\nhttps://verdict-archive.vercel.app${ledgerUri(command, doc)}\n텍스트: ${command} text`;
}

// Only rasterization lives here. Column definitions and values are shared with the web ledger.
const cache = new Map<string, Promise<LedgerImage[]>>();
export function renderLedgerImages(command: LedgerCommand, doc: SotaPortfolio, date = today()): Promise<LedgerImage[]> {
  const key = createHash("sha256").update(JSON.stringify([command, doc, date])).digest("hex");
  const saved = cache.get(key);
  if (saved) return saved;
  const task = (async () => {
    const { colors: c, fonts } = await loadResources();
    const table = commandTable(command, doc), limit = 24, pages = Math.max(1, Math.ceil(table.rows.length / limit));
    const width = table.columns.reduce((sum, column) => sum + column.width, 0) + 40;
    const result: LedgerImage[] = [];
    for (let page = 0; page < pages; page++) {
      const rows = table.rows.slice(page * limit, (page + 1) * limit);
      const height = 184 + Math.max(1, rows.length) * 42;
      const image = new ImageResponse(<div style={flex({ width, height, padding: 20, flexDirection: "column", background: c["ledger-background"], color: c.foreground, fontFamily: "Pretendard", fontSize: 22, fontVariantNumeric: "tabular-nums" })}>
        <div style={flex({ justifyContent: "space-between", marginBottom: 10 })}><div style={flex({ fontSize: 28, fontWeight: 700 })}>{table.title}</div><div style={flex()}>{doc.strategy} / {page + 1} of {pages}</div></div>
        <div style={flex({ color: c["muted-foreground"], fontSize: 18, marginBottom: 16 })}>{ledgerSummary(doc)}</div>
        <div style={flex({ flexDirection: "column", borderTop: `2px solid ${c.foreground}` })}>
          <div style={flex({ height: 38, borderBottom: `1px solid ${c.foreground}` })}>{table.columns.map((column) => <div key={column.label} style={flex({ width: column.width, flexShrink: 0, padding: "6px 8px", justifyContent: column.numeric ? "flex-end" : "flex-start", fontSize: 21, fontWeight: 700 })}>{column.label}</div>)}</div>
          {rows.length ? rows.map((row) => <div key={row.id} style={flex({ height: 42, borderBottom: `1px solid ${c.border}` })}>{row.cells.map((cell, index) => <div key={index} style={flex({ width: table.columns[index].width, flexShrink: 0, padding: "8px", justifyContent: table.columns[index].numeric ? "flex-end" : "flex-start", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", color: cell.pnl === undefined || cell.pnl === 0 ? c.foreground : cell.pnl > 0 ? c.up : c.down })}>{cell.text}</div>)}</div>) : <div style={flex({ height: 42, padding: 8 })}>{table.empty}</div>}
        </div>
        <div style={flex({ marginTop: 14, fontSize: 18, color: c["muted-foreground"] })}>가격 KRW/USD / 손익 KRW / 소수 주식 백테스트 모델</div>
      </div>, { width, height, fonts });
      const png = new Uint8Array(await image.arrayBuffer());
      if (png.length > 10 * 1024 * 1024) throw new Error("Ledger image exceeds Telegram size limit");
      result.push({ png, title: table.title, page: page + 1, pages });
    }
    return result;
  })().catch((error) => { cache.delete(key); throw error; });
  if (cache.size >= 8) cache.delete(cache.keys().next().value!);
  cache.set(key, task);
  return task;
}
