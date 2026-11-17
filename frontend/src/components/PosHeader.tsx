"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import { useTabletMenu } from "@/lib/tablet";
import type { Employee, FloorState } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";
import { useNow } from "@/lib/useServerClock";
import { isOnShift, SHIFT_LABEL } from "./ShiftGuard";
import { Empty, ErrorBox, Modal, Spinner } from "./ui";

/** POS の設定画面の場所（ヘッダーの「設定」） */
export const POS_SETTINGS_HREF = "/settings/pos";

const WEEK = ["日", "月", "火", "水", "木", "金", "土"];

function fmtClock(now: number): { date: string; time: string } {
  const p = new Intl.DateTimeFormat("ja-JP", { timeZone: "Asia/Tokyo", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false, weekday: "short" }).formatToParts(new Date(now));
  const get = (t: string) => p.find((x) => x.type === t)?.value ?? "";
  return { date: `${get("month")}/${get("day")}(${get("weekday") || WEEK[new Date(now).getDay()]})`, time: `${get("hour")}:${get("minute")}` };
}

/**
 * POS の営業ヘッダー。左から メニュー｜未精算・精算済・売上合計｜日付時刻｜ホール状況・注文・待機キャスト・設定。
 * 金額は floor/state の値をそのまま出す（画面で計算しない）。「トップ」は行き先が決まっていないので出さない（UNIFIED_PLAN 6 章 Q15）。
 */
export function PosHeader({
  floor,
  pathname,
  menuOpen,
  onToggleMenu,
  notice,
}: {
  floor: FloorState | null;
  pathname: string;
  menuOpen: boolean;
  onToggleMenu: () => void;
  notice?: React.ReactNode;
}) {
  const { can } = useSession();
  const tabletMenu = useTabletMenu();
  const now = useNow(15000);
  const clock = fmtClock(now);
  const [waiting, setWaiting] = useState(false);
  const [orderPick, setOrderPick] = useState(false);
  const openCount = floor?.open_visit_count ?? 0;

  return (
    <>
      <header className="pos-bar sticky top-0 z-40 flex items-center gap-1 px-2 h-[64px]">
        <button type="button" className="pos-bar-btn" onClick={onToggleMenu} aria-label="メニュー" aria-expanded={menuOpen}>
          <span className="pos-bar-icon" aria-hidden>
            ☰
          </span>
          メニュー
        </button>
        <div className="flex items-stretch gap-1 ml-1 min-w-0" aria-label="本日の集計">
          <HeaderStat label="未精算" value={floor ? yen(floor.unpaid_total) : "—"} />
          <HeaderStat label="精算済" value={floor ? yen(floor.paid_total) : "—"} />
          <HeaderStat label="売上合計" value={floor ? yen(floor.sales_total) : "—"} strong />
        </div>
        <div className="mx-auto text-center leading-tight px-1 whitespace-nowrap" aria-label="日付と時刻">
          <div className="text-xs font-semibold opacity-90">{clock.date}</div>
          <div className="text-xl font-bold num">{clock.time}</div>
        </div>
        {notice}
        <nav className="flex items-center" aria-label="POS の機能">
          <Link href="/floor" className="pos-bar-btn" aria-current={pathname === "/floor" ? "page" : undefined}>
            <span className="pos-bar-icon" aria-hidden>
              ▦
            </span>
            ホール状況
            {openCount > 0 && <span className="pos-bar-badge" aria-label={`営業中 ${openCount} 組`}>{openCount}</span>}
          </Link>
          <button type="button" className="pos-bar-btn" onClick={() => setOrderPick(true)}>
            <span className="pos-bar-icon" aria-hidden>
              ✎
            </span>
            注文
          </button>
          {can("employees.view") && (
            <button type="button" className="pos-bar-btn" onClick={() => setWaiting(true)}>
              <span className="pos-bar-icon" aria-hidden>
                ☺
              </span>
              待機キャスト
            </button>
          )}
          {can("settings.view") && tabletMenu.allows(POS_SETTINGS_HREF) && (
            <Link href={POS_SETTINGS_HREF} className="pos-bar-btn" aria-current={pathname.startsWith("/settings") ? "page" : undefined}>
              <span className="pos-bar-icon" aria-hidden>
                ⚙
              </span>
              設定
            </Link>
          )}
        </nav>
      </header>
      {waiting && <WaitingCastDialog engaged={floor?.engaged_employee_ids ?? []} onClose={() => setWaiting(false)} />}
      {orderPick && <OrderPickDialog floor={floor} onClose={() => setOrderPick(false)} />}
    </>
  );
}

