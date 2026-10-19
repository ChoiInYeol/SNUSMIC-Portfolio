"use client";

import { useRouter } from "next/navigation";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";

/** counts = null hides an issue's contents (a back issue lists later dates for navigation only). */
export type PickerEntry = { date: string; counts: { open: number; buys: number; sells: number } | null };

/**
 * 지난 호 바로가기 — 스냅샷 수십 개를 월별로 묶은 셀렉트.
 * shadcn Select(Radix listbox): 타이핑 탐색·방향키·Esc를 그대로 얻고, 거래일만 있는 목록이라 달력보다 촘촘하다.
 */
export function SnapshotPicker({ entries, current }: { entries: PickerEntry[]; current: string }) {
  const router = useRouter();
  const months = new Map<string, PickerEntry[]>();
  for (const entry of entries) {
    const month = entry.date.slice(0, 7);
    months.set(month, [...(months.get(month) ?? []), entry]);
  }
  return (
    <Select value={current} onValueChange={(date) => router.push(`/today/${date}`)}>
      <SelectTrigger size="sm" className="h-8 rounded-sm border-foreground/40 bg-card font-mono text-[11px] font-bold tnum" aria-label="지난 호 고르기">
        <SelectValue placeholder="지난 호">{current} 호</SelectValue>
      </SelectTrigger>
      <SelectContent position="popper" align="end" className="max-h-80 font-mono text-[11px]">
        {[...months.entries()].map(([month, items]) => (
          <SelectGroup key={month}>
            <SelectLabel className="font-mono text-[10px] font-bold uppercase tracking-[0.2em]">{month.replace("-", ".")}</SelectLabel>
            {items.map((entry) => (
              <SelectItem key={entry.date} value={entry.date} className="font-mono text-[11px] tnum">
                {entry.date}
                {entry.counts && (
                  <span className="ml-2 text-muted-foreground">
                    보유 {entry.counts.open} · 매수 {entry.counts.buys} · 경보 {entry.counts.sells}
                  </span>
                )}
              </SelectItem>
            ))}
          </SelectGroup>
        ))}
      </SelectContent>
    </Select>
  );
}
