"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useFloor } from "@/components/AppShell";
import { OpenRegisterCard } from "@/components/OpenRegister";
import { useToast } from "@/components/Toast";
import { Empty, ErrorBox, Spinner } from "@/components/ui";
import { fmtDate, yen } from "@/lib/format";
import { fmtElapsed, fmtRemainShort } from "@/lib/pos";
import type { CourseEntry, VisitBrief } from "@/lib/types";
import { computeAlarm, type AlarmLevel, useNow } from "@/lib/useServerClock";

type Mode = "10" | "5" | "both";

function worstLevel(visits: VisitBrief[], now: number, mode: Mode): AlarmLevel {
  let worst: AlarmLevel = null;
  for (const v of visits)
    for (const e of v.course_entries) {
      const a = computeAlarm(e.planned_end_at, e.is_unlimited, e.is_active, now, mode);
      if (a.level === "overdue") return "overdue";
      if (a.level === "warn5") worst = "warn5";
      else if (a.level === "warn10" && !worst) worst = "warn10";
    }
  return worst;
}

const LEVEL_CLASS: Record<string, string> = { overdue: "alarm-overdue", warn5: "alarm-warn5", warn10: "alarm-warn10" };
const LEVEL_TEXT: Record<string, string> = { overdue: "時間終了", warn5: "確認して", warn10: "まもなく終了" };
const LEVEL_BADGE: Record<string, string> = { overdue: "badge-danger", warn5: "badge-orange", warn10: "badge-warn" };

