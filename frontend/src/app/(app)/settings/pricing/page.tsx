"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Empty, ErrorBox, Field, Modal, PageHeader, SaveStatus, Spinner, Toggle, type SaveState } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate, fmtMinutes, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Category, Course, Employee, Product, StoreSettings } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type BackRule = {
  id: number;
  employee: number | null;
  employee_name: string | null;
  name: string;
  target_type: string;
  category_kind: string;
  category: number | null;
  category_name: string | null;
  product: number | null;
  product_name: string | null;
  calc_type: "fixed" | "percent";
  value: string;
  basis: string;
  include_discount: boolean;
  include_service_charge: boolean;
  rounding: string;
  confirm_timing: string;
  effective_from: string;
  effective_to: string | null;
  is_active: boolean;
  note: string;
};

const TARGET_LABELS: Record<string, string> = { all_products: "全商品", category_kind: "カテゴリ種別", category: "カテゴリ", product: "商品", nomination: "指名", douhan: "同伴" };
const KIND_LABELS: Record<string, string> = { drink: "ドリンク", cast_drink: "キャストドリンク", bottle: "ボトル", shot: "ショット", food: "フード", other: "その他" };

export default function PricingPage() {
  const { can, withReauth } = useSession();
  const [tab, setTab] = useState<"courses" | "fees" | "backs">("courses");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  return (
    <div className="space-y-4">
      <PageHeader
        title="時給・バック・料金設定"
        subtitle="価格・税率・バック条件は取引時点で明細に保存されるため、ここを変更しても過去の金額・確定済みの集計は変わりません。時給は従業員管理から設定します。"
        actions={
          <>
            <SaveStatus state={save} />
            <button className={`btn btn-sm ${tab === "courses" ? "btn-primary" : ""}`} onClick={() => setTab("courses")}>
              コース
            </button>
            <button className={`btn btn-sm ${tab === "fees" ? "btn-primary" : ""}`} onClick={() => setTab("fees")}>
              無制限変更・指名・同伴
            </button>
            {can("backs.rules.view") && (
              <button className={`btn btn-sm ${tab === "backs" ? "btn-primary" : ""}`} onClick={() => setTab("backs")}>
                バック設定
              </button>
            )}
            <Link href="/employees" className="btn btn-sm">
              時給（従業員管理）
            </Link>
          </>
        }
      />
      {tab === "courses" && <CoursesTab canEdit={can("catalog.edit")} setSave={setSave} />}
      {tab === "fees" && <FeesTab canEdit={can("settings.edit")} setSave={setSave} withReauth={withReauth} />}
      {tab === "backs" && <BacksTab canEdit={can("backs.rules.edit")} setSave={setSave} withReauth={withReauth} />}
    </div>
  );
}

