"use client";
import dynamic from "next/dynamic";
import { Skeleton } from "@/components/ui/skeleton";

const Explorer = dynamic(() => import("./explorer"), { ssr: false, loading: () => <Skeleton className="h-[65svh] w-full" aria-label="데이터 지도 로딩 중" /> });
export function ArchitectureLoader() { return <Explorer />; }
