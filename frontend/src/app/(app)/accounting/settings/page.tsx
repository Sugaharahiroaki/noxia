"use client";

import { useEffect, useState } from "react";
import { Tabs } from "@/components/common";
import { Empty, ErrorBox, Field, Modal, PageHeader, Spinner, Toggle } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api, ApiError } from "@/lib/api";
import { fmtDate, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AccountingOverview, StoreSettings, TaxPayer } from "@/lib/types";
import type { Account, AccountsResponse } from "@/lib/types_books";
import type { TaxProfile, TaxProfileResponse } from "@/lib/types_dashboard";
import type { AiSettings } from "@/lib/types_expenses";
import { useLoad } from "@/lib/useLoad";

type Grant = { id: number; username: string; display_name: string; role: string; role_label: string; is_active: boolean; expires_on: string | null; note: string };
type Rule = { id: number; version: string; effective_from: string; effective_to: string | null; rate_percent: string; daily_deduction: string; components: { name: string; rate: string }[]; legal_basis: string; source_url: string };

const TABS = [
  { key: "taxpayers", label: "事業者・店舗・権限" },
  { key: "accounts", label: "勘定科目" },
  { key: "ai", label: "AI 読取" },
  { key: "profile", label: "申告情報（個人事業主）" },
  { key: "approval", label: "店舗の承認設定" },
  { key: "dencho", label: "電子帳簿保存法" },
];

export default function AccountingSettingsPage() {
  const { can } = useSession();
  const [tab, setTab] = useState("taxpayers");
  const q = useLoad<AccountingOverview>("/api/accounting/overview");
  const canEdit = can("accounting.settings");

  return (
    <div className="space-y-4">
      <PageHeader title="会計設定" subtitle="事業者・店舗の対応、勘定科目、AI 読取、申告情報、店長の承認範囲、電子帳簿保存法の対応状況。" />
      <Tabs tabs={TABS} value={tab} onChange={setTab} size="sm" />
      {tab === "taxpayers" && <TaxPayersTab q={q} canEdit={canEdit} />}
      {tab === "accounts" && <AccountsTab canEdit={can("books.adjust")} />}
      {tab === "ai" && <AiSettingsTab canEdit={canEdit} />}
      {tab === "profile" && <TaxProfileTab taxpayer={q.data?.taxpayer ?? null} canEdit={can("tax.return.edit")} />}
      {tab === "approval" && <StoreApprovalTab canEdit={can("settings.edit")} />}
      {tab === "dencho" && <DenchoTab />}
    </div>
  );
}

