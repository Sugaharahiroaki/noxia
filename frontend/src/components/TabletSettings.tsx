"use client";

import { useEffect, useState } from "react";
import { useToast } from "@/components/Toast";
import { Empty, ErrorBox, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
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
  const { can, withReauth, refresh } = useSession();
  const toast = useToast();
  const q = useLoad<TabletData>("/api/store/tablet");
  const [menu, setMenu] = useState<string[]>([]);
  const [tabletIds, setTabletIds] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const canEdit = can("settings.edit");
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
      const r = await withReauth(() => api<TabletData>("/api/store/tablet", { method: "PUT", body }));
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
            {d.members.length === 0 && <Empty>この店舗に配属されたアカウントがありません。会社管理の「招待」から追加してください。</Empty>}
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
    </div>
  );
}