function HeaderStat({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="rounded-lg px-2 py-0.5 leading-tight text-right" style={{ background: strong ? "#3d3420" : "rgba(255,250,240,0.92)", color: strong ? "#fff8e6" : "#3d3420", minWidth: 96 }}>
      <div className="text-[0.7rem] font-semibold text-left">{label}</div>
      <div className="text-lg font-bold num whitespace-nowrap">{value}</div>
    </div>
  );
}

/** 待機キャスト: 出勤中で、営業中の伝票に付いていない人の一覧だけ（UNIFIED_PLAN 6 章 Q7） */
function WaitingCastDialog({ engaged, onClose }: { engaged: number[]; onClose: () => void }) {
  const employees = useLoad<Employee[]>("/api/staff/employees?active=1");
  const busy = new Set(engaged);
  const onShift = (employees.data ?? []).filter((e) => isOnShift(e) && e.this_store_role === "cast");
  const waiting = onShift.filter((e) => !busy.has(e.id));
  const seated = onShift.filter((e) => busy.has(e.id));
  return (
    <Modal title="待機キャスト" onClose={onClose}>
      <ErrorBox error={employees.error} onRetry={() => employees.reload(false)} />
      {employees.loading && <Spinner />}
      {employees.data && (
        <div className="space-y-4">
          <section>
            <h3 className="font-bold mb-2">
              待機中（出勤中で、どの卓にも付いていない） <span className="num">{waiting.length}</span>名
            </h3>
            {waiting.length === 0 ? (
              <Empty>待機中のキャストはいません。</Empty>
            ) : (
              <div className="grid grid-cols-3 gap-2">
                {waiting.map((e) => (
                  <div key={e.id} className="card-2 text-center">
                    <div className="text-xl font-bold">{e.display_name}</div>
                    <div className="text-sm" style={{ color: "var(--color-ok)" }}>
                      {e.shift_status ? SHIFT_LABEL[e.shift_status] : ""}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
          {seated.length > 0 && (
            <section>
              <h3 className="font-bold mb-2" style={{ color: "var(--color-muted)" }}>
                卓に付いている <span className="num">{seated.length}</span>名
              </h3>
              <div className="flex flex-wrap gap-2">
                {seated.map((e) => (
                  <span key={e.id} className="badge" style={{ fontSize: "1rem", padding: "6px 12px" }}>
                    {e.display_name}
                  </span>
                ))}
              </div>
            </section>
          )}
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            「卓に付いている」は、営業中の伝票の担当・指名・同伴に入っている人です。
          </p>
        </div>
      )}
    </Modal>
  );
}

/** ヘッダーの「注文」: 使用中の卓を選んで、その卓を開く（UNIFIED_PLAN 6 章 Q16） */
function OrderPickDialog({ floor, onClose }: { floor: FloorState | null; onClose: () => void }) {
  const router = useRouter();
  const rows = (floor?.tables ?? []).flatMap((t) => t.visits.map((v) => ({ t, v })));
  return (
    <Modal title="どの卓の注文ですか？" onClose={onClose} wide>
      {rows.length === 0 ? (
        <Empty>使用中の卓がありません。</Empty>
      ) : (
        <div className="grid grid-cols-3 md:grid-cols-4 gap-3">
          {rows.map(({ t, v }) => (
            <button
              key={v.id}
              className="btn btn-xl flex-col h-auto py-3"
              onClick={() => {
                onClose();
                router.push(orderHref(v.id));
              }}
            >
              <span className="text-2xl">{t.name}</span>
              <span className="text-sm font-normal num">
                {v.guest_count}名 ・ {yen(v.running_total)}
              </span>
            </button>
          ))}
        </div>
      )}
    </Modal>
  );
}

/** 卓の注文画面の場所 */
export function orderHref(visitId: number): string {
  return `/visits/${visitId}/order`;
}
