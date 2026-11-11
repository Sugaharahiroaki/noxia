"use client";

import { useState } from "react";
import { Empty, ErrorBox, Field, Modal, PageHeader, SaveStatus, Spinner, Toggle, type SaveState } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate, ROLE_LABELS, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Employee } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type Wages = { current: string; history: { id: number; hourly_wage: string; effective_from: string; note: string; created_by_name: string; created_at: string }[] };

export default function EmployeesPage() {
  const { can, withReauth, store } = useSession();
  const [showCompany, setShowCompany] = useState(false);
  const list = useLoad<Employee[]>(`/api/staff/employees${showCompany ? "?company=1" : ""}`);
  const [editing, setEditing] = useState<Employee | "new" | null>(null);
  const [loginFor, setLoginFor] = useState<Employee | null>(null);
  const [wageFor, setWageFor] = useState<Employee | null>(null);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const canEdit = can("employees.edit");
  const isOwner = store?.role === "owner" || store?.role === "admin" || store?.role === "operator";

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
              <th></th>
            </tr>
          </thead>
          <tbody>
            {list.data?.map((e) => (
              <tr key={e.id} style={{ opacity: e.is_active ? 1 : 0.55 }}>
                <td className="font-semibold">{e.display_name}</td>
                <td>{e.full_name}</td>
                <td>{e.this_store_role ? ROLE_LABELS[e.this_store_role] : <span style={{ color: "var(--color-muted)" }}>未配属</span>}</td>
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
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await list.reload();
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
          withReauth={withReauth}
        />
      )}
      {wageFor && <WageModal employee={wageFor} onClose={() => setWageFor(null)} canEdit={can("wages.edit")} withReauth={withReauth} setSave={setSave} />}
    </div>
  );
}

function EmployeeModal({ employee, onClose, onSaved, setSave }: { employee: Employee | null; onClose: () => void; onSaved: () => Promise<void>; setSave: (s: SaveState) => void }) {
  const { store } = useSession();
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

  async function submit() {
    setBusy(true);
    setErr(null);
    setSave({ kind: "saving" });
    try {
      const body = { display_name: form.display_name, full_name: form.full_name, hired_on: form.hired_on || null, left_on: form.left_on || null, is_active: form.is_active, note: form.note };
      if (employee) {
        await api(`/api/staff/employees/${employee.id}`, { method: "PATCH", body });
        await api(`/api/staff/employees/${employee.id}/assignment`, { method: "PUT", body: { role: form.role, is_active: form.assigned && form.is_active } });
      } else {
        await api("/api/staff/employees", { method: "POST", body: { ...body, role: form.role } });
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
          <button className="btn btn-primary" onClick={submit} disabled={busy || !form.display_name || !form.full_name}>
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

function LoginModal({ employee, onClose, onSaved, setSave, withReauth }: { employee: Employee; onClose: () => void; onSaved: () => Promise<void>; setSave: (s: SaveState) => void; withReauth: <T>(fn: () => Promise<T>) => Promise<T> }) {
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
                await withReauth(() => api(`/api/staff/employees/${employee.id}/login-account`, { method: "POST", body: { username, password, enabled } }));
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
        ログイン権限は配属店舗の役割（{employee.assignments.filter((a) => a.is_active).map((a) => `${a.store_name}: ${ROLE_LABELS[a.role]}`).join("、") || "配属なし"}）に従います。パスワードの再確認が必要です。
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

function WageModal({ employee, onClose, canEdit, withReauth, setSave }: { employee: Employee; onClose: () => void; canEdit: boolean; withReauth: <T>(fn: () => Promise<T>) => Promise<T>; setSave: (s: SaveState) => void }) {
  const [data, setData] = useState<Wages | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [wage, setWage] = useState("");
  const [from, setFrom] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  if (!loaded) {
    setLoaded(true);
    withReauth(() => api<Wages>(`/api/staff/employees/${employee.id}/wages`))
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
                    await withReauth(() => api(`/api/staff/employees/${employee.id}/wages`, { method: "POST", body: { hourly_wage: wage, effective_from: from, note } }));
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
