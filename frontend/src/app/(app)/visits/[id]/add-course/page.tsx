"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { useFloor } from "@/components/AppShell";
import { SHIFT_LABEL } from "@/components/ShiftGuard";
import { useToast } from "@/components/Toast";
import { ErrorBox, Field, PageHeader, SaveStatus, Spinner, type SaveState } from "@/components/ui";
import { api, newIdempotencyKey } from "@/lib/api";
import { fmtMinutes, yen } from "@/lib/format";
import type { Course, Employee, StoreSettings, Visit } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

export default function AddCoursePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { reload: reloadFloor } = useFloor();
  const toast = useToast();
  const visit = useLoad<Visit>(`/api/visits/${id}`);
  const courses = useLoad<Course[]>("/api/store/courses?active=1");
  const employees = useLoad<Employee[]>("/api/staff/employees?active=1");
  const settings = useLoad<StoreSettings>("/api/store/settings");
  const [label, setLabel] = useState("");
  const [guests, setGuests] = useState(1);
  const [courseId, setCourseId] = useState<number | null>(null);
  const [emp, setEmp] = useState<number | null>(null);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [idem] = useState(() => newIdempotencyKey());
  const perPerson = settings.data?.price_basis !== "per_group";

  if (visit.loading || courses.loading) return <Spinner />;

  async function submit() {
    if (!courseId) return;
    setSave({ kind: "saving" });
    try {
      await api(`/api/visits/${id}/course-entries`, { method: "POST", idempotencyKey: idem, body: { label, guest_count: guests, course_id: courseId, employee_id: emp } });
      setSave({ kind: "saved" });
      await reloadFloor();
      toast.show({ message: `${visit.data?.table_name ?? ""} に ${guests}名 合流しました` });
      router.replace("/floor");
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      <PageHeader title={`伝票#${visit.data?.slip_number} に途中合流・コース追加`} subtitle="合流した人・グループは今の時刻から別のコース・終了予定を持ちます。" />
      <ErrorBox error={visit.error || courses.error} />
      <div className="card space-y-3">
        <Field label="対象の名前（任意）">
          <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="例: 合流の山本様" />
        </Field>
        <div>
          <span className="label">人数</span>
          <div className="flex items-center gap-2">
            <button className="btn" onClick={() => setGuests(Math.max(1, guests - 1))}>
              −
            </button>
            <span className="text-3xl font-bold num w-12 text-center">{guests}</span>
            <button className="btn" onClick={() => setGuests(guests + 1)}>
              ＋
            </button>
          </div>
        </div>
        <div>
          <span className="label">コース</span>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {courses.data?.map((c) => (
              <button key={c.id} className={`btn justify-between ${courseId === c.id ? "btn-primary" : ""}`} onClick={() => setCourseId(c.id)} aria-pressed={courseId === c.id}>
                <span>
                  {c.name} <span className="text-xs opacity-80">{c.duration_minutes ? fmtMinutes(c.duration_minutes) : "無制限"}</span>
                </span>
                <span className="num">
                  {yen(c.price)}
                  {perPerson ? "/人" : "/組"}
                </span>
              </button>
            ))}
          </div>
        </div>
        <Field label="担当（任意）">
          <select className="input" value={emp ?? ""} onChange={(e) => setEmp(e.target.value ? Number(e.target.value) : null)}>
            <option value="">なし</option>
            {employees.data?.map((x) => (
              <option key={x.id} value={x.id}>
                {x.display_name}
                {x.shift_status && x.shift_status !== "working" ? `（${SHIFT_LABEL[x.shift_status]}）` : ""}
              </option>
            ))}
          </select>
        </Field>
        <div className="flex items-center gap-3 justify-end">
          <SaveStatus state={save} />
          <button className="btn" onClick={() => router.back()}>
            戻る
          </button>
          <button className="btn btn-primary btn-lg" onClick={submit} disabled={!courseId || save.kind === "saving"}>
            追加する
          </button>
        </div>
      </div>
    </div>
  );
}
