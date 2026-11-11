"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { fmtTime, ROLE_LABELS, yen } from "@/lib/format";
import { fmtRemainShort, undoLines } from "@/lib/pos";
import { useSession } from "@/lib/session";
import type { FloorAlarm, FloorState } from "@/lib/types";
import { computeAlarm, syncServerClock, useNow } from "@/lib/useServerClock";
import { CourseActionDialog, type CourseActionKind } from "./CourseActionDialog";
import { ToastProvider, useToast } from "./Toast";

type NavItem = { href: string; label: string; caps?: string[]; anyCaps?: string[]; group: "営業" | "管理" | "経理" | "設定"; freeeHandles?: boolean };

const NAV: NavItem[] = [
  { href: "/floor", label: "営業・卓一覧", caps: ["floor.operate"], group: "営業" },
  { href: "/visits/new", label: "入店・コース登録", caps: ["floor.operate"], group: "営業" },
  { href: "/closing", label: "開店・日締め・現金", caps: ["closing.operate"], group: "営業" },
  { href: "/sales", label: "売上・会計履歴", caps: ["sales.view"], group: "営業" },
  { href: "/reservations", label: "予約・LINE", anyCaps: ["reservations.operate", "line.view"], group: "営業" },
  { href: "/customers", label: "顧客・ボトルキープ", caps: ["customers.view"], group: "営業" },
  { href: "/attendance", label: "出勤・退勤・日払い", caps: ["attendance.self"], group: "営業" },
  { href: "/employees", label: "従業員管理", caps: ["employees.view"], group: "管理" },
  { href: "/inventory", label: "商品・在庫・棚卸し", caps: ["inventory.view"], group: "管理" },
  { href: "/purchasing", label: "仕入先・発注", caps: ["inventory.view"], group: "管理" },
  { href: "/dashboard", label: "経営ダッシュボード", caps: ["dashboard.view"], group: "管理" },
  { href: "/receipts", label: "領収書・経費登録", caps: ["expenses.submit"], group: "営業" },
  { href: "/accounting/dashboard", label: "会計ダッシュボード", caps: ["accounting.view"], group: "経理" },
  { href: "/accounting/expenses", label: "経費・領収書管理", anyCaps: ["expenses.approve", "accounting.view"], group: "経理" },
  { href: "/accounting/bank", label: "銀行 CSV 取込・入出金照合", caps: ["banking.view"], group: "経理", freeeHandles: true },
  { href: "/accounting/ledgers", label: "帳簿（売上・現金・預金・売掛・買掛）", caps: ["accounting.view"], group: "経理" },
  { href: "/accounting", label: "スタッフ支払・源泉徴収", caps: ["tax.view"], group: "経理" },
  { href: "/accounting/assets", label: "固定資産・棚卸", caps: ["accounting.view"], group: "経理", freeeHandles: true },
  { href: "/accounting/yearend", label: "年間決算", caps: ["accounting.view"], group: "経理", freeeHandles: true },
  { href: "/accounting/tax-return", label: "確定申告", caps: ["tax.return.view"], group: "経理", freeeHandles: true },
  { href: "/accounting/tax-return/guide", label: "国税庁 入力ガイド", caps: ["tax.return.view"], group: "経理", freeeHandles: true },
  { href: "/accounting/exports", label: "ファイル出力", caps: ["accounting.view"], group: "経理" },
  { href: "/accounting/books", label: "月次損益・経費・証憑・freee", caps: ["accounting.view"], group: "経理" },
  { href: "/accounting/contracts", label: "キャスト契約・税務区分", anyCaps: ["contracts.edit", "tax.view"], group: "経理" },
  { href: "/accounting/settings", label: "会計設定（事業者・科目・権限）", anyCaps: ["accounting.settings", "tax.view"], group: "経理" },
  { href: "/settings/pricing", label: "時給・バック・料金", caps: ["settings.view"], group: "設定" },
  { href: "/settings/store", label: "店舗設定・権限", caps: ["settings.view"], group: "設定" },
];

type FloorCtx = {
  state: FloorState | null;
  error: unknown;
  reload: () => Promise<void>;
};
const FloorContext = createContext<FloorCtx>({ state: null, error: null, reload: async () => {} });
export function useFloor() {
  return useContext(FloorContext);
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <ToastProvider>
      <ShellInner>{children}</ShellInner>
    </ToastProvider>
  );
}

