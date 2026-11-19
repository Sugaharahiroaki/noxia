"use client";

import { useEffect, useState } from "react";
import { useToast } from "@/components/Toast";
import { Empty, ErrorBox, Field, Modal, Spinner } from "@/components/ui";
import { api, errorMessage, newIdempotencyKey } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/format";
import { useSession } from "@/lib/session";
import { useLoad } from "@/lib/useLoad";

type Member = { id: number; user_id: number; username: string; display_name: string; role: string; is_active: boolean; is_tablet: boolean };
/** tablet: タブレットに出せる画面か（経理はパソコンだけ） */
type Item = { key: string; label: string; group: string; tablet: boolean };
type OpItem = { key: string; label: string; default: boolean };
type TabletData = {
  members: Member[];
  items: Item[];
  menu: string[];
  default_menu: string[];
  is_default: boolean;
  ops_items: OpItem[];
  ops: string[];
  default_ops: string[];
  ops_is_default: boolean;
  /** タブレットには付けられない（会社オーナーだけ）の項目 */
  owner_only: string[];
  /** 会社オーナー・会社管理者・運営者だけ true（タブレット・店舗管理者は見るだけ） */
  can_edit: boolean;
};
const GROUPS = ["営業", "管理", "設定", "経理"];

const same = (a: readonly (number | string)[], b: readonly (number | string)[]) => {
  const x = [...a].sort();
  const y = [...b].sort();
  return x.length === y.length && x.every((v, i) => v === y[i]);
};

/**
 * 店舗設定「タブレット」（PC から）: どのアカウントが店舗タブレット用か（作る・パスワード・印）。
 * タブレットで開ける画面とできる操作は「権限」タブ（TabletPermissionsTab）で決める。
 */
