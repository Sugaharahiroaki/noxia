"use client";

import { RequireCaps } from "@/components/common";
import { PeopleReportPage } from "@/components/PeopleReport";

export default function StaffReportPage() {
  return (
    <RequireCaps caps={["payroll.view_all"]} message="スタッフ全員の報酬を見る権限がありません。">
      <PeopleReportPage kind="staff" />
    </RequireCaps>
  );
}