function ShellInner({ children }: { children: React.ReactNode }) {
  const { me, store, can, logout, setStore } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const [floor, setFloor] = useState<FloorState | null>(null);
  const [floorError, setFloorError] = useState<unknown>(null);
  const [online, setOnline] = useState(true);

  const canFloor = can("floor.operate");

  const reloadFloor = useCallback(async () => {
    if (!canFloor) return;
    try {
      const s = await api<FloorState>("/api/floor/state");
      syncServerClock(s.server_now);
      setFloor(s);
      setFloorError(null);
    } catch (e) {
      setFloorError(e);
    }
  }, [canFloor]);

  useEffect(() => {
    reloadFloor();
    const t = setInterval(() => document.visibilityState === "visible" && reloadFloor(), 15000);
    const onVis = () => document.visibilityState === "visible" && reloadFloor();
    const onOnline = () => {
      setOnline(true);
      reloadFloor();
    };
    const onOffline = () => setOnline(false);
    setOnline(navigator.onLine);
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, [reloadFloor]);

  // 画面を移動したら卓一覧の状態も取り直す（戻ったときに最新を表示）
  useEffect(() => {
    if (pathname === "/floor") reloadFloor();
  }, [pathname, reloadFloor]);

  // 申告方法が freee の事業者では、銀行照合・固定資産・年間決算・確定申告は freee 側で行うのでメニューに出さない
  const freeeFiling = store?.taxpayer?.filing_method === "freee";
  const nav = NAV.filter((n) => (n.caps ? can(...n.caps) : true) && (n.anyCaps ? n.anyCaps.some((c) => can(c)) : true) && !(n.freeeHandles && freeeFiling));
  const groups: NavItem["group"][] = ["営業", "管理", "経理", "設定"];
  const platform = me?.user.is_platform_operator;
  const ownerCompanies = me?.companies.filter((c) => c.role === "owner" || c.role === "admin" || c.role === "operator") ?? [];

  const ctx = useMemo(() => ({ state: floor, error: floorError, reload: reloadFloor }), [floor, floorError, reloadFloor]);
  const onFloor = pathname === "/floor";

  return (
    <FloorContext.Provider value={ctx}>
      <div className="min-h-screen flex flex-col">
        <header className="sticky top-0 z-40 flex items-center gap-2 px-3 h-[68px]" style={{ background: "var(--color-panel)", borderBottom: "1px solid var(--color-border)" }}>
          {canFloor && (
            <Link href="/floor" className={`btn btn-lg whitespace-nowrap shrink-0 ${onFloor ? "btn-primary" : ""}`} style={{ minHeight: 54 }}>
              卓一覧
            </Link>
          )}
          {can("attendance.manage") && (
            <Link href="/attendance" className={`btn btn-lg whitespace-nowrap shrink-0 ${pathname === "/attendance" ? "btn-primary" : ""}`} style={{ minHeight: 54 }}>
              出勤・お渡し
            </Link>
          )}
          {can("expenses.submit") && (
            <Link href="/receipts/new" className={`btn btn-lg whitespace-nowrap shrink-0 ${pathname.startsWith("/receipts") ? "btn-primary" : ""}`} style={{ minHeight: 54 }} aria-label="領収書を撮る" title="領収書を撮る">
              📷<span className="hidden xl:inline ml-1">領収書</span>
            </Link>
          )}
          <button className="btn whitespace-nowrap shrink-0" style={{ minHeight: 54 }} onClick={() => setMenuOpen((v) => !v)} aria-label="メニュー" aria-expanded={menuOpen}>
            ☰<span className="hidden lg:inline ml-1">メニュー</span>
          </button>
          {store && (
            <button className="badge badge-accent ml-1 whitespace-nowrap overflow-hidden text-ellipsis" style={{ maxWidth: 220 }} onClick={() => router.push("/select-store")} title={`店舗を切り替える（${store.name}）`}>
              {store.name}
              {store.is_demo && <span className="ml-1 text-xs hidden xl:inline">（開発用）</span>}
            </button>
          )}
          <div className="ml-auto flex items-center gap-2 text-sm">
            {/* さりげないロゴ: 上部バーの空いている所にだけ出す（画面が狭いときは出さない・タップの邪魔をしない） */}
            <span className="hidden xl:flex items-center gap-2 mr-3 select-none pointer-events-none" style={{ opacity: 0.75 }} aria-hidden>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/logo-mark.png" alt="" width={30} height={30} />
              <span className="font-semibold tracking-[0.3em] text-sm" style={{ color: "var(--color-muted)" }}>
                NOXIA
              </span>
            </span>
            {!online && <span className="badge badge-danger">オフライン（保存できません）</span>}
            {floorError && online ? <span className="badge badge-danger">サーバーに接続できません</span> : null}
            <span className="whitespace-nowrap" style={{ color: "var(--color-muted)" }}>
              {me?.user.display_name || me?.user.username}
              {store && <span className="ml-1 hidden lg:inline">({ROLE_LABELS[store.role] ?? store.role})</span>}
            </span>
            <button
              className="btn btn-sm btn-ghost whitespace-nowrap shrink-0"
              onClick={async () => {
                await logout();
                router.replace("/login");
              }}
            >
              ログアウト
            </button>
          </div>
        </header>

        {canFloor && floor && <AlarmBanner alarms={floor.alarms} mode={floor.alarm_mode} onChanged={reloadFloor} />}

        <div className="flex flex-1">
          {menuOpen && (
            <nav className="w-72 shrink-0 p-3 space-y-4 overflow-y-auto" style={{ background: "var(--color-panel)", borderRight: "1px solid var(--color-border)" }} aria-label="主メニュー">
              {groups.map((g) => {
                const items = nav.filter((n) => n.group === g);
                if (!items.length) return null;
                return (
                  <div key={g}>
                    <div className="text-sm font-semibold mb-1 px-2" style={{ color: "var(--color-muted)" }}>
                      {g}
                    </div>
                    {items.map((n) => {
                      const active = pathname === n.href || (n.href !== "/floor" && n.href !== "/accounting" && pathname.startsWith(n.href));
                      return (
                        <Link
                          key={n.href}
                          href={n.href}
                          onClick={() => setMenuOpen(false)}
                          className="block rounded-xl px-4 py-3.5 mb-1 font-semibold text-lg"
                          style={{ background: active ? "var(--color-panel3)" : "transparent", color: active ? "var(--color-text)" : "var(--color-muted)" }}
                        >
                          {n.label}
                        </Link>
                      );
                    })}
                  </div>
                );
              })}
              {(ownerCompanies.length > 0 || platform) && (
                <div>
                  <div className="text-sm font-semibold mb-1 px-2" style={{ color: "var(--color-muted)" }}>
                    会社・運営
                  </div>
                  {ownerCompanies.map((c) => (
                    <Link key={c.id} href={`/company/${c.id}`} onClick={() => setMenuOpen(false)} className="block rounded-xl px-4 py-3.5 mb-1 font-semibold" style={{ color: "var(--color-muted)" }}>
                      {c.name}（全店）
                    </Link>
                  ))}
                  {platform && (
                    <Link href="/platform" onClick={() => setMenuOpen(false)} className="block rounded-xl px-4 py-3.5 mb-1 font-semibold" style={{ color: "var(--color-muted)" }}>
                      運営者: 会社・店舗管理
                    </Link>
                  )}
                </div>
              )}
              <div>
                <button
                  className="btn w-full"
                  onClick={async () => {
                    await setStore(null);
                    setMenuOpen(false);
                    router.push("/select-store");
                  }}
                >
                  店舗を切り替える
                </button>
              </div>
            </nav>
          )}
          <main className="flex-1 p-3 md:p-4 min-w-0">{children}</main>
        </div>
      </div>
    </FloorContext.Provider>
  );
}

/** 終了時間の通知バナー（どの画面でも上部に表示。閉じても卓一覧の超過表示は残る） */
function AlarmBanner({ alarms, mode, onChanged }: { alarms: FloorAlarm[]; mode: "10" | "5" | "both"; onChanged: () => Promise<void> }) {
  const now = useNow();
  const router = useRouter();
  const toast = useToast();
  const [dismissed, setDismissed] = useState<Record<string, number>>({});
  const [listOpen, setListOpen] = useState(false);
  const [action, setAction] = useState<{ alarm: FloorAlarm; kind: CourseActionKind } | null>(null);

  // サーバーの判定を元に、端末の時計で毎秒再計算する
  const live = alarms
    .map((a) => {
      const c = computeAlarm(a.planned_end_at, false, true, now, mode);
      return { ...a, level: (c.level ?? a.level) as FloorAlarm["level"], remaining_seconds: c.remaining };
    })
    .filter((a) => a.level !== null);
  const visible = live.filter((a) => !dismissed[`${a.entry_id}:${a.level}`]);
  if (!visible.length && !action) return null;
  const sorted = [...visible].sort((a, b) => a.remaining_seconds - b.remaining_seconds);
  const top = sorted[0];

  const levelText = (l: FloorAlarm["level"]) => (l === "overdue" ? "時間終了・対応待ち" : l === "warn5" ? "確認してください" : "まもなく終了");
  const levelCls = (l: FloorAlarm["level"]) => (l === "overdue" ? "alarm-overdue" : l === "warn5" ? "alarm-warn5" : "alarm-warn10");

  const row = (a: FloorAlarm, main: boolean) => (
    <div key={a.entry_id} className={`flex flex-wrap items-center gap-3 ${main ? "" : `card-2 ${levelCls(a.level)}`}`}>
      <span className={`badge ${a.level === "overdue" ? "badge-danger" : a.level === "warn5" ? "badge-orange" : "badge-warn"}`} style={{ fontSize: "1.05rem", padding: "6px 12px" }}>
        ⏰ {levelText(a.level)}
      </span>
      <span className="font-bold text-2xl">{a.table_name}</span>
      <span className="text-lg">{a.label}</span>
      <span className="num text-lg">
        終了 {fmtTime(a.planned_end_at)} ・ <strong className="text-2xl">{fmtRemainShort(a.remaining_seconds)}</strong>
      </span>
      <div className="ml-auto flex gap-2">
        <button className="btn btn-lg btn-primary" onClick={() => router.push(`/visits/${a.visit_id}/checkout`)}>
          会計へ
        </button>
        <button className="btn btn-lg" onClick={() => setAction({ alarm: a, kind: "extend" })}>
          1時間延長
        </button>
        <button className="btn btn-lg" onClick={() => setAction({ alarm: a, kind: "unlimited" })}>
          無制限へ
        </button>
        {main && (
          <button className="btn btn-lg btn-ghost" onClick={() => setDismissed((d) => ({ ...d, [`${a.entry_id}:${a.level}`]: Date.now() }))} aria-label="通知を閉じる">
            閉じる
          </button>
        )}
      </div>
    </div>
  );

  return (
    <>
      {top && (
        <div className={`sticky top-[68px] z-30 px-3 py-2 border-b ${levelCls(top.level)}`} style={{ borderColor: "var(--color-border)" }} role="alert" aria-live="polite">
          {row(top, true)}
          {sorted.length > 1 && (
            <button className="btn btn-sm mt-2" onClick={() => setListOpen((v) => !v)}>
              ほかに {sorted.length - 1} 件 {listOpen ? "▲ 閉じる" : "▼ 表示"}
            </button>
          )}
          {listOpen && <div className="mt-2 space-y-2">{sorted.slice(1).map((a) => row(a, false))}</div>}
        </div>
      )}
      {action && (
        <CourseActionDialog
          visitId={action.alarm.visit_id}
          entryId={action.alarm.entry_id}
          kind={action.kind}
          title={`${action.alarm.table_name} ${action.kind === "extend" ? "1時間延長" : "時間無制限へ変更"}`}
          onClose={() => setAction(null)}
          onDone={async (r) => {
            const a = action.alarm;
            setAction(null);
            await onChanged();
            const lineId = r.visit.created_line_id;
            toast.show({
              message:
                r.kind === "extend"
                  ? `${a.table_name} 延長しました（${fmtTime(r.toEnd)}まで ＋${yen(r.amount)}）`
                  : `${a.table_name} 無制限に変更しました（＋${yen(r.amount)}）`,
              action:
                r.kind === "extend" && lineId
                  ? {
                      label: "取り消す",
                      onClick: async () => {
                        try {
                          await undoLines(a.visit_id, [lineId]);
                          await onChanged();
                          toast.show({ message: "延長を取り消しました", tone: "info" });
                        } catch (e) {
                          toast.show({ message: `取り消せませんでした: ${e instanceof Error ? e.message : ""}`, tone: "error" });
                        }
                      },
                    }
                  : undefined,
            });
          }}
        />
      )}
    </>
  );
}