// --- 事業者・店舗・権限（従来の内容） ---------------------------------------------------------
function TaxPayersTab({ q, canEdit }: { q: ReturnType<typeof useLoad<AccountingOverview>>; canEdit: boolean }) {
  const { withReauth } = useSession();
  const toast = useToast();
  const [editing, setEditing] = useState<TaxPayer | "new" | null>(null);
  const [err, setErr] = useState<unknown>(null);
  if (q.loading) return <Spinner />;
  const d = q.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          源泉徴収義務者（法人または個人事業主）を登録し、店舗ごとにどの事業者の店かを設定します。同じ事業者が複数店舗を運営する場合、源泉所得税・帳簿・申告は事業者単位でまとめます。
        </p>
        {canEdit && (
          <button className="btn btn-primary" onClick={() => setEditing("new")}>
            ＋ 事業者を登録
          </button>
        )}
      </div>
      <ErrorBox error={q.error || err} onRetry={() => q.reload(false)} />
      {d && d.taxpayers.length === 0 && <Empty>事業者が登録されていません</Empty>}
      {d && d.taxpayers.length > 0 && (
        <div className="card scroll-x">
          <table className="table">
            <thead>
              <tr>
                <th>事業者名</th>
                <th>区分</th>
                <th>所轄税務署</th>
                <th>整理番号</th>
                <th>給与の納期の特例</th>
                <th>申告方法</th>
                <th>店舗</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {d.taxpayers.map((t) => (
                <tr key={t.id}>
                  <td className="font-semibold">{t.name}</td>
                  <td>{t.entity_type_label}</td>
                  <td>{t.tax_office_name || <span style={{ color: "var(--color-warn)" }}>未設定</span>}</td>
                  <td className="num">{t.reference_number || <span style={{ color: "var(--color-warn)" }}>未設定</span>}</td>
                  <td>{t.payroll_due_special ? "承認あり" : "なし"}</td>
                  <td>{t.filing_method === "freee" ? "freee" : t.filing_method === "nta_corner" ? "作成コーナー転記" : <span style={{ color: "var(--color-muted)" }}>未設定</span>}</td>
                  <td className="text-sm">{d.stores.filter((s) => s.taxpayer_id === t.id).map((s) => s.name).join("、") || "—"}</td>
                  <td>
                    {canEdit && (
                      <button className="btn btn-sm" onClick={() => setEditing(t)}>
                        編集
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {d && canEdit && d.taxpayers.length > 0 && (
        <div className="card space-y-2">
          <h2 className="text-lg font-bold">店舗の事業者</h2>
          {d.stores.map((s) => (
            <div key={s.id} className="flex items-center gap-3">
              <span className="w-48">{s.name}</span>
              <select
                className="input"
                style={{ maxWidth: 360 }}
                value={s.taxpayer_id ?? ""}
                onChange={async (e) => {
                  try {
                    await withReauth(() => api(`/api/accounting/stores/${s.id}/taxpayer`, { method: "PUT", body: { taxpayer_id: Number(e.target.value) } }));
                    toast.show({ message: `${s.name} の事業者を設定しました` });
                    await q.reload();
                  } catch (er) {
                    setErr(er);
                  }
                }}
              >
                <option value="" disabled>
                  （未設定）
                </option>
                {d.taxpayers.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>
      )}
      {canEdit && <Grants />}
      <Rules />
      {editing && (
        <TaxPayerModal
          taxpayer={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await q.reload();
          }}
        />
      )}
    </div>
  );
}

function TaxPayerModal({ taxpayer, onClose, onSaved }: { taxpayer: TaxPayer | null; onClose: () => void; onSaved: () => void }) {
  const { withReauth } = useSession();
  const [f, setF] = useState({
    name: taxpayer?.name ?? "",
    entity_type: taxpayer?.entity_type ?? "corporation",
    corporate_number: taxpayer?.corporate_number ?? "",
    representative_name: taxpayer?.representative_name ?? "",
    address: taxpayer?.address ?? "",
    phone: taxpayer?.phone ?? "",
    tax_office_name: taxpayer?.tax_office_name ?? "",
    reference_number: taxpayer?.reference_number ?? "",
    payroll_due_special: taxpayer?.payroll_due_special ?? false,
    fiscal_year_end_month: taxpayer?.fiscal_year_end_month ?? 12,
    filing_method: taxpayer?.filing_method ?? "",
  });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f, v: string | boolean | number) => setF((s) => ({ ...s, [k]: v }));
  return (
    <Modal
      wide
      title={taxpayer ? `${taxpayer.name} を編集` : "事業者を登録"}
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !f.name}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              try {
                await withReauth(() =>
                  taxpayer ? api(`/api/accounting/taxpayers/${taxpayer.id}`, { method: "PATCH", body: f }) : api("/api/accounting/taxpayers", { method: "POST", body: f }),
                );
                onSaved();
              } catch (e) {
                setErr(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            保存する
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="事業者名（法人名、または屋号・氏名）">
          <input className="input" value={f.name} onChange={(e) => set("name", e.target.value)} />
        </Field>
        <Field label="区分">
          <select className="input" value={f.entity_type} onChange={(e) => set("entity_type", e.target.value)}>
            <option value="corporation">法人</option>
            <option value="sole_proprietor">個人事業主</option>
          </select>
        </Field>
        {f.entity_type === "corporation" && (
          <Field label="法人番号（13桁）">
            <input className="input num" value={f.corporate_number} onChange={(e) => set("corporate_number", e.target.value.replace(/[^0-9]/g, ""))} />
          </Field>
        )}
        <Field label="代表者名">
          <input className="input" value={f.representative_name} onChange={(e) => set("representative_name", e.target.value)} />
        </Field>
        <Field label="所在地（納税地）">
          <input className="input" value={f.address} onChange={(e) => set("address", e.target.value)} />
        </Field>
        <Field label="電話番号">
          <input className="input" value={f.phone} onChange={(e) => set("phone", e.target.value)} />
        </Field>
        <Field label="所轄税務署">
          <input className="input" value={f.tax_office_name} onChange={(e) => set("tax_office_name", e.target.value)} />
        </Field>
        <Field label="源泉所得税の整理番号" hint="給与支払事務所等の開設届出書を出した後に税務署から通知される番号です。">
          <input className="input num" value={f.reference_number} onChange={(e) => set("reference_number", e.target.value)} />
        </Field>
        <Field label="決算月" hint="個人事業主は 12（暦年）です。">
          <input className="input num" type="number" min={1} max={12} value={f.fiscal_year_end_month} onChange={(e) => set("fiscal_year_end_month", Number(e.target.value))} />
        </Field>
        <Field label="申告方法" hint="freee を選ぶと、銀行照合・固定資産・年間決算・確定申告のメニューを出さず、NOXIA は仕訳を freee に渡すまでを担当します。">
          <select className="input" value={f.filing_method} onChange={(e) => set("filing_method", e.target.value)}>
            <option value="">未設定（すべての画面を出す）</option>
            <option value="freee">freee で申告（仕訳を freee に渡す）</option>
            <option value="nta_corner">作成コーナーへ転記（NOXIA の画面を使う）</option>
          </select>
        </Field>
        <div>
          <Toggle checked={f.payroll_due_special} onChange={(v) => set("payroll_due_special", v)} label="給与等の納期の特例の承認を受けている" />
          <p className="text-xs mt-1" style={{ color: "var(--color-muted)" }}>
            給与・退職手当・税理士等の報酬だけが半年ごとの納付になります。ホステス報酬の源泉所得税は特例の対象外で、毎月（翌月10日まで）納付します。
          </p>
        </div>
      </div>
      <p className="text-sm mt-3" style={{ color: "var(--color-muted)" }}>
        個人番号（マイナンバー）は NOXIA に保存しません。
      </p>
      <ErrorBox error={err} />
    </Modal>
  );
}

function Grants() {
  const { withReauth } = useSession();
  const q = useLoad<Grant[]>("/api/accounting/grants");
  const [f, setF] = useState({ username: "", role: "bookkeeper", expires_on: "", note: "" });
  const [err, setErr] = useState<unknown>(null);
  return (
    <div className="card space-y-2">
      <h2 className="text-lg font-bold">記帳代行担当者・税理士等への権限</h2>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        記帳代行担当者は、売上・経費の確認、領収書の登録と承認、銀行明細の照合、仕訳、月次損益、年間決算の準備、会計データ出力、freee 連携の管理ができます。
        個別の税務判断（税務区分の確定・月次の経営者確認・年度の締め・申告書の入力と出力）、税務代理としての e-Tax 送信はできません（税理士資格が必要な業務、または経営者本人の判断）。
        税理士等は閲覧と確認コメントができます。
      </p>
      <div className="card-2 grid grid-cols-2 md:grid-cols-5 gap-2 items-end">
        <Field label="ユーザー名">
          <input className="input" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} />
        </Field>
        <Field label="役割">
          <select className="input" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>
            <option value="bookkeeper">記帳代行担当者</option>
            <option value="tax_advisor">税理士等の外部専門家</option>
          </select>
        </Field>
        <Field label="有効期限（任意）">
          <input className="input" type="date" value={f.expires_on} onChange={(e) => setF({ ...f, expires_on: e.target.value })} />
        </Field>
        <Field label="メモ（契約など）">
          <input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
        </Field>
        <button
          className="btn btn-primary"
          disabled={!f.username}
          onClick={async () => {
            try {
              await withReauth(() => api("/api/accounting/grants", { method: "POST", body: f }));
              setF({ ...f, username: "", note: "" });
              await q.reload();
            } catch (e) {
              setErr(e);
            }
          }}
        >
          権限を付与
        </button>
      </div>
      <ErrorBox error={q.error || err} />
      {q.data?.map((g) => (
        <div key={g.id} className="card-2 flex flex-wrap items-center gap-3" style={{ opacity: g.is_active ? 1 : 0.5 }}>
          <span className="font-semibold">{g.display_name || g.username}</span>
          <span className="badge">{g.role_label}</span>
          <span className="text-sm">有効期限 {g.expires_on ? fmtDate(g.expires_on) : "なし"}</span>
          <span className="text-sm">{g.note}</span>
          <button
            className="btn btn-sm ml-auto"
            onClick={async () => {
              try {
                await withReauth(() => api(`/api/accounting/grants/${g.id}`, { method: "PATCH", body: { is_active: !g.is_active } }));
                await q.reload();
              } catch (e) {
                setErr(e);
              }
            }}
          >
            {g.is_active ? "停止する" : "再開する"}
          </button>
        </div>
      ))}
    </div>
  );
}

function Rules() {
  const q = useLoad<Rule[]>("/api/accounting/withholding/rules");
  if (!q.data) return null;
  return (
    <div className="card space-y-2">
      <h2 className="text-lg font-bold">源泉徴収の計算ルール（ホステス報酬）</h2>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        税額 ＝（源泉徴収対象額 − 1日あたりの控除額 × 報酬計算期間の暦日数）× 税率（1円未満切り捨て）。税制改正のときは運営者が新しいルールを追加します。計算済みの支払いは、使ったルールの版と計算根拠を保存しているため変わりません。
      </p>
      <table className="table text-sm">
        <thead>
          <tr>
            <th>適用期間（支払日）</th>
            <th>ルール</th>
            <th className="text-right">税率</th>
            <th className="text-right">1日の控除額</th>
            <th>根拠</th>
          </tr>
        </thead>
        <tbody>
          {q.data.map((r) => (
            <tr key={r.id}>
              <td className="num">
                {fmtDate(r.effective_from)}〜{r.effective_to ? fmtDate(r.effective_to) : ""}
              </td>
              <td>
                {r.version}
                <span className="block text-xs" style={{ color: "var(--color-muted)" }}>
                  {r.components.map((c) => `${c.name} ${c.rate}`).join(" ＋ ")}
                </span>
              </td>
              <td className="text-right num">{Number(r.rate_percent)}%</td>
              <td className="text-right num">¥{Number(r.daily_deduction).toLocaleString("ja-JP")}</td>
              <td className="text-xs">
                {r.legal_basis}
                {r.source_url && (
                  <a className="block underline" href={r.source_url} target="_blank" rel="noopener noreferrer">
                    確認した資料 ↗
                  </a>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- 勘定科目 ---------------------------------------------------------------------------------
const CATEGORY_LABEL: Record<string, string> = { asset: "資産", liability: "負債", equity: "資本", owner: "事業主貸借", revenue: "収益", cogs: "売上原価", expense: "経費" };

function AccountsTab({ canEdit }: { canEdit: boolean }) {
  const q = useLoad<AccountsResponse>("/api/accounting/accounts");
  const toast = useToast();
  const [err, setErr] = useState<unknown>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<Partial<Account> | null>(null);
  const [filter, setFilter] = useState("all");

  async function save(a: Account, body: Partial<Account>) {
    setErr(null);
    try {
      await api(`/api/accounting/accounts/${a.id}`, { method: "PATCH", body });
      toast.show({ message: `${a.name} を更新しました` });
      setDraft(null);
      await q.reload();
    } catch (e) {
      setErr(e);
    }
  }

  if (q.loading) return <Spinner />;
  const d = q.data;
  if (!d) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const rows = d.accounts.filter((a) => filter === "all" || a.category === filter);
  return (
    <div className="space-y-3">
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        勘定科目は事業者ごとに持ちます。標準科目（削除不可）の名前・税区分・帳票の行は変えられます。領収書の承認で選べるのはここで有効な科目です。「帳票の行」は収支内訳書・青色申告決算書のどの行に入るかです。
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <select className="input" style={{ minHeight: 44, width: 200 }} value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="all">すべての区分</option>
          {Object.entries(CATEGORY_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        {canEdit && (
          <button className="btn btn-primary ml-auto" onClick={() => setAdding(true)}>
            ＋ 科目を追加
          </button>
        )}
      </div>
      <ErrorBox error={err} />
      <div className="card scroll-x">
        <table className="table text-sm">
          <thead>
            <tr>
              <th>コード</th>
              <th>科目名</th>
              <th>区分</th>
              <th>帳票の行</th>
              <th>既定の税区分</th>
              <th>状態</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => {
              const editing = draft?.id === a.id;
              return (
                <tr key={a.id} style={{ opacity: a.is_active ? 1 : 0.5 }}>
                  <td className="num">{a.code}</td>
                  <td>
                    {editing ? (
                      <input className="input" style={{ minHeight: 40 }} value={draft?.name ?? ""} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                    ) : (
                      <span className="font-semibold">{a.name}</span>
                    )}
                    {a.is_system && <span className="badge ml-2">標準</span>}
                  </td>
                  <td>{CATEGORY_LABEL[a.category] ?? a.category}</td>
                  <td>
                    {editing ? (
                      <select className="input" style={{ minHeight: 40 }} value={draft?.statement_line ?? ""} onChange={(e) => setDraft({ ...draft, statement_line: e.target.value })}>
                        <option value="">（帳票に出ない）</option>
                        {Object.entries(d.statement_lines).map(([k, v]) => (
                          <option key={k} value={k}>
                            {v}
                          </option>
                        ))}
                      </select>
                    ) : (
                      a.statement_line_label || <span style={{ color: "var(--color-muted)" }}>—</span>
                    )}
                  </td>
                  <td>
                    {editing ? (
                      <select className="input" style={{ minHeight: 40 }} value={draft?.default_tax ?? ""} onChange={(e) => setDraft({ ...draft, default_tax: e.target.value })}>
                        {Object.entries(d.tax_categories).map(([k, v]) => (
                          <option key={k} value={k}>
                            {v}
                          </option>
                        ))}
                      </select>
                    ) : (
                      a.default_tax_label
                    )}
                  </td>
                  <td>{a.is_active ? "有効" : "無効"}</td>
                  <td className="whitespace-nowrap">
                    {canEdit && !editing && (
                      <button className="btn btn-sm" onClick={() => setDraft({ id: a.id, name: a.name, statement_line: a.statement_line, default_tax: a.default_tax })}>
                        編集
                      </button>
                    )}
                    {canEdit && editing && (
                      <span className="flex gap-1">
                        <button className="btn btn-sm btn-primary" onClick={() => save(a, { name: draft?.name, statement_line: draft?.statement_line, default_tax: draft?.default_tax })}>
                          保存
                        </button>
                        <button className="btn btn-sm" onClick={() => setDraft(null)}>
                          やめる
                        </button>
                        {!a.is_system && (
                          <button className="btn btn-sm" onClick={() => save(a, { is_active: !a.is_active })}>
                            {a.is_active ? "無効にする" : "有効にする"}
                          </button>
                        )}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {adding && (
        <AddAccountModal
          lines={d.statement_lines}
          taxes={d.tax_categories}
          onClose={() => setAdding(false)}
          onSaved={async () => {
            setAdding(false);
            toast.show({ message: "科目を追加しました" });
            await q.reload();
          }}
        />
      )}
    </div>
  );
}

function AddAccountModal({ lines, taxes, onClose, onSaved }: { lines: Record<string, string>; taxes: Record<string, string>; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ key: "", name: "", category: "expense", statement_line: "custom", default_tax: "purchase_10", code: "" });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title="科目を追加"
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !f.key || !f.name}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              try {
                await api("/api/accounting/accounts", { method: "POST", body: f });
                onSaved();
              } catch (e) {
                setErr(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            追加する
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="科目キー（英数字・アンダースコア）" hint="仕訳や出力で使う内部の名前。あとから変えられません。">
          <input className="input" value={f.key} onChange={(e) => setF({ ...f, key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "") })} placeholder="例: cleaning" />
        </Field>
        <Field label="科目名">
          <input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="例: 清掃費" />
        </Field>
        <Field label="区分">
          <select className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
            {Object.entries(CATEGORY_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </Field>
        <Field label="帳票の行（収支内訳書・決算書）">
          <select className="input" value={f.statement_line} onChange={(e) => setF({ ...f, statement_line: e.target.value })}>
            <option value="">（帳票に出ない）</option>
            {Object.entries(lines).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </Field>
        <Field label="既定の税区分">
          <select className="input" value={f.default_tax} onChange={(e) => setF({ ...f, default_tax: e.target.value })}>
            {Object.entries(taxes).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </Field>
        <Field label="コード（任意）">
          <input className="input num" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} placeholder="例: 640" />
        </Field>
      </div>
      <ErrorBox error={err} />
    </Modal>
  );
}

// --- AI 読取 ------------------------------------------------------------------------------------
function AiSettingsTab({ canEdit }: { canEdit: boolean }) {
  const { withReauth } = useSession();
  const toast = useToast();
  const q = useLoad<AiSettings>("/api/expenses/settings/ai");
  const [f, setF] = useState<{ ai_enabled: boolean; ai_monthly_budget_jpy: string } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    if (q.data && f === null) setF({ ai_enabled: !!q.data.enabled, ai_monthly_budget_jpy: String(Math.round(Number(q.data.budget_jpy ?? 3000))) });
  }, [q.data, f]);
  if (q.loading) return <Spinner />;
  if (q.error instanceof ApiError && q.error.status === 404) {
    return (
      <div className="card">
        <p className="font-semibold">AI 読取の設定はまだ使えません。</p>
        <p className="text-sm mt-1" style={{ color: "var(--color-muted)" }}>
          領収書・経費登録の機能が有効になると、ここで AI 読取のオン・オフと月の予算を設定できます。
        </p>
      </div>
    );
  }
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const d = q.data;
  return (
    <div className="card space-y-3">
      <h2 className="text-lg font-bold">領収書の AI 読取</h2>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        オンにすると、店舗で撮った領収書の日付・金額・支払先を AI が読み取って入力を補助します。読み取った値は人が確認してから登録され、勘定科目や税額を AI が決めることはありません。
        送るのは縮小した画像だけで、店舗名・お客様・スタッフの名前は送りません。予算を超えた月は手入力になります。
      </p>
      {d && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
          <div className="card-2">
            <div style={{ color: "var(--color-muted)" }}>サーバー側の接続先</div>
            <div className="font-semibold">{d.server_provider || "—"}{d.model ? ` / ${d.model}` : ""}</div>
            {d.enabled && !d.available && d.reason ? <span className="badge badge-warn">{d.reason}</span> : null}
          </div>
          <div className="card-2">
            <div style={{ color: "var(--color-muted)" }}>今月の使用額（目安）</div>
            <div className="font-semibold num">{yen(d.spent_jpy ?? 0)}</div>
            <div className="text-xs" style={{ color: "var(--color-muted)" }}>予算 {Number(d.budget_jpy) ? yen(d.budget_jpy) : "上限なし"}</div>
          </div>
          <div className="card-2">
            <div style={{ color: "var(--color-muted)" }}>状態</div>
            <div className="font-semibold">{d.enabled ? (d.available ? "オン（使えます）" : "オン（今は手入力）") : "オフ（手入力）"}</div>
          </div>
        </div>
      )}
      {f && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 items-end">
          <Toggle checked={f.ai_enabled} onChange={(v) => setF({ ...f, ai_enabled: v })} label="領収書の AI 読取を使う" />
          <Field label="月の予算（円。0 なら上限なし）">
            <input className="input num" type="number" min={0} value={f.ai_monthly_budget_jpy} onChange={(e) => setF({ ...f, ai_monthly_budget_jpy: e.target.value })} disabled={!canEdit} />
          </Field>
        </div>
      )}
      <ErrorBox error={err} />
      {canEdit && f && (
        <button
          className="btn btn-primary"
          onClick={async () => {
            setErr(null);
            try {
              await withReauth(() => api("/api/expenses/settings/ai", { method: "PUT", body: { ai_enabled: f.ai_enabled, ai_monthly_budget_jpy: Number(f.ai_monthly_budget_jpy || 0) } }));
              toast.show({ message: "AI 読取の設定を保存しました" });
              await q.reload();
            } catch (e) {
              setErr(e);
            }
          }}
        >
          保存する
        </button>
      )}
    </div>
  );
}

// --- 申告情報（個人事業主） ----------------------------------------------------------------------
const EMPTY_PROFILE: TaxProfile = {
  individual_name: "", trade_name: "", business_start_date: null, blue_approval_status: "none", blue_approved_from_year: null, blue_application_date: null,
  bookkeeping_method: "simple", consumption_tax_status: "exempt", invoice_registration_number: "", note: "",
};

function TaxProfileTab({ taxpayer, canEdit }: { taxpayer: TaxPayer | null; canEdit: boolean }) {
  const { withReauth } = useSession();
  const toast = useToast();
  const q = useLoad<TaxProfileResponse>(taxpayer ? "/api/tax/profile" : null, [taxpayer?.id]);
  const [f, setF] = useState<TaxProfile | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    if (q.data && f === null) setF({ ...EMPTY_PROFILE, ...(q.data.profile ?? {}) });
  }, [q.data, f]);
  if (!taxpayer) return <Empty>先に「事業者・店舗・権限」でこの店舗の事業者を設定してください。</Empty>;
  if (taxpayer.entity_type !== "sole_proprietor") {
    return (
      <div className="card">
        <p className="font-semibold">この事業者は法人です。</p>
        <p className="text-sm mt-1" style={{ color: "var(--color-muted)" }}>
          所得税の確定申告の支援は個人事業主の事業者だけが対象です。法人税の申告は対象外です。
        </p>
      </div>
    );
  }
  if (q.loading || !f) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const set = <K extends keyof TaxProfile>(k: K, v: TaxProfile[K]) => setF({ ...f, [k]: v });
  return (
    <div className="card space-y-3">
      <h2 className="text-lg font-bold">申告情報（個人事業主）</h2>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        確定申告で使う基本の情報です。青色申告の承認状況は申告書の計算（青色申告特別控除）に使います。マイナンバーは入力しません（国税庁の画面でご本人が入力します）。
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="氏名">
          <input className="input" value={f.individual_name} onChange={(e) => set("individual_name", e.target.value)} disabled={!canEdit} />
        </Field>
        <Field label="屋号">
          <input className="input" value={f.trade_name} onChange={(e) => set("trade_name", e.target.value)} disabled={!canEdit} />
        </Field>
        <Field label="開業日" hint="年の途中で開業した年は、開業日からの集計になります。">
          <input className="input" type="date" value={f.business_start_date ?? ""} onChange={(e) => set("business_start_date", e.target.value || null)} disabled={!canEdit} />
        </Field>
        <Field label="青色申告の承認状況" hint="承認を受けていない年は白色申告として計算します。申請は 3/15 まで（1/16 以後の開業は開業から 2 か月以内）。">
          <select className="input" value={f.blue_approval_status} onChange={(e) => set("blue_approval_status", e.target.value as TaxProfile["blue_approval_status"])} disabled={!canEdit}>
            <option value="none">申請していない（白色申告）</option>
            <option value="applied">申請中</option>
            <option value="approved">承認済み（青色申告）</option>
            <option value="revoked">取消</option>
          </select>
        </Field>
        <Field label="青色申告を適用する最初の年分">
          <input className="input num" type="number" min={2000} max={2100} value={f.blue_approved_from_year ?? ""} onChange={(e) => set("blue_approved_from_year", e.target.value ? Number(e.target.value) : null)} disabled={!canEdit} />
        </Field>
        <Field label="青色申告承認申請書の提出日">
          <input className="input" type="date" value={f.blue_application_date ?? ""} onChange={(e) => set("blue_application_date", e.target.value || null)} disabled={!canEdit} />
        </Field>
        <Field label="記帳の方法">
          <select className="input" value={f.bookkeeping_method} onChange={(e) => set("bookkeeping_method", e.target.value as TaxProfile["bookkeeping_method"])} disabled={!canEdit}>
            <option value="simple">簡易簿記（10万円控除まで）</option>
            <option value="double">複式簿記</option>
          </select>
        </Field>
        <Field label="消費税" hint="免税事業者以外は、消費税の申告が未対応のため申告書を完成にできません（税理士にご相談ください）。">
          <select className="input" value={f.consumption_tax_status} onChange={(e) => set("consumption_tax_status", e.target.value as TaxProfile["consumption_tax_status"])} disabled={!canEdit}>
            <option value="exempt">免税事業者</option>
            <option value="taxable_general">課税事業者（本則）</option>
            <option value="taxable_simplified">課税事業者（簡易課税）</option>
            <option value="taxable_2wari">課税事業者（2割特例）</option>
          </select>
        </Field>
        <Field label="適格請求書発行事業者の登録番号（登録している場合）">
          <input className="input" value={f.invoice_registration_number} onChange={(e) => set("invoice_registration_number", e.target.value.toUpperCase())} placeholder="T1234567890123" disabled={!canEdit} />
        </Field>
        <Field label="メモ">
          <input className="input" value={f.note} onChange={(e) => set("note", e.target.value)} disabled={!canEdit} />
        </Field>
      </div>
      <ErrorBox error={err} />
      {canEdit ? (
        <button
          className="btn btn-primary"
          onClick={async () => {
            setErr(null);
            try {
              await withReauth(() => api("/api/tax/profile", { method: "PUT", body: f }));
              toast.show({ message: "申告情報を保存しました" });
              await q.reload();
            } catch (e) {
              setErr(e);
            }
          }}
        >
          保存する（パスワードの再確認）
        </button>
      ) : (
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          申告情報の変更はオーナー本人だけができます。
        </p>
      )}
    </div>
  );
}

// --- 店舗の承認設定 ------------------------------------------------------------------------------
function StoreApprovalTab({ canEdit }: { canEdit: boolean }) {
  const { withReauth, store } = useSession();
  const toast = useToast();
  const q = useLoad<StoreSettings>("/api/store/settings");
  const [f, setF] = useState<{ receipt_capture_enabled: boolean; manager_can_approve_expenses: boolean; manager_approve_limit: string } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    if (q.data && f === null) {
      const d = q.data as unknown as Record<string, unknown>;
      setF({
        receipt_capture_enabled: d.receipt_capture_enabled !== false,
        manager_can_approve_expenses: !!d.manager_can_approve_expenses,
        manager_approve_limit: d.manager_approve_limit ? String(d.manager_approve_limit) : "",
      });
    }
  }, [q.data, f]);
  if (q.loading || !f) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  return (
    <div className="card space-y-3">
      <h2 className="text-lg font-bold">{store?.name} の領収書・承認の設定</h2>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        店舗ごとの設定です。承認は既定ではオーナー・会社管理者・経理担当だけができます。店長に承認を許すと、この店舗の領収書だけ承認できるようになります（科目は候補から選びます）。
      </p>
      <Toggle checked={f.receipt_capture_enabled} onChange={(v) => setF({ ...f, receipt_capture_enabled: v })} label="店舗タブレットで領収書を登録できる" />
      <Toggle checked={f.manager_can_approve_expenses} onChange={(v) => setF({ ...f, manager_can_approve_expenses: v })} label="店長がこの店舗の領収書を承認できる" />
      <Field label="店長が承認できる 1 件の上限（円。空なら制限なし）">
        <input className="input num" type="number" min={0} value={f.manager_approve_limit} onChange={(e) => setF({ ...f, manager_approve_limit: e.target.value })} disabled={!f.manager_can_approve_expenses} />
      </Field>
      <ErrorBox error={err} />
      {canEdit ? (
        <button
          className="btn btn-primary"
          onClick={async () => {
            setErr(null);
            try {
              await withReauth(() =>
                api("/api/store/settings", {
                  method: "PUT",
                  body: {
                    receipt_capture_enabled: f.receipt_capture_enabled,
                    manager_can_approve_expenses: f.manager_can_approve_expenses,
                    manager_approve_limit: f.manager_approve_limit === "" ? null : f.manager_approve_limit,
                  },
                }),
              );
              toast.show({ message: "店舗の設定を保存しました" });
              await q.reload();
            } catch (e) {
              setErr(e);
            }
          }}
        >
          保存する
        </button>
      ) : (
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          店舗設定の変更権限がありません。
        </p>
      )}
    </div>
  );
}

// --- 電子帳簿保存法 ------------------------------------------------------------------------------
const DENCHO_ITEMS: { label: string; status: "ok" | "warn" | "todo"; text: string }[] = [
  { label: "電子取引データと紙の区別", status: "ok", text: "原本ごとに「紙を撮影」「スキャン」「電子取引データ（PDF・メール）」を記録します。スタッフは登録のときに選びます。" },
  { label: "真実性（訂正・削除の履歴）", status: "ok", text: "原本は上書きせず、削除は理由付きの論理削除。登録・承認・差戻し・却下・訂正の履歴を残します。タイムスタンプは付与しません。" },
  { label: "事務処理規程", status: "todo", text: "「訂正削除の防止に関する事務処理規程」を定めて備え付ける方法を選ぶ場合は、規程を作成して保管してください（NOXIA はひな形の提供を予定）。" },
  { label: "可視性（検索）", status: "ok", text: "取引年月日・金額・取引先の範囲や組合せで原本を検索できます（証憑の画面）。" },
  { label: "見読性（表示・印刷）", status: "ok", text: "原本は画面で拡大・回転して確認でき、保存もできます。ディスプレイ・プリンタの備付けは利用者側で。" },
  { label: "スキャナ保存の要件（200dpi・カラー・入力期間）", status: "warn", text: "画像の大きさの目安と撮影日時を記録しますが、タブレットの写真が法令上のスキャナ保存の要件を満たすかは税理士に確認してください。満たさない場合は紙の原本を保存する運用にしてください。" },
  { label: "保存期間", status: "ok", text: "原本の保存期限（個人 7 年、欠損金の繰越があれば 10 年の目安）を記録し、期限内は物理削除しません。" },
  { label: "バックアップ", status: "warn", text: "DB と原本ファイルを対にしてバックアップしてください（scripts/backup-db.ps1 は両方を保存します）。暗号鍵は別の場所に保管します。" },
];

function DenchoTab() {
  return (
    <div className="card space-y-3">
      <h2 className="text-lg font-bold">電子帳簿保存法の対応状況</h2>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        画像を保存しただけで電子帳簿保存法に完全に対応しているとは言えません。要件ごとに NOXIA ができていること・利用者側で決めることを示します。最終的な判断は税理士・税務署にご確認ください。
      </p>
      <ul className="space-y-2">
        {DENCHO_ITEMS.map((it) => (
          <li key={it.label} className="card-2 flex items-start gap-3">
            <span className={`badge ${it.status === "ok" ? "badge-ok" : it.status === "warn" ? "badge-warn" : "badge-danger"}`} style={{ minWidth: 72, justifyContent: "center" }}>
              {it.status === "ok" ? "対応" : it.status === "warn" ? "要確認" : "未対応"}
            </span>
            <div>
              <div className="font-semibold">{it.label}</div>
              <div className="text-sm" style={{ color: "var(--color-muted)" }}>
                {it.text}
              </div>
            </div>
          </li>
        ))}
      </ul>
      <p className="text-sm">
        参考: 国税庁「電子帳簿保存法」
        <a className="underline ml-1" href="https://www.nta.go.jp/law/joho-zeikaishaku/sonota/jirei/02.htm" target="_blank" rel="noopener noreferrer">
          https://www.nta.go.jp/law/joho-zeikaishaku/sonota/jirei/02.htm ↗
        </a>
      </p>
    </div>
  );
}
