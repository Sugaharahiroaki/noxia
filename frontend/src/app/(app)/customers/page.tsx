"use client";

import { useState } from "react";
import { ConfirmDialog, Empty, ErrorBox, Field, Modal, PageHeader, SaveStatus, Spinner, Toggle, type SaveState } from "@/components/ui";
import { api, newIdempotencyKey } from "@/lib/api";
import { fmtDate, fmtTime, todayJst, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { CheckoutBrief, Customer, Employee, Product, VisitBrief } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type BottleKeep = {
  id: number;
  customer: number;
  customer_name: string;
  product: number | null;
  name: string;
  location: string;
  registered_on: string;
  expires_on: string | null;
  status: "active" | "consumed" | "expired" | "disposed";
  status_label: string;
  remaining_note: string;
  source_order_line: number | null;
  note: string;
  created_at: string;
};

type History = { customer: Customer; visits: VisitBrief[]; checkouts: CheckoutBrief[]; bottles: BottleKeep[] };

type CustomerForm = { display_name: string; phone: string; contact_note: string; assigned_employee: number | null; memo: string; shared_in_company: boolean };

const emptyForm: CustomerForm = { display_name: "", phone: "", contact_note: "", assigned_employee: null, memo: "", shared_in_company: false };

const BOTTLE_STATUS: { value: BottleKeep["status"]; label: string; cls: string }[] = [
  { value: "active", label: "保管中", cls: "badge-ok" },
  { value: "consumed", label: "飲み切り", cls: "badge-accent" },
  { value: "expired", label: "期限切れ", cls: "badge-warn" },
  { value: "disposed", label: "処分済", cls: "badge-danger" },
];

export default function CustomersPage() {
  const { can } = useSession();
  const canEdit = can("customers.edit");
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState<"customers" | "bottles">("customers");
  const customers = useLoad<Customer[]>(`/api/customers?active=1${search ? `&q=${encodeURIComponent(search)}` : ""}`);
  const employees = useLoad<Employee[]>("/api/staff/employees?active=1");
  const [editing, setEditing] = useState<{ id: number | null; form: CustomerForm } | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });

  async function saveCustomer() {
    if (!editing) return;
    setSave({ kind: "saving" });
    try {
      if (editing.id === null) {
        await api("/api/customers", { method: "POST", body: editing.form, idempotencyKey: newIdempotencyKey() });
      } else {
        await api(`/api/customers/${editing.id}`, { method: "PATCH", body: editing.form });
      }
      setSave({ kind: "saved" });
      setEditing(null);
      await customers.reload();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="顧客・ボトルキープ管理"
        subtitle="顧客情報は必要最小限にとどめます。キープボトルは販売用在庫とは別に管理します（販売時に在庫を減らし、キープ登録では減らしません）。"
        actions={
          <>
            <SaveStatus state={save} />
            <button className={`btn btn-sm ${tab === "customers" ? "btn-primary" : ""}`} onClick={() => setTab("customers")}>
              顧客
            </button>
            <button className={`btn btn-sm ${tab === "bottles" ? "btn-primary" : ""}`} onClick={() => setTab("bottles")}>
              ボトルキープ一覧
            </button>
            {canEdit && tab === "customers" && (
              <button className="btn btn-primary" onClick={() => setEditing({ id: null, form: emptyForm })}>
                ＋ 顧客を登録
              </button>
            )}
          </>
        }
      />

      {tab === "customers" && (
        <>
          <form
            className="flex gap-2 max-w-xl"
            onSubmit={(e) => {
              e.preventDefault();
              setSearch(q.trim());
            }}
          >
            <input className="input" placeholder="表示名で検索" value={q} onChange={(e) => setQ(e.target.value)} />
            <button type="submit" className="btn">
              検索
            </button>
            {search && (
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => {
                  setQ("");
                  setSearch("");
                }}
              >
                解除
              </button>
            )}
          </form>
          <ErrorBox error={customers.error} onRetry={() => customers.reload(false)} />
          {customers.loading && <Spinner />}
          {customers.data?.length === 0 && <Empty>顧客が登録されていません。顧客登録なしでも通常の入店・会計はできます。</Empty>}
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
            {customers.data?.map((c) => (
              <div key={c.id} className="card-2 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-bold text-lg">{c.display_name}</span>
                  {!c.is_own_store && <span className="badge badge-accent">共有 ・ {c.store_name}</span>}
                  {c.is_own_store && c.shared_in_company && <span className="badge badge-accent">会社内共有</span>}
                  {c.active_bottles > 0 && <span className="badge badge-ok">キープ {c.active_bottles}本</span>}
                </div>
                <div className="text-sm" style={{ color: "var(--color-muted)" }}>
                  {c.assigned_employee_name ? `担当 ${c.assigned_employee_name}` : "担当なし"}
                  {c.phone && ` ・ ${c.phone}`}
                </div>
                {c.memo && <div className="text-sm whitespace-pre-wrap">{c.memo}</div>}
                <div className="flex gap-2 pt-1">
                  <button className="btn btn-sm" onClick={() => setDetailId(c.id)}>
                    履歴・キープ
                  </button>
                  {canEdit && c.is_own_store && (
                    <button
                      className="btn btn-sm"
                      onClick={() =>
                        setEditing({
                          id: c.id,
                          form: { display_name: c.display_name, phone: c.phone, contact_note: c.contact_note, assigned_employee: c.assigned_employee, memo: c.memo, shared_in_company: c.shared_in_company },
                        })
                      }
                    >
                      編集
                    </button>
                  )}
                  {!c.is_own_store && (
                    <span className="text-xs self-center" style={{ color: "var(--color-muted)" }}>
                      他店舗の顧客（閲覧のみ）
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {tab === "bottles" && <BottleList canEdit={canEdit} setSave={setSave} />}

      {editing && (
        <Modal
          title={editing.id === null ? "顧客を登録" : "顧客を編集"}
          onClose={() => setEditing(null)}
          footer={
            <>
              {editing.id !== null && <DeactivateButton id={editing.id} name={editing.form.display_name} onDone={() => {
                setEditing(null);
                customers.reload();
              }} />}
              <button className="btn" onClick={() => setEditing(null)}>
                キャンセル
              </button>
              <button className="btn btn-primary" disabled={!editing.form.display_name.trim() || save.kind === "saving"} onClick={saveCustomer}>
                保存
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <Field label="表示名（必須）">
              <input className="input" value={editing.form.display_name} onChange={(e) => setEditing({ ...editing, form: { ...editing.form, display_name: e.target.value } })} />
            </Field>
            <Field label="連絡先（任意）" hint="必要最小限にしてください。">
              <input className="input" value={editing.form.phone} onChange={(e) => setEditing({ ...editing, form: { ...editing.form, phone: e.target.value } })} />
            </Field>
            <Field label="連絡先メモ（任意）">
              <input className="input" value={editing.form.contact_note} onChange={(e) => setEditing({ ...editing, form: { ...editing.form, contact_note: e.target.value } })} />
            </Field>
            <Field label="担当（任意）">
              <select className="input" value={editing.form.assigned_employee ?? ""} onChange={(e) => setEditing({ ...editing, form: { ...editing.form, assigned_employee: e.target.value ? Number(e.target.value) : null } })}>
                <option value="">なし</option>
                {employees.data?.map((emp) => (
                  <option key={emp.id} value={emp.id}>
                    {emp.display_name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="メモ">
              <textarea className="input" value={editing.form.memo} onChange={(e) => setEditing({ ...editing, form: { ...editing.form, memo: e.target.value } })} />
            </Field>
            <Toggle checked={editing.form.shared_in_company} onChange={(v) => setEditing({ ...editing, form: { ...editing.form, shared_in_company: v } })} label="同じ会社の他店舗にも共有する" />
            <p className="text-xs" style={{ color: "var(--color-muted)" }}>
              共有は会社設定で許可されている場合だけ有効です。別会社には共有されません。
            </p>
          </div>
        </Modal>
      )}

      {detailId !== null && <CustomerDetail id={detailId} canEdit={canEdit} onClose={() => setDetailId(null)} setSave={setSave} onChanged={() => customers.reload()} />}
    </div>
  );
}

function DeactivateButton({ id, name, onDone }: { id: number; name: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  return (
    <>
      <button className="btn btn-ghost mr-auto" onClick={() => setOpen(true)}>
        無効化
      </button>
      {open && (
        <ConfirmDialog
          title={`「${name}」を無効化`}
          message="一覧から非表示になります。過去の来店・会計履歴は残ります。"
          confirmLabel="無効化する"
          danger
          busy={busy}
          onCancel={() => setOpen(false)}
          onConfirm={async () => {
            setBusy(true);
            try {
              await api(`/api/customers/${id}`, { method: "DELETE" });
              setOpen(false);
              onDone();
            } catch (e) {
              setErr(e);
            } finally {
              setBusy(false);
            }
          }}
        >
          <ErrorBox error={err} />
        </ConfirmDialog>
      )}
    </>
  );
}

function CustomerDetail({ id, canEdit, onClose, setSave, onChanged }: { id: number; canEdit: boolean; onClose: () => void; setSave: (s: SaveState) => void; onChanged: () => void }) {
  const hist = useLoad<History>(`/api/customers/${id}/history`);
  const [addBottle, setAddBottle] = useState(false);
  const d = hist.data;
  return (
    <Modal title={d ? `${d.customer.display_name} の履歴・キープ` : "読み込み中…"} wide onClose={onClose}>
      <ErrorBox error={hist.error} onRetry={() => hist.reload(false)} />
      {hist.loading && <Spinner />}
      {d && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <section className="space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="font-bold">キープボトル</h3>
              {canEdit && d.customer.is_own_store && (
                <button className="btn btn-sm btn-primary" onClick={() => setAddBottle(true)}>
                  ＋ キープを登録
                </button>
              )}
            </div>
            <p className="text-xs" style={{ color: "var(--color-muted)" }}>
              販売用在庫とは別管理です。ボトル販売時に「キープとして登録」すると在庫は販売分だけ減ります。
            </p>
            {d.bottles.length === 0 && <Empty>キープボトルはありません</Empty>}
            {d.bottles.map((b) => (
              <BottleRow
                key={b.id}
                b={b}
                canEdit={canEdit && d.customer.is_own_store}
                onChanged={async () => {
                  await hist.reload();
                  onChanged();
                }}
                setSave={setSave}
              />
            ))}
          </section>
          <section className="space-y-2">
            <h3 className="font-bold">来店履歴（この店舗）</h3>
            {d.visits.length === 0 && <Empty>来店履歴はありません</Empty>}
            {d.visits.map((v) => (
              <div key={v.id} className="card-2 text-sm">
                <div className="flex justify-between">
                  <span>
                    {fmtDate(v.business_date)} {fmtTime(v.opened_at)} ・ {v.table_name} ・ 伝票#{v.slip_number}
                  </span>
                  <span className="badge">{v.status === "open" ? "営業中" : v.status === "closed" ? "会計済" : "取消"}</span>
                </div>
                <div style={{ color: "var(--color-muted)" }}>
                  {v.guest_count}名{v.primary_employee_name && ` ・ 担当 ${v.primary_employee_name}`} ・ {v.course_entries.map((e) => e.course_name).join("、")}
                </div>
              </div>
            ))}
            <h3 className="font-bold pt-2">会計履歴（この店舗）</h3>
            {d.checkouts.length === 0 && <Empty>会計履歴はありません</Empty>}
            {d.checkouts.map((c) => (
              <div key={c.id} className="card-2 text-sm flex justify-between num">
                <span>
                  {fmtDate(c.business_date)} 会計#{c.number}
                  {c.status === "voided" && <span className="badge badge-danger ml-2">取消</span>}
                </span>
                <span className="font-bold">{yen(c.total)}</span>
              </div>
            ))}
          </section>
        </div>
      )}
      {addBottle && d && (
        <BottleForm
          customerId={d.customer.id}
          onClose={() => setAddBottle(false)}
          onSaved={async () => {
            setAddBottle(false);
            await hist.reload();
            onChanged();
          }}
          setSave={setSave}
        />
      )}
    </Modal>
  );
}

function BottleRow({ b, canEdit, onChanged, setSave }: { b: BottleKeep; canEdit: boolean; onChanged: () => Promise<void>; setSave: (s: SaveState) => void }) {
  const [busy, setBusy] = useState(false);
  const st = BOTTLE_STATUS.find((s) => s.value === b.status);
  async function setStatus(status: BottleKeep["status"]) {
    setBusy(true);
    setSave({ kind: "saving" });
    try {
      await api(`/api/bottle-keeps/${b.id}`, { method: "PATCH", body: { status } });
      setSave({ kind: "saved" });
      await onChanged();
    } catch (e) {
      setSave({ kind: "error", error: e });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="card-2 space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{b.name}</span>
        <span className={`badge ${st?.cls ?? ""}`}>{b.status_label}</span>
        {b.location && <span className="badge">{b.location}</span>}
      </div>
      <div className="text-sm" style={{ color: "var(--color-muted)" }}>
        登録 {fmtDate(b.registered_on)}
        {b.expires_on && ` ・ 期限 ${fmtDate(b.expires_on)}`}
        {b.remaining_note && ` ・ 残量 ${b.remaining_note}`}
        {b.note && ` ・ ${b.note}`}
      </div>
      {canEdit && b.status === "active" && (
        <div className="flex gap-2">
          {BOTTLE_STATUS.filter((s) => s.value !== "active").map((s) => (
            <button key={s.value} className="btn btn-sm" disabled={busy} onClick={() => setStatus(s.value)}>
              {s.label}にする
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function BottleForm({ customerId, onClose, onSaved, setSave }: { customerId: number; onClose: () => void; onSaved: () => Promise<void>; setSave: (s: SaveState) => void }) {
  const products = useLoad<Product[]>("/api/store/products?active=1");
  const [productId, setProductId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [location, setLocation] = useState("");
  const [registered, setRegistered] = useState(todayJst());
  const [expires, setExpires] = useState("");
  const [remaining, setRemaining] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [idem] = useState(() => newIdempotencyKey());
  const keepable = (products.data ?? []).filter((p) => p.is_bottle_keepable);
  return (
    <Modal
      title="キープボトルを登録（手動）"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            キャンセル
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !name.trim()}
            onClick={async () => {
              setBusy(true);
              setSave({ kind: "saving" });
              try {
                await api("/api/bottle-keeps", {
                  method: "POST",
                  idempotencyKey: idem,
                  body: { customer: customerId, product: productId, name: name.trim(), location, registered_on: registered, expires_on: expires || null, remaining_note: remaining, note },
                });
                setSave({ kind: "saved" });
                await onSaved();
              } catch (e) {
                setSave({ kind: "error", error: e });
              } finally {
                setBusy(false);
              }
            }}
          >
            登録する
          </button>
        </>
      }
    >
      <p className="text-xs mb-3" style={{ color: "var(--color-muted)" }}>
        手動登録では販売用在庫は変わりません（持ち込みや既に販売済みのボトル用）。販売と同時に登録する場合は伝票画面のボトル追加から行ってください。
      </p>
      <div className="space-y-3">
        <Field label="商品（任意）">
          <select
            className="input"
            value={productId ?? ""}
            onChange={(e) => {
              const id = e.target.value ? Number(e.target.value) : null;
              setProductId(id);
              const p = keepable.find((x) => x.id === id);
              if (p && !name) setName(p.name);
            }}
          >
            <option value="">選択しない</option>
            {keepable.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="銘柄（必須）">
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="保管場所">
          <input className="input" value={location} onChange={(e) => setLocation(e.target.value)} placeholder="例: 棚A-3" />
        </Field>
        <div className="grid grid-cols-2 gap-2">
          <Field label="登録日">
            <input className="input" type="date" value={registered} onChange={(e) => setRegistered(e.target.value)} />
          </Field>
          <Field label="期限（任意）">
            <input className="input" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
          </Field>
        </div>
        <Field label="残量メモ">
          <input className="input" value={remaining} onChange={(e) => setRemaining(e.target.value)} placeholder="例: 半分" />
        </Field>
        <Field label="メモ">
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

function BottleList({ canEdit, setSave }: { canEdit: boolean; setSave: (s: SaveState) => void }) {
  const [status, setStatus] = useState<string>("active");
  const bottles = useLoad<BottleKeep[]>(`/api/bottle-keeps${status ? `?status=${status}` : ""}`);
  const today = todayJst();
  return (
    <div className="space-y-3">
      <div className="flex gap-2 flex-wrap">
        <button className={`btn btn-sm ${status === "" ? "btn-primary" : ""}`} onClick={() => setStatus("")}>
          すべて
        </button>
        {BOTTLE_STATUS.map((s) => (
          <button key={s.value} className={`btn btn-sm ${status === s.value ? "btn-primary" : ""}`} onClick={() => setStatus(s.value)}>
            {s.label}
          </button>
        ))}
      </div>
      <ErrorBox error={bottles.error} onRetry={() => bottles.reload(false)} />
      {bottles.loading && <Spinner />}
      {bottles.data?.length === 0 && <Empty>該当するキープボトルはありません</Empty>}
      <div className="scroll-x">
        <table className="table">
          <thead>
            <tr>
              <th>顧客</th>
              <th>銘柄</th>
              <th>保管場所</th>
              <th>登録日</th>
              <th>期限</th>
              <th>状態</th>
              <th>残量</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {bottles.data?.map((b) => {
              const expiring = b.status === "active" && b.expires_on && b.expires_on <= today;
              const st = BOTTLE_STATUS.find((s) => s.value === b.status);
              return (
                <tr key={b.id}>
                  <td className="font-semibold">{b.customer_name}</td>
                  <td>{b.name}</td>
                  <td>{b.location || "—"}</td>
                  <td>{fmtDate(b.registered_on)}</td>
                  <td style={expiring ? { color: "var(--color-danger)" } : undefined}>
                    {fmtDate(b.expires_on)}
                    {expiring && " （期限超過）"}
                  </td>
                  <td>
                    <span className={`badge ${st?.cls ?? ""}`}>{b.status_label}</span>
                  </td>
                  <td>{b.remaining_note || "—"}</td>
                  <td>
                    {canEdit && b.status === "active" && (
                      <BottleQuickStatus
                        id={b.id}
                        onChanged={() => bottles.reload()}
                        setSave={setSave}
                      />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs" style={{ color: "var(--color-muted)" }}>
        キープの利用（提供）では販売用在庫を減らしません。
      </p>
    </div>
  );
}

function BottleQuickStatus({ id, onChanged, setSave }: { id: number; onChanged: () => void; setSave: (s: SaveState) => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <select
      className="input"
      style={{ minHeight: 44 }}
      disabled={busy}
      value="active"
      onChange={async (e) => {
        const status = e.target.value;
        if (status === "active") return;
        setBusy(true);
        setSave({ kind: "saving" });
        try {
          await api(`/api/bottle-keeps/${id}`, { method: "PATCH", body: { status } });
          setSave({ kind: "saved" });
          onChanged();
        } catch (err) {
          setSave({ kind: "error", error: err });
        } finally {
          setBusy(false);
        }
      }}
    >
      <option value="active">状態を変更…</option>
      {BOTTLE_STATUS.filter((s) => s.value !== "active").map((s) => (
        <option key={s.value} value={s.value}>
          {s.label}
        </option>
      ))}
    </select>
  );
}
