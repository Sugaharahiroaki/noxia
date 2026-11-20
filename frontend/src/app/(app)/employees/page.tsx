"use client";

import { useState } from "react";
import type { BackApiRow } from "@/components/DesignationSettings";
import { Empty, ErrorBox, Field, Modal, PageHeader, SaveStatus, Spinner, Toggle, type SaveState } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate, ROLE_LABELS, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import { useTx } from "@/lib/terms";
import type { Employee } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type Wages = { current: string; history: { id: number; hourly_wage: string; effective_from: string; note: string; created_by_name: string; created_at: string }[] };
/** 従業員 1 人の「会計の%」のバック（/api/staff/employees/:id/designation-backs）。store は店舗の設定 */
type EmpBackRow = {
  target: string;
  kind: string;
  calc_type: "fixed" | "percent" | null;
  value: string | null;
  rule_id: number | null;
  effective_from: string | null;
  store: { mode: "none" | "fixed" | "percent" | "per_cast"; value: string | null; subtotal_basis: string; split_mode: string; include_discount: boolean };
};
const BASIS_TEXT: Record<string, string> = { set_extension: "セット＋延長", subtotal: "会計（TAX前）", total: "会計（TAX込み）" };
const SPLIT_TEXT: Record<string, string> = { equal: "人数で割る", full_each: "全員に全額" };
const pct = (v: string | null | undefined) => (v === null || v === undefined ? "" : String(Number(v)));

