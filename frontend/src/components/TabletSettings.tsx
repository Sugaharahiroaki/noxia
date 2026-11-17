"use client";

import { useEffect, useState } from "react";
import { useToast } from "@/components/Toast";
import { Empty, ErrorBox, Field, Modal, Spinner } from "@/components/ui";
import { api, errorMessage, newIdempotencyKey } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/format";
import { useSession } from "@/lib/session";
import { useLoad } from "@/lib/useLoad";

type Member = { id: number; user_id: number; username: string; display_name: string; role: string; is_active: boolean; is_tablet: boolean };
type Item = { key: string; label: string; group: string };
type TabletData = { members: Member[]; items: Item[]; menu: string[]; default_menu: string[]; is_default: boolean };
const GROUPS = ["営業", "管理", "経理", "設定"];

/**
 * 店舗設定「タブレット」（PC から）: どのアカウントが店舗タブレット用か、タブレットに出す画面。
 * タブレット用アカウントでログインすると、ここで選んだ画面だけがメニューに出る。管理・経理・設定は PC から。
 */
export function TabletSettingsTab() {
  const { can, refresh, me, store } = useSession();
  const toast = useToast();
  const q = useLoad<TabletData>("/api/store/tablet");
  const [menu, setMenu] = useState<string[]>([]);
  const [tabletIds, setTabletIds] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const canEdit = can("settings.edit");
  // アカウントを作る・パスワードを変えるのは会社オーナーと運営者だけ（API も同じ判定）
  const canManageAccounts =
    canEdit && (!!me?.user.is_platform_operator || me?.companies.find((c) => c.id === store?.company.id)?.role === "owner");
  const [accountDialog, setAccountDialog] = useState<{ mode: "create" } | { mode: "password"; member: Member } | null>(null);
  const [created, setCreated] = useState<{ username: string; mode: "create" | "password" } | null>(null);
  const d = q.data;

  useEffect(() => {
    if (d) {
      setMenu(d.menu);
      setTabletIds(d.members.filter((m) => m.is_tablet).map((m) => m.id));
    }
  }, [d]);

  const same = (a: number[] | string[], b: number[] | string[]) => a.length === b.length && [...a].sort().every((x, i) => x === [...b].sort()[i]);
  const changed = d ? !same(menu, d.menu) || !same(tabletIds, d.members.filter((m) => m.is_tablet).map((m) => m.id)) : false;

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
                店舗のタブレットでログインしたままにするアカウントに印を付けます。印の付いたアカウントは、下で選んだ画面だけがメニューに出ます（それ以外の画面を開くと卓一覧に戻ります）。
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
                      {m.username} ・ {ROLE_LABELS[m.role] ?? m.role}
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

          <section className="card space-y-3" aria-label="タブレットに出す画面">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-xl font-bold">タブレットに出す画面</h2>
                <p className="text-sm" style={{ color: "var(--color-muted)" }}>
                  既定は営業の画面だけです。チェックを増やせばタブレットからも開けます（その画面の権限は役割のとおり）。
                  {d.is_default ? " いまは既定のままです。" : " いまは変更した一覧です。"}
                </p>
              </div>
              {canEdit && (
                <button className="btn btn-sm" disabled={busy || d.is_default} onClick={() => put({ menu: null }, "タブレットに出す画面を既定に戻しました")}>
                  既定（営業の画面だけ）に戻す
                </button>
              )}
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {GROUPS.map((g) => {
                const items = d.items.filter((i) => i.group === g);
                if (!items.length) return null;
                return (
                  <div key={g} className="card-2 space-y-1" role="group" aria-label={`${g}の画面`}>
                    <div className="font-bold mb-1">{g}</div>
                    {items.map((i) => (
                      <label key={i.key} className="flex items-center gap-2 py-1">
                        <input
                          type="checkbox"
                          className="w-5 h-5"
                          checked={menu.includes(i.key)}
                          disabled={!canEdit || busy}
                          onChange={(e) => setMenu((cur) => (e.target.checked ? [...cur, i.key] : cur.filter((k) => k !== i.key)))}
                          aria-label={i.label}
                        />
                        <span>{i.label}</span>
                        {d.default_menu.includes(i.key) && (
                          <span className="text-xs" style={{ color: "var(--color-muted)" }}>
                            既定
                          </span>
                        )}
                      </label>
                    ))}
                  </div>
                );
              })}
            </div>
          </section>

          {canEdit ? (
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn btn-primary" disabled={busy || !changed} onClick={() => put({ tablet_membership_ids: tabletIds, menu }, "タブレットの設定を保存しました")}>
                保存する
              </button>
              <span className="text-sm" style={{ color: "var(--color-muted)" }}>
                タブレット側は、次に画面を開き直した（またはログインし直した）ときに反映されます。
              </span>
            </div>
          ) : (
            <p className="text-sm" style={{ color: "var(--color-muted)" }}>
              変更には店舗設定の編集権限が必要です。
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
            この店舗の「店舗管理者」として作り、タブレット用の印を付けます。タブレットには下で選んだ画面だけが出ます。
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
