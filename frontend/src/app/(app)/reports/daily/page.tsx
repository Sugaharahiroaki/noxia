"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useBusinessDate } from "@/components/DateRange";
import { Spinner } from "@/components/ui";

/** 営業日報の入口: いまの営業日の日報へ移る */
export default function DailyIndexPage() {
  const bd = useBusinessDate();
  const router = useRouter();
  useEffect(() => {
    if (bd) router.replace(`/reports/daily/${bd}`);
  }, [bd, router]);
  return <Spinner />;
}
