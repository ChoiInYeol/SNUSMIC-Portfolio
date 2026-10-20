"use client";

import { useSyncExternalStore } from "react";
import { AlertTriangle } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

// The account pages are static, so staleness must be judged in the viewer's browser against the data's
// valuation date, not at build time. Renders nothing on the server and before hydration.
const noopSubscribe = () => () => {};
const todayKst = () => new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });

/** Calendar days after which a valuation counts as stale (covers a long weekend plus a holiday). */
const STALE_DAYS = 5;

export function StaleNotice({ asOf }: { asOf: string }) {
  const today = useSyncExternalStore(noopSubscribe, todayKst, () => null);
  if (!today) return null;
  const days = Math.round((Date.parse(`${today}T00:00:00Z`) - Date.parse(`${asOf}T00:00:00Z`)) / 86_400_000);
  if (days <= STALE_DAYS) return null;
  return (
    <Alert className="mb-3 border-warn/60">
      <AlertTriangle className="size-4 text-warn" aria-hidden="true" />
      <AlertTitle>평가가 오래되었습니다</AlertTitle>
      <AlertDescription>평가 기준일 {asOf}이 오늘(KST {today})보다 {days}일 앞섭니다. 시세 수집 또는 갱신 작업이 멈췄을 수 있습니다.</AlertDescription>
    </Alert>
  );
}
