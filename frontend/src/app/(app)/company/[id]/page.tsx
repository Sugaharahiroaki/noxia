"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ConfirmDialog, Empty, ErrorBox, Field, Modal, PageHeader, SaveStatus, Spinner, Stat, Toggle, type SaveState } from "@/components/ui";
import { api, newIdempotencyKey } from "@/lib/api";
import { fmtDate, fmtDateTime, monthStart, ROLE_LABELS, todayJst, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import { useLoad } from "@/lib/useLoad";

type Company = { id: number; name: string; code: string; is_active: boolean; customer_sharing_enabled: boolean; is_demo: boolean; note: string; created_at: string };
type Store = { id: number; company: number; company_name: string; name: string; code: string; is_active: boolean; is_demo: boolean; timezone: string; created_at: string };
type UserBrief = { id: number; username: string; display_name: string; is_platform_operator: boolean; is_active: boolean };
type CompanyMembership = { id: number; user: UserBrief; company: number; role: "owner" | "admin" | "member"; is_active: boolean; created_at: string };
type StoreMembership = { id: number; user: UserBrief; store: number; store_name: string; role: "manager" | "staff" | "cast"; is_active: boolean; created_at: string };
type Members = { company_memberships: CompanyMembership[]; store_memberships: StoreMembership[] };
type Invitation = { id: number; company: number; email: string; display_name: string; company_role: string; store_roles: { store_id: number; role: string }[]; employee: number | null; token: string; expires_at: string; accepted_at: string | null; created_at: string };
type StoreSummary = { store_id: number; store_name: string; is_active: boolean; total: string; checkout_count: number; visit_count: number; guest_count: number; avg_per_guest: string | null; cash: string; card: string; receivable: string; refunds: string };
type Summary = { company: { id: number; name: string }; from: string; to: string; stores: StoreSummary[]; total: Omit<StoreSummary, "store_id" | "store_name" | "is_active">; store_count: number };

const NONE = { storeId: "none" as const };

export default function CompanyPage() {
  const { id } = useParams<{ id: string }>();
  const { me } = useSession();
  const companyId = Number(id);
  const role = me?.companies.find((c) => c.id === companyId)?.role ?? (me?.user.is_platform_operator ? "operator" : null);
  const canManage = role === "owner" || role === "operator";
  const canView = canManage || role === "admin";
  const [tab, setTab] = useState<"summary" | "stores" | "members" | "invitations" | "info">("summary");

  if (!me) return <Spinner />;
  if (!canView)
    return (
      <div className="space-y-3">
        <PageHeader title="会社管理" />
        <Empty>この会社を管理する権限がありません。</Empty>
      </div>
    );

  const tabs: { key: typeof tab; label: string }[] = [
    { key: "summary", label: "全店集計" },
    { key: "stores", label: "店舗" },
    { key: "members", label: "メンバー・配属" },
    { key: "invitations", label: "招待" },
    { key: "info", label: "会社情報" },
  ];

  return (
    <div className="space-y-4">
      <PageHeader
        title="会社管理"
        subtitle={
          <>
            {me.companies.find((c) => c.id === companyId)?.name ?? `会社 #${companyId}`} ・ あなたの権限: {ROLE_LABELS[role ?? ""] ?? role}
            {!canManage && "（閲覧のみ）"}
          </>
        }
        actions={
          <>
            {tabs.map((t) => (
              <button key={t.key} className={`btn btn-sm ${tab === t.key ? "btn-primary" : ""}`} onClick={() => setTab(t.key)}>
                {t.label}
              </button>
            ))}
            <Link href="/select-store" className="btn btn-sm btn-ghost">
              店舗選択へ
            </Link>
          </>
        }
      />
      {tab === "summary" && <SummaryTab companyId={companyId} />}
      {tab === "stores" && <StoresTab companyId={companyId} canManage={canManage} />}
      {tab === "members" && <MembersTab companyId={companyId} canManage={canManage} />}
      {tab === "invitations" && <InvitationsTab companyId={companyId} canManage={canManage} />}
      {tab === "info" && <InfoTab companyId={companyId} canManage={canManage} />}
    </div>
  );
}

function SummaryTab({ companyId }: { companyId: number }) {
  const today = todayJst();
  const [from, setFrom] = useState(monthStart(today));
  const [to, setTo] = useState(today);
  const [path, setPath] = useState<string>(`/api/companies/${companyId}/summary?from=${from}&to=${to}`);
  const q = useLoadNoStore<Summary>(path);
  return (
    <div className="space-y-3">
      <div className="card flex flex-wrap items-center gap-2">
        <input type="date" className="input w-auto" value={from} onChange={(e) => setFrom(e.target.value)} />
        <span>〜</span>
        <input type="date" className="input w-auto" value={to} onChange={(e) => setTo(e.target.value)} />
        <button className="btn btn-sm btn-primary" onClick={() => setPath(`/api/companies/${companyId}/summary?from=${from}&to=${to}`)}>
          集計する
        </button>
        <span className="text-sm" style={{ color: "var(--color-muted)" }}>
          閲覧を許可された店舗だけが対象です。
        </span>
      </div>
      <ErrorBox error={q.error} onRetry={q.reload} />
      {q.loading && <Spinner />}
      {q.data && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <Stat label={`全店売上（${q.data.store_count}店舗）`} value={yen(q.data.total.total)} accent="ok" />
            <Stat label="会計件数 / 組数" value={`${q.data.total.checkout_count} / ${q.data.total.visit_count}`} />
            <Stat label="客数" value={`${q.data.total.guest_count} 名`} />
            <Stat label="客単価" value={q.data.total.avg_per_guest ? yen(q.data.total.avg_per_guest) : "—"} />
          </div>
          <div className="card scroll-x">
            <table className="table num">
              <thead>
                <tr>
                  <th>店舗</th>
                  <th>売上</th>
                  <th>会計</th>
                  <th>組数</th>
                  <th>客数</th>
                  <th>客単価</th>
                  <th>現金</th>
                  <th>カード</th>
                  <th>未収</th>
                  <th>返金</th>
                </tr>
              </thead>
              <tbody>
                {q.data.stores.map((s) => (
                  <tr key={s.store_id} style={{ opacity: s.is_active ? 1 : 0.6 }}>
                    <td>
                      {s.store_name}
                      {!s.is_active && <span className="badge ml-1">停止中</span>}
                    </td>
                    <td>{yen(s.total)}</td>
                    <td>{s.checkout_count}</td>
                    <td>{s.visit_count}</td>
                    <td>{s.guest_count}</td>
                    <td>{s.avg_per_guest ? yen(s.avg_per_guest) : "—"}</td>
                    <td>{yen(s.cash)}</td>
                    <td>{yen(s.card)}</td>
                    <td>{yen(s.receivable)}</td>
                    <td>{yen(s.refunds)}</td>
                  </tr>
                ))}
                <tr className="font-bold">
                  <td>全店合計</td>
                  <td>{yen(q.data.total.total)}</td>
                  <td>{q.data.total.checkout_count}</td>
                  <td>{q.data.total.visit_count}</td>
                  <td>{q.data.total.guest_count}</td>
                  <td>{q.data.total.avg_per_guest ? yen(q.data.total.avg_per_guest) : "—"}</td>
                  <td>{yen(q.data.total.cash)}</td>
                  <td>{yen(q.data.total.card)}</td>
                  <td>{yen(q.data.total.receivable)}</td>
                  <td>{yen(q.data.total.refunds)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

/** 店舗ヘッダなしで読み込む簡易フック（会社レベル API 用） */
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
  return { data, error, loading, reload: () => setTick((t) => t + 1), setData };
}

function StoresTab({ companyId, canManage }: { companyId: number; canManage: boolean }) {
  const { withReauth, refresh } = useSession();
  const q = useLoadNoStore<Store[]>(`/api/companies/${companyId}/stores`);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [toggling, setToggling] = useState<Store | null>(null);
  const [renaming, setRenaming] = useState<Store | null>(null);
  const [newName, setNewName] = useState("");

  async function addStore() {
    setSave({ kind: "saving" });
    try {
      await withReauth(() => api(`/api/companies/${companyId}/stores`, { method: "POST", body: { name: name.trim(), code: code.trim() }, idempotencyKey: newIdempotencyKey(), ...NONE }));
      setSave({ kind: "saved", label: "店舗を追加しました" });
      setAdding(false);
      setName("");
      setCode("");
      q.reload();
      await refresh();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  async function patchStore(s: Store, body: Record<string, unknown>) {
    setSave({ kind: "saving" });
    try {
      await withReauth(() => api(`/api/companies/${companyId}/stores/${s.id}`, { method: "PATCH", body, ...NONE }));
      setSave({ kind: "saved" });
      q.reload();
      await refresh();
    } catch (e) {
      setSave({ kind: "error", error: e });
      throw e;
    }
  }

  return (
    <div className="space-y-3">
      <div className="card flex flex-wrap items-center gap-2">
        <p className="text-sm flex-1" style={{ color: "var(--color-muted)" }}>
          店舗を停止しても過去の売上・勤怠・会計データは削除されません（閲覧はオーナーのみ可能になります）。
        </p>
        <SaveStatus state={save} />
        {canManage && (
          <button className="btn btn-primary" onClick={() => setAdding(true)}>
            ＋ 店舗を追加
          </button>
        )}
      </div>
      <ErrorBox error={q.error} onRetry={q.reload} />
      {q.loading && <Spinner />}
      {q.data?.length === 0 && <Empty>店舗がありません</Empty>}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {q.data?.map((s) => (
          <div key={s.id} className="card-2 flex flex-wrap items-center gap-2">
            <div className="flex-1">
              <div className="font-semibold text-lg">
                {s.name}
                {s.is_demo && <span className="badge ml-2">開発用</span>}
              </div>
              <div className="text-sm" style={{ color: "var(--color-muted)" }}>
                コード {s.code} ・ 作成 {fmtDate(s.created_at.slice(0, 10))}
              </div>
            </div>
            <span className={`badge ${s.is_active ? "badge-ok" : "badge-danger"}`}>{s.is_active ? "有効" : "停止中"}</span>
            {canManage && (
              <>
                <button
                  className="btn btn-sm"
                  onClick={() => {
                    setRenaming(s);
                    setNewName(s.name);
                  }}
                >
                  名称変更
                </button>
                <button className="btn btn-sm" onClick={() => setToggling(s)}>
                  {s.is_active ? "停止する" : "有効にする"}
                </button>
              </>
            )}
          </div>
        ))}
      </div>

      {adding && (
        <Modal
          title="店舗を追加"
          onClose={() => setAdding(false)}
          footer={
            <>
              <button className="btn" onClick={() => setAdding(false)}>
                キャンセル
              </button>
              <button className="btn btn-primary" onClick={addStore} disabled={!name.trim() || !code.trim() || save.kind === "saving"}>
                追加する（パスワード再確認）
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <Field label="店舗名">
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="店舗コード（半角英数字とハイフン・会社内で一意）">
              <input className="input" value={code} onChange={(e) => setCode(e.target.value)} placeholder="例: honten" />
            </Field>
            <p className="text-xs" style={{ color: "var(--color-muted)" }}>
              追加後は店舗設定（料金・税・サービス料・端数処理・営業日切替時刻）を確定してください。
            </p>
          </div>
        </Modal>
      )}
      {renaming && (
        <Modal
          title="店舗名を変更"
          onClose={() => setRenaming(null)}
          footer={
            <>
              <button className="btn" onClick={() => setRenaming(null)}>
                キャンセル
              </button>
              <button
                className="btn btn-primary"
                disabled={!newName.trim()}
                onClick={async () => {
                  try {
                    await patchStore(renaming, { name: newName.trim() });
                    setRenaming(null);
                  } catch {
                    /* 表示済み */
                  }
                }}
              >
                保存する
              </button>
            </>
          }
        >
          <Field label="店舗名">
            <input className="input" value={newName} onChange={(e) => setNewName(e.target.value)} />
          </Field>
        </Modal>
      )}
      {toggling && (
        <ConfirmDialog
          title={toggling.is_active ? "店舗を停止する" : "店舗を有効にする"}
          message={
            toggling.is_active
              ? `「${toggling.name}」を停止します。スタッフはログイン後にこの店舗を操作できなくなります。過去のデータは削除されず、オーナーは閲覧できます。`
              : `「${toggling.name}」を再び有効にします。`
          }
          confirmLabel={toggling.is_active ? "停止する" : "有効にする"}
          danger={toggling.is_active}
          onCancel={() => setToggling(null)}
          onConfirm={async () => {
            try {
              await patchStore(toggling, { is_active: !toggling.is_active });
              setToggling(null);
            } catch {
              /* 表示済み */
            }
          }}
        />
      )}
    </div>
  );
}

const STORE_ROLES: { value: "manager" | "staff" | "cast"; label: string }[] = [
  { value: "manager", label: "店舗管理者" },
  { value: "staff", label: "スタッフ" },
  { value: "cast", label: "キャスト" },
];
const COMPANY_ROLES: { value: "owner" | "admin" | "member"; label: string }[] = [
  { value: "owner", label: "会社オーナー" },
  { value: "admin", label: "会社管理者" },
  { value: "member", label: "一般（店舗権限に従う）" },
];

function MembersTab({ companyId, canManage }: { companyId: number; canManage: boolean }) {
  const { withReauth } = useSession();
  const members = useLoadNoStore<Members>(`/api/companies/${companyId}/members`);
  const stores = useLoadNoStore<Store[]>(`/api/companies/${companyId}/stores`);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [assign, setAssign] = useState<{ user: UserBrief; store_id: number | null; role: "manager" | "staff" | "cast"; is_active: boolean } | null>(null);

  async function post(body: Record<string, unknown>) {
    setSave({ kind: "saving" });
    try {
      await withReauth(() => api(`/api/companies/${companyId}/members`, { method: "POST", body, ...NONE }));
      setSave({ kind: "saved" });
      members.reload();
    } catch (e) {
      setSave({ kind: "error", error: e });
      throw e;
    }
  }

  const byUser = useMemo(() => {
    const m = new Map<number, StoreMembership[]>();
    for (const sm of members.data?.store_memberships ?? []) {
      const list = m.get(sm.user.id) ?? [];
      list.push(sm);
      m.set(sm.user.id, list);
    }
    return m;
  }, [members.data]);

  return (
    <div className="space-y-3">
      <div className="card flex flex-wrap items-center gap-2">
        <p className="text-sm flex-1" style={{ color: "var(--color-muted)" }}>
          ユーザーの会社内権限と、利用できる店舗・店舗内権限を管理します。会社オーナーは全店舗を操作でき、それ以外は配属された店舗だけ操作できます。
        </p>
        <SaveStatus state={save} />
      </div>
      <ErrorBox error={members.error || stores.error} onRetry={members.reload} />
      {members.loading && <Spinner />}
      {members.data && members.data.company_memberships.length === 0 && <Empty>メンバーがいません。招待から追加してください。</Empty>}
      <div className="space-y-2">
        {members.data?.company_memberships.map((cm) => (
          <div key={cm.id} className="card-2 space-y-2" style={{ opacity: cm.is_active && cm.user.is_active ? 1 : 0.6 }}>
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex-1">
                <span className="font-semibold text-lg">{cm.user.display_name || cm.user.username}</span>
                <span className="text-sm ml-2" style={{ color: "var(--color-muted)" }}>
                  @{cm.user.username}
                </span>
                {!cm.user.is_active && <span className="badge badge-danger ml-2">アカウント無効</span>}
              </div>
              {canManage ? (
                <select className="input w-auto" value={cm.role} onChange={(e) => post({ user_id: cm.user.id, company_role: e.target.value }).catch(() => {})}>
                  {COMPANY_ROLES.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.label}
                    </option>
                  ))}
                </select>
              ) : (
                <span className="badge badge-accent">{ROLE_LABELS[cm.role] ?? cm.role}</span>
              )}
              {canManage && (
                <button className="btn btn-sm" onClick={() => post({ user_id: cm.user.id, company_role: cm.role, is_active: !cm.is_active }).catch(() => {})}>
                  {cm.is_active ? "会社所属を停止" : "会社所属を再開"}
                </button>
              )}
              {canManage && (
                <button className="btn btn-sm btn-primary" onClick={() => setAssign({ user: cm.user, store_id: stores.data?.[0]?.id ?? null, role: "staff", is_active: true })}>
                  店舗配属を追加・変更
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              {(byUser.get(cm.user.id) ?? []).map((sm) => (
                <span key={sm.id} className={`badge ${sm.is_active ? "badge-ok" : ""}`}>
                  {sm.store_name}: {ROLE_LABELS[sm.role] ?? sm.role}
                  {!sm.is_active && "（停止）"}
                </span>
              ))}
              {(byUser.get(cm.user.id) ?? []).length === 0 && (
                <span className="text-sm" style={{ color: "var(--color-muted)" }}>
                  {cm.role === "owner" || cm.role === "admin" ? "会社権限で全店舗を利用できます" : "店舗配属なし"}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      {assign && (
        <Modal
          title={`${assign.user.display_name || assign.user.username} の店舗配属`}
          onClose={() => setAssign(null)}
          footer={
            <>
              <button className="btn" onClick={() => setAssign(null)}>
                キャンセル
              </button>
              <button
                className="btn btn-primary"
                disabled={!assign.store_id || save.kind === "saving"}
                onClick={async () => {
                  try {
                    await post({ user_id: assign.user.id, store_id: assign.store_id, role: assign.role, is_active: assign.is_active });
                    setAssign(null);
                  } catch {
                    /* 表示済み */
                  }
                }}
              >
                保存する（パスワード再確認）
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <Field label="店舗">
              <select className="input" value={assign.store_id ?? ""} onChange={(e) => setAssign({ ...assign, store_id: e.target.value ? Number(e.target.value) : null })}>
                {stores.data?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                    {!s.is_active ? "（停止中）" : ""}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="店舗内の権限">
              <div className="grid grid-cols-3 gap-2">
                {STORE_ROLES.map((r) => (
                  <button key={r.value} type="button" className={`btn btn-sm ${assign.role === r.value ? "btn-primary" : ""}`} onClick={() => setAssign({ ...assign, role: r.value })}>
                    {r.label}
                  </button>
                ))}
              </div>
            </Field>
            <Toggle checked={assign.is_active} onChange={(v) => setAssign({ ...assign, is_active: v })} label="この店舗で有効" />
          </div>
        </Modal>
      )}
    </div>
  );
}

function InvitationsTab({ companyId, canManage }: { companyId: number; canManage: boolean }) {
  const { withReauth } = useSession();
  const invitations = useLoadNoStore<Invitation[]>(`/api/companies/${companyId}/invitations`);
  const stores = useLoadNoStore<Store[]>(`/api/companies/${companyId}/stores`);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [open, setOpen] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [companyRole, setCompanyRole] = useState<"owner" | "admin" | "member">("member");
  const [storeRoles, setStoreRoles] = useState<Record<number, string>>({});
  const [copied, setCopied] = useState<number | null>(null);

  const origin = typeof window !== "undefined" ? window.location.origin : "";

  async function create() {
    setSave({ kind: "saving" });
    try {
      const store_roles = Object.entries(storeRoles)
        .filter(([, role]) => role)
        .map(([sid, role]) => ({ store_id: Number(sid), role }));
      await withReauth(() => api(`/api/companies/${companyId}/invitations`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { display_name: displayName.trim(), email, company_role: companyRole, store_roles }, ...NONE }));
      setSave({ kind: "saved", label: "招待を作成しました" });
      setOpen(false);
      setDisplayName("");
      setEmail("");
      setStoreRoles({});
      invitations.reload();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  async function copy(inv: Invitation) {
    const link = `${origin}/invite?token=${inv.token}`;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(inv.id);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      window.prompt("招待リンクをコピーしてください", link);
    }
  }

  return (
    <div className="space-y-3">
      <div className="card flex flex-wrap items-center gap-2">
        <p className="text-sm flex-1" style={{ color: "var(--color-muted)" }}>
          招待リンクを相手に渡すと、相手がユーザー名とパスワードを決めてログインアカウントを作成します（有効期限7日）。会社所属と店舗配属は招待時の内容で作成されます。
        </p>
        <SaveStatus state={save} />
        {canManage && (
          <button className="btn btn-primary" onClick={() => setOpen(true)}>
            ＋ ユーザーを招待
          </button>
        )}
      </div>
      <ErrorBox error={invitations.error} onRetry={invitations.reload} />
      {invitations.loading && <Spinner />}
      {invitations.data?.length === 0 && <Empty>招待はありません</Empty>}
      <div className="space-y-2">
        {invitations.data?.map((inv) => {
          const expired = !inv.accepted_at && new Date(inv.expires_at).getTime() < Date.now();
          return (
            <div key={inv.id} className="card-2 flex flex-wrap items-center gap-2">
              <div className="flex-1">
                <div className="font-semibold">
                  {inv.display_name || inv.email || `招待 #${inv.id}`}
                  <span className="badge ml-2">{ROLE_LABELS[inv.company_role] ?? inv.company_role}</span>
                </div>
                <div className="text-sm" style={{ color: "var(--color-muted)" }}>
                  {inv.store_roles.map((sr) => `${stores.data?.find((s) => s.id === sr.store_id)?.name ?? `店舗#${sr.store_id}`}: ${ROLE_LABELS[sr.role] ?? sr.role}`).join("、") || "店舗配属なし"} ・ 期限 {fmtDateTime(inv.expires_at)}
                </div>
              </div>
              {inv.accepted_at ? (
                <span className="badge badge-ok">受諾済 {fmtDateTime(inv.accepted_at)}</span>
              ) : expired ? (
                <span className="badge badge-danger">期限切れ</span>
              ) : (
                <>
                  <span className="badge badge-warn">未受諾</span>
                  <button className="btn btn-sm" onClick={() => copy(inv)}>
                    {copied === inv.id ? "コピーしました" : "招待リンクをコピー"}
                  </button>
                </>
              )}
            </div>
          );
        })}
      </div>

      {open && (
        <Modal
          title="ユーザーを招待"
          onClose={() => setOpen(false)}
          footer={
            <>
              <button className="btn" onClick={() => setOpen(false)}>
                キャンセル
              </button>
              <button className="btn btn-primary" onClick={create} disabled={save.kind === "saving"}>
                招待を作成（パスワード再確認）
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <Field label="表示名（任意）">
              <input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
            </Field>
            <Field label="メールアドレス（任意・記録用）">
              <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
            <Field label="会社内の権限">
              <select className="input" value={companyRole} onChange={(e) => setCompanyRole(e.target.value as "owner" | "admin" | "member")}>
                {COMPANY_ROLES.map((r) => (
                  <option key={r.value} value={r.value}>
                    {r.label}
                  </option>
                ))}
              </select>
            </Field>
            <div>
              <span className="label">店舗配属（店舗ごとに権限を選択）</span>
              <div className="space-y-2">
                {stores.data?.map((s) => (
                  <div key={s.id} className="flex items-center gap-2">
                    <span className="w-40">{s.name}</span>
                    <select className="input w-auto" value={storeRoles[s.id] ?? ""} onChange={(e) => setStoreRoles({ ...storeRoles, [s.id]: e.target.value })}>
                      <option value="">配属しない</option>
                      {STORE_ROLES.map((r) => (
                        <option key={r.value} value={r.value}>
                          {r.label}
                        </option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

function InfoTab({ companyId, canManage }: { companyId: number; canManage: boolean }) {
  const { withReauth, refresh } = useSession();
  const q = useLoadNoStore<Company>(`/api/companies/${companyId}`);
  const [name, setName] = useState("");
  const [sharing, setSharing] = useState(false);
  const [note, setNote] = useState("");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  useEffect(() => {
    if (q.data) {
      setName(q.data.name);
      setSharing(q.data.customer_sharing_enabled);
      setNote(q.data.note);
    }
  }, [q.data]);
  if (q.loading) return <Spinner />;
  if (q.error || !q.data) return <ErrorBox error={q.error} onRetry={q.reload} />;
  return (
    <div className="card max-w-2xl space-y-3">
      <div className="flex flex-wrap gap-2">
        <span className={`badge ${q.data.is_active ? "badge-ok" : "badge-danger"}`}>{q.data.is_active ? "契約有効" : "停止中"}</span>
        {q.data.is_demo && <span className="badge">開発用データ</span>}
        <span className="text-sm" style={{ color: "var(--color-muted)" }}>
          会社コード {q.data.code}
        </span>
      </div>
      <Field label="会社名">
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} disabled={!canManage} />
      </Field>
      {canManage ? (
        <Toggle checked={sharing} onChange={setSharing} label="同一会社内の店舗間で顧客情報の共有を許可する" />
      ) : (
        <p>顧客共有: {sharing ? "許可" : "不許可"}</p>
      )}
      <p className="text-xs" style={{ color: "var(--color-muted)" }}>
        許可した場合も、顧客ごとに「会社内で共有する」を明示した顧客だけが他店舗から参照できます。別会社との共有はできません。
      </p>
      <Field label="メモ">
        <textarea className="input" rows={3} value={note} onChange={(e) => setNote(e.target.value)} disabled={!canManage} />
      </Field>
      {canManage && (
        <div className="flex items-center gap-3 justify-end">
          <SaveStatus state={save} />
          <button
            className="btn btn-primary"
            disabled={!name.trim() || save.kind === "saving"}
            onClick={async () => {
              setSave({ kind: "saving" });
              try {
                const r = await withReauth(() => api<Company>(`/api/companies/${companyId}`, { method: "PATCH", body: { name: name.trim(), customer_sharing_enabled: sharing, note }, ...NONE }));
                q.setData(r);
                setSave({ kind: "saved" });
                await refresh();
              } catch (e) {
                setSave({ kind: "error", error: e });
              }
            }}
          >
            保存する（パスワード再確認）
          </button>
        </div>
      )}
    </div>
  );
}
