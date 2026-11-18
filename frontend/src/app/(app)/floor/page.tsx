"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useFloor } from "@/components/AppShell";
import { OpenRegisterCard } from "@/components/OpenRegister";
import { useToast } from "@/components/Toast";
import { TagChip } from "@/components/TagPicker";
import { Empty, ErrorBox, Spinner } from "@/components/ui";
import { VisitActionMenu } from "@/components/VisitActionMenu";
import { fmtId } from "@/components/ReportKit";
import { fmtDate, fmtTime, yen } from "@/lib/format";
import { fmtElapsed } from "@/lib/pos";
import type { CourseEntry, FloorTable, VisitBrief } from "@/lib/types";
import { useLongPress } from "@/lib/useLongPress";
import { computeAlarm, type AlarmLevel, useNow } from "@/lib/useServerClock";

type Mode = "10" | "5" | "both";
const ELAPSED_KEY = "noxia.floor.showElapsed";

const LEVEL_CLASS: Record<string, string> = { overdue: "alarm-overdue", warn5: "alarm-warn5", warn10: "alarm-warn10" };
const LEVEL_TEXT: Record<string, string> = { overdue: "時間終了", warn5: "確認して", warn10: "まもなく終了" };
const LEVEL_BADGE: Record<string, string> = { overdue: "badge-danger", warn5: "badge-orange", warn10: "badge-warn" };

/** 伝票の中で、いちばん早く終わる時間制のセット（無ければ最初の有効なセット） */
function leadEntry(v: VisitBrief): CourseEntry | null {
  const active = v.course_entries.filter((e) => e.is_active);
  const timed = active.filter((e) => !e.is_unlimited && e.planned_end_at).sort((a, b) => new Date(a.planned_end_at!).getTime() - new Date(b.planned_end_at!).getTime());
  return timed[0] ?? active[0] ?? null;
}

function visitLevel(v: VisitBrief, now: number, mode: Mode): AlarmLevel {
  let worst: AlarmLevel = null;
  for (const e of v.course_entries) {
    const a = computeAlarm(e.planned_end_at, e.is_unlimited, e.is_active, now, mode);
    if (a.level === "overdue") return "overdue";
    if (a.level === "warn5") worst = "warn5";
    else if (a.level === "warn10" && !worst) worst = "warn10";
  }
  return worst;
}

/** 残り時間。超過はマイナスの分数で出す（表示だけ。終了予定時刻から毎回計算し直す） */
function remainText(e: CourseEntry | null, now: number): { text: string; over: boolean } {
  if (!e) return { text: "—", over: false };
  if (e.is_unlimited || !e.planned_end_at) return { text: "無制限", over: false };
  const sec = Math.floor((new Date(e.planned_end_at).getTime() - now) / 1000);
  if (sec <= 0) return { text: `−${Math.ceil(-sec / 60)}分`, over: true };
  return { text: `残り${Math.ceil(sec / 60)}分`, over: false };
}

