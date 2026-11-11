"use client";

import { useEffect, useState } from "react";
import { TerminalSettingsTab } from "@/components/TerminalSettings";
import { Empty, ErrorBox, Field, Modal, PageHeader, SaveStatus, Spinner, Toggle, type SaveState } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDateTime, ROLE_LABELS } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { PayoutDeductionItem, StoreSettings, Table } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

const ROLE_MATRIX: { cap: string; label: string; owner: boolean; manager: boolean; staff: boolean; cast: boolean }[] = [
  { cap: "floor.operate", label: "入店・注文・延長・会計", owner: true, manager: true, staff: true, cast: false },
  { cap: "sales.void", label: "会計取消・返金", owner: true, manager: true, staff: false, cast: false },
  { cap: "closing.operate", label: "開店・日締め・現金出納", owner: true, manager: true, staff: true, cast: false },
  { cap: "closing.edit_closed", label: "締め後の修正", owner: true, manager: true, staff: false, cast: false },
  { cap: "payroll.pay", label: "日払い登録", owner: true, manager: true, staff: false, cast: false },
  { cap: "settings.edit", label: "料金・店舗設定の変更", owner: true, manager: true, staff: false, cast: false },
  { cap: "employees.edit", label: "従業員の登録・配属・ログイン", owner: true, manager: true, staff: false, cast: false },
  { cap: "wages.edit", label: "時給の設定・閲覧", owner: true, manager: false, staff: false, cast: false },
  { cap: "backs.rules.edit", label: "バック設定の変更", owner: true, manager: false, staff: false, cast: false },
  { cap: "payroll.view_all", label: "全員の支払集計・締め", owner: true, manager: false, staff: false, cast: false },
  { cap: "attendance.manage", label: "他人の勤怠の操作・修正", owner: true, manager: true, staff: false, cast: false },
  { cap: "attendance.self", label: "自分の出退勤", owner: true, manager: true, staff: true, cast: true },
  { cap: "payroll.view_self", label: "自分の成績・バック・支払集計", owner: true, manager: false, staff: true, cast: true },
  { cap: "customers.export", label: "顧客情報の出力", owner: true, manager: false, staff: false, cast: false },
  { cap: "purchasing.confirm", label: "発注の確認（確定）", owner: true, manager: false, staff: false, cast: false },
  { cap: "line.send", label: "LINE送信", owner: true, manager: true, staff: false, cast: false },
  { cap: "dashboard.view", label: "経営ダッシュボード", owner: true, manager: true, staff: false, cast: false },
  { cap: "export.payroll", label: "支払集計のCSV出力", owner: true, manager: false, staff: false, cast: false },
  { cap: "company.manage", label: "店舗追加・ユーザー招待", owner: true, manager: false, staff: false, cast: false },
];

