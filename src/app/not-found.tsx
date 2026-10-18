import Link from "next/link";
import { SiteHeader } from "@/components/site-header";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <main className="mx-auto max-w-[1180px] px-4 py-6 sm:px-8">
      <SiteHeader eyebrow="404 · Not Found" />
      <section className="mt-12 max-w-xl space-y-4">
        <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">기각</p>
        <h1 className="font-display text-4xl font-black tracking-tight">해당 판결문을 찾을 수 없습니다</h1>
        <p className="text-sm text-muted-foreground">
          종목 코드나 학회 주소가 바뀌었거나, 아직 채점되지 않은 리포트일 수 있습니다.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button asChild>
            <Link href="/">증거의 서가로</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/clubs">학회별 성적표</Link>
          </Button>
        </div>
      </section>
    </main>
  );
}
