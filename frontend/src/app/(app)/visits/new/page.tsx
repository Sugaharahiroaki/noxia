"use client";

import { Tx } from "@/lib/terms";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useFloor } from "@/components/AppShell";
import { OpenRegisterCard } from "@/components/OpenRegister";
import { sortByShift, StaffButton, useShiftGuard } from "@/components/ShiftGuard";
import { useToast } from "@/components/Toast";
import { ErrorBox, Spinner } from "@/components/ui";
import { api, ApiError, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtMinutes, LINE_TYPE_LABELS, yen } from "@/lib/format";
import type { Course, Customer, Designation, Employee, StoreSettings, Table, Visit, VisitPreview } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type Step = "kind" | "count" | "guests" | "course";
type GuestDraft = { employee: number | null; designation: Designation; courseId: number | null };

const DESIGNATIONS: {
  key: Exclude<Designation, "">;
  label: string;
  fee: "nomination_fee" | "jounai_fee" | "douhan_fee";
  enabled: "nomination_enabled" | "jounai_enabled" | "douhan_enabled";
}[] = [
  { key: "nomination", label: "本指名", fee: "nomination_fee", enabled: "nomination_enabled" },
  { key: "jounai", label: "場内", fee: "jounai_fee", enabled: "jounai_enabled" },
  { key: "douhan", label: "同伴", fee: "douhan_fee", enabled: "douhan_enabled" },
];
const MAX_GUESTS = 30;

export default function NewVisitPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <NewVisitInner />
    </Suspense>
  );
}

