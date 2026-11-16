"use client";

import { useState } from "react";
import { api, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtTime, yen } from "@/lib/format";
import type { FloorTable, Visit, VisitBrief } from "@/lib/types";
import { useToast } from "./Toast";
import { Modal } from "./ui";

type Step = "menu" | "delete" | "move" | "time";
const DELETE_REASONS = ["入力ミス（入店していない）", "お客様が入店せず帰った", "卓を間違えた"];
const TIME_STEPS = [-10, -5, -1, 1, 5, 10];

/**
 * ホールの卓カードを長押ししたときの伝票操作メニュー。
 * 伝票削除（確認つき・履歴は残る）／テーブル移動／時間変更／経過時間表示の切替。
 * テーブル結合は動きが決まっていないので出さない（UNIFIED_PLAN 6 章 Q6）。
 */
export function VisitActionMenu({
  visit,
  tableName,
  tables,
  showElapsed,
  onToggleElapsed,
  onClose,
  onChanged,
}: {
  visit: VisitBrief;
  tableName: string;
  tables: FloorTable[];
  showElapsed: boolean;
  onToggleElapsed: () => void;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const toast = useToast();
  const [step, setStep] = useState<Step>("menu");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [other, setOther] = useState("");
  const [idem, setIdem] = useState(() => newIdempotencyKey());

  async function run(fn: () => Promise<string>) {
    setBusy(true);
    setError(null);
    try {
      const message = await fn();
      await onChanged();
      toast.show({ message, tone: "info" });
      onClose();
    } catch (e) {
      setError(errorMessage(e));
      // 通信エラーのときは同じキーのまま（もう一度押しても二重にならない）。それ以外は新しい操作として扱う
      const code = (e as { code?: string }).code;
      if (code !== "network" && code !== "in_progress") setIdem(newIdempotencyKey());
    } finally {
      setBusy(false);
    }
  }

  const errorLine = error && (
    <p className="text-lg font-bold mt-3" role="alert" style={{ color: "var(--color-danger)" }}>
      {error}
    </p>
  );
  const back = () => {
    setError(null);
    setStep("menu");
  };

  if (step === "menu") {
    return (
      <Modal title={`${tableName} の伝票操作（伝票#${visit.slip_number}）`} onClose={onClose}>
        <div className="grid grid-cols-2 gap-3">
          <button className="btn btn-xl btn-danger" onClick={() => setStep("delete")}>
            伝票削除
          </button>
          <button className="btn btn-xl" onClick={() => setStep("move")}>
            テーブル移動
          </button>
          <button className="btn btn-xl" onClick={() => setStep("time")}>
            時間変更
          </button>
          <button
            className="btn btn-xl flex-col h-auto py-2"
            onClick={() => {
              onToggleElapsed();
              onClose();
            }}
            aria-pressed={showElapsed}
          >
            <span>経過時間表示</span>
            <span className="text-sm font-normal">{showElapsed ? "いま: 経過時間 → 残り時間に戻す" : "いま: 残り時間 → 経過時間にする"}</span>
          </button>
        </div>
        <p className="text-sm mt-3" style={{ color: "var(--color-muted)" }}>
          {visit.guest_count}名 ・ 入店 {fmtTime(visit.opened_at)} ・ いまの金額 {yen(visit.running_total)}
        </p>
      </Modal>
    );
  }

  if (step === "delete") {
    const picked = reason === "その他" ? other.trim() : reason;
    return (
      <Modal
        title={`${tableName} の伝票を削除しますか？`}
        onClose={busy ? undefined : onClose}
        footer={
          <>
            <button className="btn btn-xl" onClick={back} disabled={busy}>
              キャンセル
            </button>
            <button
              className="btn btn-xl btn-danger"
              disabled={!picked || busy}
              onClick={() =>
                run(async () => {
                  await api(`/api/visits/${visit.id}/cancel`, { method: "POST", idempotencyKey: idem, body: { reason: picked } });
                  return `${tableName} の伝票を削除しました（履歴は残ります）`;
                })
              }
            >
              {busy ? "処理中…" : "削除する"}
            </button>
          </>
        }
      >
        <div className="card-2 mb-3">
          <div className="text-3xl font-bold">{tableName}</div>
          <div className="text-lg num">
            伝票#{visit.slip_number} ・ {visit.guest_count}名 ・ {yen(visit.running_total)}
          </div>
        </div>
        <p className="text-lg font-bold">履歴は残ります。</p>
        <p className="mb-3" style={{ color: "var(--color-muted)" }}>
          この卓は空席に戻り、未精算の金額から外れます。明細・在庫・バックも取り消されます。
        </p>
        <div className="text-base mb-2" style={{ color: "var(--color-muted)" }}>
          理由（記録されます）
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {[...DELETE_REASONS, "その他"].map((r) => (
            <button key={r} className={`btn btn-lg ${reason === r ? "btn-primary" : ""}`} onClick={() => setReason(r)} aria-pressed={reason === r}>
              {r}
            </button>
          ))}
        </div>
        {reason === "その他" && <input className="input mt-3" autoFocus value={other} onChange={(e) => setOther(e.target.value)} placeholder="理由を入力" />}
        {errorLine}
      </Modal>
    );
  }

  if (step === "move") {
    return (
      <Modal
        title={`${tableName} をどの卓へ移動しますか？（伝票・時間はそのまま）`}
        wide
        onClose={busy ? undefined : onClose}
        footer={
          <button className="btn btn-xl" onClick={back} disabled={busy}>
            戻る
          </button>
        }
      >
        <div className="grid grid-cols-4 md:grid-cols-5 gap-3">
          {tables.map((t) => {
            const current = t.id === visit.table;
            const used = t.visits.length > 0;
            return (
              <button
                key={t.id}
                className={`btn btn-xl flex-col h-auto py-3 ${!used ? "btn-primary" : ""}`}
                disabled={current || busy}
                onClick={() =>
                  run(async () => {
                    await api(`/api/visits/${visit.id}/move-table`, { method: "POST", idempotencyKey: idem, body: { table_id: t.id } });
                    return `${tableName} → ${t.name} に移動しました`;
                  })
                }
              >
                <span className="text-2xl">{t.name}</span>
                <span className="text-sm font-normal">{current ? "いまの卓" : used ? "使用中（相席）" : "空席"}</span>
              </button>
            );
          })}
        </div>
        {errorLine}
      </Modal>
    );
  }

  return <TimeChange visit={visit} tableName={tableName} busy={busy} errorLine={errorLine} onBack={back} onClose={onClose} run={run} idem={idem} />;
}

function TimeChange({
  visit,
  tableName,
  busy,
  errorLine,
  onBack,
  onClose,
  run,
  idem,
}: {
  visit: VisitBrief;
  tableName: string;
  busy: boolean;
  errorLine: React.ReactNode;
  onBack: () => void;
  onClose: () => void;
  run: (fn: () => Promise<string>) => Promise<void>;
  idem: string;
}) {
  const entries = visit.course_entries.filter((e) => e.is_active);
  const [entryId, setEntryId] = useState<number | null>(entries[0]?.id ?? null);
  const [shift, setShift] = useState(0); // 分（マイナス = 開始を早める）
  const entry = entries.find((e) => e.id === entryId) ?? null;
  const startMs = entry ? new Date(entry.started_at).getTime() + shift * 60000 : 0;
  const endMs = entry?.planned_end_at ? new Date(entry.planned_end_at).getTime() + shift * 60000 : null;
  const iso = (ms: number) => new Date(ms).toISOString();

  return (
    <Modal
      title={`${tableName} の時間変更（開始時刻の訂正）`}
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn btn-xl" onClick={onBack} disabled={busy}>
            戻る
          </button>
          <button
            className="btn btn-xl btn-primary"
            disabled={!entry || shift === 0 || busy}
            onClick={() =>
              entry &&
              run(async () => {
                await api<Visit>(`/api/visits/${visit.id}/course-entries/${entry.id}/change-time`, { method: "POST", idempotencyKey: idem, body: { started_at: iso(startMs) } });
                return `${tableName} の開始を ${fmtTime(iso(startMs))} に変更しました`;
              })
            }
          >
            {busy ? "処理中…" : "決定"}
          </button>
        </>
      }
    >
      {entries.length === 0 && <p>時間を変更できるセットがありません。</p>}
      {entries.length > 1 && (
        <div className="flex flex-wrap gap-2 mb-3">
          {entries.map((e) => (
            <button
              key={e.id}
              className={`btn ${entryId === e.id ? "btn-primary" : ""}`}
              onClick={() => {
                setEntryId(e.id);
                setShift(0);
              }}
              aria-pressed={entryId === e.id}
            >
              {e.label || e.course_name}（{e.guest_count}名）
            </button>
          ))}
        </div>
      )}
      {entry && (
        <div className="space-y-3">
          <div className="card-2 text-center">
            <div className="text-base" style={{ color: "var(--color-muted)" }}>
              {entry.course_name}
            </div>
            <div className="text-4xl font-bold num">
              {fmtTime(iso(startMs))} 〜 {endMs ? fmtTime(iso(endMs)) : "無制限"}
            </div>
            <div className="text-base num" style={{ color: "var(--color-muted)" }}>
              いまの登録: {fmtTime(entry.started_at)} 〜 {entry.planned_end_at ? fmtTime(entry.planned_end_at) : "無制限"}
              {shift !== 0 && `（${shift > 0 ? "+" : ""}${shift}分）`}
            </div>
          </div>
          <div className="grid grid-cols-6 gap-2">
            {TIME_STEPS.map((m) => (
              <button key={m} className={`btn ${m < 0 ? "btn-minus" : "btn-plus"} num whitespace-nowrap`} style={{ padding: "0 0.3rem" }} onClick={() => setShift((s) => s + m)} aria-label={`${m > 0 ? "+" : ""}${m}分`}>
                {m > 0 ? `+${m}` : m}分
              </button>
            ))}
          </div>
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            開始時刻をずらすと、終了予定も同じ分だけずれます。料金は変わりません。未来の時刻にはできません。
          </p>
        </div>
      )}
      {errorLine}
    </Modal>
  );
}
