"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSyncExternalStore } from "react";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "아카이브" },
  { href: "/today", label: "오늘의 신문" },
  { href: "/search", label: "리서치" },
  { href: "/clubs", label: "학회별 성적표" },
  { href: "/stats", label: "통계" },
  { href: "/strategy", label: "전략" },
  { href: "/track", label: "전진 기록" },
  { href: "/portfolio", label: "SOTA 원장" },
  { href: "/developers", label: "API" },
];

const noopSubscribe = () => () => {};
const isMacSnapshot = () => /Mac|iPhone|iPad/i.test(navigator.userAgent);

/** 공용 내비게이션 — 잉크 밑줄과 현재 위치 표시. 헤더·마스트헤드 공용 */
export function SiteNav({ className }: { className?: string }) {
  const pathname = usePathname();
  // 단축키 표기 — 서버에선 Ctrl로 그리고, 클라이언트에서 플랫폼을 보고 ⌘로 바꾼다 (SSG 안전)
  const isMac = useSyncExternalStore(noopSubscribe, isMacSnapshot, () => false);
  return (
    <nav
      className={cn("flex flex-wrap items-center gap-x-3.5 gap-y-1 font-mono text-[11px] font-bold uppercase tracking-[0.08em] sm:gap-x-5 sm:text-xs sm:tracking-[0.16em]", className)}
      aria-label="사이트 내비게이션"
    >
      {LINKS.map((link) => {
        const current = link.href === "/" ? pathname === "/" : pathname === link.href || pathname.startsWith(`${link.href}/`);
        return (
          <Link key={link.href} href={link.href} className="nav-link" aria-current={current ? "page" : undefined}>
            {link.label}
          </Link>
        );
      })}
      <button
        type="button"
        onClick={() => window.dispatchEvent(new Event("verdict:open-palette"))}
        className="nav-link"
        title="아카이브 전체 검색 — 종목·판결 기록으로 점프"
      >
        검색
        <kbd className="ml-1.5 hidden rounded-sm border border-border px-1 py-px font-mono text-[9px] font-bold tracking-normal text-muted-foreground sm:inline">
          {isMac ? "⌘K" : "Ctrl K"}
        </kbd>
      </button>
    </nav>
  );
}
