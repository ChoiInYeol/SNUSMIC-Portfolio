import fs from "node:fs/promises";
import path from "node:path";
import { FundedAccountSchema, type FundedAccount } from "@/lib/funded-account";

// Server-only loader. The artifact exists only after an operator runs `scripts/funded_account.py init`
// (explicit epoch and contribution day); before that the page shows a "not started" state.
// Invalid JSON or a contract violation throws so the build fails instead of rendering bad numbers.
export type FundedAccountLoad = { status: "not_started" } | { status: "ready"; account: FundedAccount };

export async function loadFundedAccount(file = path.join(process.cwd(), "public/research/funded-account.json")): Promise<FundedAccountLoad> {
  let raw: string;
  try {
    raw = await fs.readFile(file, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return { status: "not_started" };
    throw error;
  }
  return { status: "ready", account: FundedAccountSchema.parse(JSON.parse(raw)) };
}
