"use client";

/**
 * 確定申告（所得税）: 事業所得の集計、所得控除などの入力、計算（版）、未対応項目、完成、出力。
 * 税務上の入力・計算・完成・出力はオーナー本人（tax.return.edit / export、パスワードの再確認）。
 */
import Link from "next/link";
import { useEffect, useState } from "react";
import { FieldErrors, FreeeHandlesNotice, RequireCaps, Tabs, YearPicker } from "@/components/common";
import { DataTable } from "@/components/DataTable";
import { ConfirmDialog, Empty, ErrorBox, Field, PageHeader, Spinner, Stat, Toggle } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api, ApiError, downloadFile, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtDateTime, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { TaxExportRow, TaxInputs, TaxReturnData } from "@/lib/types_tax";
import { useLoad } from "@/lib/useLoad";

const STATUS_BADGE: Record<string, string> = {
  draft: "badge", in_review: "badge badge-warn", complete: "badge badge-ok", exported: "badge badge-ok", filed_by_user: "badge badge-accent", locked: "badge",
};

export default function TaxReturnPage() {
  const [year, setYear] = useState(new Date().getFullYear());
  return (
    <RequireCaps caps={["tax.return.view"]}>
      <PageHeader
        title="確定申告（所得税）"
        subtitle="NOXIA の帳簿から事業所得を集計し、所得控除などを入力して税額を計算します。申告はご本人が国税庁の作成コーナーまたは e-Tax で行います（NOXIA は送信しません）。"
        actions={<YearPicker value={year} onChange={setYear} />}
      />
      <FreeeHandlesNotice what="確定申告書の作成と電子申告" />
      <ReturnBody key={year} year={year} />
    </RequireCaps>
  );
}

function ReturnBody({ year }: { year: number }) {
  const q = useLoad<TaxReturnData>(`/api/tax/returns/${year}`, [year], { refetchOnFocus: false });
  const { can, withReauth } = useSession();
  const toast = useToast();
  const [tab, setTab] = useState("summary");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const canEdit = can("tax.return.edit");

  if (q.loading) return <Spinner />;
  if (q.error) {
    const e = q.error as ApiError;
    if (e.code === "not_individual") return <div className="card">法人の事業者は所得税の確定申告の対象外です。個人事業主の事業者を選んでください。</div>;
    return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  }
  const d = q.data;
  if (!d) return <Empty>データがありません</Empty>;
  const v = (d.computed as { values?: Record<string, string> }).values ?? {};
  const hasComputed = Object.keys(d.computed ?? {}).length > 0;

  async function act(path: string, body: unknown = {}) {
    setBusy(true);
    setErr(null);
    try {
      const res = await withReauth(() => api<TaxReturnData>(`/api/tax/returns/${year}/${path}`, { method: "POST", body, idempotencyKey: newIdempotencyKey() }));
      q.setData(res);
      toast.show({ message: "完了しました", tone: "ok" });
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center gap-3">
        <span className={STATUS_BADGE[d.status] ?? "badge"} style={{ fontSize: "1rem" }}>
          {d.status_label}
        </span>
        <span className="badge">{d.filing_type === "blue_10" ? "青色申告（10万円控除）" : "白色申告"}</span>
        <span className="badge">年度: {d.fiscal_year.status_label}</span>
        {d.profile?.blue_approval_status !== "approved" && d.filing_type === "blue_10" && <span className="badge badge-warn">青色申告の承認が未登録</span>}
        <span className="text-sm" style={{ color: "var(--color-muted)" }}>
          税制設定 {d.config.year}年分（{d.config.status === "draft" ? "公式確認中の項目あり" : d.config.status}）
        </span>
        <div className="ml-auto flex flex-wrap gap-2">
          {canEdit && (
            <button className="btn btn-primary" disabled={busy || d.status === "filed_by_user" || d.status === "locked"} onClick={() => act("compute")}>
              {busy ? "計算中…" : hasComputed ? "再計算する" : "計算する"}
            </button>
          )}
          {canEdit && hasComputed && d.status !== "complete" && d.status !== "exported" && d.status !== "filed_by_user" && (
            <button className="btn btn-ok" disabled={busy || d.unsupported_items.length > 0} onClick={() => act("complete")}>
              完成にする
            </button>
          )}
          {canEdit && (d.status === "complete" || d.status === "exported") && (
            <button className="btn" disabled={busy} onClick={() => act("reopen", { reason: "見直し" })}>
              作成中に戻す
            </button>
          )}
          <Link href="/accounting/tax-return/guide" className="btn">
            国税庁 入力ガイドへ
          </Link>
        </div>
      </div>
      {err ? <ErrorBox error={err} /> : null}
      {d.unsupported_items.length > 0 && (
        <div className="card" style={{ borderColor: "var(--color-danger)" }} role="alert">
          <p className="font-bold">未対応の項目があるため、この申告書は完成にできません。税理士・税務署にご相談ください。</p>
          <ul className="mt-2 space-y-1">
            {d.unsupported_items.map((u) => (
              <li key={u.code}>・{u.message}</li>
            ))}
          </ul>
        </div>
      )}
      {d.warnings.length > 0 && (
        <div className="card-2">
          {d.warnings.map((w) => (
            <p key={w} className="text-sm">
              ※ {w}
            </p>
          ))}
        </div>
      )}
      {hasComputed && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Stat label="事業所得" value={yen(v["business.income"])} />
          <Stat label="合計所得金額" value={yen(v["total_income"])} />
          <Stat label="課税所得" value={yen(v["taxable_income"])} />
          <Stat label="所得税＋復興特別所得税" value={yen(v["total_tax"])} />
          <Stat label={Number(v["refund"]) > 0 ? "還付される税額" : "納付する税額"} value={yen(Number(v["refund"]) > 0 ? v["refund"] : v["tax_due"])} accent={Number(v["refund"]) > 0 ? "ok" : undefined} />
        </div>
      )}
      <Tabs
        tabs={[
          { key: "summary", label: "事業収支（集計）" },
          { key: "inputs", label: "所得・控除の入力" },
          { key: "trace", label: "計算過程" },
          { key: "versions", label: `版（${d.versions.length}）` },
          { key: "exports", label: "出力" },
        ]}
        value={tab}
        onChange={setTab}
        size="sm"
      />
      {tab === "summary" && <SummaryTab d={d} />}
      {tab === "inputs" && <InputsTab d={d} year={year} canEdit={canEdit} onSaved={(nd) => q.setData(nd)} />}
      {tab === "trace" && <TraceTab d={d} />}
      {tab === "versions" && <VersionsTab d={d} year={year} canEdit={canEdit} onChanged={(nd) => q.setData(nd)} />}
      {tab === "exports" && <ExportsTab d={d} year={year} />}
    </div>
  );
}

