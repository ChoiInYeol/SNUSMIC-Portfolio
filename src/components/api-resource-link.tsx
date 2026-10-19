"use client";

import { useEffect, useState } from "react";
import { Check, Copy, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

const actionClass = "size-7 shrink-0 rounded-sm p-0 text-muted-foreground";

/**
 * 화면 뒤의 데이터 출처 — 이 지면을 만든 정확한 v2 리소스 URI를 보여주고, 복사·새 탭 열기를 제공한다.
 * 같은 바이트를 누구나 GET으로 받을 수 있다는 "판권 표시".
 */
export function ApiResourceLink({ uri, label = "API", className }: { uri: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(new URL(uri, window.location.origin).toString());
      setCopied(true);
    } catch {
      // Clipboard denied (insecure context, permissions): the URI stays visible and selectable.
    }
  };

  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 rounded-sm border border-dashed border-foreground/40 bg-card py-0.5 pl-2 pr-0.5 font-mono text-[11px] print:hidden",
        className,
      )}
      data-api-resource={uri}
    >
      <span className="shrink-0 text-[10px] font-bold uppercase tracking-[0.18em] text-muted-foreground">{label}</span>
      <span className="shrink-0 rounded-[2px] bg-stamp px-1 text-[9px] font-bold leading-4 text-background">GET</span>
      <code className="min-w-0 truncate select-all text-foreground" title={uri}>
        {uri}
      </code>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button type="button" variant="ghost" size="sm" onClick={copy} className={actionClass} aria-label={`${uri} 전체 URL 복사`}>
            {copied ? <Check className="size-4 text-stamp" aria-hidden="true" /> : <Copy className="size-4" aria-hidden="true" />}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{copied ? "복사했습니다" : "URL 복사"}</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button asChild variant="ghost" size="sm" className={actionClass}>
            <a href={uri} target="_blank" rel="noopener noreferrer" aria-label={`${uri} 새 탭에서 열기`}>
              <ExternalLink className="size-4" aria-hidden="true" />
            </a>
          </Button>
        </TooltipTrigger>
        <TooltipContent>JSON 원본 열기</TooltipContent>
      </Tooltip>
      <span className="sr-only" aria-live="polite">
        {copied ? "복사했습니다" : ""}
      </span>
    </span>
  );
}
