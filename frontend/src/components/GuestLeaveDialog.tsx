"use client";

import { useState } from "react";
import { api, ApiError, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { Visit } from "@/lib/types";
import { Modal } from "./ui";

/**
 * 人－: 退店するお客様を 1 人選ぶ。選んだ人だけ赤い「退店」、ほかは「在席」のまま。
 * 開始済みのセットの料金は減らない（退店後の延長は在席人数で計算される）。
 */
export function GuestLeaveDialog({ visit, onClose, onDone }: { visit: Visit; onClose: () => void; onDone: (v: Visit, seq: number) => void }) {
  const [target, setTarget] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [idem, setIdem] = useState(() => newIdempotencyKey());
  const present = visit.guests.filter((g) => g.status === "present");
  const picked = visit.guests.find((g) => g.id === target) ?? null;
  const entryName = (id: number | null) => visit.course_entries.find((e) => e.id === id)?.course_name ?? "";

  async function submit() {
    if (!picked) return;
    setBusy(true);
    setError(null);
    try {
      const v = await api<Visit>(`/api/visits/${visit.id}/guests/${picked.id}/leave`, { method: "POST", idempotencyKey: idem, body: {} });
      onDone(v, picked.seq);
    } catch (e) {
      setError(errorMessage(e));
      if (!(e instanceof ApiError && (e.code === "network" || e.code === "in_progress"))) setIdem(newIdempotencyKey());
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`${visit.table_name} 退店するお客様を選ぶ`}
      wide
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn btn-xl" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button className="btn btn-xl btn-danger" onClick={submit} disabled={!picked || busy || present.length <= 1}>
            {busy ? "処理中…" : picked ? `お客様${picked.seq} を退店にする` : "退店する人を選んでください"}
          </button>
        </>
      }
    >
      {visit.guests.length === 0 ? (
        <p className="text-lg">この伝票は人数だけで登録されているため、1 人ずつの退店は記録できません（会計のときにまとめて精算します）。</p>
      ) : (
        <>
          {present.length <= 1 && <p className="text-lg font-bold mb-2">在席は 1 名だけです。お帰りのときは会計を、入力ミスのときは伝票の削除をしてください。</p>}
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
            {visit.guests.map((g) => {
              const left = g.status === "left";
              const on = g.id === target;
              return (
                <button
                  key={g.id}
                  className="rounded-xl px-3 py-2 text-left"
                  style={{ minHeight: 84, background: on ? "#cf3030" : "var(--color-panel2)", color: on ? "#fff" : undefined, border: `2px solid ${on ? "#962020" : "var(--color-border)"}`, opacity: left ? 0.5 : 1 }}
                  disabled={left || busy || present.length <= 1}
                  onClick={() => setTarget(on ? null : g.id)}
                  aria-pressed={on}
                  aria-label={`お客様${g.seq} ${left ? "退店済み" : on ? "退店" : "在席"}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xl font-bold">お客様{g.seq}</span>
                    <span className="text-lg font-bold">{left ? "退店済み" : on ? "退店" : "在席"}</span>
                  </div>
                  <div className="text-sm">
                    {g.employee_name ? `${g.employee_name} ` : ""}
                    {entryName(g.course_entry)}
                    {left && g.left_at ? ` ・ ${fmtTime(g.left_at)} 退店` : ""}
                  </div>
                </button>
              );
            })}
          </div>
          <p className="text-sm mt-3" style={{ color: "var(--color-muted)" }}>
            退店にしても、開始済みのセット・延長の料金は変わりません。このあとの延長は在席の人数ぶんになります。入力ミスの訂正は、明細の取り消しで行ってください。
          </p>
        </>
      )}
      {error && (
        <p className="text-lg font-bold mt-3" role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}
    </Modal>
  );
}