/**
 * 入店ウィザード: 空席 → セット構成 → 人数 → 客ごとのキャストと 本指名・場内・同伴 → 時間と金額 → 確定 → 卓詳細。
 * 確定までは画面内の下書き（戻る・キャンセルで保存しない）。金額は見積 API の値をそのまま出す（画面で掛け算しない）。
 */
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
  const [step, setStep] = useState<Step>("kind");
  const [kind, setKind] = useState<string | null>(null);
  const [guests, setGuests] = useState<GuestDraft[]>([{ employee: null, designation: "", courseId: null }]);
  const [active, setActive] = useState(0);
  const [individual, setIndividual] = useState(false);
  const [courseId, setCourseId] = useState<number | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [primary, setPrimary] = useState<number | null>(null);
  const [customerId, setCustomerId] = useState<number | null>(null);
  const [customerName, setCustomerName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<VisitPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const idem = useRef(newIdempotencyKey());
  const { guard, element: shiftGuard } = useShiftGuard(() => employees.reload());

  const perPerson = settings.data?.price_basis !== "per_group";
  const occupied = useMemo(() => new Set((floor?.tables ?? []).filter((t) => t.visits.length > 0).map((t) => t.id)), [floor]);
  const table = tables.data?.find((t) => t.id === tableId) ?? null;
  const kinds = useMemo(() => [...new Set((courses.data ?? []).map((c) => c.kind))], [courses.data]);
  const hasKinds = kinds.length > 1;
  const shownCourses = useMemo(() => (courses.data ?? []).filter((c) => !hasKinds || kind === null || c.kind === kind), [courses.data, hasKinds, kind]);
  const empMap = useMemo(() => new Map((employees.data ?? []).map((e) => [e.id, e])), [employees.data]);
  const casts = useMemo(() => sortByShift((employees.data ?? []).filter((e) => e.this_store_role === "cast")), [employees.data]);
  const count = guests.length;

  // セット構成（入口）が 1 種類しかない店は、その手順を飛ばす
  useEffect(() => {
    if (courses.data && !hasKinds && step === "kind") setStep("count");
  }, [courses.data, hasKinds, step]);

  // 入力が変わったら新しい操作として扱う（同じ内容の再送は同じキーで二重登録を防ぐ）
  function touch() {
    idem.current = newIdempotencyKey();
    setError(null);
  }
  function setGuest(i: number, patch: Partial<GuestDraft>) {
    setGuests((gs) => gs.map((g, j) => (j === i ? { ...g, ...patch } : g)));
    touch();
  }
  function setCount(n: number) {
    const next = Math.max(1, Math.min(MAX_GUESTS, n));
    setGuests((gs) => (next > gs.length ? [...gs, ...Array.from({ length: next - gs.length }, () => ({ employee: null, designation: "" as Designation, courseId: null }))] : gs.slice(0, next)));
    setActive((a) => Math.min(a, next - 1));
    touch();
  }

  /** API に送る内容。全員同じなら 1 つのセット、個別ならコースごとにまとめる */
  const body = useMemo(() => {
    if (!tableId) return null;
    const perGuest = guests.map((g) => (individual ? g.courseId : courseId));
    if (perGuest.some((c) => !c)) return null;
    const order: number[] = [];
    for (const c of perGuest) if (!order.includes(c!)) order.push(c!);
    return {
      table_id: tableId,
      primary_employee_id: primary,
      customer_id: customerId,
      customer_name: customerName,
      entries: order.map((c) => ({ label: "", course_id: c, guest_count: perGuest.filter((x) => x === c).length })),
      guests: guests.map((g, i) => ({ entry_index: order.indexOf(perGuest[i]!), employee_id: g.employee, designation: g.employee ? g.designation : "" })),
    };
  }, [tableId, guests, individual, courseId, primary, customerId, customerName]);

  // 見積（保存しない）。時間と金額の手順で、内容が変わるたびにサーバーで計算し直す
  const previewKey = step === "course" && body ? JSON.stringify({ e: body.entries, g: body.guests }) : null;
  useEffect(() => {
    if (!previewKey || !body) {
      setPreview(null);
      setPreviewError(null);
      return;
    }
    let alive = true;
    api<VisitPreview>("/api/visits/preview", { method: "POST", body: { entries: body.entries, guests: body.guests } })
      .then((p) => {
        if (!alive) return;
        setPreview(p);
        setPreviewError(null);
      })
      .catch((e) => {
        if (!alive) return;
        setPreview(null);
        setPreviewError(errorMessage(e));
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewKey]);

  async function submit() {
    if (!body || busy) return;
    setBusy(true);
    setError(null);
    try {
      const v = await api<Visit>("/api/visits", { method: "POST", idempotencyKey: idem.current, body });
      await reloadFloor();
      toast.show({ message: `${v.table_name} 入店しました（${v.guest_count}名・伝票#${v.slip_number}）` });
      router.replace(`/visits/${v.id}`);
    } catch (e) {
      if (e instanceof ApiError && (e.code === "network" || e.code === "in_progress")) {
        setError(`${e.message} もう一度「入店する」を押しても二重には登録されません。`);
      } else {
        setError(errorMessage(e));
        idem.current = newIdempotencyKey();
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
            ホールへ
          </button>
        </div>
        <OpenRegisterCard onOpened={() => reloadFloor()} />
      </div>
    );
  }

  // --- 卓が未選択なら、まず卓を選ぶ ---
  if (!table) {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h1 className="text-3xl font-bold">どの卓に入店しますか？</h1>
          <button className="btn btn-lg" onClick={() => router.push("/floor")}>
            やめる
          </button>
        </div>
        <ErrorBox error={tables.error} />
        <div className="grid grid-cols-4 md:grid-cols-5 gap-2">
          {tables.data?.map((t) => (
            <button key={t.id} className={`btn btn-xl flex-col h-auto py-4 ${occupied.has(t.id) ? "" : "btn-primary"}`} onClick={() => setTableId(t.id)}>
              <span className="text-3xl">{t.name}</span>
              <span className="text-base font-normal">{occupied.has(t.id) ? "使用中（相席）" : "空席"}</span>
            </button>
          ))}
        </div>
      </div>
    );
  }

  const steps: { key: Step; label: string }[] = [
    ...(hasKinds ? [{ key: "kind" as Step, label: "セット構成" }] : []),
    { key: "count", label: "人数" },
    { key: "guests", label: "キャスト" },
    { key: "course", label: "時間と金額" },
  ];
  const stepIndex = steps.findIndex((s) => s.key === step);
  const fee = (d: (typeof DESIGNATIONS)[number]) => settings.data?.[d.fee] ?? null;
  // 店舗設定で使わないことにした種類は出さない（POS 設定「指名・同伴」）
  const designations = DESIGNATIONS.filter((d) => settings.data?.[d.enabled] !== false);
  const goBack = () => (stepIndex <= 0 ? router.push("/floor") : setStep(steps[stepIndex - 1].key));
  const goNext = () => setStep(steps[stepIndex + 1].key);
  const courseName = (id: number | null) => courses.data?.find((c) => c.id === id)?.name ?? "";
  const summary = [table.name, hasKinds && kind !== null ? kind || "区分なし" : null, `${count}名`, step === "course" && !individual && courseId ? courseName(courseId) : null].filter(Boolean).join(" ・ ");

  return (
    <div className="space-y-3 pb-28">
      {/* 上部: 卓名と手順 */}
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-3xl font-bold">{table.name} に入店</h1>
        {occupied.has(table.id) && <span className="badge badge-warn">この卓は使用中です（相席として追加）</span>}
        <ol className="flex items-center gap-1 ml-2" aria-label="手順">
          {steps.map((s, i) => (
            <li key={s.key} className={`rounded-full px-3 py-1 text-sm font-bold ${i === stepIndex ? "pos-selected" : ""}`} style={i === stepIndex ? undefined : { background: "var(--color-panel3)", color: i < stepIndex ? "var(--color-text)" : "var(--color-muted)" }} aria-current={i === stepIndex ? "step" : undefined}>
              {i + 1}. <Tx>{s.label}</Tx>
            </li>
          ))}
        </ol>
        <button className="btn btn-sm ml-auto" onClick={() => router.push("/floor")}>
          キャンセル
        </button>
      </div>
      <ErrorBox error={courses.error || settings.error || employees.error} />

      {/* 1. セット構成（入口） */}
      {step === "kind" && (
        <section className="card space-y-3">
          <h2 className="text-2xl font-bold">
            <Tx>セット構成を選んでください</Tx>
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {kinds.map((k) => (
              <button
                key={k || "_"}
                className={`btn btn-xl flex-col h-auto py-5 ${kind === k ? "btn-primary" : ""}`}
                onClick={() => {
                  setKind(k);
                  setCourseId(null);
                  setGuests((gs) => gs.map((g) => ({ ...g, courseId: null })));
                  touch();
                  setStep("count");
                }}
                aria-pressed={kind === k}
              >
                <span className="text-2xl">{k || "区分なし"}</span>
                <span className="text-sm font-normal">{(courses.data ?? []).filter((c) => c.kind === k).length} 種類</span>
              </button>
            ))}
          </div>
        </section>
      )}

      {/* 2. 人数 */}
      {step === "count" && (
        <section className="card space-y-3">
          <h2 className="text-2xl font-bold">新規人数</h2>
          <div className="flex items-center justify-center gap-6 py-4">
            <button type="button" className="btn btn-xl btn-minus" style={{ minWidth: 110, fontSize: "2.2rem" }} onClick={() => setCount(count - 1)} disabled={count <= 1} aria-label="減らす">
              −
            </button>
            <span className="font-bold num text-center" style={{ fontSize: "5.5rem", lineHeight: 1, minWidth: 200 }}>
              {count}
              <span className="text-3xl ml-1">名様</span>
            </span>
            <button type="button" className="btn btn-xl btn-plus" style={{ minWidth: 110, fontSize: "2.2rem" }} onClick={() => setCount(count + 1)} disabled={count >= MAX_GUESTS} aria-label="増やす">
              ＋
            </button>
          </div>
        </section>
      )}

      {/* 3. 客ごとのキャストと 本指名・場内・同伴 */}
      {step === "guests" && (
        <div className="grid grid-cols-12 gap-3">
          <section className="col-span-6 card space-y-2 overflow-y-auto" style={{ maxHeight: "calc(100vh - 290px)" }} aria-label="お客様">
            <p className="text-sm" style={{ color: "var(--color-muted)" }}>
              お客様を選んで、右のキャストを押します。キャストを付けない人はそのままで構いません。
            </p>
            {guests.map((g, i) => {
              const on = i === active;
              const emp = g.employee ? empMap.get(g.employee) : null;
              return (
                <div key={i} className={`rounded-xl px-3 py-2 ${on ? "pos-selected" : ""}`} style={on ? undefined : { background: "var(--color-panel2)", border: "1px solid var(--color-border)" }}>
                  <button type="button" className="w-full flex items-center justify-between text-left" style={{ minHeight: 44 }} onClick={() => setActive(i)} aria-pressed={on} aria-label={`お客様${i + 1}`}>
                    <span className="text-xl font-bold">お客様{i + 1}</span>
                    <span className="text-lg">{emp ? emp.display_name : "キャストなし"}</span>
                  </button>
                  <div className="grid gap-2 mt-1" style={{ gridTemplateColumns: `repeat(${Math.max(designations.length, 1)}, minmax(0, 1fr))` }} role="group" aria-label={`お客様${i + 1} の指定`}>
                    {designations.map((d) => {
                      const f = fee(d);
                      const selected = g.designation === d.key;
                      return (
                        <button
                          key={d.key}
                          type="button"
                          className={`btn btn-sm flex-col h-auto py-1 ${selected ? "btn-primary" : ""}`}
                          disabled={!g.employee || f === null}
                          onClick={() => setGuest(i, { designation: selected ? "" : d.key })}
                          aria-pressed={selected}
                          title={f === null ? `${d.label}の料金が未設定です` : undefined}
                        >
                          <span>
                            <Tx>{d.label}</Tx>
                          </span>
                          <span className="text-xs font-normal num">{f === null ? "料金未設定" : Number(f) === 0 ? "料金なし" : yen(f)}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </section>
          <section className="col-span-6 card space-y-2 overflow-y-auto" style={{ maxHeight: "calc(100vh - 290px)" }} aria-label="キャスト">
            <h2 className="text-lg font-bold">お客様{active + 1} に付くキャスト</h2>
            <div className="grid grid-cols-3 gap-2">
              <button className={`btn ${guests[active]?.employee === null ? "btn-primary" : ""}`} onClick={() => setGuest(active, { employee: null, designation: "" })}>
                なし
              </button>
              {casts.map((e) => (
                <StaffButton
                  key={e.id}
                  e={e}
                  size="md"
                  selected={guests[active]?.employee === e.id}
                  onClick={async () => {
                    if (guests[active]?.employee !== e.id && !(await guard(e))) return;
                    setGuest(active, { employee: e.id });
                    // 次の、まだキャストを決めていないお客様へ進む
                    const next = guests.findIndex((g, j) => j > active && g.employee === null);
                    if (next >= 0) setActive(next);
                  }}
                />
              ))}
            </div>
            {casts.length === 0 && <p style={{ color: "var(--color-muted)" }}>この店舗に配属されたキャストがいません。</p>}
          </section>
        </div>
      )}

      {/* 4. 時間と金額 */}
      {step === "course" && (
        <div className="grid grid-cols-12 gap-3">
          <section className="col-span-7 card space-y-3 overflow-y-auto" style={{ maxHeight: "calc(100vh - 290px)" }}>
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-2xl font-bold">{count}名様 の時間と金額</h2>
              <button
                className={`btn btn-sm ${individual ? "btn-primary" : ""}`}
                onClick={() => {
                  setIndividual((v) => {
                    if (!v) setGuests((gs) => gs.map((g) => ({ ...g, courseId: g.courseId ?? courseId })));
                    return !v;
                  });
                  touch();
                }}
                aria-pressed={individual}
              >
                {individual ? "✓ 個別に設定中（全員同じに戻す）" : "個別に設定する"}
              </button>
            </div>
            {!individual && (
              <div className="grid grid-cols-2 gap-2">
                {shownCourses.map((c) => (
                  <CourseButton key={c.id} c={c} perPerson={perPerson} selected={courseId === c.id} onClick={() => { setCourseId(c.id); touch(); }} />
                ))}
              </div>
            )}
            {individual &&
              guests.map((g, i) => (
                <div key={i} className="card-2 space-y-1">
                  <div className="font-bold">
                    お客様{i + 1}
                    {g.employee ? `（${empMap.get(g.employee)?.display_name ?? ""}）` : ""}
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    {shownCourses.map((c) => (
                      <CourseButton key={c.id} c={c} perPerson={perPerson} compact selected={g.courseId === c.id} onClick={() => setGuest(i, { courseId: c.id })} />
                    ))}
                  </div>
                </div>
              ))}
            {shownCourses.length === 0 && <p style={{ color: "var(--color-muted)" }}>このセット構成のコースが登録されていません。</p>}

            <button className="btn w-full justify-between" onClick={() => setDetailOpen((v) => !v)} aria-expanded={detailOpen}>
              <span>担当・お客様名（任意）</span>
              <span>{detailOpen ? "▲" : "▼"}</span>
            </button>
            {detailOpen && (
              <div className="space-y-3">
                <div>
                  <div className="label text-base">担当</div>
                  <div className="flex flex-wrap gap-2">
                    <button className={`btn ${primary === null ? "btn-primary" : ""}`} onClick={() => { setPrimary(null); touch(); }}>
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
                <div className="grid grid-cols-2 gap-3">
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
              </div>
            )}
          </section>

          <section className="col-span-5 card space-y-2" aria-label="入店時の金額">
            <h2 className="text-lg font-bold">入店時の金額（確定前）</h2>
            {!body && <p style={{ color: "var(--color-muted)" }}>{individual ? "全員のコースを選んでください。" : "コースを選んでください。"}</p>}
            {previewError && (
              <p className="font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
                {previewError}
              </p>
            )}
            {body && !preview && !previewError && <Spinner label="金額を計算中…" />}
            {preview && (
              <>
                <div className="space-y-1">
                  {preview.lines.map((l, i) => (
                    <div key={i} className="flex items-baseline justify-between gap-2 text-lg">
                      <span className="min-w-0">
                        <span className="badge mr-1">
                          <Tx>{LINE_TYPE_LABELS[l.line_type] ?? l.line_type}</Tx>
                        </span>
                        {l.name}
                        <span className="num" style={{ color: "var(--color-muted)" }}>
                          {" "}
                          {yen(l.unit_price)} × {l.quantity}
                        </span>
                      </span>
                      <span className="font-bold num whitespace-nowrap">{yen(l.amount)}</span>
                    </div>
                  ))}
                </div>
                <div className="pt-2 border-t space-y-0.5 num" style={{ borderColor: "var(--color-border)" }}>
                  <Row label="小計" value={yen(preview.quote.subtotal)} />
                  {Number(preview.quote.service_charge) > 0 && <Row label="サービス料" value={yen(Number(preview.quote.service_charge) + Number(preview.quote.service_charge_tax))} />}
                  <Row label="うち消費税" value={yen(preview.quote.tax_total)} />
                  <div className="flex justify-between items-baseline">
                    <span className="font-bold">合計（税込）</span>
                    <span className="text-4xl font-bold" data-testid="preview-total">
                      {yen(preview.quote.total)}
                    </span>
                  </div>
                </div>
              </>
            )}
          </section>
        </div>
      )}

      {/* 下部固定: 戻る・次へ・入店する */}
      <div className="fixed left-0 right-0 bottom-0 z-30 px-3 py-2 flex items-center gap-3" style={{ background: "var(--color-panel)", borderTop: "2px solid var(--color-border)" }}>
        <button className="btn btn-lg" onClick={goBack} disabled={busy}>
          ← {stepIndex <= 0 ? "ホールへ" : "戻る"}
        </button>
        <div className="min-w-0 flex-1">
          <div className="text-lg font-semibold truncate">{summary}</div>
          {error && (
            <div className="font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
              {error}
            </div>
          )}
        </div>
        {step === "course" ? (
          <button className="btn btn-xl btn-primary" style={{ minWidth: 260 }} onClick={submit} disabled={!body || !preview || busy}>
            {busy ? "登録中…" : "入店する"}
          </button>
        ) : (
          <button className="btn btn-xl btn-primary" style={{ minWidth: 200 }} onClick={goNext} disabled={step === "kind"}>
            次へ →
          </button>
        )}
      </div>
      {shiftGuard}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between" style={{ color: "var(--color-muted)" }}>
      <span>{label}</span>
      <span>{value}</span>
    </div>
  );
}

function CourseButton({ c, perPerson, selected, compact, onClick }: { c: Course; perPerson: boolean; selected: boolean; compact?: boolean; onClick: () => void }) {
  return (
    <button type="button" className={`btn ${compact ? "" : "btn-lg"} justify-between h-auto py-2 ${selected ? "btn-primary" : ""}`} onClick={onClick} aria-pressed={selected}>
      <span className="text-left">
        <span className="block">{c.name}</span>
        <span className="block text-sm font-normal opacity-80">{c.duration_minutes ? fmtMinutes(c.duration_minutes) : "時間無制限"}</span>
      </span>
      <span className="num whitespace-nowrap">
        {yen(c.price)}
        <span className="text-sm">{perPerson ? "/人" : "/組"}</span>
      </span>
    </button>
  );
}