function CoursesTab({ canEdit, setSave }: { canEdit: boolean; setSave: (s: SaveState) => void }) {
  const q = useLoad<Course[]>("/api/store/courses");
  const [editing, setEditing] = useState<Course | "new" | null>(null);
  const [form, setForm] = useState({ name: "", price: "", unlimited: false, duration_minutes: "60", extension_price: "", extension_minutes: "60", tax_rate: "10.00", is_active: true, sort_order: "0", note: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  function open(c: Course | "new") {
    setEditing(c);
    if (c === "new") setForm({ name: "", price: "", unlimited: false, duration_minutes: "60", extension_price: "", extension_minutes: "60", tax_rate: "10.00", is_active: true, sort_order: String((q.data?.length ?? 0) + 1), note: "" });
    else setForm({ name: c.name, price: c.price, unlimited: c.duration_minutes === null, duration_minutes: String(c.duration_minutes ?? 60), extension_price: c.extension_price, extension_minutes: String(c.extension_minutes), tax_rate: c.tax_rate, is_active: c.is_active, sort_order: String(c.sort_order), note: c.note });
  }
  return (
    <div className="card space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="font-bold">コース（セット料金・延長）</h2>
        {canEdit && (
          <button className="btn btn-sm btn-primary" onClick={() => open("new")}>
            ＋ コースを追加
          </button>
        )}
      </div>
      <ErrorBox error={q.error || err} />
      {q.loading && <Spinner />}
      {q.data?.length === 0 && <Empty>コースがありません</Empty>}
      <table className="table num">
        <thead>
          <tr>
            <th>コース名</th>
            <th>料金</th>
            <th>時間</th>
            <th>延長</th>
            <th>税率</th>
            <th>状態</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {q.data?.map((c) => (
            <tr key={c.id} style={{ opacity: c.is_active ? 1 : 0.5 }}>
              <td className="font-semibold">{c.name}</td>
              <td>{yen(c.price)}</td>
              <td>{c.duration_minutes ? fmtMinutes(c.duration_minutes) : "無制限"}</td>
              <td>{c.duration_minutes ? `${yen(c.extension_price)} / ${c.extension_minutes}分` : "—"}</td>
              <td>{Number(c.tax_rate)}%</td>
              <td>{c.is_active ? <span className="badge badge-ok">販売中</span> : <span className="badge">停止</span>}</td>
              <td>
                {canEdit && (
                  <button className="btn btn-sm" onClick={() => open(c)}>
                    編集
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {editing && (
        <Modal
          title={editing === "new" ? "コースを追加" : `${editing.name} を編集`}
          onClose={() => setEditing(null)}
          footer={
            <>
              <button className="btn" onClick={() => setEditing(null)}>
                キャンセル
              </button>
              <button
                className="btn btn-primary"
                disabled={busy || !form.name || form.price === ""}
                onClick={async () => {
                  setBusy(true);
                  setSave({ kind: "saving" });
                  try {
                    const body = {
                      name: form.name, price: form.price, duration_minutes: form.unlimited ? null : Number(form.duration_minutes), extension_price: form.extension_price || "0",
                      extension_minutes: Number(form.extension_minutes) || 60, tax_rate: form.tax_rate, is_active: form.is_active, sort_order: Number(form.sort_order) || 0, note: form.note,
                    };
                    if (editing === "new") await api("/api/store/courses", { method: "POST", body });
                    else await api(`/api/store/courses/${editing.id}`, { method: "PATCH", body });
                    setSave({ kind: "saved" });
                    setEditing(null);
                    await q.reload();
                  } catch (e) {
                    setErr(e);
                    setSave({ kind: "error", error: e });
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                保存
              </button>
            </>
          }
        >
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label="コース名">
              <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="料金（円）">
              <input className="input" inputMode="numeric" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value.replace(/[^\d]/g, "") })} />
            </Field>
            <Toggle checked={form.unlimited} onChange={(v) => setForm({ ...form, unlimited: v })} label="時間無制限" />
            {!form.unlimited && (
              <Field label="時間（分）">
                <input className="input" inputMode="numeric" value={form.duration_minutes} onChange={(e) => setForm({ ...form, duration_minutes: e.target.value.replace(/[^\d]/g, "") })} />
              </Field>
            )}
            {!form.unlimited && (
              <Field label="延長料金（円 / 1回）">
                <input className="input" inputMode="numeric" value={form.extension_price} onChange={(e) => setForm({ ...form, extension_price: e.target.value.replace(/[^\d]/g, "") })} />
              </Field>
            )}
            {!form.unlimited && (
              <Field label="延長1回の分数">
                <input className="input" inputMode="numeric" value={form.extension_minutes} onChange={(e) => setForm({ ...form, extension_minutes: e.target.value.replace(/[^\d]/g, "") })} />
              </Field>
            )}
            <Field label="税率（%）">
              <input className="input" inputMode="decimal" value={form.tax_rate} onChange={(e) => setForm({ ...form, tax_rate: e.target.value })} />
            </Field>
            <Field label="表示順">
              <input className="input" inputMode="numeric" value={form.sort_order} onChange={(e) => setForm({ ...form, sort_order: e.target.value.replace(/[^\d]/g, "") })} />
            </Field>
            <Toggle checked={form.is_active} onChange={(v) => setForm({ ...form, is_active: v })} label="販売中" />
            <div className="md:col-span-2">
              <Field label="メモ">
                <input className="input" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
              </Field>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

function FeesTab({ canEdit, setSave, withReauth }: { canEdit: boolean; setSave: (s: SaveState) => void; withReauth: <T>(fn: () => Promise<T>) => Promise<T> }) {
  const q = useLoad<StoreSettings>("/api/store/settings");
  const courses = useLoad<Course[]>("/api/store/courses?active=1");
  const [f, setF] = useState<StoreSettings | null>(null);
  useEffect(() => {
    if (q.data && !f) setF(q.data);
  }, [q.data, f]);
  if (!f) return <Spinner />;
  const set = (p: Partial<StoreSettings>) => setF({ ...f, ...p });
  const unlimitedCourses = (courses.data ?? []).filter((c) => c.duration_minutes === null);
  return (
    <div className="space-y-4">
      <ErrorBox error={q.error} />
      <section className="card space-y-3">
        <h2 className="font-bold">時間制から無制限への変更料金</h2>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          未設定のままでは「無制限へ変更」の操作はエラーになります（0円として処理はしません）。
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Field label="変更先の無制限コース">
            <select className="input" value={f.default_unlimited_course ?? ""} onChange={(e) => set({ default_unlimited_course: e.target.value ? Number(e.target.value) : null })} disabled={!canEdit}>
              <option value="">未設定</option>
              {unlimitedCourses.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}（{yen(c.price)}）
                </option>
              ))}
            </select>
          </Field>
          <Field label="変更料金の決め方">
            <select className="input" value={f.unlimited_change_mode} onChange={(e) => set({ unlimited_change_mode: e.target.value })} disabled={!canEdit}>
              <option value="unset">未設定（変更操作を拒否）</option>
              <option value="fixed">固定額</option>
              <option value="difference">無制限コース料金との差額</option>
            </select>
          </Field>
          {f.unlimited_change_mode === "fixed" && (
            <Field label="固定額（円・1人または1組あたり）">
              <input className="input" inputMode="numeric" value={f.unlimited_change_price ?? ""} onChange={(e) => set({ unlimited_change_price: e.target.value.replace(/[^\d]/g, "") })} disabled={!canEdit} />
            </Field>
          )}
          <Field label="延長後に無制限へ変更するときの料金">
            <select className="input" value={f.unlimited_change_after_ext_mode} onChange={(e) => set({ unlimited_change_after_ext_mode: e.target.value })} disabled={!canEdit}>
              <option value="unset">未設定（変更操作を拒否）</option>
              <option value="same_as_change">通常の変更料金と同じ</option>
              <option value="fixed">固定額</option>
              <option value="difference">無制限料金と支払済み（セット＋延長）との差額（負なら0）</option>
            </select>
          </Field>
          {f.unlimited_change_after_ext_mode === "fixed" && (
            <Field label="延長後の固定額（円）">
              <input className="input" inputMode="numeric" value={f.unlimited_change_after_ext_price ?? ""} onChange={(e) => set({ unlimited_change_after_ext_price: e.target.value.replace(/[^\d]/g, "") })} disabled={!canEdit} />
            </Field>
          )}
        </div>
      </section>
      <section className="card space-y-3">
        <h2 className="font-bold">指名料・同伴料（お客様への請求額。バックは別に設定）</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label="指名料（円・空欄なら未設定＝登録不可）">
            <input className="input" inputMode="numeric" value={f.nomination_fee ?? ""} onChange={(e) => set({ nomination_fee: e.target.value ? e.target.value.replace(/[^\d]/g, "") : null })} disabled={!canEdit} />
          </Field>
          <Field label="同伴料（円・空欄なら未設定＝登録不可）">
            <input className="input" inputMode="numeric" value={f.douhan_fee ?? ""} onChange={(e) => set({ douhan_fee: e.target.value ? e.target.value.replace(/[^\d]/g, "") : null })} disabled={!canEdit} />
          </Field>
        </div>
      </section>
      {canEdit && (
        <button
          className="btn btn-primary btn-lg"
          onClick={async () => {
            setSave({ kind: "saving" });
            try {
              const body = {
                default_unlimited_course: f.default_unlimited_course, unlimited_change_mode: f.unlimited_change_mode, unlimited_change_price: f.unlimited_change_price || null,
                unlimited_change_after_ext_mode: f.unlimited_change_after_ext_mode, unlimited_change_after_ext_price: f.unlimited_change_after_ext_price || null,
                nomination_fee: f.nomination_fee || null, douhan_fee: f.douhan_fee || null,
              };
              const d = await withReauth(() => api<StoreSettings>("/api/store/settings", { method: "PUT", body }));
              setF(d);
              setSave({ kind: "saved" });
            } catch (e) {
              setSave({ kind: "error", error: e });
            }
          }}
        >
          保存（パスワード再確認）
        </button>
      )}
    </div>
  );
}

function BacksTab({ canEdit, setSave, withReauth }: { canEdit: boolean; setSave: (s: SaveState) => void; withReauth: <T>(fn: () => Promise<T>) => Promise<T> }) {
  const rules = useLoad<BackRule[]>("/api/staff/back-rules");
  const employees = useLoad<Employee[]>("/api/staff/employees?active=1");
  const categories = useLoad<Category[]>("/api/store/categories");
  const products = useLoad<Product[]>("/api/store/products");
  const [editing, setEditing] = useState<BackRule | "new" | null>(null);
  const empty: Omit<BackRule, "id" | "employee_name" | "category_name" | "product_name"> = {
    employee: null, name: "", target_type: "category_kind", category_kind: "cast_drink", category: null, product: null, calc_type: "percent", value: "50", basis: "tax_included",
    include_discount: true, include_service_charge: false, rounding: "floor", confirm_timing: "checkout", effective_from: new Date().toISOString().slice(0, 10), effective_to: null, is_active: true, note: "",
  };
  const [form, setForm] = useState(empty);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  function open(r: BackRule | "new") {
    setEditing(r);
    setForm(r === "new" ? empty : { ...r });
  }
  const common = (rules.data ?? []).filter((r) => r.employee === null);
  const personal = (rules.data ?? []).filter((r) => r.employee !== null);
  const Row = ({ r }: { r: BackRule }) => (
    <tr style={{ opacity: r.is_active ? 1 : 0.5 }}>
      <td className="font-semibold">{r.name}</td>
      <td>
        {TARGET_LABELS[r.target_type]}
        {r.target_type === "category_kind" && `: ${KIND_LABELS[r.category_kind] ?? r.category_kind}`}
        {r.target_type === "category" && `: ${r.category_name}`}
        {r.target_type === "product" && `: ${r.product_name}`}
      </td>
      <td className="num">{r.calc_type === "percent" ? `${Number(r.value)}%（${r.basis === "tax_excluded" ? "税抜" : "税込"}基準）` : yen(r.value)}</td>
      <td className="text-sm">
        {r.include_discount ? "値引き反映" : "値引き無視"} / {r.include_service_charge ? "サ料含む" : "サ料除く"} / {{ floor: "切捨", ceil: "切上", half_up: "四捨五入" }[r.rounding]} / {{ order: "注文時", checkout: "会計時", payment: "入金時" }[r.confirm_timing]}
      </td>
      <td className="num">
        {fmtDate(r.effective_from)}
        {r.effective_to && ` 〜 ${fmtDate(r.effective_to)}`}
      </td>
      <td>
        {canEdit && (
          <button className="btn btn-sm" onClick={() => open(r)}>
            編集
          </button>
        )}
      </td>
    </tr>
  );
  return (
    <div className="space-y-4">
      <ErrorBox error={rules.error || err} />
      <div className="card-2 text-sm">
        優先順位: <strong>個人別 ＞ 店舗共通</strong>。対象の具体性: 商品 ＞ カテゴリ ＞ カテゴリ種別 ＞ 全商品。同じ条件なら適用開始日が新しいものを使います。計算条件は発生時点のスナップショットとして保存され、設定変更後も確定済みのバックは変わりません。
      </div>
      {canEdit && (
        <button className="btn btn-primary" onClick={() => open("new")}>
          ＋ バック設定を追加
        </button>
      )}
      {rules.loading && <Spinner />}
      {[
        { title: "店舗共通ルール", rows: common },
        { title: "個人別ルール", rows: personal },
      ].map((g) => (
        <div key={g.title} className="card scroll-x">
          <h2 className="font-bold mb-2">{g.title}</h2>
          {g.rows.length === 0 && <Empty>設定がありません</Empty>}
          {g.rows.length > 0 && (
            <table className="table">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>対象</th>
                  <th>計算</th>
                  <th>条件</th>
                  <th>適用期間</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {g.rows.map((r) => (
                  <tr key={r.id} style={{ display: "contents" }}>
                    {r.employee_name && (
                      <td className="font-semibold" style={{ display: "none" }}>
                        {r.employee_name}
                      </td>
                    )}
                    <Row r={r} />
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
      {editing && (
        <Modal
          title={editing === "new" ? "バック設定を追加" : `${editing.name} を編集`}
          wide
          onClose={() => setEditing(null)}
          footer={
            <>
              <button className="btn" onClick={() => setEditing(null)}>
                キャンセル
              </button>
              <button
                className="btn btn-primary"
                disabled={busy || !form.name || form.value === ""}
                onClick={async () => {
                  setBusy(true);
                  setSave({ kind: "saving" });
                  try {
                    const body = { ...form, category: form.target_type === "category" ? form.category : null, product: form.target_type === "product" ? form.product : null, category_kind: form.target_type === "category_kind" ? form.category_kind : "" };
                    if (editing === "new") await withReauth(() => api("/api/staff/back-rules", { method: "POST", body }));
                    else await withReauth(() => api(`/api/staff/back-rules/${editing.id}`, { method: "PATCH", body }));
                    setSave({ kind: "saved" });
                    setEditing(null);
                    await rules.reload();
                  } catch (e) {
                    setErr(e);
                    setSave({ kind: "error", error: e });
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                保存（パスワード再確認）
              </button>
            </>
          }
        >
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <Field label="名称">
              <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="適用する従業員（空欄＝店舗共通）">
              <select className="input" value={form.employee ?? ""} onChange={(e) => setForm({ ...form, employee: e.target.value ? Number(e.target.value) : null })}>
                <option value="">店舗共通</option>
                {employees.data?.map((emp) => (
                  <option key={emp.id} value={emp.id}>
                    {emp.display_name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="対象">
              <select className="input" value={form.target_type} onChange={(e) => setForm({ ...form, target_type: e.target.value })}>
                {Object.entries(TARGET_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </Field>
            {form.target_type === "category_kind" && (
              <Field label="カテゴリ種別">
                <select className="input" value={form.category_kind} onChange={(e) => setForm({ ...form, category_kind: e.target.value })}>
                  {Object.entries(KIND_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            {form.target_type === "category" && (
              <Field label="カテゴリ">
                <select className="input" value={form.category ?? ""} onChange={(e) => setForm({ ...form, category: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">選択</option>
                  {categories.data?.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            {form.target_type === "product" && (
              <Field label="商品">
                <select className="input" value={form.product ?? ""} onChange={(e) => setForm({ ...form, product: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">選択</option>
                  {products.data?.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            <Field label="計算方法">
              <select className="input" value={form.calc_type} onChange={(e) => setForm({ ...form, calc_type: e.target.value as "fixed" | "percent" })}>
                <option value="percent">割合（%）</option>
                <option value="fixed">固定額（円・数量ごと）</option>
              </select>
            </Field>
            <Field label={form.calc_type === "percent" ? "割合（%）" : "固定額（円）"}>
              <input className="input" inputMode="decimal" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} />
            </Field>
            <Field label="計算基準">
              <select className="input" value={form.basis} onChange={(e) => setForm({ ...form, basis: e.target.value })}>
                <option value="tax_included">税込金額</option>
                <option value="tax_excluded">税抜金額</option>
              </select>
            </Field>
            <Toggle checked={form.include_discount} onChange={(v) => setForm({ ...form, include_discount: v })} label="値引きを反映（伝票の値引き割合を按分）" />
            <Toggle checked={form.include_service_charge} onChange={(v) => setForm({ ...form, include_service_charge: v })} label="サービス料を含める" />
            <Field label="端数処理">
              <select className="input" value={form.rounding} onChange={(e) => setForm({ ...form, rounding: e.target.value })}>
                <option value="floor">切り捨て</option>
                <option value="ceil">切り上げ</option>
                <option value="half_up">四捨五入</option>
              </select>
            </Field>
            <Field label="確定する時点">
              <select className="input" value={form.confirm_timing} onChange={(e) => setForm({ ...form, confirm_timing: e.target.value })}>
                <option value="order">注文時</option>
                <option value="checkout">会計確定時</option>
                <option value="payment">入金完了時</option>
              </select>
            </Field>
            <Field label="適用開始日">
              <input type="date" className="input" value={form.effective_from} onChange={(e) => setForm({ ...form, effective_from: e.target.value })} />
            </Field>
            <Field label="適用終了日（任意）">
              <input type="date" className="input" value={form.effective_to ?? ""} onChange={(e) => setForm({ ...form, effective_to: e.target.value || null })} />
            </Field>
            <Toggle checked={form.is_active} onChange={(v) => setForm({ ...form, is_active: v })} label="有効" />
            <div className="md:col-span-3">
              <Field label="メモ">
                <input className="input" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
              </Field>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