export default function EmployeesPage() {
  const { can, store } = useSession();
  const tx = useTx();
  const [showCompany, setShowCompany] = useState(false);
  const list = useLoad<Employee[]>(`/api/staff/employees${showCompany ? "?company=1" : ""}`);
  const canSeeBacks = can("backs.rules.view");
  const backs = useLoad<{ business_date: string; backs: BackApiRow[] }>(canSeeBacks ? "/api/staff/designation-backs" : null);
  const [editing, setEditing] = useState<Employee | "new" | null>(null);
  const [loginFor, setLoginFor] = useState<Employee | null>(null);
  const [wageFor, setWageFor] = useState<Employee | null>(null);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const canEdit = can("employees.edit");
  const isOwner = store?.role === "owner" || store?.role === "admin" || store?.role === "operator";
  // 呼びのバック: 店舗の設定（1 回ごと・会計の%）と、従業員ごとに入れた % がある人。
  // 呼びを使う店舗で、店舗共通の会計の%バックがない（なし）か、誰かに % が入っているときだけ一覧に列を出す
  const yobiUnit = backs.data?.backs.find((b) => b.target === "yobi");
  const yobiBill = backs.data?.backs.find((b) => b.target === "yobi_subtotal");
  const yobiPersonal = new Map((yobiBill?.personal ?? []).map((p) => [p.employee_id, p]));
  const showYobiColumn = !!yobiBill && yobiBill.enabled !== false && (yobiBill.mode === "none" || yobiBill.mode === "per_cast" || yobiPersonal.size > 0);
  const yobiBack = tx("呼び") + "バック";

  return (
    <div className="space-y-4">
      <PageHeader
        title="従業員管理"
        subtitle="在籍情報とログインアカウントは別に管理します。退職者を無効化しても過去の売上・勤怠・支払履歴は残ります。"
        actions={
          <>
            <SaveStatus state={save} />
            {isOwner && (
              <button className={`btn btn-sm ${showCompany ? "btn-primary" : ""}`} onClick={() => setShowCompany((v) => !v)}>
                {showCompany ? "この店舗の配属のみ" : "会社の全従業員を表示"}
              </button>
            )}
            {canEdit && (
              <button className="btn btn-primary" onClick={() => setEditing("new")}>
                ＋ 従業員を追加
              </button>
            )}
          </>
        }
      />
      <ErrorBox error={list.error} onRetry={() => list.reload(false)} />
      {list.loading && <Spinner />}
      {list.data?.length === 0 && <Empty>従業員が登録されていません</Empty>}
      <div className="card scroll-x">
        <table className="table">
          <thead>
            <tr>
              <th>表示名</th>
              <th>氏名</th>
              <th>この店舗の役割</th>
              <th>配属店舗</th>
              <th>入店日 / 退職日</th>
              <th>在籍</th>
              <th>ログイン</th>
              {showYobiColumn && <th className="whitespace-nowrap">{yobiBack}</th>}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {list.data?.map((e) => (
              <tr key={e.id} style={{ opacity: e.is_active ? 1 : 0.55 }}>
                <td className="font-semibold whitespace-nowrap">{e.display_name}</td>
                <td className="whitespace-nowrap">{e.full_name}</td>
                <td className="whitespace-nowrap">{e.this_store_role ? ROLE_LABELS[e.this_store_role] : <span style={{ color: "var(--color-muted)" }}>未配属</span>}</td>
                <td className="text-sm">
                  {e.assignments
                    .filter((a) => a.is_active)
                    .map((a) => `${a.store_name}（${ROLE_LABELS[a.role]}）`)
                    .join("、") || "—"}
                </td>
                <td className="text-sm num">
                  {fmtDate(e.hired_on)} / {fmtDate(e.left_on)}
                </td>
                <td>{e.is_active ? <span className="badge badge-ok">在籍</span> : <span className="badge">退職</span>}</td>
                <td>{e.user_username ? <span className={`badge ${e.login_enabled ? "badge-accent" : ""}`}>{e.user_username}{e.login_enabled ? "" : "（無効）"}</span> : <span style={{ color: "var(--color-muted)" }}>なし</span>}</td>
                {showYobiColumn && (
                  <td className="num whitespace-nowrap" data-testid={`yobi-back-${e.id}`}>
                    {yobiPersonal.get(e.id) ? (
                      <span className="badge badge-accent">
                        {yobiPersonal.get(e.id)!.calc_type === "percent" ? `${pct(yobiPersonal.get(e.id)!.value)}%` : yen(yobiPersonal.get(e.id)!.value)}
                      </span>
                    ) : (
                      <span style={{ color: "var(--color-muted)" }}>{yobiBill?.mode === "percent" || yobiBill?.mode === "fixed" ? "店舗共通" : "なし"}</span>
                    )}
                  </td>
                )}
                <td className="whitespace-nowrap">
                  {canEdit && (
                    <button className="btn btn-sm" onClick={() => setEditing(e)}>
                      編集
                    </button>
                  )}{" "}
                  {canEdit && (
                    <button className="btn btn-sm" onClick={() => setLoginFor(e)}>
                      ログイン
                    </button>
                  )}{" "}
                  {can("wages.view") && (
                    <button className="btn btn-sm" onClick={() => setWageFor(e)}>
                      時給
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing && (
        <EmployeeModal
          employee={editing === "new" ? null : editing}
          yobi={canSeeBacks ? { unit: yobiUnit, bill: yobiBill, canEdit: can("backs.rules.edit") } : null}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await Promise.all([list.reload(), canSeeBacks ? backs.reload() : Promise.resolve()]);
          }}
          setSave={setSave}
        />
      )}
      {loginFor && (
        <LoginModal
          employee={loginFor}
          onClose={() => setLoginFor(null)}
          onSaved={async () => {
            setLoginFor(null);
            await list.reload();
          }}
          setSave={setSave}
        />
      )}
      {wageFor && <WageModal employee={wageFor} onClose={() => setWageFor(null)} canEdit={can("wages.edit")} setSave={setSave} />}
    </div>
  );
}

type YobiProps = { unit: BackApiRow | undefined; bill: BackApiRow | undefined; canEdit: boolean } | null;

/** 店舗の呼びのバックの設定を、従業員の画面向けの 1 文にする（% を入れたらどうなるか） */
function yobiStoreText(yobi: string, unit: BackApiRow | undefined, bill: BackApiRow | undefined): string {
  if (bill?.enabled === false) return `${yobi}は店舗設定で「使わない」なので、% を入れても付きません（入店・卓の画面に${yobi}が出ません）。`;
  const cond = bill ? `${BASIS_TEXT[bill.subtotal_basis] ?? bill.subtotal_basis}の%・複数人は${SPLIT_TEXT[bill.split_mode] ?? bill.split_mode}` : "";
  if (bill?.mode === "percent") return `店舗共通は会計の ${pct(bill.value)}%（${cond}）。入れると、この人だけこちらが優先されます。空欄は店舗共通の %。`;
  if (bill?.mode === "fixed") return `店舗共通は 1 会計 ${yen(bill.value)}。入れると、この人だけこちらが優先されます。`;
  const extra = unit?.mode === "fixed" ? `（店舗共通の ${yobi} 1 回 ${yen(unit.value)} に足して付きます）` : "";
  return `店舗共通のバックはなし。入れた人だけ、この % で付きます${extra}（${cond}）。空欄は付きません。`;
}

function EmployeeModal({
  employee,
  yobi,
  onClose,
  onSaved,
  setSave,
}: {
  employee: Employee | null;
  /** 呼びのバックを見られる人だけ（backs.rules.view）。店舗の設定と、変えられるか（backs.rules.edit） */
  yobi: YobiProps;
  onClose: () => void;
  onSaved: () => Promise<void>;
  setSave: (s: SaveState) => void;
}) {
  const { store } = useSession();
  const tx = useTx();
  const yobiName = tx("呼び");
  const existing = employee?.assignments.find((a) => a.store === store?.id);
  const [form, setForm] = useState({
    display_name: employee?.display_name ?? "",
    full_name: employee?.full_name ?? "",
    hired_on: employee?.hired_on ?? "",
    left_on: employee?.left_on ?? "",
    is_active: employee?.is_active ?? true,
    note: employee?.note ?? "",
    role: existing?.role ?? "cast",
    assigned: existing ? existing.is_active : true,
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  // 呼びのバック（この人の「呼びのある会計の%」）。今の値は従業員 1 人の API から読む（追加のときは空）
  const showYobi = !!yobi && (!!employee ? !!existing : true);
  const mine = useLoad<{ business_date: string; backs: EmpBackRow[] }>(showYobi && employee ? `/api/staff/employees/${employee.id}/designation-backs` : null);
  const mineRow = mine.data?.backs.find((b) => b.target === "yobi_subtotal");
  const yobiCurrent = mineRow?.calc_type === "percent" ? pct(mineRow.value) : "";
  const [yobiInput, setYobiInput] = useState<string | null>(null);
  const yobiValue = yobiInput ?? yobiCurrent;
  const yobiTooBig = yobiValue !== "" && Number(yobiValue) > 100;
  const yobiLoaded = !employee || !!mine.data;
  // 読めなかったときも、ほかの項目は保存できる（呼びのバックは変えない）
  const yobiReady = !showYobi || yobiLoaded || !!mine.error;
  const yobiChanged = showYobi && !!yobi?.canEdit && yobiLoaded && yobiValue.trim() !== yobiCurrent && !(yobiValue.trim() !== "" && yobiCurrent !== "" && Number(yobiValue) === Number(yobiCurrent));

  async function saveYobi(id: number) {
    await api(`/api/staff/employees/${id}/designation-backs`, {
      method: "PUT",
      body: { backs: [{ target: "yobi_subtotal", value: yobiValue.trim() === "" ? null : yobiValue.trim() }] },
    });
  }

  async function submit() {
    setBusy(true);
    setErr(null);
    setSave({ kind: "saving" });
    try {
      const body = { display_name: form.display_name, full_name: form.full_name, hired_on: form.hired_on || null, left_on: form.left_on || null, is_active: form.is_active, note: form.note };
      if (employee) {
        await api(`/api/staff/employees/${employee.id}`, { method: "PATCH", body });
        await api(`/api/staff/employees/${employee.id}/assignment`, { method: "PUT", body: { role: form.role, is_active: form.assigned && form.is_active } });
        if (yobiChanged) await saveYobi(employee.id);
      } else {
        const created = await api<Employee>("/api/staff/employees", { method: "POST", body: { ...body, role: form.role } });
        if (yobiChanged) {
          try {
            await saveYobi(created.id);
          } catch (e) {
            // 従業員は追加できている。もう一度「保存」で二重に追加しないよう、画面は閉じて知らせる
            setSave({ kind: "error", error: e });
            await onSaved();
            return;
          }
        }
      }
      setSave({ kind: "saved" });
      await onSaved();
    } catch (e) {
      setErr(e);
      setSave({ kind: "error", error: e });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={employee ? `${employee.display_name} を編集` : "従業員を追加"}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            キャンセル
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={busy || !form.display_name || !form.full_name || yobiTooBig || !yobiReady}>
            保存
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
        <Field label="表示名（源氏名など）">
          <input className="input" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
        </Field>
        <Field label="氏名">
          <input className="input" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
        </Field>
        <Field label="入店日">
          <input type="date" className="input" value={form.hired_on} onChange={(e) => setForm({ ...form, hired_on: e.target.value })} />
        </Field>
        <Field label="退職日">
          <input type="date" className="input" value={form.left_on} onChange={(e) => setForm({ ...form, left_on: e.target.value })} />
        </Field>
        <Field label={`この店舗（${store?.name}）での役割`}>
          <select className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="cast">キャスト</option>
            <option value="staff">スタッフ</option>
            <option value="manager">店舗管理者</option>
          </select>
        </Field>
        <div className="space-y-2">
          <Toggle checked={form.is_active} onChange={(v) => setForm({ ...form, is_active: v })} label="在籍中" />
          {employee && <Toggle checked={form.assigned} onChange={(v) => setForm({ ...form, assigned: v })} label="この店舗に配属" />}
        </div>
        {showYobi && yobi && (
          <div className="md:col-span-2 card-2 space-y-1" data-testid="yobi-back-field">
            <Field label={`${yobiName}バック（${yobiName}のある会計の何%か・この店舗）`} hint={yobiTooBig ? "100% 以下にしてください" : undefined}>
              <span className="flex items-center gap-2">
                <span>会計の</span>
                <input
                  className="input num"
                  style={{ maxWidth: 140 }}
                  inputMode="decimal"
                  value={yobiValue}
                  placeholder="なし"
                  disabled={!yobi.canEdit || busy || !yobiLoaded}
                  onChange={(e) => setYobiInput(e.target.value.replace(/[^\d.]/g, ""))}
                  aria-label={`${yobiName}バック（%）`}
                />
                <span>%</span>
                {!yobi.canEdit && <span className="badge badge-warn">見るだけ（変更はオーナー）</span>}
              </span>
            </Field>
            {mine.error ? (
              <ErrorBox error={mine.error} onRetry={() => mine.reload(false)} />
            ) : (
              <p className="text-sm" style={{ color: "var(--color-muted)" }}>
                {yobiStoreText(yobiName, yobi.unit, yobi.bill)} 保存した営業日から使われ、それより前の会計は変わりません。
              </p>
            )}
            {mineRow?.calc_type === "fixed" && (
              <p className="text-sm" style={{ color: "var(--color-warn)" }}>
                いまは 1 会計 {yen(mineRow.value)}（バック設定（詳しく）で登録）です。% を入れると置き換わります。
              </p>
            )}
          </div>
        )}
        <div className="md:col-span-2">
          <Field label="メモ">
            <textarea className="input" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
          </Field>
        </div>
      </div>
      {employee && !form.is_active && (
        <p className="text-sm mt-2" style={{ color: "var(--color-warn)" }}>
          在籍をOFFにすると、ログインは無効化され全店舗の配属が停止されます。過去の売上・勤怠・支払履歴は残ります。
        </p>
      )}
    </Modal>
  );
}

function LoginModal({ employee, onClose, onSaved, setSave }: { employee: Employee; onClose: () => void; onSaved: () => Promise<void>; setSave: (s: SaveState) => void }) {
  const [username, setUsername] = useState(employee.user_username ?? "");
  const [password, setPassword] = useState("");
  const [enabled, setEnabled] = useState(employee.user_username ? employee.login_enabled : true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  return (
    <Modal
      title={`${employee.display_name} のログインアカウント`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            キャンセル
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || (!employee.user_username && (!username || !password))}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              setSave({ kind: "saving" });
              try {
                await api(`/api/staff/employees/${employee.id}/login-account`, { method: "POST", body: { username, password, enabled } });
                setSave({ kind: "saved" });
                await onSaved();
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
      <ErrorBox error={err} />
      <p className="text-sm mb-3" style={{ color: "var(--color-muted)" }}>
        ログイン権限は配属店舗の役割（{employee.assignments.filter((a) => a.is_active).map((a) => `${a.store_name}: ${ROLE_LABELS[a.role]}`).join("、") || "配属なし"}）に従います。
      </p>
      <div className="space-y-3">
        <Field label="ユーザー名">
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} disabled={!!employee.user_username} autoCapitalize="none" />
        </Field>
        <Field label={employee.user_username ? "新しいパスワード（変更する場合のみ）" : "パスワード（8文字以上）"}>
          <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
        </Field>
        <Toggle checked={enabled} onChange={setEnabled} label="ログインを許可" />
      </div>
    </Modal>
  );
}

function WageModal({ employee, onClose, canEdit, setSave }: { employee: Employee; onClose: () => void; canEdit: boolean; setSave: (s: SaveState) => void }) {
  const [data, setData] = useState<Wages | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [wage, setWage] = useState("");
  const [from, setFrom] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  if (!loaded) {
    setLoaded(true);
    api<Wages>(`/api/staff/employees/${employee.id}/wages`)
      .then(setData)
      .catch(setErr);
  }

  return (
    <Modal title={`${employee.display_name} の時給（この店舗）`} onClose={onClose}>
      <ErrorBox error={err} />
      {!data && !err && <Spinner />}
      {data && (
        <div className="space-y-3">
          <div className="card-2">
            現在の時給: <span className="text-2xl font-bold num">{data.current ? yen(data.current) : "未設定"}</span>
          </div>
          <table className="table num">
            <thead>
              <tr>
                <th>適用開始日</th>
                <th>時給</th>
                <th>メモ</th>
                <th>登録者</th>
              </tr>
            </thead>
            <tbody>
              {data.history.map((h) => (
                <tr key={h.id}>
                  <td>{fmtDate(h.effective_from)}</td>
                  <td>{yen(h.hourly_wage)}</td>
                  <td>{h.note}</td>
                  <td>{h.created_by_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {canEdit && (
            <div className="card-2 space-y-2">
              <div className="font-semibold">時給を変更（履歴として追加。過去の確定済み集計は変わりません）</div>
              <div className="grid grid-cols-2 gap-2">
                <Field label="時給（円）">
                  <input className="input" inputMode="numeric" value={wage} onChange={(e) => setWage(e.target.value.replace(/[^\d]/g, ""))} />
                </Field>
                <Field label="適用開始日">
                  <input type="date" className="input" value={from} onChange={(e) => setFrom(e.target.value)} />
                </Field>
              </div>
              <Field label="メモ">
                <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
              </Field>
              <button
                className="btn btn-primary"
                disabled={busy || !wage || !from}
                onClick={async () => {
                  setBusy(true);
                  setSave({ kind: "saving" });
                  try {
                    await api(`/api/staff/employees/${employee.id}/wages`, { method: "POST", body: { hourly_wage: wage, effective_from: from, note } });
                    const d = await api<Wages>(`/api/staff/employees/${employee.id}/wages`);
                    setData(d);
                    setWage("");
                    setSave({ kind: "saved" });
                  } catch (e) {
                    setErr(e);
                    setSave({ kind: "error", error: e });
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                追加する
              </button>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