function SummaryTab({ d }: { d: TaxReturnData }) {
  const rows = (d.computed as { statement_rows?: { no: string; key: string; label: string; amount: string }[] }).statement_rows;
  const bs = d.business_summary as { by_store?: { store: string; income: string; expense: string; profit: string }[]; monthly?: { month: string; sales: string; purchases: string }[] };
  if (!rows) return <Empty>「計算する」を押すと、帳簿から事業収支を集計します。</Empty>;
  return (
    <div className="grid md:grid-cols-2 gap-4">
      <div className="card">
        <h2 className="text-lg font-bold mb-2">{d.filing_type === "blue_10" ? "青色申告決算書（一般用）損益計算書" : "収支内訳書（一般用）"}の行</h2>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 60 }}>行</th>
              <th>項目</th>
              <th style={{ textAlign: "right" }}>金額</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.key}-${i}`} style={{ fontWeight: ["revenue_total", "cogs", "gross_profit", "expenses_total", "business_income"].includes(r.key) ? 700 : 400 }}>
                <td>{r.no}</td>
                <td>{r.label}</td>
                <td className="num" style={{ textAlign: "right" }}>
                  {yen(r.amount)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-sm mt-2" style={{ color: "var(--color-muted)" }}>
          様式の行番号は現行様式（令和5年分以降用）。令和8年分の様式が公表されたら設定を更新します。
        </p>
      </div>
      <div className="space-y-4">
        {bs.by_store && bs.by_store.length > 0 && (
          <div className="card">
            <h2 className="text-lg font-bold mb-2">店舗別の内訳（申告は事業者で 1 件）</h2>
            <DataTable
              columns={[
                { key: "store", label: "店舗" },
                { key: "income", label: "収益", align: "right", render: (r) => yen(r.income) },
                { key: "expense", label: "費用", align: "right", render: (r) => yen(r.expense) },
                { key: "profit", label: "利益", align: "right", render: (r) => yen(r.profit) },
              ]}
              rows={bs.by_store}
              rowKey={(r) => r.store}
            />
          </div>
        )}
        {bs.monthly && (
          <div className="card">
            <h2 className="text-lg font-bold mb-2">月別の売上・仕入（決算書 2 ページ目）</h2>
            <DataTable
              columns={[
                { key: "month", label: "月" },
                { key: "sales", label: "売上", align: "right", render: (r) => yen(r.sales) },
                { key: "purchases", label: "仕入", align: "right", render: (r) => yen(r.purchases) },
              ]}
              rows={bs.monthly}
              rowKey={(r) => r.month}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function num(v: string | number | undefined | null): string {
  return v === undefined || v === null ? "" : String(v);
}

function InputsTab({ d, year, canEdit, onSaved }: { d: TaxReturnData; year: number; canEdit: boolean; onSaved: (d: TaxReturnData) => void }) {
  const { withReauth } = useSession();
  const toast = useToast();
  const [f, setF] = useState<TaxInputs>(d.inputs ?? {});
  const [filing, setFiling] = useState(d.filing_type);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => setF(d.inputs ?? {}), [d.inputs]);
  const set = (k: keyof TaxInputs, v: unknown) => setF((s) => ({ ...s, [k]: v }));
  const sub = <K extends keyof TaxInputs>(k: K, patch: Record<string, unknown>) => setF((s) => ({ ...s, [k]: { ...(s[k] as object), ...patch } }));
  const locked = !canEdit || d.status === "filed_by_user" || d.status === "locked";

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const res = await withReauth(() => api<TaxReturnData>(`/api/tax/returns/${year}/inputs`, { method: "PUT", body: { inputs: f, filing_type: filing, version: d.version } }));
      onSaved(res);
      toast.show({ message: "入力を保存しました。「計算する」で税額を計算します。", tone: "ok" });
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  const M = ({ label, k, hint }: { label: string; k: keyof TaxInputs; hint?: string }) => (
    <Field label={label} hint={hint}>
      <input className="input num" inputMode="numeric" value={num(f[k] as string)} onChange={(e) => set(k, e.target.value)} disabled={locked} />
    </Field>
  );

  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-lg font-bold mb-2">申告の区分</h2>
        <div className="grid grid-cols-2 gap-2 max-w-xl">
          <button className={`btn ${filing === "white" ? "btn-primary" : ""}`} disabled={locked} onClick={() => setFiling("white")}>
            白色申告（収支内訳書）
          </button>
          <button className={`btn ${filing === "blue_10" ? "btn-primary" : ""}`} disabled={locked} onClick={() => setFiling("blue_10")}>
            青色申告 10万円控除（決算書）
          </button>
        </div>
        <p className="text-sm mt-2" style={{ color: "var(--color-muted)" }}>
          青色申告は税務署の承認が必要です（承認状況は会計設定の「申告情報」）。55万円・65万円控除は未対応です。
        </p>
      </div>
      <div className="grid md:grid-cols-2 gap-4">
        <div className="card space-y-3">
          <h2 className="text-lg font-bold">事業の追加項目</h2>
          <M label="専従者控除（白色）／専従者給与（青色）" k="family_employee" hint="家族に払った給与。無ければ空欄" />
          <M label="純損失の繰越控除（青色のみ・前年以前 3 年の赤字）" k="loss_carryforward" />
        </div>
        <div className="card space-y-3">
          <h2 className="text-lg font-bold">給与・その他の所得</h2>
          <Field label="給与の収入金額（源泉徴収票の支払金額）">
            <input className="input num" inputMode="numeric" value={num(f.salary?.revenue)} onChange={(e) => sub("salary", { revenue: e.target.value })} disabled={locked} />
          </Field>
          <Field label="給与の源泉徴収税額">
            <input className="input num" inputMode="numeric" value={num(f.salary?.withheld)} onChange={(e) => sub("salary", { withheld: e.target.value })} disabled={locked} />
          </Field>
          <Field label="その他の所得（雑所得）の金額" hint="不動産・譲渡・株式などは未対応（入れると完成にできません）">
            <input
              className="input num"
              inputMode="numeric"
              value={num(f.other_incomes?.[0]?.amount)}
              onChange={(e) => set("other_incomes", e.target.value ? [{ kind: "miscellaneous", amount: e.target.value, label: "雑所得" }] : [])}
              disabled={locked}
            />
          </Field>
        </div>
        <div className="card space-y-3">
          <h2 className="text-lg font-bold">所得控除（支払額を入力）</h2>
          <M label="社会保険料控除（国民健康保険・国民年金など）" k="social_insurance" />
          <M label="小規模企業共済等掛金控除（iDeCo など）" k="small_business_mutual" />
          <M label="寄附金（ふるさと納税など）" k="donation" />
          <div className="grid grid-cols-2 gap-2">
            <Field label="医療費の支払額">
              <input className="input num" inputMode="numeric" value={num(f.medical?.paid)} onChange={(e) => sub("medical", { paid: e.target.value })} disabled={locked} />
            </Field>
            <Field label="保険金などで補填された額">
              <input className="input num" inputMode="numeric" value={num(f.medical?.reimbursed)} onChange={(e) => sub("medical", { reimbursed: e.target.value })} disabled={locked} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Field label="地震保険料">
              <input className="input num" inputMode="numeric" value={num(f.earthquake_insurance?.earthquake)} onChange={(e) => sub("earthquake_insurance", { earthquake: e.target.value })} disabled={locked} />
            </Field>
            <Field label="旧長期損害保険料">
              <input className="input num" inputMode="numeric" value={num(f.earthquake_insurance?.old_long_term)} onChange={(e) => sub("earthquake_insurance", { old_long_term: e.target.value })} disabled={locked} />
            </Field>
          </div>
        </div>
        <div className="card space-y-3">
          <h2 className="text-lg font-bold">生命保険料控除（証明書の支払額）</h2>
          {(["new_general", "old_general", "care", "new_pension", "old_pension"] as const).map((k) => (
            <Field key={k} label={{ new_general: "一般（新契約）", old_general: "一般（旧契約）", care: "介護医療", new_pension: "個人年金（新契約）", old_pension: "個人年金（旧契約）" }[k]}>
              <input className="input num" inputMode="numeric" value={num(f.life_insurance?.[k])} onChange={(e) => sub("life_insurance", { [k]: e.target.value })} disabled={locked} />
            </Field>
          ))}
          <Toggle checked={!!f.life_insurance?.has_under23} onChange={(v) => sub("life_insurance", { has_under23: v })} label="23歳未満の扶養親族がいる（令和8年分の特例）" />
        </div>
        <div className="card space-y-3">
          <h2 className="text-lg font-bold">配偶者・扶養親族</h2>
          <Toggle checked={!!f.spouse?.has} onChange={(v) => sub("spouse", { has: v })} label="配偶者がいる" />
          {f.spouse?.has && (
            <>
              <Field label="配偶者の合計所得金額（給与だけなら収入 − 給与所得控除）">
                <input className="input num" inputMode="numeric" value={num(f.spouse?.income)} onChange={(e) => sub("spouse", { income: e.target.value })} disabled={locked} />
              </Field>
              <Toggle checked={!!f.spouse?.age70} onChange={(v) => sub("spouse", { age70: v })} label="配偶者は 70 歳以上" />
            </>
          )}
          <div className="grid grid-cols-2 gap-2">
            {(["general", "specific", "elderly", "elderly_cohabiting"] as const).map((k) => (
              <Field key={k} label={{ general: "一般の扶養親族（16歳以上）", specific: "特定扶養親族（19〜22歳）", elderly: "老人扶養親族（70歳以上）", elderly_cohabiting: "同居老親等" }[k] + "（人数）"}>
                <input className="input num" inputMode="numeric" value={num(f.dependents?.[k])} onChange={(e) => sub("dependents", { [k]: Number(e.target.value || 0) })} disabled={locked} />
              </Field>
            ))}
          </div>
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            特定親族特別控除（19〜22歳で所得 62 万円超）は「計算過程」の注意を参照。氏名・マイナンバーは入力しません（作成コーナーで入力）。
          </p>
        </div>
        <div className="card space-y-3">
          <h2 className="text-lg font-bold">本人の区分・源泉・予定納税</h2>
          <Toggle checked={!!f.personal?.single_parent} onChange={(v) => sub("personal", { single_parent: v })} label="ひとり親" />
          <Toggle checked={!!f.personal?.widow} onChange={(v) => sub("personal", { widow: v })} label="寡婦" />
          <Toggle checked={!!f.personal?.working_student} onChange={(v) => sub("personal", { working_student: v })} label="勤労学生" />
          <M label="源泉徴収税額（給与以外。報酬などで引かれた分）" k="withheld_tax" />
          <M label="予定納税額（第1期・第2期の合計）" k="estimated_tax_paid" />
          <M label="住宅借入金等特別控除（入れると未対応になります）" k="housing_loan_credit" />
        </div>
      </div>
      {err ? (
        <div>
          <ErrorBox error={err} />
          <FieldErrors details={(err as ApiError).details} />
        </div>
      ) : null}
      {!locked && (
        <div className="flex gap-3">
          <button className="btn btn-primary btn-lg" disabled={busy} onClick={save}>
            {busy ? "保存中…" : "入力を保存する"}
          </button>
        </div>
      )}
    </div>
  );
}

function TraceTab({ d }: { d: TaxReturnData }) {
  const c = d.computed as { trace?: { key: string; label: string; value: string; basis: string }[]; deductions?: { key: string; label: string; amount: string; basis: string }[]; notice?: string };
  if (!c.trace) return <Empty>「計算する」を押すと計算過程を表示します。</Empty>;
  return (
    <div className="grid md:grid-cols-2 gap-4">
      <div className="card">
        <h2 className="text-lg font-bold mb-2">所得控除</h2>
        <DataTable
          columns={[
            { key: "label", label: "控除" },
            { key: "amount", label: "金額", align: "right", render: (r) => yen(r.amount) },
            { key: "basis", label: "根拠" },
          ]}
          rows={c.deductions ?? []}
          rowKey={(r) => r.key}
        />
      </div>
      <div className="card">
        <h2 className="text-lg font-bold mb-2">計算過程（所得 → 控除 → 課税所得 → 税額 → 納付／還付）</h2>
        <DataTable
          columns={[
            { key: "label", label: "項目" },
            { key: "value", label: "金額", align: "right", render: (r) => yen(r.value) },
            { key: "basis", label: "根拠・端数処理" },
          ]}
          rows={c.trace}
          rowKey={(r) => r.key}
        />
        <p className="text-sm mt-2" style={{ color: "var(--color-muted)" }}>
          {c.notice}
        </p>
      </div>
    </div>
  );
}

function VersionsTab({ d, year, canEdit, onChanged }: { d: TaxReturnData; year: number; canEdit: boolean; onChanged: (d: TaxReturnData) => void }) {
  const { withReauth } = useSession();
  const [confirm, setConfirm] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  return (
    <div className="card">
      <p className="text-sm mb-2" style={{ color: "var(--color-muted)" }}>
        計算のたびに版を保存します。申告に使った版に「この版で申告した」を記録してください（NOXIA は申告書を送信しません）。
      </p>
      <DataTable
        columns={[
          { key: "version_no", label: "版", render: (r) => `v${r.version_no}` },
          { key: "created_at", label: "計算日時", render: (r) => fmtDateTime(r.created_at) },
          { key: "created_by", label: "実行者" },
          { key: "reason", label: "理由" },
          { key: "tax_due", label: "納付", align: "right", render: (r) => yen(r.tax_due) },
          { key: "refund", label: "還付", align: "right", render: (r) => yen(r.refund) },
          { key: "unsupported", label: "未対応", align: "right" },
          { key: "is_final", label: "", render: (r) => (r.is_final ? <span className="badge badge-accent">申告に使用</span> : canEdit && (d.status === "complete" || d.status === "exported") ? <button className="btn btn-sm" onClick={() => setConfirm(r.id)}>この版で申告した</button> : null) },
        ]}
        rows={d.versions}
        rowKey={(r) => r.id}
        emptyText="まだ計算していません。"
      />
      {err ? <ErrorBox error={err} /> : null}
      {confirm !== null && (
        <ConfirmDialog
          title="申告済みとして記録"
          message="国税庁の作成コーナーまたは e-Tax で申告を終えたことを NOXIA に記録します。以後この年度の入力・再計算はできません（「作成中に戻す」で解除できます）。"
          confirmLabel="記録する"
          busy={busy}
          onCancel={() => setConfirm(null)}
          onConfirm={async () => {
            setBusy(true);
            try {
              onChanged(await withReauth(() => api<TaxReturnData>(`/api/tax/returns/${year}/mark-filed`, { method: "POST", body: { version_id: confirm }, idempotencyKey: newIdempotencyKey() })));
              setConfirm(null);
            } catch (e) {
              setErr(e);
            } finally {
              setBusy(false);
            }
          }}
        />
      )}
    </div>
  );
}

function ExportsTab({ d, year }: { d: TaxReturnData; year: number }) {
  const { can, withReauth } = useSession();
  const toast = useToast();
  const q = useLoad<TaxExportRow[]>(`/api/tax/returns/${year}/exports`, [year, d.versions.length], { refetchOnFocus: false });
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const canExport = can("tax.return.export");
  const ready = d.status === "complete" || d.status === "exported" || d.status === "filed_by_user";

  async function make(format: string) {
    setBusy(format);
    setErr(null);
    try {
      const res = await withReauth(() => api<{ id: number; filename: string }>(`/api/tax/returns/${year}/exports`, { method: "POST", body: { format }, idempotencyKey: newIdempotencyKey() }));
      await q.reload();
      await withReauth(() => downloadFile(`/api/tax/returns/${year}/exports/${res.id}/file`, res.filename));
      toast.show({ message: "出力しました", tone: "ok" });
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="card space-y-3">
        <h2 className="text-lg font-bold">出力</h2>
        {!ready && <p className="badge badge-warn">「完成にする」のあとに出力できます（Excel は計算後ならいつでも）。</p>}
        <div className="flex flex-wrap gap-2">
          <button className="btn" disabled={!canExport || !!busy || !ready} onClick={() => make("pdf_forms")}>
            転記用 PDF（収支内訳書／決算書・申告書の数字）
          </button>
          <button className="btn" disabled={!canExport || !!busy || Object.keys(d.computed ?? {}).length === 0} onClick={() => make("xlsx_return")}>
            Excel（事業収支・控除・計算過程・ガイド金額一覧）
          </button>
          <button className="btn" disabled title={d.xtx_released ? "" : "XTX は公式 e-Tax ソフトでの取込試験が完了するまで公開していません"}>
            XTX（e-Tax 申告データ）{d.xtx_released ? "" : "：未公開"}
          </button>
        </div>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          転記用 PDF は国税庁の様式そのものではありません。XTX は作成コーナーには取り込めず、e-Tax ソフトで読み込むためのもので、検証が完了した年度だけ公開します。CSV を申告書として取り込むことはできません。
        </p>
        {err ? <ErrorBox error={err} /> : null}
      </div>
      <div className="card">
        <h2 className="text-lg font-bold mb-2">出力の履歴</h2>
        {q.loading ? (
          <Spinner />
        ) : (
          <DataTable
            columns={[
              { key: "generated_at", label: "日時", render: (r) => fmtDateTime(r.generated_at) },
              { key: "format", label: "形式" },
              { key: "version_no", label: "版", render: (r) => `v${r.version_no}` },
              { key: "filename", label: "ファイル" },
              { key: "sha256", label: "SHA-256", render: (r) => <span className="text-xs">{r.sha256.slice(0, 16)}…</span> },
              { key: "download_count", label: "DL", align: "right" },
              { key: "dl", label: "", render: (r) => (r.is_released ? <button className="btn btn-sm" onClick={() => withReauth(() => downloadFile(`/api/tax/returns/${year}/exports/${r.id}/file`, r.filename)).catch((e) => toast.show({ message: errorMessage(e), tone: "error" }))}>ダウンロード</button> : <span className="badge">非公開</span>) },
            ]}
            rows={q.data ?? []}
            rowKey={(r) => r.id}
            emptyText="まだ出力していません。"
          />
        )}
      </div>
      {d.profile === null && (
        <div className="card-2">
          申告情報（屋号・開業日・青色承認・消費税の区分）が未登録です。
          <Link href="/accounting/settings" className="btn btn-sm ml-2">
            会計設定で登録
          </Link>
        </div>
      )}
    </div>
  );
}
