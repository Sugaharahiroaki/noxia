"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { useFloor } from "@/components/AppShell";
import { OpenRegisterCard } from "@/components/OpenRegister";
import { sortByShift, StaffButton, useShiftGuard } from "@/components/ShiftGuard";
import { useToast } from "@/components/Toast";
import { ErrorBox, Spinner } from "@/components/ui";
import { api, ApiError, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtMinutes, yen } from "@/lib/format";
import type { Course, Customer, Employee, StoreSettings, Table, Visit } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type Extra = { key: string; label: string; guest_count: number; course_id: number | null };

export default function NewVisitPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <NewVisitInner />
    </Suspense>
  );
}

function Stepper({ value, onChange, min = 1 }: { value: number; onChange: (v: number) => void; min?: number }) {
  return (
    <div className="flex items-center gap-3">
      <button type="button" className="btn btn-xl" style={{ minWidth: 84 }} onClick={() => onChange(Math.max(min, value - 1))} aria-label="減らす">
        −
      </button>
      <span className="text-6xl font-bold num w-24 text-center">{value}</span>
      <button type="button" className="btn btn-xl" style={{ minWidth: 84 }} onClick={() => onChange(value + 1)} aria-label="増やす">
        ＋
      </button>
    </div>
  );
}

function NewVisitInner() {
  const router = useRouter();
  const params = useSearchParams();
  const toast = useToast();
  const { state: floor, reload: reloadFloor } = useFloor();
  const tables = useLoad<Table[]>("/api/store/tables?active=1");
  const courses = useLoad<Course[]>("/api/store/courses?active=1");
  const employees = useLoad<Employee[]>("/api/staff/employees?active=1");
  const settings = useLoad<StoreSettings>("/api/store/settings");
  const customers = useLoad<Customer[]>("/api/customers?active=1");

  const [tableId, setTableId] = useState<number | null>(params.get("table") ? Number(params.get("table")) : null);
  const [guests, setGuests] = useState(1);
  const [courseId, setCourseId] = useState<number | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [primary, setPrimary] = useState<number | null>(null);
  const [customerId, setCustomerId] = useState<number | null>(null);
  const [customerName, setCustomerName] = useState("");
  const [extras, setExtras] = useState<Extra[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [idem, setIdem] = useState(() => newIdempotencyKey());
  const { guard, element: shiftGuard } = useShiftGuard(() => employees.reload());

  const perPerson = settings.data?.price_basis !== "per_group";
  const occupied = useMemo(() => new Set((floor?.tables ?? []).filter((t) => t.visits.length > 0).map((t) => t.id)), [floor]);
  const table = tables.data?.find((t) => t.id === tableId) ?? null;
  const course = courses.data?.find((c) => c.id === courseId) ?? null;

  const estimate = useMemo(() => {
    const price = (cid: number | null, n: number) => {
      const c = courses.data?.find((x) => x.id === cid);
      return c ? Number(c.price) * (perPerson ? n : 1) : 0;
    };
    return price(courseId, guests) + extras.reduce((s, e) => s + price(e.course_id, e.guest_count), 0);
  }, [courses.data, courseId, guests, extras, perPerson]);

  // 入力が変わったら新しい操作として扱う（同じ内容の再送は同じキーで二重登録を防ぐ）
  const touch = () => setIdem(newIdempotencyKey());

  async function submit() {
    if (!tableId || !courseId) return;
    if (extras.some((e) => !e.course_id)) {
      setError("追加した人のコースを選んでください。");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const v = await api<Visit>("/api/visits", {
        method: "POST",
        idempotencyKey: idem,
        body: {
          table_id: tableId,
          primary_employee_id: primary,
          customer_id: customerId,
          customer_name: customerName,
          entries: [
            { label: "", guest_count: guests, course_id: courseId, employee_id: primary },
            ...extras.map((e) => ({ label: e.label, guest_count: e.guest_count, course_id: e.course_id })),
          ],
        },
      });
      await reloadFloor();
      toast.show({ message: `${v.table_name} 入店しました（${v.guest_count}名・伝票#${v.slip_number}）` });
      router.replace("/floor");
    } catch (e) {
      if (e instanceof ApiError && (e.code === "network" || e.code === "in_progress")) {
        setError(`${e.message} もう一度「入店する」を押しても二重には登録されません。`);
      } else {
        setError(errorMessage(e));
        touch();
      }
    } finally {
      setBusy(false);
    }
  }

  if (tables.loading || courses.loading || settings.loading) return <Spinner />;

  // 開店していなければ入店できない（先に開店カードを出す）
  if (floor && !floor.business_day) {
    return (
      <div className="max-w-4xl mx-auto space-y-4">
        <div className="flex items-center justify-between">
          <h1 className="text-3xl font-bold">入店の前に開店してください</h1>
          <button className="btn btn-lg" onClick={() => router.push("/floor")}>
            卓一覧へ
          </button>
        </div>
        <OpenRegisterCard onOpened={() => reloadFloor()} />
      </div>
    );
  }

  // --- 卓が未選択なら、まず卓を選ぶ ---
  if (!table) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h1 className="text-3xl font-bold">どの卓に入店しますか？</h1>
          <button className="btn btn-lg" onClick={() => router.push("/floor")}>
            やめる
          </button>
        </div>
        <ErrorBox error={tables.error} />
        <div className="grid grid-cols-3 md:grid-cols-4 gap-3">
          {tables.data?.map((t) => (
            <button
              key={t.id}
              className={`btn btn-xl flex-col h-auto py-5 ${occupied.has(t.id) ? "" : "btn-primary"}`}
              onClick={() => setTableId(t.id)}
            >
              <span className="text-3xl">{t.name}</span>
              <span className="text-base font-normal">{occupied.has(t.id) ? "使用中（相席）" : "空席"}</span>
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-4 pb-32">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-4xl font-bold">{table.name}に入店</h1>
        {occupied.has(table.id) && <span className="badge badge-warn">この卓は使用中です（相席として追加）</span>}
        <button className="btn ml-auto" onClick={() => setTableId(null)}>
          卓を変える
        </button>
        <button className="btn" onClick={() => router.push("/floor")}>
          やめる
        </button>
      </div>
      <ErrorBox error={courses.error || settings.error} />

      <section className="card space-y-3">
        <h2 className="text-2xl font-bold">人数</h2>
        <Stepper
          value={guests}
          onChange={(v) => {
            setGuests(v);
            touch();
          }}
        />
      </section>

      <section className="card space-y-3">
        <h2 className="text-2xl font-bold">コース{!courseId && <span className="text-lg ml-2" style={{ color: "var(--color-warn)" }}>← 選んでください</span>}</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {courses.data?.map((c) => (
            <button
              key={c.id}
              type="button"
              className={`btn btn-xl justify-between h-auto py-4 ${courseId === c.id ? "btn-primary" : ""}`}
              style={courseId === c.id ? { outline: "3px solid #fff", outlineOffset: 2 } : undefined}
              onClick={() => {
                setCourseId(c.id);
                touch();
              }}
              aria-pressed={courseId === c.id}
            >
              <span className="text-left">
                <span className="block">{c.name.replace("飲み放題・", "")}</span>
                <span className="block text-base font-normal opacity-80">{c.duration_minutes ? `${fmtMinutes(c.duration_minutes)}・延長 ${yen(c.extension_price)}` : "時間無制限"}</span>
              </span>
              <span className="num">
                {yen(c.price)}
                <span className="text-base">{perPerson ? "/人" : "/組"}</span>
              </span>
            </button>
          ))}
        </div>
      </section>

      <section className="card space-y-3">
        <button className="btn w-full justify-between" onClick={() => setDetailOpen((v) => !v)} aria-expanded={detailOpen}>
          <span>担当・お客様名・別コースの人を追加（任意）</span>
          <span>{detailOpen ? "▲" : "▼"}</span>
        </button>
        {detailOpen && (
          <div className="space-y-4">
            <div>
              <div className="label text-base">担当</div>
              <div className="flex flex-wrap gap-2">
                <button className={`btn ${primary === null ? "btn-primary" : ""}`} onClick={() => setPrimary(null)}>
                  なし
                </button>
                {sortByShift(employees.data ?? []).map((e) => (
                  <StaffButton
                    key={e.id}
                    e={e}
                    size="md"
                    selected={primary === e.id}
                    onClick={async () => {
                      if (primary !== e.id && !(await guard(e))) return;
                      setPrimary(e.id);
                      touch();
                    }}
                  />
                ))}
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <label className="block">
                <span className="label text-base">登録済みのお客様</span>
                <select className="input" value={customerId ?? ""} onChange={(e) => { setCustomerId(e.target.value ? Number(e.target.value) : null); touch(); }}>
                  <option value="">選ばない</option>
                  {customers.data?.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.display_name}
                      {!c.is_own_store ? `（${c.store_name}）` : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="label text-base">お客様の呼び名（メモ）</span>
                <input className="input" value={customerName} onChange={(e) => { setCustomerName(e.target.value); touch(); }} placeholder="例: 田中様" />
              </label>
            </div>
            <div className="space-y-2">
              <div className="label text-base">別のコースの人（同じ卓で時間が別）</div>
              {extras.map((x) => (
                <div key={x.key} className="card-2 space-y-2">
                  <div className="flex flex-wrap items-center gap-3">
                    <input className="input" style={{ maxWidth: 260 }} value={x.label} placeholder="呼び名（任意）" onChange={(e) => setExtras((xs) => xs.map((y) => (y.key === x.key ? { ...y, label: e.target.value } : y)))} />
                    <button className="btn" onClick={() => setExtras((xs) => xs.map((y) => (y.key === x.key ? { ...y, guest_count: Math.max(1, y.guest_count - 1) } : y)))}>
                      −
                    </button>
                    <span className="text-2xl font-bold num">{x.guest_count}名</span>
                    <button className="btn" onClick={() => setExtras((xs) => xs.map((y) => (y.key === x.key ? { ...y, guest_count: y.guest_count + 1 } : y)))}>
                      ＋
                    </button>
                    <button className="btn btn-ghost ml-auto" onClick={() => { setExtras((xs) => xs.filter((y) => y.key !== x.key)); touch(); }}>
                      削除
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {courses.data?.map((c) => (
                      <button key={c.id} className={`btn ${x.course_id === c.id ? "btn-primary" : ""}`} onClick={() => { setExtras((xs) => xs.map((y) => (y.key === x.key ? { ...y, course_id: c.id } : y))); touch(); }}>
                        {c.name.replace("飲み放題・", "")} {yen(c.price)}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              <button className="btn" onClick={() => { setExtras((xs) => [...xs, { key: `x${Date.now()}`, label: "", guest_count: 1, course_id: null }]); touch(); }}>
                ＋ 別のコースの人を追加
              </button>
            </div>
          </div>
        )}
      </section>

      {/* 下部固定の確定バー */}
      <div className="fixed left-0 right-0 bottom-0 z-30 px-4 py-3 flex items-center gap-4" style={{ background: "var(--color-panel)", borderTop: "1px solid var(--color-border)" }}>
        <div className="min-w-0">
          <div className="text-lg truncate">
            {table.name} ・ {guests + extras.reduce((s, e) => s + e.guest_count, 0)}名 ・ {course ? course.name.replace("飲み放題・", "") : "コース未選択"}
          </div>
          <div className="text-sm" style={{ color: "var(--color-muted)" }}>
            セット料金の目安 <span className="num font-bold text-lg" style={{ color: "var(--color-text)" }}>{yen(estimate)}</span>
          </div>
        </div>
        {error && (
          <div className="text-lg font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
            {error}
          </div>
        )}
        <button className="btn btn-xl btn-primary ml-auto" style={{ minWidth: 280 }} onClick={submit} disabled={!courseId || busy}>
          {busy ? "登録中…" : "入店する"}
        </button>
      </div>
      {shiftGuard}
    </div>
  );
}
