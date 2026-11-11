"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ConfirmDialog, Empty, ErrorBox, Field, Modal, PageHeader, SaveStatus, Spinner, Toggle, type SaveState } from "@/components/ui";
import { api, newIdempotencyKey } from "@/lib/api";
import { fmtDate, fmtDateTime, fmtMinutes, fmtTime, todayJst, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Course, Customer, Employee, StoreSettings, Table, Visit } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

// --- ローカル型（API のレスポンス） -------------------------------------------
type Reservation = {
  id: number;
  customer: number | null;
  customer_name: string | null;
  guest_name: string;
  reserved_at: string;
  guest_count: number;
  table: number | null;
  table_name: string | null;
  status: "pending" | "confirmed" | "seated" | "cancelled" | "no_show";
  status_label: string;
  employee: number | null;
  employee_name: string | null;
  visit: number | null;
  source: string;
  line_user: number | null;
  note: string;
  created_at: string;
};

type LineIntegration = {
  channel_id: string;
  channel_secret_masked: string;
  channel_access_token_masked: string;
  has_credentials: boolean;
  status: "not_connected" | "mock" | "connected";
  webhook_enabled: boolean;
  send_enabled: boolean;
  official_account_name: string;
  last_verified_at: string | null;
  note: string;
  webhook_path: string | null;
  effective_send_mode: "live" | "mock";
};

type LineUser = {
  id: number;
  line_user_id: string;
  display_name: string;
  consent: "unknown" | "opted_in" | "opted_out";
  consent_label: string;
  consent_changed_at: string | null;
  customer: number | null;
  customer_name: string | null;
  linked_at: string | null;
  is_following: boolean;
  note: string;
  created_at: string;
};

type Template = { id: number; name: string; kind: string; body: string; is_active: boolean };

type Draft = {
  id: number;
  line_user: number;
  line_user_display: string;
  line_user_consent: string;
  template: number | null;
  body: string;
  status: "draft" | "reviewed" | "sent" | "mock_sent" | "failed" | "blocked";
  status_label: string;
  reservation: number | null;
  inquiry: number | null;
  reviewed_at: string | null;
  sent_at: string | null;
  result: { ok?: boolean; mock?: boolean; detail?: string } | Record<string, never>;
  created_at: string;
};

type Inquiry = { id: number; line_user: number; line_user_display: string; text: string; status: "open" | "handled"; handled_at: string | null; reservation: number | null; created_at: string };
type WebhookEvent = { id: number; webhook_event_id: string; event_type: string; received_at: string; processed: boolean; signature_valid: boolean };

const STATUS_BADGE: Record<Reservation["status"], string> = {
  pending: "badge-warn",
  confirmed: "badge-accent",
  seated: "badge-ok",
  cancelled: "badge-danger",
  no_show: "badge-danger",
};

const TEMPLATE_KINDS: { value: string; label: string }[] = [
  { value: "thanks", label: "お礼" },
  { value: "visit_guide", label: "来店案内" },
  { value: "reservation", label: "予約関連" },
  { value: "other", label: "その他" },
];

function toJstIso(date: string, time: string): string {
  return `${date}T${time || "00:00"}:00+09:00`;
}

function splitJst(iso: string): { date: string; time: string } {
  const d = new Date(iso);
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return { date: `${get("year")}-${get("month")}-${get("day")}`, time: `${get("hour") === "24" ? "00" : get("hour")}:${get("minute")}` };
}

export default function ReservationsPage() {
  const { can } = useSession();
  const [tab, setTab] = useState<"reservations" | "line">(can("reservations.operate") ? "reservations" : "line");
  return (
    <div className="space-y-4">
      <PageHeader
        title="予約・LINE連携管理"
        actions={
          <>
            {can("reservations.operate") && (
              <button className={`btn btn-sm ${tab === "reservations" ? "btn-primary" : ""}`} onClick={() => setTab("reservations")}>
                予約
              </button>
            )}
            {can("line.view") && (
              <button className={`btn btn-sm ${tab === "line" ? "btn-primary" : ""}`} onClick={() => setTab("line")}>
                LINE
              </button>
            )}
          </>
        }
      />
      {tab === "reservations" && can("reservations.operate") && <ReservationsTab />}
      {tab === "line" && can("line.view") && <LineTab />}
      {!can("reservations.operate") && !can("line.view") && <Empty>この画面を利用する権限がありません。</Empty>}
    </div>
  );
}