export function TabletSettingsTab() {
  const { refresh, me, store } = useSession();
  const toast = useToast();
  const q = useLoad<TabletData>("/api/store/tablet");
  const [tabletIds, setTabletIds] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const d = q.data;
  const canEdit = !!d?.can_edit;
  // アカウントを作る・パスワードを変えるのは会社オーナーと運営者だけ（API も同じ判定）
  const canManageAccounts =
    canEdit && (!!me?.user.is_platform_operator || me?.companies.find((c) => c.id === store?.company.id)?.role === "owner");
  const [accountDialog, setAccountDialog] = useState<{ mode: "create" } | { mode: "password"; member: Member } | null>(null);
  const [created, setCreated] = useState<{ username: string; mode: "create" | "password" } | null>(null);

  useEffect(() => {
    if (d) setTabletIds(d.members.filter((m) => m.is_tablet).map((m) => m.id));
  }, [d]);

  const changed = d ? !same(tabletIds, d.members.filter((m) => m.is_tablet).map((m) => m.id)) : false;

  async function put(body: Record<string, unknown>, done: string) {
    setBusy(true);
    try {
      const r = await api<TabletData>("/api/store/tablet", { method: "PUT", body });
      q.setData(r);
      await refresh();
      toast.show({ message: done });
    } catch (e) {
      toast.show({ message: errorMessage(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  if (q.loading && !d) return <Spinner />;
  return (
    <div className="space-y-4">
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {d && (
        <>
          <section className="card space-y-3" aria-label="タブレット用アカウント">
            <div>
              <h2 className="text-xl font-bold">タブレット用アカウント</h2>
              <p className="text-sm" style={{ color: "var(--color-muted)" }}>
                店舗のタブレットでログインしたままにするアカウントに印を付けます。印の付いたアカウントで開ける画面とできる操作は、「権限」タブのタブレットの列のチェックのとおりです。
                オーナーのアカウントには付けないでください（PC で管理・経理・設定を行うため）。
              </p>
            </div>
            {canManageAccounts && (
              <div className="flex flex-wrap items-center gap-3">
                <button className="btn btn-primary" disabled={busy} onClick={() => setAccountDialog({ mode: "create" })}>
                  ＋ タブレット用アカウントを作る
                </button>
                <span className="text-sm" style={{ color: "var(--color-muted)" }}>
                  ログイン ID とパスワードを決めるだけで作れます。タブレットでその ID とパスワードを入れればログインできます（招待は不要）。
                </span>
              </div>
            )}
            {created && (
              <div className="card-2" role="status">
                <div className="font-bold">{created.mode === "create" ? "タブレット用アカウントを作りました" : "パスワードを変更しました"}</div>
                <div className="text-sm">
                  タブレットで <span className="font-mono font-bold">{created.username}</span> と、いま決めたパスワードを入れてログインしてください。ログインしたままにできます。
                </div>
              </div>
            )}
            {d.members.length === 0 && <Empty>この店舗に配属されたアカウントがありません。上の「タブレット用アカウントを作る」から作れます。</Empty>}
            <div className="space-y-2">
              {d.members.map((m) => {
                const on = tabletIds.includes(m.id);
                return (
                  <label key={m.id} className="card-2 flex flex-wrap items-center gap-3" style={{ opacity: m.is_active ? 1 : 0.6 }}>
                    <input
                      type="checkbox"
                      className="w-6 h-6"
                      checked={on}
                      disabled={!canEdit || busy}
                      onChange={(e) => setTabletIds((cur) => (e.target.checked ? [...cur, m.id] : cur.filter((x) => x !== m.id)))}
                      aria-label={`${m.display_name} をタブレット用にする`}
                    />
                    <span className="font-semibold">{m.display_name}</span>
                    <span className="text-sm" style={{ color: "var(--color-muted)" }}>
                      {m.username}
                      {!m.is_tablet && ` ・ ${ROLE_LABELS[m.role] ?? m.role}`}
                      {!m.is_active && " ・ 停止中"}
                    </span>
                    {m.is_tablet && <span className="badge badge-accent">タブレット用</span>}
                    {canManageAccounts && m.is_tablet && (
                      <button
                        type="button"
                        className="btn btn-sm ml-auto"
                        disabled={busy}
                        onClick={(e) => {
                          e.preventDefault();
                          setAccountDialog({ mode: "password", member: m });
                        }}
                      >
                        パスワードを変える
                      </button>
                    )}
                  </label>
                );
              })}
            </div>
          </section>

          {canEdit ? (
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn btn-primary" disabled={busy || !changed} onClick={() => put({ tablet_membership_ids: tabletIds }, "タブレット用アカウントの印を保存しました")}>
                保存する
              </button>
              <span className="text-sm" style={{ color: "var(--color-muted)" }}>
                タブレットで開ける画面・できる操作は「権限」タブで選びます。
              </span>
            </div>
          ) : (
            <p className="text-sm" style={{ color: "var(--color-muted)" }}>
              タブレット用アカウントの印は、パソコンで会社オーナーが付けます。
            </p>
          )}
        </>
      )}
      {accountDialog && (
        <TabletAccountDialog
          target={accountDialog}
          onClose={() => setAccountDialog(null)}
          onDone={async (data, username) => {
            const mode = accountDialog.mode;
            q.setData(data);
            setCreated({ username, mode });
            setAccountDialog(null);
            await refresh();
            toast.show({ message: mode === "create" ? "タブレット用アカウントを作りました" : "パスワードを変更しました" });
          }}
        />
      )}
    </div>
  );
}

/** タブレット用アカウントを作る／パスワードを変える（会社オーナー・運営者だけ）。 */
function TabletAccountDialog({
  target,
  onClose,
  onDone,
}: {
  target: { mode: "create" } | { mode: "password"; member: Member };
  onClose: () => void;
  onDone: (data: TabletData, username: string) => Promise<void>;
}) {
  const { store } = useSession();
  const [displayName, setDisplayName] = useState(store ? `${store.name} タブレット` : "");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 二重タップ・再送で 2 つ作らないよう、ダイアログを開いている間は同じキーを送る
  const [key] = useState(newIdempotencyKey);
  const creating = target.mode === "create";
  const ready = password.length > 0 && (!creating || username.trim().length > 0);

  async function submit() {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      const data =
        target.mode === "create"
          ? await api<TabletData>("/api/store/tablet/accounts", {
              method: "POST",
              body: { username: username.trim(), password, display_name: displayName.trim() },
              idempotencyKey: key,
            })
          : await api<TabletData>(`/api/store/tablet/accounts/${target.member.id}/password`, { method: "POST", body: { password }, idempotencyKey: key });
      await onDone(data, target.mode === "create" ? username.trim() : target.member.username);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={target.mode === "create" ? "タブレット用アカウントを作る" : `${target.member.display_name} のパスワードを変える`}
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn" disabled={busy} onClick={onClose}>
            キャンセル
          </button>
          <button className="btn btn-primary" disabled={busy || !ready} onClick={submit}>
            {creating ? "作る" : "変更する"}
          </button>
        </>
      }
    >
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        {creating && (
          <>
            <Field label="表示名" hint="画面の右上や操作履歴に出る名前です。">
              <input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} aria-label="表示名" />
            </Field>
            <Field label="ログイン ID" hint="タブレットのログイン画面で入れる ID です。半角英数字がおすすめ（例: rivon-tablet）。">
              <input
                className="input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                aria-label="ログイン ID"
              />
            </Field>
          </>
        )}
        <Field label={creating ? "パスワード" : "新しいパスワード"} hint="8 文字以上で、数字だけ・よくある言葉は使えません。決めたパスワードは控えておいてください。">
          <input
            className="input"
            type={show ? "text" : "password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
            autoCapitalize="none"
            spellCheck={false}
            aria-label={creating ? "パスワード" : "新しいパスワード"}
          />
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} /> パスワードを表示する
        </label>
        {creating && (
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            この店舗のタブレット用アカウントとして作ります。タブレットで開ける画面とできる操作は、「権限」タブのタブレットの列のチェックのとおりです。
          </p>
        )}
        {error && (
          <p className="text-sm font-semibold" role="alert" style={{ color: "var(--color-danger)" }}>
            {error}
          </p>
        )}
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}

/**
 * 店舗設定「権限」: 会社オーナーとタブレットの 2 列の表。
 * 会社オーナーは全部（○ で固定）。タブレットの列のチェックで、タブレットのメニューに出す画面（menu）と、タブレットでできる操作（ops。API の権限）を決める。
 * 変えられるのはパソコンの会社オーナー（会社管理者・運営者）だけ。タブレットや店舗管理者は同じ表を見るだけ。
 */
export function TabletPermissionsTab() {
  const { refresh } = useSession();
  const toast = useToast();
  const q = useLoad<TabletData>("/api/store/tablet");
  const d = q.data;
  // 変えている途中の値（null = 変えていない。画面に戻ったときの再読み込みで、変えている途中のチェックを消さない）
  const [edit, setEdit] = useState<{ menu: string[]; ops: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const canEdit = !!d?.can_edit;
  const menu = edit?.menu ?? d?.menu ?? [];
  const ops = edit?.ops ?? d?.ops ?? [];
  const changed = !!d && !!edit && (!same(edit.menu, d.menu) || !same(edit.ops, d.ops));
  const isDefault = !!d && d.is_default && d.ops_is_default;

  const toggle = (kind: "menu" | "ops", key: string, on: boolean) =>
    setEdit((cur) => {
      const base = cur ?? { menu: d?.menu ?? [], ops: d?.ops ?? [] };
      const list = base[kind];
      return { ...base, [kind]: on ? [...list.filter((k) => k !== key), key] : list.filter((k) => k !== key) };
    });

  async function put(body: Record<string, unknown>, done: string) {
    setBusy(true);
    try {
      const r = await api<TabletData>("/api/store/tablet", { method: "PUT", body });
      q.setData(r);
      setEdit(null);
      await refresh();
      toast.show({ message: done });
    } catch (e) {
      toast.show({ message: errorMessage(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  const save = () => put({ menu, ops }, "タブレットの権限を保存しました");
  const muted = { color: "var(--color-muted)" } as const;
  const center = { textAlign: "center", verticalAlign: "middle" } as const;
  const mid = { verticalAlign: "middle" } as const;

  const cell = (kind: "menu" | "ops", key: string, label: string) => {
    const on = (kind === "menu" ? menu : ops).includes(key);
    if (!canEdit) return <span aria-label={on ? "できる" : "できない"}>{on ? "○" : "—"}</span>;
    return (
      <label className="inline-flex items-center justify-center w-full" style={{ minHeight: 40, cursor: "pointer" }}>
        <input
          type="checkbox"
          className="w-6 h-6"
          checked={on}
          disabled={busy}
          onChange={(e) => toggle(kind, key, e.target.checked)}
          aria-label={`${label}（タブレット）`}
        />
      </label>
    );
  };
  const fixedNo = (note: string) => (
    <span style={muted}>
      —<span className="block text-xs">{note}</span>
    </span>
  );
  const sectionRow = (title: string, note?: string) => (
    <tr key={`s-${title}`}>
      <td colSpan={3} style={{ background: "var(--color-panel2)" }}>
        <div className="font-bold">{title}</div>
        {note && (
          <div className="text-xs" style={muted}>
            {note}
          </div>
        )}
      </td>
    </tr>
  );
  const actions = canEdit && (
    <div className="flex flex-wrap items-center gap-2">
      <button className="btn" disabled={busy || isDefault} onClick={() => put({ menu: null, ops: null }, "タブレットの権限を既定に戻しました")}>
        既定に戻す
      </button>
      <button className="btn btn-primary" disabled={busy || !changed} onClick={save}>
        保存する
      </button>
    </div>
  );

  if (q.loading && !d) return <Spinner />;
  return (
    <div className="space-y-4">
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {d && (
        <section className="card space-y-3" aria-label="権限">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-xl font-bold">権限（会社オーナー・タブレット）</h2>
              <p className="text-sm" style={muted}>
                会社オーナーは、パソコンで全部の画面と操作を使えます。タブレット用アカウントで開ける画面とできる操作は、タブレットの列のチェックで決めます（画面で隠すだけでなく、サーバー側でも止めます）。
                {canEdit
                  ? " 保存すると、タブレットには 1 分ほどで（画面に戻ったときはすぐ）反映されます。"
                  : " 変更はパソコンで会社オーナーが行います（ここでは見るだけです）。"}
              </p>
              {changed && (
                <p className="text-sm font-semibold mt-1" role="status" style={{ color: "var(--color-warn)" }}>
                  まだ保存していません。
                </p>
              )}
            </div>
            {actions}
          </div>
          <div className="scroll-x">
            <table className="table">
              <thead>
                <tr>
                  <th>項目</th>
                  <th style={{ ...center, width: 120 }}>会社オーナー</th>
                  <th style={{ ...center, width: 120 }}>タブレット</th>
                </tr>
              </thead>
              <tbody>
                {sectionRow("タブレットのメニューに出す画面", "チェックした画面がタブレットのメニューに出ます。既定は営業の画面だけです。")}
                {GROUPS.flatMap((g) => {
                  const items = d.items.filter((i) => i.group === g);
                  if (!items.length) return [];
                  return [
                    <tr key={`g-${g}`}>
                      <td colSpan={3} className="text-sm font-semibold" style={muted}>
                        {g}
                      </td>
                    </tr>,
                    ...items.map((i) => (
                      <tr key={i.key}>
                        <td style={mid}>{i.label}</td>
                        <td style={center}>○</td>
                        <td style={center}>{i.tablet ? cell("menu", i.key, i.label) : fixedNo("パソコンだけ")}</td>
                      </tr>
                    )),
                  ];
                })}
                {sectionRow("タブレットでできる操作", "入店・注文・延長・会計、開店・日締め、自分の出勤・退勤、領収書の登録は、いつもできます。")}
                {d.ops_items.map((o) => (
                  <tr key={o.key}>
                    <td style={mid}>{o.label}</td>
                    <td style={center}>○</td>
                    <td style={center}>{cell("ops", o.key, o.label)}</td>
                  </tr>
                ))}
                {sectionRow("会社オーナーだけ", "タブレットには付けられません。")}
                {d.owner_only.map((label) => (
                  <tr key={`o-${label}`}>
                    <td style={mid}>{label}</td>
                    <td style={center}>○</td>
                    <td style={center}>{fixedNo("")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {actions}
        </section>
      )}
    </div>
  );
}