export default function StoreSettingsPage() {
  const { can, withReauth, store } = useSession();
  const q = useLoad<StoreSettings>("/api/store/settings");
  const tables = useLoad<Table[]>("/api/store/tables");
  const [form, setForm] = useState<StoreSettings | null>(null);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [tab, setTab] = useState<"settings" | "tables" | "terminal" | "roles" | "audit">("settings");
  const canEdit = can("settings.edit");

  useEffect(() => {
    if (q.data && !form) setForm(q.data);
  }, [q.data, form]);

  async function submit() {
    if (!form) return;
    setSave({ kind: "saving" });
    try {
      const d = await withReauth(() => api<StoreSettings>("/api/store/settings", { method: "PUT", body: form }));
      setForm(d);
      q.setData(d);
      setSave({ kind: "saved" });
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  const f = form;
  const set = (patch: Partial<StoreSettings>) => setForm((prev) => (prev ? { ...prev, ...patch } : prev));

  return (
    <div className="space-y-4">
      <PageHeader
        title="店舗設定・権限設定"
        subtitle={`${store?.company.name} / ${store?.name}。設定は店舗ごとに保持されます。`}
        actions={
          <>
            <SaveStatus state={save} />
            {(["settings", "tables", "terminal", "roles", "audit"] as const).map((t) => (
              <button key={t} className={`btn btn-sm ${tab === t ? "btn-primary" : ""}`} onClick={() => setTab(t)}>
                {{ settings: "営業ルール", tables: "卓", terminal: "カード端末", roles: "権限", audit: "操作履歴" }[t]}
              </button>
            ))}
          </>
        }
      />
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {tab === "settings" && (!f ? (
        <Spinner />
      ) : (
        <div className="space-y-4">
          {f.settings_note && (
            <div className="card-2" style={{ borderColor: "var(--color-warn)" }}>
              <div className="font-semibold">設定メモ</div>
              <div className="text-sm whitespace-pre-wrap">{f.settings_note}</div>
            </div>
          )}
          <section className="card space-y-3">
            <h2 className="font-bold">営業日・アラーム・会計</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <Field label="営業日の切替時刻" hint="この時刻より前は前日の営業分として集計します（深夜0時をまたいでも同じ営業日）">
                <input type="time" className="input" value={f.business_day_switch_time.slice(0, 5)} onChange={(e) => set({ business_day_switch_time: e.target.value })} disabled={!canEdit} />
              </Field>
              <Field label="終了時間アラーム">
                <select className="input" value={f.alarm_mode} onChange={(e) => set({ alarm_mode: e.target.value as StoreSettings["alarm_mode"] })} disabled={!canEdit}>
                  <option value="both">10分前と5分前の両方</option>
                  <option value="10">10分前のみ</option>
                  <option value="5">5分前のみ</option>
                </select>
              </Field>
              <Field label="既定の釣銭準備金（円）">
                <input className="input" inputMode="numeric" value={f.default_opening_float} onChange={(e) => set({ default_opening_float: e.target.value })} disabled={!canEdit} />
              </Field>
              <Toggle checked={f.receivable_allowed} onChange={(v) => set({ receivable_allowed: v })} label="ツケ・未収を許可する" />
            </div>
          </section>
          <section className="card space-y-3">
            <h2 className="font-bold">料金の単位・税</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <Field label="コース料金の単位">
                <select className="input" value={f.price_basis} onChange={(e) => set({ price_basis: e.target.value as StoreSettings["price_basis"] })} disabled={!canEdit}>
                  <option value="per_person">1人単位（人数分）</option>
                  <option value="per_group">1組単位</option>
                </select>
              </Field>
              <Toggle checked={f.prices_include_tax} onChange={(v) => set({ prices_include_tax: v })} label="価格は税込（内税）" />
              <Field label="標準税率（%）">
                <input className="input" inputMode="decimal" value={f.default_tax_rate} onChange={(e) => set({ default_tax_rate: e.target.value })} disabled={!canEdit} />
              </Field>
            </div>
          </section>
          <section className="card space-y-3">
            <h2 className="font-bold">サービス料</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <Toggle checked={f.service_charge_enabled} onChange={(v) => set({ service_charge_enabled: v })} label="サービス料を加算する" />
              <Field label="料率（%）">
                <input className="input" inputMode="decimal" value={f.service_charge_rate} onChange={(e) => set({ service_charge_rate: e.target.value })} disabled={!canEdit} />
              </Field>
              <Field label="計算対象">
                <select className="input" value={f.service_charge_target} onChange={(e) => set({ service_charge_target: e.target.value })} disabled={!canEdit}>
                  <option value="all">全明細</option>
                  <option value="course_only">セット・延長・コース変更のみ</option>
                  <option value="products_only">商品のみ</option>
                </select>
              </Field>
              <Field label="計算順序（外税のときに影響）">
                <select className="input" value={f.service_charge_order} onChange={(e) => set({ service_charge_order: e.target.value })} disabled={!canEdit}>
                  <option value="before_tax">税抜金額にかけ、サービス料にも消費税をかける</option>
                  <option value="after_tax">税込金額にかけ、サービス料には消費税をかけない</option>
                </select>
              </Field>
              <Toggle checked={f.service_charge_after_discount} onChange={(v) => set({ service_charge_after_discount: v })} label="値引き後の金額にかける" />
            </div>
          </section>
          <section className="card space-y-3">
            <h2 className="font-bold">端数処理</h2>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
              <Field label="消費税の端数">
                <RoundingSelect value={f.tax_rounding} onChange={(v) => set({ tax_rounding: v })} disabled={!canEdit} />
              </Field>
              <Field label="サービス料の端数">
                <RoundingSelect value={f.service_charge_rounding} onChange={(v) => set({ service_charge_rounding: v })} disabled={!canEdit} />
              </Field>
              <Field label="合計の丸め単位（円）">
                <select className="input" value={f.total_rounding_unit} onChange={(e) => set({ total_rounding_unit: Number(e.target.value) })} disabled={!canEdit}>
                  {[1, 10, 100, 1000].map((u) => (
                    <option key={u} value={u}>
                      {u}円
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="合計の丸め方">
                <RoundingSelect value={f.total_rounding_mode} onChange={(v) => set({ total_rounding_mode: v })} disabled={!canEdit} />
              </Field>
            </div>
          </section>
          <section className="card space-y-3">
            <h2 className="font-bold">バック</h2>
            <Field label="バック確定の既定タイミング（個別ルールで上書き可）">
              <select className="input" value={f.default_back_confirm_timing} onChange={(e) => set({ default_back_confirm_timing: e.target.value })} disabled={!canEdit}>
                <option value="order">注文時</option>
                <option value="checkout">会計確定時</option>
                <option value="payment">入金完了時（未収回収まで未確定）</option>
              </select>
            </Field>
          </section>
          <PayoutSection f={f} set={set} canEdit={canEdit} canDecideTax={can("tax.decide")} />
          <section className="card space-y-3">
            <Field label="設定メモ（仮設定の明示など）">
              <textarea className="input" value={f.settings_note} onChange={(e) => set({ settings_note: e.target.value })} disabled={!canEdit} />
            </Field>
          </section>
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            コース料金・無制限変更料金・指名/同伴料・バック設定は「時給・バック・料金」で設定します。
          </p>
          {canEdit && (
            <button className="btn btn-primary btn-lg" onClick={submit} disabled={save.kind === "saving"}>
              設定を保存（パスワード再確認）
            </button>
          )}
        </div>
      ))}

      {tab === "tables" && <TablesTab tables={tables} canEdit={can("catalog.edit")} setSave={setSave} />}

      {tab === "terminal" && <TerminalSettingsTab />}

      {tab === "roles" && (
        <div className="card scroll-x">
          <p className="text-sm mb-2" style={{ color: "var(--color-muted)" }}>
            権限は API 側で検証されます（画面で隠すだけではありません）。会社オーナー・会社管理者は自社の全店舗でオーナー権限、運営者は全社にアクセスでき、そのアクセスは記録されます。
          </p>
          <table className="table">
            <thead>
              <tr>
                <th>操作</th>
                <th>{ROLE_LABELS.owner}</th>
                <th>{ROLE_LABELS.manager}</th>
                <th>{ROLE_LABELS.staff}</th>
                <th>{ROLE_LABELS.cast}</th>
              </tr>
            </thead>
            <tbody>
              {ROLE_MATRIX.map((r) => (
                <tr key={r.cap}>
                  <td>{r.label}</td>
                  {[r.owner, r.manager, r.staff, r.cast].map((v, i) => (
                    <td key={i}>{v ? "○" : "—"}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "audit" && <AuditTab />}
    </div>
  );
}

/** 日払い（お渡し）: 源泉所得税を差し引くか、お渡しのときにチェックして引く控除（備品代など） */
function PayoutSection({ f, set, canEdit, canDecideTax }: { f: StoreSettings; set: (patch: Partial<StoreSettings>) => void; canEdit: boolean; canDecideTax: boolean }) {
  const items = f.payout_deductions;
  const update = (i: number, patch: Partial<PayoutDeductionItem>) => set({ payout_deductions: items.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  return (
    <section className="card space-y-3">
      <h2 className="font-bold">日払い（お渡し）</h2>
      {canDecideTax ? (
        <Toggle checked={f.withholding_enabled} onChange={(v) => set({ withholding_enabled: v })} label="源泉所得税を差し引く" />
      ) : (
        <div className="card-2">源泉所得税: {f.withholding_enabled ? "差し引く" : "差し引かない"}（変更は経営者のみ）</div>
      )}
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        オフの間は、お渡しのときに源泉所得税を計算・差し引きせず、報酬をそのまま渡します（税務区分の確認待ちの表示も出ません）。オンにすると、それ以降のお渡しから「キャスト契約・税務区分」の設定に従って差し引きます。
      </p>
      <div className="flex items-center gap-2 pt-2">
        <h3 className="font-semibold">お渡しのときに引く控除</h3>
        {canEdit && items.length < 10 && (
          <button className="btn btn-sm ml-auto" onClick={() => set({ payout_deductions: [...items, { code: "", label: "", percent: "", default_on: false }] })}>
            ＋ 控除を追加
          </button>
        )}
      </div>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        備品代など、その日の報酬（時給×勤務時間＋バック）に対する割合で引く項目です。お渡しの画面にチェック欄が出て、チェックを入れたときだけ引きます。「最初からチェック」にすると、チェックが入った状態で表示します（外せます）。
      </p>
      {items.length === 0 && <p style={{ color: "var(--color-muted)" }}>控除の項目はありません。</p>}
      {items.map((d, i) => (
        <div key={d.code || `new-${i}`} className="grid grid-cols-1 md:grid-cols-[2fr_1fr_1.5fr_auto] gap-2 items-end">
          <Field label="名目">
            <input className="input" placeholder="備品代" maxLength={20} value={d.label} onChange={(e) => update(i, { label: e.target.value })} disabled={!canEdit} />
          </Field>
          <Field label="割合（%）">
            <input
              className="input num"
              inputMode="decimal"
              placeholder="10"
              value={d.percent}
              onChange={(e) => update(i, { percent: e.target.value.replace(/[^0-9.]/g, "") })}
              disabled={!canEdit}
            />
          </Field>
          <Toggle checked={d.default_on} onChange={(v) => canEdit && update(i, { default_on: v })} label="最初からチェック" />
          {canEdit && (
            <button className="btn btn-sm btn-ghost" onClick={() => set({ payout_deductions: items.filter((_, j) => j !== i) })}>
              削除
            </button>
          )}
        </div>
      ))}
    </section>
  );
}

function RoundingSelect({ value, onChange, disabled }: { value: string; onChange: (v: string) => void; disabled?: boolean }) {
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
      <option value="floor">切り捨て</option>
      <option value="ceil">切り上げ</option>
      <option value="half_up">四捨五入</option>
    </select>
  );
}

function TablesTab({ tables, canEdit, setSave }: { tables: ReturnType<typeof useLoad<Table[]>>; canEdit: boolean; setSave: (s: SaveState) => void }) {
  const [editing, setEditing] = useState<Table | "new" | null>(null);
  const [name, setName] = useState("");
  const [capacity, setCapacity] = useState("4");
  const [sort, setSort] = useState("0");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  function open(t: Table | "new") {
    setEditing(t);
    setName(t === "new" ? "" : t.name);
    setCapacity(t === "new" ? "4" : String(t.capacity));
    setSort(t === "new" ? String((tables.data?.length ?? 0) + 1) : String(t.sort_order));
  }
  return (
    <div className="card space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="font-bold">卓</h2>
        {canEdit && (
          <button className="btn btn-sm btn-primary" onClick={() => open("new")}>
            ＋ 卓を追加
          </button>
        )}
      </div>
      <ErrorBox error={tables.error || err} />
      {tables.data?.length === 0 && <Empty>卓がありません</Empty>}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {tables.data?.map((t) => (
          <button key={t.id} className="btn justify-between" onClick={() => canEdit && open(t)} style={{ opacity: t.is_active ? 1 : 0.5 }}>
            <span>
              {t.name} <span className="text-xs opacity-70">定員{t.capacity}</span>
            </span>
            {!t.is_active && <span className="badge">停止</span>}
          </button>
        ))}
      </div>
      {editing && (
        <Modal
          title={editing === "new" ? "卓を追加" : `${editing.name} を編集`}
          onClose={() => setEditing(null)}
          footer={
            <>
              {editing !== "new" && editing.is_active && (
                <button
                  className="btn btn-danger"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      await api(`/api/store/tables/${editing.id}`, { method: "DELETE" });
                      setEditing(null);
                      await tables.reload();
                    } catch (e) {
                      setErr(e);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  停止
                </button>
              )}
              <button className="btn" onClick={() => setEditing(null)}>
                キャンセル
              </button>
              <button
                className="btn btn-primary"
                disabled={busy || !name}
                onClick={async () => {
                  setBusy(true);
                  setSave({ kind: "saving" });
                  try {
                    const body = { name, capacity: Number(capacity), sort_order: Number(sort), is_active: true };
                    if (editing === "new") await api("/api/store/tables", { method: "POST", body });
                    else await api(`/api/store/tables/${editing.id}`, { method: "PATCH", body });
                    setSave({ kind: "saved" });
                    setEditing(null);
                    await tables.reload();
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
          <div className="space-y-3">
            <Field label="卓名">
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="定員">
              <input className="input" inputMode="numeric" value={capacity} onChange={(e) => setCapacity(e.target.value.replace(/[^\d]/g, ""))} />
            </Field>
            <Field label="表示順">
              <input className="input" inputMode="numeric" value={sort} onChange={(e) => setSort(e.target.value.replace(/[^\d]/g, ""))} />
            </Field>
          </div>
        </Modal>
      )}
    </div>
  );
}

type AuditRow = { id: number; user_name: string; action: string; target_type: string; target_id: string; changes: Record<string, unknown>; created_at: string };

function AuditTab() {
  const q = useLoad<AuditRow[]>("/api/audit/logs");
  if (q.loading) return <Spinner />;
  return (
    <div className="card scroll-x">
      <ErrorBox error={q.error} />
      <table className="table">
        <thead>
          <tr>
            <th>日時</th>
            <th>ユーザー</th>
            <th>操作</th>
            <th>対象</th>
            <th>内容</th>
          </tr>
        </thead>
        <tbody>
          {q.data?.map((r) => (
            <tr key={r.id}>
              <td className="whitespace-nowrap">{fmtDateTime(r.created_at)}</td>
              <td>{r.user_name}</td>
              <td>{r.action}</td>
              <td>
                {r.target_type} {r.target_id}
              </td>
              <td className="text-xs" style={{ color: "var(--color-muted)" }}>
                {JSON.stringify(r.changes).slice(0, 160)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
