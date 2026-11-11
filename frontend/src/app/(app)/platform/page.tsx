"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ConfirmDialog, Empty, ErrorBox, Field, Modal, PageHeader, SaveStatus, Spinner, Toggle, type SaveState } from "@/components/ui";
import { api, newIdempotencyKey } from "@/lib/api";
import { fmtDate, fmtDateTime, ROLE_LABELS } from "@/lib/format";
import { useSession } from "@/lib/session";

type Store = { id: number; company: number; company_name: string; name: string; code: string; is_active: boolean; is_demo: boolean; timezone: string; created_at: string };
type Company = { id: number; name: string; code: string; is_active: boolean; customer_sharing_enabled: boolean; is_demo: boolean; note: string; created_at: string; stores: Store[] };
type PlatformUser = {
  id: number;
  username: string;
  display_name: string;
  is_platform_operator: boolean;
  is_active: boolean;
  company_memberships: { company_id: number; company_name: string; role: string; is_active: boolean }[];
};
type AccessLog = { id: number; user: string; company: string; store: string | null; method: string; path: string; created_at: string };

const NONE = { storeId: "none" as const };

/** 店舗ヘッダなしで読み込む簡易フック（運営者 API 用） */
function useLoadNoStore<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    api<T>(path, NONE)
      .then((d) => {
        if (alive) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => alive && setError(e))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [path, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}

export default function PlatformPage() {
  const { me } = useSession();
  const [tab, setTab] = useState<"companies" | "users" | "logs">("companies");
  if (!me) return <Spinner />;
  if (!me.user.is_platform_operator)
    return (
      <div className="space-y-3">
        <PageHeader title="運営者: 会社・店舗管理" />
        <Empty>運営者権限がありません。</Empty>
      </div>
    );
  return (
    <div className="space-y-4">
      <PageHeader
        title="運営者: 会社・店舗管理"
        subtitle="運営者が顧客企業のデータへアクセスした操作は記録されます。会社オーナー権限とは別の権限です。"
        actions={
          <>
            <button className={`btn btn-sm ${tab === "companies" ? "btn-primary" : ""}`} onClick={() => setTab("companies")}>
              会社・店舗
            </button>
            <button className={`btn btn-sm ${tab === "users" ? "btn-primary" : ""}`} onClick={() => setTab("users")}>
              ユーザー
            </button>
            <button className={`btn btn-sm ${tab === "logs" ? "btn-primary" : ""}`} onClick={() => setTab("logs")}>
              アクセス記録
            </button>
            <Link href="/select-store" className="btn btn-sm btn-ghost">
              店舗選択へ
            </Link>
          </>
        }
      />
      {tab === "companies" && <CompaniesTab />}
      {tab === "users" && <UsersTab />}
      {tab === "logs" && <LogsTab />}
    </div>
  );
}

function CompaniesTab() {
  const { refresh } = useSession();
  const q = useLoadNoStore<Company[]>("/api/platform/companies");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [note, setNote] = useState("");
  const [addingStore, setAddingStore] = useState<Company | null>(null);
  const [storeName, setStoreName] = useState("");
  const [storeCode, setStoreCode] = useState("");
  const [toggling, setToggling] = useState<Company | null>(null);
  const [editing, setEditing] = useState<Company | null>(null);
  const [editName, setEditName] = useState("");
  const [editNote, setEditNote] = useState("");
  const [editSharing, setEditSharing] = useState(false);

  async function run(label: string, fn: () => Promise<unknown>) {
    setSave({ kind: "saving", label });
    try {
      await fn();
      setSave({ kind: "saved" });
      q.reload();
      await refresh();
      return true;
    } catch (e) {
      setSave({ kind: "error", error: e });
      return false;
    }
  }

  return (
    <div className="space-y-3">
      <div className="card flex flex-wrap items-center gap-2">
        <p className="text-sm flex-1" style={{ color: "var(--color-muted)" }}>
          契約会社と店舗を追加・停止します。停止しても過去データは削除されません。
        </p>
        <SaveStatus state={save} />
        <button className="btn btn-primary" onClick={() => setAdding(true)}>
          ＋ 会社を追加
        </button>
      </div>
      <ErrorBox error={q.error} onRetry={q.reload} />
      {q.loading && <Spinner />}
      {q.data?.length === 0 && <Empty>会社がありません</Empty>}
      {q.data?.map((c) => (
        <div key={c.id} className="card space-y-2" style={{ opacity: c.is_active ? 1 : 0.7 }}>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex-1">
              <span className="font-bold text-lg">{c.name}</span>
              <span className="text-sm ml-2" style={{ color: "var(--color-muted)" }}>
                {c.code} ・ 作成 {fmtDate(c.created_at.slice(0, 10))}
              </span>
              {c.is_demo && <span className="badge ml-2">開発用</span>}
              {c.customer_sharing_enabled && <span className="badge badge-accent ml-2">顧客共有ON</span>}
            </div>
            <span className={`badge ${c.is_active ? "badge-ok" : "badge-danger"}`}>{c.is_active ? "有効" : "停止中"}</span>
            <Link href={`/company/${c.id}`} className="btn btn-sm">
              会社管理（全店集計・メンバー）
            </Link>
            <button
              className="btn btn-sm"
              onClick={() => {
                setEditing(c);
                setEditName(c.name);
                setEditNote(c.note);
                setEditSharing(c.customer_sharing_enabled);
              }}
            >
              編集
            </button>
            <button className="btn btn-sm" onClick={() => setToggling(c)}>
              {c.is_active ? "停止する" : "有効にする"}
            </button>
            <button
              className="btn btn-sm btn-primary"
              onClick={() => {
                setAddingStore(c);
                setStoreName("");
                setStoreCode("");
              }}
            >
              ＋ 店舗
            </button>
          </div>
          {c.note && (
            <p className="text-sm" style={{ color: "var(--color-muted)" }}>
              {c.note}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            {c.stores.map((s) => (
              <span key={s.id} className={`badge ${s.is_active ? "badge-ok" : "badge-danger"}`}>
                {s.name}（{s.code}）{!s.is_active && " 停止中"}
              </span>
            ))}
            {c.stores.length === 0 && (
              <span className="text-sm" style={{ color: "var(--color-muted)" }}>
                店舗なし
              </span>
            )}
          </div>
        </div>
      ))}

      {adding && (
        <Modal
          title="会社を追加"
          onClose={() => setAdding(false)}
          footer={
            <>
              <button className="btn" onClick={() => setAdding(false)}>
                キャンセル
              </button>
              <button
                className="btn btn-primary"
                disabled={!name.trim() || !code.trim() || save.kind === "saving"}
                onClick={async () => {
                  const ok = await run("会社を追加中…", () => api("/api/platform/companies", { method: "POST", idempotencyKey: newIdempotencyKey(), body: { name: name.trim(), code: code.trim(), note }, ...NONE }));
                  if (ok) {
                    setAdding(false);
                    setName("");
                    setCode("");
                    setNote("");
                  }
                }}
              >
                追加する
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <Field label="会社名">
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="会社コード（半角英数字とハイフン・全体で一意）">
              <input className="input" value={code} onChange={(e) => setCode(e.target.value)} />
            </Field>
            <Field label="メモ（任意）">
              <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
            </Field>
          </div>
        </Modal>
      )}
      {addingStore && (
        <Modal
          title={`${addingStore.name} に店舗を追加`}
          onClose={() => setAddingStore(null)}
          footer={
            <>
              <button className="btn" onClick={() => setAddingStore(null)}>
                キャンセル
              </button>
              <button
                className="btn btn-primary"
                disabled={!storeName.trim() || !storeCode.trim() || save.kind === "saving"}
                onClick={async () => {
                  const ok = await run("店舗を追加中…", () => api(`/api/platform/companies/${addingStore.id}/stores`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { name: storeName.trim(), code: storeCode.trim() }, ...NONE }));
                  if (ok) setAddingStore(null);
                }}
              >
                追加する
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <Field label="店舗名">
              <input className="input" value={storeName} onChange={(e) => setStoreName(e.target.value)} />
            </Field>
            <Field label="店舗コード（会社内で一意）">
              <input className="input" value={storeCode} onChange={(e) => setStoreCode(e.target.value)} />
            </Field>
          </div>
        </Modal>
      )}
      {editing && (
        <Modal
          title="会社情報を編集"
          onClose={() => setEditing(null)}
          footer={
            <>
              <button className="btn" onClick={() => setEditing(null)}>
                キャンセル
              </button>
              <button
                className="btn btn-primary"
                disabled={!editName.trim() || save.kind === "saving"}
                onClick={async () => {
                  const ok = await run("保存中…", () => api(`/api/platform/companies/${editing.id}`, { method: "PATCH", body: { name: editName.trim(), note: editNote, customer_sharing_enabled: editSharing }, ...NONE }));
                  if (ok) setEditing(null);
                }}
              >
                保存する
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <Field label="会社名">
              <input className="input" value={editName} onChange={(e) => setEditName(e.target.value)} />
            </Field>
            <Toggle checked={editSharing} onChange={setEditSharing} label="会社内の顧客共有を許可" />
            <Field label="メモ">
              <input className="input" value={editNote} onChange={(e) => setEditNote(e.target.value)} />
            </Field>
          </div>
        </Modal>
      )}
      {toggling && (
        <ConfirmDialog
          title={toggling.is_active ? "会社を停止する" : "会社を有効にする"}
          message={
            toggling.is_active
              ? `「${toggling.name}」を停止します。所属ユーザーは店舗を操作できなくなります（オーナーは閲覧のみ）。データは削除されません。`
              : `「${toggling.name}」を再び有効にします。`
          }
          confirmLabel={toggling.is_active ? "停止する" : "有効にする"}
          danger={toggling.is_active}
          onCancel={() => setToggling(null)}
          onConfirm={async () => {
            const ok = await run("更新中…", () => api(`/api/platform/companies/${toggling.id}`, { method: "PATCH", body: { is_active: !toggling.is_active }, ...NONE }));
            if (ok) setToggling(null);
          }}
        />
      )}
    </div>
  );
}

function UsersTab() {
  const users = useLoadNoStore<PlatformUser[]>("/api/platform/users");
  const companies = useLoadNoStore<Company[]>("/api/platform/companies");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [companyId, setCompanyId] = useState<number | null>(null);
  const [role, setRole] = useState("owner");

  async function create() {
    setSave({ kind: "saving" });
    try {
      await api("/api/platform/users", { method: "POST", idempotencyKey: newIdempotencyKey(), body: { username: username.trim(), password, display_name: displayName.trim(), company_id: companyId, company_role: role }, ...NONE });
      setSave({ kind: "saved", label: "ユーザーを作成しました" });
      setOpen(false);
      setUsername("");
      setPassword("");
      setDisplayName("");
      users.reload();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  return (
    <div className="space-y-3">
      <div className="card flex flex-wrap items-center gap-2">
        <p className="text-sm flex-1" style={{ color: "var(--color-muted)" }}>
          会社オーナーなどの初期ユーザーを作成します。以降の招待・配属は会社オーナーが会社管理画面から行います。パスワードは本人に安全な方法で伝え、初回ログイン後に変更してもらってください。
        </p>
        <SaveStatus state={save} />
        <button className="btn btn-primary" onClick={() => setOpen(true)}>
          ＋ ユーザーを作成
        </button>
      </div>
      <ErrorBox error={users.error} onRetry={users.reload} />
      {users.loading && <Spinner />}
      <div className="scroll-x">
        <table className="table">
          <thead>
            <tr>
              <th>ユーザー名</th>
              <th>表示名</th>
              <th>状態</th>
              <th>会社所属</th>
            </tr>
          </thead>
          <tbody>
            {users.data?.map((u) => (
              <tr key={u.id}>
                <td>
                  {u.username}
                  {u.is_platform_operator && <span className="badge badge-accent ml-2">運営者</span>}
                </td>
                <td>{u.display_name}</td>
                <td>{u.is_active ? <span className="badge badge-ok">有効</span> : <span className="badge badge-danger">無効</span>}</td>
                <td className="text-sm">
                  {u.company_memberships.map((m, i) => (
                    <span key={i} className={`badge mr-1 ${m.is_active ? "" : "badge-danger"}`}>
                      {m.company_name}: {ROLE_LABELS[m.role] ?? m.role}
                    </span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {open && (
        <Modal
          title="ユーザーを作成"
          onClose={() => setOpen(false)}
          footer={
            <>
              <button className="btn" onClick={() => setOpen(false)}>
                キャンセル
              </button>
              <button className="btn btn-primary" onClick={create} disabled={!username.trim() || password.length < 8 || save.kind === "saving"}>
                作成する
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <Field label="ユーザー名">
              <input className="input" autoCapitalize="none" value={username} onChange={(e) => setUsername(e.target.value)} />
            </Field>
            <Field label="初期パスワード（8文字以上）">
              <input className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
            <Field label="表示名">
              <input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
            </Field>
            <Field label="所属会社（任意）">
              <select className="input" value={companyId ?? ""} onChange={(e) => setCompanyId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">所属なし</option>
                {companies.data?.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </Field>
            {companyId && (
              <Field label="会社内の権限">
                <select className="input" value={role} onChange={(e) => setRole(e.target.value)}>
                  <option value="owner">会社オーナー</option>
                  <option value="admin">会社管理者</option>
                  <option value="member">一般（店舗権限に従う）</option>
                </select>
              </Field>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}

function LogsTab() {
  const q = useLoadNoStore<AccessLog[]>("/api/platform/access-logs");
  return (
    <div className="space-y-3">
      <div className="card text-sm" style={{ color: "var(--color-muted)" }}>
        運営者が会社・店舗のデータへアクセスした記録（直近500件）。
      </div>
      <ErrorBox error={q.error} onRetry={q.reload} />
      {q.loading && <Spinner />}
      {q.data?.length === 0 && <Empty>記録はありません</Empty>}
      <div className="scroll-x">
        <table className="table">
          <thead>
            <tr>
              <th>日時</th>
              <th>運営者</th>
              <th>会社</th>
              <th>店舗</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {q.data?.map((l) => (
              <tr key={l.id}>
                <td className="whitespace-nowrap">{fmtDateTime(l.created_at)}</td>
                <td>{l.user}</td>
                <td>{l.company}</td>
                <td>{l.store ?? "—"}</td>
                <td className="text-xs num">
                  {l.method} {l.path}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