// =============================================================================
// 予約
// =============================================================================
function ReservationsTab() {
  const router = useRouter();
  const [date, setDate] = useState(todayJst());
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [upcoming, setUpcoming] = useState(false);
  const path = useMemo(() => {
    const p = new URLSearchParams();
    if (upcoming) p.set("from", todayJst());
    else p.set("date", date);
    if (statusFilter) p.set("status", statusFilter);
    return `/api/reservations?${p.toString()}`;
  }, [date, statusFilter, upcoming]);
  const list = useLoad<Reservation[]>(path, [], { intervalMs: 60000 });
  const customers = useLoad<Customer[]>("/api/customers?active=1");
  const tables = useLoad<Table[]>("/api/store/tables?active=1");
  const employees = useLoad<Employee[]>("/api/staff/employees?active=1");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [editing, setEditing] = useState<Reservation | "new" | null>(null);
  const [seating, setSeating] = useState<Reservation | null>(null);
  const [cancelling, setCancelling] = useState<{ r: Reservation; status: "cancelled" | "no_show" } | null>(null);

  async function patchStatus(r: Reservation, status: Reservation["status"]) {
    setSave({ kind: "saving" });
    try {
      await api(`/api/reservations/${r.id}`, { method: "PATCH", body: { status } });
      setSave({ kind: "saved" });
      await list.reload();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  return (
    <div className="space-y-3">
      <div className="card flex flex-wrap items-center gap-2">
        <button className={`btn btn-sm ${!upcoming ? "btn-primary" : ""}`} onClick={() => setUpcoming(false)}>
          日付指定
        </button>
        <button className={`btn btn-sm ${upcoming ? "btn-primary" : ""}`} onClick={() => setUpcoming(true)}>
          今日以降すべて
        </button>
        {!upcoming && <input type="date" className="input w-auto" value={date} onChange={(e) => setDate(e.target.value)} />}
        <select className="input w-auto" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">すべての状態</option>
          <option value="pending">仮予約</option>
          <option value="confirmed">確定</option>
          <option value="seated">来店済</option>
          <option value="cancelled">キャンセル</option>
          <option value="no_show">無断キャンセル</option>
        </select>
        <SaveStatus state={save} />
        <div className="ml-auto flex gap-2">
          <button className="btn btn-sm" onClick={() => list.reload(false)}>
            更新
          </button>
          <button className="btn btn-primary" onClick={() => setEditing("new")}>
            ＋ 予約を追加
          </button>
        </div>
      </div>
      <ErrorBox error={list.error} onRetry={() => list.reload(false)} />
      {list.loading && <Spinner />}
      {list.data && list.data.length === 0 && <Empty>予約はありません</Empty>}
      <div className="space-y-2">
        {list.data?.map((r) => (
          <div key={r.id} className="card-2 flex flex-wrap items-center gap-3">
            <div className="text-xl font-bold num w-28">
              {upcoming && <div className="text-xs font-normal" style={{ color: "var(--color-muted)" }}>{fmtDate(splitJst(r.reserved_at).date)}</div>}
              {fmtTime(r.reserved_at)}
            </div>
            <div className="flex-1 min-w-[200px]">
              <div className="font-semibold">
                {r.guest_name} ・ {r.guest_count}名
                {r.customer_name && <span className="badge ml-2">顧客: {r.customer_name}</span>}
                {r.source === "line" && <span className="badge badge-accent ml-2">LINE</span>}
              </div>
              <div className="text-sm" style={{ color: "var(--color-muted)" }}>
                {r.table_name ? `${r.table_name}` : "卓未定"}
                {r.employee_name && ` ・ 担当 ${r.employee_name}`}
                {r.note && ` ・ ${r.note}`}
              </div>
            </div>
            <span className={`badge ${STATUS_BADGE[r.status]}`}>{r.status_label}</span>
            <div className="flex flex-wrap gap-2">
              {(r.status === "pending" || r.status === "confirmed") && (
                <>
                  <button className="btn btn-sm btn-primary" onClick={() => setSeating(r)}>
                    入店登録
                  </button>
                  {r.status === "pending" && (
                    <button className="btn btn-sm" onClick={() => patchStatus(r, "confirmed")}>
                      確定にする
                    </button>
                  )}
                  <button className="btn btn-sm" onClick={() => setEditing(r)}>
                    編集
                  </button>
                  <button className="btn btn-sm btn-ghost" onClick={() => setCancelling({ r, status: "cancelled" })}>
                    キャンセル
                  </button>
                  <button className="btn btn-sm btn-ghost" onClick={() => setCancelling({ r, status: "no_show" })}>
                    無断キャンセル
                  </button>
                </>
              )}
              {r.status === "seated" && r.visit && (
                <button className="btn btn-sm" onClick={() => router.push(`/visits/${r.visit}`)}>
                  伝票を開く
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {editing && (
        <ReservationModal
          reservation={editing === "new" ? null : editing}
          customers={customers.data ?? []}
          tables={tables.data ?? []}
          employees={employees.data ?? []}
          defaultDate={upcoming ? todayJst() : date}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            setSave({ kind: "saved" });
            await list.reload();
          }}
        />
      )}
      {seating && (
        <SeatModal
          reservation={seating}
          tables={tables.data ?? []}
          employees={employees.data ?? []}
          onClose={() => setSeating(null)}
          onSeated={(visit) => {
            setSeating(null);
            router.push(`/visits/${visit.id}`);
          }}
        />
      )}
      {cancelling && (
        <ConfirmDialog
          title={cancelling.status === "cancelled" ? "予約をキャンセルする" : "無断キャンセルとして記録する"}
          message={`${cancelling.r.guest_name} 様 ${fmtDateTime(cancelling.r.reserved_at)} の予約を${cancelling.status === "cancelled" ? "キャンセル" : "無断キャンセル"}にします。`}
          confirmLabel="記録する"
          danger
          onCancel={() => setCancelling(null)}
          onConfirm={async () => {
            await patchStatus(cancelling.r, cancelling.status);
            setCancelling(null);
          }}
        />
      )}
    </div>
  );
}

function ReservationModal({ reservation, customers, tables, employees, defaultDate, onClose, onSaved }: { reservation: Reservation | null; customers: Customer[]; tables: Table[]; employees: Employee[]; defaultDate: string; onClose: () => void; onSaved: () => Promise<void> }) {
  const init = reservation ? splitJst(reservation.reserved_at) : { date: defaultDate, time: "20:00" };
  const [guestName, setGuestName] = useState(reservation?.guest_name ?? "");
  const [customer, setCustomer] = useState<number | null>(reservation?.customer ?? null);
  const [date, setDate] = useState(init.date);
  const [time, setTime] = useState(init.time);
  const [guests, setGuests] = useState(reservation?.guest_count ?? 2);
  const [table, setTable] = useState<number | null>(reservation?.table ?? null);
  const [employee, setEmployee] = useState<number | null>(reservation?.employee ?? null);
  const [status, setStatus] = useState<Reservation["status"]>(reservation?.status ?? "pending");
  const [note, setNote] = useState(reservation?.note ?? "");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [idem] = useState(() => newIdempotencyKey());

  useEffect(() => {
    if (customer && !guestName) {
      const c = customers.find((x) => x.id === customer);
      if (c) setGuestName(c.display_name);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customer]);

  async function submit() {
    setSave({ kind: "saving" });
    const body = { guest_name: guestName.trim(), customer, reserved_at: toJstIso(date, time), guest_count: guests, table, employee, status, note };
    try {
      if (reservation) await api(`/api/reservations/${reservation.id}`, { method: "PATCH", body });
      else await api("/api/reservations", { method: "POST", body, idempotencyKey: idem });
      await onSaved();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  return (
    <Modal
      title={reservation ? "予約を編集" : "予約を追加"}
      onClose={onClose}
      footer={
        <>
          <SaveStatus state={save} />
          <button className="btn" onClick={onClose}>
            キャンセル
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={!guestName.trim() || !date || save.kind === "saving"}>
            保存する
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="顧客（任意・登録済み）">
          <select className="input" value={customer ?? ""} onChange={(e) => setCustomer(e.target.value ? Number(e.target.value) : null)}>
            <option value="">登録なし</option>
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.display_name}
                {!c.is_own_store ? `（${c.store_name}）` : ""}
              </option>
            ))}
          </select>
        </Field>
        <Field label="予約名（必須）">
          <input className="input" value={guestName} onChange={(e) => setGuestName(e.target.value)} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="日付">
            <input type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label="時刻">
            <input type="time" className="input" value={time} onChange={(e) => setTime(e.target.value)} />
          </Field>
        </div>
        <div>
          <span className="label">人数</span>
          <div className="flex items-center gap-2">
            <button type="button" className="btn btn-sm" onClick={() => setGuests(Math.max(1, guests - 1))}>
              −
            </button>
            <span className="text-2xl font-bold num w-10 text-center">{guests}</span>
            <button type="button" className="btn btn-sm" onClick={() => setGuests(guests + 1)}>
              ＋
            </button>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="卓（任意）">
            <select className="input" value={table ?? ""} onChange={(e) => setTable(e.target.value ? Number(e.target.value) : null)}>
              <option value="">未定</option>
              {tables.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="担当（任意）">
            <select className="input" value={employee ?? ""} onChange={(e) => setEmployee(e.target.value ? Number(e.target.value) : null)}>
              <option value="">なし</option>
              {employees.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.display_name}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field label="状態">
          <select className="input" value={status} onChange={(e) => setStatus(e.target.value as Reservation["status"])}>
            <option value="pending">仮予約</option>
            <option value="confirmed">確定</option>
          </select>
        </Field>
        <Field label="メモ（任意）">
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

type EntryDraft = { key: string; label: string; guest_count: number; course_id: number | null; employee_id: number | null };

function SeatModal({ reservation, tables, employees, onClose, onSeated }: { reservation: Reservation; tables: Table[]; employees: Employee[]; onClose: () => void; onSeated: (v: Visit) => void }) {
  const courses = useLoad<Course[]>("/api/store/courses?active=1");
  const settings = useLoad<StoreSettings>("/api/store/settings");
  const [tableId, setTableId] = useState<number | null>(reservation.table);
  const [entries, setEntries] = useState<EntryDraft[]>([{ key: "e1", label: reservation.guest_name, guest_count: reservation.guest_count, course_id: null, employee_id: reservation.employee }]);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [idem] = useState(() => newIdempotencyKey());
  const perPerson = settings.data?.price_basis !== "per_group";

  useEffect(() => {
    const first = courses.data?.[0];
    if (first) setEntries((es) => es.map((e) => (e.course_id === null ? { ...e, course_id: first.id } : e)));
  }, [courses.data]);

  function update(key: string, patch: Partial<EntryDraft>) {
    setEntries((es) => es.map((e) => (e.key === key ? { ...e, ...patch } : e)));
  }

  async function submit() {
    if (!tableId) return setSave({ kind: "error", error: new Error("卓を選択してください。") });
    if (entries.some((e) => !e.course_id)) return setSave({ kind: "error", error: new Error("コースを選択してください。") });
    setSave({ kind: "saving", label: "入店を登録中…" });
    try {
      const v = await api<Visit>(`/api/reservations/${reservation.id}/seat`, {
        method: "POST",
        idempotencyKey: idem,
        body: { table_id: tableId, entries: entries.map((e) => ({ label: e.label, guest_count: e.guest_count, course_id: e.course_id, employee_id: e.employee_id })) },
      });
      setSave({ kind: "saved" });
      onSeated(v);
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  return (
    <Modal
      title={`予約から入店登録: ${reservation.guest_name} 様（${fmtDateTime(reservation.reserved_at)}）`}
      wide
      onClose={onClose}
      footer={
        <>
          <SaveStatus state={save} />
          <button className="btn" onClick={onClose}>
            キャンセル
          </button>
          <button className="btn btn-primary btn-lg" onClick={submit} disabled={save.kind === "saving" || courses.loading}>
            入店を登録する
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <span className="label">卓</span>
          <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
            {tables.map((t) => (
              <button key={t.id} type="button" className={`btn btn-sm ${tableId === t.id ? "btn-primary" : ""}`} onClick={() => setTableId(t.id)} aria-pressed={tableId === t.id}>
                {t.name}
              </button>
            ))}
          </div>
        </div>
        {courses.loading && <Spinner label="コースを読み込み中…" />}
        {entries.map((e, idx) => (
          <div key={e.key} className="card-2 grid grid-cols-1 md:grid-cols-12 gap-3 items-end">
            <div className="md:col-span-3">
              <Field label={`対象 ${idx + 1} の名前（任意）`}>
                <input className="input" value={e.label} onChange={(ev) => update(e.key, { label: ev.target.value })} />
              </Field>
            </div>
            <div className="md:col-span-2">
              <Field label="人数">
                <div className="flex items-center gap-2">
                  <button type="button" className="btn btn-sm" onClick={() => update(e.key, { guest_count: Math.max(1, e.guest_count - 1) })}>
                    −
                  </button>
                  <span className="text-2xl font-bold w-10 text-center num">{e.guest_count}</span>
                  <button type="button" className="btn btn-sm" onClick={() => update(e.key, { guest_count: e.guest_count + 1 })}>
                    ＋
                  </button>
                </div>
              </Field>
            </div>
            <div className="md:col-span-4">
              <span className="label">コース</span>
              <div className="grid grid-cols-1 gap-2">
                {courses.data?.map((c) => (
                  <button key={c.id} type="button" className={`btn btn-sm justify-between ${e.course_id === c.id ? "btn-primary" : ""}`} onClick={() => update(e.key, { course_id: c.id })} aria-pressed={e.course_id === c.id}>
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
            <div className="md:col-span-2">
              <Field label="担当（任意）">
                <select className="input" value={e.employee_id ?? ""} onChange={(ev) => update(e.key, { employee_id: ev.target.value ? Number(ev.target.value) : null })}>
                  <option value="">なし</option>
                  {employees.map((emp) => (
                    <option key={emp.id} value={emp.id}>
                      {emp.display_name}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <div className="md:col-span-1 flex justify-end">
              {entries.length > 1 && (
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => setEntries((es) => es.filter((x) => x.key !== e.key))}>
                  削除
                </button>
              )}
            </div>
          </div>
        ))}
        <button type="button" className="btn btn-sm" onClick={() => setEntries((es) => [...es, { key: `e${Date.now()}`, label: "", guest_count: 1, course_id: courses.data?.[0]?.id ?? null, employee_id: null }])}>
          ＋ 対象を追加（別コース）
        </button>
      </div>
    </Modal>
  );
}

// =============================================================================
// LINE
// =============================================================================
function LineTab() {
  const { can } = useSession();
  const [section, setSection] = useState<"status" | "users" | "drafts" | "templates" | "inquiries" | "events">("status");
  const sections: { key: typeof section; label: string }[] = [
    { key: "status", label: "接続状態" },
    { key: "users", label: "LINE利用者・紐付け" },
    { key: "drafts", label: "メッセージ下書き・送信" },
    { key: "templates", label: "テンプレート" },
    { key: "inquiries", label: "問い合わせ" },
    { key: "events", label: "Webhook受信" },
  ];
  return (
    <div className="space-y-3">
      <div className="flex gap-2 scroll-x pb-1">
        {sections.map((s) => (
          <button key={s.key} className={`btn btn-sm whitespace-nowrap ${section === s.key ? "btn-primary" : ""}`} onClick={() => setSection(s.key)}>
            {s.label}
          </button>
        ))}
      </div>
      {section === "status" && <IntegrationSection canManage={can("line.manage")} />}
      {section === "users" && <LineUsersSection canManage={can("line.manage")} />}
      {section === "drafts" && <DraftsSection canManage={can("line.manage")} canSend={can("line.send")} />}
      {section === "templates" && <TemplatesSection canManage={can("line.manage")} />}
      {section === "inquiries" && <InquiriesSection canManage={can("line.manage")} />}
      {section === "events" && <EventsSection />}
    </div>
  );
}

function IntegrationSection({ canManage }: { canManage: boolean }) {
  const { withReauth } = useSession();
  const q = useLoad<LineIntegration>("/api/line/integration");
  const [channelId, setChannelId] = useState("");
  const [secret, setSecret] = useState("");
  const [token, setToken] = useState("");
  const [accountName, setAccountName] = useState("");
  const [webhook, setWebhook] = useState(false);
  const [sendEnabled, setSendEnabled] = useState(false);
  const [note, setNote] = useState("");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });

  useEffect(() => {
    if (q.data) {
      setChannelId(q.data.channel_id);
      setAccountName(q.data.official_account_name);
      setWebhook(q.data.webhook_enabled);
      setSendEnabled(q.data.send_enabled);
      setNote(q.data.note);
    }
  }, [q.data]);

  async function submit() {
    setSave({ kind: "saving" });
    try {
      const body: Record<string, unknown> = { channel_id: channelId, official_account_name: accountName, webhook_enabled: webhook, send_enabled: sendEnabled, note };
      if (secret) body.channel_secret = secret;
      if (token) body.channel_access_token = token;
      const r = await withReauth(() => api<LineIntegration>("/api/line/integration", { method: "PUT", body }));
      q.setData(r);
      setSecret("");
      setToken("");
      setSave({ kind: "saved" });
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  if (q.loading) return <Spinner />;
  if (q.error || !q.data) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const d = q.data;
  const connected = d.has_credentials && d.status === "connected";
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <section className="card space-y-3">
        <h2 className="font-bold">接続状態</h2>
        <div className="flex flex-wrap gap-2">
          <span className={`badge ${connected ? "badge-ok" : "badge-danger"}`}>{connected ? "接続情報あり（connected）" : "未接続"}</span>
          <span className={`badge ${d.effective_send_mode === "live" ? "badge-ok" : "badge-warn"}`}>{d.effective_send_mode === "live" ? "実送信モード" : "モック送信（実送信なし）"}</span>
          <span className={`badge ${d.webhook_enabled ? "badge-accent" : ""}`}>Webhook {d.webhook_enabled ? "有効" : "無効"}</span>
        </div>
        <ul className="text-sm space-y-1" style={{ color: "var(--color-muted)" }}>
          <li>チャネルID: {d.channel_id || "未設定"}</li>
          <li>チャネルシークレット: {d.channel_secret_masked || "未設定"}</li>
          <li>アクセストークン: {d.channel_access_token_masked || "未設定"}</li>
          <li>公式アカウント名: {d.official_account_name || "—"}</li>
          <li>Webhook URL パス: {d.webhook_path ? <code>{d.webhook_path}</code> : "—"}（LINE Developers の Webhook 設定にサーバーのURL＋このパスを登録）</li>
        </ul>
        <div className="card-2 text-sm space-y-1">
          <p>
            実際にお客様へ送信されるのは、①認証情報が揃っている ②この店舗の「送信を許可」がON ③サーバーの環境変数 <code>LINE_SEND_ENABLED=1</code> の3つがすべて満たされたときだけです。
          </p>
          <p>それ以外は「モック送信」として記録のみ行い、実送信はしません。開発・テスト中はサーバー側を OFF のままにしてください。</p>
          <p>Webhook は署名検証（X-Line-Signature）と webhookEventId による重複排除を行います。過去の全トークや友だち一覧の取得はできず、受信したイベントだけを保持します。</p>
        </div>
      </section>
      <section className="card space-y-3">
        <h2 className="font-bold">接続設定（店舗別・パスワード再確認が必要）</h2>
        {!canManage && <p style={{ color: "var(--color-muted)" }}>変更には LINE 連携の管理権限が必要です。</p>}
        <Field label="チャネルID">
          <input className="input" value={channelId} onChange={(e) => setChannelId(e.target.value)} disabled={!canManage} />
        </Field>
        <Field label="チャネルシークレット（変更時のみ入力）" hint="サーバー側でのみ保持し、画面には末尾だけ表示します">
          <input className="input" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} disabled={!canManage} />
        </Field>
        <Field label="チャネルアクセストークン（変更時のみ入力）">
          <input className="input" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} disabled={!canManage} />
        </Field>
        <Field label="公式アカウント名（表示用）">
          <input className="input" value={accountName} onChange={(e) => setAccountName(e.target.value)} disabled={!canManage} />
        </Field>
        {canManage && (
          <>
            <Toggle checked={webhook} onChange={setWebhook} label="Webhook 受信を有効にする" />
            <Toggle checked={sendEnabled} onChange={setSendEnabled} label="この店舗からの送信を許可する" />
          </>
        )}
        <Field label="メモ">
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} disabled={!canManage} />
        </Field>
        {canManage && (
          <div className="flex items-center gap-3 justify-end">
            <SaveStatus state={save} />
            <button className="btn btn-primary" onClick={submit} disabled={save.kind === "saving"}>
              保存する
            </button>
          </div>
        )}
      </section>
    </div>
  );
}

function LineUsersSection({ canManage }: { canManage: boolean }) {
  const users = useLoad<LineUser[]>("/api/line/users");
  const customers = useLoad<Customer[]>("/api/customers?active=1");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [linking, setLinking] = useState<LineUser | null>(null);
  const [linkCustomer, setLinkCustomer] = useState<number | null>(null);
  const [manual, setManual] = useState(false);
  const [newId, setNewId] = useState("");
  const [newName, setNewName] = useState("");

  async function patch(u: LineUser, body: Record<string, unknown>) {
    setSave({ kind: "saving" });
    try {
      await api(`/api/line/users/${u.id}`, { method: "PATCH", body });
      setSave({ kind: "saved" });
      await users.reload();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  return (
    <div className="space-y-3">
      <div className="card flex flex-wrap items-center gap-2">
        <p className="text-sm flex-1" style={{ color: "var(--color-muted)" }}>
          友だち追加（follow）などの Webhook で登録された LINE 利用者です。顧客との紐付けはスタッフが明示的に行います（自動で一致させません）。配信は「配信同意」の利用者にだけ行えます。
        </p>
        <SaveStatus state={save} />
        {canManage && (
          <button className="btn btn-sm" onClick={() => setManual((v) => !v)}>
            手動登録（開発用）
          </button>
        )}
      </div>
      {manual && canManage && (
        <div className="card-2 grid grid-cols-1 md:grid-cols-3 gap-2 items-end">
          <Field label="LINE ユーザーID（Uで始まる33桁）">
            <input className="input" value={newId} onChange={(e) => setNewId(e.target.value)} />
          </Field>
          <Field label="表示名">
            <input className="input" value={newName} onChange={(e) => setNewName(e.target.value)} />
          </Field>
          <button
            className="btn btn-primary"
            disabled={!newId.trim()}
            onClick={async () => {
              setSave({ kind: "saving" });
              try {
                await api("/api/line/users", { method: "POST", idempotencyKey: newIdempotencyKey(), body: { line_user_id: newId.trim(), display_name: newName } });
                setSave({ kind: "saved" });
                setNewId("");
                setNewName("");
                await users.reload();
              } catch (e) {
                setSave({ kind: "error", error: e });
              }
            }}
          >
            登録
          </button>
        </div>
      )}
      <ErrorBox error={users.error} onRetry={() => users.reload(false)} />
      {users.loading && <Spinner />}
      {users.data?.length === 0 && <Empty>LINE 利用者はまだありません</Empty>}
      <div className="scroll-x">
        <table className="table">
          <thead>
            <tr>
              <th>表示名</th>
              <th>LINE ID</th>
              <th>配信同意</th>
              <th>友だち</th>
              <th>紐付け顧客</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {users.data?.map((u) => (
              <tr key={u.id}>
                <td>
                  {u.display_name || "（名前なし）"}
                  {u.note && (
                    <div className="text-xs" style={{ color: "var(--color-muted)" }}>
                      {u.note}
                    </div>
                  )}
                </td>
                <td className="num text-xs" style={{ color: "var(--color-muted)" }}>
                  {u.line_user_id.slice(0, 6)}…{u.line_user_id.slice(-4)}
                </td>
                <td>
                  {canManage ? (
                    <select className="input w-auto" value={u.consent} onChange={(e) => patch(u, { consent: e.target.value })}>
                      <option value="unknown">未確認</option>
                      <option value="opted_in">配信同意</option>
                      <option value="opted_out">配信停止</option>
                    </select>
                  ) : (
                    <span className={`badge ${u.consent === "opted_in" ? "badge-ok" : u.consent === "opted_out" ? "badge-danger" : ""}`}>{u.consent_label}</span>
                  )}
                </td>
                <td>{u.is_following ? <span className="badge badge-ok">友だち</span> : <span className="badge badge-danger">ブロック・解除</span>}</td>
                <td>
                  {u.customer_name ? (
                    <span className="badge badge-accent">{u.customer_name}</span>
                  ) : (
                    <span style={{ color: "var(--color-muted)" }}>未紐付け</span>
                  )}
                  {u.linked_at && (
                    <div className="text-xs" style={{ color: "var(--color-muted)" }}>
                      {fmtDateTime(u.linked_at)}
                    </div>
                  )}
                </td>
                <td>
                  {canManage && (
                    <button
                      className="btn btn-sm"
                      onClick={() => {
                        setLinking(u);
                        setLinkCustomer(u.customer);
                      }}
                    >
                      顧客を紐付け
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {linking && (
        <Modal
          title={`「${linking.display_name || linking.line_user_id}」と顧客を紐付け`}
          onClose={() => setLinking(null)}
          footer={
            <>
              <button className="btn" onClick={() => setLinking(null)}>
                キャンセル
              </button>
              <button
                className="btn btn-primary"
                onClick={async () => {
                  await patch(linking, { customer: linkCustomer });
                  setLinking(null);
                }}
              >
                保存する
              </button>
            </>
          }
        >
          <Field label="顧客" hint="この店舗で参照できる顧客だけ選べます。解除する場合は「紐付けなし」を選びます。">
            <select className="input" value={linkCustomer ?? ""} onChange={(e) => setLinkCustomer(e.target.value ? Number(e.target.value) : null)}>
              <option value="">紐付けなし</option>
              {customers.data?.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.display_name}
                  {!c.is_own_store ? `（${c.store_name}）` : ""}
                </option>
              ))}
            </select>
          </Field>
        </Modal>
      )}
    </div>
  );
}

function TemplatesSection({ canManage }: { canManage: boolean }) {
  const q = useLoad<Template[]>("/api/line/templates");
  const [editing, setEditing] = useState<Template | "new" | null>(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("thanks");
  const [body, setBody] = useState("");
  const [active, setActive] = useState(true);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });

  function open(t: Template | "new") {
    setEditing(t);
    setName(t === "new" ? "" : t.name);
    setKind(t === "new" ? "thanks" : t.kind);
    setBody(t === "new" ? "" : t.body);
    setActive(t === "new" ? true : t.is_active);
  }

  async function submit() {
    setSave({ kind: "saving" });
    try {
      const payload = { name: name.trim(), kind, body, is_active: active };
      if (editing === "new") await api("/api/line/templates", { method: "POST", idempotencyKey: newIdempotencyKey(), body: payload });
      else if (editing) await api(`/api/line/templates/${editing.id}`, { method: "PATCH", body: payload });
      setSave({ kind: "saved" });
      setEditing(null);
      await q.reload();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  return (
    <div className="space-y-3">
      <div className="card flex flex-wrap items-center gap-2">
        <p className="text-sm flex-1" style={{ color: "var(--color-muted)" }}>
          本文の <code>{"{name}"}</code> は紐付け顧客名（なければ LINE の表示名）、<code>{"{store}"}</code> は店舗名に置き換わります。
        </p>
        <SaveStatus state={save} />
        {canManage && (
          <button className="btn btn-primary" onClick={() => open("new")}>
            ＋ テンプレート
          </button>
        )}
      </div>
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {q.loading && <Spinner />}
      {q.data?.length === 0 && <Empty>テンプレートはありません</Empty>}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {q.data?.map((t) => (
          <div key={t.id} className="card-2 space-y-1" style={{ opacity: t.is_active ? 1 : 0.6 }}>
            <div className="flex items-center gap-2">
              <span className="badge">{TEMPLATE_KINDS.find((k) => k.value === t.kind)?.label ?? t.kind}</span>
              <span className="font-semibold flex-1">{t.name}</span>
              {!t.is_active && <span className="badge">停止中</span>}
              {canManage && (
                <button className="btn btn-sm" onClick={() => open(t)}>
                  編集
                </button>
              )}
            </div>
            <p className="text-sm whitespace-pre-wrap">{t.body}</p>
          </div>
        ))}
      </div>
      {editing && (
        <Modal
          title={editing === "new" ? "テンプレートを追加" : "テンプレートを編集"}
          onClose={() => setEditing(null)}
          footer={
            <>
              <button className="btn" onClick={() => setEditing(null)}>
                キャンセル
              </button>
              <button className="btn btn-primary" onClick={submit} disabled={!name.trim() || !body.trim() || save.kind === "saving"}>
                保存する
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <Field label="名前">
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="種別">
              <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
                {TEMPLATE_KINDS.map((k) => (
                  <option key={k.value} value={k.value}>
                    {k.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="本文">
              <textarea className="input" rows={5} value={body} onChange={(e) => setBody(e.target.value)} />
            </Field>
            <Toggle checked={active} onChange={setActive} label="有効" />
          </div>
        </Modal>
      )}
    </div>
  );
}

const DRAFT_BADGE: Record<Draft["status"], string> = {
  draft: "",
  reviewed: "badge-accent",
  sent: "badge-ok",
  mock_sent: "badge-warn",
  failed: "badge-danger",
  blocked: "badge-danger",
};

function DraftsSection({ canManage, canSend }: { canManage: boolean; canSend: boolean }) {
  const drafts = useLoad<Draft[]>("/api/line/drafts");
  const users = useLoad<LineUser[]>("/api/line/users");
  const templates = useLoad<Template[]>("/api/line/templates");
  const integration = useLoad<LineIntegration>("/api/line/integration");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [creating, setCreating] = useState(false);
  const [lineUser, setLineUser] = useState<number | null>(null);
  const [template, setTemplate] = useState<number | null>(null);
  const [body, setBody] = useState("");
  const [sending, setSending] = useState<Draft | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const mock = integration.data?.effective_send_mode !== "live";

  async function act(d: Draft, action: "review" | "send") {
    setBusy(d.id);
    setSave({ kind: "saving", label: action === "send" ? "送信中…" : "確認中…" });
    try {
      await api(`/api/line/drafts/${d.id}/${action}`, { method: "POST", body: {}, idempotencyKey: action === "send" ? newIdempotencyKey() : undefined });
      setSave({ kind: "saved" });
    } catch (e) {
      setSave({ kind: "error", error: e });
    } finally {
      setBusy(null);
      await drafts.reload();
    }
  }

  async function create() {
    if (!lineUser) return;
    setSave({ kind: "saving" });
    try {
      await api("/api/line/drafts", { method: "POST", idempotencyKey: newIdempotencyKey(), body: { line_user: lineUser, template, body } });
      setSave({ kind: "saved" });
      setCreating(false);
      setBody("");
      setTemplate(null);
      await drafts.reload();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  return (
    <div className="space-y-3">
      <div className="card flex flex-wrap items-center gap-2">
        <div className="flex-1 text-sm" style={{ color: "var(--color-muted)" }}>
          流れ: 下書き → 送信前確認 → 送信。配信同意のある利用者にだけ送れます。
          {mock && <span className="badge badge-warn ml-2">現在はモック送信（実送信なし）</span>}
        </div>
        <SaveStatus state={save} />
        {canManage && (
          <button className="btn btn-primary" onClick={() => setCreating(true)}>
            ＋ 下書きを作成
          </button>
        )}
      </div>
      <ErrorBox error={drafts.error} onRetry={() => drafts.reload(false)} />
      {drafts.loading && <Spinner />}
      {drafts.data?.length === 0 && <Empty>下書きはありません</Empty>}
      <div className="space-y-2">
        {drafts.data?.map((d) => (
          <div key={d.id} className="card-2 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`badge ${DRAFT_BADGE[d.status]}`}>{d.status_label}</span>
              <span className="font-semibold">宛先: {d.line_user_display || `利用者#${d.line_user}`}</span>
              {d.line_user_consent !== "opted_in" && <span className="badge badge-danger">配信同意なし</span>}
              <span className="text-xs" style={{ color: "var(--color-muted)" }}>
                作成 {fmtDateTime(d.created_at)}
                {d.reviewed_at && ` ・ 確認 ${fmtDateTime(d.reviewed_at)}`}
                {d.sent_at && ` ・ 送信 ${fmtDateTime(d.sent_at)}`}
              </span>
              <div className="ml-auto flex gap-2">
                {canManage && d.status === "draft" && (
                  <button className="btn btn-sm" disabled={busy === d.id} onClick={() => act(d, "review")}>
                    送信前確認
                  </button>
                )}
                {canSend && d.status === "reviewed" && (
                  <button className="btn btn-sm btn-primary" disabled={busy === d.id} onClick={() => setSending(d)}>
                    {mock ? "モック送信" : "送信"}
                  </button>
                )}
              </div>
            </div>
            <p className="text-sm whitespace-pre-wrap">{d.body}</p>
            {d.result && "detail" in d.result && d.result.detail && (
              <p className="text-xs" style={{ color: d.result.ok ? "var(--color-muted)" : "var(--color-danger)" }}>
                結果: {d.result.mock ? "モック送信（実送信なし） " : ""}
                {d.result.detail}
              </p>
            )}
          </div>
        ))}
      </div>

      {creating && (
        <Modal
          title="メッセージ下書きを作成"
          onClose={() => setCreating(false)}
          footer={
            <>
              <button className="btn" onClick={() => setCreating(false)}>
                キャンセル
              </button>
              <button className="btn btn-primary" onClick={create} disabled={!lineUser || !body.trim() || save.kind === "saving"}>
                下書きを保存
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <Field label="宛先（LINE 利用者）">
              <select className="input" value={lineUser ?? ""} onChange={(e) => setLineUser(e.target.value ? Number(e.target.value) : null)}>
                <option value="">選択してください</option>
                {users.data?.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.display_name || u.line_user_id}
                    {u.customer_name ? `（${u.customer_name}）` : ""}
                    {u.consent !== "opted_in" ? " ※配信同意なし" : ""}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="テンプレート（任意）">
              <select
                className="input"
                value={template ?? ""}
                onChange={(e) => {
                  const id = e.target.value ? Number(e.target.value) : null;
                  setTemplate(id);
                  const t = templates.data?.find((x) => x.id === id);
                  if (t) setBody(t.body);
                }}
              >
                <option value="">使わない</option>
                {templates.data
                  ?.filter((t) => t.is_active)
                  .map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label="本文" hint="{name} / {store} は保存時に置き換わります">
              <textarea className="input" rows={5} value={body} onChange={(e) => setBody(e.target.value)} />
            </Field>
          </div>
        </Modal>
      )}

      {sending && (
        <ConfirmDialog
          title={mock ? "モック送信（実送信なし）" : "LINE で送信する"}
          confirmLabel={mock ? "モック送信する" : "送信する"}
          busy={busy === sending.id}
          onCancel={() => setSending(null)}
          onConfirm={async () => {
            await act(sending, "send");
            setSending(null);
          }}
        >
          <p className="mb-2">
            宛先: <strong>{sending.line_user_display || `利用者#${sending.line_user}`}</strong>
          </p>
          <p className="card-2 text-sm whitespace-pre-wrap">{sending.body}</p>
          {mock ? (
            <p className="text-sm mt-2" style={{ color: "var(--color-warn)" }}>
              未接続または送信が許可されていないため、記録のみ行い実際には送信しません。
            </p>
          ) : (
            <p className="text-sm mt-2" style={{ color: "var(--color-danger)" }}>
              実際のお客様の LINE に送信されます。内容をよく確認してください。
            </p>
          )}
        </ConfirmDialog>
      )}
    </div>
  );
}

function InquiriesSection({ canManage }: { canManage: boolean }) {
  const q = useLoad<Inquiry[]>("/api/line/inquiries");
  const reservations = useLoad<Reservation[]>(`/api/reservations?from=${todayJst()}`);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  async function patch(i: Inquiry, body: Record<string, unknown>) {
    setSave({ kind: "saving" });
    try {
      await api(`/api/line/inquiries/${i.id}`, { method: "PATCH", body });
      setSave({ kind: "saved" });
      await q.reload();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }
  return (
    <div className="space-y-3">
      <div className="card flex items-center gap-2">
        <p className="text-sm flex-1" style={{ color: "var(--color-muted)" }}>
          Webhook で受信したメッセージです。対応したら「対応済み」にし、必要なら予約と関連付けます。
        </p>
        <SaveStatus state={save} />
      </div>
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {q.loading && <Spinner />}
      {q.data?.length === 0 && <Empty>問い合わせはありません</Empty>}
      {q.data?.map((i) => (
        <div key={i.id} className="card-2 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`badge ${i.status === "handled" ? "badge-ok" : "badge-warn"}`}>{i.status === "handled" ? "対応済み" : "未対応"}</span>
            <span className="font-semibold">{i.line_user_display || `利用者#${i.line_user}`}</span>
            <span className="text-xs" style={{ color: "var(--color-muted)" }}>
              {fmtDateTime(i.created_at)}
            </span>
            {canManage && (
              <div className="ml-auto flex gap-2 items-center">
                <select className="input w-auto" value={i.reservation ?? ""} onChange={(e) => patch(i, { reservation: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">予約と関連付けなし</option>
                  {reservations.data?.map((r) => (
                    <option key={r.id} value={r.id}>
                      {fmtDateTime(r.reserved_at)} {r.guest_name}
                    </option>
                  ))}
                </select>
                {i.status === "open" ? (
                  <button className="btn btn-sm" onClick={() => patch(i, { status: "handled" })}>
                    対応済みにする
                  </button>
                ) : (
                  <button className="btn btn-sm btn-ghost" onClick={() => patch(i, { status: "open" })}>
                    未対応に戻す
                  </button>
                )}
              </div>
            )}
          </div>
          <p className="whitespace-pre-wrap">{i.text}</p>
        </div>
      ))}
    </div>
  );
}

function EventsSection() {
  const q = useLoad<WebhookEvent[]>("/api/line/webhook-events");
  return (
    <div className="space-y-3">
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {q.loading && <Spinner />}
      {q.data?.length === 0 && <Empty>Webhook の受信はまだありません</Empty>}
      <div className="scroll-x">
        <table className="table">
          <thead>
            <tr>
              <th>受信日時</th>
              <th>種別</th>
              <th>イベントID</th>
              <th>処理</th>
            </tr>
          </thead>
          <tbody>
            {q.data?.map((e) => (
              <tr key={e.id}>
                <td>{fmtDateTime(e.received_at)}</td>
                <td>{e.event_type}</td>
                <td className="text-xs num">{e.webhook_event_id}</td>
                <td>{e.processed ? <span className="badge badge-ok">処理済</span> : <span className="badge">未処理</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
