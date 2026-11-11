"use client";

import { useState } from "react";
import { Empty, ErrorBox, Field, Modal, PageHeader, Spinner } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api, downloadFile, newIdempotencyKey, uploadFile } from "@/lib/api";
import { CALC_PERIODS, CONTRACT_TYPES } from "@/lib/accounting";
import { fmtDate } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { CastContract, Employee } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type ContractsResp = { contracts: CastContract[]; checklist: { key: string; label: string }[] };
type PayItemRow = { code: string; label: string; withholding_target: boolean };

const REVIEW_BADGE: Record<string, string> = { unreviewed: "badge badge-warn", needs_review: "badge badge-orange", confirmed: "badge badge-ok" };

export default function ContractsPage() {
  const { can } = useSession();
  const q = useLoad<ContractsResp>("/api/accounting/contracts");
  const emps = useLoad<Employee[]>("/api/staff/employees?active=1");
  const [editing, setEditing] = useState<CastContract | "new" | null>(null);
  const [reviewing, setReviewing] = useState<CastContract | null>(null);
  const [advising, setAdvising] = useState<CastContract | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const contracts = q.data?.contracts ?? [];
  const withoutContract = (emps.data ?? []).filter((e) => !contracts.some((c) => c.employee === e.id));

  return (
    <div className="space-y-4">
      <PageHeader
        title="キャスト契約・税務区分"
        subtitle="契約書の名称（雇用・業務委託）だけで税務区分は決まりません。働き方の実態を確認し、経営者が「給与所得」か「ホステス等の報酬・料金」かを確定してください。確定するまで日払いの源泉所得税は計算せず「要確認」になります。"
        actions={
          can("contracts.edit") && (
            <button className="btn btn-primary" onClick={() => setEditing("new")}>
              ＋ 契約を登録
            </button>
          )
        }
      />
      <ErrorBox error={q.error || err} onRetry={() => q.reload(false)} />
      {q.loading && <Spinner />}
      {withoutContract.length > 0 && (
        <div className="card-2" style={{ borderColor: "var(--color-warn)" }}>
          <span className="font-semibold">契約が未登録: </span>
          {withoutContract.map((e) => e.display_name).join("、")}
          <span className="text-sm ml-2" style={{ color: "var(--color-muted)" }}>
            （日払いの源泉所得税は「要確認」になります）
          </span>
        </div>
      )}
      {q.data && contracts.length === 0 && <Empty>契約が登録されていません</Empty>}
      {contracts.length > 0 && (
        <div className="card scroll-x">
          <table className="table">
            <thead>
              <tr>
                <th>キャスト</th>
                <th>契約区分</th>
                <th>契約期間</th>
                <th>報酬計算期間</th>
                <th>税務区分</th>
                <th>確認状況</th>
                <th>その他控除</th>
                <th>契約書</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {contracts.map((c) => (
                <tr key={c.id}>
                  <td className="font-semibold">{c.employee_name}</td>
                  <td>{c.contract_type_label}</td>
                  <td className="num text-sm">
                    {fmtDate(c.start_date)}〜{c.end_date ? fmtDate(c.end_date) : ""}
                  </td>
                  <td>{c.calc_period_label}</td>
                  <td>{c.tax_category_label}</td>
                  <td>
                    <span className={REVIEW_BADGE[c.tax_review_status] ?? "badge"}>{c.tax_review_status_label}</span>
                    {c.tax_reviewed_by_name && (
                      <span className="block text-xs" style={{ color: "var(--color-muted)" }}>
                        {c.tax_reviewed_by_name} {c.tax_reviewed_at && fmtDate(c.tax_reviewed_at.slice(0, 10))}
                      </span>
                    )}
                  </td>
                  <td className="text-sm">{c.deductions.map((d) => `${d.label} ${d.value}${d.calc === "percent" ? "%" : "円"}`).join("、") || "—"}</td>
                  <td>
                    {c.has_document ? (
                      <button className="btn btn-sm btn-ghost" onClick={() => downloadFile(`/api/accounting/contracts/${c.id}/document`).catch(setErr)}>
                        {c.document_name || "表示"}
                      </button>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="whitespace-nowrap">
                    {can("contracts.edit") && (
                      <button className="btn btn-sm" onClick={() => setEditing(c)}>
                        編集
                      </button>
                    )}{" "}
                    {can("tax.decide") && (
                      <button className="btn btn-sm btn-primary" onClick={() => setReviewing(c)}>
                        税務区分を確認
                      </button>
                    )}{" "}
                    {can("tax.advise") && !can("tax.decide") && (
                      <button className="btn btn-sm" onClick={() => setAdvising(c)}>
                        コメント
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <PayItems />
      {editing && (
        <ContractModal
          contract={editing === "new" ? null : editing}
          employees={emps.data ?? []}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await q.reload();
          }}
        />
      )}
      {reviewing && q.data && (
        <ReviewModal
          contract={reviewing}
          checklist={q.data.checklist}
          onClose={() => setReviewing(null)}
          onSaved={async () => {
            setReviewing(null);
            await q.reload();
          }}
        />
      )}
      {advising && <AdviceModal contract={advising} onClose={() => setAdvising(null)} onSaved={async () => { setAdvising(null); await q.reload(); }} />}
    </div>
  );
}

function ContractModal({ contract, employees, onClose, onSaved }: { contract: CastContract | null; employees: Employee[]; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({
    employee: contract?.employee ?? employees[0]?.id ?? 0,
    contract_type: contract?.contract_type ?? "outsourcing",
    start_date: contract?.start_date ?? new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Tokyo" }),
    end_date: contract?.end_date ?? "",
    calc_period: contract?.calc_period ?? "unset",
    payment_method: contract?.payment_method ?? "daily_cash",
    payment_note: contract?.payment_note ?? "",
  });
  const [deductions, setDeductions] = useState(contract?.deductions ?? []);
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f, v: string | number) => setF((s) => ({ ...s, [k]: v }));
  return (
    <Modal
      wide
      title={contract ? `${contract.employee_name} の契約を編集` : "契約を登録"}
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button
            className="btn btn-primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              try {
                const body = { ...f, end_date: f.end_date || null, deductions };
                const saved = contract
                  ? await api<CastContract>(`/api/accounting/contracts/${contract.id}`, { method: "PATCH", body })
                  : await api<CastContract>("/api/accounting/contracts", { method: "POST", idempotencyKey: newIdempotencyKey(), body });
                if (file) await uploadFile(`/api/accounting/contracts/${saved.id}/document`, file);
                toast.show({ message: "契約を保存しました" + (saved.tax_review_status === "confirmed" ? "" : "（税務区分は経営者の確認待ち）") });
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
        <Field label="キャスト">
          <select className="input" value={f.employee} disabled={!!contract} onChange={(e) => set("employee", Number(e.target.value))}>
            {employees.map((e) => (
              <option key={e.id} value={e.id}>
                {e.display_name}（{e.full_name}）
              </option>
            ))}
          </select>
        </Field>
        <Field label="契約区分" hint="契約区分を変えると、税務区分の確認はやり直しになります。">
          <select className="input" value={f.contract_type} onChange={(e) => set("contract_type", e.target.value)}>
            {CONTRACT_TYPES.map((t) => (
              <option key={t.key} value={t.key}>
                {t.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="契約開始日">
          <input className="input" type="date" value={f.start_date} onChange={(e) => set("start_date", e.target.value)} />
        </Field>
        <Field label="契約終了日（任意）">
          <input className="input" type="date" value={f.end_date} onChange={(e) => set("end_date", e.target.value)} />
        </Field>
        <Field label="報酬計算期間" hint="ホステス報酬の控除額（5,000円×計算期間の暦日数）の計算に使います。月単位の契約で日々前払いする場合は「月ごと」。">
          <select className="input" value={f.calc_period} onChange={(e) => set("calc_period", e.target.value)}>
            {CALC_PERIODS.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="支払方法">
          <select className="input" value={f.payment_method} onChange={(e) => set("payment_method", e.target.value)}>
            <option value="daily_cash">日払い（レジ現金）</option>
            <option value="monthly_bank">月払い（振込）</option>
            <option value="mixed">日払い＋締め払い</option>
            <option value="other">その他</option>
          </select>
        </Field>
        <Field label="支払に関するメモ">
          <input className="input" value={f.payment_note} onChange={(e) => set("payment_note", e.target.value)} />
        </Field>
        <Field label="契約書（PDF・画像）">
          <input className="input" type="file" accept="application/pdf,image/*" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </Field>
      </div>
      <div className="mt-4 space-y-2">
        <div className="flex items-center gap-2">
          <h3 className="font-bold">その他控除（源泉所得税とは別）</h3>
          <button className="btn btn-sm ml-auto" onClick={() => setDeductions((d) => [...d, { code: "", label: "厚生費", calc: "percent", value: "0" }])}>
            ＋ 追加
          </button>
        </div>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          厚生費など、契約で合意した控除です。源泉所得税の対象額は減りません。インボイス制度を理由にした一律の「インボイス税」の控除は法令上の根拠がないため登録できません。
        </p>
        {deductions.map((d, i) => (
          <div key={i} className="grid grid-cols-4 gap-2 items-end">
            <Field label="名前">
              <input className="input" value={d.label} onChange={(e) => setDeductions((s) => s.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} />
            </Field>
            <Field label="計算">
              <select className="input" value={d.calc} onChange={(e) => setDeductions((s) => s.map((x, j) => (j === i ? { ...x, calc: e.target.value as typeof d.calc } : x)))}>
                <option value="percent">報酬総額の%</option>
                <option value="per_day">1日あたり（円）</option>
                <option value="per_payment">1回あたり（円）</option>
              </select>
            </Field>
            <Field label="値">
              <input className="input num" value={d.value} onChange={(e) => setDeductions((s) => s.map((x, j) => (j === i ? { ...x, value: e.target.value.replace(/[^0-9.]/g, "") } : x)))} />
            </Field>
            <button className="btn btn-sm btn-ghost" onClick={() => setDeductions((s) => s.filter((_, j) => j !== i))}>
              削除
            </button>
          </div>
        ))}
      </div>
      <ErrorBox error={err} />
    </Modal>
  );
}

function ReviewModal({ contract, checklist, onClose, onSaved }: { contract: CastContract; checklist: { key: string; label: string }[]; onClose: () => void; onSaved: () => void }) {
  const { withReauth } = useSession();
  const toast = useToast();
  const [category, setCategory] = useState(contract.tax_category === "undetermined" ? "" : contract.tax_category);
  const [status, setStatus] = useState<"confirmed" | "needs_review">("confirmed");
  const [checks, setChecks] = useState<Record<string, boolean>>(contract.review_checklist ?? {});
  const [note, setNote] = useState(contract.review_note);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const employmentLike = Object.values(checks).filter(Boolean).length;
  return (
    <Modal
      wide
      title={`${contract.employee_name} の税務区分を確認（${contract.contract_type_label}）`}
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || (status === "confirmed" && (!category || !note.trim()))}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              try {
                const r = await withReauth(() =>
                  api<CastContract & { warnings: string[] }>(`/api/accounting/contracts/${contract.id}/review`, {
                    method: "POST",
                    body: { tax_category: category || "undetermined", tax_review_status: status, review_checklist: checks, review_note: note },
                  }),
                );
                toast.show({ message: `${contract.employee_name}: ${r.tax_category_label}（${r.tax_review_status_label}）`, tone: r.warnings.length ? "error" : undefined });
                onSaved();
              } catch (e) {
                setErr(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            {status === "confirmed" ? "この区分で確定する" : "要確認として保存"}
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <p className="font-semibold mb-1">働き方の実態（当てはまるものにチェック）</p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {checklist.map((c) => (
              <label key={c.key} className="card-2 flex items-start gap-2 cursor-pointer">
                <input type="checkbox" className="mt-1 h-5 w-5" checked={!!checks[c.key]} onChange={(e) => setChecks((s) => ({ ...s, [c.key]: e.target.checked }))} />
                <span>{c.label}</span>
              </label>
            ))}
          </div>
          {employmentLike >= 3 && (
            <p className="mt-2 font-semibold" style={{ color: "var(--color-warn)" }}>
              雇用に近い実態のチェックが {employmentLike} 個あります。実態が雇用であれば給与所得として源泉徴収する必要があります。判断に迷う場合は税理士等に相談してください。
            </p>
          )}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <button className={`btn btn-lg ${category === "hostess_fee" ? "btn-primary" : ""}`} onClick={() => setCategory("hostess_fee")}>
            ホステス等の報酬・料金
          </button>
          <button className={`btn btn-lg ${category === "salary" ? "btn-primary" : ""}`} onClick={() => setCategory("salary")}>
            給与所得
          </button>
        </div>
        {category === "hostess_fee" && contract.calc_period === "unset" && (
          <p style={{ color: "var(--color-danger)" }}>先に契約の「報酬計算期間」を設定してください（控除額の計算に必要です）。</p>
        )}
        {category === "salary" && (
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            給与所得の源泉徴収は、扶養控除等申告書の有無（甲欄・乙欄）、日雇い（丙欄）、社会保険料などで変わるため、NOXIA では計算しません。給与計算ソフトで計算し、結果を「源泉所得税管理」の給与に入力してください。
          </p>
        )}
        <div className="grid grid-cols-2 gap-2">
          <button className={`btn ${status === "confirmed" ? "btn-primary" : ""}`} onClick={() => setStatus("confirmed")}>
            確認済みにする
          </button>
          <button className={`btn ${status === "needs_review" ? "btn-primary" : ""}`} onClick={() => setStatus("needs_review")}>
            要確認（専門家に相談中など）
          </button>
        </div>
        <Field label="判断の理由・確認した資料（確定には必須）">
          <textarea className="input" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        {contract.advisor_note && (
          <div className="card-2">
            <span className="font-semibold">税理士等のコメント: </span>
            {contract.advisor_note}
          </div>
        )}
        <ErrorBox error={err} />
      </div>
    </Modal>
  );
}

function AdviceModal({ contract, onClose, onSaved }: { contract: CastContract; onClose: () => void; onSaved: () => void }) {
  const [note, setNote] = useState(contract.advisor_note);
  const [err, setErr] = useState<unknown>(null);
  return (
    <Modal
      title={`${contract.employee_name} の契約へのコメント`}
      onClose={onClose}
      footer={
        <button
          className="btn btn-primary"
          onClick={async () => {
            try {
              await api(`/api/accounting/contracts/${contract.id}/advice`, { method: "POST", body: { advisor_note: note } });
              onSaved();
            } catch (e) {
              setErr(e);
            }
          }}
        >
          保存する
        </button>
      }
    >
      <p className="text-sm mb-2" style={{ color: "var(--color-muted)" }}>
        税務区分の確定は経営者が行います。確認した内容や助言をコメントとして残してください。
      </p>
      <textarea className="input" rows={4} value={note} onChange={(e) => setNote(e.target.value)} />
      <ErrorBox error={err} />
    </Modal>
  );
}

function PayItems() {
  const { can, withReauth } = useSession();
  const q = useLoad<PayItemRow[]>("/api/accounting/pay-items");
  const [rows, setRows] = useState<PayItemRow[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const data = rows ?? q.data;
  if (!data) return null;
  return (
    <div className="card space-y-2">
      <h2 className="text-lg font-bold">報酬項目ごとの税務上の扱い</h2>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        国税庁の取扱い（No.2807）では、報奨金・衣装代・深夜帰宅のタクシー代もホステス報酬の源泉徴収の対象です。既定はすべて対象です。変更は経営者のみ（税理士等に確認のうえ）。
      </p>
      <table className="table">
        <thead>
          <tr>
            <th>項目</th>
            <th>ホステス報酬の源泉徴収</th>
          </tr>
        </thead>
        <tbody>
          {data.map((r) => (
            <tr key={r.code}>
              <td>{r.label}</td>
              <td>
                {can("tax.decide") ? (
                  <button className={`btn btn-sm ${r.withholding_target ? "btn-primary" : ""}`} onClick={() => setRows(data.map((x) => (x.code === r.code ? { ...x, withholding_target: !x.withholding_target } : x)))}>
                    {r.withholding_target ? "対象" : "対象外"}
                  </button>
                ) : r.withholding_target ? (
                  "対象"
                ) : (
                  "対象外"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows && (
        <button
          className="btn btn-primary"
          onClick={async () => {
            try {
              const saved = await withReauth(() => api<PayItemRow[]>("/api/accounting/pay-items", { method: "PUT", body: { items: rows } }));
              q.setData(saved);
              setRows(null);
            } catch (e) {
              setErr(e);
            }
          }}
        >
          保存する
        </button>
      )}
      <ErrorBox error={err} />
    </div>
  );
}
