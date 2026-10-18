"use client";

import Link from "next/link";
import { AlertCircle } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="mx-auto max-w-[1180px] px-4 py-12 sm:px-8">
      <Alert variant="destructive" className="max-w-xl">
        <AlertCircle />
        <AlertTitle>페이지를 표시하지 못했습니다.</AlertTitle>
        <AlertDescription>
          <p>게시된 데이터를 읽는 중 오류가 발생했습니다.{error.digest ? ` (오류 코드 ${error.digest})` : ""}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" onClick={reset}>다시 시도</Button>
            <Button size="sm" variant="outline" asChild>
              <Link href="/">홈으로</Link>
            </Button>
          </div>
        </AlertDescription>
      </Alert>
    </main>
  );
}