export default function FloorPage() {
  const { state, error, reload } = useFloor();
  const now = useNow();
  const router = useRouter();
  const toast = useToast();
  const [showElapsed, setShowElapsed] = useState(false);
  const [menu, setMenu] = useState<{ visitId: number; tableId: number } | null>(null);
  const { bind, guardClick } = useLongPress<{ visitId: number; tableId: number }>((p) => setMenu(p));

  useEffect(() => {
    try {
      setShowElapsed(localStorage.getItem(ELAPSED_KEY) === "1");
    } catch {
      /* ignore */
    }
  }, []);
  const toggleElapsed = () =>
    setShowElapsed((v) => {
      try {
        localStorage.setItem(ELAPSED_KEY, v ? "0" : "1");
      } catch {
        /* ignore */
      }
      return !v;
    });

  // 営業枠（区分）の順に並べる。区分の順は、卓の並び順で最初に出てきた順
  const groups = useMemo(() => {
    const out: { kind: string; tables: FloorTable[] }[] = [];
    for (const t of state?.tables ?? []) {
      let g = out.find((x) => x.kind === t.kind);
      if (!g) out.push((g = { kind: t.kind, tables: [] }));
      g.tables.push(t);
    }
    return out;
  }, [state]);

  if (!state && !error) return <Spinner label="卓一覧を読み込み中…" />;
  const mode: Mode = state?.alarm_mode ?? "both";
  const registerOpen = !!state?.business_day;
  const allVisits = state?.tables.flatMap((t) => t.visits) ?? [];
  const guests = allVisits.reduce((s, v) => s + v.present_count, 0);
  const showKinds = groups.length > 1;
  const menuTable = menu ? state?.tables.find((t) => t.id === menu.tableId) : null;
  const menuVisit = menu ? menuTable?.visits.find((v) => v.id === menu.visitId) : null;

  function tapVacant(t: FloorTable) {
    if (!registerOpen) {
      toast.show({ message: "先に開店（レジ開け）してください", tone: "error" });
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    router.push(`/visits/new?table=${t.id}`);
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3 text-base">
        <span style={{ color: "var(--color-muted)" }}>{state && <>営業日 {fmtDate(state.business_date)}</>}</span>
        <span className="font-semibold">
          使用中 <strong className="num">{allVisits.length}</strong> 組 ・ <strong className="num">{guests}</strong> 名
        </span>
        <span className="text-sm" style={{ color: "var(--color-muted)" }}>
          使用中の卓を長押し（PC は右クリック）で、削除・移動・時間変更
        </span>
        <button className="btn btn-sm ml-auto" onClick={() => reload()}>
          更新
        </button>
      </div>
      <ErrorBox error={error} onRetry={() => reload()} />
      {state && !registerOpen && <OpenRegisterCard onOpened={() => reload()} />}
      {state && state.tables.length === 0 && <Empty>卓が登録されていません。設定から卓を登録してください。</Empty>}

      {groups.map((g) => (
        <section key={g.kind || "_"} aria-label={g.kind || "卓"}>
          {showKinds && (
            <h2 className="text-sm font-bold px-1 mb-1" style={{ color: "var(--pos-gold-deep, var(--color-muted))" }}>
              {g.kind || "その他"}
            </h2>
          )}
          <div className="grid grid-cols-3 md:grid-cols-5 gap-2">
            {g.tables.map((t) => {
              const stripe = t.color ? { borderTopWidth: 8, borderTopColor: t.color } : undefined;
              if (t.visits.length === 0) {
                return (
                  <button
                    key={t.id}
                    className="pos-table pos-table-vacant min-h-[132px] justify-between active:scale-[0.98] transition"
                    style={{ ...stripe, opacity: registerOpen ? 1 : 0.6 }}
                    onClick={() => tapVacant(t)}
                    aria-label={registerOpen ? `${t.name} 空席。タップして入店` : `${t.name} 空席（開店前）`}
                  >
                    <div className="text-2xl font-bold leading-tight">{t.name}</div>
                    <div className="text-4xl font-bold text-center">空席</div>
                    <div className="text-sm text-center">{registerOpen ? "タップで入店 ＋" : "開店後に入店できます"}</div>
                  </button>
                );
              }
              if (t.visits.length === 1) {
                const v = t.visits[0];
                const level = visitLevel(v, now, mode);
                const lead = leadEntry(v);
                const remain = remainText(lead, now);
                const elapsed = fmtElapsed(v.opened_at, now);
                return (
                  <button
                    key={t.id}
                    className={`pos-table pos-table-busy min-h-[132px] active:scale-[0.98] transition ${level ? LEVEL_CLASS[level] : ""}`}
                    style={stripe}
                    {...bind({ visitId: v.id, tableId: t.id })}
                    onClick={guardClick(() => router.push(`/visits/${v.id}`))}
                    aria-label={`${t.name} 伝票 ${fmtId(v.public_id)} 使用中 ${v.present_count}名 ${remain.text}`}
                  >
                    <div className="flex items-start justify-between gap-1 w-full">
                      <span className="text-2xl font-bold leading-tight">{t.name}</span>
                      <span className="text-base font-bold num whitespace-nowrap">使用中 {v.present_count}名</span>
                    </div>
                    <div className="flex items-baseline justify-between gap-1 w-full">
                      <span className="text-3xl font-bold num leading-tight" style={{ color: remain.over ? "var(--color-danger)" : undefined }}>
                        {showElapsed ? `経過 ${elapsed}` : remain.text}
                        {remain.over && <span aria-label="時間超過"> ⚠</span>}
                      </span>
                    </div>
                    <div className="text-sm num w-full" style={{ color: "var(--color-muted)" }}>
                      {lead ? `${fmtTime(lead.started_at)}〜${lead.planned_end_at ? fmtTime(lead.planned_end_at) : "無制限"}` : ""}
                      <span className="ml-2">{showElapsed ? remain.text : `経過 ${elapsed}`}</span>
                    </div>
                    <div className="mt-auto flex items-end justify-between gap-1 w-full">
                      <span className="text-sm truncate min-w-0">
                        {level ? (
                          <span className={`badge ${LEVEL_BADGE[level]}`}>⏰ {LEVEL_TEXT[level]}</span>
                        ) : v.tags.length > 0 ? (
                          <span className="inline-flex gap-1">
                            {v.tags.slice(0, 2).map((tg) => (
                              <TagChip key={tg.id} tag={tg} small />
                            ))}
                            {v.tags.length > 2 && <span className="text-xs">＋{v.tags.length - 2}</span>}
                          </span>
                        ) : (
                          v.customer_display || (v.course_entries.filter((e) => e.is_active).length > 1 ? `${v.course_entries.filter((e) => e.is_active).length}セット` : "")
                        )}
                      </span>
                      <span className="text-xl font-bold num whitespace-nowrap">{yen(v.running_total)}</span>
                    </div>
                  </button>
                );
              }
              // 相席など、1 卓に複数の伝票がある場合
              const worst = t.visits.map((v) => visitLevel(v, now, mode)).reduce<AlarmLevel>((w, l) => (l === "overdue" || w === "overdue" ? "overdue" : l === "warn5" || w === "warn5" ? "warn5" : l ?? w), null);
              return (
                <div key={t.id} className={`pos-table pos-table-busy min-h-[132px] ${worst ? LEVEL_CLASS[worst] : ""}`} style={stripe}>
                  <div className="flex items-center justify-between w-full">
                    <span className="text-2xl font-bold leading-tight">{t.name}</span>
                    <span className="text-base font-bold">使用中 {t.visits.length}組</span>
                  </div>
                  <div className="mt-1 space-y-1 w-full">
                    {t.visits.map((v) => {
                      const remain = remainText(leadEntry(v), now);
                      return (
                        <button
                          key={v.id}
                          className="w-full rounded-lg px-2 text-left flex items-center justify-between gap-2 active:scale-[0.98] transition"
                          style={{ background: "rgba(255,250,240,0.85)", border: "1px solid var(--color-border)", minHeight: 44 }}
                          {...bind({ visitId: v.id, tableId: t.id })}
                          onClick={guardClick(() => router.push(`/visits/${v.id}`))}
                          aria-label={`${t.name} 伝票 ${fmtId(v.public_id)} 使用中 ${v.guest_count}名 ${remain.text}`}
                        >
                          <span className="font-bold num" style={{ color: remain.over ? "var(--color-danger)" : undefined }}>
                            {v.present_count}名 {showElapsed ? `経過 ${fmtElapsed(v.opened_at, now)}` : remain.text}
                            {remain.over && " ⚠"}
                          </span>
                          <span className="font-bold num">{yen(v.running_total)}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      ))}

      {menu && menuVisit && menuTable && state && (
        <VisitActionMenu
          visit={menuVisit}
          tableName={menuTable.name}
          tables={state.tables}
          showElapsed={showElapsed}
          onToggleElapsed={toggleElapsed}
          onClose={() => setMenu(null)}
          onChanged={reload}
        />
      )}
    </div>
  );
}
