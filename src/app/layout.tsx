import type { Metadata } from "next";
import { Geist_Mono, Noto_Serif_KR } from "next/font/google";
import { CommandPalette } from "@/components/command-palette";
import { ThemeProvider } from "@/components/theme-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
// Pretendard Variable 자체 호스팅 — CDN 불안정성을 피하고 빌드에 woff2를 함께 싣는다
import "pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css";
import "./globals.css";

const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
const notoSerif = Noto_Serif_KR({ variable: "--font-noto-serif", subsets: ["latin"], weight: ["400", "600", "700", "900"], display: "swap" });

const SITE_TITLE = "판결 아카이브 — 시간이 매긴 성적표";
const SITE_DESCRIPTION =
  "서울대 SMIC·연세대 YIG·성균관대 STAR·고려대 KUVIC·이화여대 EIA·홍익대 VOERA 리포트 PDF를 전사·파싱해 목표가를 추출하고, point-in-time 시세로 그 후의 실제 주가 경로를 검증한 아카이브.";

export const metadata: Metadata = {
  metadataBase: new URL("https://verdict-archive.vercel.app"),
  title: SITE_TITLE,
  description: SITE_DESCRIPTION,
  openGraph: {
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
    siteName: "판결 아카이브",
    locale: "ko_KR",
    type: "website",
    url: "/",
  },
  twitter: {
    card: "summary_large_image",
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <body className={`${geistMono.variable} ${notoSerif.variable} antialiased`}>
        <ThemeProvider>
          <TooltipProvider delayDuration={200}>
            {children}
            <CommandPalette />
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