export default function FloorPage() {
  const { state, error, reload } = useFloor();
  const now = useNow();
  const router = useRouter();
  const toast = useToast();

  if (!state && !error) return <Spinner label="卓一覧を読み込み中…" />;
  const mode: Mode = state?.alarm_mode ?? "both";
  const registerOpen = !!state?.business_day;
  const allVisits = state?.tables.flatMap((t) => t.visits) ?? [];
  const guests = allVisits.reduce((s, v) => s + v.guest_count, 0);
  const openTotal = allVisits.reduce((s, v) => s + Number(v.running_total ?? 0), 0);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="text-lg" style={{ color: "var(--color-muted)" }}>
          {state && <>営業日 {fmtDate(state.business_date)}</>}
        </div>
        <div className="flex gap-2 text-lg">
          <span className="badge" style={{ fontSize: "1.05rem", padding: "6px 12px" }}>
            営業中 <strong className="num">{allVisits.length}</strong> 組 ・ <strong className="num">{guests}</strong> 名
          </span>
          <span className="badge" style={{ fontSize: "1.05rem", padding: "6px 12px" }}>
            未会計 <strong className="num">{yen(openTotal)}</strong>
          </span>
        </div>
        <button className="btn ml-auto" onClick={() => reload()}>
          更新
        </button>
      </div>
      <ErrorBox error={error} onRetry={() => reload()} />
      {state && !registerOpen && <OpenRegisterCard onOpened={() => reload()} />}
      {state && state.tables.length === 0 && <Empty>卓が登録されていません。店舗設定から卓を登録してください。</Empty>}

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3">
        {state?.tables.map((t) => {
          const visits = t.visits;
          if (visits.length === 0) {
            return (
              <button
                key={t.id}
                className="card text-left flex flex-col justify-between min-h-[180px] active:scale-[0.98] transition"
                style={{ background: "var(--color-panel)", borderStyle: "dashed", borderWidth: 2, borderColor: "var(--color-edge)", opacity: registerOpen ? 1 : 0.55 }}
                onClick={() => {
                  if (!registerOpen) {
                    toast.show({ message: "先に開店（レジ開け）してください", tone: "error" });
                    window.scrollTo({ top: 0, behavior: "smooth" });
                    return;
                  }
                  router.push(`/visits/new?table=${t.id}`);
                }}
                aria-label={registerOpen ? `${t.name} 空席。タップして入店` : `${t.name} 空席（開店前）`}
              >
                <div className="text-4xl font-bold">{t.name}</div>
                <div>
                  <div className="text-2xl font-bold" style={{ color: "var(--color-muted)" }}>
                    空席
                  </div>
                  <div className="text-base" style={{ color: "var(--color-muted)" }}>
                    {registerOpen ? "タップで入店 ＋" : "開店後に入店できます"}
                  </div>
                </div>
              </button>
            );
          }
          const level = worstLevel(visits, now, mode);
          if (visits.length === 1) {
            const v = visits[0];
            return (
              <button
                key={t.id}
                className={`card text-left flex flex-col min-h-[180px] active:scale-[0.98] transition ${level ? LEVEL_CLASS[level] : ""}`}
                style={level ? undefined : { borderColor: "var(--color-accent)", background: "#17223a" }}
                onClick={() => router.push(`/visits/${v.id}`)}
                aria-label={`${t.name} 伝票${v.slip_number} 経過${fmtElapsed(v.opened_at, now)}`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="text-4xl font-bold">{t.name}</div>
                  {level ? (
                    <span className={`badge ${LEVEL_BADGE[level]}`} style={{ fontSize: "1rem", padding: "6px 10px" }}>
                      ⏰ {LEVEL_TEXT[level]}
                    </span>
                  ) : (
                    <span className="badge badge-accent" style={{ fontSize: "1rem", padding: "6px 10px" }}>
                      {v.guest_count}名
                    </span>
                  )}
                </div>
                <div className="mt-1 flex items-baseline gap-2">
                  <span className="text-5xl font-bold num">{fmtElapsed(v.opened_at, now)}</span>
                  <span className="text-lg" style={{ color: "var(--color-muted)" }}>
                    経過
                  </span>
                </div>
                <div className="mt-1 space-y-0.5">
                  {v.course_entries
                    .filter((e) => e.is_active)
                    .map((e) => (
                      <EntryLine key={e.id} e={e} now={now} mode={mode} />
                    ))}
                </div>
                <div className="mt-auto pt-2 flex items-end justify-between">
                  <span className="text-base" style={{ color: "var(--color-muted)" }}>
                    {level ? `${v.guest_count}名` : ""}
                    {v.customer_display ? ` ${v.customer_display}` : ""}
                  </span>
                  <span className="text-2xl font-bold num">{yen(v.running_total)}</span>
                </div>
              </button>
            );
          }
          // 相席など、1卓に複数の伝票がある場合
          return (
            <div key={t.id} className={`card flex flex-col min-h-[180px] ${level ? LEVEL_CLASS[level] : ""}`} style={level ? undefined : { borderColor: "var(--color-accent)", background: "#17223a" }}>
              <div className="flex items-center justify-between">
                <div className="text-4xl font-bold">{t.name}</div>
                <span className="badge">{visits.length}組</span>
              </div>
              <div className="mt-2 space-y-2">
                {visits.map((v) => (
                  <button key={v.id} className="card-2 w-full text-left active:scale-[0.98] transition" onClick={() => router.push(`/visits/${v.id}`)}>
                    <div className="flex items-baseline justify-between">
                      <span className="text-2xl font-bold num">{fmtElapsed(v.opened_at, now)} 経過</span>
                      <span className="font-bold num">{yen(v.running_total)}</span>
                    </div>
                    {v.course_entries
                      .filter((e) => e.is_active)
                      .map((e) => (
                        <EntryLine key={e.id} e={e} now={now} mode={mode} />
                      ))}
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EntryLine({ e, now, mode }: { e: CourseEntry; now: number; mode: Mode }) {
  const a = computeAlarm(e.planned_end_at, e.is_unlimited, e.is_active, now, mode);
  const color = a.level === "overdue" ? "var(--color-danger)" : a.level === "warn5" ? "var(--color-orange)" : a.level === "warn10" ? "var(--color-warn)" : "var(--color-text)";
  const short = e.course_name.replace("飲み放題・", "");
  return (
    <div className="text-lg flex flex-wrap gap-x-2 font-semibold" style={{ color }}>
      <span>
        {e.label ? `${e.label} ` : ""}
        {short}
        {e.extension_count > 0 && <span className="text-base font-normal">（延長{e.extension_count}）</span>}
      </span>
      {e.is_unlimited ? <span className="font-normal" style={{ color: "var(--color-muted)" }}>無制限</span> : <span className="num">{fmtRemainShort(a.remaining)}</span>}
    </div>
  );
}
