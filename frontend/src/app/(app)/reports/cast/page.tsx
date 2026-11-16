"use client";

import { RequireCaps } from "@/components/common";
import { PeopleReportPage } from "@/components/PeopleReport";

export default function CastReportPage() {
  return (
    <RequireCaps caps={["payroll.view_all"]} message="キャスト全員の報酬を見る権限がありません。">
      <PeopleReportPage kind="cast" />
    </RequireCaps>
  );
}
